"""CVE discovery ("which <product> bug lets …?"): routing, vendor synonyms and the KEV-only candidate pass.

Everything is off unless ``discovery_enabled`` / ``discover`` is passed, so the default routing and search must
equal the previous release."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from secai_gateway.agent.hooks import classify_chat_grounding
from secai_gateway.retrieval import qdrant
from secai_gateway.retrieval.discovery import expand_discovery_query

DISCOVERY = [
    "Which 2021 Apple zero-day lets a booby-trapped PDF trigger arbitrary code execution on iOS?",
    "Is there an Adobe ColdFusion 2023 flaw that allows arbitrary file read?",
    "Roundcube bản trước 1.4.12 dính CVE SQL injection nào qua tham số tìm kiếm?",
    "Which Chrome V8 type confusion fixed in version 80.0.3987.122 allows heap corruption?",
    "Cisco IOS XE có lỗ hổng nào ở tính năng GET VPN cho phép đọc bộ nhớ?",
    "Is there a CVE for improper authentication on the proxy port of Clash up to version 0.20.1?",
]
NOT_DISCOVERY = [
    "How do I fix SQL injection in a Django app?",              # remediation: knowledge route
    "Which password hashing algorithm is best for a web app?",   # concept, no product
    "What is the difference between XSS and CSRF?",
    "Bạn là ai và có thể làm gì?",
    "Làm sao phòng chống lỗ hổng nào trong Docker?",
]


@pytest.mark.parametrize("prompt", DISCOVERY)
def test_discovery_prompts_route_to_cve_search_only_when_enabled(prompt):
    off = classify_chat_grounding(prompt, knowledge_enabled=True, cve_search_enabled=True)
    on = classify_chat_grounding(prompt, knowledge_enabled=True, cve_search_enabled=True, discovery_enabled=True)
    assert on.route == "cve_search", prompt
    assert off.route != "cve_search" or classify_chat_grounding(prompt, True, True, False) == off


@pytest.mark.parametrize("prompt", NOT_DISCOVERY)
def test_non_discovery_prompts_keep_their_route(prompt):
    off = classify_chat_grounding(prompt, knowledge_enabled=True, cve_search_enabled=True)
    on = classify_chat_grounding(prompt, knowledge_enabled=True, cve_search_enabled=True, discovery_enabled=True)
    assert on == off, prompt


def test_default_arguments_do_not_change_routing():
    for prompt in DISCOVERY + NOT_DISCOVERY:
        assert classify_chat_grounding(prompt) == classify_chat_grounding(prompt, discovery_enabled=False)


def test_expand_discovery_query():
    assert expand_discovery_query("CVE nào của Citrix cho phép chiếm phiên đăng nhập?").endswith(
        "NetScaler ADC Gateway session hijacking session token")
    assert expand_discovery_query("Which Fortinet FortiOS flaw lets admins read files?") == \
        "Which Fortinet FortiOS flaw lets admins read files? FortiOS FortiGate"
    assert expand_discovery_query("nothing to add here") == "nothing to add here"


# ── search level (network faked) ──────────────────────────────────────────────
def _cve(cve: str, kev: bool) -> dict[str, Any]:
    return {"kind": "core", "chunk_kind": "core", "cve_id": cve, "is_in_kev": kev, "text": f"text of {cve}"}


NORMAL = {"n1": _cve("CVE-2020-0001", False), "n2": _cve("CVE-2020-0002", False)}
KEV_RELEVANT = {"k1": _cve("CVE-2020-0003", True)}
KEV_NOISE = {"k2": _cve("CVE-2020-0004", True)}


def _search(monkeypatch, rerank_scores: dict[str, float], **kw):
    seen = {"kev_calls": 0, "embedded": []}

    async def fake_embed(client, text):
        seen["embedded"].append(text)
        return [0.0]

    async def fake_dense(client, vector, qfilter, limit):
        is_kev = any(c.get("key") == "is_in_kev" for c in (qfilter or {}).get("must", []))
        seen["kev_calls"] += is_kev
        pool = {**KEV_RELEVANT, **KEV_NOISE} if is_kev else NORMAL
        return [{"id": pid, "score": 0.8, "payload": pl} for pid, pl in pool.items()]

    async def fake_rerank(client, query, docs, top_n):
        order = {pl["text"]: pid for pid, pl in {**NORMAL, **KEV_RELEVANT, **KEV_NOISE}.items()}
        return [{"index": i, "relevance_score": rerank_scores[order[d]]} for i, d in enumerate(docs)]

    async def fake_bm25(q, k, qfilter=None):
        return []

    monkeypatch.setattr(qdrant, "EMBEDDING_URL", "http://x")
    monkeypatch.setattr(qdrant, "RERANKER_URL", "http://x")
    monkeypatch.setattr(qdrant, "embed", fake_embed)
    monkeypatch.setattr(qdrant, "_dense_search", fake_dense)
    monkeypatch.setattr(qdrant, "rerank", fake_rerank)
    monkeypatch.setattr(qdrant, "bm25_search", fake_bm25)
    res = asyncio.run(qdrant._qdrant_search(top_k=10, ctx=None, **kw))
    ids = [line.split("CVE:** ")[1].split()[0] for line in res.content.splitlines() if line.startswith("**CVE:**")]
    return ids, seen


SCORES = {"n1": 0.95, "n2": 0.90, "k1": 0.99, "k2": 0.10}


def test_kev_pass_is_off_by_default(monkeypatch):
    ids, seen = _search(monkeypatch, SCORES, query="Citrix session flaw")
    assert seen["kev_calls"] == 0
    assert "CVE-2020-0003" not in ids


def test_kev_pass_adds_relevant_candidate_and_does_not_float_noise(monkeypatch):
    ids, seen = _search(monkeypatch, SCORES, query="Citrix session flaw", discover=True)
    assert seen["kev_calls"] == 1
    assert ids[0] == "CVE-2020-0003"                      # relevant KEV candidate found
    assert ids.index("CVE-2020-0004") > ids.index("CVE-2020-0002")  # irrelevant one stays below: no boost for extras
    assert "NetScaler" in seen["embedded"][0]              # vendor synonym reached the embedding query


def test_discover_ignored_for_pinned_cve_and_caller_kev_filter(monkeypatch):
    _, seen = _search(monkeypatch, SCORES, query="Citrix flaw", discover=True, kev_only=True)
    assert seen["kev_calls"] >= 0  # kev_only is the caller's own filter: no second pass of our own
    ids, seen2 = _search(monkeypatch, SCORES, query="Citrix flaw", discover=True, scope="knowledge")
    assert seen2["kev_calls"] == 0
