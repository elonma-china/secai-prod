from __future__ import annotations

import asyncio
from dataclasses import dataclass

from linch.hooks.contexts import BeforeFinalAnswerContext, PostToolUseContext
from linch.hooks.types import ToolResult

from secai_gateway.agent.hooks import RequireChatGroundingHook, _chat_needs_kb


@dataclass
class _FakeSession:
    meta: dict


def _post_ctx(tool_name: str, run_id: str, is_error: bool = False) -> PostToolUseContext:
    return PostToolUseContext(session=None, run_id=run_id, turn_index=0, tool_name=tool_name,
                              result=ToolResult(content="ok", is_error=is_error))


def _final_ctx(run_id: str, profile: str, prompt: str) -> BeforeFinalAnswerContext:
    meta = {"profile": profile, "last_user_prompt": prompt}
    return BeforeFinalAnswerContext(session=_FakeSession(meta=meta), run_id=run_id,
                                    turn_index=1, final_text="")


def _hook(max_retries: int = 3) -> RequireChatGroundingHook:
    return RequireChatGroundingHook(profile="chat", max_retries=max_retries)


# ── classification ────────────────────────────────────────────────────────────
def test_classifies_cve_id_as_needing_kb() -> None:
    assert _chat_needs_kb("Phân tích CVE-2024-3400 giúp tôi") is True


def test_classifies_id_lookup_phrasing_as_needing_kb() -> None:
    assert _chat_needs_kb("Lỗi này thuộc CWE nào?") is True


def test_classifies_concept_question_as_not_needing_kb() -> None:
    assert _chat_needs_kb("SQL injection là gì và cách phòng chống cơ bản?") is False


def test_classifies_empty_prompt_as_not_needing_kb() -> None:
    assert _chat_needs_kb("") is False


# ── hook behavior ────────────────────────────────────────────────────────────
def test_blocks_cve_question_without_kb_lookup() -> None:
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "chat", "Phân tích CVE-2024-3400 giúp tôi")))
    assert result is not None and result.action == "force_continue"
    assert "qdrant_search" in result.feedback


def test_allows_after_qdrant_search() -> None:
    hook = _hook()
    asyncio.run(hook.on_post_tool_use(_post_ctx("qdrant_search", "run1")))
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "chat", "Phân tích CVE-2024-3400 giúp tôi")))
    assert result is None


def test_allows_after_neo4j_search() -> None:
    hook = _hook()
    asyncio.run(hook.on_post_tool_use(_post_ctx("neo4j_search", "run1")))
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "chat", "CWE-79 liên quan CVE nào?")))
    assert result is None


def test_failed_search_does_not_satisfy() -> None:
    hook = _hook()
    asyncio.run(hook.on_post_tool_use(_post_ctx("qdrant_search", "run1", is_error=True)))
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "chat", "Phân tích CVE-2024-3400 giúp tôi")))
    assert result is not None and result.action == "force_continue"


def test_concept_question_never_forces_a_tool_call() -> None:
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "chat", "SQL injection là gì và cách phòng chống cơ bản?")))
    assert result is None


def test_other_profile_is_ignored() -> None:
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", "CVE-2024-3400")))
    assert result is None  # scoped to profile="chat" only


def test_gives_up_after_max_retries() -> None:
    hook = _hook(max_retries=2)
    prompt = "Phân tích CVE-2024-3400 giúp tôi"
    r1 = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "chat", prompt)))
    r2 = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "chat", prompt)))
    r3 = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "chat", prompt)))
    assert r1 is not None and r2 is not None
    assert r3 is None  # exhausted retries — deterministic <not_found_policy> is the backstop


def test_web_related_wording_is_absent_from_feedback() -> None:
    """Air-gap guard: this hook must never nag the model about a tool (web_fetch) it doesn't
    have wired into the chat profile at all."""
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "chat", "Phân tích CVE-2024-3400 giúp tôi")))
    assert result is not None
    assert "web_fetch" not in result.feedback and "internet" not in result.feedback.lower()
