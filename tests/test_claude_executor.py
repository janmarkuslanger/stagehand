from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from stagehand.adapters.executor.claude import ClaudeExecutor
from stagehand.ports.executor import ExecutionRequest
from stagehand.ports.logger import Logger


class CapturingLogger(Logger):
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def debug(self, message: str) -> None:
        self.records.append(("debug", message))

    def info(self, message: str) -> None:
        self.records.append(("info", message))

    def warning(self, message: str) -> None:
        self.records.append(("warning", message))

    def error(self, message: str) -> None:
        self.records.append(("error", message))

    def has(self, level: str, fragment: str) -> bool:
        return any(level == lvl and fragment in msg for lvl, msg in self.records)


def _make_text_response(text: str = "done") -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _make_request() -> ExecutionRequest:
    return ExecutionRequest(
        task_id="t1",
        prompt="hello",
        tools=[],
    )


@pytest.mark.asyncio
async def test_succeeds_on_first_attempt():
    executor = ClaudeExecutor(api_key="test", rate_limit_retries=3, rate_limit_delay=0.0)
    executor.client.messages.create = AsyncMock(return_value=_make_text_response("ok"))

    result = await executor.execute(_make_request())

    assert result.output == "ok"
    assert executor.client.messages.create.call_count == 1


@pytest.mark.asyncio
async def test_retries_on_rate_limit_then_succeeds():
    executor = ClaudeExecutor(api_key="test", rate_limit_retries=3, rate_limit_delay=0.0)

    call_count = 0

    async def flaky(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429, headers={}),
                body={},
            )
        return _make_text_response("recovered")

    executor.client.messages.create = flaky

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await executor.execute(_make_request())

    assert result.output == "recovered"
    assert call_count == 3
    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(0.0)


@pytest.mark.asyncio
async def test_raises_after_exhausting_retries():
    executor = ClaudeExecutor(api_key="test", rate_limit_retries=2, rate_limit_delay=0.0)

    async def always_rate_limited(*args, **kwargs):
        raise anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )

    executor.client.messages.create = always_rate_limited

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(anthropic.RateLimitError):
            await executor.execute(_make_request())


@pytest.mark.asyncio
async def test_rate_limit_only_retries_current_step_not_full_loop():
    """A 429 mid-loop retries only that messages.create call, not previous steps."""
    executor = ClaudeExecutor(api_key="test", rate_limit_retries=3, rate_limit_delay=0.0)

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "tu_1"
    tool_block.name = "unknown_tool"
    tool_block.input = {}

    step1_resp = MagicMock()
    step1_resp.stop_reason = "tool_use"
    step1_resp.content = [tool_block]

    create_calls = []

    async def staged_create(*args, **kwargs):
        create_calls.append(len(create_calls))
        if len(create_calls) == 2:
            raise anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429, headers={}),
                body={},
            )
        if len(create_calls) == 1:
            return step1_resp
        return _make_text_response("final")

    executor.client.messages.create = staged_create

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await executor.execute(_make_request())

    # step 1 (ok) + step 2 attempt 1 (429) + step 2 attempt 2 (ok) = 3 calls
    assert len(create_calls) == 3
    assert result.output == "final"


@pytest.mark.asyncio
async def test_warning_logged_on_retry():
    log = CapturingLogger()
    executor = ClaudeExecutor(api_key="test", rate_limit_retries=3, rate_limit_delay=0.0, logger=log)

    call_count = 0

    async def flaky(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429, headers={}),
                body={},
            )
        return _make_text_response("ok")

    executor.client.messages.create = flaky

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await executor.execute(_make_request())

    assert log.has("warning", "Rate limit (429)")
    assert log.has("warning", "1/3")
