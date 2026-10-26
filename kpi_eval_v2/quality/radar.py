"""R2-RADAR90 — DERIVED (not an independent measurement): clamped weighted mean of 5 axes
built from the other quality KPIs already computed this run:
  Faithfulness = R2-FAITH98, Relevance = R2-RELEV90, Entity = R2-ENTITY100,
  Safety = 100 - R2-HALLU2,
  Reasoning = mean(R2-SAST85, R2-EXPLOIT85, R2-REMED60) — proxy: these three all require the
    model to reason about a specific vulnerability/code/remediation rather than recite a
    lookup, so their judge-graded pass rate stands in for a dedicated "reasoning" score.
Each axis clamped to [0, 100] before averaging so a >100 or negative input can't distort the
composite. Equal weights (1/5 each) — no axis-specific weighting scheme was specified.
"""
from __future__ import annotations

from .. import schema


def _clamp(v):
    if v is None:
        return None
    return max(0.0, min(100.0, float(v)))


def compute_radar(by_id: dict[str, dict]) -> dict:
    faith = _clamp(by_id.get("R2-FAITH98", {}).get("value"))
    relev = _clamp(by_id.get("R2-RELEV90", {}).get("value"))
    entity = _clamp(by_id.get("R2-ENTITY100", {}).get("value"))
    hallu = by_id.get("R2-HALLU2", {}).get("value")
    safety = _clamp(100 - hallu) if hallu is not None else None
    reasoning_parts = [by_id.get(k, {}).get("value") for k in ("R2-SAST85", "R2-EXPLOIT85", "R2-REMED60")]
    reasoning_parts = [_clamp(v) for v in reasoning_parts if v is not None]
    reasoning = round(sum(reasoning_parts) / len(reasoning_parts), 2) if reasoning_parts else None

    axes = {"faithfulness": faith, "relevance": relev, "entity": entity,
            "reasoning": reasoning, "safety": safety}
    present = [v for v in axes.values() if v is not None]
    missing = [k for k, v in axes.items() if v is None]
    radar = round(sum(present) / len(present), 2) if present else None

    notes = "DERIVED from other quality KPIs this run (see module docstring), not independently measured."
    if missing:
        notes += f" Missing axes (excluded from mean, not zero-filled): {missing}."

    return schema.make_result(
        "R2-RADAR90", radar,
        method="clamped equal-weight mean of 5 axes: Faithfulness/Relevance/Entity/Reasoning/Safety",
        basis=f"axes={axes}", n=len(present), evidence="", notes=notes, extra={"axes": axes})
