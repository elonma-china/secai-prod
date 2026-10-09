"""HTTP clients for the bge-m3 embedding endpoint and the Qwen3 reranker (both
OpenAI-compatible). Rerank is best-effort — the caller falls back to RRF when the
reranker is unset or unreachable.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
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


# Serving latency (measured 2026-10-07): the serving host is ~240 ms away. A fresh connection per search costs a
# TCP handshake on top of the request (embed 763 ms, rerank 553 ms vs 267 / 290 ms on a warm connection), and the
# input classifier embeds the same prompt that the pre-fetch embeds a moment later. Both are off by default
# (`embedding_keepalive_enabled`, `embedding_cache_ttl_s`): off = one new connection and one request per call, as before.
_shared: dict[int, httpx.AsyncClient] = {}
_cache: OrderedDict[tuple[str, str], tuple[float, list[float]]] = OrderedDict()


def _keepalive() -> bool:
    return bool(getattr(_s, "embedding_keepalive_enabled", False))


def _shared_client() -> httpx.AsyncClient:
    """One keep-alive client per event loop (a client is bound to the loop it was created in)."""
    key = id(asyncio.get_running_loop())
    cli = _shared.get(key)
    if cli is None or cli.is_closed:
        cli = _shared[key] = httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=16, keepalive_expiry=45))
    return cli


async def _post(client: httpx.AsyncClient, url: str, **kw: Any) -> httpx.Response:
    if not _keepalive():
        return await client.post(url, **kw)
    try:
        return await _shared_client().post(url, **kw)
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError):  # the peer closed an idle connection
        return await _shared_client().post(url, **kw)


async def embed(client: httpx.AsyncClient, query: str) -> list[float]:
    ttl = float(getattr(_s, "embedding_cache_ttl_s", 0.0) or 0.0)
    key = (EMBEDDING_MODEL, query)
    if ttl > 0:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            _cache.move_to_end(key)
            return list(hit[1])
    headers = {"Content-Type": "application/json"}
    if EMBEDDING_API_KEY:
        headers["Authorization"] = f"Bearer {EMBEDDING_API_KEY}"
    resp = await _post(
        client, f"{EMBEDDING_URL}/v1/embeddings",
        json={"model": EMBEDDING_MODEL, "input": query}, headers=headers, timeout=20,
    )
    resp.raise_for_status()
    vec = resp.json()["data"][0]["embedding"]
    if ttl > 0:
        _cache[key] = (time.monotonic(), list(vec))
        while len(_cache) > 256:
            _cache.popitem(last=False)
    return vec


async def rerank(client: httpx.AsyncClient, query: str, docs: list[str], top_n: int) -> list[dict[str, Any]]:
    if not docs:
        return []
    headers = {"Content-Type": "application/json"}
    if RERANKER_API_KEY:
        headers["Authorization"] = f"Bearer {RERANKER_API_KEY}"
    resp = await _post(
        client, f"{RERANKER_URL}/v1/rerank",
        json={"model": RERANKER_MODEL, "query": query, "documents": docs, "top_n": min(top_n, len(docs))},
        headers=headers, timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])
