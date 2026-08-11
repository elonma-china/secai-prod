"""Triage validation — BB ``skills/triage-validation`` 7-Question Gate.

Hybrid:
  1. Deterministic hard floor (unreachable, invented CVE, tool-dump noise)
  2. LLM verify (preferred) applies the 7 questions with reasoned keep/reject
  3. Falls back to **strict** deterministic if LLM unavailable / bad JSON
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .llm_hunt import extract_json_object

logger = logging.getLogger(__name__)

_ALWAYS_REJECT = {
    "version_disclosure",
    "missing_security_header",
    "clickjacking_without_sensitive",
    "ssl_tls_best_practice",
    "spf_dmarc_informational",
}

_TOOL_DUMP = {
    "bh_http_probe",
    "run_recon",
    "run_vuln_scan",
    "run_verify_leads",
    "run_parallel_hunt_wave",
    "run_cors_check",
    "run_secret_hunt",
    "run_api_fuzz",
    "run_param_discovery",
    "run_post_param_discovery",
    "run_sqlmap_targeted",
    "run_sqlmap_on_file",
    "run_js_analysis",
    "run_jwt_audit",
    "run_cms_exploit",
    "run_rce_scan",
    "update_working_memory",
    "read_recon_summary",
    "read_findings_summary",
    "finish",
    "finding",
}

MAX_KEPT = 15

TRIAGE_SYSTEM = """You are a bug-bounty triage reviewer using the 7-Question Gate.
For EACH lead, answer Q1–Q7 strictly. One failed question = REJECT that lead.
Do NOT invent CVE IDs. Do NOT mark exploit success. Prefer killing weak leads.
REJECT tool-wrapper noise (run_vuln_scan dumps, probe summaries) — only keep concrete vul_class leads.

Q1: Can an attacker use this RIGHT NOW with a concrete HTTP request?
Q2: Is the impact on a typical accepted BB impact list (not pure noise)?
Q3: Is it in scope given the scope note / reachable target?
Q4: Is it not duplicate fingerprint noise (banner-only / missing headers)?
Q5: Is it reproducible from the cited evidence?
Q6: Is the claim non-theoretical (no "could potentially")?
Q7: Is severity worth reporting (not pure info fluff)?

Return ONLY JSON:
{
  "results": [
    {
      "index": 0,
      "keep": true,
      "failed": [],
      "reason": "short",
      "answers": {"Q1": true, "Q2": true, "Q3": true, "Q4": true, "Q5": true, "Q6": true, "Q7": true}
    }
  ],
  "summary": "one sentence"
}
"""


def _dedupe_leads(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for h in leads:
        key = (str(h.get("vul_class") or ""), str(h.get("path") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def _is_tool_dump(lead: dict[str, Any]) -> bool:
    vul = str(lead.get("vul_class") or lead.get("tool") or "").strip().lower()
    if vul in _TOOL_DUMP or vul.startswith("run_"):
        return True
    src = str(lead.get("source") or "")
    if src in _TOOL_DUMP and not lead.get("path") and src != "verify_high_signal":
        # findings_log tool wrappers without path
        if not str(lead.get("vul_class") or "").strip() or vul in _TOOL_DUMP:
            return True
    return False


def _hard_reject(lead: dict[str, Any], surface: dict[str, Any]) -> str | None:
    """Deterministic floor — always applied before LLM."""
    if not surface.get("reachable"):
        return "target unreachable"
    if lead.get("cve") and not lead.get("cve_evidence"):
        return "CVE claimed without template/scan evidence"
    vul = str(lead.get("vul_class") or lead.get("tool") or "").strip().lower()
    if vul in _ALWAYS_REJECT:
        return f"always-rejected class: {vul}"
    if _is_tool_dump(lead):
        return f"tool-dump noise: {vul or 'unknown'}"
    return None


def _q_answers_deterministic(
    lead: dict[str, Any],
    surface: dict[str, Any],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    evidence = str(lead.get("evidence") or lead.get("text") or "").strip()
    vul = str(lead.get("vul_class") or lead.get("tool") or "").strip().lower()
    conf = float(lead.get("confidence") or 0)
    reachable = bool(surface.get("reachable"))
    has_path = bool(lead.get("path") or lead.get("url") or ("/" in evidence[:80]))
    source = str(lead.get("source") or "")
    verified = source == "verify_high_signal" or source.endswith("verify")

    if strict:
        # Fail closed: need concrete path + strong confidence or verify source
        q1 = reachable and has_path and (verified or conf >= 0.75) and len(evidence) >= 24
        q2 = (
            vul not in _ALWAYS_REJECT
            and vul not in _TOOL_DUMP
            and "needs_deeper" not in vul
            and vul != "lab_demo_app"
            and not vul.startswith("run_")
        )
        q3 = reachable
        q4 = vul not in {"stack_php", "information_disclosure", "auth_surface"} or conf >= 0.8
        q5 = len(evidence) >= 24 and has_path
        low = evidence.lower()
        q6 = not any(p in low for p in ("could potentially", "may allow", "might be", "theoretical"))
        q7 = conf >= 0.7 or verified
    else:
        q1 = reachable and conf >= 0.35 and (len(evidence) >= 12 or has_path)
        q2 = (
            vul not in _ALWAYS_REJECT
            and "needs_deeper" not in vul
            and vul != "lab_demo_app"
            and vul not in _TOOL_DUMP
        )
        q3 = reachable
        q4 = vul not in {"stack_php"} or conf >= 0.7
        q5 = len(evidence) >= 8
        low = evidence.lower()
        q6 = not any(p in low for p in ("could potentially", "may allow", "might be", "theoretical"))
        q7 = conf >= 0.35

    answers = {
        "Q1_actionable_now": q1,
        "Q2_impact_accepted": q2,
        "Q3_in_scope": q3,
        "Q4_not_noise": q4,
        "Q5_reproducible_evidence": q5,
        "Q6_not_theoretical": q6,
        "Q7_severity_floor": q7,
    }
    failed = [k for k, v in answers.items() if not v]
    return {"answers": answers, "failed": failed, "passed": not failed}


def _collect_leads(
    hunt: dict[str, Any],
    findings_hints: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    hyps = list(hunt.get("hypotheses") or [])
    for fh in findings_hints or []:
        vul = fh.get("vul_class")
        tool = fh.get("tool")
        # Skip raw tool dumps from findings_log
        if not vul or str(vul) in _TOOL_DUMP or str(tool or "") in _TOOL_DUMP:
            if str(fh.get("source") or "") != "verify_high_signal" and tool != "verify_high_signal":
                continue
        hyps.append({
            "vul_class": vul or "finding",
            "cve": fh.get("cve"),
            "confidence": float(fh.get("confidence") or 0.55),
            "evidence": fh.get("evidence") or fh.get("text") or "",
            "path": fh.get("path"),
            "source": fh.get("source") or "findings",
            "cve_evidence": fh.get("cve_evidence"),
        })
    return _dedupe_leads(hyps)


def _cap_kept(kept: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(kept, key=lambda h: float(h.get("confidence") or 0), reverse=True)
    return _dedupe_leads(ranked)[:MAX_KEPT]


def triage_leads_deterministic(
    surface: dict[str, Any],
    hunt: dict[str, Any],
    *,
    findings_hints: list[dict[str, Any]] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Deterministic 7Q proxy. ``strict=True`` for LLM-fallback (fail closed)."""
    hyps = _collect_leads(hunt, findings_hints)
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for h in hyps:
        hard = _hard_reject(h, surface)
        if hard:
            rejected.append({**h, "reason": hard, "triage": {"mode": "deterministic", "failed": [hard]}})
            continue
        gate = _q_answers_deterministic(h, surface, strict=strict)
        if not gate["passed"]:
            rejected.append({
                **h,
                "reason": f"triage failed: {', '.join(gate['failed'])}",
                "triage": {**gate, "mode": "deterministic_strict" if strict else "deterministic"},
            })
            continue
        kept.append({
            **h,
            "validated": True,
            "note": "passed deterministic triage proxy",
            "triage": {**gate, "mode": "deterministic_strict" if strict else "deterministic"},
            "cve": h.get("cve") if h.get("cve_evidence") else None,
        })

    kept = _cap_kept(kept)
    mode = "deterministic_strict" if strict else "deterministic"
    return {
        "ok": bool(kept),
        "leads": kept,
        "rejected": rejected,
        "gate": "triage-validation/7-question",
        "mode": mode,
        "summary": (
            f"Validate ({mode}): {len(kept)} kept, {len(rejected)} rejected; "
            "no exploit opened."
        ),
    }


def triage_leads(
    surface: dict[str, Any],
    hunt: dict[str, Any],
    *,
    findings_hints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Sync entry — deterministic only. Prefer ``triage_leads_llm`` in workflow."""
    return triage_leads_deterministic(surface, hunt, findings_hints=findings_hints)


def validate_hypotheses(surface: dict[str, Any], hunt: dict[str, Any]) -> dict[str, Any]:
    return triage_leads(surface, hunt)


async def triage_leads_llm(
    wf: Any,
    surface: dict[str, Any],
    hunt: dict[str, Any],
    *,
    findings_hints: list[dict[str, Any]] | None = None,
    scope_note: str = "",
    use_llm: bool = True,
) -> dict[str, Any]:
    """LLM 7-Question Gate with deterministic hard floor + strict fallback."""
    hyps = _collect_leads(hunt, findings_hints)
    if not hyps:
        return {
            "ok": False,
            "leads": [],
            "rejected": [],
            "gate": "triage-validation/7-question",
            "mode": "empty",
            "summary": "Validate: no leads to triage.",
        }

    candidates: list[tuple[int, dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    for i, h in enumerate(hyps):
        hard = _hard_reject(h, surface)
        if hard:
            rejected.append({**h, "reason": hard, "triage": {"mode": "hard_floor", "failed": [hard]}})
        else:
            candidates.append((i, h))

    if not candidates:
        return {
            "ok": False,
            "leads": [],
            "rejected": rejected,
            "gate": "triage-validation/7-question",
            "mode": "hard_floor",
            "summary": f"Validate: 0 kept, {len(rejected)} rejected by hard floor.",
        }

    if not use_llm:
        sub = triage_leads_deterministic(
            surface,
            {"hypotheses": [h for _, h in candidates]},
            strict=True,
        )
        return {
            **sub,
            "rejected": rejected + list(sub.get("rejected") or []),
            "summary": (
                f"Validate (deterministic_strict): {len(sub.get('leads') or [])} kept, "
                f"{len(rejected) + len(sub.get('rejected') or [])} rejected."
            ),
        }

    slim_leads = []
    for idx, h in candidates:
        slim_leads.append({
            "index": idx,
            "vul_class": h.get("vul_class"),
            "confidence": h.get("confidence"),
            "evidence": str(h.get("evidence") or "")[:500],
            "cve": h.get("cve"),
            "cve_evidence": bool(h.get("cve_evidence")),
            "path": h.get("path"),
            "source": h.get("source"),
        })

    prompt = (
        f"{TRIAGE_SYSTEM}\n\n"
        f"Target reachable: {surface.get('reachable')}\n"
        f"Scope note: {(scope_note or '').strip() or '(none)'}\n"
        f"Surface summary: {surface.get('summary')}\n\n"
        f"Leads JSON:\n{json.dumps(slim_leads, ensure_ascii=False)}\n"
    )

    text = ""
    try:
        text = await wf.agent(prompt, label="triage-llm", tools=[])
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM triage failed, strict fallback: %s", exc)
        sub = triage_leads_deterministic(
            surface, {"hypotheses": [h for _, h in candidates]}, strict=True,
        )
        return {
            **sub,
            "rejected": rejected + list(sub.get("rejected") or []),
            "mode": "deterministic_fallback",
            "summary": (
                f"Validate (strict fallback after LLM error): "
                f"{len(sub.get('leads') or [])} kept; error={type(exc).__name__}"
            ),
        }

    parsed = extract_json_object(text or "")
    results = (parsed or {}).get("results") if isinstance(parsed, dict) else None
    if not isinstance(results, list) or not results:
        # One JSON repair retry
        logger.warning("LLM triage bad JSON — retry once")
        try:
            text2 = await wf.agent(
                "Your previous reply was not valid JSON. "
                "Return ONLY the JSON object with key 'results' (array) and 'summary'.\n"
                f"Leads to judge:\n{json.dumps(slim_leads, ensure_ascii=False)}\n",
                label="triage-llm-retry",
                tools=[],
            )
            parsed = extract_json_object(text2 or "")
            results = (parsed or {}).get("results") if isinstance(parsed, dict) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM triage retry failed: %s", exc)
            results = None

    if not isinstance(results, list) or not results:
        logger.warning("LLM triage unusable after retry — strict deterministic fallback")
        sub = triage_leads_deterministic(
            surface, {"hypotheses": [h for _, h in candidates]}, strict=True,
        )
        return {
            **sub,
            "rejected": rejected + list(sub.get("rejected") or []),
            "mode": "deterministic_fallback",
            "summary": (
                f"Validate (strict fallback — bad LLM JSON): "
                f"{len(sub.get('leads') or [])} kept"
            ),
        }

    by_index = {int(r.get("index")): r for r in results if isinstance(r, dict) and "index" in r}
    kept: list[dict[str, Any]] = []
    for idx, h in candidates:
        r = by_index.get(idx)
        if not r:
            gate = _q_answers_deterministic(h, surface, strict=True)
            if gate["passed"]:
                kept.append({
                    **h,
                    "validated": True,
                    "note": "LLM omitted index — strict deterministic keep",
                    "triage": {**gate, "mode": "hybrid"},
                    "cve": h.get("cve") if h.get("cve_evidence") else None,
                })
            else:
                rejected.append({
                    **h,
                    "reason": "LLM omitted index; strict deterministic reject",
                    "triage": {**gate, "mode": "hybrid"},
                })
            continue

        keep = bool(r.get("keep"))
        failed = list(r.get("failed") or [])
        reason = str(r.get("reason") or ("kept" if keep else "rejected by LLM triage"))
        triage_meta = {
            "mode": "llm",
            "answers": r.get("answers") or {},
            "failed": failed,
            "passed": keep,
        }
        if keep:
            kept.append({
                **h,
                "validated": True,
                "note": reason,
                "triage": triage_meta,
                "cve": h.get("cve") if h.get("cve_evidence") else None,
            })
        else:
            rejected.append({**h, "reason": reason, "triage": triage_meta})

    kept = _cap_kept(kept)
    summary = (parsed or {}).get("summary") if isinstance(parsed, dict) else None
    return {
        "ok": bool(kept),
        "leads": kept,
        "rejected": rejected,
        "gate": "triage-validation/7-question",
        "mode": "llm",
        "summary": summary or (
            f"Validate (LLM triage): {len(kept)} kept, {len(rejected)} rejected; "
            "no exploit opened."
        ),
    }
