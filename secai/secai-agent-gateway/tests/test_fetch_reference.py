"""Unit tests for fetch_reference: allowlist/SSRF, chunking, raw mode, and evidence."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from linch.tools.base import ToolContext

import secai_gateway.retrieval.http_fetch as hf
import secai_gateway.retrieval.reference_fetch as fr
from secai_gateway.poc.inject import has_real_code_evidence, has_synthesis_evidence
from secai_gateway.retrieval.http_fetch import validate_fetch_url
from secai_gateway.retrieval.reference_fetch import (
    build_reference_tools,
    fetch_reference_markdown,
    reference_host_allowed,
    reference_hosts,
)
from secai_gateway.state import SqliteLedger


# ── allowlist policy ──────────────────────────────────────────────────────────
def test_reference_allowlist_covers_security_hosts() -> None:
    assert reference_host_allowed("nvd.nist.gov")
    assert reference_host_allowed("github.com")
    assert reference_host_allowed("raw.githubusercontent.com")  # subdomain of githubusercontent.com
    assert reference_host_allowed("cwe.mitre.org")
    assert not reference_host_allowed("evil.example")
    assert not reference_host_allowed("github.com.evil.example")


def test_validate_rejects_non_reference_host() -> None:
    with pytest.raises(ValueError, match="allowlist"):
        validate_fetch_url("https://example.com/", allowed=reference_hosts())


def test_validate_rejects_private_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hf.socket, "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("10.0.0.5", 443))],
    )
    with pytest.raises(ValueError, match="non-public"):
        validate_fetch_url("https://github.com/torvalds/linux", allowed=reference_hosts())


# ── fetch + windowing (mocked network) ───────────────────────────────────────
class _FakeResp:
    def __init__(self, *, status: int, text: str, ctype: str, url: str) -> None:
        self.status_code = status
        self.text = text
        self.headers = {"content-type": ctype}
        self.is_redirect = False
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *a: object) -> bool:
        return False

    async def get(self, url: str, headers: dict | None = None) -> _FakeResp:
        return self._resp


def _patch_net(monkeypatch: pytest.MonkeyPatch, resp: _FakeResp) -> None:
    monkeypatch.setattr(
        hf.socket, "getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("140.82.112.3", 443))],  # public
    )
    monkeypatch.setattr(fr.httpx, "AsyncClient", lambda **kw: _FakeClient(resp))


def _ctx(run_id: str = "rid") -> ToolContext:
    return ToolContext(cwd="/tmp", session_id="sid", run_id=run_id, session_store=None)


def test_html_page_converts_and_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    body = "<html><body><h1>CVE-2024-1234</h1><p>" + ("A" * 400) + "</p></body></html>"
    resp = _FakeResp(status=200, text=body, ctype="text/html", url="https://github.com/x")
    _patch_net(monkeypatch, resp)

    first = asyncio.run(fetch_reference_markdown("https://github.com/x", max_chars=100))
    assert first["status"] == 200
    assert first["truncated"] is True
    assert first["next_start_index"] == 100
    assert first["raw"] is False
    assert "CVE-2024-1234" in first["markdown"]
    assert "evil" not in first["markdown"]

    second = asyncio.run(
        fetch_reference_markdown("https://github.com/x", start_index=first["next_start_index"])
    )
    assert second["start_index"] == 100
    assert second["truncated"] is False
    assert second["next_start_index"] is None


def test_raw_mode_returns_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    body = "#!/usr/bin/env python\nprint('poc')\n"
    resp = _FakeResp(
        status=200, text=body, ctype="text/plain",
        url="https://raw.githubusercontent.com/u/r/main/poc.py",
    )
    _patch_net(monkeypatch, resp)
    out = asyncio.run(
        fetch_reference_markdown("https://raw.githubusercontent.com/u/r/main/poc.py", raw=True)
    )
    assert out["raw"] is True
    assert "print('poc')" in out["markdown"]


# ── tool + evidence ledger ───────────────────────────────────────────────────
def test_tool_records_html_as_synthesis_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    body = "<html><body><h1>GHSA-xxxx</h1><p>A stored XSS in the admin panel.</p></body></html>"
    resp = _FakeResp(status=200, text=body, ctype="text/html", url="https://github.com/advisories/GHSA-xxxx")
    _patch_net(monkeypatch, resp)
    ledger = SqliteLedger(str(tmp_path / "gw.db"))
    tool = build_reference_tools(ledger)["fetch_reference"]

    res = asyncio.run(tool.execute({"url": "https://github.com/advisories/GHSA-xxxx"}, _ctx("run-h")))
    assert res.is_error is False
    assert "GHSA-xxxx" in res.content

    ev = ledger.evidence_for_run("run-h")
    assert len(ev) == 1
    assert has_synthesis_evidence(ev) is True      # T2: description present
    assert has_real_code_evidence(ev) is False     # not misclassified as runnable code
    assert ev[0]["canonical_url"] == "https://github.com/advisories/GHSA-xxxx"
    assert ev[0]["draft_not_executed"] is True


def test_tool_records_raw_as_real_code_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    body = "import requests\nrequests.get('http://target/%00')\n"
    resp = _FakeResp(
        status=200, text=body, ctype="text/plain",
        url="https://raw.githubusercontent.com/u/r/main/exploit.py",
    )
    _patch_net(monkeypatch, resp)
    ledger = SqliteLedger(str(tmp_path / "gw.db"))
    tool = build_reference_tools(ledger)["fetch_reference"]

    res = asyncio.run(tool.execute(
        {"url": "https://raw.githubusercontent.com/u/r/main/exploit.py", "raw": True}, _ctx("run-r"),
    ))
    assert res.is_error is False

    ev = ledger.evidence_for_run("run-r")
    assert len(ev) == 1
    assert has_real_code_evidence(ev) is True      # T1: runnable source
    assert ev[0]["language"] == "python"
    assert ev[0]["sha256"]
    assert ev[0]["draft_not_executed"] is True


def test_tool_rejection_records_no_evidence(tmp_path) -> None:
    ledger = SqliteLedger(str(tmp_path / "gw.db"))
    tool = build_reference_tools(ledger)["fetch_reference"]
    res = asyncio.run(tool.execute({"url": "https://evil.example/x"}, _ctx("run-x")))
    assert res.is_error is True
    assert "rejected" in res.content
    assert "allowlist" in res.content
    assert ledger.evidence_for_run("run-x") == []
