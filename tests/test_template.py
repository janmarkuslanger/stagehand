import asyncio

import pytest

from stagehand.core.context import RunContext
from stagehand.core.template import resolve
from stagehand.core.workflow import TaskResult


def _ctx(inputs=None, results=None):
    ctx = RunContext(run_id="test-run", inputs=inputs or {})
    loop = asyncio.new_event_loop()
    for task_id, result in (results or {}).items():
        loop.run_until_complete(ctx.set_task_result(task_id, result))
    loop.close()
    return ctx


def test_resolve_input():
    ctx = _ctx(inputs={"ticket_id": "XYZ-123"})
    assert resolve("Ticket: {{ input.ticket_id }}", ctx) == "Ticket: XYZ-123"


def test_resolve_task_output():
    ctx = _ctx(results={"draft": TaskResult(output="Hello world", files=[])})
    assert resolve("{{ tasks.draft }}", ctx) == "Hello world"


def test_resolve_task_files():
    ctx = _ctx(results={"draft": TaskResult(output="x", files=["draft/file.md", "draft/other.md"])})
    result = resolve("{{ tasks.draft.files }}", ctx)
    assert result == "draft/file.md\ndraft/other.md"


def test_resolve_task_file_slug():
    ctx = _ctx(results={"draft": TaskResult(output="x", files=["draft/report.md"])})
    assert resolve("{{ tasks.draft.report_md }}", ctx) == "draft/report.md"


def test_resolve_missing_input():
    ctx = _ctx()
    with pytest.raises(ValueError, match="input"):
        resolve("{{ input.missing }}", ctx)


def test_resolve_missing_task():
    ctx = _ctx()
    with pytest.raises(ValueError, match="result not available"):
        resolve("{{ tasks.nonexistent }}", ctx)


def test_resolve_no_match():
    ctx = _ctx()
    assert resolve("no templates here", ctx) == "no templates here"


def test_resolve_unknown_namespace():
    ctx = _ctx()
    with pytest.raises(ValueError, match="unknown reference type"):
        resolve("{{ foo.bar }}", ctx)


def test_file_slug_special_chars():
    from stagehand.core.template import _file_slug
    assert _file_slug("design-system.md") == "design_system_md"
    assert _file_slug("tokens.css") == "tokens_css"
    assert _file_slug("path/to/file.txt") == "file_txt"


def test_resolve_task_data_scalar():
    ctx = _ctx(results={"t": TaskResult(output="x", data=42)})
    assert resolve("{{ tasks.t.data }}", ctx) == "42"


def test_resolve_task_data_nested():
    ctx = _ctx(results={"t": TaskResult(output="x", data={"user": {"name": "Ada"}})})
    assert resolve("{{ tasks.t.data.user.name }}", ctx) == "Ada"


def test_resolve_task_data_list_index():
    ctx = _ctx(results={"t": TaskResult(output="x", data=["a", "b", "c"])})
    assert resolve("{{ tasks.t.data.1 }}", ctx) == "b"


def test_resolve_task_data_none_is_empty():
    ctx = _ctx(results={"t": TaskResult(output="x")})
    assert resolve("[{{ tasks.t.data }}]", ctx) == "[]"


def test_resolve_extra_item():
    ctx = _ctx()
    assert resolve("item={{ item }}", ctx, extra={"item": "X"}) == "item=X"


def test_resolve_extra_item_nested():
    ctx = _ctx()
    assert resolve("{{ item.lang }}", ctx, extra={"item": {"lang": "de"}}) == "de"


def test_resolve_extra_loop():
    ctx = _ctx()
    out = resolve("i={{ loop.iteration }} p={{ loop.previous }}", ctx,
                  extra={"loop": {"iteration": 2, "previous": "old"}})
    assert out == "i=2 p=old"


def test_resolve_input_structured():
    ctx = _ctx(inputs={"cfg": {"depth": 3}})
    assert resolve("{{ input.cfg.depth }}", ctx) == "3"
