"""The gateway's single Linch Agent factory."""
from __future__ import annotations

from typing import Any

from linch import Agent
from linch.compaction import CompactionLadder, DefaultCompaction
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
    SanitizeToolResultHook,
    ToolCallBudgetHook,
)
from .profiles import compose_system_prompt

# Summary prompt for linch's LLM-compaction rung. linch's built-in default is coding-oriented
# ("files/tools task state") and, worse for this assistant, English — so on a long thread it
# drops the concrete facts a user stated early (a project code, a target IP, a chosen version)
# and re-summarizes in English against a Vietnamese system prompt. Verified live (2026-09-02):
# with the default prompt, a "remember FALCON-7788" fact stated in turn 1 was NOT recalled
# after compaction fired at turn 11. This VI prompt explicitly preserves user-stated facts
# verbatim so recall survives compaction, and keeps the summary in Vietnamese.
CHAT_SUMMARY_PROMPT_VI = (
    "Tóm tắt cuộc trò chuyện trước đó bằng tiếng Việt, cô đọng nhưng GIỮ NGUYÊN mọi dữ kiện "
    "cụ thể người dùng đã cung cấp — mã định danh, tên riêng, con số, phiên bản, mã dự án, "
    "địa chỉ/mục tiêu, yêu cầu và sở thích — vì các lượt sau có thể nhắc lại chúng. Ghi rõ:\n"
    "- Dữ kiện/thực thể/số liệu người dùng đã nêu (kèm GIÁ TRỊ CHÍNH XÁC, không lược bỏ).\n"
    "- Câu hỏi đã hỏi và kết luận đã đạt.\n"
    "- Việc đang làm dở và trạng thái hiện tại.\n"
    "Đúng sự thật, không bịa. Bỏ lời chào và câu xã giao."
)


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
    # LLM-summary rung: use the fact-preserving Vietnamese prompt when enabled (default), so a
    # long chat that crosses the compaction threshold keeps the user's earlier facts. When the
    # flag is off, linch falls back to its built-in coding-oriented default (compaction=None).
    compaction = (DefaultCompaction(prompt=CHAT_SUMMARY_PROMPT_VI)
                  if getattr(s, "chat_general_summary_prompt", True) else None)
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
        compaction=compaction,
        hooks=[
            ToolCallBudgetHook({"poc_fetch": _MAX_FETCH_PER_RUN, "web_fetch": 4}),
            RequireSearchBeforeAnswerHook(search_tool="poc_search", fetch_tool="poc_fetch",
                                          profile="exploit_research"),
            RequireKbSearchBeforeSynthesisHook(tools=("qdrant_search", "neo4j_search"),
                                               profile="exploit_research"),
            RequireSection1SynthesisHook(ledger=ledger, profile="exploit_research"),
            RequireRunInstructionsHook(ledger=ledger, profile="exploit_research"),
            RequireChatGroundingHook(profile="chat"),
            SanitizeToolResultHook(),
        ],
        compaction_ladder=CompactionLadder(),
        max_turns=s.step_cap,
        max_output_tokens=getattr(s, "llm_max_output_tokens", 8192),
        tool_timeout_ms=s.tool_timeout_ms,
        include_partial_messages=True,  # emit PartialAssistantEvent → token-by-token SSE
    )
