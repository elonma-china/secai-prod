"""Regression suite for bug #13/CQ4 (2026-09-22).

Bug: ``POST /sessions/{sid}/chat`` and ``/sessions/{sid}/chat/stream`` returned
``metadata.tool_registry`` listing internal retrieval-tool names (``qdrant_search``,
``neo4j_search``, ``poc_search``, ``poc_fetch``, ``fetch_reference``, ``web_fetch``) on
EVERY reply — including a plain greeting, and also on the classifier's own short-circuit
refusals (unsupported-language / injection / offtopic-block). Not visible in the answer
text, but an internal-implementation-detail leak in the API contract.

Confirmed during investigation: the FE (`secai-fe-devspace/src/lib/sse.ts`'s
``FinalPayload`` type) declares ``metadata.tool_registry`` but nothing renders it — so
hiding its VALUE by default is safe. The FE's live "Hoạt động công cụ" timeline
(``ToolTimeline.tsx``, wired up in ``ChatbotScreen.tsx``/``ExploitScreen.tsx``/
``ChatThread.tsx``) DOES consume the SSE ``tool_call``/``tool_result`` frames (tool name +
truncated result), so those are intentionally left untouched by this fix — redacting them
would break a real, user-facing feature.

Fix (``src/secai_gateway/config.py`` + ``src/secai_gateway/api/routes.py``): a new
``expose_internal_tool_metadata`` setting, default ``False``. When off, every chat-profile
``tool_registry`` emission point (``routes.py::_chat_tool_registry()``, wired into the
non-stream ``complete()`` wrapper, ``base_meta()`` used by both stream endpoints, and all
3 of ``_chat_gate()``'s short-circuit branches) returns ``[]`` — the KEY stays and the
VALUE stays a ``list[str]``, only its contents are emptied. Turning the flag on restores
the exact previous (full-list) behavior. Non-chat profiles (translate/report/
exploit_research) are explicitly out of this bug's scope and keep their full
``tool_registry`` in ``base_meta()`` regardless of the flag.

This suite is hermetic (no live server), mirroring ``test_projects_sessions.py``'s
``ProjectFakeRunner`` + ``create_app(runner)`` + ``httpx.ASGITransport`` pattern.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx

from linch.events import PartialAssistantEvent
from linch.sessions.memory import InMemorySessionStore
from linch.tools.base import ToolResult

import secai_gateway.api.routes as routes_mod
from secai_gateway.app import create_app
from secai_gateway.state import ProjectStore

_FULL_TOOL_REGISTRY = sorted(
    {"qdrant_search", "neo4j_search", "poc_search", "poc_fetch", "fetch_reference", "web_fetch"}
)


class _MetaFakeRunner:
    """Runner double whose ``complete``/``stream`` return a metadata dict that ALREADY
    carries a full ``tool_registry`` (mirroring the real ``LinchRunner`` — see
    ``agent/runner.py``'s ``complete()``), so this suite proves routes.py actively gates
    the value back down rather than merely "happening" to pass through an empty one."""

    def __init__(self, tmp_path) -> None:
        self.project_store = ProjectStore(str(tmp_path / "projects.db"))
        self.session_store = InMemorySessionStore()
        self.poc = None

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None,
                        **_kwargs):
        return (f"{profile_name}: {prompt}",
                {"run_id": "run-test", "profile": profile_name, "tool_registry": _FULL_TOOL_REGISTRY})

    async def stream(self, profile_name: str, prompt: str, session_id: str | None = None,
                      **_kwargs):
        yield PartialAssistantEvent(delta={"kind": "text", "text": f"{profile_name}: {prompt}"})


@asynccontextmanager
async def _client(runner):
    app = create_app(runner)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def _make_session(client, owner: str = "alice") -> str:
    proj = (await client.post("/projects", json={"name": "P", "user_id": owner})).json()
    pid = proj["project_id"]
    return (await client.post(f"/projects/{pid}/sessions", json={"user_id": owner})).json()["session_id"]


# ── Default (debug flag OFF): tool_registry key present, empty list ────────────────────

async def test_session_chat_default_hides_tool_registry_but_keeps_key_and_list_shape(tmp_path) -> None:
    runner = _MetaFakeRunner(tmp_path)
    async with _client(runner) as client:
        sid = await _make_session(client)
        # A plain greeting — no gate/refusal involved, exercises the NORMAL completion path.
        resp = await client.post(f"/sessions/{sid}/chat", json={"user_id": "alice", "message": "hello"})
        assert resp.status_code == 200
        metadata = resp.json()["metadata"]
        assert "tool_registry" in metadata, "key must NOT be removed — shape must stay stable"
        assert metadata["tool_registry"] == []
        assert isinstance(metadata["tool_registry"], list)


async def test_session_chat_stream_default_hides_tool_registry(tmp_path) -> None:
    runner = _MetaFakeRunner(tmp_path)
    async with _client(runner) as client:
        sid = await _make_session(client)
        resp = await client.post(f"/sessions/{sid}/chat/stream", json={"user_id": "alice", "message": "hello"})
        assert resp.status_code == 200
        assert "event: final" in resp.text
        assert '"tool_registry": []' in resp.text or '"tool_registry":[]' in resp.text
        for leaked in _FULL_TOOL_REGISTRY:
            assert leaked not in resp.text, f"{leaked!r} leaked into the default SSE final frame"


async def test_language_gate_short_circuit_hides_tool_registry_by_default(tmp_path) -> None:
    """One of the 3 `_chat_gate` short-circuit branches (unsupported-language refusal) —
    proves the fix covers refusal paths too, not just the normal-answer path."""
    runner = _MetaFakeRunner(tmp_path)
    async with _client(runner) as client:
        sid = await _make_session(client)
        # Predominantly non-Latin script (Japanese) trips the language gate deterministically
        # (no embedding call needed) regardless of the input classifier's availability.
        resp = await client.post(f"/sessions/{sid}/chat",
                                 json={"user_id": "alice", "message": "こんにちは、元気ですか、教えてください"})
        assert resp.status_code == 200
        metadata = resp.json()["metadata"]
        assert metadata.get("short_circuit") is True, "expected the language gate to fire"
        assert metadata["tool_registry"] == []


# ── Debug flag ON: exact previous (full-list) behavior restored ────────────────────────

async def test_session_chat_debug_flag_restores_full_tool_registry(tmp_path) -> None:
    runner = _MetaFakeRunner(tmp_path)
    app = create_app(runner)
    async with app.router.lifespan_context(app):
        app.state.gateway.settings.expose_internal_tool_metadata = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            sid = await _make_session(client)
            resp = await client.post(f"/sessions/{sid}/chat", json={"user_id": "alice", "message": "hello"})
            assert resp.status_code == 200
            metadata = resp.json()["metadata"]
            assert metadata["tool_registry"] == _FULL_TOOL_REGISTRY


async def test_session_chat_stream_debug_flag_restores_full_tool_registry(tmp_path) -> None:
    runner = _MetaFakeRunner(tmp_path)
    app = create_app(runner)
    async with app.router.lifespan_context(app):
        app.state.gateway.settings.expose_internal_tool_metadata = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            sid = await _make_session(client)
            resp = await client.post(f"/sessions/{sid}/chat/stream",
                                     json={"user_id": "alice", "message": "hello"})
            assert resp.status_code == 200
            for name in _FULL_TOOL_REGISTRY:
                assert name in resp.text


async def test_language_gate_short_circuit_debug_flag_restores_full_tool_registry(tmp_path) -> None:
    runner = _MetaFakeRunner(tmp_path)
    app = create_app(runner)
    async with app.router.lifespan_context(app):
        app.state.gateway.settings.expose_internal_tool_metadata = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            sid = await _make_session(client)
            resp = await client.post(f"/sessions/{sid}/chat",
                                     json={"user_id": "alice", "message": "こんにちは、元気ですか、教えてください"})
            assert resp.status_code == 200
            metadata = resp.json()["metadata"]
            assert metadata.get("short_circuit") is True
            assert metadata["tool_registry"] == _FULL_TOOL_REGISTRY


# ── Default config value ────────────────────────────────────────────────────────────────

def test_expose_internal_tool_metadata_defaults_off() -> None:
    from secai_gateway.config import Settings
    assert Settings().expose_internal_tool_metadata is False


# ── H2/H3 (2026-09-22): grounding metadata/SSE hygiene ──────────────────────────────────
# H2: non-stream `metadata.sources` used to spread the WHOLE pre-fetch `grounding` dict in
# (`{"name": "qdrant_search", **grounding}`), including `result` — the raw KB dump (up to
# several KB of chunk text) that fed the model's ephemeral context. No FE consumer reads
# `metadata.sources` (grepped: 0 hits in secai-fe-devspace), so nothing rendered it, but a
# plain JSON API client got the entire internal KB payload dumped back instead of a
# citation. Fixed: each source item is now citation-only (name/query/summary) — no `result`.
#
# H3: the synthetic pre-fetch `tool_result` SSE frame (`_grounding_sse_frames`) carried
# `result[:4000]` raw KB text and NO `summary` at all (only the paired `tool_call` frame
# had one) — ToolTimeline.tsx (FE) only ever renders `summary`, falling back to a 140-char
# `result` preview only when `summary` is absent. Fixed: the tool_result frame now always
# carries the same clean `summary` too, and `result` is shrunk to a short preview (kept,
# not dropped — ToolTimeline's `result === undefined` check is how it knows a row is still
# pending). Covers BOTH `/ai/chat` (module-level) and `/sessions/{sid}/chat` (session-scoped)
# since both funnel through the same `complete()`/`_ground_chat_prompt` helpers.
#
# `_qdrant_search` is imported into `secai_gateway.api.routes` at module level and looked
# up as a bare global at call time (see test_chat_grounding_duration_regression.py's own
# note on this seam), so monkeypatching `routes_mod._qdrant_search` reaches the pre-fetch
# grounding path without needing an entity-id message to hit a real KB.

_RAW_KB_DUMP = "RAW KB DUMP — internal Qdrant chunk text, never meant for the wire. " * 100


def _patch_qdrant_search(monkeypatch, *, content: str = _RAW_KB_DUMP,
                         summary: str = "qdrant_search(CVE-2021-44228) 2 hits") -> None:
    async def _fake_qdrant_search(*, query, ctx=None, top_k=10, **kwargs):
        return ToolResult(content=content, summary=summary)
    monkeypatch.setattr(routes_mod, "_qdrant_search", _fake_qdrant_search)


def _tool_result_frames(sse_text: str) -> list[dict]:
    frames = []
    for block in sse_text.split("\n\n"):
        if not block.startswith("event: tool_result"):
            continue
        _, data_line = block.split("\n", 1)
        frames.append(json.loads(data_line[len("data: "):]))
    return frames


async def test_session_chat_sources_are_citation_only_no_raw_kb_dump(tmp_path, monkeypatch) -> None:
    _patch_qdrant_search(monkeypatch)
    runner = _MetaFakeRunner(tmp_path)
    async with _client(runner) as client:
        sid = await _make_session(client)
        resp = await client.post(f"/sessions/{sid}/chat",
                                 json={"user_id": "alice", "message": "CVE-2021-44228 là gì?"})
        assert resp.status_code == 200
        body = resp.json()
        metadata = body["metadata"]
        assert "sources" in metadata
        sources = metadata["sources"]
        assert isinstance(sources, list) and len(sources) == 1
        source = sources[0]
        assert source["name"] == "qdrant_search"
        assert source.get("query")
        assert source.get("summary") == "qdrant_search(CVE-2021-44228) 2 hits"
        assert "result" not in source, "raw KB result must not be spread into metadata.sources"
        assert _RAW_KB_DUMP not in json.dumps(body), "raw KB dump leaked somewhere into the response"


async def test_ai_chat_sources_are_citation_only_no_raw_kb_dump(monkeypatch) -> None:
    """Same fix, via the module-level `/ai/chat` endpoint (not session-scoped)."""
    _patch_qdrant_search(monkeypatch)

    class _PlainFakeRunner:  # /ai/chat needs no project/session store, unlike _MetaFakeRunner
        async def complete(self, profile_name, prompt, session_id=None, **_kwargs):
            return f"{profile_name}: {prompt}", {"run_id": "run-test", "profile": profile_name}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(_PlainFakeRunner())),
                                 base_url="http://test") as client:
        resp = await client.post("/ai/chat", json={"user_id": "u1", "message": "CVE-2021-44228 là gì?"})
    assert resp.status_code == 200
    metadata = resp.json()["metadata"]
    source = metadata["sources"][0]
    assert "result" not in source
    assert source["name"] == "qdrant_search"


async def test_session_chat_stream_tool_result_has_summary_and_shrunk_result(tmp_path, monkeypatch) -> None:
    _patch_qdrant_search(monkeypatch)
    runner = _MetaFakeRunner(tmp_path)
    async with _client(runner) as client:
        sid = await _make_session(client)
        resp = await client.post(f"/sessions/{sid}/chat/stream",
                                 json={"user_id": "alice", "message": "CVE-2021-44228 là gì?"})
        assert resp.status_code == 200
        body = resp.text
        assert "event: tool_result" in body, "tool_result frame must not be removed"
        frames = _tool_result_frames(body)
        prefetch = [f for f in frames if f.get("name") == "qdrant_search"]
        assert prefetch, f"no qdrant_search tool_result frame found; got {frames}"
        frame = prefetch[0]
        assert frame.get("summary") == "qdrant_search(CVE-2021-44228) 2 hits"
        assert "result" in frame, "result key must stay (FE pending-state check)"
        assert len(frame["result"]) <= 200
        assert _RAW_KB_DUMP not in body, "raw KB dump must not appear anywhere in the SSE stream"
