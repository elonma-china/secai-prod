"""R2-ENTITY100 (Entity Precision, CVE/CWE ids the gateway asserts must be real) and
R2-HALLU2 (Hallucination Rate) — both FAIL-CLOSED: a KB lookup error is excluded from the
denominator (never silently counted as "real"/"pass"), and the error count is reported.

Two independent evidence pools feed R2-HALLU2:
  A. every CVE/CWE id the gateway's answers (to real, in-KB questions) assert -> checked
     against Qdrant. An id that positively does NOT exist in the KB is a fabrication.
  B. a handful of KNOWN-NONEXISTENT CVE ids (verified absent from the KB right before use,
     so the probe itself cannot be stale/wrong) asked directly -> the system should refuse
     / say not-found. A confident, non-refusing answer (or a fabricated citation URL
     containing the fake id) counts as a hallucination.
R2-ENTITY100 is computed from pool A only (precision over real, in-domain answers).
"""
from __future__ import annotations

import json
import re

from .. import config, schema
from . import common

URL_RE = re.compile(r"https?://[^\s)\]\"']+")

# Syntactically valid CVE ids that are essentially certain not to exist (verified absent
# from the KB at run time before use — see _pick_fake_cves — so this probe is honest even
# if one of these were ever coincidentally assigned in the future).
_FAKE_CVE_CANDIDATES = [
    "CVE-2099-88888", "CVE-2088-00001", "CVE-2200-12345",
    "CVE-1990-00001", "CVE-2026-9999999", "CVE-2077-54321",
]


def _pick_fake_cves(n: int = 5) -> list[str]:
    picked = []
    for c in _FAKE_CVE_CANDIDATES:
        cnt = common.kb_count("cve_id", c)
        if cnt == 0:  # confirmed absent (None=error -> skip, don't risk an uncertain probe)
            picked.append(c)
        if len(picked) >= n:
            break
    return picked


def run_entity_kpis(answered: list[dict]) -> list[dict]:
    """``answered``: gateway answers for acceptance_indep_dinh_danh (shared with ragas_kpi)."""
    usable = [a for a in answered if not a["error"] and (a["answer"] or "").strip()]

    kb_cache: dict[tuple[str, str], int | None] = {}

    def kb_exists(kind: str, ident: str) -> int | None:
        key = (kind, ident)
        if key not in kb_cache:
            field = "cve_id" if kind == "cve" else "cwe_refs"
            kb_cache[key] = common.kb_count(field, ident)
        return kb_cache[key]

    # ── Pool A: entities mentioned in real answers ──────────────────────────────────────
    mentioned_total = 0
    real_total = 0
    bad_total = 0
    n_kb_errors_a = 0
    item_log = []
    for a in usable:
        cves, cwes = common.extract_ids(a["answer"])
        ids = [("cve", c) for c in cves] + [("cwe", c) for c in cwes]
        item_bad = []
        for kind, ident in ids:
            cnt = kb_exists(kind, ident)
            if cnt is None:
                n_kb_errors_a += 1
                continue  # fail-closed: exclude from denominator entirely
            mentioned_total += 1
            if cnt > 0:
                real_total += 1
            else:
                bad_total += 1
                item_bad.append(ident)
        if ids:
            item_log.append({"id": a["id"], "mentioned": [i for _, i in ids], "fabricated": item_bad})

    entity_precision_pct = round(100 * real_total / mentioned_total, 2) if mentioned_total else None

    # ── Pool B: known-nonexistent CVE probes ────────────────────────────────────────────
    fake_cves = _pick_fake_cves(5)
    probe_items = [{"id": f"hallu_probe_{c}", "input": f"{c} là lỗ hổng gì? Mô tả chi tiết.", "fake_cve": c}
                   for c in fake_cves]
    from .answers import collect_answers
    probed = collect_answers(probe_items, message_key="input", cache_name="hallu_probe") if probe_items else []

    n_probe_bad = 0
    n_probe_checked = 0
    probe_log = []
    for p in probed:
        if p["error"]:
            continue  # gateway error (e.g. rate-limit) -> exclude, not a measurement
        n_probe_checked += 1
        refused = common.looks_like_refusal(p["answer"])
        fabricated_url = any(p["fake_cve"].lower() in u.lower() for u in URL_RE.findall(p["answer"] or ""))
        is_bad = (not refused) or fabricated_url
        n_probe_bad += 1 if is_bad else 0
        probe_log.append({"fake_cve": p["fake_cve"], "answer": (p["answer"] or "")[:400],
                          "refused": refused, "fabricated_url": fabricated_url, "hallucinated": is_bad})

    denom = mentioned_total + n_probe_checked
    numer = bad_total + n_probe_bad
    hallu_pct = round(100 * numer / denom, 2) if denom else None

    log_path = config.RESULTS / "quality_entity_hallu_items.jsonl"
    with log_path.open("w", encoding="utf-8") as f:
        for rec in item_log:
            f.write(json.dumps({"pool": "A_mentions", **rec}, ensure_ascii=False) + "\n")
        for rec in probe_log:
            f.write(json.dumps({"pool": "B_fake_probe", **rec}, ensure_ascii=False) + "\n")

    basis_entity = (f"gateway :8131 real answers to acceptance_indep_dinh_danh, "
                     f"n_answers_with_entity_mentions={len(item_log)}, mentioned_ids={mentioned_total}, "
                     f"n_kb_lookup_errors_excluded={n_kb_errors_a}; KB=Qdrant {common.QDRANT_COLLECTION}")
    basis_hallu = (basis_entity + f"; + fake-CVE refusal probe n={n_probe_checked}"
                    f" (requested {len(probe_items)}, {len(probe_items) - n_probe_checked} gateway errors)")

    return [
        schema.make_result(
            "R2-ENTITY100", entity_precision_pct,
            method="regex-extract CVE/CWE ids from real gateway answers -> Qdrant existence "
                   "check (fail-closed: lookup error excluded from denominator, not counted real)",
            basis=basis_entity, n=mentioned_total, evidence=str(log_path),
            notes="Pool A only (in-domain answers) — see module docstring.",
            extra={"real": real_total, "fabricated": bad_total, "n_kb_errors_excluded": n_kb_errors_a},
        ),
        schema.make_result(
            "R2-HALLU2", hallu_pct,
            method="fail-closed hallucination rate = (fabricated CVE/CWE mentions in real answers "
                   "+ non-refusals/fabricated-citation-URLs on known-nonexistent CVE probes) / "
                   "(checked mentions + checked probes)",
            basis=basis_hallu, n=denom, evidence=str(log_path),
            notes=f"fake CVE ids used (confirmed absent from KB at run time): {fake_cves}",
            extra={"pool_a_bad": bad_total, "pool_a_checked": mentioned_total,
                   "pool_b_bad": n_probe_bad, "pool_b_checked": n_probe_checked,
                   "fake_cve_probes": fake_cves},
        ),
    ]
