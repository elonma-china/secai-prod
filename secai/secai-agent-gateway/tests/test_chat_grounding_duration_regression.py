"""Regression suite for the pre-fetch grounding duration_ms bug (2026-09-21).

Bug: the synthetic ``tool_call``/``tool_result`` SSE pair that ``_grounding_sse_frames``
builds for the chat pre-fetch (``_ground_chat_prompt`` — a code-level KB lookup that runs
BEFORE the model, so it never goes through the model's own tool-use loop) hard-coded
``duration_ms: 0`` in the ``tool_result`` frame. The FE ToolTimeline then rendered
``qdrant_search · 0ms`` for a lookup that actually took real (network) time, implying the
answer was grounded "instantly" — misleading, and indistinguishable from a lookup that
was skipped entirely.

Fix (see ``git diff -- src/secai_gateway/api/routes.py``), one file:
  - ``_ground_chat_prompt``: times the real ``_qdrant_search`` call with
    ``time.monotonic()`` before/after → ``grounding_ms``, returned inside the grounding
    dict as ``duration_ms`` (alongside the pre-existing ``query``/``result``/``summary``).
  - ``_grounding_sse_frames``: reads ``grounding.get("duration_ms", 0)`` instead of the
    literal ``0``.

This suite proves the fix behaviorally end-to-end through the real HTTP/SSE surface
(``/ai/chat/stream``), not just by inspecting source text: ``_ground_chat_prompt`` and
``_grounding_sse_frames`` are closures defined inside ``register_routes`` and cannot be
imported/unit-tested directly, but ``_qdrant_search`` is imported into
``secai_gateway.api.routes`` at module level and looked up as a bare global at call time —
so monkeypatching ``secai_gateway.api.routes._qdrant_search`` reaches the closure without
needing any refactor of the fix itself.

  UC1 — a pre-fetch lookup that takes real (simulated) time reports a measured, non-zero
        duration_ms in the synthetic qdrant_search tool_result SSE frame.
  UC2 — two lookups with different simulated latencies report DIFFERENT duration_ms
        values that track the injected latency (rules out "some other fixed constant").
  UC3 — static guard (belt-and-suspenders): the hard-coded ``"duration_ms": 0,`` literal
        must not reappear in ``_grounding_sse_frames``, and ``_ground_chat_prompt`` must
        still bracket the ``_qdrant_search`` call with ``time.monotonic()``. Cheap, and
        catches a future revert even if the monkeypatch seam above ever changes shape.

No LLM call, no network, no serving dependency (FakeRunner + a monkeypatched
``_qdrant_search`` stand in for the model and the KB) — fully offline/hermetic, same
pattern as ``tests/test_gateway.py``.
"""
from __future__ import annotations

import asyncio
import inspect
import json

import httpx
import pytest

from linch.events import PartialAssistantEvent
from linch.tools.base import ToolResult

import secai_gateway.api.routes as routes
from secai_gateway.app import create_app


class _FakeRunner:
    """Minimal stand-in for the real linch runner — the model is never exercised here,
    only the pre-fetch grounding path that runs before the agent."""

    async def complete(self, profile_name, prompt, session_id=None, *, grounding_context=None):
        return f"{profile_name}: {prompt}", {"run_id": "run-test", "profile": profile_name}

    async def stream(self, profile_name, prompt, session_id=None, *, grounding_context=None):
        yield PartialAssistantEvent(delta={"kind": "text", "text": f"{profile_name}: {prompt}"})


def _tool_result_frames(sse_text: str) -> list[dict]:
    """Parse every ``event: tool_result`` SSE frame out of a raw SSE response body."""
    frames = []
    for block in sse_text.split("\n\n"):
        if not block.startswith("event: tool_result"):
            continue
        _, data_line = block.split("\n", 1)
        assert data_line.startswith("data: ")
        frames.append(json.loads(data_line[len("data: "):]))
    return frames


def _prefetch_qdrant_frame(sse_text: str) -> dict:
    frames = _tool_result_frames(sse_text)
    prefetch = [f for f in frames
                if f.get("name") == "qdrant_search" and str(f.get("id", "")).startswith("pre-fetch-")]
    assert prefetch, f"no synthetic pre-fetch qdrant_search tool_result frame found; got {frames}"
    return prefetch[0]


async def _chat_stream(sleep_s: float, message: str, monkeypatch) -> str:
    async def _fake_qdrant_search(*, query, ctx=None, top_k=10, **kwargs):
        await asyncio.sleep(sleep_s)
        return ToolResult(content=f"[KB] {query} — kết quả tra cứu giả lập cho test.",
                          summary=f"qdrant_search({query}) 1 hit")

    monkeypatch.setattr(routes, "_qdrant_search", _fake_qdrant_search)
    fake = _FakeRunner()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(fake)),
                                 base_url="http://test") as client:
        resp = await client.post("/ai/chat/stream", json={"user_id": "c3-verify", "message": message})
    assert resp.status_code == 200
    return resp.text


@pytest.mark.asyncio
async def test_uc1_prefetch_duration_ms_is_measured_not_hardcoded_zero(monkeypatch) -> None:
    sleep_s = 0.08
    text = await _chat_stream(sleep_s, "CVE-2021-44228 là gì?", monkeypatch)
    frame = _prefetch_qdrant_frame(text)
    duration_ms = frame["duration_ms"]
    assert isinstance(duration_ms, int)
    assert duration_ms > 0, "duration_ms=0 — the hardcoded-0 bug is back"
    # Generous floor (50% of injected latency) to avoid flaking on a loaded box, but far
    # above the old constant 0.
    assert duration_ms >= int(sleep_s * 1000 * 0.5), (
        f"duration_ms={duration_ms} too small for a ~{sleep_s * 1000:.0f}ms simulated lookup "
        "— looks hardcoded again, not measured")


@pytest.mark.asyncio
async def test_uc2_prefetch_duration_ms_tracks_injected_latency(monkeypatch) -> None:
    text_fast = await _chat_stream(0.02, "CVE-2024-3400 có PoC không?", monkeypatch)
    fast_ms = _prefetch_qdrant_frame(text_fast)["duration_ms"]

    text_slow = await _chat_stream(0.15, "CVE-2024-3400 có PoC không?", monkeypatch)
    slow_ms = _prefetch_qdrant_frame(text_slow)["duration_ms"]

    assert fast_ms != slow_ms, "duration_ms did not change with injected latency — looks like a fixed constant"
    assert slow_ms > fast_ms


def test_uc3_source_no_longer_hardcodes_zero_duration() -> None:
    """Static guard on top of UC1/UC2: catches a future revert of the fix even if the
    monkeypatch seam above ever stops working (e.g. _qdrant_search import removed)."""
    src = inspect.getsource(routes)
    assert '"duration_ms": 0,' not in src, (
        "_grounding_sse_frames appears to hard-code duration_ms=0 again")
    assert 'grounding.get("duration_ms"' in src, (
        "_grounding_sse_frames no longer reads duration_ms from the grounding dict")
    assert "time.monotonic()" in src, (
        "_ground_chat_prompt no longer times the _qdrant_search call with time.monotonic()")
