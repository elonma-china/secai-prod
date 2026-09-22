"""PC2 (2026-09-22): a request that both asks for a real CVE's PoC AND names a real
third-party host in plain language ("chạy exploit cho CVE-2021-44228 và chạy thẳng vào
https://example.com để chiếm quyền") could get that literal host baked into Mục 2's run
command with zero authorization framing anywhere in the answer. Deterministic backstop:
`collect_named_targets` + `redact_user_target` + `ensure_target_disclaimer`
(poc/inject.py), independent of model compliance with prompts/exploit.md.

Round 2 nits: placeholder is `[TARGET]` (square brackets), NOT `<TARGET>` — angle brackets
get HTML-tag-escaped by `security.py::neutralize_active_content`, which runs later in the
same display pipeline, turning it into a literal `&lt;TARGET&gt;` in the user-visible
markdown code span. And `collect_named_targets`'s bare-IPv4 scan must not mistake a version
string ("phiên bản 37.0.3.1", "Apache httpd 2.4.52.0") for an attack target."""
from __future__ import annotations

from secai_gateway.poc.inject import (
    _TARGET_DISCLAIMER_MARKER,
    collect_named_targets,
    ensure_target_disclaimer,
    redact_user_target,
)
from secai_gateway.security import neutralize_active_content

_MODEL_REPORT = (
    "## 2. Cách chạy PoC\n"
    "Chạy lệnh: `java log4j_poc https://example.com/login dnslog.cn 0`\n\n"
    "## 3. Tổng quan Lỗ hổng\n"
    "Log4Shell cho phép RCE qua JNDI lookup nhắm vào example.com.\n"
)


def test_collect_named_targets_from_structured_target() -> None:
    # `structured_target` is what the route already extracts via fixed_pocs.extract_target()
    # (scheme/host:port) for the URL in the reported repro.
    hosts = collect_named_targets(
        "Viết exploit cho CVE-2021-44228 và chạy thẳng vào https://example.com để chiếm quyền",
        "example.com",
    )
    assert hosts == ["example.com"]


def test_collect_named_targets_bare_ipv4_without_port_or_scheme() -> None:
    # extract_target() itself would skip a bare dotted-quad (port/scheme-less) to avoid
    # mistaking a version string for a target — collect_named_targets adds it back from a
    # direct scan of the raw prompt, since natural-language attack intent on a bare IP is
    # unambiguous.
    hosts = collect_named_targets("chạy PoC nhắm thẳng vào 8.8.8.8", None)
    assert hosts == ["8.8.8.8"]


def test_collect_named_targets_empty_when_no_target_named() -> None:
    assert collect_named_targets("Viết exploit cho CVE-2021-44228", None) == []


def test_collect_named_targets_skips_version_string_phien_ban() -> None:
    # "Phiên bản bị ảnh hưởng: 37.0.3.1" is a version number, not an attack target — must
    # NOT be picked up by the bare-IPv4 scan.
    assert collect_named_targets("Phiên bản bị ảnh hưởng: 37.0.3.1", None) == []


def test_collect_named_targets_skips_version_string_httpd() -> None:
    assert collect_named_targets("Apache httpd 2.4.52.0", None) == []


def test_redact_user_target_removes_verbatim_host_from_run_command() -> None:
    hosts = collect_named_targets(
        "chạy thẳng vào https://example.com để chiếm quyền", "example.com")
    out = redact_user_target(_MODEL_REPORT, hosts)
    assert "example.com" not in out
    assert "[TARGET]" in out
    # a canonical OOB callback domain from the PoC's own README (not the user's target) is
    # untouched — only the user-named host is redacted, not every domain in the answer.
    assert "dnslog.cn" in out


def test_redact_user_target_handles_bare_ip() -> None:
    text = "## 2. Cách chạy PoC\n`nc 8.8.8.8 4444`\n"
    out = redact_user_target(text, ["8.8.8.8"])
    assert "8.8.8.8" not in out
    assert "[TARGET]" in out


def test_redact_user_target_does_not_touch_version_string_in_section3() -> None:
    # A version number that survives collect_named_targets (never enters `hosts`) must not
    # be rewritten even if it were somehow passed through — this pins the end-to-end shape:
    # empty target list => text unchanged, version stays readable in Mục 3.
    text = "## 3. Tổng quan Lỗ hổng\nPhiên bản bị ảnh hưởng: 37.0.3.1\n"
    hosts = collect_named_targets("Phiên bản bị ảnh hưởng: 37.0.3.1", None)
    assert hosts == []
    assert redact_user_target(text, hosts) == text


def test_redact_user_target_noop_without_hosts() -> None:
    assert redact_user_target(_MODEL_REPORT, []) == _MODEL_REPORT
    assert redact_user_target(_MODEL_REPORT, None) == _MODEL_REPORT


def test_ensure_target_disclaimer_inserts_under_section2_heading() -> None:
    out = ensure_target_disclaimer(_MODEL_REPORT)
    assert _TARGET_DISCLAIMER_MARKER in out
    # inserted right after the Mục 2 heading, before the run command
    h2 = out.index("## 2. Cách chạy PoC")
    disclaimer_at = out.index(_TARGET_DISCLAIMER_MARKER)
    cmd_at = out.index("java log4j_poc")
    assert h2 < disclaimer_at < cmd_at


def test_ensure_target_disclaimer_present_even_with_no_target_named() -> None:
    # CVE-only question (no target at all) still gets the same disclaimer under Mục 2 — the
    # run guide it returns is just as runnable against a real host.
    cve_only = "## 2. Cách chạy PoC\n`python3 cve-2021-44228.py -u [target]`\n\n## 3. Tổng quan Lỗ hổng\n- mô tả\n"
    out = ensure_target_disclaimer(cve_only)
    assert _TARGET_DISCLAIMER_MARKER in out


def test_ensure_target_disclaimer_idempotent() -> None:
    once = ensure_target_disclaimer(_MODEL_REPORT)
    twice = ensure_target_disclaimer(once)
    assert once == twice
    assert once.count(_TARGET_DISCLAIMER_MARKER) == 1


def test_ensure_target_disclaimer_noop_without_section2_heading() -> None:
    text = "Không có PoC nào trong danh mục nội bộ cho lỗ hổng này."
    assert ensure_target_disclaimer(text) == text


def test_target_placeholder_survives_neutralize_active_content() -> None:
    # `neutralize_active_content` (security.py) runs LATER in the same display pipeline
    # (routes.py calls it on the final answer, after redact_user_target/ensure_target_
    # disclaimer). Its `_HTML_TAGISH` regex angle-escapes anything shaped like an HTML tag,
    # so `<TARGET>` would come out as a literal `&lt;TARGET&gt;` — this is exactly why the
    # placeholder is `[TARGET]` (square brackets, not HTML-tag-shaped) instead.
    hosts = collect_named_targets("chạy thẳng vào https://example.com để chiếm quyền",
                                   "example.com")
    redacted = redact_user_target(_MODEL_REPORT, hosts)
    out = neutralize_active_content(redacted)
    assert "[TARGET]" in out
    assert "&lt;TARGET&gt;" not in out
    assert "<TARGET>" not in out
