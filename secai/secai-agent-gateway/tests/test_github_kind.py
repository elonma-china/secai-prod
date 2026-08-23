"""GitHub PoC kind: writeup/hardening → advisory, else exploit."""
from __future__ import annotations

from secai_gateway.poc.collectors.github_kind import classify_github_poc_kind
from secai_gateway.poc.catalog import PocCatalog
from secai_gateway.poc.models import NormalizedPoc


def test_office_ole_writeup_is_advisory() -> None:
    assert classify_github_poc_kind(
        "CVE-2026-21509", "CVE-2026-21509 Research writeup",
        "sentinel-aidefense/CVE-2026-21509") == "advisory"


def test_bitlocker_hardening_is_advisory() -> None:
    assert classify_github_poc_kind("bitlocker-hardening", "", "alice/bitlocker-hardening") == "advisory"
    assert classify_github_poc_kind(
        "YellowKey-WinRE-Remediation", "Intune Remediation package",
        "bob/YellowKey-WinRE-Remediation") == "advisory"


def test_real_exploit_repo_stays_exploit() -> None:
    assert classify_github_poc_kind("CVE-2026-33825", "Defender EoP PoC", "alice/CVE-2026-33825") == "exploit"
    assert classify_github_poc_kind("", "", "alice/some-rce") == "exploit"


def test_catalog_advisory_kind_is_sticky(tmp_path) -> None:
    cat = PocCatalog(tmp_path / "cat.db")
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="sentinel/CVE-2026-21509", kind="exploit",
        title="CVE-2026-21509", description="Research writeup", cve_ids=["CVE-2026-21509"]))
    cat.commit()
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="sentinel/CVE-2026-21509", kind="advisory",
        title="CVE-2026-21509", description="Research writeup", cve_ids=["CVE-2026-21509"]))
    cat.commit()
    row = cat.get(1)
    assert row is not None and row["kind"] == "advisory"
    # trickest re-yield as exploit must not downgrade
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="sentinel/CVE-2026-21509", kind="exploit",
        title="CVE-2026-21509", description="", cve_ids=["CVE-2026-21509"]))
    cat.commit()
    row = cat.get(1)
    assert row is not None and row["kind"] == "advisory"
