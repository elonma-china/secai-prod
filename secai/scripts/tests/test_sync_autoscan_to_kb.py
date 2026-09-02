#!/usr/bin/env python3
"""Offline unit tests for sync_autoscan_to_kb.py.

No network, no Qdrant, no embed endpoint, no Neo4j — pure function tests against the
saved AutoScan API fixtures in tests/fixtures/. Run with:

    cd /home/ict/secai-autoscan-wt && python3 -m pytest secai/scripts/tests/test_sync_autoscan_to_kb.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest

# The gateway's src/ is not needed by this script directly (no secai_gateway import at
# module scope), but the task spec asks for it on sys.path defensively in case that
# ever changes.
GATEWAY_SRC = "/home/ict/AIEngines/secai/secai-agent-gateway/src"
if GATEWAY_SRC not in sys.path:
    sys.path.insert(0, GATEWAY_SRC)

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "sync_autoscan_to_kb.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_autoscan_to_kb", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


sync = _load_module()

LIST_PAGE = _load_fixture("cve_list_page.json")
DETAIL = _load_fixture("cve_detail.json")

# results[0] in cve_list_page.json: CVE-2026-84442, minimal "/api/cve" list-row shape
# (no parsed_weaknesses/parsed_vendors/title/has_public_exploit — those only show up on
# the fuller detail record). Good coverage for the fallback paths.
LIST_RECORD = LIST_PAGE["results"][0]


# ── _pick_cvss ──────────────────────────────────────────────────────────────────
def test_pick_cvss_prefers_v4_when_present():
    score, vector = sync._pick_cvss(DETAIL["metrics"])
    assert score == 8.7
    assert vector.startswith("CVSS:4.0")


def test_pick_cvss_falls_back_to_v3_1_when_v4_missing():
    metrics = {
        "cvssV4_0": {"data": {}, "provider": None},
        "cvssV3_1": {"data": {"score": 4.3, "vector": "CVSS:3.1/AV:A"}, "provider": "vulnrichment"},
        "cvssV3_0": {"data": {"score": 9.9, "vector": "CVSS:3.0/AV:N"}, "provider": "mitre"},
        "cvssV2_0": {"data": {"score": 1.0, "vector": "AV:L"}, "provider": "mitre"},
    }
    score, vector = sync._pick_cvss(metrics)
    assert score == 4.3
    assert vector == "CVSS:3.1/AV:A"


def test_pick_cvss_falls_back_to_v3_0_when_v4_and_v3_1_missing():
    metrics = {
        "cvssV4_0": {"data": {}, "provider": None},
        "cvssV3_1": {"data": {}, "provider": None},
        "cvssV3_0": {"data": {"score": 5.5, "vector": "CVSS:3.0/AV:N"}, "provider": "mitre"},
        "cvssV2_0": {"data": {"score": 1.0, "vector": "AV:L"}, "provider": "mitre"},
    }
    score, vector = sync._pick_cvss(metrics)
    assert score == 5.5
    assert vector == "CVSS:3.0/AV:N"


def test_pick_cvss_falls_back_to_v2_0_when_only_it_present():
    metrics = {
        "cvssV4_0": {"data": {}, "provider": None},
        "cvssV3_1": {"data": {}, "provider": None},
        "cvssV3_0": {"data": {}, "provider": None},
        "cvssV2_0": {"data": {"score": 3.2, "vector": "AV:L/AC:L"}, "provider": "mitre"},
    }
    score, vector = sync._pick_cvss(metrics)
    assert score == 3.2
    assert vector == "AV:L/AC:L"


def test_pick_cvss_all_missing_returns_none_and_empty_vector():
    metrics = {
        "cvssV4_0": {"data": {}, "provider": None},
        "cvssV3_1": {"data": {}, "provider": None},
        "cvssV3_0": {"data": {}, "provider": None},
        "cvssV2_0": {"data": {}, "provider": None},
    }
    score, vector = sync._pick_cvss(metrics)
    assert score is None
    assert vector == ""


def test_pick_cvss_handles_missing_or_none_metrics():
    assert sync._pick_cvss({}) == (None, "")
    assert sync._pick_cvss(None) == (None, "")


def test_pick_cvss_non_numeric_score_is_none_but_vector_kept():
    metrics = {"cvssV4_0": {"data": {"score": "n/a", "vector": "CVSS:4.0/junk"}, "provider": None}}
    score, vector = sync._pick_cvss(metrics)
    assert score is None
    assert vector == "CVSS:4.0/junk"


# ── normalize ───────────────────────────────────────────────────────────────────
def test_normalize_detail_record_full_mapping():
    row = sync.normalize(DETAIL)
    assert row["cve_id"] == "CVE-2026-13518"
    assert row["title"] == "Tenda JD12L addressNat fromAddressNat stack-based overflow"
    # CWE from parsed_weaknesses keys, uppercased, CWE-* only, in dict order.
    assert row["cwes"] == ["CWE-119", "CWE-121"]
    # products/vendors flattened out of parsed_vendors {vendor: [products...]}.
    assert row["vendors"] == ["tenda"]
    assert row["products"] == ["jd12l"]
    # metrics.kev.data == {} -> falsy -> not in KEV.
    assert row["is_in_kev"] is False
    # metrics.epss.data.score
    assert row["epss_score"] == pytest.approx(0.00854)
    assert row["has_public_exploit"] is False
    # cvssV4_0 present (score=8.7) -> preferred over the higher V3_1 score.
    assert row["cvss_score"] == 8.7
    assert row["cvss_vector"].startswith("CVSS:4.0")
    assert row["updated_at"] == "2026-06-30T17:57:37Z"
    assert row["published_date"] == "2026-06-29T00:15:08Z"


def test_normalize_list_record_falls_back_to_flat_weaknesses_and_empty_products():
    row = sync.normalize(LIST_RECORD)
    assert row["cve_id"] == "CVE-2026-84442"
    # No parsed_weaknesses on the list-row shape -> fall back to flat "weaknesses".
    assert row["cwes"] == ["CWE-22"]
    # No parsed_vendors on the list-row shape (only flat "vendors", which normalize()
    # does not consume for products) -> empty.
    assert row["products"] == []
    assert row["vendors"] == []
    # No "title" key at all on this record shape.
    assert row["title"] == ""
    # metrics.kev.data == {} -> not in KEV; metrics.epss.data == {} -> no score.
    assert row["is_in_kev"] is False
    assert row["epss_score"] is None
    assert row["has_public_exploit"] is False
    # All four CVSS schemes present -> V4 (score=4.8) wins.
    assert row["cvss_score"] == 4.8
    assert row["cvss_vector"].startswith("CVSS:4.0")


def test_normalize_cwe_ids_are_uppercased_and_filtered():
    rec = {
        "cve_id": "cve-2026-00001",
        "parsed_weaknesses": {"cwe-79": None, "not-a-cwe": None, "CWE-89": None},
        "metrics": {},
    }
    row = sync.normalize(rec)
    assert row["cve_id"] == "CVE-2026-00001"
    assert row["cwes"] == ["CWE-79", "CWE-89"]


# ── build_chunks ────────────────────────────────────────────────────────────────
REQUIRED_PAYLOAD_KEYS = {
    "cve_id", "chunk_kind", "kind", "cwe_refs", "capec_entries", "mitre_techniques",
    "title", "severity", "cvss_score", "is_in_kev", "year", "products", "text",
}


def test_build_chunks_produces_exactly_two_chunks():
    row = sync.normalize(DETAIL)
    chunks = sync.build_chunks(row)
    assert len(chunks) == 2
    kinds = [c["chunk_kind"] for c in chunks]
    assert kinds == ["core", "context"]


def test_build_chunks_payload_has_required_gateway_reader_keys():
    row = sync.normalize(DETAIL)
    chunks = sync.build_chunks(row)
    for chunk in chunks:
        missing = REQUIRED_PAYLOAD_KEYS - chunk.keys()
        assert not missing, f"chunk {chunk.get('chunk_id')} missing payload keys: {missing}"


def test_build_chunks_field_values_match_row():
    row = sync.normalize(DETAIL)
    core, context = sync.build_chunks(row)

    assert core["cve_id"] == "CVE-2026-13518"
    assert core["year"] == "2026"
    assert core["cvss_score"] == 8.7
    assert core["severity"] == "HIGH"
    assert core["cwe_refs"] == ["CWE-119", "CWE-121"]
    assert core["products"] == ["jd12l"]
    assert core["is_in_kev"] is False
    assert core["title"] == "Tenda JD12L addressNat fromAddressNat stack-based overflow"
    assert core["epss_score"] == pytest.approx(0.00854)
    assert core["has_public_exploit"] is False
    assert "CVE-2026-13518" in core["text"]
    assert "CVE-2026-13518" in context["text"]
    # kind mirrors chunk_kind (gateway reader checks kind first, chunk_kind fallback).
    assert core["kind"] == core["chunk_kind"] == "core"
    assert context["kind"] == context["chunk_kind"] == "context"


def test_build_chunks_chunk_id_and_point_id_are_stable_per_invariant():
    row = sync.normalize(DETAIL)
    cve = row["cve_id"]
    chunks_a = sync.build_chunks(row)
    chunks_b = sync.build_chunks(row)

    for kind in ("core", "context"):
        chunk_a = next(c for c in chunks_a if c["chunk_kind"] == kind)
        chunk_b = next(c for c in chunks_b if c["chunk_kind"] == kind)
        expected_chunk_id = f"{cve}::{kind}"
        assert chunk_a["chunk_id"] == expected_chunk_id
        assert chunk_b["chunk_id"] == expected_chunk_id

        # point id = uuid5(NAMESPACE_DNS, "{CVE}::{chunk_kind}") — the exact dedup
        # invariant re-runs rely on for idempotency.
        expected_point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, expected_chunk_id))
        assert str(uuid.uuid5(sync.NS, chunk_a["chunk_id"])) == expected_point_id
        assert str(uuid.uuid5(sync.NS, chunk_b["chunk_id"])) == expected_point_id


def test_build_chunks_context_lists_products_and_severity_unknown_without_score():
    row = sync.normalize(LIST_RECORD)
    # LIST_RECORD has no parsed_vendors -> no products, but cvss should still resolve.
    core, context = sync.build_chunks(row)
    assert core["products"] == []
    assert core["severity"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"}

    # A row with no usable CVSS score anywhere maps to UNKNOWN severity.
    no_score_row = dict(row)
    no_score_row["cvss_score"] = None
    core2, _ = sync.build_chunks(no_score_row)
    assert core2["severity"] == "UNKNOWN"


# ── severity_of (small helper exercised indirectly above; direct coverage too) ──
@pytest.mark.parametrize(
    "score,expected",
    [
        (None, "UNKNOWN"),
        (0.0, "UNKNOWN"),
        (0.1, "LOW"),
        (3.9, "LOW"),
        (4.0, "MEDIUM"),
        (6.9, "MEDIUM"),
        (7.0, "HIGH"),
        (8.9, "HIGH"),
        (9.0, "CRITICAL"),
        (10.0, "CRITICAL"),
    ],
)
def test_severity_of_thresholds(score, expected):
    assert sync.severity_of(score) == expected


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
