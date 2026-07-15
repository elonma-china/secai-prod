from __future__ import annotations

from collections import OrderedDict

from linch.hooks.contexts import PreToolUseContext
from linch.hooks.types import HookResult

_MAX_TRACKED_RUNS = 20000  # evict oldest (run_id, tool_name) counters once exceeded


class ToolCallBudgetHook:
    """Blocks a tool once its per-run call count exceeds a configured cap.

    ``limits`` maps tool_name -> max calls per run_id. Each run gets its own fresh budget.
    Blocking here (PreToolUse) means the model sees a normal tool-error result and can
    still write its final answer from whatever evidence it already gathered — it is not a
    hard crash, just a hard stop on that one tool."""

    name = "tool_call_budget"

    def __init__(self, limits: dict[str, int]) -> None:
        self.limits = limits
        self._counts: "OrderedDict[tuple[str, str], int]" = OrderedDict()

    async def on_pre_tool_use(self, ctx: PreToolUseContext) -> HookResult | None:
        cap = self.limits.get(ctx.tool_name)
        if cap is None:
            return None
        key = (ctx.run_id or "", ctx.tool_name)
        n = self._counts.get(key, 0)
        if n >= cap:
            return HookResult.block(
                f"{ctx.tool_name} call limit reached ({cap} per run) — stop calling it and "
                f"answer from the evidence already gathered.")
        self._counts[key] = n + 1
        while len(self._counts) > _MAX_TRACKED_RUNS:
            self._counts.popitem(last=False)
        return None
