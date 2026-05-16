from __future__ import annotations

import json
from typing import Any, Optional

import openai

from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult
from stagehand.ports.storage import ArtifactStorage

MAX_AGENT_STEPS = 20
OLLAMA_DEFAULT_MODEL = "qwen2.5"
OLLAMA_DEFAULT_HOST = "http://localhost:11434"


class OllamaExecutor(AgentExecutor):
    """Runs a task by calling a local Ollama instance via its OpenAI-compatible endpoint."""

    def __init__(
        self,
        host: str = OLLAMA_DEFAULT_HOST,
        storage: Optional[ArtifactStorage] = None,
    ) -> None:
        self.client = openai.AsyncOpenAI(
            base_url=f"{host}/v1",
            api_key="ollama",
        )
        self.storage = storage

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        model = request.model or OLLAMA_DEFAULT_MODEL

        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        tools = _build_ollama_tools(request.tools)

        final_output = ""
        written_files: list[str] = []
        last_finish_reason: Optional[str] = None

        for step in range(MAX_AGENT_STEPS):
            params: dict[str, Any] = {
                "model": model,
                "messages": messages,
            }
            if tools:
                params["tools"] = tools

            resp = await self.client.chat.completions.create(**params)

            if not resp.choices:
                raise RuntimeError(
                    f"ollama executor: task {request.task_id}: step {step}: empty response"
                )

            choice = resp.choices[0]
            last_finish_reason = choice.finish_reason

            if choice.message.content:
                final_output = choice.message.content

            if choice.finish_reason != "tool_calls":
                break

            messages.append(choice.message.model_dump())

            tool_results: list[dict[str, Any]] = []
            for tool_call in choice.message.tool_calls or []:
                try:
                    raw_input = json.loads(tool_call.function.arguments)
                    result_content = await self._dispatch_tool(
                        request.task_id, tool_call.function.name, raw_input, written_files
                    )
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_content,
                        }
                    )
                except Exception as exc:
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": str(exc),
                        }
                    )

            messages.extend(tool_results)

        if last_finish_reason == "tool_calls":
            raise RuntimeError(
                f"ollama executor: task {request.task_id}: agent did not complete within {MAX_AGENT_STEPS} steps"
            )

        return ExecutionResult(output=final_output, files=written_files)

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


def _build_ollama_tools(tool_names: list[str]) -> list[dict[str, Any]]:
    available: dict[str, dict[str, Any]] = {
        "write_file": {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write text content to a file. Creates the file if it does not exist.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path"},
                        "content": {"type": "string", "description": "Text content to write"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        "read_file": {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the text content of a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path"},
                    },
                    "required": ["path"],
                },
            },
        },
        "list_files": {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files matching a glob pattern.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Glob pattern, e.g. *.md"},
                    },
                    "required": ["pattern"],
                },
            },
        },
    }
    return [available[name] for name in tool_names if name in available]
