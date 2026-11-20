"""KB-grounding rubric: the before-final-answer hook fails closed on a fabricated draft.

The HTTP-level checks of this rubric went away with /ai/exploit (removed 2026-11-20, see
branch backup/pre-api-prune-20261120); the hook itself is still part of the agent library.
"""
from __future__ import annotations

import asyncio

from secai_gateway.agent.hooks import RequireKbSearchBeforeSynthesisHook
from secai_gateway.poc.prefetch import session_coverage_scope

DRAFT_MARKER = "FABRICATED_MODEL_DRAFT_MARKER"


class _Session:
    def __init__(self, id_value: str):
        self.id = id_value
        self.meta = {"profile": "exploit_research"}


def test_rubric_hook_exhaustion_mutates_fabricated_draft_fail_closed():
    from linch.hooks.contexts import BeforeFinalAnswerContext

    async def exercise():
        with session_coverage_scope():
            hook = RequireKbSearchBeforeSynthesisHook(
                tools=("qdrant_search", "neo4j_search"),
                profile="exploit_research", max_retries=2,
            )
            context = BeforeFinalAnswerContext(
                session=_Session("rubric-hook"), run_id="rubric-hook-run", turn_index=1,
                final_text=DRAFT_MARKER,
            )
            first = await hook.on_before_final_answer(context)
            second = await hook.on_before_final_answer(context)
            terminal = await hook.on_before_final_answer(context)
            repeated = await hook.on_before_final_answer(context)
            return first, second, terminal, repeated

    first, second, terminal, repeated = asyncio.run(exercise())
    assert first.action == second.action == "force_continue"
    assert terminal.action == repeated.action == "mutate"
    assert terminal.reason == "kb_search_before_synthesis_exhausted"
    assert DRAFT_MARKER not in terminal.final_text
    assert "Không thể xác thực thông tin lỗ hổng từ cơ sở dữ liệu nội bộ (KB)" in terminal.final_text
