from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import anthropic

from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult
from stagehand.ports.storage import ArtifactStorage

MAX_AGENT_STEPS = 20
DEFAULT_MODEL = "claude-opus-4-5"


@dataclass
class ToolDefinition:
    """A custom tool that can be passed to ClaudeExecutor."""
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


class ClaudeExecutor(AgentExecutor):
    """Runs a task by calling the Anthropic Messages API.

    Extra tools can be passed at construction time, enabling extension
    without modifying core code:

        executor = ClaudeExecutor(api_key="...", extra_tools=[MyTool])
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        storage: Optional[ArtifactStorage] = None,
        extra_tools: Optional[list[ToolDefinition]] = None,
    ) -> None:
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.storage = storage
        self.extra_tools: list[ToolDefinition] = extra_tools or []

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        model = request.model or DEFAULT_MODEL
        system_prompt = request.system_prompt or "You are a helpful AI assistant."

        built_in_tools = _build_tools(request.tools)
        custom_tool_params = [
            anthropic.types.ToolParam(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema,
            )
            for t in self.extra_tools
        ]
        all_tools = built_in_tools + custom_tool_params

        messages: list[anthropic.types.MessageParam] = [
            {"role": "user", "content": request.prompt}
        ]

        final_output = ""
        written_files: list[str] = []
        last_stop_reason: Optional[str] = None

        for step in range(MAX_AGENT_STEPS):
            resp = await self.client.messages.create(
                model=model,
                max_tokens=16000,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
                tools=all_tools if all_tools else [],
            )

            last_stop_reason = resp.stop_reason

            for block in resp.content:
                if block.type == "text":
                    final_output = block.text

            if resp.stop_reason != "tool_use":
                break

            messages.append({"role": "assistant", "content": resp.content})

            tool_results: list[anthropic.types.ToolResultBlockParam] = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                try:
                    result_content = await self._dispatch_tool(
                        request.task_id, block, written_files
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_content,
                        }
                    )
                except Exception as error:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(error),
                            "is_error": True,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})

        if last_stop_reason == "tool_use":
            raise RuntimeError(
                f"claude executor: task {request.task_id}: agent did not complete within {MAX_AGENT_STEPS} steps"
            )

        return ExecutionResult(output=final_output, files=written_files)

    async def _dispatch_tool(
        self,
        task_id: str,
        tool_use: anthropic.types.ToolUseBlock,
        written_files: list[str],
    ) -> str:
        name = tool_use.name
        raw_input: dict[str, Any] = tool_use.input  # type: ignore[assignment]

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


def _build_tools(tool_names: list[str]) -> list[anthropic.types.ToolParam]:
    available: dict[str, anthropic.types.ToolParam] = {
        "write_file": anthropic.types.ToolParam(
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
        ),
        "read_file": anthropic.types.ToolParam(
            name="read_file",
            description="Read the text content of a file.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path"},
                },
                "required": ["path"],
            },
        ),
        "list_files": anthropic.types.ToolParam(
            name="list_files",
            description="List files matching a glob pattern.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. *.md"},
                },
                "required": ["pattern"],
            },
        ),
    }
    return [available[name] for name in tool_names if name in available]
