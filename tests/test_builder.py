import tempfile

import pytest

from stagehand import WorkflowBuilder
from stagehand.core.workflow import DynamicOutputs, PatternOutputs, StaticOutputs
from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult


class StubExecutor(AgentExecutor):
    def __init__(self, output: str = "stub"):
        self.requests: list[ExecutionRequest] = []
        self._output = output

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.requests.append(request)
        return ExecutionResult(output=self._output, files=[])


@pytest.mark.asyncio
async def test_single_task():
    ex = StubExecutor()
    run_id = await (
        WorkflowBuilder("test")
        .agent("a", ex)
        .task("t1", agent="a", prompt="hello")
        .state_dir(tempfile.mkdtemp())
        .run()
    )
    assert run_id.startswith("sh-")
    assert len(ex.requests) == 1
    assert ex.requests[0].task_id == "t1"
    assert ex.requests[0].prompt == "hello"


@pytest.mark.asyncio
async def test_sequential_with_template():
    ex = StubExecutor(output="first result")
    run_id = await (
        WorkflowBuilder("seq")
        .agent("a", ex)
        .task("t1", agent="a", prompt="step one")
        .task("t2", agent="a", prompt="follow up: {{ tasks.t1 }}", after=["t1"])
        .state_dir(tempfile.mkdtemp())
        .run()
    )
    t2_req = next(r for r in ex.requests if r.task_id == "t2")
    assert t2_req.prompt == "follow up: first result"


@pytest.mark.asyncio
async def test_parallel_tasks_different_executors():
    ex_a = StubExecutor(output="from a")
    ex_b = StubExecutor(output="from b")
    await (
        WorkflowBuilder("parallel")
        .agent("a", ex_a, model="qwen2.5", system_prompt="You are A.")
        .agent("b", ex_b, model="gpt-4o", system_prompt="You are B.")
        .task("ta", agent="a", prompt="task for a")
        .task("tb", agent="b", prompt="task for b")
        .state_dir(tempfile.mkdtemp())
        .run()
    )
    assert len(ex_a.requests) == 1
    assert len(ex_b.requests) == 1
    assert ex_a.requests[0].system_prompt == "You are A."
    assert ex_b.requests[0].system_prompt == "You are B."
    assert ex_a.requests[0].model == "qwen2.5"
    assert ex_b.requests[0].model == "gpt-4o"


@pytest.mark.asyncio
async def test_merge_pattern():
    ex = StubExecutor(output="out")
    await (
        WorkflowBuilder("merge")
        .agent("w", ex)
        .task("pros", agent="w", prompt="pros")
        .task("cons", agent="w", prompt="cons")
        .task("summary", agent="w", prompt="{{ tasks.pros }} + {{ tasks.cons }}", after=["pros", "cons"])
        .state_dir(tempfile.mkdtemp())
        .run()
    )
    summary_req = next(r for r in ex.requests if r.task_id == "summary")
    assert summary_req.prompt == "out + out"


@pytest.mark.asyncio
async def test_input_template():
    ex = StubExecutor()
    await (
        WorkflowBuilder("inputs")
        .agent("a", ex)
        .task("t1", agent="a", prompt="ticket: {{ input.ticket_id }}")
        .state_dir(tempfile.mkdtemp())
        .run(inputs={"ticket_id": "XYZ-99"})
    )
    assert ex.requests[0].prompt == "ticket: XYZ-99"


@pytest.mark.asyncio
async def test_tools_forwarded():
    ex = StubExecutor()
    await (
        WorkflowBuilder("tools")
        .agent("a", ex, tools=["write_file", "read_file"])
        .task("t1", agent="a", prompt="use tools")
        .state_dir(tempfile.mkdtemp())
        .run()
    )
    assert ex.requests[0].tools == ["write_file", "read_file"]


def test_build_validates_unknown_agent():
    with pytest.raises(ValueError, match="unknown agent"):
        WorkflowBuilder("bad").task("t1", agent="ghost", prompt="hi").build()


def test_build_rejects_fn_and_agent_together():
    with pytest.raises(ValueError, match="not both"):
        (
            WorkflowBuilder("bad")
            .agent("a", StubExecutor())
            .task("t1", agent="a", prompt="hi", fn=lambda ctx: "x")
            .build()
        )


def test_build_validates_unknown_dependency():
    ex = StubExecutor()
    with pytest.raises(ValueError, match="unknown task"):
        (
            WorkflowBuilder("bad")
            .agent("a", ex)
            .task("t1", agent="a", prompt="hi", after=["nonexistent"])
            .build()
        )


def test_build_returns_workflow():
    ex = StubExecutor()
    wf = (
        WorkflowBuilder("wf", version="2")
        .agent("a", ex, model="x")
        .task("t1", agent="a", prompt="go")
        .build()
    )
    assert wf.name == "wf"
    assert wf.version == "2"
    assert "a" in wf.agents
    assert wf.agents["a"].executor is ex
    assert "t1" in wf.tasks


@pytest.mark.asyncio
async def test_failing_task_raises():
    class FailingExecutor(AgentExecutor):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await (
            WorkflowBuilder("fail")
            .agent("a", FailingExecutor())
            .task("t1", agent="a", prompt="will fail")
            .state_dir(tempfile.mkdtemp())
            .run()
        )


@pytest.mark.asyncio
async def test_concurrency_limits_parallel_tasks():
    import asyncio
    peak = 0
    current = 0

    class TrackingExecutor(AgentExecutor):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            nonlocal peak, current
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0)
            current -= 1
            return ExecutionResult(output="ok", files=[])

    ex = TrackingExecutor()
    await (
        WorkflowBuilder("test")
        .agent("a", ex)
        .task("t1", agent="a", prompt="p1")
        .task("t2", agent="a", prompt="p2")
        .task("t3", agent="a", prompt="p3")
        .concurrency(2)
        .state_dir(tempfile.mkdtemp())
        .run()
    )
    assert peak <= 2


def test_concurrency_invalid_raises():
    with pytest.raises(ValueError, match="max_concurrency"):
        WorkflowBuilder("test").concurrency(0)
