"""End-to-end wiring of the activity log through the real routes.

Two things are checked: that each AI endpoint produces exactly one record with the fields
the viewer needs, and — the constraint that outranks the feature — that turning logging on
leaves the old /ai/chat contract byte-identical.
"""
from __future__ import annotations

import httpx
import pytest

from linch.events import PartialAssistantEvent, ResultEvent
from linch.types import Usage

from secai_gateway.app import create_app


class FakeRunner:
    """Emits the same event shape a real run does: token deltas followed by the ResultEvent
    that carries the assembled answer — which is what the activity log records."""

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None):
        return f"{profile_name}: {prompt}", {"run_id": "run-1", "profile": profile_name}

    async def stream(self, profile_name: str, prompt: str, session_id: str | None = None):
        text = f"{profile_name}: {prompt}"
        yield PartialAssistantEvent(delta={"kind": "text", "text": text})
        yield ResultEvent(subtype="success", stop_reason="end_turn", total_usage=Usage(),
                          duration_ms=1, final_text=text)


class RecordingSink:
    """Stands in for ActivityLogSink: same surface the routes touch, records instead of
    talking to Elasticsearch."""

    enabled = True

    def __init__(self) -> None:
        self.records: list = []

    async def emit(self, record) -> None:
        self.records.append(record)

    def stats(self) -> dict:
        return {"enabled": True, "sent": len(self.records)}


@pytest.fixture()
def app_and_sink(monkeypatch):
    """create_app() wires GatewayState internally, so reach the instance the closures
    captured through the app's dependency-free `state` object."""
    import secai_gateway.app as app_module

    holder = {}
    original = app_module.GatewayState

    class Patched(original):  # type: ignore[misc, valid-type]
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.logsink = RecordingSink()
            holder["sink"] = self.logsink

    monkeypatch.setattr(app_module, "GatewayState", Patched)
    app = app_module.create_app(FakeRunner())
    return app, holder["sink"]


async def _post(app, path, payload):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        return await client.post(path, json=payload)


@pytest.mark.asyncio
async def test_chat_writes_one_record_with_question_and_answer(app_and_sink) -> None:
    app, sink = app_and_sink
    resp = await _post(app, "/ai/chat", {"user_id": "u1", "message": "SQL injection là gì?"})
    assert resp.status_code == 200
    assert len(sink.records) == 1

    rec = sink.records[0]
    assert rec.function == "chat"
    assert rec.endpoint == "/ai/chat"
    assert rec.user_id == "u1"
    assert rec.question == "SQL injection là gì?"
    assert rec.answer.startswith("chat:")
    assert rec.run_id == "run-1"
    assert rec.profile == "chat"
    assert rec.stream is False
    assert rec.duration_ms >= 0
    # The document actually written carries the server hostname ("tên máy") + timestamp.
    doc = rec.to_doc(max_field_chars=1000)
    assert doc["server_hostname"] and doc["@timestamp"]


@pytest.mark.asyncio
async def test_old_chat_contract_is_unchanged_with_logging_on(app_and_sink) -> None:
    """The Japanese integration consumes these endpoints — logging must be invisible to it."""
    app, sink = app_and_sink
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        for path in ("/ai/chat", "/chat_no_stream"):
            r = await client.post(path, json={"user_id": "u1", "message": "hello"})
            assert set(r.json().keys()) == {"response", "answer", "message", "metadata"}

        s = await client.post("/ai/chat/stream", json={"user_id": "u1", "message": "hello"})
        body = s.text
        assert "event: token" in body and "event: final" in body and "data: [DONE]" in body
        assert "event: conversation" not in body      # no additive frame leaks onto the old path
    assert len(sink.records) == 3                     # still logged, just invisibly


@pytest.mark.asyncio
async def test_stream_logs_after_the_stream_completes(app_and_sink) -> None:
    app, sink = app_and_sink
    await _post(app, "/ai/chat/stream", {"user_id": "u2", "message": "xin chào"})
    assert len(sink.records) == 1
    rec = sink.records[0]
    assert rec.stream is True
    assert rec.answer.startswith("chat:")          # the assembled final answer, not a token
    assert rec.question == "xin chào"


@pytest.mark.asyncio
async def test_every_ai_function_is_logged_once(app_and_sink) -> None:
    app, sink = app_and_sink
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        await client.post("/ai/translate", json={"text": "xin chào"})
        await client.post("/ai/generate-report", json={"content": "sự cố"})
        await client.post("/ai/generate-statistical-report", json={"data": {"n": 1}})
        await client.post("/ai/exploit", json={"question": "CVE-2024-3400"})
        await client.post("/ai/conversations/chat", json={"user_id": "u3", "message": "hi"})

    functions = [r.function for r in sink.records]
    assert functions.count("translate") == 1
    assert functions.count("report") == 1
    assert functions.count("statistical_report") == 1
    assert functions.count("exploit") == 1
    assert functions.count("conversation_chat") == 1


@pytest.mark.asyncio
async def test_conversation_record_carries_conversation_id(app_and_sink) -> None:
    app, sink = app_and_sink
    resp = await _post(app, "/ai/conversations/chat", {"user_id": "u4", "message": "hi"})
    cid = resp.json()["conversation_id"]
    assert sink.records[0].conversation_id == cid


@pytest.mark.asyncio
async def test_client_ip_and_user_agent_are_captured(app_and_sink) -> None:
    app, sink = app_and_sink
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 1234)),
                                 base_url="http://test") as client:
        await client.post("/ai/chat", json={"user_id": "u5", "message": "hi"},
                          headers={"User-Agent": "SecAI-FE/1.0"})
    rec = sink.records[0]
    assert rec.client_ip == "203.0.113.9"
    assert rec.user_agent == "SecAI-FE/1.0"


@pytest.mark.asyncio
async def test_forwarded_for_wins_when_proxied(app_and_sink) -> None:
    app, sink = app_and_sink
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("10.0.0.1", 1)),
                                 base_url="http://test") as client:
        await client.post("/ai/chat", json={"user_id": "u6", "message": "hi"},
                          headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.1"})
    rec = sink.records[0]
    assert rec.client_ip == "198.51.100.7"          # original client, not the proxy hop
    assert rec.client_forwarded_for.startswith("198.51.100.7")


@pytest.mark.asyncio
async def test_health_is_not_logged(app_and_sink) -> None:
    """/health runs every few seconds; logging it would bury the real traffic."""
    app, sink = app_and_sink
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        await client.get("/health")
    assert sink.records == []
