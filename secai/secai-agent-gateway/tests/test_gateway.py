from __future__ import annotations

import asyncio
import tempfile

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
            # The exploit endpoint expects a CVE id; a bare "hello" is now treated as
            # chitchat and returns the "nhập CVE" guide instead of running the model, so
            # give this one a real CVE to exercise the model streaming path.
            body = {"user_id": "u1", "message": "hello", "text": "hello"}
            if path == "/ai/exploit/stream":
                body = {"user_id": "u1", "text": "CVE-2024-1234"}
            response = await client.post(path, json=body)
            assert response.status_code == 200, path
            assert "event: token" in response.text
            assert f"{profile}:" in response.text
            assert "event: final" in response.text
            assert "data: [DONE]" in response.text


def test_prefetch_prompt_appendix_grounds_muc2_3() -> None:
    """When prefetch fetched a PoC, its content is fed into the model prompt so Mục 2/3 are
    grounded regardless of the model's own tool-calling (the CVE-2026-33032 root cause)."""
    from secai_gateway.api.routes import _prefetch_prompt_appendix

    # No evidence → no appendix (honest not-found path unchanged).
    assert _prefetch_prompt_appendix({"evidence": []}) == ""

    ev = [{"source": "nuclei", "url": "https://x/CVE-2026-33032.yaml",
           "source_files": [{"path": "CVE-2026-33032.yaml",
                             "code": "id: CVE-2026-33032\ninfo:\n  severity: critical\n",
                             "role": "entry"}]}]
    appendix = _prefetch_prompt_appendix({"evidence": ev})
    assert "CVE-2026-33032.yaml" in appendix          # real PoC content reached the prompt
    assert "id: CVE-2026-33032" in appendix            # the fetched bytes, not just a mention
    assert "nuclei" in appendix                        # provenance named
    assert "Mục 2" in appendix and "Mục 3" in appendix
    assert "không có thông tin" in appendix            # explicit instruction not to say it


@pytest.mark.asyncio
async def test_exploit_chitchat_returns_cve_guide() -> None:
    """A greeting on the exploit endpoints returns a short "nhập CVE" guide WITHOUT running
    the model or forcing the "## 1. …" report scaffolding."""
    fake = FakeRunner()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(fake)), base_url="http://test") as client:
        # Non-stream: guide body, chitchat flag, and the model was never called.
        resp = await client.post("/ai/exploit", json={"user_id": "u1", "message": "xin chào"})
        assert resp.status_code == 200
        data = resp.json()
        assert "CVE-2026-42533" in data["description"]  # the guide's example id
        assert "## 1." not in data["description"]
        assert data["metadata"]["input_kind"] == "chitchat"
        assert fake.calls == []

        # Stream: same guide via token+final, still no model call.
        stream = await client.post("/ai/exploit/stream", json={"user_id": "u1", "text": "hello"})
        assert stream.status_code == 200
        assert "event: token" in stream.text
        assert "event: final" in stream.text
        assert "data: [DONE]" in stream.text
        assert '"input_kind": "chitchat"' in stream.text
        assert "## 1." not in stream.text
        assert fake.calls == []

        # A malformed CVE is NOT chitchat — it reaches the model (normalized to canonical).
        await client.post("/ai/exploit", json={"user_id": "u1", "message": "cve 2024 1234"})
        assert fake.calls, "malformed CVE should have run the model"
        assert "CVE-2024-1234" in fake.calls[-1][1]  # prompt normalized before the model


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


class FailingRunner:
    """A runner whose stream dies mid-run — models the live 'session not found' case."""

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None):
        raise RuntimeError("boom: session not found: chat:deadbeef")

    async def stream(self, profile_name: str, prompt: str, session_id: str | None = None):
        raise RuntimeError("boom: session not found: chat:deadbeef")
        yield  # pragma: no cover — makes this an async generator


@pytest.mark.asyncio
async def test_stream_failure_is_visible_not_silent() -> None:
    """Bug #2: a failed run must reach the user on the *visible* channels (token +
    final), not only as an `event: error` frame that token-only UIs ignore. It must
    also never leak the internal exception text."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(FailingRunner())), base_url="http://test") as client:
        resp = await client.post("/ai/chat/stream", json={"user_id": "u1", "message": "hello"})
    assert resp.status_code == 200
    body = resp.text
    assert "event: error" in body        # machine-readable channel
    assert "event: token" in body        # visible channel for token-only UIs
    assert "event: final" in body        # final answer carries the message too
    assert "data: [DONE]" in body
    assert "Xin lỗi" in body             # friendly, user-facing message
    assert "session not found" not in body  # internal detail must not leak to the client


class SlowRunner:
    """Like FakeRunner but stream() sleeps before yielding — long enough for the
    concurrent deterministic prefetch (real catalog lookup, no network) to resolve
    first — and exposes poc/ledger so exploit_prefetch() can gather real evidence.
    Used to assert the `section1` preview (see api/routes.py exploit_stream) actually
    arrives before `final`, not just that both eventually show up."""

    def __init__(self, poc, ledger, delay: float = 0.3) -> None:
        self.poc = poc
        self.ledger = ledger
        self.delay = delay

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None):
        return f"{profile_name}: {prompt}", {"run_id": "run-test", "profile": profile_name}

    async def stream(self, profile_name: str, prompt: str, session_id: str | None = None):
        await asyncio.sleep(self.delay)
        yield PartialAssistantEvent(delta={"kind": "text", "text": "Mục 2. Phân tích..."})


class FailingExploitRunner:
    """Own-generator sibling of FailingRunner — exploit_stream() no longer reuses the
    shared stream() helper, so its error path needs its own regression coverage."""

    poc = None
    ledger = None

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None):
        raise RuntimeError("boom: session not found: exploit:deadbeef")

    async def stream(self, profile_name: str, prompt: str, session_id: str | None = None):
        raise RuntimeError("boom: session not found: exploit:deadbeef")
        yield  # pragma: no cover — makes this an async generator


@pytest.mark.asyncio
async def test_exploit_stream_section1_preview_arrives_before_final() -> None:
    from secai_gateway.poc.backend import LocalPocBackend
    from secai_gateway.state import SqliteLedger

    poc = LocalPocBackend()
    ledger = SqliteLedger(tempfile.mktemp(suffix=".db"))
    runner = SlowRunner(poc, ledger, delay=0.3)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(runner)), base_url="http://test") as client:
        resp = await client.post("/ai/exploit/stream", json={"vulnerability_type": "CVE-2021-41773"})
    assert resp.status_code == 200
    body = resp.text
    assert "event: section1" in body, "PoC evidence exists for this CVE — preview must fire"
    assert body.index("event: section1") < body.index("event: final")
    assert "data: [DONE]" in body


@pytest.mark.asyncio
async def test_exploit_stream_failure_is_visible_not_silent() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(FailingExploitRunner())), base_url="http://test") as client:
        resp = await client.post("/ai/exploit/stream", json={"vulnerability_type": "CVE-0000-0000"})
    assert resp.status_code == 200
    body = resp.text
    assert "event: error" in body
    assert "event: token" in body
    assert "event: final" in body
    assert "data: [DONE]" in body
    assert "Xin lỗi" in body
    assert "session not found" not in body


def test_chat_session_id_scoping() -> None:
    from secai_gateway.api.routes import _chat_session_id
    from secai_gateway.api.schemas import LegacyRequest

    # Same real user → same stable session (conversation memory persists).
    a = _chat_session_id(LegacyRequest(user_id="u1", message="hi"))
    b = _chat_session_id(LegacyRequest(user_id="u1", message="again"))
    assert a == b and a.startswith("chat:")
    # Anonymous callers must NOT share a session id (no cross-user history leak).
    x = _chat_session_id(LegacyRequest(message="hi"))
    y = _chat_session_id(LegacyRequest(message="hi"))
    assert x != y and x.startswith("chat:anon:")
