"""BM25 lexical half of hybrid retrieval + Reciprocal Rank Fusion.

bm25s artifacts are cloned from production; ``doc_ids.json`` is a list of Qdrant point
UUIDs — the join key between bm25 rows and Qdrant points. RRF fuses the dense and BM25
rankings with the same k as the production hybrid sidecar.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from collections import defaultdict
from typing import Any

from ..config import get_settings

BM25_DIR = get_settings().bm25_dir
RRF_K = 60
BM25_FANOUT = 50

_bm25_lock = threading.Lock()
_bm25_state: dict[str, Any] = {"loaded": False, "retriever": None, "doc_ids": None}


def _load_bm25() -> tuple[Any, list[str] | None]:
    """Lazy-load the bm25s index + doc_ids once per process. Returns (None, None)
    when BM25_DIR is unset or the artifacts are missing (dense-only fallback)."""
    if _bm25_state["loaded"]:
        return _bm25_state["retriever"], _bm25_state["doc_ids"]
    with _bm25_lock:
        if _bm25_state["loaded"]:
            return _bm25_state["retriever"], _bm25_state["doc_ids"]
        retriever = doc_ids = None
        try:
            if BM25_DIR and os.path.isdir(BM25_DIR):
                import bm25s

                retriever = bm25s.BM25.load(BM25_DIR, load_corpus=False)
                with open(os.path.join(BM25_DIR, "doc_ids.json"), encoding="utf-8") as fh:
                    doc_ids = json.load(fh)
        except Exception:
            retriever = doc_ids = None
        _bm25_state.update(loaded=True, retriever=retriever, doc_ids=doc_ids)
        return retriever, doc_ids


def _bm25_search_sync(query: str, top_k: int) -> list[tuple[str, float]]:
    retriever, doc_ids = _load_bm25()
    if retriever is None or not doc_ids:
        return []
    try:
        import bm25s

        tokenized = bm25s.tokenize([query], stopwords="en", show_progress=False)
        k = min(top_k, len(doc_ids))
        results, scores = retriever.retrieve(tokenized, k=k, show_progress=False)
        hits: list[tuple[str, float]] = []
        for idx, score in zip(results[0], scores[0]):
            idx, score = int(idx), float(score)
            if 0 <= idx < len(doc_ids) and score > 0:
                hits.append((doc_ids[idx], score))
        return hits
    except Exception:
        return []


async def bm25_search(query: str, top_k: int) -> list[tuple[str, float]]:
    # bm25s retrieve is sync CPU work — off-load so it doesn't block the event loop.
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
