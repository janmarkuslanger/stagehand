import tempfile
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest

from stagehand.core.context import RunContext
from stagehand.core.runstate import TaskStatus, load_state, save
from stagehand.core.scheduler import Scheduler
from stagehand.core.workflow import AgentConfig, RetryPolicy, Task, TaskResult, Workflow
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


def test_retry_policy_rejects_invalid_max_attempts():
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=-1)
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=True)


def test_retry_policy_rejects_negative_delay():
    with pytest.raises(ValueError, match="delay"):
        RetryPolicy(delay=-1.0)


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failure():
    attempts: list[str] = []

    class FlakyExecutor(AgentExecutor):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            attempts.append(request.task_id)
            if len(attempts) < 2:
                raise RuntimeError("transient failure")
            return ExecutionResult(output="ok", files=[])

    wf = _make_workflow(
        {"t1": Task(agent_id="a", prompt="do it", retry=RetryPolicy(max_attempts=3))},
        FlakyExecutor(),
    )
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_retry_exhausted_raises():
    attempts: list[str] = []

    class AlwaysFailExecutor(AgentExecutor):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            attempts.append(request.task_id)
            raise RuntimeError("permanent failure")

    wf = _make_workflow(
        {"t1": Task(agent_id="a", prompt="do it", retry=RetryPolicy(max_attempts=3))},
        AlwaysFailExecutor(),
    )
    with pytest.raises(RuntimeError, match="permanent failure"):
        await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_no_retry_by_default():
    attempts: list[str] = []

    class AlwaysFailExecutor(AgentExecutor):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            attempts.append(request.task_id)
            raise RuntimeError("fail")

    wf = _make_workflow(
        {"t1": Task(agent_id="a", prompt="do it")},
        AlwaysFailExecutor(),
    )
    with pytest.raises(RuntimeError):
        await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_retry_delay_is_applied_between_attempts():
    attempts: list[str] = []

    class FlakyExecutor(AgentExecutor):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            attempts.append(request.task_id)
            if len(attempts) < 3:
                raise RuntimeError("transient failure")
            return ExecutionResult(output="ok", files=[])

    wf = _make_workflow(
        {"t1": Task(agent_id="a", prompt="do it", retry=RetryPolicy(max_attempts=3, delay=1.5))},
        FlakyExecutor(),
    )
    with patch("stagehand.core.scheduler.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)

    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(1.5)


@pytest.mark.asyncio
async def test_retry_no_delay_when_delay_is_zero():
    class FlakyExecutor(AgentExecutor):
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            self.calls += 1
            if self.calls < 2:
                raise RuntimeError("transient failure")
            return ExecutionResult(output="ok", files=[])

    wf = _make_workflow(
        {"t1": Task(agent_id="a", prompt="do it", retry=RetryPolicy(max_attempts=2, delay=0.0))},
        FlakyExecutor(),
    )
    with patch("stagehand.core.scheduler.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)

    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_retry_success_runs_downstream():
    attempts: list[str] = []
    order: list[str] = []

    class FlakyThenOkExecutor(AgentExecutor):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            order.append(request.task_id)
            if request.task_id == "t1":
                attempts.append("t1")
                if len(attempts) < 2:
                    raise RuntimeError("transient failure")
            return ExecutionResult(output=f"out-{request.task_id}", files=[])

    wf = _make_workflow({
        "t1": Task(agent_id="a", prompt="flaky", retry=RetryPolicy(max_attempts=3)),
        "t2": Task(agent_id="a", prompt="downstream", depends_on=["t1"]),
    }, FlakyThenOkExecutor())
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)

    assert len(attempts) == 2
    assert "t2" in order
    assert order.index("t1") < order.index("t2")


@pytest.mark.asyncio
async def test_retry_exhausted_cancels_downstream():
    order: list[str] = []

    class AlwaysFailExecutor(AgentExecutor):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            order.append(request.task_id)
            raise RuntimeError("permanent failure")

    wf = _make_workflow({
        "t1": Task(agent_id="a", prompt="fail", retry=RetryPolicy(max_attempts=2)),
        "t2": Task(agent_id="a", prompt="downstream", depends_on=["t1"]),
    }, AlwaysFailExecutor())
    with pytest.raises(RuntimeError, match="permanent failure"):
        await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)

    assert order.count("t1") == 2
    assert "t2" not in order


@pytest.mark.asyncio
async def test_bad_template_reported_as_task_failure():
    executor = RecordingExecutor()
    wf = _make_workflow(
        {"t1": Task(agent_id="a", prompt="{{ tasks.nonexistent }}")},
        executor,
    )
    with pytest.raises(Exception):
        await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert "t1" not in executor.order


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
