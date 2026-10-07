"""BM25 lexical half of hybrid retrieval + Reciprocal Rank Fusion.

Qdrant sparse vector ``bm25`` (FastEmbed ``Qdrant/bm25`` + collection IDF modifier).
"""
from __future__ import annotations

import asyncio
import os
import threading
from collections import defaultdict
from typing import Any

import httpx

from ..config import get_settings

_settings = get_settings()
QDRANT_URL = _settings.qdrant_url.rstrip("/")
COLLECTION = _settings.qdrant_collection
BM25_VECTOR = _settings.qdrant_bm25_vector
BM25_MODEL = _settings.bm25_model
RRF_K = 60
BM25_FANOUT = 50

# Qdrant/bm25 is just stopword lists (no real ONNX weights); when huggingface.co is
# unreachable (e.g. IP-level rate limit) point fastembed at a local copy instead of
# retrying a doomed download on every single query.
_LOCAL_BM25_MODEL_DIR = "/home/ict/AIEngines/secai/secai-data/_state/bm25_local_model"

_bm25_lock = threading.Lock()
_bm25_state: dict[str, Any] = {"sparse_model": None}


def _load_sparse_model() -> Any:
    if _bm25_state.get("sparse_model") is not None:
        return _bm25_state["sparse_model"]
    with _bm25_lock:
        if _bm25_state.get("sparse_model") is not None:
            return _bm25_state["sparse_model"]
        from fastembed import SparseTextEmbedding

        kwargs: dict[str, Any] = {}
        if os.path.isdir(_LOCAL_BM25_MODEL_DIR):
            kwargs["specific_model_path"] = _LOCAL_BM25_MODEL_DIR
        model = SparseTextEmbedding(model_name=BM25_MODEL, **kwargs)
        _bm25_state["sparse_model"] = model
        return model


def _encode_query(query: str) -> dict[str, list] | None:
    text = (query or "").strip()
    if not text:
        return None
    model = _load_sparse_model()
    emb = next(iter(model.embed([text])))
    indices = [int(i) for i in emb.indices]
    values = [float(v) for v in emb.values]
    if not indices:
        return None
    return {"indices": indices, "values": values}


def _qdrant_sparse_search_sync(query: str, top_k: int, qfilter: dict | None = None) -> list[tuple[str, float]]:
    sparse = _encode_query(query)
    if not sparse or not BM25_VECTOR:
        return []
    body = {
        "vector": {"name": BM25_VECTOR, "vector": sparse},
        "limit": top_k,
        "with_payload": False,
    }
    if qfilter:
        body["filter"] = qfilter
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
            json=body,
        )
        resp.raise_for_status()
        return [
            (str(r["id"]), float(r["score"]))
            for r in resp.json().get("result", [])
            if r.get("score", 0) > 0
        ]


def _bm25_search_sync(query: str, top_k: int, qfilter: dict | None = None) -> list[tuple[str, float]]:
    if not BM25_VECTOR:
        return []
    try:
        return _qdrant_sparse_search_sync(query, top_k, qfilter)
    except Exception:
        return []


async def bm25_search(query: str, top_k: int, qfilter: dict | None = None) -> list[tuple[str, float]]:
    """``qfilter`` is applied inside Qdrant. Only the knowledge scope passes one: its documents are
    ~1% of the collection, so a collection-wide top-k followed by a post-filter would leave
    (almost) none of them. Other callers keep the old unfiltered fan-out + post-filter."""
    if qfilter:
        return await asyncio.to_thread(_bm25_search_sync, query, top_k, qfilter)
    return await asyncio.to_thread(_bm25_search_sync, query, top_k)


def rrf_merge(dense_ids: list[str], bm25_hits: list[tuple[str, float]], k: int) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion of the dense and BM25 rankings (matches the production
    hybrid sidecar: score += 1/(RRF_K + rank))."""
    scores: dict[str, float] = defaultdict(float)
    for rank, pid in enumerate(dense_ids):
        scores[pid] += 1.0 / (RRF_K + rank + 1)
    for rank, (pid, _) in enumerate(bm25_hits):
        scores[pid] += 1.0 / (RRF_K + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])[:k]
