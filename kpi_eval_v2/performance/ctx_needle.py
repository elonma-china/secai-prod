"""R2-CTX16K — needle-in-haystack effective context length against the raw LLM
(:13905, ``DuyTa/Cyber-F1-AWQ``, deployed with ``--max-model-len 65536`` per the live
vLLM launch flags read over SSH 2026-09-20). A unique fact (random token) is inserted
at ~50% depth inside a growing haystack of unrelated filler sentences; the prompt length
at each step is measured EXACTLY via vLLM's own ``/tokenize`` (real model tokenizer, not
an estimate). Effective context = the largest tested length where the model still
recalls the needle correctly. Honest about a served max_model_len below 16k (does not
apply here, but the code checks and reports it either way).
"""
from __future__ import annotations

import random
import string

from . import llm_raw

_FILLER_SENTENCES = [
    "The security team reviewed network logs for anomalous outbound traffic patterns.",
    "Threat intelligence feeds are updated hourly with new indicators of compromise.",
    "Patch management remains a top priority for reducing the attack surface.",
    "Multi-factor authentication significantly reduces the risk of credential stuffing.",
    "Incident responders triaged the alert queue before the daily stand-up meeting.",
    "Vulnerability scanners flagged several outdated TLS configurations on edge hosts.",
    "The SOC dashboard shows a steady decline in false-positive alert volume this quarter.",
    "Access control reviews are performed quarterly across all production environments.",
    "Container images are scanned for known CVEs before being pushed to the registry.",
    "Backup integrity checks run nightly to validate the disaster recovery plan.",
]

_TARGET_LENGTHS = [4000, 8000, 12000, 16000, 24000, 32000, 40000]


def _random_token(n: int = 10) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


def _calibrate_chars_per_token() -> float:
    """One real /tokenize call on a filler sample to get this text's actual chars/token
    ratio (measured, not assumed) — avoids badly undershooting target lengths the way a
    fixed ~4 chars/token guess does on this filler's shorter, plainer sentences."""
    sample = " ".join(_FILLER_SENTENCES * 5)
    try:
        n_tok = llm_raw.tokenize(sample)
        if n_tok:
            return len(sample) / n_tok
    except Exception:
        pass
    return 4.2  # fallback if calibration call fails


def _build_haystack(target_tokens: int, needle_token: str, chars_per_token: float) -> str:
    """Grow filler text to ~target_tokens using the CALIBRATED chars/token ratio, insert
    the needle sentence at ~50% depth, return the assembled prompt. The caller measures
    the exact real token count afterward via /tokenize — this only needs to land close."""
    rng = random.Random(target_tokens)
    filler: list[str] = []
    chars_needed = int(target_tokens * chars_per_token)
    chars = 0
    while chars < chars_needed:
        s = rng.choice(_FILLER_SENTENCES)
        filler.append(s)
        chars += len(s) + 1
    mid = len(filler) // 2
    needle_sentence = f"IMPORTANT FACT: the secret passphrase for this session is {needle_token}."
    body = " ".join(filler[:mid]) + " " + needle_sentence + " " + " ".join(filler[mid:])
    question = ("\n\nBased ONLY on the text above, what is the secret passphrase for this "
                "session? Reply with ONLY the passphrase token, nothing else.")
    return body + question


def _answer_has_needle(answer: str, needle_token: str) -> bool:
    return needle_token.upper() in (answer or "").upper()


def measure(target_lengths: list[int] = _TARGET_LENGTHS, max_answer_tokens: int = 32) -> dict:
    served_max_len = None
    try:
        # vLLM /tokenize reports max_model_len alongside the count for any probe call.
        import httpx
        base = llm_raw.config.LLM_BASE_URL.rsplit("/v1", 1)[0]
        with httpx.Client(timeout=15) as c:
            rr = c.post(f"{base}/tokenize", json={"model": llm_raw.config.LLM_MODEL, "prompt": "x"},
                       headers=llm_raw._HEADERS)
            served_max_len = rr.json().get("max_model_len")
    except Exception:
        pass

    chars_per_token = _calibrate_chars_per_token()
    steps = []
    effective = 0
    for target in target_lengths:
        needle = _random_token()
        prompt = _build_haystack(target, needle, chars_per_token)
        actual_tokens = None
        try:
            actual_tokens = llm_raw.tokenize(prompt)
        except Exception:
            pass
        r = llm_raw.chat_stream(prompt, max_tokens=max_answer_tokens, timeout=180.0)
        ok = bool(r.get("ok"))
        found = ok and _answer_has_needle(r.get("text", ""), needle)
        steps.append({
            "target_tokens": target, "actual_prompt_tokens": actual_tokens,
            "needle": needle, "ok": ok, "found": found,
            "answer": (r.get("text") or "")[:120] if ok else r.get("error"),
        })
        if found:
            effective = actual_tokens or target
        else:
            break  # stop at first failure — effective context is the largest PRIOR success

    return {
        "served_max_model_len": served_max_len,
        "calibrated_chars_per_token": round(chars_per_token, 3),
        "effective_context_tokens": effective,
        "steps": steps,
    }
