from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from stagehand.ports.executor import (
    AgentExecutor,
    ExecutionRequest,
    ExecutionResult,
    ToolDefinition,
)
from stagehand.ports.storage import ArtifactStorage

MAX_AGENT_STEPS = 20

# Provider-neutral registry of the built-in tools. Each adapter serialises
# these into its own SDK format via `_serialize_tools`. Keeping the schemas in
# one place means a new built-in tool is defined exactly once.
BUILTIN_TOOLS: dict[str, ToolDefinition] = {
    "write_file": ToolDefinition(
        name="write_file",
        description="Write text content to a file. Creates the file if it does not exist.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "content": {"type": "string", "description": "Text content to write"},
            },
            "required": ["path", "content"],
        },
        handler=lambda _: None,  # handled internally by the executor, not via handler
    ),
    "read_file": ToolDefinition(
        name="read_file",
        description="Read the text content of a file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
            },
            "required": ["path"],
        },
        handler=lambda _: None,
    ),
    "list_files": ToolDefinition(
        name="list_files",
        description="List files matching a glob pattern.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. *.md"},
            },
            "required": ["pattern"],
        },
        handler=lambda _: None,
    ),
}


@dataclass
class ToolInvocation:
    """A single tool call requested by the model, in backend-neutral form."""

    id: str
    name: str
    raw_arguments: Any  # native payload; decoded by `_parse_arguments`


@dataclass
class ParsedTurn:
    """The backend-agnostic result of one model response.

    - `text` is the assistant's text output, or `None` to leave the running
      output unchanged for this turn.
    - `tool_calls` are the tool invocations to dispatch (empty when complete).
    - `is_complete` is `True` when the agent loop should stop.
    - `assistant_message` is the provider-specific message to append to the
      transcript before the tool results (only set on a tool turn).
    """

    text: Optional[str] = None
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    is_complete: bool = True
    assistant_message: Any = None


class BaseAgentExecutor(AgentExecutor):
    """Template Method base for tool-using agent executors.

    Subclasses implement the small set of backend-specific hooks below; the
    agent loop, the built-in storage tools and custom-tool dispatch live here
    and are shared across all backends.
    """

    #: Prefix used in error messages, e.g. "claude executor".
    _label: str = "agent executor"

    def __init__(
        self,
        storage: Optional[ArtifactStorage] = None,
        extra_tools: Optional[list[ToolDefinition]] = None,
    ) -> None:
        self.storage = storage
        self.extra_tools: list[ToolDefinition] = extra_tools or []

    # ------------------------------------------------------------------ #
    # Template method: the agent loop                                    #
    # ------------------------------------------------------------------ #
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        model = request.model or self._default_model()
        messages = self._init_messages(request)
        tools = self._serialize_tools(self._resolve_tools(request.tools))

        final_output = ""
        written_files: list[str] = []
        incomplete = False

        for step in range(MAX_AGENT_STEPS):
            resp = await self._call_model(model, request, messages, tools)
            turn = self._parse_response(resp, request, step)

            if turn.text is not None:
                final_output = turn.text

            if turn.is_complete:
                incomplete = False
                break

            incomplete = True
            messages.append(turn.assistant_message)

            tool_results = []
            for call in turn.tool_calls:
                try:
                    arguments = self._parse_arguments(call.raw_arguments)
                    result_content = await self._dispatch_tool(
                        request.task_id, call.name, arguments, written_files
                    )
                    tool_results.append(
                        self._format_tool_result(call, result_content, is_error=False)
                    )
                except Exception as error:
                    tool_results.append(
                        self._format_tool_result(call, str(error), is_error=True)
                    )

            self._append_tool_results(messages, tool_results)

        if incomplete:
            raise RuntimeError(
                f"{self._label}: task {request.task_id}: "
                f"agent did not complete within {MAX_AGENT_STEPS} steps"
            )

        return ExecutionResult(output=final_output, files=written_files)

    # ------------------------------------------------------------------ #
    # Shared: tool resolution + dispatch + built-in storage tools        #
    # ------------------------------------------------------------------ #
    def _resolve_tools(self, tool_names: list[str]) -> list[ToolDefinition]:
        """Built-in tools requested by name, followed by custom tools."""
        built_in = [BUILTIN_TOOLS[name] for name in tool_names if name in BUILTIN_TOOLS]
        return built_in + self.extra_tools

    async def _dispatch_tool(
        self,
        task_id: str,
        name: str,
        raw_input: dict[str, Any],
        written_files: list[str],
    ) -> str:
        if name == "write_file":
            return await self._execute_write_file(task_id, raw_input, written_files)
        if name == "read_file":
            return await self._execute_read_file(task_id, raw_input)
        if name == "list_files":
            return await self._execute_list_files(task_id, raw_input)

        for custom_tool in self.extra_tools:
            if custom_tool.name == name:
                result = custom_tool.handler(raw_input)
                if hasattr(result, "__await__"):
                    result = await result
                return str(result)

        raise ValueError(f"unknown tool {name!r}")

    async def _execute_write_file(
        self,
        task_id: str,
        raw_input: dict[str, Any],
        written_files: list[str],
    ) -> str:
        path = raw_input.get("path", "")
        content = raw_input.get("content", "")
        if not path:
            raise ValueError("write_file: path is required")
        if self.storage is None:
            raise RuntimeError("write_file: no storage configured")
        storage_path = f"{task_id}/{path}"
        await self.storage.write(storage_path, content.encode())
        written_files.append(storage_path)
        return "ok"

    async def _execute_read_file(self, task_id: str, raw_input: dict[str, Any]) -> str:
        path = raw_input.get("path", "")
        if not path:
            raise ValueError("read_file: path is required")
        if self.storage is None:
            raise RuntimeError("read_file: no storage configured")
        data = await self.storage.read(f"{task_id}/{path}")
        return data.decode()

    async def _execute_list_files(self, task_id: str, raw_input: dict[str, Any]) -> str:
        pattern = raw_input.get("pattern", "")
        full_pattern = f"{task_id}/{pattern}" if pattern else f"{task_id}/*"
        if self.storage is None:
            raise RuntimeError("list_files: no storage configured")
        files = await self.storage.list(full_pattern)
        return "\n".join(files)

    # ------------------------------------------------------------------ #
    # Backend-specific hooks                                             #
    # ------------------------------------------------------------------ #
    @abstractmethod
    def _default_model(self) -> str:
        """Model id to use when the request does not specify one."""

    @abstractmethod
    def _init_messages(self, request: ExecutionRequest) -> list[Any]:
        """Build the initial transcript for this backend."""

    @abstractmethod
    def _serialize_tools(self, tools: list[ToolDefinition]) -> Any:
        """Translate neutral tool definitions into the backend's tool format."""

    @abstractmethod
    async def _call_model(
        self,
        model: str,
        request: ExecutionRequest,
        messages: list[Any],
        tools: Any,
    ) -> Any:
        """Perform one model call and return the raw response."""

    @abstractmethod
    def _parse_response(
        self, resp: Any, request: ExecutionRequest, step: int
    ) -> ParsedTurn:
        """Interpret a raw response into a backend-agnostic `ParsedTurn`."""

    @abstractmethod
    def _format_tool_result(
        self, call: ToolInvocation, content: str, is_error: bool
    ) -> Any:
        """Build the transcript entry carrying a tool's result."""

    @abstractmethod
    def _append_tool_results(self, messages: list[Any], tool_results: list[Any]) -> None:
        """Append the formatted tool results to the transcript."""

    def _parse_arguments(self, raw_arguments: Any) -> dict[str, Any]:
        """Decode a tool call's arguments into a dict. Default: already a dict."""
        return raw_arguments
