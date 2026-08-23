"""CVE KB payload overrides — CWE/product/KEV corrections."""
from __future__ import annotations

from secai_gateway.retrieval.cve_overrides import apply_cve_override


def test_unknown_cve_passthrough() -> None:
    pl = {"cve_id": "CVE-2026-99999", "cwe_refs": ["CWE-89"], "text": "SQL injection"}
    assert apply_cve_override(pl) is pl


def test_empty_payload() -> None:
    assert apply_cve_override(None) == {}
    assert apply_cve_override({}) == {}


def test_yellowkey_cwe77_becomes_sfb() -> None:
    pl = {
        "cve_id": "CVE-2026-45585",
        "cwe_refs": ["CWE-77"],
        "text": "CVE ID: CVE-2026-45585 | CWE Weaknesses: CWE-77 | Description: YellowKey",
    }
    out = apply_cve_override(pl)
    assert out["cwe_refs"] == ["CWE-693"]
    assert "CWE-77" not in out["text"]
    assert "CWE Weaknesses: CWE-693" in out["text"]
    assert pl["cwe_refs"] == ["CWE-77"]  # input not mutated
    assert apply_cve_override(out)["cwe_refs"] == ["CWE-693"]  # idempotent


def test_dotnet_drops_linux_kernel() -> None:
    pl = {
        "cve_id": "CVE-2026-26127",
        "products": ["macos", "linux_kernel", ".net", "windows"],
        "cwe_refs": ["CWE-125"],
        "text": "CVE ID: CVE-2026-26127 | Affected Products: macos, linux_kernel, .net, windows",
    }
    out = apply_cve_override(pl)
    assert "linux_kernel" not in out["products"]
    assert ".net" in out["products"]
    assert "linux_kernel" not in out["text"]


def test_httpsys_and_aug_incomplete_fields() -> None:
    http = apply_cve_override({
        "cve_id": "CVE-2026-49160",
        "products": ["windows_11_24h2"],
        "cwe_refs": ["CWE-400"],
        "text": "CVE ID: CVE-2026-49160 | Affected Products: windows_11_24h2",
    })
    assert "http.sys" in http["products"]
    assert "http.sys" in http["text"]

    eop = apply_cve_override({
        "cve_id": "CVE-2026-62832",
        "cwe_refs": [],
        "products": [],
        "text": "CVE ID: CVE-2026-62832 | Description: link following in User Profile Service",
    })
    assert eop["cwe_refs"] == ["CWE-59"]
    assert "user_profile_service" in eop["products"]
    assert "CWE Weaknesses: CWE-59" in eop["text"]

    afd = apply_cve_override({
        "cve_id": "CVE-2026-68820",
        "cwe_refs": ["CWE-416"],
        "products": [],
        "is_in_kev": False,
        "text": "CVE ID: CVE-2026-68820 | CWE Weaknesses: CWE-416 | Description: use after free",
    })
    assert afd["is_in_kev"] is True
    assert "afd.sys" in afd["products"]
    assert "KEV: Known Exploited Vulnerability" in afd["text"]
    # second pass must not duplicate the KEV clause
    again = apply_cve_override(afd)
    assert again["text"].count("KEV:") == 1
