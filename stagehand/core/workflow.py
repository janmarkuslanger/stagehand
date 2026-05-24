from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, Union

if TYPE_CHECKING:
    from stagehand.ports.executor import AgentExecutor


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
    """
    agent_id: str = ""
    depends_on: list[str] = field(default_factory=list)
    prompt: str = ""
    outputs: OutputSpec = field(default_factory=DynamicOutputs)
    secrets: list[str] = field(default_factory=list)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    fn: Optional[Callable] = None
    timeout: Optional[float] = None

    def __post_init__(self) -> None:
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError(f"Task.timeout must be > 0, got {self.timeout!r}")


@dataclass
class TaskResult:
    """Holds the output produced by a completed task."""
    output: str = ""
    files: list[str] = field(default_factory=list)


@dataclass
class Workflow:
    """The top-level unit of execution."""
    name: str = ""
    version: str = ""
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
