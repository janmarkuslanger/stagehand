from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

if TYPE_CHECKING:
    from stagehand.ports.executor import AgentExecutor
    from stagehand.core.context import RunContext


@dataclass
class AgentConfig:
    """Defines an agent's role, model, executor instance, and available tools."""
    role: str = ""
    system_prompt: str = ""
    model: str = ""
    executor: Optional["AgentExecutor"] = None
    tools: list[str] = field(default_factory=list)


class StaticOutputs(list):
    """Declares a fixed list of output file names."""
    pass


@dataclass
class DynamicOutputs:
    """Indicates the agent decides which files to produce at runtime."""
    pass


@dataclass
class PatternOutputs:
    """Declares output files via a glob pattern."""
    pattern: str = ""


OutputSpec = Union[StaticOutputs, DynamicOutputs, PatternOutputs]


@dataclass
class RetryPolicy:
    """Controls if and how a failed task is retried."""
    max_attempts: int = 1
    delay: float = 0.0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError(f"RetryPolicy.max_attempts must be an integer >= 1, got {self.max_attempts!r}")
        if self.delay < 0:
            raise ValueError(f"RetryPolicy.delay must be non-negative, got {self.delay!r}")


@dataclass
class Task:
    """A single node in the workflow DAG.

    Either set ``agent_id`` + ``prompt`` to run an AI agent, or set ``fn`` to
    run a plain Python callable.  ``fn`` receives a ``RunContext`` and must
    return a ``TaskResult`` or a plain ``str``.  Both sync and async callables
    are supported.

    Runtime dynamics:

    - ``when`` — a predicate ``(RunContext) -> bool``.  When it returns falsy the
      task is *skipped*: it produces an empty result, is recorded as ``skipped``
      and its dependents still become ready (skips do not cascade).
    - ``loop_until`` — a predicate ``(RunContext, TaskResult) -> bool``.  The task
      body re-runs until the predicate returns truthy or ``max_iterations`` is
      reached.  Agent prompts may reference ``{{ loop.iteration }}`` and
      ``{{ loop.previous }}``.
    - ``over`` — a callable ``(RunContext) -> list``.  Fans the task out into one
      child per item, running the body for each.  Agent prompts may reference
      ``{{ item }}``; ``fn`` callables receive ``fn(ctx, item)``.  The task's own
      result aggregates the children (``data`` = list of child results).

    Both predicates and ``over`` may be sync or async.  ``over`` and
    ``loop_until`` cannot be combined.
    """
    agent_id: str = ""
    depends_on: list[str] = field(default_factory=list)
    prompt: str = ""
    outputs: OutputSpec = field(default_factory=DynamicOutputs)
    secrets: list[str] = field(default_factory=list)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    fn: Optional[Callable] = None
    timeout: Optional[float] = None
    when: Optional[Callable[["RunContext"], Any]] = None
    over: Optional[Callable[["RunContext"], Any]] = None
    loop_until: Optional[Callable[["RunContext", "TaskResult"], Any]] = None
    max_iterations: int = 1

    def __post_init__(self) -> None:
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError(f"Task.timeout must be > 0, got {self.timeout!r}")
        if type(self.max_iterations) is not int or self.max_iterations < 1:
            raise ValueError(
                f"Task.max_iterations must be an integer >= 1, got {self.max_iterations!r}"
            )
        if self.over is not None and self.loop_until is not None:
            raise ValueError("Task: 'over' (fan-out) and 'loop_until' (loop) cannot be combined")


@dataclass
class TaskResult:
    """Holds the output produced by a completed task.

    ``output`` is the textual result; ``data`` carries an optional structured
    value (any Python object) for downstream tasks to branch or map over.
    """
    output: str = ""
    files: list[str] = field(default_factory=list)
    data: Any = None


@dataclass
class Workflow:
    """The top-level unit of execution."""
    name: str = ""
    version: str = ""
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
