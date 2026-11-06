"""UC suite for 🅱2 lớp 1 — entity-follow-up server-side pre-fetch (2026-09-26).

Bug: ``_ground_chat_prompt`` (api/routes.py) only pre-fetched KB context when the
CURRENT prompt named an explicit CVE/CWE/CAPEC/ATT&CK id (``ENTITY_ID_RES``). A
follow-up like "có CVE nào?" / "còn CVE nào khác không?" right after "CWE-89 là gì?"
carries NO id itself — ``classify_chat_grounding`` routes it as ``id_lookup``
(need_kb=True) but the pre-fetch early-returned ``None`` — so
``RequireChatGroundingHook`` had to force the (weak, Cyber-F1) model to call
``qdrant_search`` on its own, which it usually doesn't, exhausting
``max_retries`` and shipping ``_CHAT_EMPTY_FALLBACK`` (an apology) OVER a draft that
may already have streamed to the client — the "giật"/live≠store bug.

Fix: when the current prompt has no explicit id but is an ``id_lookup`` follow-up,
resolve the entity from the most recent PRIOR **user** turn in the same session
(``_resolve_followup_entity_id``) and pre-fetch KB with ``f"{entity} {prompt}"`` —
same mechanism as the existing explicit-id pre-fetch, so
``chat_prefetch_grounded`` is set and the hook never has to force a regenerate.

Deliberately user-turns-only (not assistant): a prior ANSWER may cite unrelated
example CVEs (e.g. "CWE-89 là gì?" answered with "Ví dụ: CVE-2021-44228..."), and
scanning assistant text would resolve to that stray example instead of the actual
topic the user set. Verified live (isolated instance, real KB): a real answer to
"CWE-89 là gì?" DOES cite several example CVEs — UC5 below pins this behavior with
a fabricated case.

Hermetic — no LLM/network: ``_qdrant_search`` is monkeypatched and history is
seeded directly via ``InMemorySessionStore.append_messages`` (same pattern as
``test_chat_grounding_duration_regression.py`` / ``test_projects_sessions.py``).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx
import pytest

from linch.events import PartialAssistantEvent
from linch.sessions.memory import InMemorySessionStore
from linch.tools.base import ToolResult
from linch.types import Message, TextBlock

import secai_gateway.api.routes as routes
from secai_gateway.app import create_app


class _FollowupFakeRunner:
    """Runner double: REAL in-memory linch session store (so
    ``_resolve_followup_entity_id``'s ``load_messages`` call sees seeded history) +
    a call log recording every ``grounding_context`` the endpoint actually threaded
    through, so tests can assert grounding fired without needing a real model."""

    def __init__(self) -> None:
        self.session_store = InMemorySessionStore()
        self.poc = None  # /health uses getattr(..., "poc", None)
        self.calls: list[tuple[str, str, str | None, str | None]] = []

    async def complete(self, profile_name, prompt, session_id=None, *, grounding_context=None):
        self.calls.append((profile_name, prompt, session_id, grounding_context))
        return f"{profile_name}: {prompt}", {"run_id": "run-test", "profile": profile_name}

    async def stream(self, profile_name, prompt, session_id=None, *, grounding_context=None):
        self.calls.append((profile_name, prompt, session_id, grounding_context))
        yield PartialAssistantEvent(delta={"kind": "text", "text": f"{profile_name}: {prompt}"})


@asynccontextmanager
async def _client(runner):
    app = create_app(runner)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client


def _tool_result_frames(sse_text: str) -> list[dict]:
    frames = []
    for block in sse_text.split("\n\n"):
        if not block.startswith("event: tool_result"):
            continue
        _, data_line = block.split("\n", 1)
        frames.append(json.loads(data_line[len("data: "):]))
    return frames


def _prefetch_qdrant_frame(sse_text: str) -> dict | None:
    frames = _tool_result_frames(sse_text)
    prefetch = [f for f in frames
                if f.get("name") == "qdrant_search" and str(f.get("id", "")).startswith("pre-fetch-")]
    return prefetch[0] if prefetch else None


async def _seed_user_turn(runner: _FollowupFakeRunner, sid: str, text: str) -> None:
    await runner.session_store.append_messages(sid, [Message(role="user", content=[TextBlock(text=text)])])


async def _seed_assistant_turn(runner: _FollowupFakeRunner, sid: str, text: str) -> None:
    await runner.session_store.append_messages(sid, [Message(role="assistant", content=[TextBlock(text=text)])])


def _fake_qdrant_search(captured: list[str]):
    async def _search(*, query, ctx=None, top_k=10, **kwargs):
        captured.append(query)
        return ToolResult(content=f"[KB] {query} — kết quả tra cứu giả lập.",
                          summary=f"qdrant_search({query}) 1 hit")
    return _search


# ── UC1 — entity-follow-up right after an explicit-id turn resolves + pre-fetches ────

@pytest.mark.asyncio
async def test_uc1_followup_without_id_resolves_entity_from_prior_user_turn(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(routes, "_qdrant_search", _fake_qdrant_search(captured))
    runner = _FollowupFakeRunner()
    async with _client(runner) as client:
        created = await client.post("/sessions", json={"user_id": "u1", "title": "t"})
        sid = created.json()["session_id"]
        await _seed_user_turn(runner, sid, "CWE-89 là gì?")

        resp = await client.post(f"/sessions/{sid}/chat/stream",
                                 json={"user_id": "u1", "message": "có CVE nào?"})
    assert resp.status_code == 200
    frame = _prefetch_qdrant_frame(resp.text)
    assert frame is not None, "expected a synthetic pre-fetch qdrant_search tool_result frame"
    assert captured, "_qdrant_search was never called — no pre-fetch happened"
    assert "CWE-89" in captured[-1], f"resolved entity missing from search query: {captured}"
    # The prompt persisted/forwarded to the runner must stay the ORIGINAL clean text
    # (grounding travels via grounding_context, never baked into the prompt string).
    profile_name, prompt, call_sid, grounding_context = runner.calls[-1]
    assert prompt == "có CVE nào?"
    assert grounding_context, "grounding_context must be set when pre-fetch succeeded"
    assert call_sid == sid


# ── UC2 — chained follow-up (2 hops back) still resolves the same entity ────────────

@pytest.mark.asyncio
async def test_uc2_chained_followup_resolves_across_multiple_hops(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(routes, "_qdrant_search", _fake_qdrant_search(captured))
    runner = _FollowupFakeRunner()
    async with _client(runner) as client:
        created = await client.post("/sessions", json={"user_id": "u1", "title": "t"})
        sid = created.json()["session_id"]
        await _seed_user_turn(runner, sid, "CWE-89 là gì?")
        await _seed_assistant_turn(runner, sid, "CWE-89 là SQL Injection.")
        await _seed_user_turn(runner, sid, "có CVE nào?")
        await _seed_assistant_turn(runner, sid, "Có, ví dụ CVE-2021-1111.")

        resp = await client.post(f"/sessions/{sid}/chat/stream",
                                 json={"user_id": "u1", "message": "còn CVE nào khác không?"})
    assert resp.status_code == 200
    assert captured, "no pre-fetch happened for the 2nd-hop follow-up"
    assert "CWE-89" in captured[-1], f"expected CWE-89 resolved from the earliest user turn: {captured}"


# ── UC3 — no prior entity anywhere ────────────────────────────────────────────────
# Before 2026-10-06 this fell back to "no pre-fetch" and the model, left to call the tool itself, usually
# did not (core eval: "CVE nào…" questions answered with the default apology). Now the question itself is
# searched; CHAT_CVE_SEARCH_PREFETCH_ENABLED=false restores the old behaviour.

@pytest.mark.asyncio
async def test_uc3_no_prior_entity_searches_with_the_question_itself(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(routes, "_qdrant_search", _fake_qdrant_search(captured))
    runner = _FollowupFakeRunner()
    async with _client(runner) as client:
        created = await client.post("/sessions", json={"user_id": "u1", "title": "t"})
        sid = created.json()["session_id"]
        resp = await client.post(f"/sessions/{sid}/chat/stream",
                                 json={"user_id": "u1", "message": "có CVE nào?"})
    assert resp.status_code == 200
    assert captured == ["có CVE nào?"]
    assert _prefetch_qdrant_frame(resp.text) is not None
    assert runner.calls[-1][3]


@pytest.mark.asyncio
async def test_uc3_flag_off_keeps_the_old_no_prefetch_behaviour(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(routes, "_qdrant_search", _fake_qdrant_search(captured))
    monkeypatch.setenv("CHAT_CVE_SEARCH_PREFETCH_ENABLED", "false")
    runner = _FollowupFakeRunner()
    async with _client(runner) as client:
        sid = (await client.post("/sessions", json={"user_id": "u1", "title": "t"})).json()["session_id"]
        resp = await client.post(f"/sessions/{sid}/chat/stream", json={"user_id": "u1", "message": "có CVE nào?"})
    assert resp.status_code == 200 and not captured
    assert _prefetch_qdrant_frame(resp.text) is None and not runner.calls[-1][3]


# ── UC4 — a concept question (no id, not an id_lookup phrasing) is untouched ────────

@pytest.mark.asyncio
async def test_uc4_plain_concept_question_still_skips_prefetch(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(routes, "_qdrant_search", _fake_qdrant_search(captured))
    runner = _FollowupFakeRunner()
    async with _client(runner) as client:
        created = await client.post("/sessions", json={"user_id": "u1", "title": "t"})
        sid = created.json()["session_id"]
        await _seed_user_turn(runner, sid, "CWE-89 là gì?")

        resp = await client.post(f"/sessions/{sid}/chat/stream",
                                 json={"user_id": "u1", "message": "SQL injection là gì?"})
    assert resp.status_code == 200
    assert not captured, "a plain concept question (route=concept) must not trigger pre-fetch"


# ── UC5 — entity resolution scans USER turns only, never assistant text ─────────────

@pytest.mark.asyncio
async def test_uc5_resolution_ignores_assistant_only_entities(monkeypatch) -> None:
    """A prior ANSWER citing an unrelated example CVE must not hijack resolution —
    the conversation topic is whatever the USER asked about, not what the model
    happened to cite. Reproduces the real KB behavior observed live: answering
    "CWE-89 là gì?" cites several example CVEs in the assistant's own text."""
    captured: list[str] = []
    monkeypatch.setattr(routes, "_qdrant_search", _fake_qdrant_search(captured))
    runner = _FollowupFakeRunner()
    async with _client(runner) as client:
        created = await client.post("/sessions", json={"user_id": "u1", "title": "t"})
        sid = created.json()["session_id"]
        await _seed_user_turn(runner, sid, "CWE-89 là gì?")
        # Assistant cites a DIFFERENT, unrelated-looking example CVE in its answer.
        await _seed_assistant_turn(
            runner, sid,
            "CWE-89 là SQL Injection. Ví dụ nổi tiếng: CVE-2021-44228 (Log4Shell).",
        )
        resp = await client.post(f"/sessions/{sid}/chat/stream",
                                 json={"user_id": "u1", "message": "có CVE nào?"})
    assert resp.status_code == 200
    assert captured, "expected a pre-fetch (CWE-89 from the user turn is resolvable)"
    assert "CWE-89" in captured[-1]
    assert "CVE-2021-44228" not in captured[-1], (
        "resolution must prefer the USER's own CWE-89 topic over an incidental CVE "
        "the assistant happened to cite in its previous answer"
    )


# ── UC6 — explicit id in the CURRENT prompt is unaffected (regression guard) ────────

@pytest.mark.asyncio
async def test_uc6_explicit_current_id_path_unchanged(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(routes, "_qdrant_search", _fake_qdrant_search(captured))
    runner = _FollowupFakeRunner()
    async with _client(runner) as client:
        created = await client.post("/sessions", json={"user_id": "u1", "title": "t"})
        sid = created.json()["session_id"]
        # No history at all — the id is right there in the current prompt.
        resp = await client.post(f"/sessions/{sid}/chat/stream",
                                 json={"user_id": "u1", "message": "CVE-2021-44228 là gì?"})
    assert resp.status_code == 200
    assert captured and captured[-1] == "CVE-2021-44228 là gì?", (
        "explicit-id pre-fetch must still search the prompt verbatim, unchanged by "
        "the follow-up-resolution addition"
    )


# ── UC7 — stateless /ai/chat/stream (no session id) degrades gracefully ────────────

@pytest.mark.asyncio
async def test_uc7_no_session_id_fails_open_no_crash(monkeypatch) -> None:
    """/ai/chat/stream derives a session id from user_id (not None in practice), but
    ``_resolve_followup_entity_id`` must fail open to None for ANY falsy/missing
    session id or store error — never raise and never block the chat."""
    captured: list[str] = []
    monkeypatch.setattr(routes, "_qdrant_search", _fake_qdrant_search(captured))
    runner = _FollowupFakeRunner()
    async with _client(runner) as client:
        resp = await client.post("/ai/chat/stream", json={"user_id": "fresh-user", "message": "có CVE nào?"})
    assert resp.status_code == 200
    # brand-new derived session has no history → no entity to resolve; the question itself is searched
    assert captured == ["có CVE nào?"]
