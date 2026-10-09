"""qdrant_search CVE-exact fast path — a pinned cve_id must skip embed/dense/bm25/rerank
(two network round-trips) and fetch the record directly via a filtered scroll (~40ms vs
~1.5s). A cwe/capec/technique query must keep the full hybrid pipeline (its filter matches
the definition node PLUS the many CVEs referencing it, where semantic ranking matters)."""
from __future__ import annotations

import asyncio

import pytest

from secai_gateway.retrieval import qdrant


class _Ctx:
    pass


async def _boom(*a, **k):  # any hybrid-path call is a routing bug on the fast path
    raise AssertionError("hybrid retrieval path ran on a CVE-exact query")


def test_cve_id_query_uses_scroll_fastpath(monkeypatch) -> None:
    seen = {}

    async def fake_scroll(client, qfilter, limit):
        seen["qfilter"] = qfilter
        return [("pt1", {"cve_id": "CVE-2021-44228", "text": "Log4Shell RCE", "kind": "cve"})]

    monkeypatch.setattr(qdrant, "_scroll_by_filter", fake_scroll)
    monkeypatch.setattr(qdrant, "embed", _boom)
    monkeypatch.setattr(qdrant, "_dense_search", _boom)
    monkeypatch.setattr(qdrant, "rerank", _boom)

    r = asyncio.run(qdrant._qdrant_search("CVE-2021-44228", _Ctx()))
    assert "[cve-exact]" in r.summary
    assert seen.get("qfilter") is not None  # scroll actually ran with the pinned filter
    assert "CVE-2021-44228" in (r.content or "")


def test_cve_id_with_freetext_still_fastpaths(monkeypatch) -> None:
    # A pinned cve_id narrows the filter to that exact record regardless of surrounding
    # words, so trailing free text ("là gì") must not force the slow hybrid path.
    async def fake_scroll(client, qfilter, limit):
        return [("pt1", {"cve_id": "CVE-2020-0796", "text": "SMBGhost", "kind": "cve"})]

    monkeypatch.setattr(qdrant, "_scroll_by_filter", fake_scroll)
    monkeypatch.setattr(qdrant, "embed", _boom)

    r = asyncio.run(qdrant._qdrant_search("PoC cho CVE-2020-0796 là gì", _Ctx()))
    assert "[cve-exact]" in r.summary


def test_scroll_error_falls_back_to_hybrid(monkeypatch) -> None:
    # Fail-open: a scroll error must not sink the query — it falls through to hybrid.
    async def broken_scroll(client, qfilter, limit):
        raise RuntimeError("qdrant scroll down")

    monkeypatch.setattr(qdrant, "_scroll_by_filter", broken_scroll)
    monkeypatch.setattr(qdrant, "EMBEDDING_URL", "")  # dense skipped

    async def empty_bm25(q, k, qfilter=None):
        return []

    monkeypatch.setattr(qdrant, "bm25_search", empty_bm25)
    r = asyncio.run(qdrant._qdrant_search("CVE-2021-44228", _Ctx()))
    # hybrid ran and found nothing (no embed, empty bm25) — NOT a cve-exact result, no crash
    assert "[cve-exact]" not in r.summary


def test_cwe_query_does_not_use_fastpath(monkeypatch) -> None:
    async def boom_scroll(*a, **k):
        raise AssertionError("a CWE query must not take the CVE-exact fast path")

    monkeypatch.setattr(qdrant, "_scroll_by_filter", boom_scroll)
    monkeypatch.setattr(qdrant, "EMBEDDING_URL", "")  # dense skipped

    async def empty_bm25(q, k, qfilter=None):
        return []

    monkeypatch.setattr(qdrant, "bm25_search", empty_bm25)
    r = asyncio.run(qdrant._qdrant_search("CWE-89 là gì", _Ctx()))
    assert "[cve-exact]" not in r.summary  # kept the hybrid path (0 hits here, but hybrid)
