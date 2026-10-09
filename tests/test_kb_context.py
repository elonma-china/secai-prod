"""Exact CVE grounding cannot be inferred from generic knowledge or service errors."""
from __future__ import annotations

import asyncio
import importlib
import json

import httpx
import pytest

CVE = "CVE-2021-44228"
DESCRIPTION = "Apache Log4j resolves attacker controlled JNDI lookups and permits remote code execution."


@pytest.fixture(autouse=True)
def local_catalog(monkeypatch, tmp_path):
    monkeypatch.setenv("POC_CATALOG_DB", str(tmp_path / "poc.db"))


def graph(properties=None, cwes=None, capecs=None, techniques=None):
    rows = [] if properties is None else [{"row": [properties, cwes or [], capecs or [], techniques or []]}]
    return {"results": [{"columns": ["cve", "cwe_refs", "capec_entries", "mitre_techniques"], "data": rows}], "errors": []}


def install_services(monkeypatch, primary=(), neo=None, supplemental=(), qdrant_error=False,
                     qdrant_response=None, collection_payloads=None, collection_errors=(),
                     collection_malformed=()):
    kb = importlib.import_module("secai_gateway.retrieval.kb_context")
    requests = []
    original_client = httpx.AsyncClient

    def handler(request):
        body = json.loads(request.content)
        requests.append((str(request.url), body))
        if "/tx/commit" in request.url.path:
            return httpx.Response(200, json=neo if neo is not None else graph())
        assert request.url.path.endswith("/points/scroll"), "unexpected embedding/reranking call"
        collection = request.url.path.split("/collections/", 1)[1].split("/", 1)[0]
        if collection in collection_errors:
            return httpx.Response(503, json={"error": "supplemental collection unavailable"})
        if collection in collection_malformed:
            return httpx.Response(200, json={"result": {}})
        if qdrant_error:
            return httpx.Response(503, json={"error": "temporarily unavailable"})
        if qdrant_response is not None:
            return httpx.Response(200, json=qdrant_response)
        if collection_payloads is not None:
            payloads = collection_payloads.get(collection, ())
            return httpx.Response(200, json={"result": {"points": [
                {"id": f"{collection}-{i}", "payload": p} for i, p in enumerate(payloads)
            ]}})
        exact = "should" not in body["filter"]
        payloads = primary if exact else supplemental
        return httpx.Response(200, json={"result": {"points": [
            {"id": str(i), "payload": p} for i, p in enumerate(payloads)
        ]}})

    monkeypatch.setattr(kb.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))
    return kb, requests


def retrieve(kb, cve=CVE):
    return asyncio.run(kb.get_cve_context(cve))


def test_exact_cve_with_description_and_source(monkeypatch):
    kb, requests = install_services(monkeypatch, primary=[{
        "cve_id": CVE, "kind": "core", "text": DESCRIPTION, "source_url": "https://example.org/advisory",
        "cwe_refs": ["CWE-502"],
    }])
    result = retrieve(kb, " cve-2021-44228 ")
    assert result["status"] == "found"
    assert DESCRIPTION in result["content"]
    assert "https://example.org/advisory" in result["content"]
    assert result["cwe_refs"] == ["CWE-502"]
    assert requests[0][1]["filter"]["must"][0]["match"]["any"] == [CVE]


def test_qdrant_error_is_not_absence(monkeypatch):
    kb, _ = install_services(monkeypatch, qdrant_error=True)
    assert retrieve(kb) == {"status": "error", "content": "", "cwe_refs": []}


def test_neo4j_description_can_ground_when_qdrant_fails(monkeypatch):
    kb, _ = install_services(monkeypatch, qdrant_error=True, neo=graph(
        {"id": CVE, "description": DESCRIPTION, "source_url": "https://example.org/neo"}, ["CWE-502"],
    ))
    result = retrieve(kb)
    assert result["status"] == "found"
    assert DESCRIPTION in result["content"]
    assert "https://example.org/neo" in result["content"]
    assert result["cwe_refs"] == ["CWE-502"]


def test_empty_services_return_not_found(monkeypatch):
    kb, requests = install_services(monkeypatch)
    assert retrieve(kb) == {"status": "not_found", "content": "", "cwe_refs": []}
    assert len(requests) == 2


@pytest.mark.parametrize("payload", [
    {"cve_id": CVE},
    {"cve_id": CVE, "title": "Log4Shell", "text": "Log4Shell"},
    {"cve_id": CVE, "text": f"CVE: {CVE}\nSeverity: CRITICAL\nCVSS: 10.0"},
    {"cve_id": CVE, "kind": "mitigation", "text": "Disable lookup features and apply current security patches to protect your deployment."},
])
def test_identifier_title_metadata_or_mitigation_alone_is_insufficient(monkeypatch, payload):
    kb, requests = install_services(monkeypatch, primary=[payload])
    assert retrieve(kb)["status"] == "insufficient"
    assert len(requests) == 2


def test_neo4j_identifier_only_is_insufficient(monkeypatch):
    kb, _ = install_services(monkeypatch, neo=graph({"id": CVE}, ["CWE-502"]))
    assert retrieve(kb)["status"] == "insufficient"


def test_generic_or_different_cve_cannot_confirm_target(monkeypatch):
    kb, _ = install_services(monkeypatch, primary=[
        {"kind": "cheatsheet", "text": DESCRIPTION, "cwe_refs": ["CWE-502"]},
        {"cve_id": "CVE-2022-12345", "text": DESCRIPTION},
    ])
    assert retrieve(kb)["status"] == "not_found"


def test_neo4j_query_error_is_not_evidence(monkeypatch):
    kb, _ = install_services(monkeypatch, neo={"errors": [{"code": "Neo.DatabaseError", "message": CVE}], "results": []})
    assert retrieve(kb)["status"] == "error"


def test_linked_supplemental_is_scoped_deduplicated_and_separate(monkeypatch):
    common = {"kind": "cheatsheet", "cwe_refs": ["CWE-502"], "text": "Avoid deserializing objects supplied by untrusted clients.", "source_url": "https://example.org/cheatsheet"}
    kb, requests = install_services(monkeypatch, primary=[{"cve_id": CVE, "text": DESCRIPTION, "cwe_refs": ["CWE-502"], "capec_entries": ["CAPEC-586"], "mitre_techniques": ["T1190"]}], supplemental=[
        common, common.copy(),
        {"kind": "pattern", "capec_id": "CAPEC-586", "text": "Attackers manipulate objects to bypass expected program controls."},
        {"kind": "technique", "technique_id": "T1190", "text": "Adversaries exploit public facing applications for initial access."},
        {"kind": "cheatsheet", "cwe_refs": ["CWE-79"], "text": "UNRELATED DOCUMENT"},
        {"cve_id": "CVE-2022-12345", "cwe_refs": ["CWE-502"], "text": "OTHER CVE SHOULD BE EXCLUDED"},
    ])
    result = retrieve(kb)
    content = result["content"]
    assert content.index(DESCRIPTION) < content.index("General knowledge")
    assert content.count(common["text"]) == 1
    assert common["source_url"] in content
    assert "CAPEC-586" in content and "T1190" in content
    assert "UNRELATED DOCUMENT" not in content and "OTHER CVE" not in content
    qdrant_requests = [(url, body) for url, body in requests if "/collections/" in url]
    assert len(qdrant_requests) == 2
    assert qdrant_requests[0][0] == qdrant_requests[1][0]
    assert qdrant_requests[1][1]["limit"] <= 16


def test_output_is_bounded_and_primary_source_survives(monkeypatch):
    kb, _ = install_services(monkeypatch, primary=[{"cve_id": CVE, "text": DESCRIPTION + " extra" * 4000, "source_url": "https://example.org/primary", "cwe_refs": ["CWE-502"]}], supplemental=[
        {"kind": "weakness", "cwe_id": "CWE-502", "text": "Generic mitigation advice. " * 2000, "source_url": "https://example.org/general"},
    ])
    result = retrieve(kb)
    assert result["status"] == "found"
    assert len(result["content"]) <= 6000
    assert "https://example.org/primary" in result["content"]
    assert result["content"].index(DESCRIPTION) < result["content"].index("General knowledge")


def test_invalid_identifier_does_not_query_services(monkeypatch):
    kb, requests = install_services(monkeypatch)
    assert retrieve(kb, "CVE invented") == {"status": "not_found", "content": "", "cwe_refs": []}
    assert requests == []


def test_primary_requests_run_concurrently(monkeypatch):
    kb = importlib.import_module("secai_gateway.retrieval.kb_context")
    original_client = httpx.AsyncClient

    async def run():
        started = set()
        both_started = asyncio.Event()

        async def handler(request):
            service = "neo4j" if "/tx/commit" in request.url.path else "qdrant"
            started.add(service)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.5)
            return httpx.Response(200, json=graph() if service == "neo4j" else {"result": {"points": []}})

        monkeypatch.setattr(kb.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))
        result = await kb.get_cve_context(CVE)
        assert result["status"] == "not_found"
        assert started == {"qdrant", "neo4j"}

    asyncio.run(run())


def test_qdrant_remains_usable_when_neo4j_returns_error(monkeypatch):
    kb, _ = install_services(monkeypatch, primary=[{"cve_id": CVE, "text": DESCRIPTION}], neo={"errors": [{"message": "query failed"}], "results": []})
    assert retrieve(kb)["status"] == "found"


@pytest.mark.parametrize("text", [
    "No description is available for this CVE record.",
    "Qdrant request failed while looking up this vulnerability record.",
    "CVE-2021-44228 Log4Shell remote code execution vulnerability in Apache Log4j",
])
def test_placeholder_error_or_title_only_text_is_insufficient(monkeypatch, text):
    kb, _ = install_services(monkeypatch, primary=[{"cve_id": CVE, "title": "Log4Shell remote code execution vulnerability in Apache Log4j", "text": text}])
    assert retrieve(kb)["status"] == "insufficient"


def test_duplicate_text_from_different_sources_preserves_both_sources_once(monkeypatch):
    kb, _ = install_services(monkeypatch, primary=[
        {"cve_id": CVE, "text": DESCRIPTION, "source_url": "https://example.org/one"},
        {"cve_id": CVE, "text": DESCRIPTION, "source_url": "https://example.org/two"},
    ])
    content = retrieve(kb)["content"]
    assert content.count(DESCRIPTION) == 1
    assert "https://example.org/one" in content and "https://example.org/two" in content


def test_supplemental_plural_cve_ids_cannot_leak_other_cves(monkeypatch):
    kb, _ = install_services(monkeypatch, primary=[{"cve_id": CVE, "text": DESCRIPTION, "cwe_refs": ["CWE-502"]}], supplemental=[
        {"cwe_id": "CWE-502", "cve_ids": ["CVE-2022-12345"], "text": "UNRELATED MULTI-CVE ADVISORY"},
    ])
    assert "UNRELATED MULTI-CVE ADVISORY" not in retrieve(kb)["content"]


@pytest.mark.parametrize("response", [
    {"status": {"error": "lookup unavailable"}},
    {"result": {}},
    {"result": {"points": "invalid payload"}},
])
def test_malformed_qdrant_response_is_error_not_absence(monkeypatch, response):
    kb, _ = install_services(monkeypatch, qdrant_response=response)
    assert retrieve(kb)["status"] == "error"


def test_autoscan_flattened_metadata_without_description_is_insufficient(monkeypatch):
    kb, _ = install_services(monkeypatch, primary=[{
        'cve_id': CVE, 'kind': 'core',
        'text': f'CVE ID: {CVE} | Severity: HIGH | CVSS: 8.8 | CWE Weaknesses: CWE-79',
    }])
    assert retrieve(kb)['status'] == 'insufficient'


def test_long_metadata_cannot_erase_usable_primary_description(monkeypatch):
    kb, _ = install_services(monkeypatch, primary=[{
        'cve_id': CVE, 'kind': 'core', 'text': DESCRIPTION,
        'title': 'Product ' * 1000, 'products': ['a' * 5000],
        'source_url': 'https://example.org/advisory',
    }])
    result = retrieve(kb)
    assert result['status'] == 'found'
    assert DESCRIPTION in result['content']
    assert 'https://example.org/advisory' in result['content']
    assert len(result['content']) <= kb.MAX_CONTEXT_CHARS


@pytest.mark.parametrize("description", ["", "Description: " + DESCRIPTION])
def test_opencve_ingestion_metadata_requires_description(monkeypatch, description):
    kb, _ = install_services(monkeypatch, primary=[{
        "cve_id": CVE, "kind": "core",
        "text": f"CVE ID: {CVE} | Severity: HIGH | CVSS: 8.8 | Affected Products: Apache Log4j Enterprise Server, Apache Tomcat Application Server | {description}",
    }])
    result = retrieve(kb)
    assert result["status"] == ("found" if description else "insufficient")


def install_ghsa_services(monkeypatch, *, primary=(), ghsa=(), extra_collections="ghsa-lab",
                          primary_collection="primary-cve", collection_errors=(),
                          collection_malformed=()):
    monkeypatch.setenv("QDRANT_KB_SUPPLEMENTAL_COLLECTIONS", extra_collections)
    kb, requests = install_services(
        monkeypatch,
        collection_payloads={primary_collection: primary, "ghsa-lab": ghsa},
        collection_errors=collection_errors,
        collection_malformed=collection_malformed,
    )
    monkeypatch.setattr(kb.qdrant, "COLLECTION", primary_collection)
    return kb, requests


def _primary_description():
    return {"cve_id": CVE, "kind": "core", "text": DESCRIPTION}


def test_ghsa_supplemental_query_is_exact_bounded_and_labeled(monkeypatch):
    url = "https://github.com/advisories/GHSA-fixture"
    advisory = {
        "source": "ghsa", "kind": "advisory", "cve_refs": [CVE],
        "tags": ["patch", "vendor-fix"], "source_url": url,
        "text": "GHSA_PATCH_MARKER: the vendor advisory lists a fixed release.",
    }
    kb, requests = install_ghsa_services(monkeypatch, primary=[_primary_description()], ghsa=[advisory])
    result = retrieve(kb)

    qdrant_requests = [(url, body) for url, body in requests if "/collections/" in url]
    assert result["status"] == "found"
    assert result["content"].index(DESCRIPTION) < result["content"].index("CVE-linked GHSA advisory/patch context")
    assert "GHSA_PATCH_MARKER" in result["content"]
    assert "source: ghsa" in result["content"]
    assert "kind: advisory" in result["content"]
    tags_label = next(line for line in result["content"].splitlines() if line.startswith("tags: "))
    assert "patch" in tags_label and "vendor-fix" in tags_label
    assert f"Source: {url}" in result["content"]
    assert len(qdrant_requests) == 2
    primary_url, primary_body = qdrant_requests[0]
    ghsa_url, ghsa_body = qdrant_requests[1]
    assert "/collections/primary-cve/" in primary_url
    assert primary_body["filter"]["must"][0]["key"] == "cve_id"
    assert "/collections/ghsa-lab/" in ghsa_url
    assert ghsa_body["filter"] == {"must": [
        {"key": "source", "match": {"value": "ghsa"}},
        {"key": "cve_refs", "match": {"value": CVE}},
    ]}
    assert ghsa_body["limit"] == 8


@pytest.mark.parametrize("payload", [
    {"source": "ghsa", "cve_refs": ["CVE-2022-12345"],
     "text": "WRONG_CVE_ONLY_MARKER must not be included."},
    {"source": "autoscan", "cve_refs": [CVE],
     "text": "WRONG_SOURCE_MARKER must not be included."},
    {"source": "ghsa", "cve_refs": [CVE, "CVE-2022-12345"],
     "text": "MIXED_CVE_REFS_MARKER must not be included."},
])
def test_ghsa_supplemental_filters_wrong_source_or_non_exact_cve_refs(monkeypatch, payload):
    kb, _ = install_ghsa_services(monkeypatch, primary=[_primary_description()], ghsa=[payload])
    content = retrieve(kb)["content"]
    assert payload["text"].split(":", 1)[0] not in content
    assert "Exact CVE technical context" in content


def test_ghsa_supplemental_deduplicates_text_but_keeps_source_urls(monkeypatch):
    text = "DUPLICATE_GHSA_TEXT_MARKER: vendor release fixes this issue."
    ghsa = [
        {"source": "ghsa", "kind": "advisory", "cve_refs": [CVE],
         "source_url": "https://github.com/advisories/GHSA-one", "text": text},
        {"source": "ghsa", "kind": "patch", "cve_refs": [CVE],
         "source_url": "https://github.com/advisories/GHSA-two", "text": f" {text} "},
    ]
    kb, _ = install_ghsa_services(monkeypatch, primary=[_primary_description()], ghsa=ghsa)
    content = retrieve(kb)["content"]
    assert content.count("DUPLICATE_GHSA_TEXT_MARKER") == 1
    assert "https://github.com/advisories/GHSA-one" in content
    assert "https://github.com/advisories/GHSA-two" in content


def test_patched_versions_keep_priority_when_ghsa_context_budget_is_full(monkeypatch):
    ghsa = [
        {"source": "ghsa", "kind": "mitigation", "section": "advisory",
         "cve_refs": [CVE], "source_url": "https://github.com/advisories/GHSA-large",
         "text": "LONG_ADVISORY_MARKER " + "advisory prose " * 3000},
        {"source": "ghsa", "kind": "mitigation", "section": "patched-versions",
         "cve_refs": [CVE], "source_url": "https://github.com/advisories/GHSA-patch",
         "text": "PATCHED_VERSION_MARKER: version 9.9.9 is fixed."},
    ]
    kb, _ = install_ghsa_services(monkeypatch, primary=[_primary_description()], ghsa=ghsa)
    content = retrieve(kb)["content"]
    advisory = content.index("CVE-linked GHSA advisory/patch context")
    assert content.index(DESCRIPTION) < advisory
    assert "PATCHED_VERSION_MARKER" in content
    assert "https://github.com/advisories/GHSA-patch" in content
    assert len(content) <= kb.MAX_CONTEXT_CHARS


def test_ghsa_collection_error_does_not_downgrade_primary_found(monkeypatch):
    kb, requests = install_ghsa_services(
        monkeypatch, primary=[_primary_description()],
        ghsa=[{"source": "ghsa", "cve_refs": [CVE], "text": "unused"}],
        collection_errors={"ghsa-lab"},
    )
    result = retrieve(kb)
    assert result["status"] == "found"
    assert DESCRIPTION in result["content"]
    assert "CVE-linked GHSA advisory/patch context" not in result["content"]
    assert any("/collections/ghsa-lab/" in url for url, _ in requests)


def test_malformed_ghsa_collection_does_not_downgrade_primary_found(monkeypatch):
    kb, _ = install_ghsa_services(
        monkeypatch, primary=[_primary_description()],
        collection_malformed={"ghsa-lab"},
    )
    result = retrieve(kb)
    assert result["status"] == "found"
    assert DESCRIPTION in result["content"]
    assert "CVE-linked GHSA advisory/patch context" not in result["content"]


def test_ghsa_supplemental_alone_never_establishes_found(monkeypatch):
    kb, requests = install_ghsa_services(
        monkeypatch, primary=[],
        ghsa=[{"source": "ghsa", "cve_refs": [CVE], "text": "GHSA_ALONE_MARKER"}],
    )
    result = retrieve(kb)
    assert result == {"status": "not_found", "content": "", "cwe_refs": []}
    assert not any("/collections/ghsa-lab/" in url for url, _ in requests)


def test_supplemental_collection_names_are_deduplicated_and_primary_is_excluded(monkeypatch):
    kb, requests = install_ghsa_services(
        monkeypatch, primary=[_primary_description()],
        ghsa=[{"source": "ghsa", "cve_refs": [CVE], "text": "DEDUPED_COLLECTION_MARKER"}],
        extra_collections=" ghsa-lab,primary-cve,ghsa-lab, , ghsa-lab ",
    )
    result = retrieve(kb)
    ghsa_requests = [(url, body) for url, body in requests if "/collections/ghsa-lab/" in url]
    assert result["status"] == "found"
    assert len(ghsa_requests) == 1
    primary_requests = [(url, body) for url, body in requests if "/collections/primary-cve/" in url]
    assert len(primary_requests) == 1
