"""R2-TPS + R2-LLMTPS28 — raw LLM (:13905) decode throughput, REAL ``usage.completion_tokens``
(``stream_options.include_usage``), generation window = first-content-delta -> stream-end
(excludes prefill/TTFT). Sequential, modest N — this endpoint is the SAME backend
production's gateway uses, so it is not hammered.
"""
from __future__ import annotations

from . import llm_raw

_PROMPTS = [
    "Explain in detail how a SQL injection attack works and how to prevent it.",
    "Describe the CVE-2021-44228 Log4Shell vulnerability and its impact in detail.",
    "What are the OWASP Top 10 web application security risks? List and briefly explain each.",
    "Explain the difference between symmetric and asymmetric encryption with examples.",
    "Describe how a buffer overflow vulnerability can be exploited by an attacker.",
    "What is a Cross-Site Request Forgery (CSRF) attack and how do you defend against it?",
]


def _pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = int(round((p / 100) * (len(s) - 1)))
    return s[k]


def measure(prompts: list[str] = _PROMPTS, max_tokens: int = 300) -> dict:
    samples = []
    for p in prompts:
        r = llm_raw.chat_stream(p, max_tokens=max_tokens)
        if not r.get("ok"):
            samples.append({"prompt": p[:50], "ok": False, "error": r.get("error")})
            continue
        samples.append({
            "prompt": p[:50], "ok": True,
            "completion_tokens": r["completion_tokens"], "prompt_tokens": r["prompt_tokens"],
            "ttft_ms": round(r["ttft_s"] * 1000, 1) if r["ttft_s"] else None,
            "gen_s": round(r["gen_s"], 3) if r["gen_s"] else None,
            "tok_s": r["tok_s"],
        })
    ok = [s for s in samples if s["ok"] and s.get("tok_s")]
    tok_s_vals = [s["tok_s"] for s in ok]
    return {
        "n": len(prompts), "n_ok": len(ok),
        "tok_s_p50": round(_pct(tok_s_vals, 50), 2) if tok_s_vals else None,
        "tok_s_mean": round(sum(tok_s_vals) / len(tok_s_vals), 2) if tok_s_vals else None,
        "tok_s_min": round(min(tok_s_vals), 2) if tok_s_vals else None,
        "tok_s_max": round(max(tok_s_vals), 2) if tok_s_vals else None,
        "samples": samples,
    }
