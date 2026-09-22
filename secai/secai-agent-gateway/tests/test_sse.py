from __future__ import annotations

import json

from linch.events import HookEventRecord, PartialAssistantEvent, ToolCallEndEvent
from linch.tools.base import ToolResult

from secai_gateway.api.sse import event_to_sse


def _decode(frame: bytes) -> tuple[str, dict]:
    text = frame.decode()
    event_line, data_line = text.strip("\n").split("\n", 1)
    event_name = event_line.removeprefix("event: ")
    payload = json.loads(data_line.removeprefix("data: "))
    return event_name, payload


def test_force_continue_hook_emits_regenerate() -> None:
    # RequireSearchBeforeAnswerHook/RequireRunInstructionsHook (agent/hooks.py) both use
    # HookResult.force_continue() when the model answers before gathering evidence — the
    # client must be told to discard the rejected draft's `token` deltas already streamed.
    record = HookEventRecord(event="before_final_answer", hook="require_search_before_answer",
                              action="force_continue", reason="search_before_answer_unsatisfied")
    frame = event_to_sse(record)
    assert frame is not None
    name, payload = _decode(frame)
    assert name == "regenerate"
    assert payload == {"hook": "require_search_before_answer", "reason": "search_before_answer_unsatisfied"}


def test_retry_hook_emits_regenerate() -> None:
    record = HookEventRecord(event="pre_tool_use", hook="some_hook", action="retry", reason="bad_input")
    frame = event_to_sse(record)
    assert frame is not None
    name, _ = _decode(frame)
    assert name == "regenerate"


def test_non_regenerating_hook_actions_are_dropped() -> None:
    # "block"/"stop"/"resolve"/"mutate" don't discard already-streamed assistant text —
    # forwarding them as `regenerate` would make well-behaved clients clear text that was
    # never wrong.
    for action in ("block", "stop", "resolve", "mutate", "continue"):
        record = HookEventRecord(event="pre_tool_use", hook="some_hook", action=action, reason="")
        assert event_to_sse(record) is None


def test_partial_assistant_event_still_maps_to_token() -> None:
    event = PartialAssistantEvent(delta={"kind": "text", "text": "hello"})
    frame = event_to_sse(event)
    assert frame is not None
    name, payload = _decode(frame)
    assert name == "token"
    assert payload == {"delta": "hello"}


# ── H3 (2026-09-22): SSE tool_result payload shrink, model's own tool calls ──────────────
# `ToolCallEndEvent` (a model-initiated tool call, e.g. qdrant_search/poc_fetch) used to
# forward up to 4000 raw chars of KB/tool content — ToolTimeline.tsx (FE) only ever renders
# `summary` (falling back to `result.slice(0, 140)` when absent, never rendering more of
# `result` than that), so the rest was dead weight on the wire. Fix: always set a clean
# `summary` (the tool's own ToolResult.summary, else a short content preview) and shrink
# `result` to a short preview — while still ALWAYS including the `result` KEY (even ``""``),
# since ToolTimeline's `result === undefined` check is how it knows a row is still pending.

def test_tool_call_end_event_sets_clean_summary_and_shrinks_raw_result() -> None:
    raw = ToolResult(content="RAW KB DUMP " * 500, summary="qdrant_search(CVE-2021-44228) 3 hits")
    event = ToolCallEndEvent(tool_use_id="t1", tool_name="qdrant_search", result="",
                             is_error=False, duration_ms=42, tool_result=raw)
    frame = event_to_sse(event)
    assert frame is not None
    name, payload = _decode(frame)
    assert name == "tool_result"                      # frame type kept
    assert payload["name"] == "qdrant_search"          # tool name kept
    assert payload["summary"] == "qdrant_search(CVE-2021-44228) 3 hits"
    assert "result" in payload                         # key kept (FE pending-state check)
    assert len(payload["result"]) <= 200
    assert payload["result"] != raw.content, "must not forward the full raw KB dump anymore"


def test_tool_call_end_event_falls_back_to_short_result_preview_when_no_summary() -> None:
    # No ToolResult (e.g. event.result is a plain string, no `.summary` attribute available)
    # — summary must still never be empty, using the same 140-char convention the FE itself
    # falls back to client-side.
    event = ToolCallEndEvent(tool_use_id="t2", tool_name="poc_fetch", result="Y" * 500,
                             is_error=False, duration_ms=10, tool_result=None)
    frame = event_to_sse(event)
    assert frame is not None
    _, payload = _decode(frame)
    assert payload["summary"]
    assert len(payload["summary"]) <= 140
    assert len(payload["result"]) <= 200


def test_tool_call_end_event_result_key_present_even_when_empty() -> None:
    # A tool that legitimately returned nothing must still resolve the FE's pending state —
    # `result` must be present (empty string), never dropped/omitted.
    event = ToolCallEndEvent(tool_use_id="t3", tool_name="qdrant_search", result="",
                             is_error=False, duration_ms=5, tool_result=None)
    frame = event_to_sse(event)
    assert frame is not None
    _, payload = _decode(frame)
    assert payload["result"] == ""
    assert payload["summary"] is None
