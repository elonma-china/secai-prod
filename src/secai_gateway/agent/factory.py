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
