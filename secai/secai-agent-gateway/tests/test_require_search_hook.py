from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from linch.hooks.contexts import BeforeFinalAnswerContext, PostToolUseContext, PreToolUseContext
from linch.hooks.types import ToolResult

from secai_gateway.agent.hooks import RequireSearchBeforeAnswerHook
from secai_gateway.poc.prefetch import clear_session_coverage, set_session_coverage


@dataclass
class _FakeSession:
    meta: dict
    id: str | None = None


def _pre_ctx(tool_name: str, run_id: str) -> PreToolUseContext:
    return PreToolUseContext(session=None, run_id=run_id, turn_index=0, tool_name=tool_name)


def _post_ctx(tool_name: str, run_id: str, hits: list | None, is_error: bool = False) -> PostToolUseContext:
    content = "boom" if is_error else json.dumps({"hits": hits or []})
    return PostToolUseContext(session=None, run_id=run_id, turn_index=0, tool_name=tool_name,
                              result=ToolResult(content=content, is_error=is_error))


def _final_ctx(run_id: str, profile: str, final_text: str = "",
               session_id: str | None = None) -> BeforeFinalAnswerContext:
    return BeforeFinalAnswerContext(
        session=_FakeSession(meta={"profile": profile}, id=session_id),
        run_id=run_id, turn_index=1, final_text=final_text)


def _hook(max_retries: int = 2) -> RequireSearchBeforeAnswerHook:
    return RequireSearchBeforeAnswerHook(search_tool="poc_search", fetch_tool="poc_fetch",
                                         profile="exploit_research", max_retries=max_retries)


# ── stage 1: must search at all ──────────────────────────────────────────────
def test_blocks_final_answer_when_no_search_attempted() -> None:
    clear_session_coverage()
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is not None and result.action == "force_continue"
    assert "poc_search" in result.feedback


def test_allows_when_search_attempted_and_returns_no_hits() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("poc_search", "run1")))
    asyncio.run(hook.on_post_tool_use(_post_ctx("poc_search", "run1", hits=[])))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is None  # legitimately nothing to fetch


def test_other_tool_calls_dont_satisfy_the_requirement() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("qdrant_search", "run1")))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is not None and result.action == "force_continue"


def test_does_not_fire_for_other_profiles() -> None:
    clear_session_coverage()
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "chat")))
    assert result is None


# ── stage 2: search found hits → must also fetch ─────────────────────────────
def test_blocks_when_search_found_hits_but_never_fetched() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("poc_search", "run1")))
    asyncio.run(hook.on_post_tool_use(_post_ctx("poc_search", "run1", hits=[{"ref": "poc:1"}])))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is not None and result.action == "force_continue"
    assert "poc_fetch" in result.feedback


def test_allows_when_search_found_hits_and_fetch_was_attempted() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("poc_search", "run1")))
    asyncio.run(hook.on_post_tool_use(_post_ctx("poc_search", "run1", hits=[{"ref": "poc:1"}])))
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("poc_fetch", "run1")))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is None


def test_ignores_errored_or_malformed_search_results() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("poc_search", "run1")))
    asyncio.run(hook.on_post_tool_use(_post_ctx("poc_search", "run1", hits=[{"ref": "poc:1"}], is_error=True)))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is None  # errored result never registers hits — searched-with-nothing path


# ── prefetch coverage closes the search floor ────────────────────────────────
def test_prefetch_enough_skips_force_continue_without_model_tools() -> None:
    clear_session_coverage()
    set_session_coverage("sess-enough", "enough")
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-enough")))
    assert result is None


def test_prefetch_empty_skips_force_continue_without_model_tools() -> None:
    clear_session_coverage()
    set_session_coverage("sess-empty", "empty")
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-empty")))
    assert result is None


def test_prefetch_partial_still_requires_model_search() -> None:
    clear_session_coverage()
    set_session_coverage("sess-partial", "partial")
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-partial")))
    assert result is not None and result.action == "force_continue"


def test_prefetch_failed_still_requires_model_search() -> None:
    clear_session_coverage()
    set_session_coverage("sess-failed", "failed")
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-failed")))
    assert result is not None and result.action == "force_continue"


def test_prefetch_coverage_is_per_session() -> None:
    clear_session_coverage()
    set_session_coverage("sess-a", "enough")
    hook = _hook()
    assert asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-a"))) is None
    blocked = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run2", "exploit_research", session_id="sess-b")))
    assert blocked is not None and blocked.action == "force_continue"


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
    assert third is None  # gave up — the deterministic no-evidence fallback takes over


def test_state_is_independent_per_run() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("poc_search", "run1")))
    asyncio.run(hook.on_post_tool_use(_post_ctx("poc_search", "run1", hits=[])))
    assert asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research"))) is None
    blocked = asyncio.run(hook.on_before_final_answer(_final_ctx("run2", "exploit_research")))
    assert blocked is not None and blocked.action == "force_continue"
