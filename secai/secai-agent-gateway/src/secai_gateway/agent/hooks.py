"""Deterministic Linch hooks used by the shared gateway agent.

The hooks intentionally keep only bounded, per-run in-memory state.  They are
coverage/completeness nudges rather than correctness gates: after a limited
number of regeneration requests, they allow the normal deterministic fallback
paths to produce the final answer.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from typing import Any

from linch.hooks.contexts import BeforeFinalAnswerContext, PostToolUseContext, PreToolUseContext
from linch.hooks.types import HookResult

from ..poc.prefetch import COVERAGE_CLOSES_SEARCH_HOOK, get_session_coverage

_MAX_TRACKED_RUNS = 20000


def _trim(store: OrderedDict[str, Any]) -> None:
    """Keep an OrderedDict bounded, evicting least-recently-used run state."""
    while len(store) > _MAX_TRACKED_RUNS:
        store.popitem(last=False)


def _profile_of(ctx: Any) -> str | None:
    session = getattr(ctx, "session", None)
    meta = getattr(session, "meta", None)
    return meta.get("profile") if isinstance(meta, dict) else None


class ToolCallBudgetHook:
    """Block a tool after its configured per-run invocation budget is exhausted."""

    name = "tool_call_budget"

    def __init__(self, limits: dict[str, int]) -> None:
        self.limits = limits
        self._counts: OrderedDict[str, dict[str, int]] = OrderedDict()

    async def on_pre_tool_use(self, ctx: PreToolUseContext) -> HookResult | None:
        limit = self.limits.get(ctx.tool_name)
        if limit is None:
            return None

        counts = self._counts.setdefault(ctx.run_id, {})
        self._counts.move_to_end(ctx.run_id)
        _trim(self._counts)
        used = counts.get(ctx.tool_name, 0)
        if used >= limit:
            return HookResult.block(
                f"{ctx.tool_name} limit reached ({limit} calls per run).",
                feedback=f"{ctx.tool_name} limit reached ({limit} calls per run).",
            )
        counts[ctx.tool_name] = used + 1
        return None


class RequireSearchBeforeAnswerHook:
    """Require an exploit-research run to attempt search and, when needed, fetch.

    A successful deterministic prefetch with ``enough`` or ``empty`` coverage
    closes this requirement for the corresponding session.
    """

    name = "require_search_before_answer"

    def __init__(
        self,
        search_tool: str,
        fetch_tool: str,
        profile: str,
        max_retries: int = 2,
    ) -> None:
        self.search_tool = search_tool
        self.fetch_tool = fetch_tool
        self.profile = profile
        self.max_retries = max_retries
        self._searched: OrderedDict[str, bool] = OrderedDict()
        self._has_hits: OrderedDict[str, bool] = OrderedDict()
        self._fetched: OrderedDict[str, bool] = OrderedDict()
        self._attempts: OrderedDict[str, int] = OrderedDict()

    @staticmethod
    def _session_id(ctx: Any) -> str | None:
        session = getattr(ctx, "session", None)
        return getattr(session, "id", None)

    @staticmethod
    def _mark(store: OrderedDict[str, bool], run_id: str) -> None:
        store[run_id] = True
        store.move_to_end(run_id)
        _trim(store)

    @staticmethod
    def _get(store: OrderedDict[str, Any], run_id: str, default: Any = None) -> Any:
        if run_id in store:
            store.move_to_end(run_id)
            return store[run_id]
        return default

    async def on_pre_tool_use(self, ctx: PreToolUseContext) -> None:
        if ctx.tool_name == self.search_tool:
            self._mark(self._searched, ctx.run_id)
        elif ctx.tool_name == self.fetch_tool:
            self._mark(self._fetched, ctx.run_id)
        return None

    async def on_post_tool_use(self, ctx: PostToolUseContext) -> None:
        if ctx.tool_name != self.search_tool or getattr(ctx.result, "is_error", False):
            return None
        try:
            payload = json.loads(ctx.result.content)
        except (TypeError, ValueError):
            return None
        if isinstance(payload, dict) and isinstance(payload.get("hits"), list) and payload["hits"]:
            self._mark(self._has_hits, ctx.run_id)
        return None

    async def on_before_final_answer(self, ctx: BeforeFinalAnswerContext) -> HookResult | None:
        if _profile_of(ctx) != self.profile:
            return None
        if get_session_coverage(self._session_id(ctx)) in COVERAGE_CLOSES_SEARCH_HOOK:
            return None

        run_id = ctx.run_id
        searched = self._get(self._searched, run_id, False)
        needs_fetch = self._get(self._has_hits, run_id, False) and not self._get(
            self._fetched, run_id, False
        )
        if searched and not needs_fetch:
            return None

        attempts = self._get(self._attempts, run_id, 0)
        if attempts >= self.max_retries:
            return None
        self._attempts[run_id] = attempts + 1
        self._attempts.move_to_end(run_id)
        _trim(self._attempts)

        if not searched:
            feedback = f"Before answering, call {self.search_tool} to search for PoC evidence."
        else:
            feedback = (
                f"{self.search_tool} returned hits; before answering, call "
                f"{self.fetch_tool} for the relevant PoC evidence."
            )
        return HookResult.force_continue(feedback, reason="search_before_answer_unsatisfied")


_RUN_INFO_SIGNAL = re.compile(r"argparse|optparse|ArgumentParser|click\.command|usage:|--help", re.I)
_RUN_SECTION_HEADING = re.compile(r"cách chạy", re.I)


class RequireRunInstructionsHook:
    """Require a run-instructions section when the run gathered run-info evidence."""

    name = "require_run_instructions"

    def __init__(self, ledger: Any, profile: str, max_retries: int = 2) -> None:
        self.ledger = ledger
        self.profile = profile
        self.max_retries = max_retries
        self._attempts: OrderedDict[str, int] = OrderedDict()

    def _has_run_info_evidence(self, run_id: str) -> bool:
        for evidence in self.ledger.evidence_for_run(run_id):
            source_files = evidence.get("source_files", [])
            if isinstance(source_files, list) and any(
                isinstance(file, dict) and file.get("role") == "readme" for file in source_files
            ):
                return True
            source_code = evidence.get("source_code", "")
            if isinstance(source_code, str) and _RUN_INFO_SIGNAL.search(source_code):
                return True
        return False

    async def on_before_final_answer(self, ctx: BeforeFinalAnswerContext) -> HookResult | None:
        if _profile_of(ctx) != self.profile or not self._has_run_info_evidence(ctx.run_id):
            return None
        if _RUN_SECTION_HEADING.search(ctx.final_text or ""):
            return None

        attempts = self._attempts.get(ctx.run_id, 0)
        if attempts >= self.max_retries:
            return None
        self._attempts[ctx.run_id] = attempts + 1
        self._attempts.move_to_end(ctx.run_id)
        _trim(self._attempts)
        return HookResult.force_continue(
            "Evidence includes run information. Add a 'Cách chạy PoC' section with the "
            "supported commands and prerequisites before answering.",
            reason="run_instructions_missing",
        )
