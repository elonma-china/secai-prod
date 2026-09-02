#!/usr/bin/env python3
"""Offline unit + dedup tests for sync_autoscan_poc.py.

No network, no live catalog. Loads the script by file path (importlib) and exercises
``normalize_record()`` against the saved AutoScan API fixtures in tests/fixtures/, plus a
real (temp-file) ``PocCatalog`` round-trip proving the dedup invariant: upserting the same
``NormalizedPoc`` twice never produces a second ``poc`` row or a duplicate ``poc_cve`` link.
Run with:

    cd /home/ict/secai-autoscan-wt && python3 -m pytest secai/scripts/tests/test_sync_autoscan_poc.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

GATEWAY_SRC = "/home/ict/AIEngines/secai/secai-agent-gateway/src"
if GATEWAY_SRC not in sys.path:
    sys.path.insert(0, GATEWAY_SRC)

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sync_autoscan_poc.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_autoscan_poc", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


sync = _load_module()

LIST_PAGE = _load_fixture("exploitdb_list_page.json")
DETAIL = _load_fixture("exploitdb_detail.json")

# results[0] in exploitdb_list_page.json is edb_id 52666, the SAME exploit as
# exploitdb_detail.json (verified by hand against the live API 2026-09-02).
LIST_RECORD = LIST_PAGE["results"][0]
assert LIST_RECORD["edb_id"] == DETAIL["edb_id"] == 52666


# ── normalize_record: list-only shape ────────────────────────────────────────────
def test_normalize_record_list_only_maps_core_fields():
    poc = sync.normalize_record(LIST_RECORD)
    assert poc.source == "autoscan-exploitdb"  # dedicated label, isolated from git-collector "exploitdb"
    assert poc.source_ref == "52666"
    assert poc.kind == "exploit"
    assert poc.title == "C-MOR  6.0104 - Directory Traversal"
    assert poc.description == poc.title
    assert poc.verified is False
    assert poc.url == "https://www.exploit-db.com/exploits/52666"
    assert poc.redistributable is True
    assert poc.license == "Exploit-DB terms; verify artifact license"
    assert poc.date_published == "2026-08-31"
    assert poc.provenance == {
        "collector": "autoscan-exploitdb",
        "edb_id": 52666,
        "raw_file_url": (
            "https://gitlab.com/exploit-database/exploitdb/-/raw/main/"
            "exploits/multiple/webapps/52666.txt"
        ),
        # list-only record has no updated_at/_wm → source stamp falls back to date_published
        "src_updated_at": "2026-08-31",
    }


def test_normalize_record_list_only_has_no_cve_ids_or_language():
    # The list endpoint carries neither `cve_ids` nor `file` — both are detail-only.
    poc = sync.normalize_record(LIST_RECORD)
    assert poc.cve_ids == []
    assert poc.language is None


# ── normalize_record: list + detail ──────────────────────────────────────────────
def test_normalize_record_with_detail_adds_cve_ids_and_language():
    poc = sync.normalize_record(LIST_RECORD, DETAIL)
    assert poc.source == "autoscan-exploitdb"
    assert poc.source_ref == "52666"
    assert poc.cve_ids == ["CVE-2026-51134"]
    # language = suffix of detail["file"] = "exploits/multiple/webapps/52666.txt" -> "txt"
    assert poc.language == "txt"
    assert poc.verified is False
    assert poc.title == "C-MOR  6.0104 - Directory Traversal"


def test_normalize_record_cve_ids_are_normalized_upper_and_deduped():
    detail = {**DETAIL, "cve_ids": ["cve-2026-51134", "CVE-2026-51134", "not-a-cve"]}
    poc = sync.normalize_record(LIST_RECORD, detail)
    assert poc.cve_ids == ["CVE-2026-51134"]


def test_normalize_record_platform_prefers_type_over_platform_field():
    # fixture: type="webapps", platform="multiple" -> mapping convention prefers `type`.
    poc = sync.normalize_record(LIST_RECORD)
    assert poc.platform == "webapps"


def test_normalize_record_missing_description_falls_back_to_untitled():
    rec = {**LIST_RECORD, "description": None}
    poc = sync.normalize_record(rec)
    assert poc.title == "Untitled"
    assert poc.description == "Untitled"


# ── wm_of (watermark field selection) ────────────────────────────────────────────
def test_wm_of_prefers_updated_at_over_date_published():
    rec = {"updated_at": "2026-08-31T04:00:00Z", "date_published": "2026-08-30"}
    assert sync.wm_of(rec) == "2026-08-31T04:00:00Z"


def test_wm_of_falls_back_to_date_published_when_no_updated_at():
    assert sync.wm_of({"date_published": "2026-08-30"}) == "2026-08-30"


def test_wm_of_empty_record_returns_empty_string():
    assert sync.wm_of({}) == ""


# ── dedup: upserting the SAME NormalizedPoc twice must not duplicate rows ───────
def test_dedup_upsert_same_poc_twice_is_idempotent(tmp_path):
    db_path = tmp_path / "catalog.db"
    catalog = sync.PocCatalog(db_path)
    poc = sync.normalize_record(LIST_RECORD, DETAIL)

    catalog.upsert(poc)
    catalog.commit()
    catalog.upsert(poc)  # re-run: same (source, source_ref) -> ON CONFLICT DO UPDATE
    catalog.commit()

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM poc").fetchone()[0] == 1
        poc_id = conn.execute(
            "SELECT id FROM poc WHERE source=? AND source_ref=?", (poc.source, poc.source_ref),
        ).fetchone()[0]
        cve_rows = conn.execute("SELECT cve_id FROM poc_cve WHERE poc_id=?", (poc_id,)).fetchall()
        assert [r[0] for r in cve_rows] == ["CVE-2026-51134"]  # linked once, not duplicated
    finally:
        conn.close()


def test_dedup_is_isolated_from_git_collector_exploitdb_source(tmp_path):
    """Same numeric edb_id as a hypothetical git-collector `exploitdb` row must NOT
    collide with our `autoscan-exploitdb` row — UNIQUE(source, source_ref) makes `source`
    part of the key, so the two labels are fully isolated rows."""
    db_path = tmp_path / "catalog.db"
    catalog = sync.PocCatalog(db_path)
    git_collector_poc = sync.NormalizedPoc(
        source="exploitdb", source_ref="52666", kind="exploit", title="git-collector row",
    )
    autoscan_poc = sync.normalize_record(LIST_RECORD, DETAIL)

    catalog.upsert(git_collector_poc)
    catalog.upsert(autoscan_poc)
    catalog.commit()

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) FROM poc").fetchone()[0] == 2
        rows = {(r[0], r[1]) for r in conn.execute("SELECT source, title FROM poc").fetchall()}
        assert ("exploitdb", "git-collector row") in rows
        assert ("autoscan-exploitdb", "C-MOR  6.0104 - Directory Traversal") in rows
    finally:
        conn.close()


# ── load_existing (skip-existing lookup) ─────────────────────────────────────────
def test_load_existing_missing_db_returns_empty(tmp_path):
    assert sync.load_existing(tmp_path / "does-not-exist.db") == {}


def test_load_existing_reports_cve_count_per_source_ref(tmp_path):
    db_path = tmp_path / "catalog.db"
    catalog = sync.PocCatalog(db_path)
    catalog.upsert(sync.normalize_record(LIST_RECORD, DETAIL))  # has 1 cve link
    catalog.upsert(sync.NormalizedPoc(
        source="autoscan-exploitdb", source_ref="1", kind="exploit", title="no cves",
    ))
    catalog.commit()

    existing = sync.load_existing(db_path)
    assert existing["52666"]["cve_count"] == 1
    assert existing["1"]["cve_count"] == 0
    assert "999" not in existing


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ── refresh-if-newer decision (default) ───────────────────────────────────────
def test_poc_needs_write_new_row():
    assert sync._poc_needs_write(None, "2026-08-31", True, no_refresh=False) is True


def test_poc_needs_write_source_newer_refreshes():
    prior = {"cve_count": 1, "src_updated_at": "2026-08-30T00:00:00Z"}
    assert sync._poc_needs_write(prior, "2026-08-31T00:00:00Z", True, no_refresh=False) is True


def test_poc_needs_write_unchanged_skips():
    prior = {"cve_count": 1, "src_updated_at": "2026-08-31T00:00:00Z"}
    assert sync._poc_needs_write(prior, "2026-08-31T00:00:00Z", True, no_refresh=False) is False


def test_poc_needs_write_backfills_missing_cve_links():
    prior = {"cve_count": 0, "src_updated_at": "2026-08-31T00:00:00Z"}
    assert sync._poc_needs_write(prior, "2026-08-31T00:00:00Z", True, no_refresh=False) is True


def test_poc_needs_write_unstamped_legacy_adopted():
    prior = {"cve_count": 1, "src_updated_at": ""}
    assert sync._poc_needs_write(prior, "2026-08-31T00:00:00Z", True, no_refresh=False) is True


def test_poc_needs_write_no_refresh_skips_existing():
    prior = {"cve_count": 0, "src_updated_at": ""}
    assert sync._poc_needs_write(prior, "2026-08-31T00:00:00Z", True, no_refresh=True) is False


def test_provenance_carries_src_updated_at():
    rec = dict(LIST_PAGE["results"][0]); rec["_wm"] = "2026-08-31T04:00:00Z"
    poc = sync.normalize_record(rec)
    assert poc.provenance.get("src_updated_at") == "2026-08-31T04:00:00Z"
