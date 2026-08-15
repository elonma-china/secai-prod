from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from secai_gateway.poc.backend import _MAX_FETCH_PER_RUN, LocalPocBackend, PocError
from secai_gateway.poc.catalog import PocCatalog
from secai_gateway.poc.collectors.exploitdb import ExploitDbCollector

_CSV_HEADER = ("id,file,description,date_published,author,type,platform,port,date_added,"
               "date_updated,verified,codes,tags,aliases,screenshot_url,application_url,source_url\n")


def _backend(tmp_path: Path) -> LocalPocBackend:
    root = tmp_path / "exploitdb"
    (root / "exploits" / "linux").mkdir(parents=True)
    (root / "exploits" / "linux" / "1.py").write_text(
        "import socket\n# private source body\n", encoding="utf-8")
    (root / "files_exploits.csv").write_text(
        _CSV_HEADER + "1,exploits/linux/1.py,Demo RCE,2021-01-01,x,remote,linux,,,,1,CVE-2021-44228,,,,,\n",
        encoding="utf-8")
    cat = PocCatalog(tmp_path / "cat.db")
    for poc in ExploitDbCollector(root).collect():
        cat.upsert(poc)
    cat.commit()
    return LocalPocBackend(catalog=cat, source_roots={"exploitdb": root})


def test_search_returns_opaque_ref_no_path() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        be = _backend(Path(d))
        hits = asyncio.run(be.search("CVE-2021-44228", 10, "run1"))["hits"]
        assert hits and hits[0]["ref"].startswith("poc:")
        assert "CVE-2021-44228" in hits[0]["cve_ids"]
        # the model sees only an opaque ref — no filesystem path (url is a public link, OK)
        assert "path" not in hits[0] and "artifact_path" not in hits[0]


def test_search_normalizes_malformed_cve(tmp_path: Path) -> None:
    """Separator/case typos still reach the CVE-exact path — a malformed id must return the
    same indexed PoC as the canonical form, not degrade to keyword search."""
    be = _backend(tmp_path)
    canonical = asyncio.run(be.search("CVE-2021-44228", 10, "r0"))["hits"]
    assert canonical, "sanity: canonical id indexes the demo PoC"
    for malformed in ("cve 2021 44228", "CVE2021-44228", "cve_2021_44228", "cVe-2021 44228"):
        hits = asyncio.run(be.search(malformed, 10, "r1"))["hits"]
        assert hits, malformed
        assert "CVE-2021-44228" in hits[0]["cve_ids"], malformed


def test_fetch_returns_source_for_redistributable(tmp_path: Path) -> None:
    # ExploitDbCollector marks redistributable=True → fetch grounds source_code in the
    # actual artifact text (never model-generated).
    be = _backend(tmp_path)
    ref = asyncio.run(be.search("CVE-2021-44228", 10, "run1"))["hits"][0]["ref"]
    fetched = asyncio.run(be.fetch(ref, "run1"))
    assert fetched["sha256"] and fetched["candidate_status"] == "draft_not_executed"
    si = fetched["static_indicators"]
    assert si["network"]["socket_or_http"] >= 1          # `import socket` in the demo PoC
    assert any("socket" in imp for imp in si["imports"])  # condensed-but-informative extraction
    assert fetched["description"] == "" or isinstance(fetched["description"], str)
    assert fetched["source_code"] == "import socket\n# private source body\n"
    assert fetched["code_truncated"] is False


def test_fetch_returns_source_for_sandbox_fetched_github(tmp_path: Path) -> None:
    # Sandbox-fetched GitHub PoC artifacts are returned as grounded source_code so the
    # SecAI LLM can generate from real PoC files (private lab). Never fabricated.
    from secai_gateway.poc.models import NormalizedPoc

    root = tmp_path / "poc_github"
    (root / "aa").mkdir(parents=True)
    body = "import socket\n# unresolved-license body\n"
    (root / "aa" / "deadbeef").write_text(body, encoding="utf-8")
    cat = PocCatalog(tmp_path / "cat2.db")
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="octocat/demo/poc.py", kind="exploit", title="Demo CVE PoC",
        cve_ids=["CVE-2021-44228"], url="https://github.com/octocat/demo/blob/main/poc.py",
        license="varies", redistributable=False, artifact_sha256="deadbeef",
        artifact_path="aa/deadbeef", language="py", verified=True))
    cat.commit()
    be = LocalPocBackend(catalog=cat, source_roots={"poc_github": root})
    ref = asyncio.run(be.search("CVE-2021-44228", 10, "run1"))["hits"][0]["ref"]
    fetched = asyncio.run(be.fetch(ref, "run1"))
    assert fetched["source_code"] == body
    assert fetched["code_truncated"] is False
    si = fetched["static_indicators"]
    assert si["network"]["socket_or_http"] >= 1


def test_fetch_metadata_only_when_no_artifact(tmp_path: Path) -> None:
    # Link-only GitHub rows (not yet fetched) stay metadata-only — no invented code.
    from secai_gateway.poc.models import NormalizedPoc

    cat = PocCatalog(tmp_path / "cat3.db")
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="octocat/demo", kind="exploit", title="Demo link only",
        cve_ids=["CVE-2021-44228"], url="https://github.com/octocat/demo", license="varies",
        redistributable=False, artifact_sha256=None, artifact_path=None))
    cat.commit()
    be = LocalPocBackend(catalog=cat, source_roots={"poc_github": tmp_path / "empty"})
    ref = asyncio.run(be.search("CVE-2021-44228", 10, "run1"))["hits"][0]["ref"]
    fetched = asyncio.run(be.fetch(ref, "run1"))
    assert fetched["source_code"] is None
    assert fetched["candidate_status"] == "metadata_only"

def test_fetch_budget_capped_in_code(tmp_path: Path) -> None:
    # Enforced in the backend, NOT the prompt — a model told "fetch max 2" via system prompt
    # can still ignore it; this cap can't be talked around regardless of what the model does.
    be = _backend(tmp_path)
    ref = asyncio.run(be.search("CVE-2021-44228", 10, "run1"))["hits"][0]["ref"]
    for _ in range(_MAX_FETCH_PER_RUN):
        asyncio.run(be.fetch(ref, "run1"))  # within budget — succeeds every time
    with pytest.raises(PocError, match="limit reached"):
        asyncio.run(be.fetch(ref, "run1"))  # one over budget — hard stop, not advisory
    # a different run_id has its own fresh budget (and its own ref, refs are run-scoped)
    ref2 = asyncio.run(be.search("CVE-2021-44228", 10, "run2"))["hits"][0]["ref"]
    asyncio.run(be.fetch(ref2, "run2"))


def test_fabricated_ref_rejected(tmp_path: Path) -> None:
    be = _backend(tmp_path)
    with pytest.raises(PocError):
        asyncio.run(be.fetch("poc:deadbeefdeadbeef", "run1"))


def test_ref_scoped_to_run(tmp_path: Path) -> None:
    be = _backend(tmp_path)
    ref = asyncio.run(be.search("CVE-2021-44228", 10, "runA"))["hits"][0]["ref"]
    with pytest.raises(PocError):
        asyncio.run(be.fetch(ref, "runB"))  # ref belongs to a different session


def test_search_prefers_exploit_kind_over_higher_rank_nuclei(tmp_path: Path) -> None:
    """CVE-exact search must surface kind=exploit before nuclei detection even when the
    nuclei row has a higher source-tier rank (the historical bury-GitHub-PoCs bug)."""
    from secai_gateway.poc.models import NormalizedPoc

    cat = PocCatalog(tmp_path / "rank.db")
    cat.upsert(NormalizedPoc(
        source="nuclei", source_ref="CVE-2021-44228", kind="detection",
        title="Nuclei detect", cve_ids=["CVE-2021-44228"], language="yaml",
        verified=True, rank=21, url="https://n/x.yaml", redistributable=True,
        artifact_path="http/cves/2021/CVE-2021-44228.yaml"))
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="alice/log4shell", kind="exploit",
        title="GitHub PoC", cve_ids=["CVE-2021-44228"], language="py",
        verified=False, rank=5, url="https://github.com/alice/log4shell",
        redistributable=False))
    cat.commit()
    be = LocalPocBackend(catalog=cat, source_roots={
        "nuclei": tmp_path / "nuclei", "poc_github": tmp_path / "gh"})
    hits = asyncio.run(be.search("CVE-2021-44228", 10, "run1"))["hits"]
    assert len(hits) >= 2
    assert hits[0]["kind"] == "exploit"
    assert hits[0]["source"] == "poc_github"
    assert hits[0].get("language") == "py"
    assert any(h["kind"] == "detection" for h in hits)


def test_prefetch_rank_prefers_github_then_exploit_over_nuclei() -> None:
    from secai_gateway.poc.prefetch import _rank

    nuclei = {"kind": "detection", "source": "nuclei", "verified": True}
    github = {"kind": "exploit", "source": "poc_github", "verified": False}
    edb = {"kind": "exploit", "source": "exploitdb", "verified": True}
    ranked = sorted([nuclei, github, edb], key=_rank)
    # GitHub PoCs are preferred within the exploit tier (they feed the consolidation step),
    # then other exploit sources, with detection templates (nuclei) always last.
    assert ranked[0]["source"] == "poc_github"
    assert ranked[1]["source"] == "exploitdb"
    assert ranked[2]["source"] == "nuclei"