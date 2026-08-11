"""enforce_section1: 4-tier Section-1 dispatch (real code / synthesize-from-README /
synthesize-from-CVE-definition / not-found)."""
from __future__ import annotations

from secai_gateway.poc.inject import (
    _SYNTHESIS_BANNER,
    _SYNTHESIS_FOOTER_MARKER,
    _description_for,
    enforce_section1,
)

_MODEL_REPORT = "## 2. Tổng quan Lỗ hổng\n\n- mô tả\n"
_SYNTHESIZED_REPORT = (
    "## 1. Mã Khai thác (PoC) và Payload\n\n"
    "```python\nimport requests\nrequests.get('http://target/vuln')\n```\n\n"
    "## 2. Cách chạy PoC\n\n- lệnh\n\n"
    "## 3. Tổng quan Lỗ hổng\n\n- mô tả\n"
)
_EMPTY_SECTION1_REPORT = "## 1. Mã Khai thác (PoC) và Payload\n\n## 2. Tổng quan Lỗ hổng\n\n- mô tả\n"

_REAL_EVIDENCE = [{
    "source": "exploitdb", "source_ref": "51234", "sha256": "a" * 64,
    "source_code": "import socket\nprint('real exploit')\n",
    "static_indicators": {"language": "py"},
}]
_GHSA_DESCRIPTION_ONLY = [{
    "source": "ghsa", "source_ref": "GHSA-xxxx", "source_code": None,
    "description": "SSRF trong endpoint /admin/import cho phép đọc file nội bộ qua redirect.",
}]
_README_ONLY_SOURCE_FILES = [{
    "source": "poc_github", "source_ref": "foo/bar", "source_code": None,
    "source_files": [{"path": "README.md", "role": "readme", "code": "## Reproduce\n1. curl ...\n"}],
}]
_NO_EVIDENCE: list[dict] = []


# ── _description_for ─────────────────────────────────────────────────────────────
def test_description_for_reads_ghsa_style_description() -> None:
    assert _description_for(_GHSA_DESCRIPTION_ONLY[0]).startswith("SSRF trong endpoint")


def test_description_for_reads_readme_role_source_files() -> None:
    assert "Reproduce" in _description_for(_README_ONLY_SOURCE_FILES[0])


def test_description_for_empty_when_real_code_present() -> None:
    # real code already satisfies _blocks_for — _description_for must not double-count it.
    assert _description_for(_REAL_EVIDENCE[0]) == ""


def test_description_for_empty_with_nothing() -> None:
    assert _description_for({"source": "vulhub", "source_code": None}) == ""


# ── T1: real code always wins, model's own text discarded ───────────────────────
def test_tier1_real_code_unchanged_behavior() -> None:
    out, report = enforce_section1(_MODEL_REPORT, _REAL_EVIDENCE)
    assert report["section1_tier"] == "real"
    assert report["reference_code_injected"] is True
    assert "import socket\nprint('real exploit')" in out
    assert out.startswith("## 1. Mã Khai thác (PoC) và Payload")


def test_tier1_wins_even_with_cve_known_true() -> None:
    out, report = enforce_section1(_MODEL_REPORT, _REAL_EVIDENCE, cve_known=True, cve_id="CVE-2024-1234")
    assert report["section1_tier"] == "real"
    assert "import socket" in out


# ── T2: description/README evidence, model wrote Section 1 → banner enforced ────
def test_tier2_readme_evidence_keeps_model_code_with_banner() -> None:
    out, report = enforce_section1(_SYNTHESIZED_REPORT, _GHSA_DESCRIPTION_ONLY)
    assert report["section1_tier"] == "synthesized_readme"
    assert report["synthesized"] is True
    assert _SYNTHESIS_BANNER in out
    assert "requests.get('http://target/vuln')" in out
    # banner must be the very first thing in the Section-1 body
    body = out.split("## 1. Mã Khai thác (PoC) và Payload", 1)[1].lstrip()
    assert body.startswith(_SYNTHESIS_BANNER)


def test_tier2_readme_role_source_files_also_trigger_synthesis() -> None:
    out, report = enforce_section1(_SYNTHESIZED_REPORT, _README_ONLY_SOURCE_FILES)
    assert report["section1_tier"] == "synthesized_readme"
    assert _SYNTHESIS_BANNER in out


# ── T2/T3 fail-closed: evidence exists but model wrote nothing usable ───────────
def test_tier2_falls_back_to_synthesis_failed_when_model_wrote_no_section1() -> None:
    out, report = enforce_section1(_MODEL_REPORT, _GHSA_DESCRIPTION_ONLY)
    assert report["section1_tier"] == "synthesis_failed"
    assert "Không tổng hợp được PoC minh hoạ" in out
    assert "```" not in out
    # real evidence existed (GHSA description) — must not claim "no info at all"
    assert "Chưa có thông tin PoC được xác thực" not in out


def test_tier2_falls_back_to_not_found_when_section1_body_is_empty() -> None:
    out, report = enforce_section1(_EMPTY_SECTION1_REPORT, _GHSA_DESCRIPTION_ONLY)
    assert report["section1_tier"] == "synthesis_failed"
    assert _SYNTHESIS_BANNER not in out
    # must not claim the CVE/vuln is unknown — evidence genuinely existed this turn
    assert "Chưa có thông tin PoC" not in out


# ── T3: no catalog evidence at all, but CVE confirmed real ──────────────────────
def test_tier3_cve_known_keeps_model_code_with_banner() -> None:
    out, report = enforce_section1(_SYNTHESIZED_REPORT, _NO_EVIDENCE, cve_known=True, cve_id="CVE-2024-1234")
    assert report["section1_tier"] == "synthesized_cve"
    assert report["synthesized"] is True
    assert _SYNTHESIS_BANNER in out
    assert "requests.get" in out


def test_tier3_falls_back_to_synthesis_failed_when_model_wrote_nothing() -> None:
    # must NOT say "CVE chưa có trong dữ liệu nội bộ" — cve_known=True means it demonstrably
    # IS known; only the synthesis attempt itself came up empty this turn.
    out, report = enforce_section1(_MODEL_REPORT, _NO_EVIDENCE, cve_known=True, cve_id="CVE-2024-1234")
    assert report["section1_tier"] == "synthesis_failed"
    assert "CVE-2024-1234 chưa có trong dữ liệu nội bộ" not in out
    assert "Không tổng hợp được PoC minh hoạ" in out


# ── T4: nothing to ground on ─────────────────────────────────────────────────────
def test_tier4_no_cve_id_uses_generic_notice() -> None:
    out, report = enforce_section1(_MODEL_REPORT, _NO_EVIDENCE, cve_known=False, cve_id=None)
    assert report["section1_tier"] == "not_found"
    assert "Chưa có thông tin PoC được xác thực trong dữ liệu nội bộ cho lỗ hổng này." in out


def test_tier4_names_the_specific_cve_when_known() -> None:
    out, report = enforce_section1(_MODEL_REPORT, _NO_EVIDENCE, cve_known=False, cve_id="CVE-1999-99999")
    assert report["section1_tier"] == "not_found"
    assert "CVE CVE-1999-99999 chưa có trong dữ liệu nội bộ." in out


def test_tier4_is_idempotent() -> None:
    once, r1 = enforce_section1(_MODEL_REPORT, _NO_EVIDENCE, cve_id="CVE-1999-99999")
    twice, r2 = enforce_section1(once, _NO_EVIDENCE, cve_id="CVE-1999-99999")
    assert r1["no_poc_forced"] is True
    assert r2["no_poc_forced"] is False and r2["reason"] == "already_canonical"
    assert once == twice


# ── end-of-answer synthesis footer (P3): a SECOND warning at the very end of the report,
# on top of the existing in-place banner, for the two synthesized tiers only ──────────────
def test_tier2_footer_present_at_end_of_answer() -> None:
    out, report = enforce_section1(_SYNTHESIZED_REPORT, _GHSA_DESCRIPTION_ONLY)
    assert report["section1_tier"] == "synthesized_readme"
    assert _SYNTHESIS_FOOTER_MARKER in out
    # at the very end, after Mục 2/Mục 3 — not just near the top banner
    assert out.rstrip().endswith("mục đích nào.")
    assert out.index(_SYNTHESIS_BANNER) < out.rindex(_SYNTHESIS_FOOTER_MARKER)


def test_tier3_footer_present_at_end_of_answer() -> None:
    out, report = enforce_section1(_SYNTHESIZED_REPORT, _NO_EVIDENCE, cve_known=True, cve_id="CVE-2024-1234")
    assert report["section1_tier"] == "synthesized_cve"
    assert _SYNTHESIS_FOOTER_MARKER in out


def test_footer_names_source_url_when_evidence_carries_one() -> None:
    web_hint = [{
        "source": "web_reference", "url": "https://nvd.nist.gov/vuln/detail/CVE-2024-1234",
        "description": "Mô tả lỗ hổng từ trang tham khảo công khai.",
    }]
    out, _ = enforce_section1(_SYNTHESIZED_REPORT, web_hint, cve_known=True, cve_id="CVE-2024-1234")
    assert "nvd.nist.gov/vuln/detail/CVE-2024-1234" in out


def test_footer_absent_on_synthesis_failed_not_found_and_real_tiers() -> None:
    for out, _report in (
        enforce_section1(_MODEL_REPORT, _GHSA_DESCRIPTION_ONLY),                       # synthesis_failed
        enforce_section1(_MODEL_REPORT, _NO_EVIDENCE, cve_known=False, cve_id=None),    # not_found
        enforce_section1(_MODEL_REPORT, _REAL_EVIDENCE),                                # real
    ):
        assert _SYNTHESIS_FOOTER_MARKER not in out


def test_footer_is_idempotent() -> None:
    once, _ = enforce_section1(_SYNTHESIZED_REPORT, _GHSA_DESCRIPTION_ONLY)
    twice, _ = enforce_section1(once, _GHSA_DESCRIPTION_ONLY)
    assert once.count(_SYNTHESIS_FOOTER_MARKER) == 1
    assert twice.count(_SYNTHESIS_FOOTER_MARKER) == 1
    assert once == twice
