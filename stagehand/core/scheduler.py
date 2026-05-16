from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from stagehand.core.context import RunContext
from stagehand.core.graph import build_graph
from stagehand.core.runstate import build_run_state, generate_run_id, load_state, save
from stagehand.core.template import resolve
from stagehand.core.workflow import TaskResult, Workflow
from stagehand.ports.executor import AgentExecutor, ExecutionRequest


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


class Scheduler:
    """Executes a Workflow's tasks in dependency order, running independent tasks in parallel.

    Each agent can carry its own executor instance. The optional `default_executor`
    is used as fallback for agents that have none set.
    """

    def __init__(
        self,
        default_executor: Optional[AgentExecutor] = None,
        run_state_directory: str = ".stagehand/runs",
    ) -> None:
        self.default_executor = default_executor
        self.run_state_directory = run_state_directory

    async def run(
        self,
        workflow: Workflow,
        inputs: Optional[dict[str, str]] = None,
        run_label: str = "",
    ) -> str:
        """Runs all tasks in the workflow. Returns the run_id."""
        run_id = generate_run_id()
        run_context = RunContext(run_id=run_id, inputs=inputs or {})
        err = await self._execute(workflow, run_context)
        state = build_run_state(run_id, run_label, workflow, inputs or {}, run_context, err)
        save(state, self.run_state_directory)
        if err is not None:
            raise err
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

        err = await self._execute(workflow, run_context)
        new_state = build_run_state(run_id, state.workflow_file, workflow, state.inputs, run_context, err)
        save(new_state, self.run_state_directory)
        if err is not None:
            raise err
        return run_id

    async def _execute(self, workflow: Workflow, run_context: RunContext) -> Optional[Exception]:
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

        async def launch(task_id: str) -> None:
            nonlocal running_count
            phases[task_id] = TaskPhase.RUNNING
            running_count += 1
            asyncio.create_task(_run_task(task_id))

        async def _run_task(task_id: str) -> None:
            task = workflow.tasks[task_id]
            try:
                prompt = resolve(task.prompt, run_context)
                agent = workflow.agents[task.agent_id]
                executor = agent.executor or self.default_executor
                if executor is None:
                    raise RuntimeError(
                        f"task {task_id}: agent {task.agent_id!r} has no executor set"
                    )
                request = ExecutionRequest(
                    system_prompt=agent.system_prompt,
                    model=agent.model,
                    tools=agent.tools,
                    prompt=prompt,
                    run_id=run_context.run_id,
                    task_id=task_id,
                )
                exec_result = await executor.execute(request)
                await outcome_queue.put(
                    _TaskOutcome(
                        task_id=task_id,
                        result=TaskResult(output=exec_result.output, files=exec_result.files),
                    )
                )
            except Exception as exc:
                await outcome_queue.put(_TaskOutcome(task_id=task_id, error=exc))

        for task_id in workflow.tasks:
            if in_degree[task_id] == 0 and phases[task_id] == TaskPhase.WAITING:
                await launch(task_id)

        first_error: Optional[Exception] = None

        while running_count > 0:
            outcome = await outcome_queue.get()
            running_count -= 1

            if outcome.error is not None:
                if first_error is None:
                    first_error = outcome.error
                phases[outcome.task_id] = TaskPhase.FAILED
                _cancel_downstream(outcome.task_id, graph, phases)
            else:
                phases[outcome.task_id] = TaskPhase.DONE
                await run_context.set_task_result(outcome.task_id, outcome.result)  # type: ignore[arg-type]

                for dependent_id in graph.dependents(outcome.task_id):
                    if phases[dependent_id] != TaskPhase.WAITING:
                        continue
                    in_degree[dependent_id] -= 1
                    if in_degree[dependent_id] == 0:
                        await launch(dependent_id)

        return first_error


def _cancel_downstream(
    task_id: str,
    graph: "Graph",  # type: ignore[name-defined]
    phases: dict[str, TaskPhase],
) -> None:
    for dependent_id in graph.dependents(task_id):
        if phases[dependent_id] == TaskPhase.WAITING:
            phases[dependent_id] = TaskPhase.CANCELLED
            _cancel_downstream(dependent_id, graph, phases)
