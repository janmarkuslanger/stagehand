import tempfile
from typing import Optional

import pytest

from stagehand.core.context import RunContext
from stagehand.core.runstate import TaskStatus, load_state, save
from stagehand.core.scheduler import Scheduler
from stagehand.core.workflow import AgentConfig, Task, TaskResult, Workflow
from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult


class RecordingExecutor(AgentExecutor):
    """Captures execution order and returns controllable outputs."""

    def __init__(self, outputs: Optional[dict] = None, fail_on: Optional[set] = None):
        self.order: list[str] = []
        self.outputs = outputs or {}
        self.fail_on = fail_on or set()

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.order.append(request.task_id)
        if request.task_id in self.fail_on:
            raise RuntimeError(f"task {request.task_id} failed")
        output = self.outputs.get(request.task_id, f"output-{request.task_id}")
        return ExecutionResult(output=output, files=[])


def _make_workflow(tasks: dict, executor: AgentExecutor) -> Workflow:
    return Workflow(
        name="test",
        agents={"a": AgentConfig(executor=executor, tools=[])},
        tasks=tasks,
    )


@pytest.mark.asyncio
async def test_single_task():
    executor = RecordingExecutor()
    wf = _make_workflow({"t1": Task(agent_id="a", prompt="do it")}, executor)
    scheduler = Scheduler(run_state_directory=tempfile.mkdtemp())
    run_id = await scheduler.run(wf, inputs={})
    assert executor.order == ["t1"]
    assert run_id.startswith("sh-")


@pytest.mark.asyncio
async def test_sequential_order():
    executor = RecordingExecutor()
    wf = _make_workflow({
        "t1": Task(agent_id="a", prompt="step 1"),
        "t2": Task(agent_id="a", prompt="step 2", depends_on=["t1"]),
        "t3": Task(agent_id="a", prompt="step 3", depends_on=["t2"]),
    }, executor)
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert executor.order.index("t1") < executor.order.index("t2")
    assert executor.order.index("t2") < executor.order.index("t3")


@pytest.mark.asyncio
async def test_parallel_execution():
    executor = RecordingExecutor()
    wf = _make_workflow({
        "t1": Task(agent_id="a", prompt="parallel 1"),
        "t2": Task(agent_id="a", prompt="parallel 2"),
        "t3": Task(agent_id="a", prompt="merge", depends_on=["t1", "t2"]),
    }, executor)
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert set(executor.order[:2]) == {"t1", "t2"}
    assert executor.order[2] == "t3"


@pytest.mark.asyncio
async def test_template_resolution():
    executor = RecordingExecutor(outputs={"t1": "hello from t1"})
    wf = _make_workflow({
        "t1": Task(agent_id="a", prompt="write something"),
        "t2": Task(agent_id="a", prompt="follow up: {{ tasks.t1 }}", depends_on=["t1"]),
    }, executor)
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert "t2" in executor.order


@pytest.mark.asyncio
async def test_failing_task_cancels_downstream():
    executor = RecordingExecutor(fail_on={"t1"})
    wf = _make_workflow({
        "t1": Task(agent_id="a", prompt="fail"),
        "t2": Task(agent_id="a", prompt="downstream", depends_on=["t1"]),
    }, executor)
    with pytest.raises(RuntimeError, match="t1 failed"):
        await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert "t2" not in executor.order


@pytest.mark.asyncio
async def test_input_template():
    executor = RecordingExecutor()
    wf = _make_workflow({"t1": Task(agent_id="a", prompt="ticket: {{ input.ticket_id }}")}, executor)
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf, inputs={"ticket_id": "XYZ-42"})
    assert executor.order == ["t1"]


@pytest.mark.asyncio
async def test_no_executor_raises():
    wf = Workflow(
        name="test",
        agents={"a": AgentConfig()},  # no executor set
        tasks={"t1": Task(agent_id="a", prompt="hi")},
    )
    with pytest.raises(RuntimeError, match="no executor set"):
        await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)


@pytest.mark.asyncio
async def test_resume_skips_completed():
    call_count = {"n": 0}

    class CountingExecutor(AgentExecutor):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            call_count["n"] += 1
            return ExecutionResult(output=f"out-{request.task_id}", files=[])

    executor = CountingExecutor()
    tmpdir = tempfile.mkdtemp()
    wf = _make_workflow({
        "t1": Task(agent_id="a", prompt="step 1"),
        "t2": Task(agent_id="a", prompt="step 2", depends_on=["t1"]),
    }, executor)

    await Scheduler(run_state_directory=tmpdir).run(wf)
    assert call_count["n"] == 2

    # Simulate partial run: t2 was not completed
    run_id = load_state.__module__  # just need any run_id from the dir
    import os
    run_id = [f[:-5] for f in os.listdir(tmpdir) if f.endswith(".json")][0]
    state = load_state(run_id, tmpdir)
    state.tasks["t2"].status = TaskStatus.CANCELLED
    state.tasks["t2"].output = ""
    save(state, tmpdir)

    # Test core _execute logic directly with pre-populated context
    run_context = RunContext(run_id=run_id, inputs={})
    await run_context.set_task_result("t1", TaskResult(output="out-t1", files=[]))
    count_before = call_count["n"]
    err = await Scheduler(run_state_directory=tmpdir)._execute(wf, run_context)
    assert err is None
    assert call_count["n"] == count_before + 1
