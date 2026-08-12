from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from linch import InMemoryRunStore, InMemorySessionStore
from linch.events import ResultEvent
from linch.tools.registry import empty_tools
from linch.types import SystemBlock

from ..config import get_settings
from ..poc import LocalPocBackend, build_poc_tools
from ..retrieval import build_reference_tools, neo4j_search, qdrant_search, web_fetch, web_search
from ..state import RedisSessionStore, SqliteLedger
from ..bughunt import stream_bughunt_workflow
from ..bughunt.tools import build_bughunt_tools
from .factory import make_gateway_agent
from .profiles import PROFILES, compose_system_prompt
from .providers import build_llm_provider


_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Python's Monday=0 .. Sunday=6; Vietnamese calendars count Chủ Nhật (Sunday) as
# the week start and label the rest "Thứ 2".."Thứ 7" — a plain %A/weekday number
# doesn't map to that, and the model reliably miscalculates weekday-from-date on
# its own, so compute the label deterministically instead of letting it guess.
_VN_WEEKDAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ Nhật"]


def _dated_prompt(prompt: str) -> str:
    """Prefix the user turn with today's date (Vietnam local time, weekday included).

    Deliberately NOT part of the system prompt: the system block (+ the tool
    schemas the Qwen chat template appends right after it) is meant to stay a
    byte-identical, indefinitely prefix-cacheable prefix under vLLM
    ``--enable-prefix-caching`` (see factory.py's ``compaction_ladder`` note).
    The user turn is already unique per request/never cached across requests,
    so prefixing it here costs nothing extra — while landing the date in the
    system block would invalidate that whole cached prefix (system + tools)
    once a day. ``meta["last_user_prompt"]`` keeps the unwrapped prompt so
    grounding-classification hooks aren't affected.
    """
    now = datetime.now(_VN_TZ)
    weekday = _VN_WEEKDAYS[now.weekday()]
    today = now.strftime("%d/%m/%Y")
    return f"[Hôm nay là {weekday}, ngày {today} (giờ Việt Nam)]\n{prompt}"


class CompletionRunner(Protocol):
    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None) -> tuple[str, dict[str, Any]]: ...
    def stream(self, profile_name: str, prompt: str, session_id: str | None = None) -> AsyncIterator[Any]: ...


class LinchRunner:
    """Profile-bound Linch runner. The session override is the enforcement boundary."""

    def __init__(self, database_path: str | Path) -> None:
        root = Path(database_path).parent
        root.mkdir(parents=True, exist_ok=True)
        self.ledger = SqliteLedger(database_path)
        # PoC (searchsploit/Exploit-DB) runs in-process as a plain tool.
        self.poc = LocalPocBackend()
        self.tools: dict[str, Any] = {
            qdrant_search.name: qdrant_search,
            neo4j_search.name: neo4j_search,
            # Allowlisted HTTPS→markdown for current OWASP/CWE taxonomy pages (chat).
            **({web_fetch.name: web_fetch} if get_settings().web_fetch_enabled else {}),
            # Broader security-reference fetch (CVE/GHSA/advisory/PoC) for chat + exploit.
            # Ledger-bound: a successful fetch records evidence for the report pipeline.
            **(build_reference_tools(self.ledger) if get_settings().fetch_reference_enabled else {}),
            # SearXNG-backed web discovery for exploit PoC hints — gated OFF (needs SEARXNG_URL).
            **({web_search.name: web_search} if get_settings().web_search_enabled else {}),
            # settings gates the optional `poc_verify` tool (verify_enabled) — absent by default.
            **build_poc_tools(self.poc, self.ledger, get_settings()),
            **build_bughunt_tools(),
        }
        # Conversation memory is unified in Redis via linch's own session store (messages +
        # compaction snapshots). No Redis configured → in-memory (dev/test). The run-event
        # trace is not persisted (prod: traces not retained) — the run store is telemetry-only.
        redis_url = get_settings().redis_url
        self.session_store = RedisSessionStore(redis_url) if redis_url else InMemorySessionStore()
        # InMemory is enough for Phase 0 journal; durable SQLite can land with Phase 4 resume.
        self.run_store = InMemoryRunStore()
        provider, model = build_llm_provider()
        self.agent = make_gateway_agent(
            model=model, provider=provider,
            tools=empty_tools(*self.tools.values()), session_store=self.session_store,
            run_store=self.run_store, ledger=self.ledger,
        )

    def _apply_profile(self, session: Any, profile_name: str, prompt: str = "") -> None:
        # Override both runnable registry and system context per session. The exploit tools
        # are absent from chat, rather than merely hidden from model instructions.
        profile = PROFILES[profile_name]
        # Skip tool names not built (e.g. poc_verify when verify_enabled=false) so a profile
        # listing an optional tool never KeyErrors — the tool is simply absent that session.
        session.tools_override = empty_tools(
            *(self.tools[name] for name in profile.tool_names if name in self.tools))
        session.system_blocks_override = [SystemBlock(text=compose_system_prompt(profile, settings=get_settings()))]
        # RequireChatGroundingHook reads last_user_prompt from session.meta.
        meta = dict(getattr(session, "meta", None) or {})
        meta["profile"] = profile.name
        if prompt:
            meta["last_user_prompt"] = prompt
        session.meta = meta

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None) -> tuple[str, dict[str, Any]]:
        profile = PROFILES[profile_name]
        session = await self.agent.session(id=session_id, meta={"profile": profile.name})
        self._apply_profile(session, profile_name, prompt)
        final = ""
        run_id = None
        async for event in session.run(_dated_prompt(prompt)):
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
        self._apply_profile(session, profile_name, prompt)
        async for event in session.run(_dated_prompt(prompt)):
            yield event

    def stream_bughunt(
        self,
        *,
        target: str,
        scope_note: str = "",
        run_id: str | None = None,
        enable_exploit: bool = False,
        run_cve_sweep: bool = True,
    ) -> AsyncIterator[Any]:
        """Linch workflow: AgentHunt → Validate → MapCVE → Report → Exploit."""
        return stream_bughunt_workflow(
            self.agent,
            target=target,
            scope_note=scope_note,
            run_id=run_id,
            enable_exploit=enable_exploit,
            run_cve_sweep=run_cve_sweep,
        )
