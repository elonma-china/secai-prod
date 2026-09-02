"""Unit tests for api/sse.py::event_to_sse — the linch-event → SSE-frame mapping."""
from __future__ import annotations

import json

from linch.events import (
    ErrorEvent,
    HookEventRecord,
    PartialAssistantEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from linch.hooks.types import ToolResult

from secai_gateway.api.sse import event_to_sse


def _decode(frame: bytes) -> tuple[str, dict]:
    text = frame.decode()
    event = text.split("event: ", 1)[1].split("\n", 1)[0]
    data = json.loads(text.split("data: ", 1)[1].split("\n\n", 1)[0])
    return event, data


# ── regenerate frame (HookEventRecord) ──────────────────────────────────────────
def test_force_continue_becomes_regenerate_frame() -> None:
    ev = HookEventRecord(event="on_before_final_answer", hook="require_chat_grounding",
                         action="force_continue", reason="chat_kb_required")
    frame = event_to_sse(ev)
    assert frame is not None
    event, data = _decode(frame)
    assert event == "regenerate"
    assert data == {"hook": "require_chat_grounding", "reason": "chat_kb_required"}


def test_retry_action_also_becomes_regenerate_frame() -> None:
    ev = HookEventRecord(event="on_before_final_answer", hook="require_search_before_answer",
                         action="retry", reason="x")
    frame = event_to_sse(ev)
    assert frame is not None
    assert _decode(frame)[0] == "regenerate"


def test_other_hook_actions_are_dropped() -> None:
    for action in ("block", "stop", "resolve", "mutate", "continue"):
        ev = HookEventRecord(event="on_before_final_answer", hook="h", action=action, reason="")
        assert event_to_sse(ev) is None


# ── token frame: CJK stripped + active content defanged ─────────────────────────
def test_token_frame_carries_plain_text() -> None:
    ev = PartialAssistantEvent(delta={"kind": "text", "text": "hello"})
    frame = event_to_sse(ev)
    assert frame is not None
    event, data = _decode(frame)
    assert event == "token" and data == {"delta": "hello"}


def test_token_frame_strips_cjk() -> None:
    ev = PartialAssistantEvent(delta={"kind": "text", "text": "Poly讲讲 hệ thống"})
    _, data = _decode(event_to_sse(ev))
    assert "讲" not in data["delta"]


def test_token_frame_defangs_active_html() -> None:
    """Anti-XSS: a UI rendering the live token stream must never see a live <script> tag,
    even briefly before the `final` frame corrects the accumulated text (a tag split across
    two token deltas still gets caught by the sanitized final regardless)."""
    ev = PartialAssistantEvent(delta={"kind": "text", "text": "<script>alert(1)</script>"})
    _, data = _decode(event_to_sse(ev))
    assert "<script>" not in data["delta"]


def test_empty_token_after_cleanup_is_dropped() -> None:
    ev = PartialAssistantEvent(delta={"kind": "text", "text": "讲"})  # pure CJK -> empty after strip
    assert event_to_sse(ev) is None


# ── tool_call / tool_result / error ──────────────────────────────────────────────
def test_tool_call_start_frame() -> None:
    ev = ToolCallStartEvent(tool_use_id="t1", tool_name="qdrant_search",
                            input={"query": "x"}, summary="qdrant_search(x)")
    event, data = _decode(event_to_sse(ev))
    assert event == "tool_call"
    assert data == {"id": "t1", "name": "qdrant_search", "input": {"query": "x"}, "summary": "qdrant_search(x)"}


def test_tool_call_end_frame_truncates_result() -> None:
    ev = ToolCallEndEvent(tool_use_id="t1", tool_name="qdrant_search",
                          tool_result=ToolResult(content="x" * 5000, is_error=False),
                          is_error=False, duration_ms=12)
    event, data = _decode(event_to_sse(ev))
    assert event == "tool_result"
    assert len(data["result"]) == 4000


def test_error_frame() -> None:
    ev = ErrorEvent(error=RuntimeError("boom"))
    event, data = _decode(event_to_sse(ev))
    assert event == "error" and data == {"error": "boom"}
