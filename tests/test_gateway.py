from __future__ import annotations

import httpx
import pytest

from linch.events import PartialAssistantEvent

from secai_gateway.app import create_app
from secai_gateway.agent import registry_for


class FakeRunner:
    """Implements both interfaces the routes use: complete() for the non-stream endpoints
    and stream() for the SSE endpoints. Records each call so tests can assert routing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None):
        self.calls.append((profile_name, prompt, session_id))
        return f"{profile_name}: {prompt}", {"run_id": "run-test", "profile": profile_name}

    async def stream(self, profile_name: str, prompt: str, session_id: str | None = None):
        self.calls.append((profile_name, prompt, session_id))
        yield PartialAssistantEvent(delta={"kind": "text", "text": f"{profile_name}: {prompt}"})


@pytest.mark.asyncio
async def test_legacy_contract_paths_and_profiles() -> None:
    fake = FakeRunner()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(fake)), base_url="http://test") as client:
        for path in ("/chat_no_stream", "/ai/chat"):
            response = await client.post(path, json={"user_id": "u1", "message": "hello"})
            assert response.status_code == 200
            assert response.json()["response"].startswith("chat:")
        assert (await client.post("/ai/translate", json={"text": "xin chao"})).json()["translation"].startswith("translate:")
        assert (await client.post("/ai/generate-report", json={"content": "facts"})).json()["report"].startswith("report:")
        assert (await client.post("/ai/generate-statistical-report", json={"data": {"n": 2}})).status_code == 200
        exploit = await client.post("/ai/exploit", json={"question": "CVE-2024-1"})
        assert exploit.status_code == 200
        assert {"description", "exploit_guide", "metadata"} <= exploit.json().keys()
        assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_streaming_endpoints() -> None:
    fake = FakeRunner()
    stream_paths = {
        "/ai/chat/stream": "chat",
        "/ai/translate/stream": "translate",
        "/ai/generate-report/stream": "report",
        "/ai/generate-statistical-report/stream": "statistical_report",
        "/ai/exploit/stream": "exploit_research",
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(fake)), base_url="http://test") as client:
        for path, profile in stream_paths.items():
            response = await client.post(path, json={"user_id": "u1", "message": "hello", "text": "hello"})
            assert response.status_code == 200, path
            assert "event: token" in response.text
            assert f"{profile}:" in response.text
            assert "event: final" in response.text
            assert "data: [DONE]" in response.text


@pytest.mark.asyncio
async def test_sse_and_admin_key(monkeypatch) -> None:
    fake = FakeRunner()
    monkeypatch.setenv("ADMIN_API_KEY", "admin-test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(fake)), base_url="http://test") as client:
        stream = await client.post("/ai/chat/stream", json={"message": "hello"})
        assert stream.status_code == 200
        assert "data: [DONE]" in stream.text
        assert (await client.post("/admin/refresh-models")).status_code == 401
        assert (await client.post("/admin/refresh-models", headers={"X-API-Key": "admin-test"})).status_code == 200


def test_registry_isolation() -> None:
    # chat may search PoC metadata but must NOT fetch artifacts (exploit-only).
    assert "poc_fetch" not in registry_for("chat")
    assert "poc_search" in registry_for("chat")
    assert not registry_for("translate")
    # report may retrieve from the KB, but the exploit-only PoC tools must never leak in.
    assert registry_for("report") == {"qdrant_search", "neo4j_search"}
    assert not ({"poc_fetch"} & registry_for("report"))
    # 2-tool PoC surface: analyze/generate_draft were folded into poc_fetch and removed.
    assert {"poc_search", "poc_fetch"} <= registry_for("exploit_research")
    assert not ({"poc_analyze", "poc_generate_draft"} & registry_for("exploit_research"))


@pytest.mark.asyncio
async def test_secrets_are_rejected_before_runner() -> None:
    fake = FakeRunner()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(fake)), base_url="http://test") as client:
        response = await client.post("/ai/chat", json={"message": "password=super-secret-token-value"})
    assert response.status_code == 422
    assert not fake.calls
