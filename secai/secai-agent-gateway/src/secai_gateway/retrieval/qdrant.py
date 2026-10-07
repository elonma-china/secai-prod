"""qdrant_search — hybrid (dense bge-m3 + BM25) retrieval over the ``cve_kb`` Qdrant
collection, RRF-fused and reranked, with exact entity-ID grounding.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any

import httpx
from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from ..config import get_settings
from .bm25 import BM25_FANOUT, BM25_VECTOR, bm25_search, rrf_merge
from .cve_overrides import apply_cve_override
from .embedding import EMBEDDING_URL, RERANKER_URL, embed, rerank
from .vi_en import expand_for_bm25
from .neo4j import NEO4J_DATABASE, _auth_header, _neo4j_http_url
from .filters import (ENTITY_ID_RES, KNOWLEDGE_KINDS, boost, build_filter, entity_scope_filter,
                      passes_filter, resolve_scope, scope_ok, upper_list)

logger = logging.getLogger(__name__)

_settings = get_settings()
QDRANT_URL = _settings.qdrant_url
COLLECTION = _settings.qdrant_collection
CANDIDATE_LIMIT = 20
MAX_TEXT_CHARS = 800
KNOWLEDGE_TEXT_CHARS = 1400  # knowledge chunks are written to fit one embedding window (~1.2k chars)
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


def _truncate(s: str, limit: int = MAX_TEXT_CHARS) -> str:
    return s[:limit] + "…" if len(s) > limit else s


def _format_knowledge_hit(index: int, score: float, payload: dict[str, Any], rerank_score: float | None) -> str:
    """Cheat sheet / ASVS / mitigation chunk: the model must be able to cite source + licence."""
    lines = [
        f"### Result {index + 1} (score: {score:.4f}" + (f", rerank: {rerank_score:.4f})" if rerank_score is not None else ")"),
        f"**Type:** {payload.get('kind')} (knowledge)",
        f"**Title:** {payload.get('title') or payload.get('name') or ''}",
        f"**Source:** {payload.get('source', '')} {payload.get('version', '')}".rstrip(),
    ]
    if payload.get("doc_id"):
        lines.append(f"**Ref:** {payload['doc_id']}")
    if payload.get("source_url"):
        lines.append(f"**URL:** {payload['source_url']}")
    if payload.get("attribution"):
        lines.append(f"**License:** {payload.get('license', '')} — {payload['attribution']}")
    for label, key in (("CWE", "cwe_refs"), ("CAPEC", "capec_entries"), ("Techniques", "mitre_techniques")):
        if payload.get(key):
            lines.append(f"**{label}:** {', '.join(payload[key][:5])}")
    lines += ["", _truncate(str(payload.get("text") or ""), KNOWLEDGE_TEXT_CHARS)]
    return "\n".join(lines)


def _format_hit(index: int, score: float, payload: dict[str, Any], rerank_score: float | None) -> str:
    if (payload.get("kind") or payload.get("chunk_kind")) in KNOWLEDGE_KINDS:
        return _format_knowledge_hit(index, score, payload, rerank_score)
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
    if payload.get("title") or payload.get("name"):
        lines.append(f"**Title:** {payload.get('title') or payload.get('name')}")
    if payload.get("severity"):
        lines.append(f"**Severity:** {payload['severity']}")
    if payload.get("cvss_score"):
        lines.append(f"**CVSS:** {payload['cvss_score']}")
    if payload.get("is_in_kev") is not None:
        # Bug (found 2026-09-18): `if payload.get("is_in_kev"):` silently dropped the
        # line whenever the value was False, making "not in KEV" indistinguishable from
        # "KEV status unknown" in the LLM's context — the model then guessed "yes" for
        # KEV questions regardless of the real answer (verified: CVE-2022-29353,
        # is_in_kev=False, model answered "yes"). Must render the negative explicitly.
        lines.append(f"**KEV:** {'yes' if payload['is_in_kev'] else 'no'}")
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


async def _scroll_by_filter(client: httpx.AsyncClient, qfilter: dict[str, Any] | None,
                            limit: int) -> list[tuple[str, dict[str, Any]]]:
    """Fetch payloads matching ``qfilter`` directly (no vector, no rerank) — the exact-lookup
    fast path. Returns ``[(point_id, payload)]`` in scroll order."""
    if not qfilter:
        return []
    resp = await client.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll",
        json={"filter": qfilter, "limit": limit, "with_payload": True, "with_vector": False},
        timeout=20,
    )
    resp.raise_for_status()
    return [(str(p["id"]), apply_cve_override(p.get("payload") or {}))
            for p in resp.json().get("result", {}).get("points", [])]


async def _fetch_definition_nodes(client: httpx.AsyncClient, ids_by_field: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """Directly fetch the CWE/CAPEC/ATT&CK *definition* nodes for the named ids, matched
    on each node's own id field. Needed because a single definition node is easily crowded
    out of the dense/BM25 candidate pool by the thousands of CVEs that reference it."""
    should = [{"key": field, "match": {"any": vals}} for field, vals in ids_by_field.items() if vals]
    if not should:
        return {}
    resp = await client.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll",
        # must_not: knowledge chunks carry cwe_id/capec_id/technique_id too, but are not definitions.
        json={"filter": {"should": should, **entity_scope_filter()}, "limit": 16, "with_payload": True, "with_vector": False},
        timeout=20,
    )
    resp.raise_for_status()
    return {str(p["id"]): apply_cve_override(p.get("payload") or {})
            for p in resp.json().get("result", {}).get("points", [])}


async def _graph_reverse_candidates(
    client: httpx.AsyncClient, ids_by_field: dict[str, list[str]], limit: int = 15,
) -> dict[str, dict[str, Any]]:
    """Graph-augmented fusion. For a *class* query naming a CWE/CAPEC/ATT&CK id, pull the
    CVEs the knowledge graph links to that class (KEV-first, then CVSS-desc) and fetch their
    core chunks so famous class members enter the rerank window. Pure semantic search buries
    these — a broad weakness class has no distinctive description text. Fail-open: any
    Neo4j/Qdrant error returns {} and the hybrid pipeline stands alone."""
    rel_by_field = {
        "cwe_id": ("CWE", "HAS_WEAKNESS"),
        "capec_id": ("CAPEC", "MAPS_TO_CAPEC"),
        "technique_id": ("Technique", "EXPLOITS_TECHNIQUE"),
    }
    cve_ids: list[str] = []
    for field, (label, rel) in rel_by_field.items():
        vals = ids_by_field.get(field) or []
        if not vals:
            continue
        cypher = (
            f"MATCH (c:CVE)-[:{rel}]->(n:{label}) WHERE n.id IN $ids "
            "RETURN c.id AS id "
            "ORDER BY coalesce(c.is_in_kev,false) DESC, coalesce(c.cvss_score,0.0) DESC "
            "LIMIT $n"
        )
        try:
            resp = await client.post(
                f"{_neo4j_http_url()}/db/{NEO4J_DATABASE}/tx/commit",
                json={"statements": [{"statement": cypher, "parameters": {"ids": vals, "n": limit}}]},
                headers={"Accept": "application/json", "Content-Type": "application/json",
                         "Authorization": _auth_header()},
                timeout=10,
            )
            resp.raise_for_status()
            for row in ((resp.json().get("results") or [{}])[0].get("data") or []):
                r = row.get("row") or []
                if r and r[0]:
                    cve_ids.append(str(r[0]).upper())
        except Exception:
            continue
    if not cve_ids:
        return {}
    seen: set[str] = set()
    ordered = [c for c in cve_ids if not (c in seen or seen.add(c))]
    try:
        resp = await client.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll",
            json={"filter": {"must": [
                {"key": "cve_id", "match": {"any": ordered}},
                {"key": "chunk_kind", "match": {"value": "core"}},
            ]}, "limit": len(ordered) + 5, "with_payload": True, "with_vector": False},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception:
        return {}
    return {str(pt["id"]): apply_cve_override(pt.get("payload") or {})
            for pt in resp.json().get("result", {}).get("points", [])}


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
    scope: str | None = None,
) -> ToolResult:
    # Exact-ID grounding: if the caller didn't pin an entity but the query names one
    # (or more), filter to those exact IDs so a bare "CVE-2025-0282" / "CWE-89" / "T1059"
    # can't be out-ranked by fuzzy same-token matches. Multiple IDs → match any.
    scope = resolve_scope(scope, kind)
    _locals = {"cve_id": cve_id, "cwe_id": cwe_id, "capec_id": capec_id, "technique_id": technique_id}
    for field, regex in ENTITY_ID_RES.items():
        if not _locals[field]:
            found = upper_list(regex.findall(query or ""))
            if found:
                _locals[field] = found
    cve_id, cwe_id, capec_id, technique_id = (
        _locals["cve_id"], _locals["cwe_id"], _locals["capec_id"], _locals["technique_id"],
    )
    kn_ids: dict[str, list[str]] = {}
    if scope == "knowledge":
        # Knowledge chunks never carry a cve_id, and only ~1% carry a cwe/capec/technique id, so
        # hard-filtering on a detected id returns nothing ("how do I fix CVE-X"). The semantic pass
        # stays unpinned; CWE/CAPEC/ATT&CK ids only add a second, pinned pass (their mitigations).
        cve_id = None
        kn_ids = {f: upper_list(v) for f, v in (("cwe_id", cwe_id), ("capec_id", capec_id), ("technique_id", technique_id)) if v}
        cwe_id = capec_id = technique_id = None

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
        "kev_only": kev_only, "scope": scope,
    }
    t_start = time.monotonic()
    params["scope"] = scope
    top_k = min(top_k or DEFAULT_TOP_K, CANDIDATE_LIMIT)
    qfilter = build_filter(params)
    # What the CALLER asked to filter on, scope aside: only that decides whether BM25 hits need
    # the extra payload fetch + wider fan-out (scope alone is enforced after the normal fetch below).
    user_filtered = bool(build_filter({**params, "scope": "all"}))
    scope_filter = build_filter({"scope": scope})  # scope clause alone: applied inside Qdrant on the BM25 leg too
    ids_filter = build_filter({"scope": "knowledge", **kn_ids}) if kn_ids else None

    # EXACT-CVE FAST PATH. A pinned cve_id makes the filter a hard `must` on that exact
    # record, so the full pipeline can only ever return that CVE's own chunk(s) anyway —
    # the dense embedding + rerank (two network round-trips, ~1.5s to a remote serving)
    # merely reorder a handful of same-CVE docs, pure waste. Fetch them directly via a
    # filtered scroll (~40ms). Scoped to cve_id ONLY: a cwe/capec/technique filter matches
    # the definition node AND the thousands of CVEs referencing it, where semantic ranking
    # genuinely decides which to surface — those keep the full hybrid pipeline below.
    # Fail-open: any scroll error falls through to the hybrid path.
    if cve_id and qfilter:
        try:
            async with httpx.AsyncClient() as client:
                rows = await _scroll_by_filter(client, qfilter, max(top_k, 8))
            if rows:
                final = rows[:top_k]
                output = "\n\n---\n\n".join(
                    _format_hit(i, boost(pl, 1.0), pl, None) for i, (_pid, pl) in enumerate(final))
                return ToolResult(content=output,
                                  summary=f"qdrant_search({query}) {len(final)} hits [cve-exact]")
        except Exception:
            pass  # fall through to the full hybrid pipeline

    async with httpx.AsyncClient() as client:
        # 1+2. Dense (embed→search, network) and BM25 (local sparse + Qdrant) are independent
        # until RRF fusion — run them CONCURRENTLY so the total is max(dense, bm25), not the
        # sum. Dense is optional (skipped when no embedding endpoint is configured).
        async def _dense_path() -> list[dict[str, Any]]:
            if not EMBEDDING_URL:
                return []
            vector = await embed(client, query)
            main = await _dense_search(client, vector, qfilter, CANDIDATE_LIMIT)
            if ids_filter:
                pinned = await _dense_search(client, vector, ids_filter, 4)
                seen = {h["id"] for h in pinned}
                return pinned + [h for h in main if h["id"] not in seen]
            return main

        async def _bm25_path() -> list[tuple[str, float]]:
            # Entity scope: the scope's must_not runs inside Qdrant, so knowledge chunks cannot crowd
            # the top-k and a CVE query keeps exactly the candidates it had before knowledge existed.
            # Knowledge scope: Vietnamese words are given English terms (the chunks are English).
            bq = expand_for_bm25(query) if scope == "knowledge" else query
            if not scope_filter:
                return await bm25_search(bq, bm25_fanout)
            hits = await bm25_search(bq, bm25_fanout, scope_filter)
            if ids_filter:
                pinned = await bm25_search(bq, 4, ids_filter)
                seen = {pid for pid, _ in pinned}
                hits = pinned + [h for h in hits if h[0] not in seen]
            return hits

        bm25_fanout = BM25_FANOUT * 3 if user_filtered else BM25_FANOUT
        dense_res, bm25_res = await asyncio.gather(_dense_path(), _bm25_path(), return_exceptions=True)
        if isinstance(dense_res, Exception):
            return ToolResult(content=f"KB dense search failed: {dense_res}",
                              summary=f"qdrant_search({query}) error", is_error=True)
        dense_hits: list[dict[str, Any]] = dense_res
        bm25_raw = [] if isinstance(bm25_res, Exception) else bm25_res
        bm25_used = bool(bm25_raw)

        payloads: dict[str, dict[str, Any]] = {h["id"]: h["payload"] for h in dense_hits}

        # 2b. Post-filter BM25 hits against the same predicate Qdrant applied to dense.
        bm25_hits: list[tuple[str, float]] = bm25_raw
        if user_filtered and bm25_raw:
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

        top_dense = max((h["score"] for h in dense_hits), default=0.0)
        if (scope == "knowledge" and dense_hits and _settings.knowledge_min_dense > 0
                and top_dense < _settings.knowledge_min_dense):
            # Nothing in the knowledge base is semantically close: better no reference than a random one.
            logger.info("qdrant_search scope=knowledge gated top_dense=%.3f < %.3f", top_dense, _settings.knowledge_min_dense)
            dense_hits, bm25_hits = [], []
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
        merged = [(pid, s) for pid, s in merged if pid in payloads and scope_ok(payloads[pid], scope)]

        # 4b. Guarantee the named entity's own definition node is a candidate (see
        # _fetch_definition_nodes) — prepend it so it enters the rerank window and the
        # ×2 definition boost below can float it to the top.
        defn_ids_by_field = {
            "cwe_id": upper_list(cwe_id), "capec_id": upper_list(capec_id),
            "technique_id": upper_list(technique_id),
        }
        entity_pinned = scope == "entity" and any(defn_ids_by_field.values())
        if entity_pinned:
            try:
                defn = await _fetch_definition_nodes(client, defn_ids_by_field)
            except Exception:
                defn = {}
            merged_ids = {pid for pid, _ in merged}
            for pid, pl in defn.items():
                if pid not in merged_ids:
                    payloads[pid] = pl
                    merged.insert(0, (pid, 1.0))

        # 4c. Graph-augmented fusion: bring the graph's CVEs for a named class (CWE/CAPEC/
        # ATT&CK), KEV/CVSS-ranked, into the candidate pool so they reach rerank. Only fires
        # for class queries — leaves alias/description/product/exact-id retrieval untouched.
        if entity_pinned:
            try:
                graph_cands = await _graph_reverse_candidates(client, defn_ids_by_field)
            except Exception:
                graph_cands = {}
            existing = {pid for pid, _ in merged}
            add: list[tuple[str, float]] = []
            for pid, pl in graph_cands.items():
                if pid not in existing:
                    payloads[pid] = pl
                    add.append((pid, 0.9))
                    existing.add(pid)
            if add:
                merged = add + merged

        # 5. Rerank the top fused candidates (best-effort; skipped without a reranker).
        rerank_scores: dict[str, float] = {}
        if RERANKER_URL and merged:
            try:
                candidates = merged[: min(10 if scope == "knowledge" else 20, len(merged))]
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
        if not pinned_ids or (pl.get("kind") or pl.get("chunk_kind")) in KNOWLEDGE_KINDS:
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
    # #2 dedup theo CVE: giữ chunk điểm cao nhất mỗi CVE → top_k = CVE riêng biệt (tránh
    # nhiều chunk cùng 1 CVE chiếm slot). Node định nghĩa (CWE/CAPEC/T) không có cve_id → giữ.
    deduped: list[dict[str, Any]] = []
    seen_cve: set[str] = set()
    per_parent: dict[str, int] = {}
    for h in scored:
        if scope == "knowledge":
            # At most 2 chunks per source document so one cheat sheet cannot fill every slot.
            parent = str(h["payload"].get("parent_id") or h["payload"].get("doc_id") or h["point_id"])
            per_parent[parent] = per_parent.get(parent, 0) + 1
            if per_parent[parent] > 2:
                continue
        cve = str(h["payload"].get("cve_id") or "").upper()
        if cve:
            if cve in seen_cve:
                continue
            seen_cve.add(cve)
        deduped.append(h)
    if scope == "knowledge" and _settings.knowledge_min_rerank > 0:
        # Only meaningful when a reranker scored the hit; without one we keep the RRF order.
        deduped = [h for h in deduped if h["rerank_score"] is None or h["rerank_score"] >= _settings.knowledge_min_rerank]
    final_hits = deduped[:top_k]
    if not final_hits:  # everything was scoped out / cut off: say so instead of returning an empty tool result
        return ToolResult(content="Không tìm thấy kết quả trong knowledge base.", summary=f"qdrant_search({query}) 0 hits")

    output = "\n\n---\n\n".join(_format_hit(i, h["score"], h["payload"], h["rerank_score"]) for i, h in enumerate(final_hits))
    mode = "hybrid" if (dense_hits and bm25_used) else ("dense" if dense_hits else "bm25")
    logger.info(
        "qdrant_search scope=%s mode=%s top_dense=%.3f hits=%d kinds=%s sensitivity=%s ms=%d",
        scope, mode, top_dense, len(final_hits),
        dict(Counter(str(h["payload"].get("kind") or h["payload"].get("chunk_kind")) for h in final_hits)),
        dict(Counter(str(h["payload"].get("sensitivity", "-")) for h in final_hits)),
        int((time.monotonic() - t_start) * 1000))
    return ToolResult(content=output, summary=f"qdrant_search({query}) {len(final_hits)} hits [{mode}]")


async def _qdrant_search_tool(
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
    """What the model can call. It has no `scope` parameter (linch drops unknown keys) and cannot ask
    for knowledge kinds: the knowledge KB reaches the model only through the server-side pre-fetch."""
    entity_kinds = [k for k in (kind or []) if k not in KNOWLEDGE_KINDS] or None
    return await _qdrant_search(
        query, ctx, top_k=top_k, kind=entity_kinds, cve_id=cve_id, cwe_id=cwe_id, capec_id=capec_id,
        technique_id=technique_id, product=product, severity=severity, year=year, year_from=year_from,
        year_to=year_to, cvss_min=cvss_min, cvss_max=cvss_max, kev_only=kev_only, scope="entity")


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
)(_qdrant_search_tool)
