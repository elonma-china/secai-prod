"""howto_search — hybrid (dense + BM25) retrieval over the ``ias_howto_kb`` Qdrant
collection: the IAS product user-guide KB, separate from ``cve_kb_hybrid``. No
entity-ID grounding, no CVE dedup, no Neo4j graph-fusion — the manual has none of
those concepts, this is plain semantic+lexical search over doc chunks.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from ..config import get_settings
from .bm25 import BM25_VECTOR, _encode_query, rrf_merge
from .embedding import EMBEDDING_URL, RERANKER_URL, embed, rerank

_settings = get_settings()
QDRANT_URL = _settings.qdrant_url.rstrip("/")
COLLECTION = _settings.qdrant_howto_collection
CANDIDATE_LIMIT = 20
MAX_TEXT_CHARS = 1200
DEFAULT_TOP_K = 5

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Câu hỏi về cách dùng/thao tác/chức năng phần mềm IAS."},
        "top_k": {"type": "integer", "description": "Optional. Số kết quả tối đa (mặc định 5)."},
    },
    "required": ["query"],
}


def _format_hit(index: int, score: float, payload: dict[str, Any]) -> str:
    chuong = payload.get("chuong") or ""
    tieu_de = payload.get("tieu_de")
    label = f"{chuong} > {tieu_de}" if tieu_de else chuong
    text = str(payload.get("text") or "")
    return f"### Result {index + 1} (score: {score:.4f})\n**Mục:** {label}\n\n{text}"


async def _dense_search(client: httpx.AsyncClient, vector: list[float], limit: int) -> list[dict[str, Any]]:
    body = {"vector": {"name": "dense", "vector": vector}, "limit": limit, "with_payload": True}
    resp = await client.post(f"{QDRANT_URL}/collections/{COLLECTION}/points/search", json=body, timeout=20)
    resp.raise_for_status()
    return [{"id": str(r["id"]), "score": r["score"], "payload": r.get("payload") or {}}
            for r in resp.json().get("result", [])]


async def _retrieve_by_ids(client: httpx.AsyncClient, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    # Point IDs in this collection are plain unsigned ints (see ingest.py) — Qdrant rejects
    # a stringified int ("1") as "not a valid point ID", only a real JSON int or a UUID
    # string. ids here are always our own str(int) dict keys, so cast back unconditionally.
    resp = await client.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points",
        json={"ids": [int(i) for i in ids], "with_payload": True}, timeout=20,
    )
    resp.raise_for_status()
    return {str(r["id"]): (r.get("payload") or {}) for r in resp.json().get("result", [])}


def _sparse_search_sync(query: str, top_k: int) -> list[tuple[str, float]]:
    if not BM25_VECTOR:
        return []
    sparse = _encode_query(query)
    if not sparse:
        return []
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
            json={"vector": {"name": BM25_VECTOR, "vector": sparse}, "limit": top_k, "with_payload": False},
        )
        resp.raise_for_status()
        return [(str(r["id"]), float(r["score"])) for r in resp.json().get("result", []) if r.get("score", 0) > 0]


async def _bm25_search(query: str, top_k: int) -> list[tuple[str, float]]:
    try:
        return await asyncio.to_thread(_sparse_search_sync, query, top_k)
    except Exception:
        return []


async def _howto_search(query: str, ctx: ToolContext, top_k: int = DEFAULT_TOP_K) -> ToolResult:
    top_k = min(top_k or DEFAULT_TOP_K, CANDIDATE_LIMIT)

    async with httpx.AsyncClient() as client:
        async def _dense_path() -> list[dict[str, Any]]:
            if not EMBEDDING_URL:
                return []
            vector = await embed(client, query)
            return await _dense_search(client, vector, CANDIDATE_LIMIT)

        dense_res, bm25_res = await asyncio.gather(
            _dense_path(), _bm25_search(query, CANDIDATE_LIMIT), return_exceptions=True)
        if isinstance(dense_res, Exception):
            return ToolResult(content=f"Howto KB dense search failed: {dense_res}",
                              summary=f"howto_search({query}) error", is_error=True)
        dense_hits: list[dict[str, Any]] = dense_res
        bm25_hits: list[tuple[str, float]] = [] if isinstance(bm25_res, Exception) else bm25_res
        bm25_used = bool(bm25_hits)

        payloads: dict[str, dict[str, Any]] = {h["id"]: h["payload"] for h in dense_hits}
        if not dense_hits and not bm25_hits:
            note = "" if (EMBEDDING_URL or BM25_VECTOR) else " (no embedding / BM25 configured)"
            return ToolResult(content=f"Không tìm thấy kết quả trong KB hướng dẫn sử dụng.{note}",
                              summary=f"howto_search({query}) 0 hits")

        merged = rrf_merge([h["id"] for h in dense_hits], bm25_hits, max(top_k * 4, CANDIDATE_LIMIT))
        missing = [pid for pid, _ in merged if pid not in payloads]
        if missing:
            try:
                payloads.update(await _retrieve_by_ids(client, missing))
            except Exception:
                pass
        merged = [(pid, s) for pid, s in merged if pid in payloads]

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
                pass

    scored = [
        {"id": pid, "score": rerank_scores.get(pid, rrf), "payload": payloads[pid]}
        for pid, rrf in merged
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    final_hits = scored[:top_k]

    output = "\n\n---\n\n".join(_format_hit(i, h["score"], h["payload"]) for i, h in enumerate(final_hits))
    mode = "hybrid" if (dense_hits and bm25_used) else ("dense" if dense_hits else "bm25")
    return ToolResult(content=output, summary=f"howto_search({query}) {len(final_hits)} hits [{mode}]")


howto_search = tool(
    name="howto_search",
    description=(
        "Search the IAS product user-guide knowledge base (hướng dẫn sử dụng phần mềm IAS: "
        "các phân hệ, màn hình, thao tác, chức năng). Dùng cho câu hỏi về CÁCH DÙNG sản phẩm, "
        "KHÔNG dùng cho câu hỏi về CVE/CWE/lỗ hổng kỹ thuật (dùng qdrant_search cho việc đó)."
    ),
    input_schema=_INPUT_SCHEMA,
    scope="read",
    parallel=True,
    summary=lambda input: f"howto_search({input.get('query', '?')[:50]})",
)(_howto_search)
