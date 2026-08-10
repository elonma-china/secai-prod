"""HTTP clients for the bge-m3 embedding endpoint and the Qwen3 reranker (both
OpenAI-compatible). Rerank is best-effort — the caller falls back to RRF when the
reranker is unset or unreachable.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..config import get_settings

_s = get_settings()
EMBEDDING_URL = _s.embedding_api_url
RERANKER_URL = _s.reranker_api_url
EMBEDDING_MODEL = _s.embedding_model
RERANKER_MODEL = _s.reranker_model
EMBEDDING_API_KEY = _s.embedding_api_key
RERANKER_API_KEY = _s.reranker_api_key


async def embed(client: httpx.AsyncClient, query: str) -> list[float]:
    headers = {"Content-Type": "application/json"}
    if EMBEDDING_API_KEY:
        headers["Authorization"] = f"Bearer {EMBEDDING_API_KEY}"
    resp = await client.post(
        f"{EMBEDDING_URL}/v1/embeddings",
        json={"model": EMBEDDING_MODEL, "input": query}, headers=headers, timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


async def rerank(client: httpx.AsyncClient, query: str, docs: list[str], top_n: int) -> list[dict[str, Any]]:
    if not docs:
        return []
    headers = {"Content-Type": "application/json"}
    if RERANKER_API_KEY:
        headers["Authorization"] = f"Bearer {RERANKER_API_KEY}"
    resp = await client.post(
        f"{RERANKER_URL}/v1/rerank",
        json={"model": RERANKER_MODEL, "query": query, "documents": docs, "top_n": min(top_n, len(docs))},
        headers=headers, timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])
