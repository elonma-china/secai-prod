"""R2-RPS (>=0.6 req/s) + R2-CCU (>=30 concurrent users) — REAL capacity ceiling, not an
echo of ``--concurrency``. Ramps offered concurrency upward against the RAW LLM endpoint
(:13905 — the same backend production's gateway calls, but NOT gateway :8131 itself, so
the RAG/tool-call/guard-layer pipeline and its DB/session-store writes are never touched)
until errors appear or p95 latency exceeds a bound, then reports the sustained ceiling —
the last concurrency level that stayed clean.

SAFETY GUARD: :8131 is production, and this LLM is its live backend, so a full ceiling
ramp is gated behind ``KPI2_LOAD=1``. By default only a small, low-risk validation runs
(concurrency up to ``_SMALL_LEVELS[-1]``) and the KPI is recorded with ``pass=None`` —
a real, honest number from what WAS tested, explicitly not claiming it found the ceiling.
Set ``KPI2_LOAD=1`` in a maintenance window to run the full ramp and get a certified
pass/fail against the ``>=30`` / ``>=0.6`` thresholds.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import llm_raw

_SMALL_LEVELS = [1, 2, 4]
_FULL_LEVELS = [1, 2, 4, 8, 12, 16, 24, 32, 40, 50, 60]
_SHORT_PROMPT = "In one short sentence, what is a firewall?"
_MAX_TOKENS = 16
_P95_BOUND_S = 8.0          # abort ramp if p95 latency exceeds this
_ERROR_RATE_BOUND = 0.02    # abort ramp if >2% of requests error


def _pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = int(round((p / 100) * (len(s) - 1)))
    return s[k]


def _run_level(concurrency: int, total: int) -> dict:
    lat = []
    errors = 0

    def one(_):
        r = llm_raw.chat_once(_SHORT_PROMPT, max_tokens=_MAX_TOKENS, timeout=30.0)
        return r

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for r in ex.map(one, range(total)):
            if r["ok"]:
                lat.append(r["latency_s"])
            else:
                errors += 1
    wall = time.monotonic() - t0
    ok = total - errors
    return {
        "concurrency": concurrency, "total": total, "ok": ok, "errors": errors,
        "error_rate": round(errors / total, 3) if total else None,
        "wall_s": round(wall, 2), "rps": round(ok / wall, 3) if wall else None,
        "p50_s": round(_pct(lat, 50), 3) if lat else None,
        "p95_s": round(_pct(lat, 95), 3) if lat else None,
    }


def measure() -> dict:
    full = os.environ.get("KPI2_LOAD") == "1"
    levels = _FULL_LEVELS if full else _SMALL_LEVELS

    steps = []
    ceiling = None
    for lvl in levels:
        total = max(lvl * 3, lvl)
        r = _run_level(lvl, total)
        steps.append(r)
        bad = (r["error_rate"] or 0) > _ERROR_RATE_BOUND or (r["p95_s"] or 0) > _P95_BOUND_S
        if bad:
            break
        ceiling = r  # last clean level

    return {
        "mode": "full_ramp" if full else "small_validation",
        "guard_note": ("full ramp — env KPI2_LOAD=1 set" if full else
                        "SMALL validation only (default, prod-safety guard); "
                        "set KPI2_LOAD=1 to run the full ceiling ramp in a maintenance window"),
        "steps": steps,
        "sustained_ceiling": ceiling,
    }
