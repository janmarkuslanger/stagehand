import tempfile
from typing import Optional

import pytest

from stagehand.core.context import RunContext
from stagehand.core.runstate import TaskStatus, load_state
from stagehand.core.scheduler import Scheduler
from stagehand.core.workflow import AgentConfig, Task, TaskResult, Workflow
from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult


class RecordingExecutor(AgentExecutor):
    def __init__(self, outputs: Optional[dict] = None):
        self.order: list[str] = []
        self.outputs = outputs or {}

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.order.append(request.task_id)
        return ExecutionResult(output=self.outputs.get(request.task_id, f"out-{request.task_id}"))


def _wf(tasks, executor):
    return Workflow(name="t", agents={"a": AgentConfig(executor=executor, tools=[])}, tasks=tasks)


@pytest.mark.asyncio
async def test_when_true_runs():
    executor = RecordingExecutor()
    wf = _wf({"t1": Task(agent_id="a", prompt="go", when=lambda ctx: True)}, executor)
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert executor.order == ["t1"]


@pytest.mark.asyncio
async def test_when_false_skips():
    executor = RecordingExecutor()
    wf = _wf({"t1": Task(agent_id="a", prompt="go", when=lambda ctx: False)}, executor)
    tmp = tempfile.mkdtemp()
    run_id = await Scheduler(run_state_directory=tmp).run(wf)
    assert executor.order == []
    state = load_state(run_id, tmp)
    assert state.tasks["t1"].status == TaskStatus.SKIPPED


@pytest.mark.asyncio
async def test_skip_does_not_cascade_downstream_runs_with_empty():
    executor = RecordingExecutor()
    wf = _wf({
        "t1": Task(agent_id="a", prompt="go", when=lambda ctx: False),
        "t2": Task(agent_id="a", prompt="prev=[{{ tasks.t1 }}]", depends_on=["t1"]),
    }, executor)
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert executor.order == ["t2"]  # downstream still runs


@pytest.mark.asyncio
async def test_when_can_inspect_upstream_result():
    executor = RecordingExecutor(outputs={"t1": "APPROVED"})
    seen = {}

    def gate(ctx):
        seen["val"] = ctx.get_task_result("t1").output
        return "APPROVED" in ctx.get_task_result("t1").output

    wf = _wf({
        "t1": Task(agent_id="a", prompt="decide"),
        "t2": Task(agent_id="a", prompt="act", depends_on=["t1"], when=gate),
    }, executor)
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert seen["val"] == "APPROVED"
    assert "t2" in executor.order


@pytest.mark.asyncio
async def test_async_when():
    executor = RecordingExecutor()

    async def gate(ctx):
        return False

    wf = _wf({"t1": Task(agent_id="a", prompt="go", when=gate)}, executor)
    tmp = tempfile.mkdtemp()
    run_id = await Scheduler(run_state_directory=tmp).run(wf)
    assert executor.order == []
    assert load_state(run_id, tmp).tasks["t1"].status == TaskStatus.SKIPPED


@pytest.mark.asyncio
async def test_resume_does_not_rerun_skipped():
    calls = {"when": 0}

    def gate(ctx):
        calls["when"] += 1
        return False

    executor = RecordingExecutor()
    wf = _wf({"t1": Task(agent_id="a", prompt="go", when=gate)}, executor)

    run_context = RunContext(run_id="r1", inputs={})
    run_context.mark_skipped("t1")
    await run_context.set_task_result("t1", TaskResult())
    err = await Scheduler(run_state_directory=tempfile.mkdtemp())._execute(wf, run_context)
    assert err is None
    assert calls["when"] == 0  # preloaded skip, predicate not re-evaluated
