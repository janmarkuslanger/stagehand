from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Union

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


@dataclass
class Task:
    """A single node in the workflow DAG."""
    agent_id: str = ""
    depends_on: list[str] = field(default_factory=list)
    prompt: str = ""
    outputs: OutputSpec = field(default_factory=DynamicOutputs)
    secrets: list[str] = field(default_factory=list)
    retry: RetryPolicy = field(default_factory=RetryPolicy)


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
