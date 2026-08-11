"""Unit tests for allowlisted web_fetch (SSRF + HTML→markdown)."""
from __future__ import annotations

import pytest

from secai_gateway.retrieval.http_fetch import (
    html_to_markdown,
    host_allowed,
    validate_fetch_url,
)


def test_host_allowed_apex_and_subdomain() -> None:
    allowed = ["owasp.org", "cwe.mitre.org"]
    assert host_allowed("owasp.org", allowed)
    assert host_allowed("www.owasp.org", allowed)
    assert host_allowed("cwe.mitre.org", allowed)
    assert not host_allowed("evil.com", allowed)
    assert not host_allowed("owasp.org.evil.com", allowed)


def test_validate_rejects_http_and_credentials() -> None:
    with pytest.raises(ValueError, match="https"):
        validate_fetch_url("http://owasp.org/Top10/2025/")
    with pytest.raises(ValueError, match="credentials"):
        validate_fetch_url("https://user:pass@owasp.org/")


def test_validate_rejects_non_allowlisted_host() -> None:
    with pytest.raises(ValueError, match="allowlist"):
        validate_fetch_url("https://example.com/")


def test_validate_rejects_private_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    import secai_gateway.retrieval.http_fetch as wf

    def fake_getaddrinfo(host, port, **kwargs):  # noqa: ANN001
        return [(None, None, None, None, ("127.0.0.1", 443))]

    monkeypatch.setattr(wf.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="non-public"):
        validate_fetch_url("https://owasp.org/")


def test_validate_accepts_public_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    import secai_gateway.retrieval.http_fetch as wf

    def fake_getaddrinfo(host, port, **kwargs):  # noqa: ANN001
        return [(None, None, None, None, ("1.2.3.4", 443))]

    monkeypatch.setattr(wf.socket, "getaddrinfo", fake_getaddrinfo)
    out = validate_fetch_url("https://owasp.org/Top10/2025/")
    assert out == "https://owasp.org/Top10/2025/"


def test_html_to_markdown_extracts_headings_and_lists() -> None:
    html = """
    <html><head><script>evil()</script><title>x</title></head>
    <body>
      <nav>skip</nav>
      <h1>OWASP Top 10:2025</h1>
      <p>Intro <strong>text</strong>.</p>
      <ul><li>A01 Broken Access Control</li><li>A02 Security Misconfiguration</li></ul>
      <a href="https://owasp.org/x">link</a>
    </body></html>
    """
    md = html_to_markdown(html)
    assert "OWASP Top 10:2025" in md
    assert "A01 Broken Access Control" in md
    assert "evil()" not in md
    assert "skip" not in md
    assert "**text**" in md
