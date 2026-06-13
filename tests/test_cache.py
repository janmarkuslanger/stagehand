import tempfile
from typing import Optional

import pytest

from stagehand.adapters.cache.filesystem import FilesystemCache
from stagehand.adapters.cache.memory import InMemoryCache
from stagehand.core.scheduler import Scheduler
from stagehand.core.workflow import AgentConfig, Task, Workflow
from stagehand.ports.cache import cache_key
from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult


class CountingExecutor(AgentExecutor):
    """Counts how many times each task actually reaches the backend."""

    def __init__(self, outputs: Optional[dict] = None):
        self.calls: list[str] = []
        self.outputs = outputs or {}

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append(request.task_id)
        output = self.outputs.get(request.task_id, f"output-{request.task_id}")
        return ExecutionResult(output=output, files=[])


def _make_workflow(tasks: dict, executor: AgentExecutor) -> Workflow:
    return Workflow(
        name="test",
        agents={"a": AgentConfig(executor=executor, model="m", system_prompt="s", tools=[])},
        tasks=tasks,
    )


# --- cache_key --------------------------------------------------------------


def test_cache_key_ignores_run_and_task_id():
    a = ExecutionRequest(model="m", system_prompt="s", prompt="p", run_id="r1", task_id="t1")
    b = ExecutionRequest(model="m", system_prompt="s", prompt="p", run_id="r2", task_id="t2")
    assert cache_key(a) == cache_key(b)


def test_cache_key_differs_on_prompt():
    a = ExecutionRequest(model="m", system_prompt="s", prompt="one")
    b = ExecutionRequest(model="m", system_prompt="s", prompt="two")
    assert cache_key(a) != cache_key(b)


def test_cache_key_tool_order_independent():
    a = ExecutionRequest(model="m", prompt="p", tools=["read_file", "write_file"])
    b = ExecutionRequest(model="m", prompt="p", tools=["write_file", "read_file"])
    assert cache_key(a) == cache_key(b)


# --- InMemoryCache ----------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_get_miss_then_hit():
    cache = InMemoryCache()
    assert await cache.get("k") is None
    await cache.set("k", ExecutionResult(output="hi"))
    hit = await cache.get("k")
    assert hit is not None and hit.output == "hi"


# --- Scheduler integration --------------------------------------------------


@pytest.mark.asyncio
async def test_cache_skips_identical_task_across_runs():
    executor = CountingExecutor()
    cache = InMemoryCache()
    tasks = {"t1": Task(agent_id="a", prompt="do it")}

    s1 = Scheduler(run_state_directory=tempfile.mkdtemp(), cache=cache)
    await s1.run(_make_workflow(tasks, executor))
    s2 = Scheduler(run_state_directory=tempfile.mkdtemp(), cache=cache)
    await s2.run(_make_workflow(tasks, executor))

    # Backend was hit only once; the second run was served from cache.
    assert executor.calls == ["t1"]


@pytest.mark.asyncio
async def test_no_cache_recomputes():
    executor = CountingExecutor()
    tasks = {"t1": Task(agent_id="a", prompt="do it")}
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(_make_workflow(tasks, executor))
    await Scheduler(run_state_directory=tempfile.mkdtemp()).run(_make_workflow(tasks, executor))
    assert executor.calls == ["t1", "t1"]


@pytest.mark.asyncio
async def test_cache_returns_stored_output():
    executor = CountingExecutor(outputs={"t1": "cached-value"})
    cache = InMemoryCache()
    tasks = {"t1": Task(agent_id="a", prompt="do it")}

    await Scheduler(run_state_directory=tempfile.mkdtemp(), cache=cache).run(
        _make_workflow(tasks, executor)
    )
    # Swap in an executor that would return something else; cache must win.
    other = CountingExecutor(outputs={"t1": "fresh-value"})
    sd = tempfile.mkdtemp()
    await Scheduler(run_state_directory=sd, cache=cache).run(_make_workflow(tasks, other))

    # The second executor was never called — the cached result was served.
    assert other.calls == []


# --- FilesystemCache --------------------------------------------------------


@pytest.mark.asyncio
async def test_filesystem_cache_roundtrip():
    cache = FilesystemCache(root=tempfile.mkdtemp())
    assert await cache.get("k") is None
    await cache.set("k", ExecutionResult(output="persisted", files=["a.md"]))
    hit = await cache.get("k")
    assert hit is not None
    assert hit.output == "persisted"
    assert hit.files == ["a.md"]


@pytest.mark.asyncio
async def test_filesystem_cache_survives_new_instance():
    root = tempfile.mkdtemp()
    await FilesystemCache(root=root).set("k", ExecutionResult(output="kept"))
    hit = await FilesystemCache(root=root).get("k")
    assert hit is not None and hit.output == "kept"


@pytest.mark.asyncio
async def test_filesystem_cache_persists_across_schedulers():
    executor = CountingExecutor()
    root = tempfile.mkdtemp()
    tasks = {"t1": Task(agent_id="a", prompt="do it")}

    await Scheduler(run_state_directory=tempfile.mkdtemp(), cache=FilesystemCache(root=root)).run(
        _make_workflow(tasks, executor)
    )
    await Scheduler(run_state_directory=tempfile.mkdtemp(), cache=FilesystemCache(root=root)).run(
        _make_workflow(tasks, executor)
    )
    assert executor.calls == ["t1"]
