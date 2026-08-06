"""Server-Sent Events encoding: map linch run events to named SSE frames so UIs can
render tool activity (tool_call/tool_result) apart from assistant text (token)."""
from __future__ import annotations

import json
from typing import Any

from linch.events import (
    ErrorEvent,
    PartialAssistantEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

from ..security import remove_chinese_content


def sse_frame(event_name: str, payload: dict[str, Any]) -> bytes:
    """One named SSE event. UIs switch on the event name to render tool activity
    (tool_call/tool_result) apart from assistant text (token) and the final answer."""
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def event_to_sse(event: Any) -> bytes | None:
    """Map a linch run event to an SSE frame, or None to drop it from the stream."""
    if isinstance(event, PartialAssistantEvent):
        # linch delta is {"kind":"text","text":"…"}; forward only text tokens.
        # Strip CJK before yield — model sometimes emits Chinese mid-stream.
        delta = event.delta if isinstance(event.delta, dict) else {}
        if delta.get("kind") == "text" and delta.get("text"):
            cleaned = remove_chinese_content(delta["text"])
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
        # Include the retrieved content so the UI can render citations (sources).
        raw = getattr(event, "tool_result", None)
        content = getattr(raw, "content", None)
        if content is None:
            content = getattr(event, "result", None)
        if not isinstance(content, str):
            content = "" if content is None else json.dumps(content, ensure_ascii=False, default=str)
        return sse_frame("tool_result", {
            "id": event.tool_use_id, "name": event.tool_name,
            "is_error": event.is_error, "duration_ms": event.duration_ms,
            "result": content[:4000],
        })
    if isinstance(event, ErrorEvent):
        return sse_frame("error", {"error": str(event.error)})
    return None
