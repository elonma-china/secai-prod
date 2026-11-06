"""Thin client for the gateway's full chat path (:8131 — the actual production
endpoint, RAG agent + tool calls + guard layers, NOT the raw LLM). No auth header is
required: /ai/chat and /ai/chat/stream are unauthenticated by design (private-network
gateway, see secai-docker/.env comment) — only admin/exec endpoints need X-API-Key.

Used by R2-RESP15 (full response p50), R2-TTFT (client wall-clock incl RTT + RAG),
R2-CHATFLOW (end-to-end decode tok/s through the gateway SSE).
"""
from __future__ import annotations

import time

import httpx

from .. import config


def chat_once(message: str, *, timeout: float = 120.0) -> dict:
    """Non-streaming ``POST /ai/chat`` — used for R2-RESP15 (full response p50)."""
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"{config.GATEWAY_URL}/ai/chat", json={"message": message})
            r.raise_for_status()
            data = r.json()
        dt = time.monotonic() - t0
        return {"ok": True, "total_s": dt, "text": data.get("answer") or data.get("response") or ""}
    except Exception as e:
        return {"ok": False, "total_s": time.monotonic() - t0, "error": f"{type(e).__name__}: {e}"}


def chat_stream_timed(message: str, *, timeout: float = 120.0) -> dict:
    """``POST /ai/chat/stream`` (SSE) — captures real client-side timestamps:
    ``ttft_s`` (request sent -> first ``token`` event), ``t_last_token_s`` (-> last
    ``token`` event received), ``total_s`` (-> stream fully closed / ``final`` event).
    Text is reassembled from the ``token`` deltas (same text the FE would render).
    """
    t0 = time.monotonic()
    t_first = None
    t_last = None
    parts: list[str] = []
    cur_event = None
    try:
        with httpx.Client(timeout=timeout) as c:
            with c.stream("POST", f"{config.GATEWAY_URL}/ai/chat/stream",
                          json={"message": message}) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if line.startswith("event:"):
                        cur_event = line.split(":", 1)[1].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    if cur_event == "token":
                        now = time.monotonic()
                        if t_first is None:
                            t_first = now
                        t_last = now
                        try:
                            import json as _json
                            delta = _json.loads(payload).get("delta", "")
                        except Exception:
                            delta = ""
                        parts.append(delta)
        t_end = time.monotonic()
        return {
            "ok": True, "text": "".join(parts),
            "ttft_s": (t_first - t0) if t_first is not None else None,
            "t_last_token_s": (t_last - t0) if t_last is not None else None,
            "gen_window_s": (t_last - t_first) if (t_first and t_last and t_last > t_first) else None,
            "total_s": t_end - t0,
        }
    except Exception as e:
        return {"ok": False, "total_s": time.monotonic() - t0, "error": f"{type(e).__name__}: {e}"}
