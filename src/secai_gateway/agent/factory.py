"""The gateway's single Linch Agent factory."""
from __future__ import annotations

from typing import Any

from linch import Agent
from linch.compaction import CompactionLadder
from linch.config import FeatureFlags
from linch.loop_guard import LoopGuard

from ..config import get_settings
from ..poc.backend import _MAX_FETCH_PER_RUN
from .hooks import ToolCallBudgetHook
from .profiles import BASE_SYSTEM


def make_gateway_agent(
    *,
    provider: Any,
    model: str,
    tools: Any,
    session_store: Any,
    run_store: Any,
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
        system_prompt=BASE_SYSTEM,
        session_store=session_store,
        run_store=run_store,
        permissions={"mode": "skip-dangerous"},
        features=FeatureFlags(skills=False, subagents=False, mcp=False),
        loop_guard=LoopGuard(max_identical_tool_calls=3, force_final_answer=True),
        # Harness-level steering (not prompt-level): stop poc_fetch after N calls per run
        # regardless of what the model does — see agent/hooks.py. Same cap the PoC backend
        # itself enforces (_MAX_FETCH_PER_RUN), so a hook removal/misconfig still fails safe.
        hooks=[ToolCallBudgetHook({"poc_fetch": _MAX_FETCH_PER_RUN})],
        compaction_ladder=CompactionLadder(),
        max_turns=s.step_cap,
        tool_timeout_ms=s.tool_timeout_ms,
        include_partial_messages=True,  # emit PartialAssistantEvent → token-by-token SSE
    )
