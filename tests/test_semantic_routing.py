"""Semantic routing: the knowledge-KB gate (retrieval/qdrant.py ``gate=``), the explicit-CVE-request rule and the candidate
filter. Everything is off unless ``gate`` is passed, so the default search must equal the previous release."""
from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from secai_gateway.agent.hooks import classify_chat_grounding, is_semantic_route_candidate
from secai_gateway.retrieval import qdrant

N = 12  # more hits than the knowledge rerank window (10): the last two are never judged by the reranker


def _search(monkeypatch, rr: dict[int, float], top_dense: float = 0.8, rerank_fails: bool = False, **kw):
    payloads = {f"k{i}": {"kind": "cheatsheet", "chunk_kind": "cheatsheet", "doc_id": f"d{i}", "parent_id": f"p{i}",
                          "title": f"title {i}", "text": f"text {i}"} for i in range(N)}

    async def fake_embed(client, text):
        return [0.0]

    async def fake_dense(client, vector, qfilter, limit):
        return [{"id": pid, "score": top_dense - 0.01 * i, "payload": pl} for i, (pid, pl) in enumerate(payloads.items())]

    async def fake_rerank(client, query, docs, top_n):
        if rerank_fails:
            raise RuntimeError("reranker down")
        return [{"index": i, "relevance_score": rr[int(d.split()[1])]} for i, d in enumerate(docs)]

    async def fake_bm25(q, k, qfilter=None):
        return []

    monkeypatch.setattr(qdrant, "EMBEDDING_URL", "http://x")
    monkeypatch.setattr(qdrant, "RERANKER_URL", "http://x")
    monkeypatch.setattr(qdrant, "embed", fake_embed)
    monkeypatch.setattr(qdrant, "_dense_search", fake_dense)
    monkeypatch.setattr(qdrant, "rerank", fake_rerank)
    monkeypatch.setattr(qdrant, "bm25_search", fake_bm25)
    res = asyncio.run(qdrant._qdrant_search(query="q", top_k=6, ctx=None, scope="knowledge", **kw))
    return [int(m) for m in re.findall(r"\*\*Title:\*\* title (\d+)", res.content)], res.content


ALL_LOW = {i: 0.05 for i in range(10)}


def test_default_gate_still_lets_unjudged_candidates_through(monkeypatch):
    # Documents the previous behaviour that the flag replaces: candidates beyond the rerank window carry no rerank score
    # and pass the "rerank >= 0.9" rule, so a question nothing in the KB matches still got reference text.
    ids, _ = _search(monkeypatch, ALL_LOW)
    assert ids and all(i >= 10 for i in ids)


def test_confirm_gate_drops_everything_when_the_reranker_found_nothing(monkeypatch):
    ids, content = _search(monkeypatch, ALL_LOW, gate="confirm")
    assert ids == [] and "### Result" not in content


def test_confirm_gate_weak_evidence_keeps_judged_chunks_above_the_floor(monkeypatch):
    ids, _ = _search(monkeypatch, {**ALL_LOW, 1: 0.7, 3: 0.55, 4: 0.3}, gate="confirm")
    assert sorted(ids) == [1, 3]  # not the 0.3 one, not the unjudged tail


def test_confirm_gate_strong_evidence_keeps_only_strong_chunks(monkeypatch):
    ids, _ = _search(monkeypatch, {**ALL_LOW, 0: 0.97, 2: 0.92, 1: 0.7}, gate="confirm")
    assert sorted(ids) == [0, 2]


def test_confirm_gate_without_reranker_behaves_as_before(monkeypatch):
    ids, _ = _search(monkeypatch, ALL_LOW, rerank_fails=True, gate="confirm")
    assert ids  # serving down: keep the RRF order rather than hide the reference text


def test_semantic_gate_needs_an_unmistakable_match(monkeypatch):
    assert _search(monkeypatch, {**ALL_LOW, 0: 0.95}, gate="semantic")[0] == []        # relevant-looking is not enough
    ids, _ = _search(monkeypatch, {**ALL_LOW, 0: 0.995, 1: 0.93, 2: 0.5}, gate="semantic")
    assert sorted(ids) == [0, 1]


def test_semantic_gate_needs_a_close_cosine(monkeypatch):
    assert _search(monkeypatch, {**ALL_LOW, 0: 0.999}, top_dense=0.60, gate="semantic")[0] == []
    assert _search(monkeypatch, {**ALL_LOW, 0: 0.999}, top_dense=0.70, gate="semantic")[0] == [0]


def test_semantic_gate_without_reranker_returns_nothing(monkeypatch):
    assert _search(monkeypatch, ALL_LOW, rerank_fails=True, gate="semantic")[0] == []


# ── routing rules ─────────────────────────────────────────────────────────────
CVE_REQUESTS = [
    "lỗ hổng cho phép thoát khỏi container trong runc, liệt kê CVE giúp mình",
    "Lỗ hổng nào khiến module HTTP/3 QUIC của nginx bị crash worker? cho xin số CVE",
    "list CVEs for openssh where a signal handler race gives unauthenticated root",
]
NOT_CVE_REQUESTS = [
    "CVE là gì và dùng để làm gì?",
    "Làm sao vá lỗ hổng trong runc, cho xin cách khắc phục",
    "Bạn có thể liệt kê những gì bạn làm được?",
]


@pytest.mark.parametrize("prompt", CVE_REQUESTS)
def test_explicit_cve_request_without_capitalised_product_routes_to_cve_search(prompt):
    on = classify_chat_grounding(prompt, knowledge_enabled=True, cve_search_enabled=True, discovery_enabled=True)
    assert on.route == "cve_search", prompt


@pytest.mark.parametrize("prompt", NOT_CVE_REQUESTS)
def test_non_requests_keep_their_route(prompt):
    off = classify_chat_grounding(prompt, knowledge_enabled=True, cve_search_enabled=True)
    on = classify_chat_grounding(prompt, knowledge_enabled=True, cve_search_enabled=True, discovery_enabled=True)
    assert on == off, prompt


@pytest.mark.parametrize("prompt,expected", [
    ("How do I defend against open redirect vulnerabilities?", True),
    ("Cách tránh race condition TOCTOU trong luồng thanh toán", True),
    ("hi", False),
    ("xin chào bạn", False),
    ("Bạn là ai và có thể làm gì?", False),
    ("cve", False),
    ("", False),
])
def test_semantic_route_candidate(prompt, expected):
    assert is_semantic_route_candidate(prompt) is expected
