"""Thin client for the RAW LLM endpoint (:13905, vLLM OpenAI-compatible), used by
R2-TPS / R2-LLMTPS28 / R2-CTX16K / R2-RPS / R2-CCU / R2-TTFT(raw variant).

Real numbers only: completion token counts come from ``usage.completion_tokens``
(requested via ``stream_options.include_usage``), never estimated from characters.
Prompt-length measurement for the context test uses vLLM's own ``/tokenize`` endpoint
(the exact model tokenizer — more accurate than an HF/tiktoken approximation, and
available without downloading the model locally).
"""
from __future__ import annotations

import json
import time

import httpx

from .. import config

_HEADERS = {"Content-Type": "application/json"}
if config.LLM_KEY:
    _HEADERS["Authorization"] = f"Bearer {config.LLM_KEY}"


def tokenize(text: str, timeout: float = 30.0) -> int:
    """Exact token count via vLLM's native ``/tokenize`` (real model tokenizer)."""
    base = config.LLM_BASE_URL.rsplit("/v1", 1)[0]
    with httpx.Client(timeout=timeout) as c:
        r = c.post(f"{base}/tokenize", json={"model": config.LLM_MODEL, "prompt": text},
                   headers=_HEADERS)
        r.raise_for_status()
        return r.json()["count"]


def chat_stream(prompt: str, *, max_tokens: int = 256, system: str | None = None,
                 timeout: float = 120.0) -> dict:
    """One streamed raw-LLM chat completion. Returns real timing + real token counts:

    ``ttft_s`` (wall clock to first content delta), ``gen_s`` (first delta -> stream end,
    i.e. pure decode time excluding prefill/TTFT), ``total_s``, ``completion_tokens``
    (from ``usage``, exact), ``prompt_tokens`` (from ``usage``, exact), ``text``, ``ok``.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": config.LLM_MODEL, "messages": messages, "max_tokens": max_tokens,
            "temperature": 0.0, "stream": True, "stream_options": {"include_usage": True}}

    t0 = time.monotonic()
    t_first = None
    t_last = t0
    text_parts: list[str] = []
    usage = None
    try:
        with httpx.Client(timeout=timeout) as c:
            with c.stream("POST", f"{config.LLM_BASE_URL}/chat/completions", json=body,
                          headers=_HEADERS) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):]
                    if payload.strip() == "[DONE]":
                        break
                    chunk = json.loads(payload)
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta", {}) or {}
                        piece = delta.get("content")
                        if piece:
                            if t_first is None:
                                t_first = time.monotonic()
                            text_parts.append(piece)
                            t_last = time.monotonic()
        t_end = time.monotonic()
        ok = True
    except Exception as e:
        t_end = time.monotonic()
        ok = False
        usage = usage or {}
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "total_s": t_end - t0}

    text = "".join(text_parts)
    ttft_s = (t_first - t0) if t_first is not None else None
    gen_s = (t_last - t_first) if (t_first is not None and t_last > t_first) else None
    return {
        "ok": ok, "text": text,
        "ttft_s": ttft_s, "gen_s": gen_s, "total_s": t_end - t0,
        "completion_tokens": (usage or {}).get("completion_tokens"),
        "prompt_tokens": (usage or {}).get("prompt_tokens"),
        "tok_s": (round((usage or {}).get("completion_tokens", 0) / gen_s, 2)
                  if (usage and gen_s and gen_s > 0) else None),
    }


def chat_once(prompt: str, *, max_tokens: int = 64, timeout: float = 60.0) -> dict:
    """Non-streaming completion — used by the concurrency ramp (fewer moving parts,
    still returns real ``usage`` token counts)."""
    body = {"model": config.LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0.0, "stream": False}
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"{config.LLM_BASE_URL}/chat/completions", json=body, headers=_HEADERS)
            r.raise_for_status()
            data = r.json()
        dt = time.monotonic() - t0
        return {"ok": True, "latency_s": dt, "usage": data.get("usage")}
    except Exception as e:
        return {"ok": False, "latency_s": time.monotonic() - t0, "error": f"{type(e).__name__}: {e}"}
