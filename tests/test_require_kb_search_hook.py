"""RequireKbSearchBeforeSynthesisHook — closes the gap where a model could finalize an
exploit_research answer (and, per poc/inject.py's T3, have its own Section-1 text trusted)
without ever calling qdrant_search/neo4j_search, when the PoC catalog prefetch alone already
satisfied RequireSearchBeforeAnswerHook."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from linch.hooks.contexts import BeforeFinalAnswerContext, PostToolUseContext, PreToolUseContext

from linch.hooks.types import ToolResult

from secai_gateway.agent.hooks import RequireKbSearchBeforeSynthesisHook
from secai_gateway.poc.prefetch import (
    clear_session_coverage, set_session_coverage, set_session_kb_searched,
    set_session_real_code_evidence,
)


@dataclass
class _FakeSession:
    meta: dict
    id: str | None = None


def _pre_ctx(tool_name: str, run_id: str) -> PreToolUseContext:
    return PreToolUseContext(session=None, run_id=run_id, turn_index=0, tool_name=tool_name)



def _post_ctx(tool_name: str, run_id: str, result: ToolResult | None) -> PostToolUseContext:
    return PostToolUseContext(
        session=None, run_id=run_id, turn_index=0, tool_name=tool_name, result=result)


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


def test_allows_when_qdrant_search_returns_context() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_post_tool_use(_post_ctx(
        "qdrant_search", "run1", ToolResult(content="[1] CVE-2025-0282: An unauthenticated attacker can trigger a stack buffer overflow in the request parser."))))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is None


def test_allows_when_neo4j_search_returns_context() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_post_tool_use(_post_ctx(
        "neo4j_search", "run1", ToolResult(content="CVE-2025-0282 | CWE-121\nDescription: An unauthenticated attacker can trigger a stack buffer overflow in the request parser."))))
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


# ── only real fetched PoC code can bypass KB grounding ──
def test_prefetch_real_code_skips_force_continue_without_model_tools() -> None:
    clear_session_coverage()
    set_session_coverage("sess-enough", "enough")
    set_session_real_code_evidence("sess-enough", True)
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-enough")))
    assert result is None


def test_prefetch_kb_searched_satisfies_requirement() -> None:
    clear_session_coverage()
    set_session_kb_searched("sess-kb-done", True)
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-kb-done")))
    assert result is None


def test_prefetch_empty_still_requires_kb_search_if_kb_not_searched() -> None:
    clear_session_coverage()
    set_session_coverage("sess-empty", "empty")
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-empty")))
    assert result is not None and result.action == "force_continue"


def test_prefetch_partial_still_requires_kb_search_if_kb_not_searched() -> None:
    clear_session_coverage()
    set_session_coverage("sess-partial", "partial")
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", session_id="sess-partial")))
    assert result is not None and result.action == "force_continue"


# ── shared: retries + fail-closed mutation + per-run isolation ────────────────────
def test_gives_up_after_max_retries_and_mutates_fail_closed() -> None:
    clear_session_coverage()
    hook = _hook(max_retries=2)
    ctx = _final_ctx("run1", "exploit_research")
    ctx.final_text = "Fabricated CVSS 9.9 and synthesized exploit"
    first = asyncio.run(hook.on_before_final_answer(ctx))
    second = asyncio.run(hook.on_before_final_answer(ctx))
    third = asyncio.run(hook.on_before_final_answer(ctx))
    assert first is not None and first.action == "force_continue"
    assert second is not None and second.action == "force_continue"
    assert third is not None and third.action == "mutate"
    assert third.reason == "kb_search_before_synthesis_exhausted"
    assert "Fabricated" not in third.final_text
    assert "cơ sở dữ liệu" in third.final_text


def test_state_is_independent_per_run() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_post_tool_use(_post_ctx(
        "qdrant_search", "run1", ToolResult(content="[1] CVE-2025-0282: An unauthenticated attacker can trigger a stack buffer overflow in the request parser."))))
    assert asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research"))) is None
    blocked = asyncio.run(hook.on_before_final_answer(_final_ctx("run2", "exploit_research")))
    assert blocked is not None and blocked.action == "force_continue"


def test_prefetch_metadata_only_does_not_bypass_kb_grounding() -> None:
    clear_session_coverage()
    set_session_coverage("metadata", "enough")
    hook = _hook()
    result = asyncio.run(hook.on_before_final_answer(
        _final_ctx("run1", "exploit_research", "metadata")))
    assert result is not None and result.action == "force_continue"


def test_pre_tool_attempt_does_not_satisfy_requirement() -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx("qdrant_search", "run1")))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert result is not None and result.action == "force_continue"


@pytest.mark.parametrize("tool_name,result", [
    ("qdrant_search", None),
    ("qdrant_search", ToolResult(content="Embedding failed", is_error=True)),
    ("qdrant_search", ToolResult(content=" ")),
    ("qdrant_search", ToolResult(content="Không tìm thấy kết quả trong knowledge base.")),
    ("neo4j_search", ToolResult(content="No results returned from the graph database.")),
    ("neo4j_search", ToolResult(content="No results found.")),
    ("neo4j_search", ToolResult(
        content="No CVE/CWE/CAPEC/Technique/Tactic ID or product name found in the query. Run qdrant_search first to resolve an ID, then call neo4j_search with it.")),
])
def test_unusable_kb_tool_result_does_not_allow_synthesis(tool_name, result) -> None:
    clear_session_coverage()
    hook = _hook()
    asyncio.run(hook.on_pre_tool_use(_pre_ctx(tool_name, "run1")))
    asyncio.run(hook.on_post_tool_use(_post_ctx(tool_name, "run1", result)))
    verdict = asyncio.run(hook.on_before_final_answer(_final_ctx("run1", "exploit_research")))
    assert verdict is not None and verdict.action == "force_continue"


@pytest.mark.parametrize('content', [
    '### Row 1\n',
    '### Row 1\n- **cve**: CVE-2025-0282\n- **cwe**: CWE-121',
    'CVE ID: CVE-2025-0282 | Severity: HIGH | CVSS: 8.8 | CWE Weaknesses: CWE-79',
])
def test_successful_metadata_only_tool_result_does_not_authorize_synthesis(content):
    clear_session_coverage()
    hook = _hook(max_retries=0)
    asyncio.run(hook.on_post_tool_use(_post_ctx('neo4j_search', 'run-metadata', ToolResult(content=content))))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx('run-metadata', 'exploit_research')))
    assert result is not None and result.action == 'mutate'


@pytest.mark.parametrize("formatter", ["qdrant-empty", "qdrant-title", "neo4j-products"])
def test_real_tool_format_metadata_cannot_authorize_synthesis(formatter):
    from secai_gateway.retrieval.qdrant import _format_hit
    from secai_gateway.retrieval.cypher import format_results
    title = "Apache Log4j Enterprise Server remote code execution"
    if formatter == "qdrant-empty":
        content = "\n\n---\n\n".join(_format_hit(i, 1.0, {"kind": "core", "cve_id": "CVE-2025-0282"}, None) for i in range(3))
    elif formatter == "qdrant-title":
        content = _format_hit(0, 1.0, {"kind": "core", "title": title, "text": title}, None)
    else:
        content = format_results(["cve", "products"], [["CVE-2025-0282", ["Apache Log4j Enterprise Server", "Apache Tomcat Application Server"]]])
    clear_session_coverage()
    hook = _hook(max_retries=0)
    asyncio.run(hook.on_post_tool_use(_post_ctx("qdrant_search", "metadata", ToolResult(content=content))))
    result = asyncio.run(hook.on_before_final_answer(_final_ctx("metadata", "exploit_research")))
    assert result is not None and result.action == "mutate"
