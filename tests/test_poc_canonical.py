"""Canonical-PoC marking: search ordering, CVE-link guard, case-insensitive lookup."""
from __future__ import annotations

import pytest

from secai_gateway.poc.catalog import PocCatalog
from secai_gateway.poc.models import NormalizedPoc

CVE = "CVE-2017-5638"


def _poc(source_ref: str, *, rank: int, cve: str = CVE, language: str = "py") -> NormalizedPoc:
    return NormalizedPoc(
        source="poc_github", source_ref=source_ref, kind="exploit", title=source_ref,
        cve_ids=[cve], language=language, verified=True, rank=rank,
        artifact_sha256=(source_ref * 64)[:64], artifact_path=f"store/{source_ref}",
    )


@pytest.fixture()
def cat(tmp_path):
    c = PocCatalog(tmp_path / "catalog.db")
    c.upsert(_poc("aaa", rank=5))
    c.upsert(_poc("bbb", rank=9))
    c.upsert(_poc("ccc", rank=1, cve="CVE-2099-10001"))
    c.commit()
    return c


def test_canonical_marks_and_orders_first(cat):
    rows = cat.search(CVE, 10)
    assert rows[0]["title"] == "bbb" and rows[0]["is_canonical"] == 0  # rank desc, no canonical yet

    target_id = rows[0]["id"]
    assert cat.set_canonical(CVE, target_id, success_signal="uid=", target="127.0.0.1:1") is True
    assert cat.canonical_count() == 1

    rows = cat.search(CVE, 10)
    assert rows[0]["id"] == target_id
    assert rows[0]["is_canonical"] == 1  # canonical wins even though another row ranks higher


def test_canonical_lookup_is_case_insensitive(cat):
    target_id = cat.search(CVE, 10)[0]["id"]
    cat.set_canonical(CVE, target_id)
    assert cat.get_canonical_poc_id("cve-2017-5638") == target_id
    assert cat.get_canonical_poc_id(CVE.lower()) == target_id


def test_set_canonical_refuses_wrong_cve_link(cat):
    other = cat.search("CVE-2099-10001", 10)[0]["id"]
    assert cat.set_canonical(CVE, other) is False       # poc not linked to this CVE
    assert cat.set_canonical("not-a-cve", other) is False
    assert cat.get_canonical_poc_id("CVE-2099-10001") is None


def test_clear_canonical(cat):
    target_id = cat.search(CVE, 10)[0]["id"]
    cat.set_canonical(CVE, target_id)
    cat.clear_canonical(CVE)
    assert cat.get_canonical_poc_id(CVE) is None
    assert cat.canonical_count() == 0
