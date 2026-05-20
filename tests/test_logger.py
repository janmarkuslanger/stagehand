import logging
import tempfile
from unittest.mock import patch

import pytest

from stagehand.adapters.logger import NullLogger, StdlibLogger
from stagehand.core.scheduler import Scheduler
from stagehand.core.workflow import AgentConfig, RetryPolicy, Task, Workflow
from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult
from stagehand.ports.logger import Logger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class CapturingLogger(Logger):
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def debug(self, message: str) -> None:
        self.records.append(("debug", message))

    def info(self, message: str) -> None:
        self.records.append(("info", message))

    def warning(self, message: str) -> None:
        self.records.append(("warning", message))

    def error(self, message: str) -> None:
        self.records.append(("error", message))

    def levels(self) -> list[str]:
        return [r[0] for r in self.records]

    def messages(self) -> list[str]:
        return [r[1] for r in self.records]

    def has(self, level: str, fragment: str) -> bool:
        return any(level == lvl and fragment in msg for lvl, msg in self.records)


class SimpleExecutor(AgentExecutor):
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(output=f"out-{request.task_id}", files=[])


class FailingExecutor(AgentExecutor):
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raise RuntimeError("bang")


def _wf(tasks: dict, executor: AgentExecutor | None = None) -> Workflow:
    agents = {"a": AgentConfig(executor=executor or SimpleExecutor(), tools=[])} if executor is not False else {}
    return Workflow(name="test", agents=agents, tasks=tasks)


# ---------------------------------------------------------------------------
# StdlibLogger
# ---------------------------------------------------------------------------

def test_stdlib_logger_delegates_to_python_logging(caplog):
    with caplog.at_level(logging.DEBUG, logger="stagehand"):
        log = StdlibLogger(suppress_http_logs=False)
        log.debug("d")
        log.info("i")
        log.warning("w")
        log.error("e")

    messages = [r.message for r in caplog.records]
    assert "d" in messages
    assert "i" in messages
    assert "w" in messages
    assert "e" in messages


def test_stdlib_logger_suppresses_httpx_by_default():
    StdlibLogger()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_stdlib_logger_keeps_httpx_when_opted_out():
    # Reset first so the previous test doesn't interfere
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    StdlibLogger(suppress_http_logs=False)
    assert logging.getLogger("httpx").level == logging.NOTSET


# ---------------------------------------------------------------------------
# NullLogger
# ---------------------------------------------------------------------------

def test_null_logger_does_not_raise():
    log = NullLogger()
    log.debug("x")
    log.info("x")
    log.warning("x")
    log.error("x")


# ---------------------------------------------------------------------------
# Scheduler logging — task lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scheduler_logs_workflow_start_and_finish():
    log = CapturingLogger()
    wf = _wf({"t1": Task(agent_id="a", prompt="go")})
    await Scheduler(run_state_directory=tempfile.mkdtemp(), logger=log).run(wf)
    assert log.has("info", "workflow 'test' started")
    assert log.has("info", "workflow 'test' finished")


@pytest.mark.asyncio
async def test_scheduler_logs_task_start_and_done():
    log = CapturingLogger()
    wf = _wf({"t1": Task(agent_id="a", prompt="go")})
    await Scheduler(run_state_directory=tempfile.mkdtemp(), logger=log).run(wf)
    assert log.has("info", "task 't1' starting")
    assert log.has("info", "task 't1' done")


@pytest.mark.asyncio
async def test_scheduler_logs_task_failure():
    log = CapturingLogger()
    wf = _wf({"t1": Task(agent_id="a", prompt="go")}, executor=FailingExecutor())
    with pytest.raises(RuntimeError):
        await Scheduler(run_state_directory=tempfile.mkdtemp(), logger=log).run(wf)
    assert log.has("error", "task 't1' failed")
    assert log.has("error", "workflow 'test' failed")


@pytest.mark.asyncio
async def test_scheduler_logs_cancelled_downstream():
    log = CapturingLogger()
    wf = _wf({
        "t1": Task(agent_id="a", prompt="go"),
        "t2": Task(agent_id="a", prompt="after", depends_on=["t1"]),
    }, executor=FailingExecutor())
    with pytest.raises(RuntimeError):
        await Scheduler(run_state_directory=tempfile.mkdtemp(), logger=log).run(wf)
    assert log.has("info", "task 't2' cancelled")


@pytest.mark.asyncio
async def test_scheduler_logs_retry_warning():
    calls = []

    class FlakyExecutor(AgentExecutor):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("transient")
            return ExecutionResult(output="ok", files=[])

    log = CapturingLogger()
    wf = _wf(
        {"t1": Task(agent_id="a", prompt="go", retry=RetryPolicy(max_attempts=3))},
        executor=FlakyExecutor(),
    )
    await Scheduler(run_state_directory=tempfile.mkdtemp(), logger=log).run(wf)
    assert log.has("warning", "task 't1' attempt 1/3 failed")
    assert "retrying" in " ".join(log.messages())


@pytest.mark.asyncio
async def test_scheduler_logs_retry_with_delay_message():
    calls = []

    class FlakyExecutor(AgentExecutor):
        async def execute(self, request: ExecutionRequest) -> ExecutionResult:
            calls.append(1)
            if len(calls) < 2:
                raise RuntimeError("transient")
            return ExecutionResult(output="ok", files=[])

    log = CapturingLogger()
    wf = _wf(
        {"t1": Task(agent_id="a", prompt="go", retry=RetryPolicy(max_attempts=2, delay=1.0))},
        executor=FlakyExecutor(),
    )
    with patch("stagehand.core.scheduler.asyncio.sleep"):
        await Scheduler(run_state_directory=tempfile.mkdtemp(), logger=log).run(wf)
    assert log.has("warning", "retrying in 1.0s")


@pytest.mark.asyncio
async def test_scheduler_logs_fn_task_lifecycle():
    log = CapturingLogger()

    def fetch(ctx):
        return "tickets"

    wf = Workflow(
        name="test",
        agents={},
        tasks={"fetch": Task(fn=fetch)},
    )
    await Scheduler(run_state_directory=tempfile.mkdtemp(), logger=log).run(wf)
    assert log.has("info", "task 'fetch' starting")
    assert log.has("info", "task 'fetch' done")


@pytest.mark.asyncio
async def test_scheduler_logs_fn_task_failure():
    log = CapturingLogger()

    def broken(ctx):
        raise RuntimeError("api down")

    wf = Workflow(
        name="test",
        agents={},
        tasks={"fetch": Task(fn=broken)},
    )
    with pytest.raises(RuntimeError):
        await Scheduler(run_state_directory=tempfile.mkdtemp(), logger=log).run(wf)
    assert log.has("error", "task 'fetch' failed")


@pytest.mark.asyncio
async def test_scheduler_without_logger_still_works():
    """Default (no logger) must not raise."""
    wf = _wf({"t1": Task(agent_id="a", prompt="go")})
    run_id = await Scheduler(run_state_directory=tempfile.mkdtemp()).run(wf)
    assert run_id.startswith("sh-")
