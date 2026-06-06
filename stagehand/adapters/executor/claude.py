from __future__ import annotations

import asyncio
from typing import Any, Optional

import anthropic

from stagehand.adapters.executor.base import (
    BaseAgentExecutor,
    ParsedTurn,
    ToolInvocation,
)
from stagehand.adapters.logger import NullLogger
from stagehand.ports.executor import ExecutionRequest, ToolDefinition
from stagehand.ports.logger import Logger
from stagehand.ports.storage import ArtifactStorage

DEFAULT_MODEL = "claude-opus-4-5"


class ClaudeExecutor(BaseAgentExecutor):
    """Runs a task by calling the Anthropic Messages API.

    Extra tools can be passed at construction time, enabling extension
    without modifying core code:

        executor = ClaudeExecutor(api_key="...", extra_tools=[MyTool])
    """

    _label = "claude executor"

    def __init__(
        self,
        api_key: Optional[str] = None,
        storage: Optional[ArtifactStorage] = None,
        extra_tools: Optional[list[ToolDefinition]] = None,
        rate_limit_retries: int = 3,
        rate_limit_delay: float = 60.0,
        logger: Optional[Logger] = None,
    ) -> None:
        super().__init__(storage=storage, extra_tools=extra_tools)
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.rate_limit_retries = rate_limit_retries
        self.rate_limit_delay = rate_limit_delay
        self._logger: Logger = logger or NullLogger()

    def _default_model(self) -> str:
        return DEFAULT_MODEL

    def _init_messages(self, request: ExecutionRequest) -> list[Any]:
        return [{"role": "user", "content": request.prompt}]

    def _serialize_tools(self, tools: list[ToolDefinition]) -> list[anthropic.types.ToolParam]:
        return [
            anthropic.types.ToolParam(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema,
            )
            for t in tools
        ]

    async def _call_model(
        self,
        model: str,
        request: ExecutionRequest,
        messages: list[Any],
        tools: list[anthropic.types.ToolParam],
    ) -> anthropic.types.Message:
        system_prompt = request.system_prompt or "You are a helpful AI assistant."
        return await self._create_with_retry(model, system_prompt, messages, tools)

    def _parse_response(
        self, resp: anthropic.types.Message, request: ExecutionRequest, step: int
    ) -> ParsedTurn:
        text: Optional[str] = None
        tool_calls: list[ToolInvocation] = []
        for block in resp.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolInvocation(id=block.id, name=block.name, raw_arguments=block.input)
                )

        is_complete = resp.stop_reason != "tool_use"
        assistant_message = (
            None if is_complete else {"role": "assistant", "content": resp.content}
        )
        return ParsedTurn(
            text=text,
            tool_calls=tool_calls,
            is_complete=is_complete,
            assistant_message=assistant_message,
        )

    def _format_tool_result(
        self, call: ToolInvocation, content: str, is_error: bool
    ) -> anthropic.types.ToolResultBlockParam:
        result: anthropic.types.ToolResultBlockParam = {
            "type": "tool_result",
            "tool_use_id": call.id,
            "content": content,
        }
        if is_error:
            result["is_error"] = True
        return result

    def _append_tool_results(self, messages: list[Any], tool_results: list[Any]) -> None:
        messages.append({"role": "user", "content": tool_results})

    async def _create_with_retry(
        self,
        model: str,
        system_prompt: str,
        messages: list[anthropic.types.MessageParam],
        tools: list[anthropic.types.ToolParam],
    ) -> anthropic.types.Message:
        total = self.rate_limit_retries
        for attempt in range(1, total + 1):
            try:
                return await self.client.messages.create(
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
                    tools=tools if tools else [],
                )
            except anthropic.RateLimitError:
                if attempt == total:
                    raise
                self._logger.warning(
                    f"Rate limit (429) on attempt {attempt}/{total} — sleeping {self.rate_limit_delay}s"
                )
                await asyncio.sleep(self.rate_limit_delay)
