from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

from linch import InMemoryRunStore, InMemorySessionStore
from linch.events import ResultEvent
from linch.tools.registry import empty_tools
from linch.types import SystemBlock

from ..config import get_settings
from ..poc import LocalPocBackend, build_poc_tools
from ..retrieval import neo4j_search, qdrant_search
from ..state import ConversationStore, RedisSessionStore, SqliteLedger
from .factory import make_gateway_agent
from .profiles import PROFILES, compose_system_prompt
from .providers import build_llm_provider


class CompletionRunner(Protocol):
    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None) -> tuple[str, dict[str, Any]]: ...
    def stream(self, profile_name: str, prompt: str, session_id: str | None = None) -> AsyncIterator[Any]: ...


class LinchRunner:
    """Profile-bound Linch runner. The session override is the enforcement boundary."""

    def __init__(self, database_path: str | Path) -> None:
        root = Path(database_path).parent
        root.mkdir(parents=True, exist_ok=True)
        self.ledger = SqliteLedger(database_path)
        # Durable multi-conversation index + transcript (shares the same sqlite file as the
        # ledger). System of record for the /ai/conversations/* endpoints; the linch session
        # store below stays the LIVE working memory (24h + compaction).
        self.conversations = ConversationStore(database_path)
        # PoC (searchsploit/Exploit-DB) runs in-process as a plain tool.
        self.poc = LocalPocBackend()
        self.tools: dict[str, Any] = {
            qdrant_search.name: qdrant_search,
            neo4j_search.name: neo4j_search,
            **build_poc_tools(self.poc, self.ledger),
        }
        # Conversation memory is unified in Redis via linch's own session store (messages +
        # compaction snapshots). No Redis configured → in-memory (dev/test). The run-event
        # trace is not persisted (prod: traces not retained) — the run store is telemetry-only.
        s = get_settings()
        redis_url = s.redis_url
        self.session_store = (
            RedisSessionStore(redis_url, ttl_seconds=s.session_ttl_seconds)
            if redis_url else InMemorySessionStore()
        )
        self.run_store = InMemoryRunStore()
        provider, model = build_llm_provider()
        self.agent = make_gateway_agent(
            model=model, provider=provider,
            tools=empty_tools(*self.tools.values()), session_store=self.session_store,
            run_store=self.run_store, ledger=self.ledger,
        )

    def _apply_profile(self, session: Any, profile_name: str) -> None:
        # Override both runnable registry and system context per session. The exploit tools
        # are absent from chat, rather than merely hidden from model instructions.
        profile = PROFILES[profile_name]
        session.tools_override = empty_tools(*(self.tools[name] for name in profile.tool_names))
        session.system_blocks_override = [SystemBlock(text=compose_system_prompt(profile))]

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None) -> tuple[str, dict[str, Any]]:
        profile = PROFILES[profile_name]
        session = await self.agent.session(id=session_id, meta={"profile": profile.name})
        self._apply_profile(session, profile_name)
        final = ""
        run_id = None
        async for event in session.run(prompt):
            run_id = session.active_run_id or run_id
            if isinstance(event, ResultEvent):
                final = event.final_text or ""
        metadata = {"run_id": run_id, "profile": profile.name, "tool_registry": sorted(profile.tool_names)}
        if profile_name == "exploit_research":
            metadata.update(self.ledger.exploit_metadata(run_id))
        return final, metadata

    async def stream(self, profile_name: str, prompt: str, session_id: str | None = None):
        """Yield raw linch run events (SystemEvent, ToolCallStart/End, PartialAssistant,
        Result, …) so the caller can render tool calls separately from assistant text
        over SSE. Applies the same per-profile tool/system override as ``complete()`` —
        the enforcement boundary is identical whether streaming or not."""
        profile = PROFILES[profile_name]
        session = await self.agent.session(id=session_id, meta={"profile": profile.name})
        self._apply_profile(session, profile_name)
        async for event in session.run(prompt):
            yield event
