"""R2-RESP15 (p50 full response) + R2-TTFT (client wall-clock) + R2-CHATFLOW (end-to-end
decode tok/s through the gateway SSE) — all measured against the real gateway :8131 over
the MODEST query set in ``queries.py``, run strictly sequentially (never concurrent —
this is a production endpoint; concurrency capacity is a separate, gated concern in
``load_ramp.py``).

R2-CHATFLOW basis: the gateway's SSE stream carries no ``usage`` block (see
``api/sse.py``/``event_to_sse`` — only ``token``/``tool_call``/``tool_result``/``final``
events, no token accounting), so completion tokens are counted by tokenizing the
reassembled final answer text with the REAL model tokenizer via vLLM's native
``/tokenize`` endpoint (``llm_raw.tokenize``) — exact, not an approximation — and the
basis is labeled accordingly per the task spec.
"""
from __future__ import annotations

from . import gateway_client, llm_raw
from .queries import QUERIES


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    k = int(round((p / 100) * (len(s) - 1)))
    return s[k]


def measure_resp15(queries: list[str] = QUERIES) -> dict:
    """Non-streaming ``/ai/chat`` full-response wall-clock time, sequential."""
    samples = []
    for q in queries:
        r = gateway_client.chat_once(q)
        samples.append({"query": q[:60], "ok": r["ok"], "total_ms": round(r["total_s"] * 1000, 1),
                         "error": r.get("error")})
    ok_ms = [s["total_ms"] for s in samples if s["ok"]]
    return {
        "n": len(queries), "n_ok": len(ok_ms),
        "p50_ms": round(_pct(ok_ms, 50), 1) if ok_ms else None,
        "p95_ms": round(_pct(ok_ms, 95), 1) if ok_ms else None,
        "mean_ms": round(sum(ok_ms) / len(ok_ms), 1) if ok_ms else None,
        "samples": samples,
    }


def measure_ttft_and_chatflow(queries: list[str] = QUERIES) -> dict:
    """Streaming ``/ai/chat/stream``: TTFT (client wall-clock incl RTT+RAG) + end-to-end
    decode tok/s (real tokenizer on the reassembled final text)."""
    samples = []
    for q in queries:
        r = gateway_client.chat_stream_timed(q)
        if not r["ok"] or r.get("ttft_s") is None:
            samples.append({"query": q[:60], "ok": False, "error": r.get("error")})
            continue
        n_tok = None
        tok_s = None
        try:
            n_tok = llm_raw.tokenize(r["text"]) if r["text"] else 0
            if r.get("gen_window_s") and r["gen_window_s"] > 0 and n_tok:
                tok_s = round(n_tok / r["gen_window_s"], 2)
        except Exception:
            pass
        samples.append({
            "query": q[:60], "ok": True,
            "ttft_ms": round(r["ttft_s"] * 1000, 1),
            "total_ms": round(r["total_s"] * 1000, 1),
            "gen_window_s": round(r["gen_window_s"], 3) if r.get("gen_window_s") else None,
            "completion_tokens_tokenized": n_tok,
            "tok_s": tok_s,
        })
    ok = [s for s in samples if s["ok"]]
    ttft_ms = [s["ttft_ms"] for s in ok]
    tok_s_vals = [s["tok_s"] for s in ok if s.get("tok_s")]
    return {
        "n": len(queries), "n_ok": len(ok),
        "ttft_p50_ms": round(_pct(ttft_ms, 50), 1) if ttft_ms else None,
        "ttft_p95_ms": round(_pct(ttft_ms, 95), 1) if ttft_ms else None,
        "chatflow_tok_s_p50": round(_pct(tok_s_vals, 50), 2) if tok_s_vals else None,
        "chatflow_tok_s_mean": round(sum(tok_s_vals) / len(tok_s_vals), 2) if tok_s_vals else None,
        "samples": samples,
    }
