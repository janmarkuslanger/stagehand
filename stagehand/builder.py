from __future__ import annotations

from typing import Callable, Optional

from stagehand.core.workflow import AgentConfig, DynamicOutputs, OutputSpec, RetryPolicy, Task, Workflow
from stagehand.core.scheduler import Scheduler
from stagehand.ports.executor import AgentExecutor
from stagehand.ports.logger import Logger


class WorkflowBuilder:
    """Fluent builder for defining and running workflows in pure Python.

    Example::

        run_id = await (
            WorkflowBuilder("haiku-pipeline")
            .agent("writer", OllamaExecutor(), model="qwen2.5", system_prompt="You write haiku.")
            .agent("critic", ClaudeExecutor(api_key="..."), model="claude-opus-4-5")
            .task("draft",  agent="writer", prompt="Write a haiku about the ocean.")
            .task("review", agent="critic",  prompt="Review this:\\n\\n{{ tasks.draft }}", after=["draft"])
            .run(inputs={"topic": "ocean"})
        )
    """

    def __init__(self, name: str, version: str = "1") -> None:
        self._name = name
        self._version = version
        self._agents: dict[str, AgentConfig] = {}
        self._tasks: dict[str, Task] = {}
        self._state_dir = ".stagehand/runs"
        self._logger: Optional[Logger] = None

    def agent(
        self,
        agent_id: str,
        executor: AgentExecutor,
        *,
        model: str = "",
        system_prompt: str = "",
        role: str = "",
        tools: Optional[list[str]] = None,
    ) -> "WorkflowBuilder":
        """Register an agent with its executor."""
        self._agents[agent_id] = AgentConfig(
            role=role,
            system_prompt=system_prompt,
            model=model,
            executor=executor,
            tools=tools or [],
        )
        return self

    def task(
        self,
        task_id: str,
        *,
        agent: Optional[str] = None,
        prompt: str = "",
        fn: Optional[Callable] = None,
        after: Optional[list[str]] = None,
        outputs: Optional[OutputSpec] = None,
        secrets: Optional[list[str]] = None,
        retry: Optional[RetryPolicy] = None,
        timeout: Optional[float] = None,
    ) -> "WorkflowBuilder":
        """Add a task node to the DAG.

        Pass either ``agent`` + ``prompt`` for an AI-agent task, or ``fn`` for
        a deterministic task that runs a plain Python callable.
        """
        self._tasks[task_id] = Task(
            agent_id=agent or "",
            prompt=prompt,
            fn=fn,
            depends_on=after or [],
            outputs=outputs or DynamicOutputs(),
            secrets=secrets or [],
            retry=retry or RetryPolicy(),
            timeout=timeout,
        )
        return self

    def state_dir(self, directory: str) -> "WorkflowBuilder":
        """Override the directory where run state is persisted."""
        self._state_dir = directory
        return self

    def logger(self, logger: Logger) -> "WorkflowBuilder":
        """Attach a logger that receives workflow and task lifecycle events."""
        self._logger = logger
        return self

    def build(self) -> Workflow:
        """Returns the Workflow without running it."""
        self._validate()
        return Workflow(
            name=self._name,
            version=self._version,
            agents=dict(self._agents),
            tasks=dict(self._tasks),
        )

    async def run(self, inputs: Optional[dict[str, str]] = None) -> str:
        """Build and run the workflow. Returns the run_id."""
        workflow = self.build()
        scheduler = Scheduler(run_state_directory=self._state_dir, logger=self._logger)
        return await scheduler.run(workflow, inputs=inputs or {})

    def _validate(self) -> None:
        if not self._name:
            raise ValueError("WorkflowBuilder: workflow name is required")
        for task_id, task in self._tasks.items():
            if task.fn is not None and (task.agent_id or task.prompt):
                raise ValueError(
                    f"WorkflowBuilder: task {task_id!r} must use either 'fn' or 'agent'/'prompt', not both"
                )
            if task.fn is None:
                if not task.agent_id:
                    raise ValueError(
                        f"WorkflowBuilder: task {task_id!r} requires either 'agent' or 'fn'"
                    )
                if task.agent_id not in self._agents:
                    raise ValueError(
                        f"WorkflowBuilder: task {task_id!r} references unknown agent {task.agent_id!r}"
                    )
            for dep in task.depends_on:
                if dep not in self._tasks:
                    raise ValueError(
                        f"WorkflowBuilder: task {task_id!r} depends on unknown task {dep!r}"
                    )
