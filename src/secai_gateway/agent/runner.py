from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from linch import InMemoryRunStore, InMemorySessionStore
from linch.events import ErrorEvent, LoopGuardEvent, ResultEvent, ToolCallEndEvent
from linch.errors import ProviderError
from linch.session import RunOptions
from linch.sessions.sqlite import SqliteSessionStore
from linch.tools.registry import empty_tools
from linch.types import SystemBlock

from ..config import get_settings
from ..poc import LocalPocBackend, build_poc_tools
from ..retrieval import build_reference_tools, howto_search, neo4j_search, qdrant_search, web_fetch, web_search
from ..state import ConversationStore, ProjectStore, RedisSessionStore, SqliteLedger
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


def _ephemeral_context_text(profile_name: str, grounding_context: str | None, scope_context: str | None = None) -> str | None:
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

    2026-09-22 (bug: date-banner mistaken for a new user turn — regression from the
    2026-09-21 ephemeral-context refactor above): ``EphemeralTurnContextHook`` delivers
    this text as a SEPARATE ``Message(role="user", ...)`` appended AFTER the real,
    already-persisted question (see that hook's docstring + ``linch``'s
    ``_build_turn_request``: ``messages = list(session.provider_view); messages.extend(
    context.messages)`` — ``linch``'s ``Message.role`` is restricted to
    ``Literal["user", "assistant"]``, there is no "system note" role available on this
    channel, and the OpenAI-chat-completions serializer (``providers/openai_chat.py::
    _build_chat_payload``) emits each ``Message`` as its own separate ``{"role": "user",
    ...}`` dict — it does NOT merge consecutive same-role messages). The result: the
    model is handed TWO consecutive "user" turns with no assistant reply in between,
    and the LAST one — often just the bare date line, with no grounding — reads exactly
    like a fresh, disconnected human message. Reproduced live (2026-09-22, isolated
    instance): asking "OWASP Top 10 2021 gồm những gì?" made the model reply "Cảm ơn
    bạn đã cung cấp thông tin ngày tháng..." — answering the trailing date banner as if
    it were the user's actual current turn, instead of the real question one message
    earlier. The bug concentrates on OWASP/CWE Top-N-with-year questions because
    ``prompts/base_system.md``'s ``tool_routing`` block used to explicitly tell the model
    to inspect "ngày hiện tại (đầu câu hỏi người dùng)" for exactly that question shape
    (stale wording from BEFORE this refactor, when the date line really was baked onto
    the head of the SAME user-turn string — fixed separately in that file), priming the
    model to treat a trailing bare-date message as a signal worth reacting to for that
    query shape specifically — but any profile/query can hit the same structural
    ambiguity since the date line is unconditional whenever grounding is absent.
    Moving this back into ``system_blocks`` is NOT the fix: ``_build_turn_request``
    folds ``context.system_blocks`` into the SAME single system-role text right before
    the tool schemas the Qwen chat template appends, so per-turn-volatile content there
    would invalidate the whole system+tools prefix on every turn instead of just once a
    day (see ``EphemeralTurnContextHook``'s docstring) — a real, measured vLLM
    prefix-cache/throughput regression, not merely a style concern. Fix: wrap the
    ephemeral text so it cannot be read as a new question — explicit open/close markers
    stating it is internal, non-conversational context, plus a closing instruction to
    keep answering the real question in the message right above it. Textual framing
    only; no architecture change, so the leak fix (turn kept out of the persisted store)
    and the cache-prefix property (still delivered via ``messages``, not
    ``system_blocks``) are both preserved as-is.
    """
    parts: list[str] = []
    if profile_name not in {"translate", "translate_v2", "ocr_correction", "translation_orchestrator"}:
        parts.append(_date_line())
    if grounding_context:
        parts.append(grounding_context)
    if scope_context:
        parts.append(scope_context)
    if not parts:
        return None
    body = "\n\n".join(parts)
    return (
        "[GHI CHÚ NGỮ CẢNH HỆ THỐNG — KHÔNG phải tin nhắn/câu hỏi mới của người dùng; "
        "đây chỉ là thông tin tham khảo nội bộ (ngày hệ thống hiện tại"
        + (" + dữ liệu tra cứu KB" if grounding_context else "")
        + ") để bạn dùng khi soạn câu trả lời. TUYỆT ĐỐI KHÔNG diễn giải, chào hỏi lại, "
        "cảm ơn, hay phản hồi trực tiếp về bản thân ghi chú này.]\n"
        f"{body}\n"
        "[HẾT GHI CHÚ NGỮ CẢNH — nhiệm vụ của bạn vẫn là trả lời ĐÚNG câu hỏi của người "
        "dùng ở tin nhắn NGAY PHÍA TRÊN ghi chú này; tiếp tục trả lời câu hỏi đó ngay bây "
        "giờ, không được coi ghi chú trên là một câu hỏi/yêu cầu mới cần xử lý.]"
    )


class CompletionRunner(Protocol):
    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None,
                        *, grounding_context: str | None = None, scope_context: str | None = None) -> tuple[str, dict[str, Any]]: ...
    def stream(self, profile_name: str, prompt: str, session_id: str | None = None,
               *, grounding_context: str | None = None, scope_context: str | None = None) -> AsyncIterator[Any]: ...


class LinchRunner:
    """Profile-bound Linch runner. The session override is the enforcement boundary."""

    def __init__(self, database_path: str | Path, *, strict_transform_errors: bool = False) -> None:
        self.strict_transform_errors = strict_transform_errors
        root = Path(database_path).parent
        root.mkdir(parents=True, exist_ok=True)
        self.ledger = SqliteLedger(database_path)
        self.project_store = ProjectStore(database_path)
        # Durable multi-conversation index + transcript (shares the same sqlite file as the
        # ledger). System of record for the /ai/conversations/* endpoints; the linch session
        # store below stays the LIVE working memory (24h + compaction).
        self.conversations = ConversationStore(database_path)
        # PoC (searchsploit/Exploit-DB) runs in-process as a plain tool.
        self.poc = LocalPocBackend()
        self.tools: dict[str, Any] = {
            qdrant_search.name: qdrant_search,
            neo4j_search.name: neo4j_search,
            howto_search.name: howto_search,
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
        # This opt-in profile alone can delegate to the specialist. Legacy chat
        # registries are unchanged even when the specialist is configured.
        from ..translation.settings import TranslationSettings
        translation_settings = TranslationSettings()
        if (translation_settings.enabled and translation_settings.specialist_base_url
                and translation_settings.specialist_api_key):
            from ..translation.tool import build_translation_tool
            self.tools["translation_specialist"] = build_translation_tool(translation_settings)
        settings = get_settings()
        backend = settings.session_store_backend
        if backend == "sqlite":
            session_db_path = Path(settings.session_db_path)
            session_db_path.parent.mkdir(parents=True, exist_ok=True)
            self.session_store = SqliteSessionStore(session_db_path)
        elif backend == "redis":
            redis_url = settings.redis_url
            self.session_store = (
                RedisSessionStore(redis_url, ttl_seconds=settings.session_ttl_seconds)
                if redis_url else InMemorySessionStore()
            )
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
        self._translation_agent: Any | None = None

    def _agent_for(self, profile_name: str) -> Any:
        if profile_name not in ("translate_v2", "translate"):  # translation: deterministic decoding
            return self.agent
        if self._translation_agent is None:
            provider, model = build_llm_provider(temperature_override=0.0)
            self._translation_agent = make_gateway_agent(
                model=model, provider=provider,
                tools=empty_tools(*self.tools.values()), session_store=self.session_store,
                run_store=self.run_store, ledger=self.ledger,
            )
        return self._translation_agent

    def _apply_profile(self, session: Any, profile_name: str, prompt: str = "",
                        *, grounding_context: str | None = None, scope_context: str | None = None) -> None:
        if profile_name == "translation_orchestrator" and "translation_specialist" not in self.tools:
            raise RuntimeError("Translation specialist is unavailable")
        # Override both runnable registry and system context per session. The exploit tools
        # are absent from chat, rather than merely hidden from model instructions.
        profile = PROFILES[profile_name]
        # Skip tool names not built (e.g. poc_verify when verify_enabled=false) so a profile
        # listing an optional tool never KeyErrors — the tool is simply absent that session.
        session.tools_override = empty_tools(
            *(self.tools[name] for name in profile.tool_names if name in self.tools))
        session.system_blocks_override = [SystemBlock(text=compose_system_prompt(profile, settings=get_settings()))]
        if scope_context:
            session.system_blocks_override.append(SystemBlock(text=
                "Trong lượt này chỉ trả lời các câu hỏi ATTT được chọn, không làm nhiệm vụ ngoài phạm vi."
                + scope_context))
        # RequireChatGroundingHook reads last_user_prompt from session.meta. `prompt` here
        # is always the caller's ORIGINAL, unwrapped text (routes.py no longer bakes KB
        # grounding/date into it) — session.meta is in-memory-only (set via plain attribute
        # assignment, never session.update_meta()), so nothing here reaches the durable
        # SessionRecord/`GET /sessions/{sid}` endpoints.
        meta = dict(getattr(session, "meta", None) or {})
        meta["profile"] = profile.name
        meta["scope_user_prompt"] = None
        if scope_context:
            requests = json.loads(scope_context)
            if not isinstance(requests, list) or not requests or not all(isinstance(x, str) and x.strip() for x in requests):
                raise ValueError("Invalid selected security requests")
            meta["scope_user_prompt"] = "\n".join(requests)
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
        meta["ephemeral_context"] = _ephemeral_context_text(profile_name, grounding_context, scope_context)
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

    def _run_opts(self, profile_name: str) -> RunOptions:
        """Per-turn GENERATION cap (RunOptions.max_output_tokens), decoupled from the agent-level
        compaction reserve. Generous per profile so nothing is truncated — exploit_research gets
        the largest budget because a synthesized PoC can be long. See config.py for the values."""
        s = get_settings()
        cap = (s.llm_exploit_max_output_tokens if profile_name == "exploit_research"
               else s.llm_max_output_tokens)
        return RunOptions(max_output_tokens=cap)

    async def complete(self, profile_name: str, prompt: str, session_id: str | None = None,
                        *, grounding_context: str | None = None, scope_context: str | None = None) -> tuple[str, dict[str, Any]]:
        if (profile_name == "translation_orchestrator" and
                not isinstance(self.session_store, InMemorySessionStore)):
            raise RuntimeError("Translation orchestration requires in-memory sessions")
        async with self._session_lock(session_id):
            profile = PROFILES[profile_name]
            session = await self._agent_for(profile_name).session(id=session_id, meta={"profile": profile.name})
            self._apply_profile(session, profile_name, prompt, grounding_context=grounding_context, scope_context=scope_context)
            opts = self._run_opts(profile_name)
            final = ""
            delegated = None
            run_id = None
            # `prompt` is the caller's ORIGINAL, unwrapped text — date + any KB grounding
            # reach the model via session.meta["ephemeral_context"] (set just above), never
            # via this string, so linch persists exactly this clean prompt as the user turn.
            async for event in session.run(prompt, opts):
                run_id = session.active_run_id or run_id
                if (profile_name == "translation_orchestrator" and
                        isinstance(event, ToolCallEndEvent) and
                        event.tool_name == "translation_specialist"):
                    if event.is_error:
                        raise RuntimeError("Translation specialist failed")
                    delegated = (event.tool_result.content if event.tool_result is not None
                                 else event.result)
                if (self.strict_transform_errors and profile_name in {"translate_v2", "ocr_correction"}
                        and isinstance(event, ErrorEvent)):
                    error = event.error or {}
                    status = error.get("status")
                    raise ProviderError("Transformation service unavailable",
                                        status=status if isinstance(status, int) else None,
                                        retryable=error.get("retryable") is True)
                if isinstance(event, ResultEvent):
                    final = event.final_text or ""
            if profile_name == "translation_orchestrator":
                if not delegated:
                    raise RuntimeError("Translation specialist was not called")
                final = delegated
            if not final.strip() and profile_name in _SALVAGE_PROFILES:
                # Loop exhausted its turns without a document → force one tool-less
                # synthesis turn so the endpoint returns content, not a blank body.
                session.tools_override = empty_tools()
                async for event in session.run(_SALVAGE_INSTRUCTION, opts):
                    run_id = session.active_run_id or run_id
                    if isinstance(event, ResultEvent):
                        final = event.final_text or ""
        if profile_name == "chat":
            # Value-level chat floor — a second, independent layer on top of
            # RequireChatGroundingHook's on_before_final_answer guard (_chat_final_guarantee),
            # NOT a workaround for it being inert. CORRECTION (2026-09-26): this comment used
            # to claim the container's linch never dispatches on_before_final_answer — that was
            # a misdiagnosis (see agent/hooks.py::chat_final_text's docstring for the full
            # correction); the hook DOES run and its force_continue DOES reach the loop (proven
            # by the max_retries/"phantom regenerate" investigation the same day). Post-process
            # the final TEXT here anyway, so the same empty/closer-only, meta-deflection and
            # false "highest-CVSS" floor holds even if the hook chain/dispatch ever changes.
            # ONLY the text VALUE changes — the (final, metadata) return tuple shape, the
            # metadata dict keys and every schema are untouched. `prompt` here is the raw user
            # prompt (same string classify_chat_grounding / last_user_prompt use), NOT the
            # date-wrapped model turn.
            final = chat_final_text(prompt, final)
        metadata = {"run_id": run_id, "profile": profile.name, "tool_registry": sorted(profile.tool_names)}
        if profile_name == "exploit_research":
            metadata.update(self.ledger.exploit_metadata(run_id))
        return final, metadata

    async def stream(self, profile_name: str, prompt: str, session_id: str | None = None,
                      *, grounding_context: str | None = None, scope_context: str | None = None):
        """Yield raw linch run events (SystemEvent, ToolCallStart/End, PartialAssistant,
        Result, …) so the caller can render tool calls separately from assistant text
        over SSE. Applies the same per-profile tool/system override as ``complete()`` —
        the enforcement boundary is identical whether streaming or not."""
        async with self._session_lock(session_id):
            profile = PROFILES[profile_name]
            session = await self._agent_for(profile_name).session(id=session_id, meta={"profile": profile.name})
            self._apply_profile(session, profile_name, prompt, grounding_context=grounding_context, scope_context=scope_context)
            opts = self._run_opts(profile_name)
            salvageable = profile_name in _SALVAGE_PROFILES
            got_text = False
            turn_limited = False
            # See complete()'s comment: `prompt` is the caller's original text; date/KB
            # grounding are delivered via session.meta["ephemeral_context"], not this string.
            async for event in session.run(prompt, opts):
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
                async for event in session.run(_SALVAGE_INSTRUCTION, opts):
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
