from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ExecutionRequest:
    system_prompt: str = ""
    model: str = ""
    tools: list[str] = field(default_factory=list)
    prompt: str = ""
    run_id: str = ""
    task_id: str = ""


@dataclass
class ExecutionResult:
    output: str = ""
    files: list[str] = field(default_factory=list)
    data: Any = None


@dataclass
class ToolDefinition:
    """A custom tool that can be passed to an executor."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


class AgentExecutor(ABC):
    """Port: runs a task against an AI backend and returns the result."""

    @abstractmethod
    async def execute(self, request: ExecutionRequest) -> ExecutionResult: ...
