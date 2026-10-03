"""Regression suite for the chat-history-leak bug (2026-09-21).

Bug: switching away from and back to the chatbot re-displayed the raw internal prompt
(``<env>...``, ``[Ngữ cảnh tra cứu...]`` + KB dump, ``[Câu hỏi của người dùng]``) instead
of the user's own message. Root cause: ``api/routes.py::_ground_chat_prompt()`` used to
splice the KB-grounding block (and, one layer down in ``agent/runner.py``, a VN date
line) directly INTO the string handed to ``LinchRunner.complete()/stream()``, which then
called ``session.run(that_string)`` — and linch persists EXACTLY the string passed to
``session.run()`` as the user turn (``linch/loop/runner.py``: ``build_user_message(prompt)``
→ ``session.append([user_message])``). Whatever ``GET /sessions/{sid}/messages`` later
returns for that turn is that same persisted content, verbatim.

Fix (see ``git diff`` — agent/hooks.py, agent/runner.py, agent/factory.py, api/routes.py,
api/projects.py): date + KB grounding are now delivered to the model via linch's per-turn
``build_context`` channel (``EphemeralTurnContextHook``, agent/hooks.py) — appended to the
outgoing provider request ONLY, never to ``session.provider_view``/``full_history``/the
store. ``session.run()`` is now always called with the caller's original, unwrapped text.

This suite proves, at three levels:
  1. (linch-integration, real Agent/Session/store, no gateway HTTP layer) the persisted
     user turn is clean while the model-bound provider request still carries the
     grounding — i.e. the fix doesn't regress faithfulness by silently dropping context.
  2. (api/routes.py, FakeRunner) routes.py hands the runner the CLEAN prompt and a
     SEPARATE ``grounding_context`` string — it no longer builds a combined/marked prompt.
  3. (api/projects.py) the ``GET /sessions/{sid}/messages`` response strips linch's own
     unavoidable ``<env>...</env>`` preamble block (see that module's comment for why it
     can't be suppressed at the source) so the FE-visible turn is fully clean too.
  4. (agent/hooks.py) ``RequireChatGroundingHook`` no longer depends on sniffing
     ``CHAT_PRE_FETCH_MARKER`` inside the (now-clean) prompt to know pre-fetch already
     satisfied the KB requirement — it reads the explicit ``chat_prefetch_grounded`` flag.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from linch import Agent, InMemoryRunStore, InMemorySessionStore
from linch.config import FeatureFlags
from linch.evals import ScriptedProvider, TextTurn
from linch.hooks.contexts import BeforeFinalAnswerContext
from linch.sessions.store import StoredMessage
from linch.skills.system_reminder import wrap_in_system_reminder
from linch.tools.registry import empty_tools
from linch.types import Message, SystemBlock, TextBlock, ToolResultBlock, ToolUseBlock, message_to_dict

from secai_gateway.agent.hooks import (
    CHAT_PRE_FETCH_MARKER,
    EphemeralTurnContextHook,
    RequireChatGroundingHook,
)
from secai_gateway.agent.runner import _ephemeral_context_text
from secai_gateway.api.projects import _display_message_dict, _filter_display_rows, _is_retry_tail_message
from secai_gateway.app import create_app

# Markers OUR OWN code used to bake into the persisted prompt (the high-severity part of
# the leak: a multi-KB KB dump + injection-shaped markers). Fully eliminable — asserted
# absent everywhere below, including in the raw session-store row.
_OUR_LEAK_MARKERS = (
    CHAT_PRE_FETCH_MARKER,
    "### Result",
    "[Câu hỏi của người dùng]",
    "[Hôm nay là",
)
# linch's own <env>Today's date: ...</env> preamble (linch/loop/request.py::
# build_user_message) is a SEPARATE, library-internal, unconditional addition — it is
# NOT eliminable from the raw store via any public hook/config (see
# EphemeralTurnContextHook's docstring), only filterable at the API response layer
# (api/projects.py::_display_message_dict). Tracked separately, not lumped into the
# "fully eliminable" set above.
_LINCH_ENV_MARKERS = ("<env>", "Today's date")
_LEAK_MARKERS = _OUR_LEAK_MARKERS + _LINCH_ENV_MARKERS


class _RecordingScriptedProvider(ScriptedProvider):
    """ScriptedProvider that also records the last ProviderRequest it received, so tests
    can assert what the MODEL actually saw (system blocks, messages) independent of what
    got persisted."""

    def __init__(self, turns: list[Any]) -> None:
        super().__init__(turns)
        self.requests: list[Any] = []

    async def stream(self, req):
        self.requests.append(req)
        async for chunk in super().stream(req):
            yield chunk


@pytest.mark.asyncio
async def test_ephemeral_context_not_persisted_but_reaches_model() -> None:
    """Core fix, exercised against REAL linch (Agent/Session/InMemorySessionStore) — no
    gateway HTTP layer, no mocks of linch itself."""
    provider = _RecordingScriptedProvider([TextTurn("Log4Shell là CVE-2021-44228.")])
    session_store = InMemorySessionStore()
    agent = Agent(
        model="scripted",
        provider=provider,
        tools=empty_tools(),
        session_store=session_store,
        run_store=InMemoryRunStore(),
        features=FeatureFlags(skills=False, subagents=False, mcp=False),
        hooks=[EphemeralTurnContextHook()],
        max_turns=2,
    )

    session = await agent.session(id="leak-fix-1")
    session.system_blocks_override = [SystemBlock(text="You are IAS.AI chat.")]
    grounding_block = (
        CHAT_PRE_FETCH_MARKER + " — PHẢI dùng để trả lời chính xác, KHÔNG bịa/suy đoán "
        "khi thiếu thông tin]\n### Result 1 (score: 1.7250)\nCVE-2021-44228 — Log4Shell — "
        "CVSS 10.0 — KEV: yes"
    )
    session.meta = {
        "profile": "chat",
        "last_user_prompt": "CVE-2021-44228",
        "chat_prefetch_grounded": True,
        "ephemeral_context": "[Hôm nay là Thứ 2, ngày 21/09/2026 (giờ Việt Nam)]\n\n" + grounding_block,
    }

    clean_prompt = "CVE-2021-44228"
    events = [event async for event in session.run(clean_prompt)]
    assert events, "run produced no events"

    # 1) STORED history is clean: the persisted user turn must not carry any of the
    #    internal-prompt leak markers, and must literally contain the user's own text.
    stored = await session_store.load_messages("leak-fix-1")
    user_rows = [r for r in stored if r.message.role == "user"]
    assert user_rows, "no user turn was persisted"
    stored_text = " ".join(
        block.text for block in user_rows[0].message.content if hasattr(block, "text")
    )
    for marker in _OUR_LEAK_MARKERS:
        assert marker not in stored_text, f"leaked marker {marker!r} found in stored user turn: {stored_text!r}"
    assert clean_prompt in stored_text

    # 2) The MODEL still received the grounding + date — proves the fix didn't just
    #    delete grounding (which would regress faithfulness). Delivered as an EXTRA
    #    `messages` entry appended after session.provider_view (_build_turn_request),
    #    NOT via `system` — see EphemeralTurnContextHook's docstring for why: folding it
    #    into `system` would sit in front of the tool schemas in the rendered prompt and
    #    break vLLM's system+tools prefix cache on every turn.
    assert provider.requests, "provider never received a request"
    last_req = provider.requests[-1]
    system_text = "\n".join(getattr(b, "text", "") for b in (last_req.system or []))
    assert system_text == "You are IAS.AI chat.", (
        "system blocks must stay byte-identical/cacheable — ephemeral content leaked into `system`")
    req_messages_text = "\n".join(
        block.text for m in last_req.messages for block in m.content if hasattr(block, "text")
    )
    assert CHAT_PRE_FETCH_MARKER in req_messages_text
    assert "CVE-2021-44228" in req_messages_text  # the KB content itself
    assert "Hôm nay là" in req_messages_text
    # And it's a message DISTINCT from (appended after) the real persisted question.
    assert last_req.messages[-1].content[0].text != clean_prompt

    # 3) The linch-internal <env> block is present in the RAW store (library behavior,
    #    not suppressible via any public hook/config — see agent/hooks.py's long comment
    #    on EphemeralTurnContextHook) but is filtered out at the API response layer by
    #    api/projects.py::_display_message_dict (checked separately below).
    raw_dict = message_to_dict(user_rows[0].message)
    assert any(
        block.get("type") == "text" and block.get("text", "").startswith("<env>")
        for block in raw_dict["content"]
    ), "expected linch's own <env> block to still be present in the raw store row"


def test_display_message_dict_strips_env_block_for_api_response() -> None:
    """api/projects.py's GET /sessions/{sid}/messages must not surface linch's internal
    <env>Today's date: ...</env> preamble to the FE, even though the raw store row has it
    (see the assertion above) — the persisted row itself is untouched (no schema change),
    only what THIS endpoint returns is filtered."""
    from linch.types import Message, TextBlock

    message = Message(
        role="user",
        content=[
            TextBlock(text="<env>\nToday's date: 2026-09-21\n</env>"),
            TextBlock(text="CVE-2021-44228"),
        ],
    )
    out = _display_message_dict(message)
    texts = [b["text"] for b in out["content"]]
    assert texts == ["CVE-2021-44228"]

    # Never drops the LAST remaining block even if it looks env-like (defensive: a
    # content-only-env message still round-trips instead of vanishing).
    only_env = Message(role="user", content=[TextBlock(text="<env>\nToday's date: 2026-09-21\n</env>")])
    out2 = _display_message_dict(only_env)
    assert out2["content"], "must not drop the only content block"


def test_display_message_dict_recovers_question_from_pre_fix_contaminated_turn() -> None:
    """MAJOR-2 (Opus review round 2, 2026-09-21): a session with a turn saved BEFORE this
    fix landed has the KB-grounding block baked directly into the persisted user-turn text
    (the exact leak this whole change targets) — the env-block strip alone does nothing
    for it, since the contaminated text is the SECOND block, not the env one. Reloading
    such an old session must show the user's real original question, not the marker/KB
    dump/"[Câu hỏi của người dùng]" scaffolding."""
    from linch.types import Message, TextBlock

    contaminated_text = (
        CHAT_PRE_FETCH_MARKER + " — PHẢI dùng để trả lời chính xác, KHÔNG bịa/suy đoán "
        "khi thiếu thông tin]\n### Result 1 (score: 1.7250)\nCVE-2021-44228 — Log4Shell — "
        "CVSS 10.0 — KEV: yes\n\n[Câu hỏi của người dùng]\nCVE-2021-44228"
    )
    message = Message(
        role="user",
        content=[
            TextBlock(text="<env>\nToday's date: 2026-09-20\n</env>"),
            TextBlock(text=contaminated_text),
        ],
    )
    out = _display_message_dict(message)
    texts = [b["text"] for b in out["content"]]
    assert texts == ["CVE-2021-44228"], f"expected only the recovered clean question, got {texts!r}"
    joined = " ".join(texts)
    for marker in _OUR_LEAK_MARKERS + _LINCH_ENV_MARKERS:
        assert marker not in joined, f"leaked marker {marker!r} survived recovery: {joined!r}"

    # assistant/tool content must NEVER be touched by the marker-recovery logic, even if
    # (implausibly) it starts with the same marker text.
    assistant_message = Message(role="assistant", content=[TextBlock(text=contaminated_text)])
    assistant_out = _display_message_dict(assistant_message)
    assert assistant_out["content"][0]["text"] == contaminated_text

    # Marker present but the "[Câu hỏi của người dùng]" split point missing (can't recover
    # the real question) → the whole grounding-dump block is dropped, never shown raw.
    unsplittable = CHAT_PRE_FETCH_MARKER + " — ...]\n### Result 1\nsome KB dump with no split marker"
    message2 = Message(role="user", content=[TextBlock(text="hello"), TextBlock(text=unsplittable)])
    out2 = _display_message_dict(message2)
    texts2 = [b["text"] for b in out2["content"]]
    assert texts2 == ["hello"], f"unsplittable grounding dump must be dropped entirely, got {texts2!r}"


class _CapturingFakeRunner:
    """Records exactly what api/routes.py hands the runner — the (prompt, grounding_context)
    split is the thing under test here, not the model's actual behavior."""

    def __init__(self) -> None:
        self.complete_calls: list[dict[str, Any]] = []

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None,
                        *, grounding_context: str | None = None):
        self.complete_calls.append(
            {"profile": profile_name, "prompt": prompt, "session_id": session_id,
             "grounding_context": grounding_context}
        )
        return f"{profile_name}: {prompt}", {"run_id": "run-test", "profile": profile_name}

    async def stream(self, profile_name: str, prompt: str, session_id: str | None = None,
                      *, grounding_context: str | None = None):
        from linch.events import PartialAssistantEvent
        self.complete_calls.append(
            {"profile": profile_name, "prompt": prompt, "session_id": session_id,
             "grounding_context": grounding_context}
        )
        yield PartialAssistantEvent(delta={"kind": "text", "text": f"{profile_name}: {prompt}"})


@pytest.mark.asyncio
async def test_routes_send_clean_prompt_and_separate_grounding_context(monkeypatch) -> None:
    """/ai/chat (non-stream) with a real CVE id: the runner must receive the ORIGINAL
    clean prompt as `prompt`, and the KB dump/marker separately as `grounding_context` —
    never combined into one string (that combination was the leak)."""
    import secai_gateway.api.routes as routes
    from linch.tools.base import ToolResult

    async def _fake_qdrant_search(*, query, ctx=None, top_k=10, **kwargs):
        return ToolResult(content="### Result 1 (score: 1.7250)\nCVE-2021-44228 — Log4Shell",
                          summary="qdrant_search(1 hit)")

    monkeypatch.setattr(routes, "_qdrant_search", _fake_qdrant_search)
    fake = _CapturingFakeRunner()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(fake)),
                                 base_url="http://test") as client:
        resp = await client.post("/ai/chat", json={"user_id": "u1", "message": "CVE-2021-44228"})
    assert resp.status_code == 200
    assert fake.complete_calls, "runner.complete was never called"
    call = fake.complete_calls[-1]
    assert call["prompt"] == "CVE-2021-44228"  # clean — no marker/KB dump baked in
    assert call["grounding_context"] is not None
    assert CHAT_PRE_FETCH_MARKER in call["grounding_context"]
    assert "### Result 1" in call["grounding_context"]
    # And the KB source is still surfaced to the client for citation, unaffected by the split.
    assert resp.json()["metadata"]["sources"][0]["name"] == "qdrant_search"


@pytest.mark.asyncio
async def test_routes_stream_send_clean_prompt_and_separate_grounding_context(monkeypatch) -> None:
    """Same split, over the SSE path (/ai/chat/stream)."""
    import secai_gateway.api.routes as routes
    from linch.tools.base import ToolResult

    async def _fake_qdrant_search(*, query, ctx=None, top_k=10, **kwargs):
        return ToolResult(content="### Result 1 (score: 1.7250)\nCVE-2021-44228 — Log4Shell",
                          summary="qdrant_search(1 hit)")

    monkeypatch.setattr(routes, "_qdrant_search", _fake_qdrant_search)
    fake = _CapturingFakeRunner()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(fake)),
                                 base_url="http://test") as client:
        resp = await client.post("/ai/chat/stream", json={"user_id": "u1", "message": "CVE-2021-44228"})
    assert resp.status_code == 200
    assert fake.complete_calls, "runner.stream was never called"
    call = fake.complete_calls[-1]
    assert call["prompt"] == "CVE-2021-44228"
    assert call["grounding_context"] is not None
    assert CHAT_PRE_FETCH_MARKER in call["grounding_context"]
    for marker in _LEAK_MARKERS:
        assert marker not in call["prompt"]


@pytest.mark.asyncio
async def test_chat_grounding_hook_uses_explicit_flag_not_marker_sniff() -> None:
    """RequireChatGroundingHook must recognize pre-fetch grounding via the explicit
    session.meta["chat_prefetch_grounded"] flag (agent/runner.py::_apply_profile), NOT by
    sniffing CHAT_PRE_FETCH_MARKER inside the (now always-clean) prompt — otherwise, after
    this fix, EVERY pre-fetch-grounded chat turn would look "ungrounded" to the hook and
    it would force wasteful/harmful regenerate loops (or, worse, overwrite an already-
    correct pre-fetched answer with the empty-fallback text once retries exhausted)."""

    class _FakeSession:
        def __init__(self, meta: dict[str, Any]) -> None:
            self.meta = meta

    hook = RequireChatGroundingHook(profile="chat")
    session = _FakeSession({
        "profile": "chat",
        "last_user_prompt": "CVE-2021-44228",  # clean — no marker present anymore
        "chat_prefetch_grounded": True,
    })
    ctx = BeforeFinalAnswerContext(
        session=session, run_id="run-1", turn_index=0, deps=None,
        final_text="Log4Shell là CVE-2021-44228, CVSS 10.0.",
    )

    result = await hook.on_before_final_answer(ctx)
    # need_kb was satisfied by the explicit flag despite no qdrant_search/neo4j_search
    # tool call being recorded for this run_id — must NOT force a retry.
    assert result is None, f"expected no force_continue/mutate, got {result!r}"


# ── 2026-09-22 regression: date-banner mistaken for a new user turn ────────────────────
#
# Bug (separate from, but adjacent to, the leak fix above): EphemeralTurnContextHook
# delivers ``_ephemeral_context_text()``'s output as a SEPARATE ``Message(role="user")``
# appended AFTER the real, already-persisted question (linch's ``Message.role`` only
# supports "user"/"assistant", and the OpenAI-chat-completions serializer emits each
# Message as its own dict — it never merges consecutive same-role messages). Live
# reproduction (isolated instance, 2026-09-22): asking "OWASP Top 10 2021 gồm những
# gì?" made the model answer the bare trailing date line instead of the real question —
# "Cảm ơn bạn đã cung cấp thông tin ngày tháng...". Fix: ``_ephemeral_context_text()``
# now wraps its output in explicit open/close markers stating this is internal,
# non-conversational context (not a new question) and instructing the model to keep
# answering the real question above it. These tests pin that wrapping's presence/
# structure at the unit level (the live behavior itself was verified manually against a
# running instance, not re-asserted here).
_EPHEMERAL_OPEN_MARKER = "[GHI CHÚ NGỮ CẢNH HỆ THỐNG"
_EPHEMERAL_CLOSE_MARKER = "[HẾT GHI CHÚ NGỮ CẢNH"


def test_ephemeral_context_text_date_only_has_wrapper_and_no_grounding_mention() -> None:
    text = _ephemeral_context_text("chat", None)
    assert text is not None
    assert _EPHEMERAL_OPEN_MARKER in text
    assert _EPHEMERAL_CLOSE_MARKER in text
    assert "[Hôm nay là" in text
    # Date-only case: must not claim it also carries KB grounding data.
    assert "dữ liệu tra cứu KB" not in text
    # The close marker must anchor the model back onto the real (preceding) question,
    # not invite it to treat this message as a new question to process.
    assert "câu hỏi" in text.lower()


def test_ephemeral_context_text_with_grounding_mentions_kb_and_keeps_marker() -> None:
    grounding_block = (
        CHAT_PRE_FETCH_MARKER + " — PHẢI dùng để trả lời chính xác, KHÔNG bịa/suy đoán "
        "khi thiếu thông tin]\n### Result 1 (score: 1.7250)\nCVE-2021-44228 — Log4Shell — "
        "CVSS 10.0 — KEV: yes"
    )
    text = _ephemeral_context_text("chat", grounding_block)
    assert text is not None
    assert _EPHEMERAL_OPEN_MARKER in text
    assert _EPHEMERAL_CLOSE_MARKER in text
    assert "dữ liệu tra cứu KB" in text
    # The pre-fetch marker + KB body must still be carried through unchanged.
    assert CHAT_PRE_FETCH_MARKER in text
    assert "CVE-2021-44228" in text
    assert "[Hôm nay là" in text  # date line still present alongside grounding


def test_ephemeral_context_text_translate_profile_skips_date_and_stays_none_without_grounding() -> None:
    # translate has no date/context need (see _ephemeral_context_text's docstring) — with
    # no grounding_context either, there is nothing to deliver at all.
    assert _ephemeral_context_text("translate", None) is None


# ── 2026-09-26 regression: retry-tail persistence leak + phantom "regenerate" ───────────
#
# Bug 1 (BE hook cap vs linch's real reentry cap): RequireChatGroundingHook's own
# `max_retries` used to default to 3, but linch only ever HONORS ONE `force_continue`-induced
# reentry per run (`linch/loop/runner.py::_MAX_FINAL_ANSWER_REENTRIES = 1`, hardcoded, not
# configurable). Past that one real reentry, `api/sse.py::event_to_sse` still turns the hook's
# 2nd/3rd `force_continue` into an SSE `regenerate` frame (it maps on the HookEventRecord's
# raw action, before linch's own reentry-budget check runs) — but linch silently ships the
# STALE draft instead of actually re-running the model. The FE resets its buffer for nothing.
# Fix: this hook's default now matches linch's real cap (1), so past that one attempt it falls
# back to `HookResult.mutate(...)` (never a 2nd `force_continue`) — `mutate` is not one of
# `api/sse.py::_REGENERATE_ACTIONS`, so no phantom `regenerate` frame is ever sent for it.
#
# Bug 2 (persistence leak): when that one real retry happens, linch's own
# `loop/terminals.py::_gate_retry_tail` appends the hook's English repair feedback as a
# role=user message wrapped in `<system-reminder>...</system-reminder>`
# (`linch/skills/system_reminder.py::wrap_in_system_reminder`) — plus the model's rejected
# draft answer right before it. `GET /sessions/{sid}/messages` used to show both verbatim.
# Fix: `api/projects.py::_filter_display_rows` drops the retry-tail row and the rejected
# draft that triggered it, so only [user question] -> [assistant final answer] remains.


def _row(seq: int, message: Message) -> StoredMessage:
    return StoredMessage(seq=seq, appended_at=f"2026-09-26T00:00:{seq:02d}Z", message=message)


def test_require_chat_grounding_hook_default_matches_linch_reentry_cap() -> None:
    """The hook's own force_continue budget must not exceed linch's real
    `_MAX_FINAL_ANSWER_REENTRIES` (1) — see the module comment above for the mechanism."""
    hook = RequireChatGroundingHook(profile="chat")
    assert hook.max_retries == 1


@pytest.mark.asyncio
async def test_require_chat_grounding_hook_never_force_continues_past_one_reentry() -> None:
    """First call (KB not yet satisfied): a real, honored force_continue. Second call, same
    run, still not satisfied: must NOT force_continue again (that's the exact phantom-
    regenerate bug) — falls back to the anti-empty/anti-fabrication floor (`mutate`) instead."""

    class _FakeSession:
        def __init__(self, meta: dict[str, Any]) -> None:
            self.meta = meta

    hook = RequireChatGroundingHook(profile="chat")
    session = _FakeSession({
        "profile": "chat",
        "last_user_prompt": "CVE-2021-44228 có trong KEV không?",
        # no chat_prefetch_grounded flag and no qdrant/neo4j tool call recorded for this
        # run_id — KB requirement is genuinely unsatisfied, both calls below.
    })
    ctx = BeforeFinalAnswerContext(
        session=session, run_id="run-phantom-1", turn_index=0, deps=None,
        final_text="Có, CVE-2021-44228 nằm trong KEV.",
    )

    first = await hook.on_before_final_answer(ctx)
    assert first is not None and first.action == "force_continue", (
        "the ONE reentry linch actually honors must still happen")

    second = await hook.on_before_final_answer(ctx)
    assert second is not None
    assert second.action != "force_continue", (
        "a 2nd force_continue here is exactly the phantom-regenerate bug: linch won't honor "
        "it (reentry budget already spent) but api/sse.py would still emit a `regenerate` "
        "SSE frame for it")
    assert second.action == "mutate"


def test_is_retry_tail_message_matches_gate_retry_tail_shape_only() -> None:
    feedback = "Route=entity/kb: call qdrant_search or neo4j_search for CVE/CWE evidence."
    retry_tail = Message(role="user", content=[TextBlock(text=wrap_in_system_reminder(feedback))])
    assert _is_retry_tail_message(retry_tail) is True

    # A real user message that merely mentions the tag text is NOT mistaken for one — only
    # linch's own exact single-block wrapper shape matches.
    lookalike = Message(role="user", content=[TextBlock(text="what does <system-reminder> mean?")])
    assert _is_retry_tail_message(lookalike) is False

    # Same wrapped text on the assistant role never matches (linch never produces this on
    # assistant turns, but the check must be role-strict regardless).
    assistant_copy = Message(role="assistant", content=[TextBlock(text=wrap_in_system_reminder(feedback))])
    assert _is_retry_tail_message(assistant_copy) is False

    # A real user message plus other content blocks alongside a system-reminder-shaped text
    # block does not match either — linch's wrapper is always the ONLY block in the message.
    multi_block = Message(role="user", content=[
        TextBlock(text=wrap_in_system_reminder(feedback)), TextBlock(text="also this"),
    ])
    assert _is_retry_tail_message(multi_block) is False


def test_filter_display_rows_drops_retry_tail_and_its_rejected_draft() -> None:
    rows = [
        _row(1, Message(role="user", content=[TextBlock(text="OWASP Top 10 2025 gồm gì?")])),
        _row(2, Message(role="assistant", content=[TextBlock(text="(rejected draft, no grounding)")])),
        _row(3, Message(role="user", content=[
            TextBlock(text=wrap_in_system_reminder("Route=taxonomy: call web_fetch on the canonical page.")),
        ])),
        _row(4, Message(role="assistant", content=[TextBlock(text="(final grounded answer)")])),
    ]
    kept = _filter_display_rows(rows)
    assert [r.seq for r in kept] == [1, 4]
    assert kept[0].message.content[0].text == "OWASP Top 10 2025 gồm gì?"
    assert kept[1].message.content[0].text == "(final grounded answer)"


def test_filter_display_rows_drops_every_round_of_multiple_retries() -> None:
    """Old (pre-2026-09-26) sessions could have up to 3 retry rounds — every rejected draft
    must be dropped, leaving only the single accepted final."""
    rows = [
        _row(1, Message(role="user", content=[TextBlock(text="Q")])),
        _row(2, Message(role="assistant", content=[TextBlock(text="draft 1")])),
        _row(3, Message(role="user", content=[TextBlock(text=wrap_in_system_reminder("feedback 1"))])),
        _row(4, Message(role="assistant", content=[TextBlock(text="draft 2")])),
        _row(5, Message(role="user", content=[TextBlock(text=wrap_in_system_reminder("feedback 2"))])),
        _row(6, Message(role="assistant", content=[TextBlock(text="final")])),
    ]
    kept = _filter_display_rows(rows)
    assert [r.seq for r in kept] == [1, 6]
    assert kept[1].message.content[0].text == "final"


def test_filter_display_rows_keeps_tool_call_exchange_and_multi_turn_history_intact() -> None:
    """Must not collapse a legitimate assistant tool-call message (always followed by a
    tool_result user row, never a retry-tail one) and must not bleed a retry in one turn
    into an unrelated, earlier or later, clean turn."""
    rows = [
        # Turn 1: clean, with a real tool-use exchange — nothing here should be dropped.
        _row(1, Message(role="user", content=[TextBlock(text="CVE-2021-44228 là gì?")])),
        _row(2, Message(role="assistant", content=[
            ToolUseBlock(id="t1", name="qdrant_search", input={"query": "CVE-2021-44228"}),
        ])),
        _row(3, Message(role="user", content=[ToolResultBlock(tool_use_id="t1", content="Log4Shell ...")])),
        _row(4, Message(role="assistant", content=[TextBlock(text="Log4Shell là CVE-2021-44228, CVSS 10.0.")])),
        # Turn 2: one rejected draft, then the grounded final.
        _row(5, Message(role="user", content=[TextBlock(text="Còn CVE-2020-1472 thì sao?")])),
        _row(6, Message(role="assistant", content=[TextBlock(text="draft chưa tra KB")])),
        _row(7, Message(role="user", content=[
            TextBlock(text=wrap_in_system_reminder("Route=entity/kb: call qdrant_search ...")),
        ])),
        _row(8, Message(role="assistant", content=[TextBlock(text="Zerologon là CVE-2020-1472, CVSS 10.0.")])),
    ]
    kept = _filter_display_rows(rows)
    assert [r.seq for r in kept] == [1, 2, 3, 4, 5, 8]


def test_filter_display_rows_noop_when_no_retries_present() -> None:
    rows = [
        _row(1, Message(role="user", content=[TextBlock(text="hello")])),
        _row(2, Message(role="assistant", content=[TextBlock(text="hi there")])),
    ]
    kept = _filter_display_rows(rows)
    assert kept == rows
