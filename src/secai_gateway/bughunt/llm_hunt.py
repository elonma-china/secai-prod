"""LLM Hunt helpers: prompt, JSON parse, merge with deterministic baseline."""
from __future__ import annotations

import json
import re
from typing import Any


HUNT_JSON_HINT = """Return ONLY one JSON object (no markdown fences) with this shape:
{
  "hypotheses": [
    {
      "vul_class": "string",
      "cve": null,
      "confidence": 0.0,
      "evidence": "string grounded in the surface JSON",
      "next": "string"
    }
  ],
  "summary": "one short sentence"
}
Rules:
- Do NOT invent CVE IDs. Set "cve" to null always in this phase.
- confidence in [0,1]; only cite evidence present in the provided surface.
- Prefer concrete leads (disclosure, auth surface, stack) over vague guesses.
"""


def build_hunt_prompt(*, target: str, scope_note: str, surface: dict[str, Any]) -> str:
    note = (scope_note or "").strip() or "(none)"
    # Keep prompt bounded for Cyber-F1 context.
    slim = {
        "base_url": surface.get("base_url"),
        "reachable": surface.get("reachable"),
        "tech": surface.get("tech"),
        "findings": (surface.get("findings") or [])[:15],
        "summary": surface.get("summary"),
        "errors": (surface.get("errors") or [])[:5],
    }
    return (
        f"You are the Hunt worker in a scan-first bughunt workflow.\n"
        f"Target: {target}\n"
        f"Scope note: {note}\n\n"
        f"Surface evidence (deterministic recon — ground truth):\n"
        f"{json.dumps(slim, ensure_ascii=False)}\n\n"
        f"Optional: call bh_http_probe once if you need a fresh probe; otherwise use the JSON above.\n"
        f"{HUNT_JSON_HINT}"
    )


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort parse of a JSON object from model text."""
    raw = (text or "").strip()
    if not raw:
        return None
    # Strip ```json fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    if fence:
        raw = fence.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def normalize_hunt_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    hyps_in = data.get("hypotheses")
    if not isinstance(hyps_in, list) or not hyps_in:
        return None
    cleaned: list[dict[str, Any]] = []
    for item in hyps_in[:12]:
        if not isinstance(item, dict):
            continue
        vul = str(item.get("vul_class") or "").strip()
        if not vul:
            continue
        # Hard rule Phase 1/2a: never accept model-invented CVE here.
        cve = item.get("cve")
        if cve is not None and str(cve).strip():
            cve = None
        try:
            conf = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        cleaned.append({
            "vul_class": vul[:80],
            "cve": None,
            "confidence": conf,
            "evidence": str(item.get("evidence") or "")[:400],
            "next": str(item.get("next") or "")[:240],
            "source": "llm",
        })
    if not cleaned:
        return None
    summary = str(data.get("summary") or "").strip() or f"{len(cleaned)} LLM hypothes(es)."
    return {"hypotheses": cleaned, "summary": summary[:400], "source": "llm"}


def merge_hunt(baseline: dict[str, Any], llm: dict[str, Any] | None) -> dict[str, Any]:
    """Prefer LLM hypotheses when valid; keep baseline as fallback / annotation."""
    if not llm:
        out = dict(baseline)
        out["source"] = "deterministic"
        return out
    # Tag baseline classes for traceability.
    base_hyps = []
    for h in baseline.get("hypotheses") or []:
        if isinstance(h, dict):
            base_hyps.append({**h, "source": h.get("source") or "deterministic"})
    llm_hyps = list(llm.get("hypotheses") or [])
    # Dedupe by vul_class (LLM wins).
    by_class: dict[str, dict[str, Any]] = {}
    for h in base_hyps:
        by_class[str(h.get("vul_class"))] = h
    for h in llm_hyps:
        by_class[str(h.get("vul_class"))] = h
    merged = list(by_class.values())
    return {
        "hypotheses": merged,
        "summary": llm.get("summary") or baseline.get("summary"),
        "source": "llm+deterministic",
        "baseline_summary": baseline.get("summary"),
    }


def provider_supports_llm(agent: Any) -> bool:
    """Skip LLM path for ScriptedProvider (unit tests) and missing providers."""
    provider = getattr(agent, "provider", None)
    if provider is None:
        return False
    return type(provider).__name__ != "ScriptedProvider"
