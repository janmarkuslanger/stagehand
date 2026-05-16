from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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


class AgentExecutor(ABC):
    """Port: runs a task against an AI backend and returns the result."""

    @abstractmethod
    async def execute(self, request: ExecutionRequest) -> ExecutionResult: ...
