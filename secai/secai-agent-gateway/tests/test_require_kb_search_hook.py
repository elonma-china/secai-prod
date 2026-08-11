"""RequireKbSearchBeforeSynthesisHook — closes the gap where a model could finalize an
exploit_research answer (and, per poc/inject.py's T3, have its own Section-1 text trusted)
without ever calling qdrant_search/neo4j_search, when the PoC catalog prefetch alone already
satisfied RequireSearchBeforeAnswerHook."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from linch.hooks.contexts import BeforeFinalAnswerContext, PreToolUseContext

from secai_gateway.agent.hooks import RequireKbSearchBeforeSynthesisHook
from secai_gateway.poc.prefetch import clear_session_coverage, set_session_coverage


@dataclass
class _FakeSession:
    meta: dict
    id: str | None = None


def _pre_ctx(tool_name: str, run_id: str) -> PreToolUseContext:
    return PreToolUseContext(session=None, run_id=run_id, turn_index=0, tool_name=tool_name)


def _final_ctx(run_id: str, profile: str, session_id: str | None = None) -> BeforeFinalAnswerContext:
    return BeforeFinalAnswerContext(
        session=_FakeSession(meta={"profile": profile}, id=session_id),
        run_id=run_id, turn_index=1, final_text="")


def _hook(max_retries: int = 2) -> RequireKbSearchBeforeSynthesisHook:
    return RequireKbSearchBeforeSynthesisHook(
        tools=("qdrant_search", "neo4j_search"), profile="exploit_research", max_retries=max_retries)


def test_blocks_when_no_kb_tool_called_and_no_prefetch_coverage() -> None:
    clear_session_coverage()
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is not None and result.action == "force_continue"
    assert "qdrant_search" in result.feedback


def test_allows_when_qdrant_search_was_called() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("qdrant_search", "run1")))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is None


def test_allows_when_neo4j_search_was_called() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("neo4j_search", "run1")))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is None


def test_other_tool_calls_dont_satisfy_the_requirement() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("poc_search", "run1")))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is not None and result.action == "force_continue"


def test_does_not_fire_for_other_profiles() -> None:
    clear_session_coverage()
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "chat")))
    assert result is None


# ── prefetch "enough" (real fetched PoC code) is the only coverage that closes this ──
def test_prefetch_enough_skips_force_continue_without_model_tools() -> None:
    clear_session_coverage()
    set_session_coverage("sess-enough", "enough")
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-enough")))
    assert result is None


def test_prefetch_empty_still_requires_kb_search() -> None:
    # this is exactly the gap that let a model skip ALL tools and fabricate: the PoC
    # catalog was empty (RequireSearchBeforeAnswerHook is satisfied by prefetch alone),
    # but nothing else forced a KB lookup before finalizing.
    clear_session_coverage()
    set_session_coverage("sess-empty", "empty")
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-empty")))
    assert result is not None and result.action == "force_continue"


def test_prefetch_partial_still_requires_kb_search() -> None:
    clear_session_coverage()
    set_session_coverage("sess-partial", "partial")
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-partial")))
    assert result is not None and result.action == "force_continue"


# ── shared: retries + per-run isolation ──────────────────────────────────────
def test_gives_up_after_max_retries_and_allows_through() -> None:
    clear_session_coverage()
    hook = _hook(max_retries=2)
    ctx = _final_ctx("run1", "exploit_research")
    first = asyncio.run(hook.on_before_final_answer(ctx))
    second = asyncio.run(hook.on_before_final_answer(ctx))
    third = asyncio.run(hook.on_before_final_answer(ctx))
    assert first is not None and first.action == "force_continue"
    assert second is not None and second.action == "force_continue"
    assert third is None  # gave up — deterministic Section-1 fallback (T4) takes over


def test_state_is_independent_per_run() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("qdrant_search", "run1")))
    assert asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research"))) is None
    blocked = asyncio.run(hook.on_before_final_answer(_final_ctx("run2", "exploit_research")))
    assert blocked is not None and blocked.action == "force_continue"
