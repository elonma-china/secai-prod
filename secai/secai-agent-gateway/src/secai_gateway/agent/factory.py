"""The gateway's single Linch Agent factory."""
from __future__ import annotations

from typing import Any

from linch import Agent
from linch.compaction import CompactionLadder
from linch.config import FeatureFlags
from linch.loop_guard import LoopGuard

from ..config import get_settings
from ..poc.backend import _MAX_FETCH_PER_RUN
from .hooks import (
    RequireChatGroundingHook,
    RequireKbSearchBeforeSynthesisHook,
    RequireRunInstructionsHook,
    RequireSearchBeforeAnswerHook,
    RequireSection1SynthesisHook,
    ToolCallBudgetHook,
)
from .profiles import compose_system_prompt


def make_gateway_agent(
    *,
    provider: Any,
    model: str,
    tools: Any,
    session_store: Any,
    run_store: Any,
    ledger: Any,
) -> Agent:
    """Build the shared agent. The runner supplies a per-session ``tools``/system
    override (the profile-enforcement boundary); this keeps the security-agent identity,
    loop guard and turn cap, and streams token-by-token via ``include_partial_messages``.

    ``compaction_ladder`` turns on linch's proactive micro-compaction (elide old tool
    results, keep the last N turns) so long chats stay under the window with a stable,
    prompt-cacheable prefix — memory lives in the session store, not re-stuffed per turn."""
    s = get_settings()
    return Agent(
        model=model,
        provider=provider,
        tools=tools,
        system_prompt=compose_system_prompt(),
        session_store=session_store,
        run_store=run_store,
        permissions={"mode": "skip-dangerous"},
        features=FeatureFlags(skills=False, subagents=False, mcp=False),
        loop_guard=LoopGuard(max_identical_tool_calls=3, force_final_answer=True),
        # Harness-level steering (not prompt-level): stop poc_fetch after N calls per run
        # regardless of what the model does — see agent/hooks.py. Same cap the PoC backend
        # itself enforces (_MAX_FETCH_PER_RUN), so a hook removal/misconfig still fails safe.
        # RequireSearchBeforeAnswerHook is the floor to that ceiling: exploit_research must
        # attempt poc_search, and (if it found real hits) poc_fetch too, before answering
        # (bounded retries, then falls through to the deterministic no-evidence report —
        # never blocks correctness, only widens evidence coverage). Complements
        # poc/prefetch.py's deterministic search, which covers most requests without
        # touching the model's turn at all.
        # RequireKbSearchBeforeSynthesisHook closes a gap the poc_search-only hook above
        # doesn't cover: when the PoC catalog is empty, prefetch's own search already
        # satisfies RequireSearchBeforeAnswerHook, so nothing forced a qdrant_search/
        # neo4j_search call — a model could finalize (and, since poc/inject.py now trusts
        # its own Section 1 text for the synthesize-from-CVE tier, fabricate) without ever
        # consulting the KB. See hooks.py's docstring for the live-observed failure.
        # RequireSection1SynthesisHook: when T2/T3 evidence exists (catalog description or
        # a confirmed CVE) but the model skipped Section 1 anyway, force one retry asking it
        # to write it — turns a reticent first pass into a synthesized answer instead of a
        # correct-but-unhelpful "không tổng hợp được" (see hooks.py's docstring).
        # RequireRunInstructionsHook is a completeness nudge, not a safety gate: when real
        # run-info evidence exists (README-role file / argparse signals) but the model's own
        # text never wrote "Cách chạy PoC", force it to add one — deterministic on both
        # sides (see hooks.py's docstring for why this isn't an LLM-judged check).
        # RequireChatGroundingHook: chat must web_fetch taxonomy pages / qdrant entity IDs
        # instead of answering OWASP 2021 / CWE lists from stale parametric memory.
        hooks=[
            ToolCallBudgetHook({"poc_fetch": _MAX_FETCH_PER_RUN, "web_fetch": 4}),
            RequireSearchBeforeAnswerHook(search_tool="poc_search", fetch_tool="poc_fetch",
                                          profile="exploit_research"),
            RequireKbSearchBeforeSynthesisHook(tools=("qdrant_search", "neo4j_search"),
                                               profile="exploit_research"),
            RequireSection1SynthesisHook(ledger=ledger, profile="exploit_research"),
            RequireRunInstructionsHook(ledger=ledger, profile="exploit_research"),
            RequireChatGroundingHook(profile="chat"),
        ],
        compaction_ladder=CompactionLadder(),
        max_turns=s.step_cap,
        tool_timeout_ms=s.tool_timeout_ms,
        include_partial_messages=True,  # emit PartialAssistantEvent → token-by-token SSE
    )
