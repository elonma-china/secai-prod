"""qdrant_search — hybrid (dense bge-m3 + BM25) retrieval over the ``cve_kb`` Qdrant
collection, RRF-fused and reranked, with exact entity-ID grounding.
"""
from __future__ import annotations

from typing import Any

import httpx
from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from ..config import get_settings
from .bm25 import BM25_FANOUT, BM25_VECTOR, bm25_search, rrf_merge
from .cve_overrides import apply_cve_override
from .embedding import EMBEDDING_URL, RERANKER_URL, embed, rerank
from .filters import ENTITY_ID_RES, boost, build_filter, passes_filter, upper_list

_settings = get_settings()
QDRANT_URL = _settings.qdrant_url
COLLECTION = _settings.qdrant_collection
CANDIDATE_LIMIT = 20
MAX_TEXT_CHARS = 800
DEFAULT_TOP_K = 5

# Schema kept deliberately small for a small (30B-A3B) model: it only sees `query`
# plus a few optional facet filters. CVE/CWE/CAPEC/ATT&CK IDs are auto-detected from
# the query text (see ENTITY_ID_RES), so the model never has to pass them. The extra
# range filters (cvss_min/max, year_from/to) still exist on the function for internal
# callers but are intentionally not advertised to the model.
_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "What to look up. CVE/CWE/CAPEC/ATT&CK IDs in the text are matched automatically."},
        "kind": {"type": "array", "items": {"type": "string", "enum": ["weakness", "technique", "pattern", "core"]},
                 "description": "Optional. weakness=CWE definition, technique=ATT&CK, pattern=CAPEC, core=CVE. Use weakness for 'what is <vuln>' concept questions."},
        "severity": {"type": "array", "items": {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
                     "description": "Optional severity filter."},
        "product": {"type": "string", "description": "Optional exact product name, e.g. 'linux_kernel'."},
        "kev_only": {"type": "boolean", "description": "Optional. true = only CVEs known to be actively exploited (CISA KEV)."},
    },
    "required": ["query"],
}


def _truncate(s: str) -> str:
    return s[:MAX_TEXT_CHARS] + "…" if len(s) > MAX_TEXT_CHARS else s


def _format_hit(index: int, score: float, payload: dict[str, Any], rerank_score: float | None) -> str:
    lines = [
        f"### Result {index + 1} (score: {score:.4f}" + (f", rerank: {rerank_score:.4f})" if rerank_score is not None else ")"),
        f"**Type:** {payload.get('kind') or payload.get('chunk_kind') or 'unknown'}",
    ]
    if payload.get("cve_id"):
        lines.append(f"**CVE:** {payload['cve_id']}")
    if payload.get("cwe_refs"):
        lines.append(f"**CWE:** {', '.join(payload['cwe_refs'])}")
    if payload.get("capec_entries"):
        entries = payload["capec_entries"]
        lines.append(f"**CAPEC:** {', '.join(entries[:5])}" + ("…" if len(entries) > 5 else ""))
    if payload.get("mitre_techniques"):
        lines.append(f"**Techniques:** {', '.join(payload['mitre_techniques'][:5])}")
    if payload.get("title"):
        lines.append(f"**Title:** {payload['title']}")
    if payload.get("severity"):
        lines.append(f"**Severity:** {payload['severity']}")
    if payload.get("cvss_score"):
        lines.append(f"**CVSS:** {payload['cvss_score']}")
    if payload.get("is_in_kev"):
        lines.append("**KEV:** yes")
    if payload.get("year"):
        lines.append(f"**Year:** {payload['year']}")
    if payload.get("products"):
        prods = payload["products"]
        lines.append(f"**Products:** {', '.join(prods[:5])}" + ("…" if len(prods) > 5 else ""))
    text = _truncate(str(payload.get("text") or ""))
    lines.append("")
    lines.append(text)
    return "\n".join(lines)


async def _dense_search(client: httpx.AsyncClient, vector: list[float], qfilter: dict[str, Any] | None, limit: int) -> list[dict[str, Any]]:
    body: dict[str, Any] = {"vector": {"name": "dense", "vector": vector}, "limit": limit, "with_payload": True}
    if qfilter:
        body["filter"] = qfilter
    resp = await client.post(f"{QDRANT_URL}/collections/{COLLECTION}/points/search", json=body, timeout=20)
    resp.raise_for_status()
    return [{"id": str(r["id"]), "score": r["score"],
             "payload": apply_cve_override(r.get("payload") or {})}
            for r in resp.json().get("result", [])]


async def _retrieve_by_ids(client: httpx.AsyncClient, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    resp = await client.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points",
        json={"ids": ids, "with_payload": True}, timeout=20,
    )
    resp.raise_for_status()
    return {str(r["id"]): apply_cve_override(r.get("payload") or {})
            for r in resp.json().get("result", [])}


async def _fetch_definition_nodes(client: httpx.AsyncClient, ids_by_field: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """Directly fetch the CWE/CAPEC/ATT&CK *definition* nodes for the named ids, matched
    on each node's own id field. Needed because a single definition node is easily crowded
    out of the dense/BM25 candidate pool by the thousands of CVEs that reference it."""
    should = [{"key": field, "match": {"any": vals}} for field, vals in ids_by_field.items() if vals]
    if not should:
        return {}
    resp = await client.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll",
        json={"filter": {"should": should}, "limit": 16, "with_payload": True, "with_vector": False},
        timeout=20,
    )
    resp.raise_for_status()
    return {str(p["id"]): apply_cve_override(p.get("payload") or {})
            for p in resp.json().get("result", {}).get("points", [])}


async def _qdrant_search(
    query: str,
    ctx: ToolContext,
    top_k: int = DEFAULT_TOP_K,
    kind: list[str] | None = None,
    cve_id: str | None = None,
    cwe_id: str | None = None,
    capec_id: str | None = None,
    technique_id: str | None = None,
    product: str | None = None,
    severity: list[str] | None = None,
    year: list[str] | None = None,
    year_from: str | None = None,
    year_to: str | None = None,
    cvss_min: float | None = None,
    cvss_max: float | None = None,
    kev_only: bool = False,
) -> ToolResult:
    # Exact-ID grounding: if the caller didn't pin an entity but the query names one
    # (or more), filter to those exact IDs so a bare "CVE-2025-0282" / "CWE-89" / "T1059"
    # can't be out-ranked by fuzzy same-token matches. Multiple IDs → match any.
    _locals = {"cve_id": cve_id, "cwe_id": cwe_id, "capec_id": capec_id, "technique_id": technique_id}
    for field, regex in ENTITY_ID_RES.items():
        if not _locals[field]:
            found = upper_list(regex.findall(query or ""))
            if found:
                _locals[field] = found
    cve_id, cwe_id, capec_id, technique_id = (
        _locals["cve_id"], _locals["cwe_id"], _locals["capec_id"], _locals["technique_id"],
    )

    # year is a keyword field (no Qdrant range), so expand an inclusive year span into
    # an explicit list of years and merge it with any exact years supplied.
    if (year_from or year_to) and not year:
        try:
            lo = int(year_from) if year_from else 1999
            hi = int(year_to) if year_to else 2035
            if lo <= hi:
                year = [str(y) for y in range(lo, hi + 1)]
        except ValueError:
            pass

    params = {
        "query": query, "top_k": top_k, "kind": kind, "cve_id": cve_id, "cwe_id": cwe_id,
        "capec_id": capec_id, "technique_id": technique_id, "product": product,
        "severity": severity, "year": year, "cvss_min": cvss_min, "cvss_max": cvss_max,
        "kev_only": kev_only,
    }
    top_k = min(top_k or DEFAULT_TOP_K, CANDIDATE_LIMIT)
    qfilter = build_filter(params)

    async with httpx.AsyncClient() as client:
        # 1. Dense retrieval (optional — skipped when no embedding endpoint is configured).
        dense_hits: list[dict[str, Any]] = []
        if EMBEDDING_URL:
            try:
                vector = await embed(client, query)
                dense_hits = await _dense_search(client, vector, qfilter, CANDIDATE_LIMIT)
            except Exception as exc:
                return ToolResult(content=f"KB dense search failed: {exc}", summary=f"qdrant_search({query}) error", is_error=True)

        # 2. BM25 lexical retrieval (fan out wider when filtering, then post-filter).
        try:
            bm25_raw = await bm25_search(query, BM25_FANOUT * 3 if qfilter else BM25_FANOUT)
        except Exception:
            bm25_raw = []
        bm25_used = bool(bm25_raw)

        payloads: dict[str, dict[str, Any]] = {h["id"]: h["payload"] for h in dense_hits}

        # 2b. Post-filter BM25 hits against the same predicate Qdrant applied to dense.
        bm25_hits: list[tuple[str, float]] = bm25_raw
        if qfilter and bm25_raw:
            need = [pid for pid, _ in bm25_raw if pid not in payloads]
            try:
                fetched = await _retrieve_by_ids(client, need)
            except Exception:
                fetched = {}
            payloads.update(fetched)
            bm25_hits = [
                (pid, score) for pid, score in bm25_raw
                if pid in payloads and passes_filter(payloads[pid], params)
            ][:BM25_FANOUT]

        if not dense_hits and not bm25_hits:
            note = "" if (EMBEDDING_URL or BM25_VECTOR) else " (no embedding / BM25 configured)"
            return ToolResult(content=f"Không tìm thấy kết quả trong knowledge base.{note}", summary=f"qdrant_search({query}) 0 hits")

        # 3. RRF fusion of the two rankings.
        merged = rrf_merge([h["id"] for h in dense_hits], bm25_hits, max(top_k * 4, CANDIDATE_LIMIT))

        # 4. Fetch payloads for any fused id we don't have yet (BM25-only hits).
        missing = [pid for pid, _ in merged if pid not in payloads]
        if missing:
            try:
                payloads.update(await _retrieve_by_ids(client, missing))
            except Exception:
                pass
        merged = [(pid, s) for pid, s in merged if pid in payloads]

        # 4b. Guarantee the named entity's own definition node is a candidate (see
        # _fetch_definition_nodes) — prepend it so it enters the rerank window and the
        # ×2 definition boost below can float it to the top.
        defn_ids_by_field = {
            "cwe_id": upper_list(cwe_id), "capec_id": upper_list(capec_id),
            "technique_id": upper_list(technique_id),
        }
        if any(defn_ids_by_field.values()):
            try:
                defn = await _fetch_definition_nodes(client, defn_ids_by_field)
            except Exception:
                defn = {}
            merged_ids = {pid for pid, _ in merged}
            for pid, pl in defn.items():
                if pid not in merged_ids:
                    payloads[pid] = pl
                    merged.insert(0, (pid, 1.0))

        # 5. Rerank the top fused candidates (best-effort; skipped without a reranker).
        rerank_scores: dict[str, float] = {}
        if RERANKER_URL and merged:
            try:
                candidates = merged[: min(20, len(merged))]
                docs = [str(payloads[pid].get("text") or "") for pid, _ in candidates]
                results = await rerank(client, query, docs, len(docs))
                for r in results:
                    idx = r.get("index")
                    if idx is not None and idx < len(candidates):
                        rerank_scores[candidates[idx][0]] = r.get("relevance_score", 0.0)
            except Exception:
                pass  # best-effort, same fallback as production

    # 6. Final score: rerank if available else RRF, then payload boost. When the query
    # names a CWE/CAPEC/ATT&CK entity, its own definition node should outrank the CVEs
    # that merely reference it ("CWE-89 là gì" wants the CWE definition first).
    pinned_ids = set(upper_list(cwe_id)) | set(upper_list(capec_id)) | set(upper_list(technique_id))

    def _is_pinned_definition(pl: dict[str, Any]) -> bool:
        if not pinned_ids:
            return False
        own = str(pl.get("cwe_id") or pl.get("capec_id") or pl.get("technique_id") or "").upper()
        return bool(own) and own in pinned_ids

    scored = []
    for pid, rrf in merged:
        reranked = rerank_scores.get(pid)
        base = reranked if reranked is not None else rrf
        score = boost(payloads[pid], base)
        if _is_pinned_definition(payloads[pid]):
            score *= 2.0
        scored.append({"point_id": pid, "score": score, "payload": payloads[pid], "rerank_score": reranked})
    scored.sort(key=lambda x: x["score"], reverse=True)
    final_hits = scored[:top_k]

    output = "\n\n---\n\n".join(_format_hit(i, h["score"], h["payload"], h["rerank_score"]) for i, h in enumerate(final_hits))
    mode = "hybrid" if (dense_hits and bm25_used) else ("dense" if dense_hits else "bm25")
    return ToolResult(content=output, summary=f"qdrant_search({query}) {len(final_hits)} hits [{mode}]")


qdrant_search = tool(
    name="qdrant_search",
    description=(
        "Search the internal CVE/CWE/CAPEC/ATT&CK knowledge base. Pass the user's question "
        "as `query`; any CVE/CWE/CAPEC/T-id in it is matched automatically. One call is "
        "usually enough."
    ),
    input_schema=_INPUT_SCHEMA,
    scope="read",
    parallel=True,
    summary=lambda input: f"qdrant_search({input.get('query', '?')[:50]})",
)(_qdrant_search)
