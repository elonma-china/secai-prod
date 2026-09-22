"""Server-Sent Events encoding: map linch run events to named SSE frames so UIs can
render tool activity (tool_call/tool_result) apart from assistant text (token)."""
from __future__ import annotations

import json
from typing import Any

from linch.events import (
    ErrorEvent,
    HookEventRecord,
    PartialAssistantEvent,
    SubagentEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    WorkflowEvent,
)

from ..security import neutralize_active_content, remove_chinese_content

# HookEventRecord.action values that discard the model's current in-flight answer and
# start a fresh generation — e.g. RequireSearchBeforeAnswerHook/RequireRunInstructionsHook
# (agent/hooks.py) calling HookResult.force_continue() at on_before_final_answer when the
# model answered without evidence. linch's loop/finalize.py yields these into the SAME
# event stream as the rejected draft's own PartialAssistantEvent tokens, with no other
# marker between them — a client that appends every `token` delta into one buffer renders
# the rejected draft (e.g. an ungrounded "no PoC found" answer) immediately followed by
# the corrected regeneration, concatenated with no boundary (observed live: CVE-2026-27944
# streamed "Không có thông tin..." first, then the real evidence-backed answer, both
# visible to the client with nothing to tell them apart). Forwarded as a dedicated
# `regenerate` SSE event so the client knows to reset its assistant-text buffer here.
_REGENERATE_ACTIONS = {"force_continue", "retry"}

# H3 (2026-09-22): short preview length for the `tool_result` frame's `result` field — see
# ToolCallEndEvent branch below (same rationale/constant as
# `routes.py::_SSE_RESULT_PREVIEW_CHARS`, duplicated rather than imported to avoid a
# routes<->sse import cycle: routes.py imports `sse_frame`/`event_to_sse` from this module).
_TOOL_RESULT_PREVIEW_CHARS = 200


def sse_frame(event_name: str, payload: dict[str, Any]) -> bytes:
    """One named SSE event. UIs switch on the event name to render tool activity
    (tool_call/tool_result) apart from assistant text (token) and the final answer."""
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def event_to_sse(event: Any) -> bytes | None:
    """Map a linch run event to an SSE frame, or None to drop it from the stream."""
    # wf.agent / deep workers wrap child events — unwrap so tokens/tools stream live.
    if isinstance(event, SubagentEvent):
        return event_to_sse(event.event)
    if isinstance(event, HookEventRecord):
        if event.action in _REGENERATE_ACTIONS:
            return sse_frame("regenerate", {"hook": event.hook, "reason": event.reason})
        return None
    if isinstance(event, PartialAssistantEvent):
        # linch delta is {"kind":"text","text":"…"}; forward only text tokens.
        # Strip CJK before yield — model sometimes emits Chinese mid-stream.
        delta = event.delta if isinstance(event.delta, dict) else {}
        if delta.get("kind") == "text" and delta.get("text"):
            # Defang active HTML/JS per token (anti-XSS) in case the UI renders the live token
            # stream; a tag split across two tokens still gets caught by the sanitized `final`.
            cleaned = neutralize_active_content(remove_chinese_content(delta["text"]))
            if not cleaned:
                return None
            return sse_frame("token", {"delta": cleaned})
        return None
    if isinstance(event, ToolCallStartEvent):
        return sse_frame("tool_call", {
            "id": event.tool_use_id, "name": event.tool_name,
            "input": event.input, "summary": event.summary,
        })
    if isinstance(event, ToolCallEndEvent):
        # H3 (2026-09-22): this used to forward up to 4000 raw chars of tool content
        # (``result``) so the UI could render citations — but ToolTimeline.tsx (FE) only
        # ever renders ``summary``, falling back to ``result.slice(0, 140)`` only when
        # ``summary`` is absent (this branch never set one). Always set a clean ``summary``
        # now (the tool's own ``ToolResult.summary`` when the runner captured it, else a
        # short preview of ``content`` — the same 140-char convention the FE falls back to
        # itself) and shrink ``result`` to a short preview — the raw multi-KB payload the
        # model already saw in its own context had no reader on the SSE wire. ``result`` is
        # still ALWAYS present (even ``""``) — ToolTimeline's ``result === undefined`` check
        # is how it knows a tool_call is still pending, so dropping the key would leave the
        # UI spinning forever for this frame's row.
        raw = getattr(event, "tool_result", None)
        content = getattr(raw, "content", None)
        if content is None:
            content = getattr(event, "result", None)
        if not isinstance(content, str):
            content = "" if content is None else json.dumps(content, ensure_ascii=False, default=str)
        summary = getattr(raw, "summary", None) or (content[:140] or None)
        return sse_frame("tool_result", {
            "id": event.tool_use_id, "name": event.tool_name,
            "is_error": event.is_error, "duration_ms": event.duration_ms,
            "summary": summary,
            "result": content[:_TOOL_RESULT_PREVIEW_CHARS],
        })
    if isinstance(event, WorkflowEvent):
        # Deterministic bughunt phases (Recon → Hunt → Validate → …).
        return sse_frame("phase", {
            "kind": event.kind,
            "title": event.title,
            "call_key": event.call_key,
            "occurrence": event.occurrence,
            "subagent_type": event.subagent_type,
            "result_text": (event.result_text or "")[:2000] or None,
            "structured_output": event.structured_output,
            "structured_error": event.structured_error,
        })
    if isinstance(event, ErrorEvent):
        return sse_frame("error", {"error": str(event.error)})
    return None
