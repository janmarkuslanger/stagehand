from __future__ import annotations

import json
from typing import Any, Optional

import openai

from stagehand.adapters.executor.base import (
    BaseAgentExecutor,
    ParsedTurn,
    ToolInvocation,
)
from stagehand.ports.executor import ExecutionRequest, ToolDefinition
from stagehand.ports.storage import ArtifactStorage

OLLAMA_DEFAULT_MODEL = "qwen2.5"
OLLAMA_DEFAULT_HOST = "http://localhost:11434"


class OllamaExecutor(BaseAgentExecutor):
    """Runs a task by calling a local Ollama instance via its OpenAI-compatible endpoint."""

    _label = "ollama executor"

    def __init__(
        self,
        host: str = OLLAMA_DEFAULT_HOST,
        storage: Optional[ArtifactStorage] = None,
        extra_tools: Optional[list[ToolDefinition]] = None,
    ) -> None:
        super().__init__(storage=storage, extra_tools=extra_tools)
        self.client = openai.AsyncOpenAI(
            base_url=f"{host}/v1",
            api_key="ollama",
        )

    def _default_model(self) -> str:
        return OLLAMA_DEFAULT_MODEL

    def _init_messages(self, request: ExecutionRequest) -> list[Any]:
        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        return messages

    def _serialize_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    async def _call_model(
        self,
        model: str,
        request: ExecutionRequest,
        messages: list[Any],
        tools: list[dict[str, Any]],
    ) -> Any:
        params: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            params["tools"] = tools
        return await self.client.chat.completions.create(**params)

    def _parse_response(
        self, resp: Any, request: ExecutionRequest, step: int
    ) -> ParsedTurn:
        if not resp.choices:
            raise RuntimeError(
                f"{self._label}: task {request.task_id}: step {step}: empty response"
            )

        choice = resp.choices[0]
        message = choice.message

        text = message.content if message.content else None
        is_complete = choice.finish_reason != "tool_calls"

        tool_calls: list[ToolInvocation] = []
        if not is_complete:
            for tool_call in message.tool_calls or []:
                tool_calls.append(
                    ToolInvocation(
                        id=tool_call.id,
                        name=tool_call.function.name,
                        raw_arguments=tool_call.function.arguments,
                    )
                )

        assistant_message = None if is_complete else message.model_dump()
        return ParsedTurn(
            text=text,
            tool_calls=tool_calls,
            is_complete=is_complete,
            assistant_message=assistant_message,
        )

    def _parse_arguments(self, raw_arguments: Any) -> dict[str, Any]:
        return json.loads(raw_arguments)

    def _format_tool_result(
        self, call: ToolInvocation, content: str, is_error: bool
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call.id,
            "content": content,
        }

    def _append_tool_results(self, messages: list[Any], tool_results: list[Any]) -> None:
        messages.extend(tool_results)
