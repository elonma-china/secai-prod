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


def test_definition_node_scroll_excludes_knowledge_chunks(monkeypatch):
    sent = {}

    class _Resp:
        def raise_for_status(self): ...
        def json(self): return {"result": {"points": []}}

    class _Client:
        async def post(self, url, json=None, timeout=None):
            sent["json"] = json
            return _Resp()

    asyncio.run(qdrant._fetch_definition_nodes(_Client(), {"cwe_id": ["CWE-89"]}))
    flt = sent["json"]["filter"]
    assert flt["should"] and flt["must_not"] == F.entity_scope_filter()["must_not"]


# ── real pipeline, only the network layer faked (these catch what _qdrant_search-level fakes cannot) ──
def _pipeline(monkeypatch, *, dense=None, bm25=None, payloads=None, with_embedding=True):
    seen = {"dense": [], "bm25": []}

    async def fake_embed(client, q):
        return [0.0]

    async def fake_dense(client, vector, qfilter, limit):
        seen["dense"].append(qfilter)
        return [h for h in (dense or []) if F.passes_filter(h["payload"], _as_params(qfilter))]

    async def fake_bm25(q, k, qfilter=None):
        seen["bm25"].append((q, qfilter))
        return [(pid, sc) for pid, sc in (bm25 or []) if F.passes_filter((payloads or {})[pid], _as_params(qfilter))]

    async def fake_retrieve(client, ids):
        return {i: (payloads or {})[i] for i in ids if i in (payloads or {})}

    async def none(client, ids):
        return {}

    monkeypatch.setattr(qdrant, "EMBEDDING_URL", "http://e" if with_embedding else "")
    monkeypatch.setattr(qdrant, "RERANKER_URL", "")
    monkeypatch.setattr(qdrant, "embed", fake_embed)
    monkeypatch.setattr(qdrant, "_dense_search", fake_dense)
    monkeypatch.setattr(qdrant, "bm25_search", fake_bm25)
    monkeypatch.setattr(qdrant, "_retrieve_by_ids", fake_retrieve)
    monkeypatch.setattr(qdrant, "_fetch_definition_nodes", none)
    monkeypatch.setattr(qdrant, "_graph_reverse_candidates", none)
    return seen


def _as_params(qfilter):
    """Invert a built filter back to the params dict passes_filter understands (only what these tests use)."""
    p: dict[str, Any] = {"scope": "all"}
    if not qfilter:
        return p
    for clause in qfilter.get("must", []):
        if "should" in clause:
            for c in clause["should"]:
                key = {"cwe_refs": "cwe_id", "cwe_id": "cwe_id", "capec_entries": "capec_id", "capec_id": "capec_id"}.get(c["key"])
                if key:
                    p[key] = c["match"]["any"]
        elif clause["key"] == "kind":
            p["kind"] = clause["match"].get("any") or [clause["match"]["value"]]
        elif clause["key"] == "cve_id":
            p["cve_id"] = clause["match"].get("any") or [clause["match"]["value"]]
    mn = qfilter.get("must_not", [])
    if any(c["key"] == "kind" for c in mn):
        p["scope"] = "entity"
    elif any(c["key"] == "sensitivity" for c in mn):
        p["scope"] = "knowledge"
    return p


def _dense_hit(pid, payload, score=0.9):
    return {"id": pid, "score": score, "payload": payload}


def test_fix_cve_question_keeps_knowledge_hits_instead_of_pinning_cve_id(monkeypatch):
    """H1: 'cách vá CVE-X' used to add must cve_id to the knowledge filter, which no knowledge chunk has."""
    seen = _pipeline(monkeypatch, dense=[_dense_hit("k", KNOW)], bm25=[("k", 2.0)], payloads={"k": KNOW})
    r = asyncio.run(qdrant._qdrant_search("cách vá CVE-2021-44228", _Ctx(), scope="knowledge"))
    assert "SQL Injection Prevention" in r.content
    for f in seen["dense"] + [flt for _, flt in seen["bm25"]]:
        assert "cve_id" not in json.dumps(f)


def test_cwe_id_adds_a_pinned_pass_without_hard_filtering_the_semantic_pass(monkeypatch):
    """H2: only ~1% of cheat-sheet chunks carry cwe refs, so the CWE id must not gate the semantic pass."""
    mit = {**KNOW, "kind": "mitigation", "doc_id": "cwe:CWE-502#mitigation", "parent_id": "CWE-502", "title": "CWE-502 mitigations",
           "cwe_id": "CWE-502", "cwe_refs": ["CWE-502"], "text": "avoid untrusted deserialization"}
    sheet = {**KNOW, "doc_id": "owasp-cs:Deserialization#x", "parent_id": "owasp-cs:Deserialization_Cheat_Sheet", "cwe_refs": [], "title": "Deserialization"}
    seen = _pipeline(monkeypatch, dense=[_dense_hit("sheet", sheet), _dense_hit("mit", mit, 0.8)], payloads={"sheet": sheet, "mit": mit}, bm25=[])
    r = asyncio.run(qdrant._qdrant_search("how to fix CWE-502", _Ctx(), scope="knowledge"))
    assert len(seen["dense"]) == 2  # semantic pass + pinned pass
    assert "cwe" not in json.dumps(seen["dense"][0]).lower()  # semantic pass unpinned
    assert "CWE-502" in json.dumps(seen["dense"][1])
    assert "Deserialization" in r.content and "CWE-502 mitigations" in r.content  # both reach the result


def test_entity_scope_bm25_crowding_cannot_push_cve_hits_out(monkeypatch):
    """H3: 25 knowledge hits would fill the BM25 top-k and, after post-filtering, leave no CVE."""
    k_payloads = {f"k{i}": KNOW for i in range(25)}
    payloads = {**k_payloads, "c": CVE}
    bm25 = [(f"k{i}", 10.0 - i * 0.01) for i in range(25)] + [("c", 1.0)]
    seen = _pipeline(monkeypatch, bm25=bm25, payloads=payloads, with_embedding=False)
    r = asyncio.run(qdrant._qdrant_search("sql injection prevention", _Ctx()))
    assert "a CVE" in r.content and seen["bm25"][0][1] == F.entity_scope_filter()


def test_all_hits_scoped_out_returns_not_found_not_an_empty_result(monkeypatch):
    """H4: the tool result must say 'not found' (the prompt relies on that sentence), never ''."""
    _pipeline(monkeypatch, dense=[], bm25=[("k", 3.0)], payloads={"k": KNOW}, with_embedding=False)
    monkeypatch.setattr(qdrant, "bm25_search", lambda q, k, qfilter=None: _async([("k", 3.0)]))
    r = asyncio.run(qdrant._qdrant_search("sql injection", _Ctx()))  # entity scope drops the only hit
    assert r.content.startswith("Không tìm thấy") and not r.is_error


async def _async(v):
    return v


def test_model_cannot_force_scope_or_knowledge_kinds_through_the_tool(monkeypatch):
    """M4: the registered tool has no scope parameter and strips knowledge kinds."""
    seen = {}

    async def fake(query, ctx, **kw):
        seen.update(kw)
        return qdrant.ToolResult(content="x", summary="s")

    monkeypatch.setattr(qdrant, "_qdrant_search", fake)
    validated = qdrant.qdrant_search.validate({"query": "x", "scope": "all", "kind": ["cheatsheet", "core"]})
    assert "scope" not in validated
    asyncio.run(qdrant._qdrant_search_tool(**validated, ctx=_Ctx()))
    assert seen["scope"] == "entity" and seen["kind"] == ["core"]
    asyncio.run(qdrant._qdrant_search_tool(query="x", ctx=_Ctx(), kind=["mitigation"]))
    assert seen["kind"] is None


def test_knowledge_scope_caps_chunks_per_document(monkeypatch):
    docs = {f"k{i}": {**KNOW, "doc_id": f"d{i}", "parent_id": "owasp-cs:Same" if i < 4 else f"owasp-cs:Other{i}"} for i in range(6)}
    _pipeline(monkeypatch, dense=[_dense_hit(pid, pl, 0.9 - i * 0.01) for i, (pid, pl) in enumerate(docs.items())], payloads=docs, bm25=[])
    r = asyncio.run(qdrant._qdrant_search("x", _Ctx(), scope="knowledge", top_k=6))
    assert r.content.count("owasp-cs:Same") == 0 and r.content.count("**Ref:** d") == 4  # 2 of the 4 "Same" + 2 others


def test_dense_gate_returns_nothing_when_no_knowledge_is_close(monkeypatch):
    _pipeline(monkeypatch, dense=[_dense_hit("k", KNOW, 0.31)], payloads={"k": KNOW}, bm25=[])
    monkeypatch.setattr(qdrant._settings, "knowledge_min_dense", 0.5)
    assert qdrant.asyncio.run(qdrant._qdrant_search("what is the weather", _Ctx(), scope="knowledge")).content.startswith("Không tìm thấy")
    monkeypatch.setattr(qdrant._settings, "knowledge_min_dense", 0.2)
    assert "SQL Injection Prevention" in asyncio.run(qdrant._qdrant_search("sql", _Ctx(), scope="knowledge")).content


def test_vietnamese_query_gets_english_terms_on_the_bm25_leg_only(monkeypatch):
    seen = _pipeline(monkeypatch, dense=[_dense_hit("k", KNOW)], payloads={"k": KNOW}, bm25=[])
    asyncio.run(qdrant._qdrant_search("Quy trình quên mật khẩu hoạt động thế nào?", _Ctx(), scope="knowledge"))
    assert "forgot password" in seen["bm25"][0][0]
    seen2 = _pipeline(monkeypatch, bm25=[], payloads={}, with_embedding=False)
    asyncio.run(qdrant._qdrant_search("quên mật khẩu", _Ctx()))  # entity scope: query untouched
    assert seen2["bm25"][0][0] == "quên mật khẩu"
