from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional

from stagehand.core.context import RunContext
from stagehand.core.graph import build_graph
from stagehand.core.runstate import build_run_state, generate_run_id, load_state, save
from stagehand.core.template import resolve
from stagehand.core.workflow import RetryPolicy, TaskResult, Workflow
from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult
from stagehand.ports.logger import Logger


class TaskPhase(Enum):
    WAITING = auto()
    RUNNING = auto()
    DONE = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class _TaskOutcome:
    task_id: str
    result: Optional[TaskResult] = None
    error: Optional[Exception] = None


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
        inputs: Optional[dict[str, str]] = None,
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
        state = load_state(run_id, self.run_state_directory)
        run_context = RunContext(run_id=run_id, inputs=state.inputs)

        for task_id, task_state in state.tasks.items():
            if task_state.status == "done":
                await run_context.set_task_result(
                    task_id,
                    TaskResult(output=task_state.output, files=task_state.files),
                )

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

        for task_id in workflow.tasks:
            if run_context.get_task_result(task_id) is not None:
                phases[task_id] = TaskPhase.DONE
                for dependent_id in graph.dependents(task_id):
                    in_degree[dependent_id] -= 1

        outcome_queue: asyncio.Queue[_TaskOutcome] = asyncio.Queue()
        running_count = 0
        running_tasks: set[asyncio.Task[None]] = set()
        pending: list[str] = []

        def launch(task_id: str) -> None:
            nonlocal running_count
            phases[task_id] = TaskPhase.RUNNING
            running_count += 1
            asyncio_task = asyncio.create_task(self._run_task(task_id, workflow, run_context, outcome_queue))
            running_tasks.add(asyncio_task)
            asyncio_task.add_done_callback(running_tasks.discard)

        def try_launch(task_id: str) -> None:
            if self.max_concurrency is None or running_count < self.max_concurrency:
                launch(task_id)
            else:
                pending.append(task_id)

        for task_id in workflow.tasks:
            if in_degree[task_id] == 0 and phases[task_id] == TaskPhase.WAITING:
                try_launch(task_id)

        first_error: Optional[Exception] = None

        while running_count > 0:
            outcome = await outcome_queue.get()
            running_count -= 1

            if outcome.error is not None:
                if first_error is None:
                    first_error = outcome.error
                phases[outcome.task_id] = TaskPhase.FAILED
                _cancel_downstream(outcome.task_id, graph, phases, self._logger)
            else:
                phases[outcome.task_id] = TaskPhase.DONE
                await run_context.set_task_result(outcome.task_id, outcome.result)  # type: ignore[arg-type]

                for dependent_id in graph.dependents(outcome.task_id):
                    if phases[dependent_id] != TaskPhase.WAITING:
                        continue
                    in_degree[dependent_id] -= 1
                    if in_degree[dependent_id] == 0:
                        try_launch(dependent_id)

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
        workflow: Workflow,
        run_context: RunContext,
        outcome_queue: asyncio.Queue[_TaskOutcome],
    ) -> None:
        self._logger.info(f"task '{task_id}' starting")
        task = workflow.tasks[task_id]

        if task.fn is not None:
            try:
                result = await _call_fn_with_retry(task.fn, run_context, task.retry, task_id, self._logger, task.timeout)
            except Exception as error:
                self._logger.error(f"task '{task_id}' failed: {error}")
                await outcome_queue.put(_TaskOutcome(task_id=task_id, error=error))
                return
            self._logger.info(f"task '{task_id}' done")
            await outcome_queue.put(_TaskOutcome(task_id=task_id, result=result))
            return

        agent = workflow.agents[task.agent_id]
        executor = agent.executor or self.default_executor
        if executor is None:
            error = RuntimeError(f"task {task_id}: agent {task.agent_id!r} has no executor set")
            self._logger.error(f"task '{task_id}' failed: {error}")
            await outcome_queue.put(_TaskOutcome(task_id=task_id, error=error))
            return

        try:
            request = ExecutionRequest(
                system_prompt=agent.system_prompt,
                model=agent.model,
                tools=agent.tools,
                prompt=resolve(task.prompt, run_context),
                run_id=run_context.run_id,
                task_id=task_id,
            )
            exec_result = await _execute_with_retry(executor, request, task.retry, self._logger, task.timeout)
        except Exception as error:
            self._logger.error(f"task '{task_id}' failed: {error}")
            await outcome_queue.put(_TaskOutcome(task_id=task_id, error=error))
            return

        self._logger.info(f"task '{task_id}' done")
        await outcome_queue.put(
            _TaskOutcome(task_id=task_id, result=TaskResult(output=exec_result.output, files=exec_result.files))
        )


async def _execute_with_retry(
    executor: AgentExecutor,
    request: ExecutionRequest,
    policy: RetryPolicy,
    logger: Logger,
    timeout: Optional[float] = None,
) -> ExecutionResult:
    last_error: Optional[Exception] = None

    for attempt in range(policy.max_attempts):
        try:
            coro = executor.execute(request)
            result = await (asyncio.wait_for(coro, timeout=timeout) if timeout is not None else coro)
            return result
        except TimeoutError:
            last_error = TimeoutError(f"task '{request.task_id}' timed out after {timeout}s")
        except Exception as error:
            last_error = error
        has_remaining_attempts = attempt < policy.max_attempts - 1
        if has_remaining_attempts:
            delay_suffix = f", retrying in {policy.delay}s" if policy.delay > 0 else ", retrying"
            logger.warning(
                f"task '{request.task_id}' attempt {attempt + 1}/{policy.max_attempts} failed: {last_error}{delay_suffix}"
            )
            if policy.delay > 0:
                await asyncio.sleep(policy.delay)

    raise last_error  # type: ignore[misc]


async def _call_fn_with_retry(
    fn: Callable,
    run_context: RunContext,
    policy: RetryPolicy,
    task_id: str,
    logger: Logger,
    timeout: Optional[float] = None,
) -> TaskResult:
    last_error: Optional[Exception] = None

    for attempt in range(policy.max_attempts):
        try:
            raw = fn(run_context)
            if inspect.isawaitable(raw):
                raw = await (asyncio.wait_for(raw, timeout=timeout) if timeout is not None else raw)
            if isinstance(raw, TaskResult):
                return raw
            return TaskResult(output=str(raw))
        except TimeoutError:
            last_error = TimeoutError(f"task '{task_id}' timed out after {timeout}s")
        except Exception as error:
            last_error = error
        has_remaining_attempts = attempt < policy.max_attempts - 1
        if has_remaining_attempts:
            delay_suffix = f", retrying in {policy.delay}s" if policy.delay > 0 else ", retrying"
            logger.warning(
                f"task '{task_id}' attempt {attempt + 1}/{policy.max_attempts} failed: {last_error}{delay_suffix}"
            )
            if policy.delay > 0:
                await asyncio.sleep(policy.delay)

    raise last_error  # type: ignore[misc]


def _cancel_downstream(
    task_id: str,
    graph: "Graph",  # type: ignore[name-defined]
    phases: dict[str, TaskPhase],
    logger: Logger,
) -> None:
    for dependent_id in graph.dependents(task_id):
        if phases[dependent_id] == TaskPhase.WAITING:
            phases[dependent_id] = TaskPhase.CANCELLED
            logger.info(f"task '{dependent_id}' cancelled (upstream '{task_id}' failed)")
            _cancel_downstream(dependent_id, graph, phases, logger)
