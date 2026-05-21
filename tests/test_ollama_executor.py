from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from stagehand.adapters.executor.ollama import OllamaExecutor
from stagehand.ports.executor import ExecutionRequest, ToolDefinition


def _make_text_choice(text: str = "done") -> MagicMock:
    message = MagicMock()
    message.content = text
    message.tool_calls = None
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message = message
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_tool_choice(tool_name: str, arguments: dict) -> MagicMock:
    tool_call = MagicMock()
    tool_call.id = "tc_1"
    tool_call.function.name = tool_name
    tool_call.function.arguments = json.dumps(arguments)

    message = MagicMock()
    message.content = ""
    message.tool_calls = [tool_call]
    message.model_dump.return_value = {"role": "assistant", "content": ""}

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message = message

    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_request(tools: list[str] | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        task_id="t1",
        prompt="hello",
        tools=tools or [],
    )


@pytest.mark.asyncio
async def test_basic_execution_no_tools():
    executor = OllamaExecutor()
    executor.client.chat.completions.create = AsyncMock(
        return_value=_make_text_choice("hello world")
    )

    result = await executor.execute(_make_request())

    assert result.output == "hello world"
    assert result.files == []


@pytest.mark.asyncio
async def test_custom_tool_is_called():
    called_with: list[dict] = []

    def my_tool(args: dict) -> str:
        called_with.append(args)
        return "tool result"

    tool = ToolDefinition(
        name="my_tool",
        description="A test tool",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        handler=my_tool,
    )

    executor = OllamaExecutor(extra_tools=[tool])

    responses = [
        _make_tool_choice("my_tool", {"value": "hello"}),
        _make_text_choice("done"),
    ]
    executor.client.chat.completions.create = AsyncMock(side_effect=responses)

    result = await executor.execute(_make_request())

    assert called_with == [{"value": "hello"}]
    assert result.output == "done"


@pytest.mark.asyncio
async def test_custom_async_tool_is_awaited():
    async def async_tool(args: dict) -> str:
        return f"async:{args['x']}"

    tool = ToolDefinition(
        name="async_tool",
        description="Async test tool",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
        handler=async_tool,
    )

    executor = OllamaExecutor(extra_tools=[tool])

    responses = [
        _make_tool_choice("async_tool", {"x": "42"}),
        _make_text_choice("done"),
    ]
    executor.client.chat.completions.create = AsyncMock(side_effect=responses)

    result = await executor.execute(_make_request())

    assert result.output == "done"


@pytest.mark.asyncio
async def test_custom_tool_included_in_api_call():
    tool = ToolDefinition(
        name="search",
        description="Search the web",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=lambda args: "result",
    )

    executor = OllamaExecutor(extra_tools=[tool])
    create_mock = AsyncMock(return_value=_make_text_choice("ok"))
    executor.client.chat.completions.create = create_mock

    await executor.execute(_make_request())

    call_kwargs = create_mock.call_args.kwargs
    tool_names = [t["function"]["name"] for t in call_kwargs["tools"]]
    assert "search" in tool_names


@pytest.mark.asyncio
async def test_unknown_tool_error_returned_to_model():
    # Errors from unknown tools are caught and sent back as tool results,
    # allowing the model to recover rather than crashing the executor.
    executor = OllamaExecutor()

    create_mock = AsyncMock(side_effect=[
        _make_tool_choice("nonexistent_tool", {}),
        _make_text_choice("recovered"),
    ])
    executor.client.chat.completions.create = create_mock

    result = await executor.execute(_make_request())

    assert result.output == "recovered"
    second_call_messages = create_mock.call_args_list[1].kwargs["messages"]
    tool_result = next(m for m in second_call_messages if m.get("role") == "tool")
    assert "unknown tool" in tool_result["content"]
