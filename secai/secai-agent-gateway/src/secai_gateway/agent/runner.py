from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from linch import InMemoryRunStore, InMemorySessionStore
from linch.events import ErrorEvent, LoopGuardEvent, ResultEvent
from linch.sessions.sqlite import SqliteSessionStore
from linch.tools.registry import empty_tools
from linch.types import SystemBlock

from ..config import get_settings
from ..poc import LocalPocBackend, build_poc_tools
from ..retrieval import build_reference_tools, neo4j_search, qdrant_search, web_fetch, web_search
from ..state import ProjectStore, RedisSessionStore, SqliteLedger
from ..bughunt import stream_bughunt_workflow
from ..bughunt.tools import build_bughunt_tools
from .factory import make_gateway_agent
from .hooks import chat_final_text
from .profiles import PROFILES, compose_system_prompt
from .providers import build_llm_provider


_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Python's Monday=0 .. Sunday=6; Vietnamese calendars count Chủ Nhật (Sunday) as
# the week start and label the rest "Thứ 2".."Thứ 7" — a plain %A/weekday number
# doesn't map to that, and the model reliably miscalculates weekday-from-date on
# its own, so compute the label deterministically instead of letting it guess.
_VN_WEEKDAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ Nhật"]


# Profiles whose output is a self-contained document (Markdown/JSON) that the caller
# renders verbatim. If the agent loop exhausts its turn budget on tool calls WITHOUT
# emitting that document, linch ends the run with an empty error-ResultEvent — so the
# endpoint would return a blank body. For these profiles we fall back to one tool-less
# synthesis turn (below) which forces the model to write the document from the context
# it already gathered. Deliberately NOT chat/exploit_research: those carry grounding
# hooks that require a tool call before answering, which a tool-less turn can't satisfy.
_SALVAGE_PROFILES = frozenset({"report", "statistical_report"})

# The tool-less follow-up turn: the accumulated tool results already live in the session
# store, so the model synthesizes the final document from them without any new lookups.
_SALVAGE_INSTRUCTION = (
    "Bạn đã dùng hết số lượt gọi công cụ cho phép. TUYỆT ĐỐI KHÔNG gọi thêm bất kỳ công "
    "cụ nào nữa. Chỉ dựa trên thông tin đã thu thập ở các bước trên, hãy tạo NGAY câu trả "
    "lời cuối cùng theo ĐÚNG định dạng/hợp đồng đã nêu trong hướng dẫn hệ thống."
)


def _has_text(event: Any) -> bool:
    return isinstance(event, ResultEvent) and bool((event.final_text or "").strip())


def _is_turn_limit(event: Any) -> bool:
    return isinstance(event, LoopGuardEvent) and getattr(event, "reason", "") == "max_turns"


def _date_line() -> str:
    """Today's date (Vietnam local time, weekday included) as a standalone line.

    2026-09-21: used to be prefixed directly onto the user-turn STRING passed to
    ``session.run()`` — but linch persists exactly that string as the user turn (see
    ``linch/loop/runner.py``), so it ended up baked verbatim into the session store
    and re-displayed whenever a client reloaded chat history (``GET
    /sessions/{sid}/messages``) — one leg of the internal-prompt-leak bug. Delivered
    now via ``session.meta["ephemeral_context"]`` + ``EphemeralTurnContextHook``
    (agent/hooks.py) instead: a per-turn, non-persisted extra message appended to the
    outgoing provider request only. The user turn passed to ``session.run()`` is the
    caller's ORIGINAL, unwrapped prompt again.
    """
    now = datetime.now(_VN_TZ)
    weekday = _VN_WEEKDAYS[now.weekday()]
    today = now.strftime("%d/%m/%Y")
    return f"[Hôm nay là {weekday}, ngày {today} (giờ Việt Nam)]"


def _ephemeral_context_text(profile_name: str, grounding_context: str | None) -> str | None:
    """Build the per-turn ephemeral context text (date + optional KB grounding block)
    for ``session.meta["ephemeral_context"]`` — see ``_date_line`` and
    ``EphemeralTurnContextHook`` (agent/hooks.py) for why this is a separate channel
    from the persisted user turn.

    Skips the date line for ``translate``: it's a pure text-transformation task with
    no need for date context, and unlike other profiles the model is told to
    reproduce/transform the ENTIRE user turn — so a leaked or (worse) paraphrased copy
    of an injected preamble could land in the output verbatim. Reproduced 2026-09-12:
    the model rendered ``[Hôm nay là Thứ 7, ngày 12/09/2026 (giờ Việt Nam)]`` as
    ``[Tuần thứ 7, ngày 12/09/2026 (giờ Việt Nam)]`` inside a translated CVE title — a
    paraphrase, not a literal echo, so no fixed regex could reliably strip every
    variant after the fact. Not exposing the date line to that profile at all is what
    closes every variant (this is now moot for the persisted turn either way, but the
    model would still fully "see" content delivered via the context channel, so the
    same reasoning still applies there).
    """
    parts: list[str] = []
    if profile_name != "translate":
        parts.append(_date_line())
    if grounding_context:
        parts.append(grounding_context)
    if not parts:
        return None
    return "\n\n".join(parts)


class CompletionRunner(Protocol):
    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None,
                        *, grounding_context: str | None = None) -> tuple[str, dict[str, Any]]: ...
    def stream(self, profile_name: str, prompt: str, session_id: str | None = None,
               *, grounding_context: str | None = None) -> AsyncIterator[Any]: ...


class LinchRunner:
    """Profile-bound Linch runner. The session override is the enforcement boundary."""

    def __init__(self, database_path: str | Path) -> None:
        root = Path(database_path).parent
        root.mkdir(parents=True, exist_ok=True)
        self.ledger = SqliteLedger(database_path)
        self.project_store = ProjectStore(database_path)
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
        # Conversation memory backend is config-selectable (SESSION_STORE_BACKEND):
        #   sqlite (default) — linch's own SqliteSessionStore, durable on local disk, no
        #     expiry. Fixes the old "24h TTL loses the thread on restart" behavior.
        #   redis — legacy back-compat; falls back to in-memory if REDIS_URL is unset.
        #   memory (or anything else) — in-memory only, dev/test.
        # The run-event trace is not persisted (prod: traces not retained) — the run store
        # is telemetry-only regardless of the session backend chosen above.
        settings = get_settings()
        backend = settings.session_store_backend
        if backend == "sqlite":
            session_db_path = Path(settings.session_db_path)
            session_db_path.parent.mkdir(parents=True, exist_ok=True)
            self.session_store = SqliteSessionStore(session_db_path)
        elif backend == "redis":
            redis_url = settings.redis_url
            self.session_store = RedisSessionStore(redis_url) if redis_url else InMemorySessionStore()
        else:
            self.session_store = InMemorySessionStore()
        # InMemory is enough for Phase 0 journal; durable SQLite can land with Phase 4 resume.
        self.run_store = InMemoryRunStore()
        # linch's Session is single-flight (Session.run() raises ConfigError if a run is
        # already active) and agent.session(id=...) returns the SAME live Session object
        # for a given id — so two requests for the same user_id (e.g. a caller that fires a
        # second chat message before the first stream finished) race onto one Session and
        # the loser crashes instead of getting an answer. Serialize per session_id here so
        # the second request queues and runs after the first, rather than failing.
        self._session_locks: dict[str, asyncio.Lock] = {}
        provider, model = build_llm_provider()
        self.agent = make_gateway_agent(
            model=model, provider=provider,
            tools=empty_tools(*self.tools.values()), session_store=self.session_store,
            run_store=self.run_store, ledger=self.ledger,
        )

    def _apply_profile(self, session: Any, profile_name: str, prompt: str = "",
                        *, grounding_context: str | None = None) -> None:
        # Override both runnable registry and system context per session. The exploit tools
        # are absent from chat, rather than merely hidden from model instructions.
        profile = PROFILES[profile_name]
        # Skip tool names not built (e.g. poc_verify when verify_enabled=false) so a profile
        # listing an optional tool never KeyErrors — the tool is simply absent that session.
        session.tools_override = empty_tools(
            *(self.tools[name] for name in profile.tool_names if name in self.tools))
        session.system_blocks_override = [SystemBlock(text=compose_system_prompt(profile, settings=get_settings()))]
        # RequireChatGroundingHook reads last_user_prompt from session.meta. `prompt` here
        # is always the caller's ORIGINAL, unwrapped text (routes.py no longer bakes KB
        # grounding/date into it) — session.meta is in-memory-only (set via plain attribute
        # assignment, never session.update_meta()), so nothing here reaches the durable
        # SessionRecord/`GET /sessions/{sid}` endpoints.
        meta = dict(getattr(session, "meta", None) or {})
        meta["profile"] = profile.name
        if prompt:
            meta["last_user_prompt"] = prompt
        # Explicit flag replacing the old "sniff CHAT_PRE_FETCH_MARKER inside the persisted
        # prompt" trick (see agent/hooks.py::CHAT_PRE_FETCH_MARKER comment) — tells
        # RequireChatGroundingHook that the pre-fetch in routes.py::_ground_chat_prompt
        # already satisfied the KB-grounding requirement for this turn.
        meta["chat_prefetch_grounded"] = bool(grounding_context)
        # Per-turn context delivered via EphemeralTurnContextHook (agent/hooks.py) — the
        # date line (skipped for translate) plus, for chat, the pre-fetched KB grounding
        # block. Never part of the string passed to session.run(), so never persisted.
        meta["ephemeral_context"] = _ephemeral_context_text(profile_name, grounding_context)
        session.meta = meta

    @asynccontextmanager
    async def _session_lock(self, session_id: str | None):
        """Serialize concurrent runs against the same linch Session. No-op when
        ``session_id`` is falsy (anonymous callers get a fresh id/Session per request
        anyway, so there's nothing to contend on — see ``_chat_session_id``).

        The dict entry is removed once the holder releases it (guarded by identity, so a
        waiter that already grabbed the lock object isn't affected by another task's
        cleanup) rather than kept forever — a stable per-user id is reused indefinitely,
        so an uncapped cache would grow for as long as the process runs."""
        if not session_id:
            yield
            return
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        try:
            async with lock:
                yield
        finally:
            if self._session_locks.get(session_id) is lock:
                del self._session_locks[session_id]

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None,
                        *, grounding_context: str | None = None) -> tuple[str, dict[str, Any]]:
        async with self._session_lock(session_id):
            profile = PROFILES[profile_name]
            session = await self.agent.session(id=session_id, meta={"profile": profile.name})
            self._apply_profile(session, profile_name, prompt, grounding_context=grounding_context)
            final = ""
            run_id = None
            # `prompt` is the caller's ORIGINAL, unwrapped text — date + any KB grounding
            # reach the model via session.meta["ephemeral_context"] (set just above), never
            # via this string, so linch persists exactly this clean prompt as the user turn.
            async for event in session.run(prompt):
                run_id = session.active_run_id or run_id
                if isinstance(event, ResultEvent):
                    final = event.final_text or ""
            if not final.strip() and profile_name in _SALVAGE_PROFILES:
                # Loop exhausted its turns without a document → force one tool-less
                # synthesis turn so the endpoint returns content, not a blank body.
                session.tools_override = empty_tools()
                async for event in session.run(_SALVAGE_INSTRUCTION):
                    run_id = session.active_run_id or run_id
                    if isinstance(event, ResultEvent):
                        final = event.final_text or ""
        if profile_name == "chat":
            # Value-level chat floor — the RELIABLE path. The container's linch never
            # dispatches on_before_final_answer, so RequireChatGroundingHook's guard
            # (_chat_final_guarantee) is INERT. Post-process the final TEXT here instead:
            # replace empty/closer-only, meta-deflection and false "highest-CVSS" finals with
            # canonical/clarify/fallback text. ONLY the text VALUE changes — the (final,
            # metadata) return tuple shape, the metadata dict keys and every schema are
            # untouched. `prompt` here is the raw user prompt (same string classify_chat_
            # grounding / last_user_prompt use), NOT the date-wrapped model turn.
            final = chat_final_text(prompt, final)
        metadata = {"run_id": run_id, "profile": profile.name, "tool_registry": sorted(profile.tool_names)}
        if profile_name == "exploit_research":
            metadata.update(self.ledger.exploit_metadata(run_id))
        return final, metadata

    async def stream(self, profile_name: str, prompt: str, session_id: str | None = None,
                      *, grounding_context: str | None = None):
        """Yield raw linch run events (SystemEvent, ToolCallStart/End, PartialAssistant,
        Result, …) so the caller can render tool calls separately from assistant text
        over SSE. Applies the same per-profile tool/system override as ``complete()`` —
        the enforcement boundary is identical whether streaming or not."""
        async with self._session_lock(session_id):
            profile = PROFILES[profile_name]
            session = await self.agent.session(id=session_id, meta={"profile": profile.name})
            self._apply_profile(session, profile_name, prompt, grounding_context=grounding_context)
            salvageable = profile_name in _SALVAGE_PROFILES
            got_text = False
            turn_limited = False
            # See complete()'s comment: `prompt` is the caller's original text; date/KB
            # grounding are delivered via session.meta["ephemeral_context"], not this string.
            async for event in session.run(prompt):
                got_text = got_text or _has_text(event)
                if salvageable and _is_turn_limit(event):
                    # Swallow the max_turns terminal (guard → error → empty result);
                    # a salvage pass below produces the real final event instead.
                    turn_limited = True
                    continue
                if turn_limited and isinstance(event, (ErrorEvent, ResultEvent)):
                    continue  # swallow the TurnLimitError + empty error-ResultEvent tail
                yield event
            if turn_limited and not got_text:
                session.tools_override = empty_tools()
                async for event in session.run(_SALVAGE_INSTRUCTION):
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
