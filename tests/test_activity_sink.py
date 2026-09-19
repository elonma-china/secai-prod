"""ActivityLogSink: batching, and — the property that actually matters — that a broken
Elasticsearch can only lose log lines, never break or block the caller."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from secai_gateway.activity.schema import LogRecord
from secai_gateway.activity.sink import ActivityLogSink


class _Settings:
    activity_log_enabled = True
    elasticsearch_url = "http://es.invalid:9200"
    elasticsearch_username = ""
    elasticsearch_password = ""
    activity_log_index = "test-logs"
    activity_log_retention_days = 7
    activity_log_queue_max = 5
    activity_log_batch_max = 10
    activity_log_flush_interval_s = 0.05
    activity_log_timeout_s = 1.0
    activity_log_breaker_threshold = 2
    activity_log_breaker_cooldown_s = 30.0
    activity_log_max_field_chars = 50


def _rec(n: int = 1) -> LogRecord:
    return LogRecord(request_id=f"r{n}", function="chat", endpoint="/ai/chat",
                     question="hỏi", answer="đáp")


def _sink(handler, **over) -> ActivityLogSink:
    settings = _Settings()
    for k, v in over.items():
        setattr(settings, k, v)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ActivityLogSink(settings, client=client)


async def _drain(sink: ActivityLogSink, ticks: int = 6) -> None:
    for _ in range(ticks):
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_records_are_bulk_indexed() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_bulk"):
            seen.append(request.content.decode())
            return httpx.Response(200, json={"errors": False, "items": []})
        return httpx.Response(200, json={"acknowledged": True})

    sink = _sink(handler)
    await sink.start()
    await sink.emit(_rec(1))
    await sink.emit(_rec(2))
    await _drain(sink)
    await sink.stop()

    assert seen, "no _bulk call was made"
    body = "\n".join(seen).replace(" ", "")
    assert '{"create":{}}' in body          # data streams only accept create
    assert '"function":"chat"' in body
    assert '"endpoint":"/ai/chat"' in body
    assert sink.stats()["sent"] == 2


@pytest.mark.asyncio
async def test_dead_elasticsearch_never_raises_and_counts_drops() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    sink = _sink(handler)
    await sink.start()                       # bootstrap fails: must not raise
    await sink.emit(_rec())                  # emit must not raise either
    await _drain(sink)
    stats = sink.stats()
    await sink.stop()

    assert stats["dropped"] >= 1
    assert stats["sent"] == 0


@pytest.mark.asyncio
async def test_breaker_opens_after_repeated_failures() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_bulk"):
            calls["n"] += 1
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={})

    sink = _sink(handler)
    await sink.start()
    for i in range(6):
        await sink.emit(_rec(i))
        await asyncio.sleep(0.06)
    stats = sink.stats()
    await sink.stop()

    assert stats["breaker_open"] is True
    # Threshold is 2: once the breaker trips, later batches are shed without touching ES.
    assert calls["n"] <= 3


@pytest.mark.asyncio
async def test_full_queue_drops_instead_of_blocking() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": False, "items": []})

    sink = _sink(handler, activity_log_queue_max=2, activity_log_flush_interval_s=10.0)
    sink._running = True                     # producer side only; no worker draining
    for i in range(10):
        await asyncio.wait_for(sink.emit(_rec(i)), timeout=0.5)   # never blocks
    assert sink.stats()["dropped"] == 8


@pytest.mark.asyncio
async def test_emit_is_noop_before_start() -> None:
    """Unit tests never run the app lifespan, so an unstarted sink must do nothing at all —
    this is what keeps the rest of the suite free of Elasticsearch and network calls."""
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not touch the network")

    sink = _sink(handler)
    await sink.emit(_rec())
    assert sink.stats()["queued"] == 0


@pytest.mark.asyncio
async def test_partial_bulk_rejection_is_counted_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_bulk"):
            return httpx.Response(200, json={"errors": True, "items": [
                {"create": {"status": 201}},
                {"create": {"status": 400, "error": {"type": "mapper_parsing_exception",
                                                     "reason": "bad field"}}},
            ]})
        return httpx.Response(200, json={})

    sink = _sink(handler)
    await sink.start()
    await sink.emit(_rec(1))
    await sink.emit(_rec(2))
    await _drain(sink)
    stats = sink.stats()
    await sink.stop()

    assert stats["dropped"] == 1 and stats["sent"] == 1
    assert "mapper_parsing_exception" in stats["last_error"]


@pytest.mark.asyncio
async def test_long_content_is_clipped_and_flagged() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_bulk"):
            seen.append(request.content.decode())
        return httpx.Response(200, json={"errors": False, "items": []})

    sink = _sink(handler)                     # max_field_chars = 50
    await sink.start()
    await sink.emit(LogRecord(request_id="r", function="exploit", endpoint="/ai/exploit",
                              question="x" * 500, answer="y" * 500))
    await _drain(sink)
    await sink.stop()

    assert '"truncated":true' in "".join(seen).replace(" ", "")
    assert '"answer_len":500' in "".join(seen).replace(" ", "")   # original length preserved
