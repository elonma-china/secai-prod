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


def test_fetch_withholds_source_for_non_redistributable(tmp_path: Path) -> None:
    # A source with redistributable=False (e.g. raw GitHub PoC, unresolved upstream license)
    # must still return the static_indicators evidence, but source_code stays null — never
    # fabricated as a substitute.
    from secai_gateway.poc.models import NormalizedPoc

    root = tmp_path / "poc_github"
    (root / "aa").mkdir(parents=True)
    (root / "aa" / "deadbeef").write_text("import socket\n# unresolved-license body\n", encoding="utf-8")
    cat = PocCatalog(tmp_path / "cat2.db")
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="octocat/demo", kind="exploit", title="Demo CVE PoC",
        cve_ids=["CVE-2021-44228"], url="https://github.com/octocat/demo", license="varies",
        redistributable=False, artifact_sha256="deadbeef", artifact_path="aa/deadbeef",
        language="py"))
    cat.commit()
    be = LocalPocBackend(catalog=cat, source_roots={"poc_github": root})
    ref = asyncio.run(be.search("CVE-2021-44228", 10, "run1"))["hits"][0]["ref"]
    fetched = asyncio.run(be.fetch(ref, "run1"))
    assert fetched["source_code"] is None
    assert "unresolved-license body" not in json.dumps(fetched)
    si = fetched["static_indicators"]
    assert si["network"]["socket_or_http"] >= 1  # evidence still present, just no raw code


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
