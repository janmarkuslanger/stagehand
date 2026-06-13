import tempfile

import pytest

from stagehand.core.scheduler import Scheduler
from stagehand.core.workflow import AgentConfig, Task, TaskResult, Workflow
from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult


class CountingExecutor(AgentExecutor):
    """Returns 'iter-N' and records the prompt it received each call."""

    def __init__(self):
        self.calls = 0
        self.prompts: list[str] = []

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.prompts.append(request.prompt)
        self.calls += 1
        return ExecutionResult(output=f"iter-{self.calls}")


def _wf(task, executor):
    return Workflow(name="t", agents={"a": AgentConfig(executor=executor, tools=[])}, tasks={"t1": task})


@pytest.mark.asyncio
async def test_default_single_run():
    executor = CountingExecutor()
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(
        _wf(Task(agent_id="a", prompt="go"), executor)
    )
    assert executor.calls == 1


@pytest.mark.asyncio
async def test_loop_stops_when_predicate_true():
    executor = CountingExecutor()
    # stop once output reaches iter-3
    task = Task(
        agent_id="a",
        prompt="go",
        loop_until=lambda ctx, result: result.output == "iter-3",
        max_iterations=10,
    )
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(_wf(task, executor))
    assert executor.calls == 3


@pytest.mark.asyncio
async def test_loop_respects_max_iterations():
    executor = CountingExecutor()
    task = Task(
        agent_id="a",
        prompt="go",
        loop_until=lambda ctx, result: False,  # never stop early
        max_iterations=4,
    )
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(_wf(task, executor))
    assert executor.calls == 4


@pytest.mark.asyncio
async def test_loop_exposes_iteration_and_previous():
    executor = CountingExecutor()
    task = Task(
        agent_id="a",
        prompt="i={{ loop.iteration }} prev={{ loop.previous }}",
        loop_until=lambda ctx, result: False,
        max_iterations=3,
    )
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(_wf(task, executor))
    assert executor.prompts == [
        "i=0 prev=",
        "i=1 prev=iter-1",
        "i=2 prev=iter-2",
    ]


@pytest.mark.asyncio
async def test_loop_fn_task():
    calls = {"n": 0}

    def body(ctx):
        calls["n"] += 1
        return f"v{calls['n']}"

    wf = Workflow(
        name="t",
        agents={},
        tasks={"t1": Task(fn=body, loop_until=lambda ctx, r: r.output == "v2", max_iterations=5)},
    )
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert calls["n"] == 2


def test_over_and_loop_cannot_combine():
    with pytest.raises(ValueError, match="cannot be combined"):
        Task(agent_id="a", prompt="x", over=lambda ctx: [], loop_until=lambda ctx, r: True)


def test_max_iterations_must_be_positive_int():
    with pytest.raises(ValueError, match="max_iterations"):
        Task(agent_id="a", prompt="x", max_iterations=0)
