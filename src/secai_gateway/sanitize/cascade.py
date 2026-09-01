"""Uncertain-band cascade: escalate an ambiguous injection score to an LLM judge instead of a
binary threshold call. See docs/input-classifier-improvement-plan.md §Ư2.

The numpy head's calibrated injection score is usually decisive: after the over-defense retrain
(2026-09-01), held-out ATTACK scores sit >= 0.60 and held-out BENIGN scores sit <= 0.36 (see
evals/sanitize/report-2026-09-01.md) — a clean 0.24 margin with zero held-out errors either side.
Scores that fall BETWEEN those observed extremes have no direct evidence in the held-out set
either way; rather than trust a threshold coin-flip there, this asks the same LLM already about
to answer the user to make the same distinction it would apply anyway if the message passes
through — reusing the ``injection_judge`` profile (no tools, JSON-only) via the existing
one-off ``runner.complete()`` pattern (mirrors poc/slicer.py::decide_keep_drop).

FAIL-SAFE, not fail-open: on ANY judge error (timeout, unreachable LLM, malformed/unparseable
response) this returns ``None`` and the caller MUST fall back to the classifier's own threshold
decision — i.e. the cascade only ever ADDS a second opinion on already-ambiguous cases when the
judge is reachable; an unreachable judge reproduces today's exact pre-cascade behavior, never a
new failure mode. Nothing here can make the guard block or pass something it would not already
have decided without the cascade, beyond an ambiguous case the judge actually adjudicates.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.S)


def build_judge_prompt(text: str, injection_score: float) -> str:
    return (
        f"Điểm nghi ngờ sơ bộ (bộ phân loại tự động): {injection_score:.3f} "
        "(0 = chắc chắn an toàn, 1 = chắc chắn injection). Điểm này rơi vào vùng xám nên cần bạn "
        "phân xử.\n\nTin nhắn cần phân xử:\n---\n" + text + "\n---"
    )


async def judge_injection(
    runner: Any,
    text: str,
    injection_score: float,
    *,
    timeout_s: float = 8.0,
) -> bool | None:
    """Ask the LLM judge whether ``text`` is a prompt-injection attempt.

    Returns ``True``/``False`` on a well-formed judge verdict, or ``None`` on any failure —
    callers MUST treat ``None`` as "no verdict, fall back", never as a decision either way.
    """
    prompt = build_judge_prompt(text, injection_score)
    try:
        result, _meta = await asyncio.wait_for(
            runner.complete("injection_judge", prompt), timeout=timeout_s)
    except Exception:  # noqa: BLE001 — a judge failure must never break chat
        logger.warning("injection-cascade judge call failed → falling back to threshold decision",
                       exc_info=True)
        return None
    match = _JSON_RE.search(result or "")
    if not match:
        logger.warning("injection-cascade judge returned unparseable response → falling back")
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("injection-cascade judge returned invalid JSON → falling back")
        return None
    verdict = payload.get("injection") if isinstance(payload, dict) else None
    if not isinstance(verdict, bool):
        logger.warning("injection-cascade judge response missing bool 'injection' field → "
                       "falling back")
        return None
    return verdict
