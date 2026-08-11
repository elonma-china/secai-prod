"""cve_exists_in_kb — deterministic exact-match CVE-KB existence check used to gate the
exploit-research synthesize-from-CVE-definition tier (poc/inject.py::enforce_section1)."""
from __future__ import annotations

import httpx

from secai_gateway.retrieval.neo4j import cve_exists_in_kb


def _patch_client(monkeypatch, handler) -> None:
    real_async_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs = dict(kwargs)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)


async def test_returns_true_on_hit(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [], "results": [
            {"columns": ["n"], "data": [{"row": [1]}]}]})
    _patch_client(monkeypatch, handler)
    assert await cve_exists_in_kb("CVE-2024-1234") is True


async def test_returns_false_on_zero_count(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [], "results": [
            {"columns": ["n"], "data": [{"row": [0]}]}]})
    _patch_client(monkeypatch, handler)
    assert await cve_exists_in_kb("CVE-1999-99999") is False


async def test_returns_false_on_transport_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)
    _patch_client(monkeypatch, handler)
    assert await cve_exists_in_kb("CVE-2024-1234") is False


async def test_returns_false_on_neo4j_error_payload(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"code": "X", "message": "bad"}], "results": []})
    _patch_client(monkeypatch, handler)
    assert await cve_exists_in_kb("CVE-2024-1234") is False


async def test_empty_id_returns_false_without_network(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not make a network call for an empty id")
    _patch_client(monkeypatch, handler)
    assert await cve_exists_in_kb("") is False
    assert await cve_exists_in_kb(None) is False  # type: ignore[arg-type]
