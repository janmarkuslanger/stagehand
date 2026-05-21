"""Stagehand — asyncio DAG workflow engine for AI agents."""

from stagehand.adapters.executor.claude import ClaudeExecutor
from stagehand.adapters.executor.ollama import OllamaExecutor
from stagehand.adapters.logger import NullLogger, StdlibLogger
from stagehand.adapters.secrets.env import EnvSecretProvider
from stagehand.adapters.storage.filesystem import FilesystemStorage
from stagehand.builder import WorkflowBuilder
from stagehand.core.context import RunContext
from stagehand.core.graph import build_graph
from stagehand.core.runstate import RunState, TaskState, generate_run_id, load_state, save
from stagehand.core.scheduler import Scheduler
from stagehand.core.workflow import (
    AgentConfig,
    DynamicOutputs,
    PatternOutputs,
    RetryPolicy,
    StaticOutputs,
    Task,
    TaskResult,
    Workflow,
)
from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult, ToolDefinition
from stagehand.ports.logger import Logger
from stagehand.ports.secrets import SecretProvider
from stagehand.ports.storage import ArtifactStorage

__all__ = [
    # Primary entry point
    "WorkflowBuilder",
    # Core
    "Workflow",
    "Task",
    "AgentConfig",
    "TaskResult",
    "RetryPolicy",
    "StaticOutputs",
    "DynamicOutputs",
    "PatternOutputs",
    "RunContext",
    "Scheduler",
    "build_graph",
    # Run state
    "RunState",
    "TaskState",
    "generate_run_id",
    "load_state",
    "save",
    # Ports (ABCs — extension points)
    "AgentExecutor",
    "ExecutionRequest",
    "ExecutionResult",
    "ArtifactStorage",
    "Logger",
    "SecretProvider",
    # Adapters
    "ClaudeExecutor",
    "OllamaExecutor",
    "StdlibLogger",
    "NullLogger",
    "FilesystemStorage",
    "EnvSecretProvider",
    "ToolDefinition",
]
