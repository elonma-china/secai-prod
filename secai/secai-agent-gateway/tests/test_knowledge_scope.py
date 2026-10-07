"""Knowledge KB scope: cheat sheets / ASVS / mitigations share the CVE collection, so every
search must say which side it wants. Default (entity) must behave as if they did not exist."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from secai_gateway.retrieval import filters as F
from secai_gateway.retrieval import qdrant

KNOW = {"kind": "cheatsheet", "chunk_kind": "cheatsheet", "title": "SQL Injection Prevention — Primary Defenses",
        "text": "Use prepared statements.", "source": "owasp-cheatsheet", "version": "git:abc",
        "source_url": "https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html",
        "license": "CC-BY-SA-4.0", "attribution": "OWASP Cheat Sheet Series, CC BY-SA 4.0",
        "cwe_refs": ["CWE-89"], "sensitivity": "defensive"}
ATTACK = {**KNOW, "sensitivity": "contains_attack_example"}
CWE_DEF = {"kind": "weakness", "cwe_id": "CWE-89", "name": "SQL Injection", "text": "CWE-89 definition"}
CVE = {"kind": "core", "chunk_kind": "core", "cve_id": "CVE-2024-1", "cwe_refs": ["CWE-89"], "text": "a CVE"}


def test_resolve_scope():
    assert F.resolve_scope(None, None) == "entity"
    assert F.resolve_scope(None, ["core", "weakness"]) == "entity"
    assert F.resolve_scope(None, ["cheatsheet", "mitigation"]) == "knowledge"
    assert F.resolve_scope(None, ["core", "mitigation"]) == "all"
    assert F.resolve_scope("all", ["core"]) == "all"
    assert F.resolve_scope("bogus", None) == "entity"


def test_build_filter_entity_excludes_knowledge_and_keeps_user_filters():
    f = F.build_filter({"cwe_id": ["CWE-89"], "severity": ["HIGH"]})
    assert f["must_not"] == [{"key": "kind", "match": {"any": list(F.KNOWLEDGE_KINDS)}}]
    assert len(f["must"]) == 2
    assert F.build_filter({"query": "x"}) == {"must_not": f["must_not"]}


def test_build_filter_knowledge_requires_kind_and_drops_attack_payloads():
    f = F.build_filter({"scope": "knowledge", "cwe_id": ["CWE-89"]})
    assert {"key": "kind", "match": {"any": list(F.KNOWLEDGE_KINDS)}} in f["must"]
    assert f["must_not"] == [{"key": "sensitivity", "match": {"value": "contains_attack_example"}}]
    only_cs = F.build_filter({"kind": ["cheatsheet"]})
    assert only_cs["must"] == [{"key": "kind", "match": {"any": ["cheatsheet"]}}]


def test_build_filter_all_has_no_scope_clause():
    assert F.build_filter({"scope": "all"}) is None


def test_scope_ok_and_passes_filter_agree():
    for scope, expect in (("entity", {"know": False, "attack": False, "cwe": True, "cve": True, "legacy": True}),
                          ("knowledge", {"know": True, "attack": False, "cwe": False, "cve": False, "legacy": False}),
                          ("all", {"know": True, "attack": True, "cwe": True, "cve": True, "legacy": True})):
        pls = {"know": KNOW, "attack": ATTACK, "cwe": CWE_DEF, "cve": CVE, "legacy": {"text": "no kind field"}}
        for name, pl in pls.items():
            assert F.scope_ok(pl, scope) is expect[name], (scope, name)
            assert F.passes_filter(pl, {"scope": scope}) is expect[name], (scope, name)


def test_pinned_cwe_in_entity_scope_never_matches_knowledge_chunk():
    params = {"cwe_id": ["CWE-89"], "scope": "entity"}
    assert F.passes_filter(CWE_DEF, params) and F.passes_filter(CVE, params)
    assert not F.passes_filter(KNOW, params)  # carries cwe_refs=[CWE-89] but is not an entity doc


# ── pipeline level (all network faked) ────────────────────────────────────────
class _Ctx:
    pass


def _run(monkeypatch, bm25: list[tuple[str, float]], payloads: dict[str, dict[str, Any]], query: str, **kw):
    calls = {"retrieve": 0, "dense_filter": None, "fanout": None}

    async def fake_bm25(q, k, qfilter=None):
        calls["fanout"] = k
        calls["bm25_filter"] = qfilter
        return bm25

    async def fake_retrieve(client, ids):
        calls["retrieve"] += 1
        return {i: payloads[i] for i in ids if i in payloads}

    monkeypatch.setattr(qdrant, "EMBEDDING_URL", "")
    monkeypatch.setattr(qdrant, "RERANKER_URL", "")
    monkeypatch.setattr(qdrant, "bm25_search", fake_bm25)
    monkeypatch.setattr(qdrant, "_retrieve_by_ids", fake_retrieve)

    async def fake_defs(client, ids):
        return {}

    monkeypatch.setattr(qdrant, "_fetch_definition_nodes", fake_defs)
    monkeypatch.setattr(qdrant, "_graph_reverse_candidates", fake_defs)
    r = asyncio.run(qdrant._qdrant_search(query, _Ctx(), **kw))
    return r, calls


def test_entity_scope_drops_knowledge_bm25_hit_without_extra_round_trip(monkeypatch):
    r, calls = _run(monkeypatch, [("k", 3.0), ("c", 2.0)], {"k": KNOW, "c": CVE}, "sql injection")
    assert "SQL Injection Prevention" not in r.content and "a CVE" in r.content
    assert calls["retrieve"] == 1  # the single normal payload fetch, not an additional post-filter fetch
    assert calls["fanout"] == qdrant.BM25_FANOUT  # scope alone does not widen the fan-out
    assert calls["bm25_filter"] == F.entity_scope_filter()  # scope runs inside Qdrant on the BM25 leg too


def test_knowledge_scope_returns_chunk_with_citation_fields(monkeypatch):
    r, calls = _run(monkeypatch, [("k", 3.0), ("c", 2.0), ("a", 1.0)], {"k": KNOW, "c": CVE, "a": ATTACK},
                "how to prevent sql injection", scope="knowledge")
    assert "a CVE" not in r.content
    assert "**Title:** SQL Injection Prevention — Primary Defenses" in r.content
    assert "**URL:** https://cheatsheetseries.owasp.org" in r.content
    assert "CC-BY-SA-4.0" in r.content and "(knowledge)" in r.content
    assert r.content.count("### Result") == 1  # attack-payload chunk filtered out
    assert calls["bm25_filter"] == F.build_filter({"scope": "knowledge"})  # BM25 is scoped inside Qdrant


def test_kind_filter_infers_knowledge_scope(monkeypatch):
    r, _ = _run(monkeypatch, [("k", 3.0), ("c", 2.0)], {"k": KNOW, "c": CVE}, "sql injection", kind=["cheatsheet"])
    assert "SQL Injection Prevention" in r.content and "a CVE" not in r.content


def test_definition_node_title_falls_back_to_name():
    out = qdrant._format_hit(0, 1.0, CWE_DEF, None)
    assert "**Title:** SQL Injection" in out


def test_min_rerank_cutoff_applies_to_knowledge_only(monkeypatch):
    monkeypatch.setattr(qdrant._settings, "knowledge_min_rerank", 0.5)
    monkeypatch.setattr(qdrant, "RERANKER_URL", "http://r")

    async def fake_rerank(client, query, docs, n):
        return [{"index": i, "relevance_score": 0.9 if i == 0 else 0.1} for i in range(len(docs))]

    async def fake_bm25(q, k, qfilter=None):
        return [("k1", 3.0), ("k2", 2.0)]

    async def fake_retrieve(client, ids):
        return {"k1": KNOW, "k2": {**KNOW, "title": "Weak match", "text": "other"}}

    monkeypatch.setattr(qdrant, "EMBEDDING_URL", "")
    monkeypatch.setattr(qdrant, "rerank", fake_rerank)
    monkeypatch.setattr(qdrant, "bm25_search", fake_bm25)
    monkeypatch.setattr(qdrant, "_retrieve_by_ids", fake_retrieve)
    r = asyncio.run(qdrant._qdrant_search("q", _Ctx(), scope="knowledge"))
    assert r.content.count("### Result") == 1 and "Weak match" not in r.content


def test_exploit_relevance_gate_searches_entity_scope_only(monkeypatch):
    """kb_top_rerank_score feeds the exploit off-topic gate; a cheat sheet scoring high for
    'how do I prevent XSS' must not make an off-topic prompt look in-scope."""
    from secai_gateway.poc import prefetch
    from secai_gateway.retrieval import embedding

    seen = {}

    async def fake_embed(client, q):
        return [0.0]

    async def fake_dense(client, vector, qfilter, limit):
        seen["filter"] = qfilter
        return [{"id": "1", "score": 1.0, "payload": {"text": "x"}}]

    async def fake_rerank(client, q, docs, n):
        return [{"index": 0, "relevance_score": 0.4}]

    monkeypatch.setattr(embedding, "EMBEDDING_URL", "http://e")
    monkeypatch.setattr(embedding, "RERANKER_URL", "http://r")
    monkeypatch.setattr(embedding, "embed", fake_embed)
    monkeypatch.setattr(embedding, "rerank", fake_rerank)
    monkeypatch.setattr(qdrant, "_dense_search", fake_dense)
    assert asyncio.run(prefetch.kb_top_rerank_score("how do I prevent xss")) == 0.4
    assert seen["filter"] == F.entity_scope_filter()


def test_definition_nodes_are_fetched_by_point_id_and_the_scan_fallback_excludes_knowledge_chunks():
    """By id (2 ms) instead of an unindexed scroll (2.1 s on prod); the fallback scan must still exclude knowledge."""
    calls = []

    class _Resp:
        def __init__(self, result): self._r = result
        def raise_for_status(self): ...
        def json(self): return {"result": self._r}

    class _Client:
        def __init__(self, by_ids): self.by_ids = by_ids
        async def post(self, url, json=None, timeout=None):
            calls.append((url.rsplit("/", 1)[-1], json))
            return _Resp(self.by_ids if url.endswith("/points") else {"points": []})

    import uuid
    want = str(uuid.uuid5(uuid.NAMESPACE_URL, "cwe::CWE-89"))
    got = asyncio.run(qdrant._fetch_definition_nodes(_Client([{"id": want, "payload": {"cwe_id": "CWE-89", "name": "SQL Injection", "kind": "weakness"}}]),
                                                     {"cwe_id": ["CWE-89"], "capec_id": ["CAPEC-66"], "technique_id": ["T1190"]}))
    assert list(got) == [want] and calls[0][0] == "points" and len(calls) == 1  # no scan when the id lookup answered
    assert set(calls[0][1]["ids"]) == {want, str(uuid.uuid5(uuid.NAMESPACE_URL, "capec::CAPEC-66")), str(uuid.uuid5(uuid.NAMESPACE_URL, "mitre::T1190"))}
    calls.clear()
    asyncio.run(qdrant._fetch_definition_nodes(_Client([]), {"cwe_id": ["CWE-89"]}))
    assert [c[0] for c in calls] == ["points", "scroll"]
    flt = calls[1][1]["filter"]
    assert flt["should"] and flt["must_not"] == F.entity_scope_filter()["must_not"]


def test_definition_point_id_scheme_matches_the_production_collection():
    """Pinned against real ids read from cve_kb_hybrid (CWE-1245 / chunk_id cwe::CWE-1245)."""
    assert qdrant._definition_point_id("cwe_id", "CWE-1245") == "00b7a4bd-6522-55d9-b476-9c4828e5ce56"
