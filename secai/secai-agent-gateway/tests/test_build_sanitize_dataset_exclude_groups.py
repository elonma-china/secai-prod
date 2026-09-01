"""build_sanitize_dataset.py --exclude-groups / build_rows(exclude_groups=...) — Ư3 LODO flag.

Fast, no-embedding tests for the row-filtering mechanism itself (the slow retrain-based LODO
experiment lives in scripts/eval_lodo_family_holdout.py, run offline on demand — see
evals/sanitize/report-lodo-2026-09-01.md for its results).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from build_sanitize_dataset import build_rows  # noqa: E402


def test_exclude_groups_drops_the_family_entirely():
    excluded = build_rows(exclude_groups=frozenset({"hnreport"}))
    assert not any(r["group"].startswith("hnreport") for r in excluded)


def test_exclude_groups_is_deterministic():
    # NOTE: excluding a family does NOT leave other families byte-identical — build_rows()
    # threads ONE shared `rng` through a sequential build, and the CATCAP/train-test-split loop
    # iterates `by_cat_group` (a dict keyed by category, in first-occurrence insertion order).
    # Removing hnreport removes its dict key entirely, so every rng.shuffle() call for a
    # category that comes after it draws a DIFFERENT sequence than in the unexcluded run — a
    # butterfly effect that cascades into which rows survive CATCAP caps and which VI rows get
    # a no-diacritics/teencode variant. This is still fully deterministic (same exclude_groups
    # -> same output every time, verified below) and doesn't affect the LODO experiment's
    # validity (scripts/eval_lodo_family_holdout.py trains on whatever this produces and scores
    # against a SEPARATE, unaffected held-out file) — it just means "other families are
    # unaffected content-for-content" is NOT a valid invariant to assert.
    a = build_rows(exclude_groups=frozenset({"hnreport"}))
    b = build_rows(exclude_groups=frozenset({"hnreport"}))
    assert a == b


def test_no_exclusion_matches_default_build():
    assert build_rows(exclude_groups=frozenset()) == build_rows()


def test_exclude_multiple_groups():
    excluded = build_rows(exclude_groups=frozenset({"hnreport", "hncve"}))
    assert not any(r["group"].startswith(("hnreport", "hncve")) for r in excluded)
    assert any(r["group"].startswith("hnaudit") for r in excluded)  # untouched families remain


def test_exclude_unknown_group_is_a_noop():
    assert build_rows(exclude_groups=frozenset({"not-a-real-group"})) == build_rows()
