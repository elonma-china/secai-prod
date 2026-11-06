"""Payload filtering, entity-ID auto-detection and score boosting for qdrant_search.

Pure logic (no I/O): shared by the Qdrant dense path and the BM25 post-filter path so
both apply exactly the same predicate.
"""
from __future__ import annotations

import re
from typing import Any

# A bare/embedded entity ID must resolve to THAT entity. BM25 tokenises "CVE-2025-0282"
# into cve/2025/0282, and the "0282" token collides with every -0282 CVE across all
# years (CVE-2001-0282, CVE-2005-0282, …) which score higher than the intended one,
# so hybrid retrieval alone returns the wrong one. When the query names entity IDs
# we pin them as exact payload filters instead of trusting fuzzy ranking. Same failure
# mode for CWE/CAPEC/ATT&CK IDs, so all four are auto-detected.
ENTITY_ID_RES = {
    "cve_id": re.compile(r"CVE-\d{4}-\d{4,7}", re.I),
    "cwe_id": re.compile(r"CWE-\d+", re.I),
    "capec_id": re.compile(r"CAPEC-\d+", re.I),
    "technique_id": re.compile(r"\bT\d{4}(?:\.\d{3})?\b", re.I),
}


# Knowledge chunks (OWASP cheat sheets, ASVS, CWE/CAPEC/ATT&CK mitigations) live in the same
# collection as the CVE/CWE/CAPEC/ATT&CK entity docs. They carry cwe_id/cwe_refs/technique ids
# too, so without a scope an entity lookup ("CWE-89 là gì") would match them. Scopes:
#   entity    (default) — never return knowledge chunks; behaviour identical to before they existed.
#   knowledge           — only knowledge chunks, minus ones flagged as holding attack payloads.
#   all                 — no kind restriction (internal/debug).
KNOWLEDGE_KINDS = ("cheatsheet", "requirement", "mitigation", "taxonomy")
ATTACK_EXAMPLE = "contains_attack_example"
SCOPES = ("entity", "knowledge", "all")


def resolve_scope(scope: str | None, kind: list[str] | None) -> str:
    """Explicit scope wins; otherwise infer it from the requested kinds."""
    if scope in SCOPES:
        return str(scope)
    kinds = list(kind or [])
    if kinds and all(k in KNOWLEDGE_KINDS for k in kinds):
        return "knowledge"
    if any(k in KNOWLEDGE_KINDS for k in kinds):
        return "all"
    return "entity"


def entity_scope_filter() -> dict[str, Any]:
    """Qdrant filter for callers that search the raw collection and must stay entity-only."""
    return {"must_not": [{"key": "kind", "match": {"any": list(KNOWLEDGE_KINDS)}}]}


def scope_ok(payload: dict[str, Any], scope: str | None) -> bool:
    """Local mirror of the scope predicate, for hits that did not come through a Qdrant filter."""
    if scope in (None, "all"):
        return True
    is_knowledge = (payload.get("kind") or payload.get("chunk_kind")) in KNOWLEDGE_KINDS
    if scope == "knowledge":
        return is_knowledge and payload.get("sensitivity") != ATTACK_EXAMPLE
    return not is_knowledge


def upper_list(value: Any) -> list[str]:
    """Normalise a str | list id filter into a de-duplicated list of upper-cased ids."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    return list(dict.fromkeys(str(v).upper() for v in items))


def build_filter(p: dict[str, Any]) -> dict[str, Any] | None:
    must: list[dict[str, Any]] = []
    must_not: list[dict[str, Any]] = []
    scope = resolve_scope(p.get("scope"), p.get("kind"))

    def add(key: str, value: Any) -> None:
        if isinstance(value, list):
            must.append({"key": key, "match": {"any": value}})
        else:
            must.append({"key": key, "match": {"value": value}})

    def add_any_of(fields: list[str], values: list[str]) -> None:
        # Match if ANY field contains ANY value. A CWE/CAPEC/ATT&CK id lives on the
        # definition node's own id field (cwe_id/capec_id/technique_id) AND on the
        # referencing CVE's array field (cwe_refs/capec_entries/mitre_techniques), so the
        # OR returns both the concept definition and the CVEs that reference it.
        must.append({"should": [{"key": f, "match": {"any": values}} for f in fields]})

    if p.get("kind"):
        add("kind", list(p["kind"]))
    elif scope == "knowledge":
        add("kind", list(KNOWLEDGE_KINDS))
    if scope == "entity":
        must_not.append({"key": "kind", "match": {"any": list(KNOWLEDGE_KINDS)}})
    elif scope == "knowledge":
        must_not.append({"key": "sensitivity", "match": {"value": ATTACK_EXAMPLE}})
    # Entity IDs accept str or list (auto-detected IDs arrive as lists).
    if p.get("cve_id"):
        add("cve_id", upper_list(p["cve_id"]))
    if p.get("cwe_id"):
        add_any_of(["cwe_refs", "cwe_id"], upper_list(p["cwe_id"]))
    if p.get("capec_id"):
        add_any_of(["capec_entries", "capec_id"], upper_list(p["capec_id"]))
    if p.get("technique_id"):
        add_any_of(["mitre_techniques", "technique_id"], upper_list(p["technique_id"]))
    if p.get("product"):
        add("products", p["product"])
    if p.get("severity"):
        add("severity", list(p["severity"]))
    if p.get("year"):
        add("year", list(p["year"]))
    if p.get("cvss_min") is not None or p.get("cvss_max") is not None:
        rng: dict[str, float] = {}
        if p.get("cvss_min") is not None:
            rng["gte"] = float(p["cvss_min"])
        if p.get("cvss_max") is not None:
            rng["lte"] = float(p["cvss_max"])
        must.append({"key": "cvss_score", "range": rng})
    if p.get("kev_only"):
        add("is_in_kev", True)
    out: dict[str, Any] = {}
    if must:
        out["must"] = must
    if must_not:
        out["must_not"] = must_not
    return out or None


def boost(payload: dict[str, Any], score: float) -> float:
    s = score
    if payload.get("is_in_kev") is True:
        s *= 1.5
    if payload.get("chunk_kind") == "core":
        s *= 1.2
    if payload.get("severity") == "CRITICAL":
        s *= 1.15
    elif payload.get("severity") == "HIGH":
        s *= 1.05
    year = payload.get("year")
    if isinstance(year, str) and year >= "2024":
        s *= 1.05
    return s


def passes_filter(payload: dict[str, Any], p: dict[str, Any]) -> bool:
    """Replicate the Qdrant keyword filter locally, to post-filter BM25-only hits."""
    def kw(value: Any, expected: Any) -> bool:
        if isinstance(expected, list):
            return any(v in expected for v in value) if isinstance(value, list) else value in expected
        return expected in value if isinstance(value, list) else value == expected

    def match_any_of(fields: list[str], values: list[str]) -> bool:
        vs = set(values)
        for f in fields:
            pv = payload.get(f)
            if pv is None:
                continue
            hay = {str(x).upper() for x in pv} if isinstance(pv, list) else {str(pv).upper()}
            if vs & hay:
                return True
        return False

    if not scope_ok(payload, resolve_scope(p.get("scope"), p.get("kind"))):
        return False
    if p.get("kind") and not kw(payload.get("kind") or payload.get("chunk_kind"), p["kind"]):
        return False
    if p.get("cve_id") and str(payload.get("cve_id", "")).upper() not in set(upper_list(p["cve_id"])):
        return False
    if p.get("cwe_id") and not match_any_of(["cwe_refs", "cwe_id"], upper_list(p["cwe_id"])):
        return False
    if p.get("capec_id") and not match_any_of(["capec_entries", "capec_id"], upper_list(p["capec_id"])):
        return False
    if p.get("technique_id") and not match_any_of(["mitre_techniques", "technique_id"], upper_list(p["technique_id"])):
        return False
    if p.get("product") and p["product"] not in (payload.get("products") or []):
        return False
    if p.get("severity") and not kw(payload.get("severity"), p["severity"]):
        return False
    if p.get("year") and not kw(payload.get("year"), p["year"]):
        return False
    if p.get("cvss_min") is not None or p.get("cvss_max") is not None:
        score = payload.get("cvss_score")
        if not isinstance(score, (int, float)):
            return False
        if p.get("cvss_min") is not None and score < float(p["cvss_min"]):
            return False
        if p.get("cvss_max") is not None and score > float(p["cvss_max"]):
            return False
    if p.get("kev_only") and payload.get("is_in_kev") is not True:
        return False
    return True
