from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Awaitable, Callable, Optional

from stagehand.core.context import RunContext
from stagehand.core.graph import Graph, build_graph
from stagehand.core.runstate import build_run_state, generate_run_id, load_state, save
from stagehand.core.template import resolve
from stagehand.core.workflow import RetryPolicy, Task, TaskResult, Workflow
from stagehand.ports.executor import AgentExecutor, ExecutionRequest
from stagehand.ports.logger import Logger

#: Sentinel marking a task that is not a fan-out child (carries no item).
_NO_ITEM = object()


@dataclass
class _RetryContext:
    task_id: str
    policy: RetryPolicy
    logger: Logger
    timeout: Optional[float]


class TaskPhase(Enum):
    WAITING = auto()
    RUNNING = auto()
    DONE = auto()
    FAILED = auto()
    CANCELLED = auto()
    SKIPPED = auto()


@dataclass
class _TaskOutcome:
    task_id: str
    result: Optional[TaskResult] = None
    error: Optional[Exception] = None
    skipped: bool = False
    expand: bool = False
    items: Optional[list] = None


class _NullLogger(Logger):
    def debug(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...


class Scheduler:
    """Executes a Workflow's tasks in dependency order, running independent tasks in parallel.

    Each agent can carry its own executor instance. The optional `default_executor`
    is used as fallback for agents that have none set.
    """

    def __init__(
        self,
        default_executor: Optional[AgentExecutor] = None,
        run_state_directory: str = ".stagehand/runs",
        logger: Optional[Logger] = None,
        max_concurrency: Optional[int] = None,
    ) -> None:
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency!r}")
        self.default_executor = default_executor
        self.run_state_directory = run_state_directory
        self._logger: Logger = logger or _NullLogger()
        self.max_concurrency = max_concurrency

    async def run(
        self,
        workflow: Workflow,
        inputs: Optional[dict[str, Any]] = None,
        run_label: str = "",
    ) -> str:
        """Runs all tasks in the workflow. Returns the run_id."""
        run_id = generate_run_id()
        run_context = RunContext(run_id=run_id, inputs=inputs or {})
        error = await self._execute(workflow, run_context)
        state = build_run_state(run_id, run_label, workflow, inputs or {}, run_context, error)
        save(state, self.run_state_directory)
        if error is not None:
            raise error
        return run_id

    async def resume(self, run_id: str, workflow: Workflow) -> str:
        """Resumes a previously saved run, skipping already-completed tasks.

        The workflow must be passed explicitly since there is no YAML file to reload.
        """
        from stagehand.core.runstate import TaskStatus

        state = load_state(run_id, self.run_state_directory)
        run_context = RunContext(run_id=run_id, inputs=state.inputs)

        for task_id, task_state in state.tasks.items():
            if task_state.status == TaskStatus.DONE:
                await run_context.set_task_result(
                    task_id,
                    TaskResult(output=task_state.output, files=task_state.files),
                )
            elif task_state.status == TaskStatus.SKIPPED:
                run_context.mark_skipped(task_id)
                await run_context.set_task_result(task_id, TaskResult())

        error = await self._execute(workflow, run_context)
        new_state = build_run_state(run_id, state.workflow_file, workflow, state.inputs, run_context, error)
        save(new_state, self.run_state_directory)
        if error is not None:
            raise error
        return run_id

    async def _execute(self, workflow: Workflow, run_context: RunContext) -> Optional[Exception]:
        self._logger.info(f"workflow '{workflow.name}' started [run={run_context.run_id}]")

        graph = build_graph(workflow)

        in_degree: dict[str, int] = {
            task_id: len(task.depends_on) for task_id, task in workflow.tasks.items()
        }
        phases: dict[str, TaskPhase] = {
            task_id: TaskPhase.WAITING for task_id in workflow.tasks
        }

        # Fan-out is the one feature that mutates the graph at runtime. We keep
        # the dynamically generated children in run-local structures rather than
        # mutating `workflow`, so the workflow definition stays immutable.
        tasks_by_id: dict[str, Task] = dict(workflow.tasks)
        dynamic_dependents: dict[str, list[str]] = {}
        child_items: dict[str, Any] = {}
        map_children: dict[str, list[str]] = {}
        expanded: set[str] = set()

        outcome_queue: asyncio.Queue[_TaskOutcome] = asyncio.Queue()
        running_count = 0
        running_tasks: set[asyncio.Task[None]] = set()
        pending: list[str] = []
        first_error: Optional[Exception] = None

        def dependents_of(task_id: str) -> list[str]:
            return graph.dependents(task_id) + dynamic_dependents.get(task_id, [])

        for task_id in workflow.tasks:
            if run_context.get_task_result(task_id) is not None or run_context.is_skipped(task_id):
                phases[task_id] = TaskPhase.DONE
                for dependent_id in dependents_of(task_id):
                    in_degree[dependent_id] -= 1

        def launch(task_id: str) -> None:
            nonlocal running_count
            phases[task_id] = TaskPhase.RUNNING
            running_count += 1
            task = tasks_by_id[task_id]
            item = child_items.get(task_id, _NO_ITEM)
            asyncio_task = asyncio.create_task(
                self._run_task(task_id, task, item, workflow, run_context, outcome_queue)
            )
            running_tasks.add(asyncio_task)
            asyncio_task.add_done_callback(running_tasks.discard)

        def try_launch(task_id: str) -> None:
            if self.max_concurrency is None or running_count < self.max_concurrency:
                launch(task_id)
            else:
                pending.append(task_id)

        async def on_ready(task_id: str) -> None:
            # A map node becomes "ready" a second time once all its children are
            # done — that is the join, not a re-run of the body.
            if task_id in expanded:
                await do_join(task_id)
            else:
                try_launch(task_id)

        async def release_dependents(task_id: str) -> None:
            for dependent_id in dependents_of(task_id):
                if phases.get(dependent_id) != TaskPhase.WAITING:
                    continue
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    await on_ready(dependent_id)

        def cancel_downstream(task_id: str) -> None:
            for dependent_id in dependents_of(task_id):
                if phases.get(dependent_id) == TaskPhase.WAITING:
                    phases[dependent_id] = TaskPhase.CANCELLED
                    self._logger.info(f"task '{dependent_id}' cancelled (upstream '{task_id}' failed)")
                    cancel_downstream(dependent_id)

        async def handle_expand(map_id: str, items: list) -> None:
            base = tasks_by_id[map_id]
            child_ids: list[str] = []
            for index, item in enumerate(items):
                child_id = f"{map_id}#{index}"
                tasks_by_id[child_id] = _child_task(base)
                child_items[child_id] = item
                dynamic_dependents[child_id] = [map_id]
                phases[child_id] = TaskPhase.WAITING
                in_degree[child_id] = 0
                child_ids.append(child_id)
            map_children[map_id] = child_ids
            expanded.add(map_id)
            phases[map_id] = TaskPhase.WAITING  # now waiting on its children

            to_launch: list[str] = []
            for child_id in child_ids:
                if run_context.get_task_result(child_id) is not None:
                    phases[child_id] = TaskPhase.DONE  # already done (resume)
                else:
                    to_launch.append(child_id)
            in_degree[map_id] = len(to_launch)

            if not to_launch:
                await do_join(map_id)
            else:
                for child_id in to_launch:
                    try_launch(child_id)

        async def do_join(map_id: str) -> None:
            outputs: list[str] = []
            data: list[Any] = []
            files: list[str] = []
            for child_id in map_children.get(map_id, []):
                result = run_context.get_task_result(child_id)
                if result is None:
                    continue
                outputs.append(result.output)
                data.append(result.data if result.data is not None else result.output)
                files.extend(result.files)
            phases[map_id] = TaskPhase.DONE
            await run_context.set_task_result(
                map_id, TaskResult(output="\n".join(outputs), files=files, data=data)
            )
            self._logger.info(f"task '{map_id}' done")
            await release_dependents(map_id)

        for task_id in workflow.tasks:
            if in_degree[task_id] == 0 and phases[task_id] == TaskPhase.WAITING:
                try_launch(task_id)

        while running_count > 0:
            outcome = await outcome_queue.get()
            running_count -= 1

            if outcome.error is not None:
                if first_error is None:
                    first_error = outcome.error
                phases[outcome.task_id] = TaskPhase.FAILED
                cancel_downstream(outcome.task_id)
            elif outcome.skipped:
                phases[outcome.task_id] = TaskPhase.SKIPPED
                run_context.mark_skipped(outcome.task_id)
                await run_context.set_task_result(outcome.task_id, TaskResult())
                await release_dependents(outcome.task_id)
            elif outcome.expand:
                await handle_expand(outcome.task_id, outcome.items or [])
            else:
                phases[outcome.task_id] = TaskPhase.DONE
                await run_context.set_task_result(outcome.task_id, outcome.result)  # type: ignore[arg-type]
                await release_dependents(outcome.task_id)

            while pending and (self.max_concurrency is None or running_count < self.max_concurrency):
                launch(pending.pop(0))

        if first_error is not None:
            self._logger.error(
                f"workflow '{workflow.name}' failed: {first_error} [run={run_context.run_id}]"
            )
        else:
            self._logger.info(f"workflow '{workflow.name}' finished [run={run_context.run_id}]")

        return first_error

    async def _run_task(
        self,
        task_id: str,
        task: Task,
        item: Any,
        workflow: Workflow,
        run_context: RunContext,
        outcome_queue: asyncio.Queue[_TaskOutcome],
    ) -> None:
        self._logger.info(f"task '{task_id}' starting")
        ctx = _RetryContext(task_id=task_id, policy=task.retry, logger=self._logger, timeout=task.timeout)

        # Conditional skip — evaluated before any work.
        if task.when is not None:
            try:
                proceed = await _maybe_await(task.when(run_context))
            except Exception as error:
                self._logger.error(f"task '{task_id}' failed: {error}")
                await outcome_queue.put(_TaskOutcome(task_id=task_id, error=error))
                return
            if not proceed:
                self._logger.info(f"task '{task_id}' skipped")
                await outcome_queue.put(_TaskOutcome(task_id=task_id, skipped=True))
                return

        # Fan-out — resolve the collection and let the scheduler expand it.
        if task.over is not None:
            try:
                items = list(await _maybe_await(task.over(run_context)))
            except Exception as error:
                self._logger.error(f"task '{task_id}' failed: {error}")
                await outcome_queue.put(_TaskOutcome(task_id=task_id, error=error))
                return
            await outcome_queue.put(_TaskOutcome(task_id=task_id, expand=True, items=items))
            return

        try:
            result = await _retry(
                lambda: self._run_body(task_id, task, item, workflow, run_context), ctx
            )
        except Exception as error:
            self._logger.error(f"task '{task_id}' failed: {error}")
            await outcome_queue.put(_TaskOutcome(task_id=task_id, error=error))
            return

        self._logger.info(f"task '{task_id}' done")
        await outcome_queue.put(_TaskOutcome(task_id=task_id, result=result))

    async def _run_body(
        self,
        task_id: str,
        task: Task,
        item: Any,
        workflow: Workflow,
        run_context: RunContext,
    ) -> TaskResult:
        """Runs the task body once, or repeatedly when ``loop_until`` is set."""
        if task.fn is not None:
            return await self._run_fn(task, item, run_context)
        return await self._run_agent(task_id, task, item, workflow, run_context)

    async def _run_fn(self, task: Task, item: Any, run_context: RunContext) -> TaskResult:
        result = TaskResult()
        for _ in range(task.max_iterations):
            raw = task.fn(run_context) if item is _NO_ITEM else task.fn(run_context, item)
            if inspect.isawaitable(raw):
                raw = await raw
            result = _coerce_result(raw)
            if task.loop_until is not None and await _maybe_await(task.loop_until(run_context, result)):
                break
        return result

    async def _run_agent(
        self,
        task_id: str,
        task: Task,
        item: Any,
        workflow: Workflow,
        run_context: RunContext,
    ) -> TaskResult:
        agent = workflow.agents[task.agent_id]
        executor = agent.executor or self.default_executor
        if executor is None:
            raise RuntimeError(f"task {task_id}: agent {task.agent_id!r} has no executor set")

        previous_output = ""
        result = TaskResult()
        for iteration in range(task.max_iterations):
            extra: dict[str, Any] = {}
            if item is not _NO_ITEM:
                extra["item"] = item
            if task.loop_until is not None:
                extra["loop"] = {"iteration": iteration, "previous": previous_output}
            request = ExecutionRequest(
                system_prompt=agent.system_prompt,
                model=agent.model,
                tools=agent.tools,
                prompt=resolve(task.prompt, run_context, extra or None),
                run_id=run_context.run_id,
                task_id=task_id,
            )
            exec_result = await executor.execute(request)
            result = TaskResult(output=exec_result.output, files=exec_result.files, data=exec_result.data)
            if task.loop_until is not None and await _maybe_await(task.loop_until(run_context, result)):
                break
            previous_output = result.output
        return result


def _child_task(base: Task) -> Task:
    """Builds a fan-out child that runs the body once for a single item."""
    return Task(
        agent_id=base.agent_id,
        prompt=base.prompt,
        fn=base.fn,
        retry=base.retry,
        timeout=base.timeout,
    )


def _coerce_result(raw: Any) -> TaskResult:
    if isinstance(raw, TaskResult):
        return raw
    if isinstance(raw, str):
        return TaskResult(output=raw)
    return TaskResult(output=str(raw), data=raw)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _retry(
    coro_factory: Callable[[], Awaitable[Any]],
    ctx: _RetryContext,
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(ctx.policy.max_attempts):
        try:
            coro = coro_factory()
            result = await (
                asyncio.wait_for(coro, timeout=ctx.timeout)
                if ctx.timeout is not None else coro
            )
            return result
        except TimeoutError as error:
            last_error = (
                TimeoutError(f"task '{ctx.task_id}' timed out after {ctx.timeout}s")
                if ctx.timeout is not None else error
            )
        except Exception as error:
            last_error = error
        has_remaining_attempts = attempt < ctx.policy.max_attempts - 1
        if has_remaining_attempts:
            delay_suffix = f", retrying in {ctx.policy.delay}s" if ctx.policy.delay > 0 else ", retrying"
            ctx.logger.warning(
                f"task '{ctx.task_id}' attempt {attempt + 1}/{ctx.policy.max_attempts} failed: {last_error}{delay_suffix}"
            )
            if ctx.policy.delay > 0:
                await asyncio.sleep(ctx.policy.delay)
    raise last_error  # type: ignore[misc]
