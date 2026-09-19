"""Deterministic pre-fetch — the harness gathers evidence in plain Python, independent of
whether the model calls poc_search/poc_fetch itself. Prefers an exact CVE-ID match;
otherwise falls back to the same keyword FTS search poc_search already offers the model,
using the raw prompt text — so this now covers keyword/product-name questions too, not
just ones naming a CVE."""
from __future__ import annotations

import asyncio
from pathlib import Path

from secai_gateway.poc.backend import LocalPocBackend
from secai_gateway.poc.catalog import PocCatalog
from secai_gateway.poc.collectors.exploitdb import ExploitDbCollector
from secai_gateway.poc.models import NormalizedPoc
from secai_gateway.poc.prefetch import merge_evidence, prefetch_evidence
from secai_gateway.state import SqliteLedger

_CSV_HEADER = ("id,file,description,date_published,author,type,platform,port,date_added,"
               "date_updated,verified,codes,tags,aliases,screenshot_url,application_url,source_url\n")


def _exploitdb_backend(tmp_path: Path, csv_rows: str) -> LocalPocBackend:
    root = tmp_path / "exploitdb"
    (root / "exploits" / "linux").mkdir(parents=True)
    (root / "exploits" / "linux" / "1.py").write_text("import socket\n# real poc\n", encoding="utf-8")
    (root / "files_exploits.csv").write_text(_CSV_HEADER + csv_rows, encoding="utf-8")
    cat = PocCatalog(tmp_path / "cat.db")
    for poc in ExploitDbCollector(root).collect():
        cat.upsert(poc)
    cat.commit()
    return LocalPocBackend(catalog=cat, source_roots={"exploitdb": root})


def test_prefetch_finds_and_records_real_evidence_by_cve_id(tmp_path) -> None:
    be = _exploitdb_backend(tmp_path, "1,exploits/linux/1.py,Demo,2021-01-01,x,remote,linux,,,,1,CVE-2021-44228,,,,,\n")
    ledger = SqliteLedger(tmp_path / "ledger.db")
    report = asyncio.run(prefetch_evidence(be, ledger, "Cho tôi biết về CVE-2021-44228 và cách khai thác", "run1"))
    assert report["prefetched"] is True and report["query"] == "CVE-2021-44228" and report["fetched"] == 1
    ev = ledger.evidence_for_run("run1")
    assert len(ev) == 1
    assert ev[0]["source_code"] == "import socket\n# real poc\n"


def test_prefetch_noop_when_cve_not_in_catalog(tmp_path) -> None:
    be = _exploitdb_backend(tmp_path, "1,exploits/linux/1.py,Demo,2021-01-01,x,remote,linux,,,,1,CVE-2021-44228,,,,,\n")
    ledger = SqliteLedger(tmp_path / "ledger.db")
    report = asyncio.run(prefetch_evidence(be, ledger, "CVE-2099-99999", "run1"))
    assert report == {"prefetched": False, "reason": "no_hits", "query": "CVE-2099-99999"}
    assert ledger.evidence_for_run("run1") == []


# ── broadened: keyword/product-name queries with no CVE-ID at all ───────────────
def test_prefetch_falls_back_to_keyword_search_when_no_cve_in_prompt(tmp_path) -> None:
    # regression case: the live gateway's first version only fired on an explicit CVE-ID —
    # a keyword-only question like this one relied entirely on the model choosing to
    # search, which testing showed is unreliable. This is the fix: the harness tries the
    # SAME keyword FTS poc_search already offers, using the raw prompt text as-is.
    be = _exploitdb_backend(
        tmp_path, "1,exploits/linux/1.py,Langflow AI unauthenticated RCE exploit,"
                  "2025-01-01,x,webapps,python,,,,1,CVE-2025-3248,,,,,\n")
    ledger = SqliteLedger(tmp_path / "ledger.db")
    report = asyncio.run(prefetch_evidence(
        be, ledger, "PoC cho lỗ hổng RCE không cần xác thực trong Langflow AI, bản mới nhất", "run1"))
    assert report["prefetched"] is True and report["fetched"] == 1
    assert len(ledger.evidence_for_run("run1")) == 1


def test_prefetch_noop_when_keyword_search_matches_nothing(tmp_path) -> None:
    be = _exploitdb_backend(tmp_path, "1,exploits/linux/1.py,Demo,2021-01-01,x,remote,linux,,,,1,CVE-2021-44228,,,,,\n")
    ledger = SqliteLedger(tmp_path / "ledger.db")
    report = asyncio.run(prefetch_evidence(be, ledger, "hoàn toàn không liên quan gì cả", "run1"))
    assert report["prefetched"] is False and report["reason"] == "no_hits"
    assert ledger.evidence_for_run("run1") == []


def test_prefetch_noop_on_empty_prompt(tmp_path) -> None:
    be = _exploitdb_backend(tmp_path, "1,exploits/linux/1.py,Demo,2021-01-01,x,remote,linux,,,,1,CVE-2021-44228,,,,,\n")
    ledger = SqliteLedger(tmp_path / "ledger.db")
    report = asyncio.run(prefetch_evidence(be, ledger, "   ", "run1"))
    assert report == {"prefetched": False, "reason": "empty_prompt"}


def test_prefetch_caps_at_max_prefetch(tmp_path) -> None:
    root = tmp_path / "exploitdb"
    (root / "exploits" / "linux").mkdir(parents=True)
    rows = []
    for i in range(1, 5):
        (root / "exploits" / "linux" / f"{i}.py").write_text(f"# poc {i}\n", encoding="utf-8")
        rows.append(f"{i},exploits/linux/{i}.py,Demo {i},2021-01-01,x,remote,linux,,,,1,CVE-2021-44228,,,,,\n")
    (root / "files_exploits.csv").write_text(_CSV_HEADER + "".join(rows), encoding="utf-8")
    cat = PocCatalog(tmp_path / "cat.db")
    for poc in ExploitDbCollector(root).collect():
        cat.upsert(poc)
    cat.commit()
    be = LocalPocBackend(catalog=cat, source_roots={"exploitdb": root})
    ledger = SqliteLedger(tmp_path / "ledger.db")
    report = asyncio.run(prefetch_evidence(be, ledger, "CVE-2021-44228", "run1"))
    assert report["hits"] == 4 and report["fetched"] == 2   # 4 candidates, only top 2 fetched
    assert len(ledger.evidence_for_run("run1")) == 2


def test_prefetch_is_independent_of_model_tool_calls(tmp_path) -> None:
    # the whole point: this never touches an LLM/tool-call path — plain backend calls
    be = _exploitdb_backend(tmp_path, "1,exploits/linux/1.py,Demo,2021-01-01,x,remote,linux,,,,1,CVE-2021-44228,,,,,\n")
    ledger = SqliteLedger(tmp_path / "ledger.db")
    asyncio.run(prefetch_evidence(be, ledger, "CVE-2021-44228", "harness-run"))
    # evidence exists under the harness's own scratch run_id, no model/session involved at all
    assert len(ledger.evidence_for_run("harness-run")) == 1


# ── merge_evidence ───────────────────────────────────────────────────────────
def test_merge_evidence_dedupes_by_sha256() -> None:
    a = [{"sha256": "aaa", "source": "exploitdb"}]
    b = [{"sha256": "aaa", "source": "exploitdb"}, {"sha256": "bbb", "source": "nuclei"}]
    merged = merge_evidence(a, b)
    assert len(merged) == 2
    assert merged[0]["sha256"] == "aaa" and merged[0]["source"] == "exploitdb"  # first occurrence wins
    assert merged[1]["sha256"] == "bbb"


def test_merge_evidence_handles_missing_sha256_and_empty_groups() -> None:
    a = [{"source": "ghsa"}]   # no sha256 key — never deduped against
    b = []
    c = [{"sha256": "x"}]
    merged = merge_evidence(a, b, c)
    assert len(merged) == 2
