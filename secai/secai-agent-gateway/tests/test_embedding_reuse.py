"""Embedding/rerank client reuse and the prompt-vector cache (both off by default)."""
from __future__ import annotations

import asyncio

import httpx

from secai_gateway.retrieval import embedding as emb


class _S:
    def __init__(self, keepalive=False, ttl=0.0):
        self.embedding_keepalive_enabled = keepalive
        self.embedding_cache_ttl_s = ttl


def _handler(calls):
    def h(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})
    return h


import pytest


@pytest.fixture(autouse=True)
def _urls(monkeypatch):
    monkeypatch.setattr(emb, "EMBEDDING_URL", "http://emb.test")
    monkeypatch.setattr(emb, "RERANKER_URL", "http://rr.test")


def _run(coro_fn):
    return asyncio.run(coro_fn())


def test_default_uses_the_callers_client_and_never_caches(monkeypatch):
    monkeypatch.setattr(emb, "_s", _S())
    emb._cache.clear()
    mine, shared = [], []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(mine))) as c:
            emb._shared[id(asyncio.get_running_loop())] = httpx.AsyncClient(transport=httpx.MockTransport(_handler(shared)))
            await emb.embed(c, "q"); await emb.embed(c, "q"); await emb.rerank(c, "q", ["d"], 1)
    _run(go)
    assert len(mine) == 3 and shared == [] and not emb._cache


def test_keepalive_routes_through_the_shared_client(monkeypatch):
    monkeypatch.setattr(emb, "_s", _S(keepalive=True))
    mine, shared = [], []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(mine))) as c:
            emb._shared[id(asyncio.get_running_loop())] = httpx.AsyncClient(transport=httpx.MockTransport(_handler(shared)))
            await emb.embed(c, "q"); await emb.rerank(c, "q", ["d"], 1)
    _run(go)
    assert mine == [] and len(shared) == 2


def test_cache_serves_the_second_embedding_of_a_prompt(monkeypatch):
    monkeypatch.setattr(emb, "_s", _S(ttl=60))
    emb._cache.clear()
    calls = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(calls))) as c:
            a = await emb.embed(c, "same prompt"); a.append(9.9)  # callers may mutate their copy
            b = await emb.embed(c, "same prompt"); other = await emb.embed(c, "another prompt")
            return a, b, other
    a, b, other = _run(go)
    assert len(calls) == 2 and b == [0.1, 0.2] and other == [0.1, 0.2]


def test_cache_entry_expires(monkeypatch):
    monkeypatch.setattr(emb, "_s", _S(ttl=0.001))
    emb._cache.clear()
    calls = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(calls))) as c:
            await emb.embed(c, "q"); await asyncio.sleep(0.01); await emb.embed(c, "q")
    _run(go)
    assert len(calls) == 2
