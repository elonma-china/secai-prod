"""Cypher builders, entity/product extraction and result formatting for neo4j_search.

Pure logic (no I/O). The graph is ID-only (CWE/CAPEC/Technique/Tactic nodes carry no
name/description), so free text is not supported here — callers resolve an ID via
qdrant_search first.
"""
from __future__ import annotations

import re
from typing import Any

MAX_LIMIT = 50
MAX_ARRAY_ITEMS = 20
MAX_OUTPUT_CHARS = 4000

# Non-anchored: the entity ID is extracted from anywhere in the query, so the model can
# pass the user's whole question ("CVE-2025-0282 liên quan CWE nào?") without a needs_id
# retry. Order = extraction priority when several IDs co-occur; the leftmost match wins.
# TACTIC before TECH so "TA0003" isn't mis-read; CVE before CWE (both start "C").
_ENTITY_RES: list[tuple[str, "re.Pattern[str]"]] = [
    ("cve", re.compile(r"CVE-\d{4}-\d+", re.I)),
    ("capec", re.compile(r"CAPEC-\d+", re.I)),
    ("cwe", re.compile(r"CWE-\d+", re.I)),
    ("tactic", re.compile(r"\bTA\d{4}\b", re.I)),
    ("technique", re.compile(r"\bT\d{4}(?:\.\d{3})?\b", re.I)),
]


def extract_entity(query: str) -> tuple[str, str] | None:
    """Find the leftmost CVE/CWE/CAPEC/Technique/Tactic id anywhere in the query."""
    best: tuple[int, str, str] | None = None
    for kind, regex in _ENTITY_RES:
        m = regex.search(query or "")
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), kind, m.group(0).upper())
    return (best[1], best[2]) if best else None


# Product names in the graph are normalised snake_case tokens (connect_secure,
# linux_kernel) — distinctive enough to spot in a sentence and unlikely to collide with
# ordinary words. When no ID is present we treat such a token (or a single bare word) as
# a Product entry point so "CVE nào của connect_secure" works without an ID.
_SNAKE_RE = re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b", re.I)


def extract_product(query: str) -> str | None:
    m = _SNAKE_RE.search(query or "")
    if m:
        return m.group(0).lower()
    q = (query or "").strip().rstrip("?.!,")
    if q and " " not in q:
        return q.lower()
    return None


# Count/statistics intent: aggregate instead of listing ("bao nhiêu CVE critical dùng
# CWE-89", "how many KEV CVEs affect connect_secure").
COUNT_RE = re.compile(r"bao nhiêu|số lượng|tổng số|thống kê|đếm|how many|number of|\bcount\b|\btop\b", re.I)

# Fixed label/relationship map for count mode — values are internal constants (never user
# input), so interpolating them into Cypher is safe.
_COUNT_SPEC = {
    "cwe": ("CWE", "id", "HAS_WEAKNESS"),
    "capec": ("CAPEC", "id", "MAPS_TO_CAPEC"),
    "technique": ("Technique", "id", "EXPLOITS_TECHNIQUE"),
    "product": ("Product", "name", "AFFECTS"),
}


def build_cypher(kind: str, entity_id: str, depth: int, mode: str = "list") -> tuple[str, dict[str, Any]] | None:
    """Return (statement, parameters) for the extracted (kind, id), or None."""
    limit = min(depth * 10, MAX_LIMIT)
    q = entity_id

    # Count/statistics mode: aggregate the CVEs linked to the entity by severity + KEV.
    if mode == "count" and kind in _COUNT_SPEC:
        label, key, rel = _COUNT_SPEC[kind]
        return (
            f"""
            MATCH (n:{label} {{{key}: $id}})<-[:{rel}]-(c:CVE)
            RETURN $id AS entity, count(c) AS total_cves,
                   count(CASE WHEN c.severity = 'CRITICAL' THEN 1 END) AS critical,
                   count(CASE WHEN c.severity = 'HIGH' THEN 1 END) AS high,
                   count(CASE WHEN c.severity = 'MEDIUM' THEN 1 END) AS medium,
                   count(CASE WHEN c.severity = 'LOW' THEN 1 END) AS low,
                   count(CASE WHEN c.is_in_kev THEN 1 END) AS kev_exploited
            """.strip(),
            {"id": q},
        )
    if mode == "count" and kind == "tactic":
        return (
            """
            MATCH (tac:Tactic {id: $id})<-[:FOLLOWS_TACTIC]-(t:Technique)
            RETURN tac.id AS tactic, count(DISTINCT t) AS techniques
            """.strip(),
            {"id": q},
        )

    if kind == "product":
        return (
            """
            MATCH (p:Product {name: $id})<-[:AFFECTS]-(c:CVE)
            WITH p, c
            ORDER BY c.cvss_score IS NULL, c.cvss_score DESC
            WITH p, collect(c)[..$limit] AS cves
            UNWIND cves AS c
            OPTIONAL MATCH (c)-[:HAS_WEAKNESS]->(cwe:CWE)
            RETURN p.name AS product,
                   collect(DISTINCT {id: c.id, severity: c.severity, cvss: c.cvss_score, kev: c.is_in_kev, year: c.year}) AS cves,
                   collect(DISTINCT cwe.id) AS cwes
            """.strip(),
            {"id": q, "limit": limit},
        )

    if kind == "cve":
        return (
            """
            MATCH (c:CVE {id: $id})
            OPTIONAL MATCH (c)-[:HAS_WEAKNESS]->(cwe:CWE)
            OPTIONAL MATCH (c)-[:EXPLOITS_TECHNIQUE]->(t:Technique)
            OPTIONAL MATCH (t)-[:FOLLOWS_TACTIC]->(tac:Tactic)
            OPTIONAL MATCH (c)-[:MAPS_TO_CAPEC]->(capec:CAPEC)
            OPTIONAL MATCH (c)-[:AFFECTS]->(p:Product)
            RETURN c.id AS cve, c.severity AS severity, c.cvss_score AS cvss,
                   c.is_in_kev AS is_in_kev, c.year AS year,
                   collect(DISTINCT cwe.id) AS cwes,
                   collect(DISTINCT t.id) AS techniques,
                   collect(DISTINCT tac.id) AS tactics,
                   collect(DISTINCT capec.id) AS capecs,
                   collect(DISTINCT p.name) AS products
            LIMIT $limit
            """.strip(),
            {"id": q, "limit": limit},
        )

    if kind == "cwe":
        return (
            """
            MATCH (cwe:CWE {id: $id})
            OPTIONAL MATCH (cwe)<-[:HAS_WEAKNESS]-(c:CVE)
            WITH cwe, c
            ORDER BY c.cvss_score IS NULL, c.cvss_score DESC
            WITH cwe, collect(c)[..$limit] AS cves
            UNWIND cves AS c
            OPTIONAL MATCH (c)-[:EXPLOITS_TECHNIQUE]->(t:Technique)
            OPTIONAL MATCH (c)-[:MAPS_TO_CAPEC]->(capec:CAPEC)
            RETURN cwe.id AS cwe,
                   collect(DISTINCT {id: c.id, severity: c.severity, cvss: c.cvss_score, kev: c.is_in_kev, year: c.year}) AS cves,
                   collect(DISTINCT t.id) AS techniques,
                   collect(DISTINCT capec.id) AS capecs
            """.strip(),
            {"id": q, "limit": limit},
        )

    if kind == "capec":
        return (
            """
            MATCH (capec:CAPEC {id: $id})
            OPTIONAL MATCH (capec)<-[:MAPS_TO_CAPEC]-(c:CVE)
            WITH capec, c
            ORDER BY c.cvss_score IS NULL, c.cvss_score DESC
            WITH capec, collect(c)[..$limit] AS cves
            UNWIND cves AS c
            OPTIONAL MATCH (c)-[:HAS_WEAKNESS]->(cwe:CWE)
            OPTIONAL MATCH (c)-[:EXPLOITS_TECHNIQUE]->(t:Technique)
            RETURN capec.id AS capec,
                   collect(DISTINCT {id: c.id, severity: c.severity, cvss: c.cvss_score, kev: c.is_in_kev, year: c.year}) AS cves,
                   collect(DISTINCT cwe.id) AS cwes,
                   collect(DISTINCT t.id) AS techniques
            """.strip(),
            {"id": q, "limit": limit},
        )

    if kind == "technique":
        return (
            """
            MATCH (t:Technique {id: $id})
            OPTIONAL MATCH (t)-[:FOLLOWS_TACTIC]->(tac:Tactic)
            OPTIONAL MATCH (t)<-[:EXPLOITS_TECHNIQUE]-(c:CVE)
            WITH t, tac, c
            ORDER BY c.cvss_score IS NULL, c.cvss_score DESC
            WITH t, collect(DISTINCT tac.id) AS tactics, collect(c)[..$limit] AS cves
            UNWIND cves AS c
            OPTIONAL MATCH (c)-[:HAS_WEAKNESS]->(cwe:CWE)
            RETURN t.id AS technique, tactics,
                   collect(DISTINCT {id: c.id, severity: c.severity, cvss: c.cvss_score, kev: c.is_in_kev, year: c.year}) AS cves,
                   collect(DISTINCT cwe.id) AS cwes
            """.strip(),
            {"id": q, "limit": limit},
        )

    if kind == "tactic":
        return (
            """
            MATCH (tac:Tactic {id: $id})
            OPTIONAL MATCH (tac)<-[:FOLLOWS_TACTIC]-(t:Technique)
            RETURN tac.id AS tactic, collect(DISTINCT t.id)[..$limit] AS techniques
            """.strip(),
            {"id": q, "limit": limit},
        )

    return None


def _serialize(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return str(v)
    if isinstance(v, list):
        return ", ".join(_serialize(x) for x in v)
    if isinstance(v, dict):
        return " ".join(f"{k}={_serialize(val)}" for k, val in v.items() if val not in (None, ""))
    return str(v)


def format_results(columns: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "No results found for that ID. The entity may not be in the knowledge graph."
    blocks = []
    for i, row in enumerate(rows):
        parts = []
        for j, col in enumerate(columns):
            val = row[j] if j < len(row) else None
            if val is None:
                continue
            if isinstance(val, list):
                if not val:
                    continue
                items = [f"  - {_serialize(x)}" for x in val[:MAX_ARRAY_ITEMS]]
                suffix = f"\n  ... ({len(val) - MAX_ARRAY_ITEMS} more truncated)" if len(val) > MAX_ARRAY_ITEMS else ""
                parts.append(f"**{col}:**\n" + "\n".join(items) + suffix)
            else:
                parts.append(f"**{col}:** {_serialize(val)}")
        blocks.append(f"### Row {i + 1}\n" + "\n".join(parts))
    out = "\n\n---\n\n".join(blocks)
    return out[:MAX_OUTPUT_CHARS] + f"\n\n... (truncated at {MAX_OUTPUT_CHARS} chars)" if len(out) > MAX_OUTPUT_CHARS else out
