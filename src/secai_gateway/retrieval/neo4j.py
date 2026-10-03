"""neo4j_search — ID-based knowledge-graph traversal over the CVE/CWE/CAPEC/ATT&CK graph.

Schema: (CVE)-[:HAS_WEAKNESS]->(CWE), (CVE)-[:EXPLOITS_TECHNIQUE]->(Technique),
        (Technique)-[:FOLLOWS_TACTIC]->(Tactic), (CVE)-[:MAPS_TO_CAPEC]->(CAPEC),
        (CVE)-[:AFFECTS]->(Product).
"""
from __future__ import annotations

import base64
import re
from typing import Any

import httpx
from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from ..config import get_settings
from .cypher import COUNT_RE, build_cypher, extract_entity, extract_product, format_results

_s = get_settings()
NEO4J_URI = _s.neo4j_uri
NEO4J_USER = _s.neo4j_user
NEO4J_PASSWORD = _s.neo4j_password
NEO4J_DATABASE = _s.neo4j_database
NEO4J_HTTP_PORT = _s.neo4j_http_port

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "A CVE/CWE/CAPEC/Technique/Tactic ID or exact product name (or a question containing one). Free text: use qdrant_search first.",
        },
    },
    "required": ["query"],
}


def _neo4j_http_url() -> str:
    if NEO4J_URI.startswith(("http://", "https://")):
        return NEO4J_URI
    m = re.match(r"^(bolt\+?s?)://([^:/]+)", NEO4J_URI)
    if not m:
        return f"http://localhost:{NEO4J_HTTP_PORT}"
    scheme, host = m.group(1), m.group(2)
    proto = "https" if scheme == "bolt+s" else "http"
    return f"{proto}://{host}:{NEO4J_HTTP_PORT}"


def _auth_header() -> str:
    token = base64.b64encode(f"{NEO4J_USER}:{NEO4J_PASSWORD}".encode()).decode()
    return f"Basic {token}"


async def cve_exists_in_kb(cve_id: str) -> bool:
    """Deterministic exact-match existence check on the same `(c:CVE {id: $id})` primary-key
    path `build_cypher("cve", ...)` uses, minus the OPTIONAL MATCH fan-out a full
    `neo4j_search` traversal needs. Used by the exploit-research prefetch (poc/prefetch.py)
    to gate the synthesize-from-CVE-definition tier on whether the CVE is real, independent
    of whether a PoC artifact exists for it. Never raises — any transport/query error is
    treated as "not confirmed" (fail-closed to the safer "not found" tier, not to synthesis)."""
    cve_id = (cve_id or "").strip().upper()
    if not cve_id:
        return False
    body = {"statements": [{
        "statement": "MATCH (c:CVE {id: $id}) RETURN count(c) AS n",
        "parameters": {"id": cve_id},
    }]}
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": _auth_header()}
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{_neo4j_http_url()}/db/{NEO4J_DATABASE}/tx/commit",
                                      json=body, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return False
    if data.get("errors"):
        return False
    results = data.get("results") or []
    rows = (results[0].get("data") if results else None) or []
    if not rows:
        return False
    row = rows[0].get("row") or []
    return bool(row and row[0])


async def cve_cwe_refs(cve_id: str) -> list[str]:
    """H1 (2026-09-22, exploit metadata grounding hygiene): the CWE ids a CVE record is
    linked to via `(CVE)-[:HAS_WEAKNESS]->(CWE)` — the same CVE-record KB lookup
    `cve_exists_in_kb` above already does (same client/auth/endpoint), extended one hop to
    the CVE's weakness edges so `/ai/exploit`'s `metadata.cwe_refs` can be populated from a
    real CVE record instead of staying an always-empty key. Never raises: any transport/
    query error, or the CVE simply not existing/having no HAS_WEAKNESS edge, all fail-closed
    to `[]` — same "absence is not an error" contract `cve_exists_in_kb` uses for `False`."""
    cve_id = (cve_id or "").strip().upper()
    if not cve_id:
        return []
    body = {"statements": [{
        "statement": "MATCH (c:CVE {id: $id})-[:HAS_WEAKNESS]->(w:CWE) RETURN DISTINCT w.id AS id",
        "parameters": {"id": cve_id},
    }]}
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": _auth_header()}
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{_neo4j_http_url()}/db/{NEO4J_DATABASE}/tx/commit",
                                      json=body, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
    if data.get("errors"):
        return []
    results = data.get("results") or []
    rows = (results[0].get("data") if results else None) or []
    return sorted({str(r["row"][0]).upper() for r in rows if r.get("row") and r["row"][0]})


async def _neo4j_search(query: str, ctx: ToolContext, depth: int = 3) -> ToolResult:
    depth = max(1, min(depth or 3, 5))
    mode = "count" if COUNT_RE.search(query or "") else "list"
    # Prefer an explicit entity ID; otherwise fall back to a product-name entry point.
    entity = extract_entity(query)
    if entity is None:
        product = extract_product(query)
        entity = ("product", product) if product else None
    plan = build_cypher(entity[0], entity[1], depth, mode) if entity else None

    if plan is None:
        return ToolResult(
            content=(
                "No CVE/CWE/CAPEC/Technique/Tactic ID or product name found in the query. Run "
                "qdrant_search first to resolve an ID, then call neo4j_search with it."
            ),
            summary=f"neo4j_search({query}) needs_id",
        )

    statement, parameters = plan
    http_base = _neo4j_http_url()
    body = {"statements": [{"statement": statement, "parameters": parameters, "resultDataContents": ["row"]}]}
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": _auth_header()}

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{http_base}/db/{NEO4J_DATABASE}/tx/commit", json=body, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return ToolResult(content=f"Neo4j request failed: {exc}", summary=f"neo4j_search({query}) error", is_error=True)

    errors = data.get("errors") or []
    if errors:
        err = errors[0]
        return ToolResult(content=f"Neo4j error [{err.get('code')}]: {err.get('message')}", summary=f"neo4j_search({query}) error", is_error=True)

    results = data.get("results") or []
    if not results:
        return ToolResult(content="No results returned from the graph database.", summary=f"neo4j_search({query}) 0 rows")

    result = results[0]
    columns = result.get("columns") or []
    rows = [d.get("row") or [] for d in result.get("data") or []]
    output = format_results(columns, rows)
    return ToolResult(content=output, summary=f"neo4j_search({query}) {len(rows)} rows")


neo4j_search = tool(
    name="neo4j_search",
    description=(
        "Graph traversal over CVE/CWE/CAPEC/Technique/Tactic + product. Pass a question "
        "containing an ID or exact product name — it is matched automatically. Shows what "
        "the entity links to; a counting question ('how many CVEs…') returns stats by "
        "severity/KEV. No free text — use qdrant_search to find an ID first."
    ),
    input_schema=_INPUT_SCHEMA,
    scope="read",
    parallel=True,
    summary=lambda input: f"neo4j_search({input.get('query', '?')})",
)(_neo4j_search)
