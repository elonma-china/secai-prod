from __future__ import annotations

import asyncio

from linch.hooks.contexts import PreToolUseContext

from secai_gateway.agent.hooks import ToolCallBudgetHook


def _ctx(tool_name: str, run_id: str) -> PreToolUseContext:
    return PreToolUseContext(session=None, run_id=run_id, turn_index=0, tool_name=tool_name)


def test_blocks_after_cap_reached() -> None:
    hook = ToolCallBudgetHook({"poc_fetch": 2})
    for _ in range(2):
        assert asyncio.run(hook.on_pre_tool_use(_ctx("poc_fetch", "run1"))) is None
    result = asyncio.run(hook.on_pre_tool_use(_ctx("poc_fetch", "run1")))
    assert result is not None and result.action == "block"
    assert "limit reached" in result.reason


def test_unlisted_tool_is_never_blocked() -> None:
    hook = ToolCallBudgetHook({"poc_fetch": 1})
    for _ in range(10):
        assert asyncio.run(hook.on_pre_tool_use(_ctx("qdrant_search", "run1"))) is None


def test_budget_is_independent_per_run() -> None:
    hook = ToolCallBudgetHook({"poc_fetch": 1})
    assert asyncio.run(hook.on_pre_tool_use(_ctx("poc_fetch", "run1"))) is None
    assert asyncio.run(hook.on_pre_tool_use(_ctx("poc_fetch", "run2"))) is None
    blocked = asyncio.run(hook.on_pre_tool_use(_ctx("poc_fetch", "run1")))
    assert blocked is not None and blocked.action == "block"
