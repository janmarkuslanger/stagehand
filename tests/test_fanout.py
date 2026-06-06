import tempfile
from typing import Optional

import pytest

from stagehand.core.context import RunContext
from stagehand.core.runstate import TaskStatus, load_state
from stagehand.core.scheduler import Scheduler
from stagehand.core.workflow import AgentConfig, Task, TaskResult, Workflow
from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult


class EchoExecutor(AgentExecutor):
    """Echoes the resolved prompt, recording every task id it ran."""

    def __init__(self, fail_on: Optional[set] = None):
        self.order: list[str] = []
        self.fail_on = fail_on or set()

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.order.append(request.task_id)
        if request.task_id in self.fail_on:
            raise RuntimeError(f"{request.task_id} boom")
        return ExecutionResult(output=request.prompt)


def _wf(tasks, executor):
    return Workflow(name="t", agents={"a": AgentConfig(executor=executor, tools=[])}, tasks=tasks)


@pytest.mark.asyncio
async def test_fanout_runs_one_child_per_item():
    executor = EchoExecutor()
    wf = _wf({
        "map": Task(agent_id="a", prompt="translate: {{ item }}", over=lambda ctx: ["a", "b", "c"]),
    }, executor)
    run_context = RunContext(run_id="r", inputs={})
    err = await Scheduler(run_state_directory=tempfile.mkdtemp())._execute(wf, run_context)
    assert err is None
    assert sorted(executor.order) == ["map#0", "map#1", "map#2"]


@pytest.mark.asyncio
async def test_fanout_substitutes_item_in_prompt():
    executor = EchoExecutor()
    wf = _wf({
        "map": Task(agent_id="a", prompt="X={{ item }}", over=lambda ctx: ["one", "two"]),
    }, executor)
    run_context = RunContext(run_id="r", inputs={})
    await Scheduler(run_state_directory=tempfile.mkdtemp())._execute(wf, run_context)
    result = run_context.get_task_result("map")
    assert result.data == ["X=one", "X=two"]
    assert result.output == "X=one\nX=two"


@pytest.mark.asyncio
async def test_fanout_aggregate_visible_downstream():
    executor = EchoExecutor()
    wf = _wf({
        "map": Task(agent_id="a", prompt="{{ item }}", over=lambda ctx: ["p", "q"]),
        "after": Task(agent_id="a", prompt="all: {{ tasks.map }}", depends_on=["map"]),
    }, executor)
    run_context = RunContext(run_id="r", inputs={})
    await Scheduler(run_state_directory=tempfile.mkdtemp())._execute(wf, run_context)
    # "after" sees the joined output of the children
    assert run_context.get_task_result("after").output == "all: p\nq"


@pytest.mark.asyncio
async def test_fanout_empty_list():
    executor = EchoExecutor()
    wf = _wf({
        "map": Task(agent_id="a", prompt="{{ item }}", over=lambda ctx: []),
        "after": Task(agent_id="a", prompt="done", depends_on=["map"]),
    }, executor)
    run_context = RunContext(run_id="r", inputs={})
    await Scheduler(run_state_directory=tempfile.mkdtemp())._execute(wf, run_context)
    assert run_context.get_task_result("map").data == []
    assert "after" in executor.order  # downstream still runs


@pytest.mark.asyncio
async def test_fanout_over_upstream_data():
    executor = EchoExecutor()

    def fetch(ctx):
        return TaskResult(output="fetched", data=["t1", "t2", "t3"])

    wf = Workflow(
        name="t",
        agents={"a": AgentConfig(executor=executor, tools=[])},
        tasks={
            "fetch": Task(fn=fetch),
            "map": Task(
                agent_id="a",
                prompt="{{ item }}",
                over=lambda ctx: ctx.get_task_result("fetch").data,
                depends_on=["fetch"],
            ),
        },
    )
    run_context = RunContext(run_id="r", inputs={})
    await Scheduler(run_state_directory=tempfile.mkdtemp())._execute(wf, run_context)
    assert run_context.get_task_result("map").data == ["t1", "t2", "t3"]


@pytest.mark.asyncio
async def test_fanout_fn_receives_item():
    seen = []

    def body(ctx, item):
        seen.append(item)
        return f"got-{item}"

    wf = Workflow(
        name="t",
        agents={},
        tasks={"map": Task(fn=body, over=lambda ctx: [1, 2, 3])},
    )
    run_context = RunContext(run_id="r", inputs={})
    await Scheduler(run_state_directory=tempfile.mkdtemp())._execute(wf, run_context)
    assert sorted(seen) == [1, 2, 3]
    assert sorted(run_context.get_task_result("map").data) == ["got-1", "got-2", "got-3"]


@pytest.mark.asyncio
async def test_fanout_child_failure_fails_map_and_downstream():
    executor = EchoExecutor(fail_on={"map#1"})
    wf = _wf({
        "map": Task(agent_id="a", prompt="{{ item }}", over=lambda ctx: ["a", "b", "c"]),
        "after": Task(agent_id="a", prompt="x", depends_on=["map"]),
    }, executor)
    with pytest.raises(RuntimeError, match="boom"):
        await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert "after" not in executor.order


@pytest.mark.asyncio
async def test_fanout_resume_skips_done_children():
    executor = EchoExecutor()
    wf = _wf({
        "map": Task(agent_id="a", prompt="{{ item }}", over=lambda ctx: ["a", "b", "c"]),
    }, executor)
    # Pre-seed one child as already complete (simulating a resumed run).
    run_context = RunContext(run_id="r", inputs={})
    await run_context.set_task_result("map#1", TaskResult(output="cached"))
    await Scheduler(run_state_directory=tempfile.mkdtemp())._execute(wf, run_context)
    assert "map#1" not in executor.order
    assert sorted(executor.order) == ["map#0", "map#2"]
    # aggregation still includes the cached child in order
    assert run_context.get_task_result("map").data == ["a", "cached", "c"]


@pytest.mark.asyncio
async def test_fanout_children_persisted_in_state():
    executor = EchoExecutor()
    wf = _wf({
        "map": Task(agent_id="a", prompt="{{ item }}", over=lambda ctx: ["a", "b"]),
    }, executor)
    tmp = tempfile.mkdtemp()
    run_id = await Scheduler(run_state_directory=tmp).run(wf)
    state = load_state(run_id, tmp)
    assert state.tasks["map"].status == TaskStatus.DONE
    assert state.tasks["map#0"].status == TaskStatus.DONE
    assert state.tasks["map#1"].status == TaskStatus.DONE
