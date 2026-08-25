"""Target-scoped verified-good (fixed) PoC store — poc/fixed_pocs.py."""
from __future__ import annotations

import json

import pytest

from secai_gateway.poc.fixed_pocs import (
    _split_target,
    extract_target,
    load_fixed,
    save_fixed,
)


def _write(tmp_path, name: str, code: str, targets=None):
    path = tmp_path / name
    ev = {"source_code": code, "cve_ids": [], "title": name}
    if targets is not None:
        ev["targets"] = targets
    path.write_text(json.dumps(ev), encoding="utf-8")
    return path


def test_extract_target_requires_port_or_scheme():
    assert extract_target("khai thác 46.250.247.218:8094") == "46.250.247.218:8094"
    assert extract_target("target http://46.250.247.218:8094/path") == "46.250.247.218:8094"
    assert extract_target("http://lab.example.com") == "lab.example.com"
    assert extract_target("lab.example.com:443") == "lab.example.com:443"
    # bare dotted quad (version lookalike) must NOT be mistaken for a target
    assert extract_target("nginx plus 37.0.3.1") is None
    assert extract_target("CVE-2026-42533") is None


def test_split_target():
    assert _split_target("46.250.247.218:8094") == ("46.250.247.218", "8094")
    assert _split_target("http://46.250.247.218:8094/x") == ("46.250.247.218", "8094")
    assert _split_target("46.250.247.218") == ("46.250.247.218", None)
    assert _split_target("[::1]:8080") == ("::1", "8080")
    assert _split_target("not a target with:bad") == (None, None)
    assert _split_target("") == (None, None)


def test_unscoped_default_matches_any_target(tmp_path):
    _write(tmp_path, "CVE-2026-45504.json", "print('x')")
    assert load_fixed(tmp_path, "CVE-2026-45504", "94.72.105.201:443") is not None
    assert load_fixed(tmp_path, "CVE-2026-45504", None) is not None


def test_scoped_default_falls_through_on_other_target(tmp_path):
    _write(tmp_path, "CVE-2026-42533.json", "rift", targets=["46.250.247.218:29443"])
    assert load_fixed(tmp_path, "CVE-2026-42533", "46.250.247.218:29443") is not None
    # a different lab's target must NOT win the scoped default
    assert load_fixed(tmp_path, "CVE-2026-42533", "46.250.247.218:8094") is None
    # no target → generic default still returned
    assert load_fixed(tmp_path, "CVE-2026-42533", None) is not None


def test_variant_wins_for_its_target(tmp_path):
    _write(tmp_path, "CVE-2026-42533.json", "rift", targets=["46.250.247.218:29443"])
    _write(tmp_path, "CVE-2026-42533@46.250.247.218_8094.json", "imbas007",
           targets=["46.250.247.218:8094"])

    ev_default = load_fixed(tmp_path, "CVE-2026-42533", "46.250.247.218:29443")
    assert ev_default["source_code"] == "rift"

    ev_var = load_fixed(tmp_path, "CVE-2026-42533", "46.250.247.218:8094")
    assert ev_var["source_code"] == "imbas007"

    # host-only request still matches the host:port variant via its `targets` field
    ev_var_host = load_fixed(tmp_path, "CVE-2026-42533", "46.250.247.218")
    assert ev_var_host is not None


def test_variant_matches_other_port_in_targets(tmp_path):
    _write(tmp_path, "CVE-2026-42533.json", "rift", targets=["46.250.247.218:29443"])
    _write(tmp_path, "CVE-2026-42533@46.250.247.218_8094.json", "imbas007",
           targets=["46.250.247.218:8094", "46.250.247.218:8095"])
    assert load_fixed(tmp_path, "CVE-2026-42533", "46.250.247.218:8095")["source_code"] == "imbas007"


def test_missing_and_invalid(tmp_path):
    assert load_fixed(tmp_path, "CVE-2026-99999", "46.250.247.218:8094") is None
    assert load_fixed(None, "CVE-2026-42533", None) is None
    # corrupt file is a miss (fail-open)
    (tmp_path / "CVE-2026-42533.json").write_text("{not json", encoding="utf-8")
    assert load_fixed(tmp_path, "CVE-2026-42533", None) is None


def test_save_fixed_variant_and_roundtrip(tmp_path):
    assert save_fixed(tmp_path, "CVE-2026-42533", "code", target="46.250.247.218:8094")
    ev = load_fixed(tmp_path, "CVE-2026-42533", "46.250.247.218:8094")
    assert ev is not None and ev["source_code"] == "code"
    assert ev["targets"] == ["46.250.247.218:8094"]
    assert ev["fixed"] is True and ev["consolidated"] is True


def test_save_fixed_default_unscoped(tmp_path):
    assert save_fixed(tmp_path, "CVE-2026-45504", "code")
    ev = load_fixed(tmp_path, "CVE-2026-45504", "94.72.105.201:443")
    assert ev is not None and ev["source_code"] == "code"
    assert not ev.get("targets")


def test_save_fixed_rejects_targets_without_target(tmp_path):
    """Regression guard for target-scope-bypass (2026-08 research pass): targets= without a
    paired target= would write a SCOPED targets list into the DEFAULT (unscoped) file path —
    then load_fixed's `if not scoped or host is None: return default` serves it to EVERY
    target-less request regardless of that scope, defeating the point of scoping it at all."""
    assert save_fixed(tmp_path, "CVE-2026-11111", "code", targets=["1.2.3.4:80"]) is False
    assert load_fixed(tmp_path, "CVE-2026-11111") is None  # nothing was written
    # the normal target-specific-variant path (target= alone) is unaffected
    assert save_fixed(tmp_path, "CVE-2026-11111", "code", target="1.2.3.4:80") is True
    assert load_fixed(tmp_path, "CVE-2026-11111", "1.2.3.4:80") is not None
