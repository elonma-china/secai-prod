from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from linch.events import ResultEvent, SystemEvent

from ..agent import registry_for
from ..poc import (
    enforce_reference_code,
    force_no_poc_notice,
    merge_evidence,
    prefetch_evidence,
    strip_system_narration,
)
from ..sanitize import classify_input, decide, judge_injection
from ..sanitize.classifier import note_verdict, verdict_stats
from ..security import (
    UNSUPPORTED_LANGUAGE_MSG,
    is_unsupported_language,
    require_public_input,
    sanitize_output_language,
)
from ..state.conversations import derive_title
from .literal_mask import unmask_literals
from .messages import build_translate_prompt, parse_statistical, report_prompt, statistical_prompt, translate_langs
from .text_chunk import split_for_translation
from .schemas import (
    ConversationChatRequest,
    CreateConversationRequest,
    LegacyRequest,
    RenameConversationRequest,
)
from .sse import event_to_sse, sse_frame

logger = logging.getLogger(__name__)

# Shown to the user when a run fails. Delivered on the *visible* channels (token +
# final), not only as an `event: error` frame — some UIs render assistant text but
# ignore the error event, and a silent empty bubble is worse than a plain apology.
STREAM_ERROR_MESSAGE = (
    "Xin lỗi, trợ lý đang gặp sự cố tạm thời nên chưa thể trả lời. Vui lòng thử lại sau giây lát."
)


def _health_status(qwen_ok: bool, sanitize_stats: dict[str, float]) -> str:
    """Pure decision for /health's ``status`` field. Degraded when the LLM is unreachable OR
    the input classifier has been failing open on more than half its calls over a real sample
    (``total>=20`` — a small/cold-start sample shouldn't flip status on one or two unlucky
    calls). Separate function so this can be unit-tested without a live LLM/httpx round trip."""
    sanitize_degraded = sanitize_stats.get("total", 0) >= 20 and sanitize_stats.get("unknown_rate", 0.0) > 0.5
    return "ok" if (qwen_ok and not sanitize_degraded) else "degraded"


def _chat_session_id(body: LegacyRequest) -> str:
    # Stable per-user session id → linch loads/compacts the prior conversation from its
    # (Redis) session store. Only the new message is sent; history is NOT re-stuffed.
    user_id = str(body.user_id or "").strip()
    if not user_id or user_id == "anonymous":
        # No stable identity → a fresh id per request, so anonymous callers never
        # share (and read back) one another's conversation history.
        return f"chat:anon:{uuid4().hex}"
    return f"chat:{hashlib.sha256(user_id.encode()).hexdigest()}"


# ── Durable multi-conversation chat history (/ai/conversations/*) ──────────────────────────
# Same-owner-hashing scheme as `_chat_session_id` above, but the hash is folded directly into
# the linch session id (`chat:conv:{owner_hash}:{conversation_id}`) rather than being the whole
# id — so a request can only ever address a session under ITS OWN owner_hash prefix. Guessing
# another user's conversation_id resolves to a different (empty) session; there is nothing to
# leak. `state.runner.conversations` (ConversationStore, SQLite) is the durable index +
# transcript layered on top — independent of the 24h Redis live-memory window.

def _conv_owner_hash(user_id: Any) -> str | None:
    uid = str(user_id or "").strip()
    if not uid or uid == "anonymous":
        return None
    return hashlib.sha256(uid.encode()).hexdigest()


def _conv_session_id(owner_hash: str | None, conversation_id: str) -> str:
    if owner_hash:
        return f"chat:conv:{owner_hash}:{conversation_id}"
    return f"chat:conv:anon:{uuid4().hex}"


_RENAME_TITLE_MAX = 120

# Fire-and-forget background tasks (the auto-title LLM call) must keep a live reference or
# asyncio may garbage-collect them mid-flight — a well-known footgun for create_task() calls
# whose result nothing awaits. This module-level set is that reference; entries are dropped via
# add_done_callback once each task finishes (success or failure), so it never grows unbounded.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _fire_and_forget(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


def register_routes(app: FastAPI, state: Any) -> None:
    """Attach every gateway endpoint to ``app`` (closures capture the shared state)."""

    async def complete(profile: str, body: LegacyRequest, *, chat: bool = False, prompt_override: str | None = None) -> tuple[str, dict[str, Any]]:
        session_id = None
        if prompt_override is not None:
            # Endpoint already built the task-specific user message (translate/report/…).
            prompt = prompt_override
        else:
            prompt = require_public_input(body.supplied_text())
            if chat:
                session_id = _chat_session_id(body)
        try:
            text, metadata = await state.runner.complete(profile, prompt, session_id)
        except HTTPException:
            raise
        except Exception as exc:  # provider/runtime failure → clean 503, not an opaque 500
            # Mirrors the streaming path (see stream()), which already surfaces failures
            # as an SSE error event instead of tearing down the response.
            raise HTTPException(status_code=503, detail=f"AI service unavailable: {exc}") from exc
        return sanitize_output_language(text), metadata

    def base_meta(profile: str, run_id: str | None) -> dict[str, Any]:
        return {"run_id": run_id, "profile": profile, "tool_registry": sorted(registry_for(profile))}

    def stream(profile: str, prompt: str, session_id: str | None, build_final, *,
              prelude: list[bytes] | None = None, observe=None) -> StreamingResponse:
        """Shared SSE runner for every agentic/text endpoint: streams tool_call /
        tool_result / token events as they happen, then one final event built by the
        endpoint's ``build_final(final_text, run_id)`` (which may also persist state).

        ``prelude``/``observe`` are additive, optional hooks for the conversation-scoped
        endpoints (see /ai/conversations/chat/stream): every EXISTING call site passes
        neither, so their frame sequence is byte-identical to before. ``prelude`` yields
        fixed frames before the run starts (e.g. the leading `conversation` id frame);
        ``observe(event)`` is called per raw linch event and may return an extra SSE frame
        to yield (e.g. an additive `compaction` frame) without altering anything else."""

        async def gen() -> AsyncIterator[bytes]:
            for frame in (prelude or []):
                yield frame
            final = ""
            run_id = None
            failed = False
            try:
                async for event in state.runner.stream(profile, prompt, session_id):
                    if isinstance(event, SystemEvent):
                        run_id = getattr(event, "run_id", None) or run_id
                    if isinstance(event, ResultEvent):
                        final = sanitize_output_language(event.final_text or "")
                    frame = event_to_sse(event)
                    if frame:
                        yield frame
                    if observe is not None:
                        extra = observe(event)
                        if extra:
                            yield extra
            except Exception:  # surface runtime failures as a visible message, not a broken/blank stream
                failed = True
                # Log the real cause server-side (the client only gets a friendly message).
                logger.exception("stream failed for profile=%s", profile)
                # error frame for UIs that handle it, PLUS token+final so token-only
                # UIs still show the failure instead of an empty assistant bubble.
                yield sse_frame("error", {"error": STREAM_ERROR_MESSAGE})
                yield sse_frame("token", {"delta": STREAM_ERROR_MESSAGE})
                yield sse_frame("final", await build_final(STREAM_ERROR_MESSAGE, run_id))
            if not failed:
                yield sse_frame("final", await build_final(final, run_id))
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    def _audit(run_id: str, tool_name: str, key_material: str, meta: dict[str, Any]) -> None:
        """Audit line for the input-sanitize gate — parallel to ledger.audit_once's use
        elsewhere, so any block/PII hit leaves a who/when/what trail. Fail-open — audit
        failures must never break the request."""
        ledger = getattr(state.runner, "ledger", None)
        if ledger is None:
            return
        try:
            ledger.audit_once(f"{run_id}:audit", run_id, tool_name,
                              hashlib.sha256(key_material.encode()).hexdigest(), meta)
        except Exception:
            logger.exception("audit log failed run_id=%s tool=%s", run_id, tool_name)

    async def _chat_gate(prompt: str) -> tuple[str, dict[str, Any]] | None:
        """Model-based input sanitization for the chat path (runs BEFORE the agent).

        Embeds the prompt with the deployed sec-embedding model and applies the numpy head
        (secai_gateway.sanitize) to classify intent + risk. Returns a short-circuit
        ``(refusal_text, metadata)`` when the classifier blocks (injection / off-topic), or
        ``None`` to let the agent answer. FAIL-OPEN: any classifier error/unavailability, or
        the feature being disabled, returns ``None`` (pass through). Scoped to /ai/chat only —
        translate/report/exploit have their own (different) input shapes and are not gated
        here by design."""
        # Language gate (runs first, no embedding needed): the classifier head was trained on
        # vi/en only, so a message predominantly in a non-Latin script (CJK/kana/Hangul/
        # Cyrillic/…) is refused with a bilingual ask to use Vietnamese or English. Latin-script
        # languages pass through to the classifier. Independent of input_classifier_enabled.
        if getattr(state.settings, "language_gate_enabled", True) and is_unsupported_language(
                prompt, ratio=getattr(state.settings, "language_gate_nonlatin_ratio", 0.5)):
            run_id = uuid4().hex
            logger.info("input-sanitize run_id=%s action=refuse_language", run_id)
            _audit(run_id, "input_sanitize", prompt,
                   {"label": "unsupported_language", "action": "refuse_language", "pii": False})
            metadata = {
                "run_id": run_id, "profile": "chat",
                "tool_registry": sorted(registry_for("chat")),
                "input_classification": {"label": "unsupported_language",
                                         "action": "refuse_language", "blocked": True, "pii": False},
                "short_circuit": True,
            }
            return UNSUPPORTED_LANGUAGE_MSG, metadata
        if not getattr(state.settings, "input_classifier_enabled", True):
            return None
        run_id = uuid4().hex
        try:
            verdict = await classify_input(prompt, settings=state.settings,
                                           client=getattr(state, "http_client", None))
            # Ư2 uncertain-band cascade: an ambiguous (calibrated) injection score gets a
            # second opinion from the LLM judge instead of a threshold coin-flip. Only fires
            # when a calibration asset is shipped (injection_uncertain) AND the flag is on;
            # ANY judge failure (timeout/unreachable/malformed) returns None and this falls
            # straight back to the classifier's own threshold decision — never a new failure
            # mode. See sanitize/cascade.py.
            if (verdict.ok and verdict.injection_uncertain
                    and getattr(state.settings, "injection_cascade_enabled", False)):
                # Prefer the calibrated score (the one band-membership was actually decided on)
                # if shipped; falls back to the raw score for display when it isn't.
                cascade_score = verdict.scores.get("injection_calibrated",
                                                   verdict.scores.get("injection", 0.0))
                judge_verdict = await judge_injection(
                    state.runner, prompt, cascade_score,
                    timeout_s=getattr(state.settings, "injection_cascade_timeout_s", 8.0))
                if judge_verdict is not None:
                    logger.info(
                        "input-sanitize-cascade run_id=%s score=%.3f threshold_verdict=%s "
                        "judge_verdict=%s", run_id, cascade_score, verdict.injection, judge_verdict)
                    verdict = replace(verdict, injection=judge_verdict)
            decision = decide(verdict, state.settings)
        except Exception:  # a guard must never break chat
            logger.exception("input classifier failed → fail-open")
            return None

        classification = {
            "label": decision.label,
            "action": decision.action.value,
            "blocked": decision.short_circuit,
            "pii": decision.pii,
            "scores": {k: round(v, 4) for k, v in verdict.scores.items()},
            "latency_ms": round(verdict.latency_ms, 1),
        }
        # Fail-open observability: a verdict that is not ``ok`` means the guard was bypassed
        # (embed down / embed-model mismatch / timeout). That is safe but SILENT, so surface it
        # at WARNING with the running unknown-rate rather than only INFO (see CLAUDE.md incident).
        note_verdict(verdict.ok)
        if not verdict.ok:
            st = verdict_stats()
            logger.warning("input-sanitize FAIL-OPEN run_id=%s reason=%s unknown_rate=%.3f (%d/%d)",
                           run_id, verdict.reason, st["unknown_rate"], st["unknown"], st["total"])
        logger.info("input-sanitize run_id=%s verdict=%s action=%s latency_ms=%.1f",
                    run_id, verdict.summary(), decision.action.value, verdict.latency_ms)
        # Persist an audit line (digest, not plaintext) for any block or PII hit.
        if decision.short_circuit or decision.pii:
            _audit(run_id, "input_sanitize", prompt,
                   {"label": decision.label, "action": decision.action.value, "pii": decision.pii})

        if not decision.short_circuit:
            return None
        metadata = {
            "run_id": run_id, "profile": "chat",
            "tool_registry": sorted(registry_for("chat")),
            "input_classification": classification, "short_circuit": True,
        }
        return decision.message, metadata

    def _short_circuit_stream(message: str, metadata: dict[str, Any], *,
                              prelude: list[bytes] | None = None,
                              top_level: dict[str, Any] | None = None) -> StreamingResponse:
        """SSE response for a classifier short-circuit: one token + one final, then DONE
        (same frame shape as chat_stream's build_final, so token-only UIs still render it).
        ``prelude``/``top_level`` are additive, optional (see ``stream()`` above) — every
        existing call site passes neither, so its output is byte-identical to before."""
        async def gen() -> AsyncIterator[bytes]:
            for frame in (prelude or []):
                yield frame
            yield sse_frame("token", {"delta": message})
            final_payload = {"content": message, "answer": message,
                             "message": message, "metadata": metadata}
            if top_level:
                final_payload.update(top_level)
            yield sse_frame("final", final_payload)
            yield b"data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/chat_no_stream")
    @app.post("/ai/chat")
    async def chat(body: LegacyRequest) -> dict[str, Any]:
        prompt = require_public_input(body.supplied_text())
        gated = await _chat_gate(prompt)
        if gated:
            message, metadata = gated
            return {"response": message, "answer": message, "message": message, "metadata": metadata}
        session_id = _chat_session_id(body)
        try:
            text, metadata = await state.runner.complete("chat", prompt, session_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"AI service unavailable: {exc}") from exc
        text = sanitize_output_language(text)
        return {"response": text, "answer": text, "message": text, "metadata": metadata}

    @app.post("/ai/chat/stream")
    async def chat_stream(body: LegacyRequest) -> StreamingResponse:
        text = require_public_input(body.supplied_text())
        gated = await _chat_gate(text)
        if gated:
            message, metadata = gated
            return _short_circuit_stream(message, metadata)
        session_id = _chat_session_id(body)

        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            # linch persists the turn to its session store during the run — nothing to do here.
            return {"content": final, "answer": final, "message": final, "metadata": base_meta("chat", run_id)}

        return stream("chat", text, session_id, build_final)

    # ── Durable multi-conversation chat history (/ai/conversations/*) ──────────────────────
    # Additive endpoints layered on the SAME `chat` profile pipeline as /ai/chat above (same
    # `_chat_gate`, same `state.runner.complete`/`stream`) — /ai/chat itself is byte-identical,
    # untouched. Live memory (linch/Redis, 24h) stays the working set the model reasons over;
    # `state.runner.conversations` (ConversationStore, SQLite) is the durable system of record
    # for listing/reopening old conversations and reading their transcript back after the live
    # window has expired.

    async def _maybe_rehydrate(session_id: str, conversation_id: str) -> None:
        """Reopen-after-expiry: if the linch session has no live messages but the durable
        transcript does, seed the session with the last N turns so context survives the 24h
        Redis window. Fail-open — any error just means older context isn't reloaded."""
        conversations = getattr(state.runner, "conversations", None)
        store = getattr(state.runner, "session_store", None)
        n = int(getattr(state.settings, "chat_history_rehydrate_turns", 0) or 0)
        if conversations is None or store is None or n <= 0:
            return
        try:
            existing = await store.load_messages(session_id)
            if existing:
                return  # live memory still present — nothing to rehydrate
            turns = await asyncio.to_thread(conversations.recent_turns, conversation_id, n)
            if not turns:
                return
            from linch.types import Message, TextBlock
            msgs = [Message(role=t["role"], content=[TextBlock(text=t["content"])])
                    for t in turns
                    if (t.get("content") or "").strip() and t.get("role") in ("user", "assistant")]
            if not msgs:
                return
            try:
                await store.create(id=session_id)
            except Exception:
                pass
            await store.append_messages(session_id, msgs)
        except Exception:
            logger.exception("conversation rehydrate failed (fail-open)")

    def _persist_turn(owner_hash: str, conversation_id: str, session_id: str,
                      user_text: str, assistant_text: str) -> bool:
        """Durably record one user+assistant turn in SQLite (create-if-first). Fail-open: a
        store error must never break a chat reply that already succeeded. Skips a cross-owner
        conversation_id collision (owns() re-checked after create).

        Returns whether this call is the conversation's FIRST turn (i.e. it just created the
        row) — the caller uses that to fire the background auto-title generation exactly once,
        right after the model's first real answer exists to summarize."""
        conversations = getattr(state.runner, "conversations", None)
        if conversations is None or not owner_hash:
            return False
        first_use = False
        try:
            now = int(time.time())
            if not conversations.owns(owner_hash, conversation_id):
                conversations.create(owner_hash, conversation_id, session_id, "", now)
                first_use = True
            if conversations.owns(owner_hash, conversation_id):
                conversations.append_turn(conversation_id, user_text, assistant_text, now)
        except Exception:
            logger.exception("durable conversation persist failed (fail-open)")
        return first_use

    def _build_title_prompt(user_text: str, assistant_text: str) -> str:
        u = (user_text or "").strip()[:500]
        a = (assistant_text or "").strip()[:500]
        return f'Hỏi: "{u}"\nĐáp (rút gọn): "{a}"\n\nTiêu đề:'

    def _clean_title(raw: str) -> str:
        """First non-empty line of the model's reply, stripped of wrapping quotes/punctuation
        the model sometimes adds despite the prompt, capped generously as a backstop (the model
        is asked for ≤6 words; this is not the primary length control)."""
        line = next((l.strip() for l in (raw or "").splitlines() if l.strip()), "")
        line = line.strip(" \"'“”.–-")
        return line[:_RENAME_TITLE_MAX]

    async def _maybe_autotitle(owner_hash: str, conversation_id: str,
                               user_text: str, assistant_text: str) -> None:
        """Background, non-blocking: replace the interim (first-line-of-question) title with a
        short LLM-generated one summarizing the first Q&A pair. Fires exactly once per
        conversation (only on its first turn — see _persist_turn's `first_use`). Fail-open and
        fully decoupled from the request/response cycle: any failure just leaves the interim
        title in place, never surfaces to the caller, and never delays the chat reply (this
        coroutine is scheduled via `_fire_and_forget`, not awaited).

        Uses `rename_if_matches` (NOT the unconditional `rename`) so a manual PATCH rename
        that lands while this LLM call is still in flight always wins, never the other way
        around — this job only writes when the title still equals the exact interim value
        `_persist_turn`'s own fallback set, i.e. nothing has renamed it since."""
        conversations = getattr(state.runner, "conversations", None)
        if conversations is None:
            return
        try:
            expected_interim = derive_title(user_text)
            prompt = _build_title_prompt(user_text, assistant_text)
            raw, _meta = await asyncio.wait_for(
                state.runner.complete("conversation_title", prompt), timeout=15.0)
            title = _clean_title(raw)
            if title:
                await asyncio.to_thread(conversations.rename_if_matches,
                                        conversation_id, expected_interim, title, int(time.time()))
        except Exception:
            logger.warning("auto-title generation failed (fail-open, keeping interim title)",
                           exc_info=True)

    def _new_cid(supplied: str | None) -> str:
        cid = (supplied or "").strip()
        return cid or uuid4().hex

    @app.post("/ai/conversations/chat")
    async def conversations_chat(body: ConversationChatRequest) -> dict[str, Any]:
        owner_hash = _conv_owner_hash(body.user_id)
        conversation_id = _new_cid(body.conversation_id)
        session_id = _conv_session_id(owner_hash, conversation_id)
        if owner_hash:
            await _maybe_rehydrate(session_id, conversation_id)
        prompt = require_public_input(body.supplied_text())
        gated = await _chat_gate(prompt)
        if gated:
            message, metadata = gated
            metadata = {**metadata, "conversation_id": conversation_id}
            return {"response": message, "answer": message, "message": message,
                    "metadata": metadata, "conversation_id": conversation_id}
        try:
            text, metadata = await state.runner.complete("chat", prompt, session_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"AI service unavailable: {exc}") from exc
        text = sanitize_output_language(text)
        metadata = {**metadata, "conversation_id": conversation_id}
        if owner_hash:
            # Offload the sync SQLite write to a worker thread so it never blocks the event loop.
            first_use = await asyncio.to_thread(
                _persist_turn, owner_hash, conversation_id, session_id, prompt, text)
            if first_use:
                _fire_and_forget(_maybe_autotitle(owner_hash, conversation_id, prompt, text))
        return {"response": text, "answer": text, "message": text,
                "metadata": metadata, "conversation_id": conversation_id}

    @app.post("/ai/conversations/chat/stream")
    async def conversations_chat_stream(body: ConversationChatRequest) -> StreamingResponse:
        text = require_public_input(body.supplied_text())
        owner_hash = _conv_owner_hash(body.user_id)
        conversation_id = _new_cid(body.conversation_id)
        session_id = _conv_session_id(owner_hash, conversation_id)
        conv_frame = sse_frame("conversation", {"conversation_id": conversation_id})
        gated = await _chat_gate(text)
        if gated is not None:  # classifier short-circuit before the agent runs (not persisted)
            message, gated_metadata = gated
            md = {**gated_metadata, "conversation_id": conversation_id}
            return _short_circuit_stream(message, md, prelude=[conv_frame],
                                         top_level={"conversation_id": conversation_id})
        if owner_hash:
            await _maybe_rehydrate(session_id, conversation_id)
        user_text = text
        holder: dict[str, Any] = {"compaction": None}

        def observe(event: Any) -> bytes | None:
            if (getattr(state.settings, "conversation_compaction_meta", True)
                    and type(event).__name__ == "CompactionEvent"):
                info = {k: getattr(event, k, None) for k in
                        ("strategy", "tokens_before", "tokens_after",
                         "messages_before", "messages_after")}
                holder["compaction"] = info
                return sse_frame("compaction", info)
            return None

        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            md = {**base_meta("chat", run_id), "conversation_id": conversation_id}
            if holder["compaction"]:
                md["compaction"] = holder["compaction"]
            if owner_hash and final and final != STREAM_ERROR_MESSAGE:
                first_use = await asyncio.to_thread(
                    _persist_turn, owner_hash, conversation_id, session_id, user_text, final)
                if first_use:
                    _fire_and_forget(_maybe_autotitle(owner_hash, conversation_id, user_text, final))
            return {"content": final, "answer": final, "message": final,
                    "metadata": md, "conversation_id": conversation_id}

        return stream("chat", text, session_id, build_final,
                      prelude=[conv_frame], observe=observe)

    @app.post("/ai/conversations")
    async def create_conversation(body: CreateConversationRequest) -> dict[str, Any]:
        owner_hash = _conv_owner_hash(body.user_id)
        if not owner_hash:
            raise HTTPException(400, "user_id là bắt buộc để tạo hội thoại có lịch sử.")
        conversation_id = uuid4().hex
        session_id = _conv_session_id(owner_hash, conversation_id)
        now = int(time.time())
        conversations = getattr(state.runner, "conversations", None)
        if conversations is not None:
            try:
                await asyncio.to_thread(conversations.create, owner_hash, conversation_id,
                                        session_id, body.title or "", now)
            except Exception:
                logger.exception("create_conversation persist failed")
        return {"conversation_id": conversation_id, "title": body.title or "",
                "created_at": now, "updated_at": now, "message_count": 0}

    @app.get("/ai/conversations")
    async def list_conversations(user_id: str | None = None, limit: int = 20,
                                 offset: int = 0) -> dict[str, Any]:
        owner_hash = _conv_owner_hash(user_id)
        if not owner_hash:
            raise HTTPException(400, "user_id là bắt buộc để liệt kê hội thoại.")
        conversations = getattr(state.runner, "conversations", None)
        if conversations is None:
            return {"conversations": [], "total": 0, "limit": limit, "offset": offset}
        rows, total = await asyncio.to_thread(conversations.list_for_owner, owner_hash, limit, offset)
        return {"conversations": rows, "total": total, "limit": limit, "offset": offset}

    @app.get("/ai/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, user_id: str | None = None,
                               limit: int = 200, offset: int = 0) -> dict[str, Any]:
        owner_hash = _conv_owner_hash(user_id)
        if not owner_hash:
            raise HTTPException(400, "user_id là bắt buộc để đọc hội thoại.")
        conversations = getattr(state.runner, "conversations", None)
        owned = conversations is not None and await asyncio.to_thread(
            conversations.owns, owner_hash, conversation_id)
        if not owned:
            raise HTTPException(404, "Không tìm thấy hội thoại.")   # 404, không xác nhận tồn tại
        row = await asyncio.to_thread(conversations.get, conversation_id) or {}
        messages, total = await asyncio.to_thread(conversations.messages, conversation_id, limit, offset)
        return {"conversation_id": conversation_id, "title": row.get("title", ""),
                "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
                "message_count": row.get("message_count", total), "messages": messages,
                "total": total, "limit": limit, "offset": offset}

    @app.patch("/ai/conversations/{conversation_id}")
    async def rename_conversation(conversation_id: str, body: RenameConversationRequest) -> dict[str, Any]:
        """User-driven rename — sets an explicit title, overriding whatever the auto-title
        (interim first-line, or the background LLM title) produced. Idempotent; no effect on
        the transcript or the live session."""
        owner_hash = _conv_owner_hash(body.user_id)
        if not owner_hash:
            raise HTTPException(400, "user_id là bắt buộc để đổi tên hội thoại.")
        title = (body.title or "").strip()
        if not title:
            raise HTTPException(400, "title không được để trống.")
        title = title[:_RENAME_TITLE_MAX]
        conversations = getattr(state.runner, "conversations", None)
        owned = conversations is not None and await asyncio.to_thread(
            conversations.owns, owner_hash, conversation_id)
        if not owned:
            raise HTTPException(404, "Không tìm thấy hội thoại.")
        now = int(time.time())
        await asyncio.to_thread(conversations.rename, conversation_id, title, now)
        row = await asyncio.to_thread(conversations.get, conversation_id) or {}
        return {"conversation_id": conversation_id, "title": title,
                "created_at": row.get("created_at"), "updated_at": now,
                "message_count": row.get("message_count", 0)}

    @app.delete("/ai/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str,
                                  user_id: str | None = None) -> dict[str, Any]:
        owner_hash = _conv_owner_hash(user_id)
        if not owner_hash:
            raise HTTPException(400, "user_id là bắt buộc để xoá hội thoại.")
        conversations = getattr(state.runner, "conversations", None)
        owned = conversations is not None and await asyncio.to_thread(
            conversations.owns, owner_hash, conversation_id)
        if not owned:
            raise HTTPException(404, "Không tìm thấy hội thoại.")
        row = await asyncio.to_thread(conversations.get, conversation_id) or {}
        await asyncio.to_thread(conversations.soft_delete, conversation_id)
        store = getattr(state.runner, "session_store", None)
        sid = row.get("session_id")
        if store is not None and sid:
            try:
                await store.delete(sid)
            except Exception:
                logger.exception("delete_conversation session cleanup failed")
        return {"deleted": True, "conversation_id": conversation_id}

    def _translate_chunks(body: LegacyRequest) -> tuple[list[tuple[str, str]], Any, Any]:
        """Split the input and enforce the max-chunk cap (413) so one request can't fan out
        into unbounded model calls. Returns (chunks, src, tgt)."""
        src, tgt = translate_langs(body)
        chunks = split_for_translation(body.supplied_text(), state.settings.translate_chunk_chars)
        if len(chunks) > state.settings.translate_max_chunks:
            raise HTTPException(413, f"Văn bản quá dài để dịch (>{state.settings.translate_max_chunks} "
                                     "khối). Vui lòng chia nhỏ yêu cầu.")
        return chunks, src, tgt

    async def translate_assemble(body: LegacyRequest) -> tuple[str, dict[str, Any]]:
        """Translate the whole input, chunking long text so no single model turn has to emit
        a translation past its output-token cap (which would truncate). Each chunk runs the
        full mask→translate→sanitize→unmask pipeline; chunks are rejoined with their exact
        separators. complete() already sanitizes; we unmask each chunk's literals here."""
        chunks, src, tgt = _translate_chunks(body)

        async def render(chunk_text: str) -> str:
            prompt, literals = build_translate_prompt(chunk_text, src, tgt)
            out, _meta = await complete("translate", body, prompt_override=prompt)
            return unmask_literals(out, literals)

        if len(chunks) == 1:
            return await render(chunks[0][0]), base_meta("translate", None)

        sem = asyncio.Semaphore(max(1, state.settings.translate_chunk_concurrency))

        async def render_bounded(chunk_text: str) -> str:
            async with sem:
                return await render(chunk_text)

        outs = await asyncio.gather(*(render_bounded(c) for c, _ in chunks))
        final = "".join(out + sep for out, (_c, sep) in zip(outs, chunks))
        meta = base_meta("translate", None)
        meta["chunks"] = len(chunks)
        return final, meta

    @app.post("/ai/translate")
    async def translate(body: LegacyRequest) -> dict[str, Any]:
        text, metadata = await translate_assemble(body)
        return {"translated_text": text, "translation": text, "metadata": metadata}

    @app.post("/ai/translate/stream")
    async def translate_stream(body: LegacyRequest) -> StreamingResponse:
        chunks, src, tgt = _translate_chunks(body)  # raises 413 before streaming starts

        if len(chunks) <= 1:
            prompt, literals = build_translate_prompt(chunks[0][0], src, tgt)

            async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
                # Restore literals in the authoritative `final` event (token deltas may still
                # show ⟦L…⟧ placeholders mid-stream; the final text is always correct).
                final = unmask_literals(final, literals)
                return {"content": final, "translation": final, "translated_text": final, "metadata": base_meta("translate", run_id)}

            return stream("translate", prompt, None, build_final)

        # Long input: assemble the chunked translation, then emit it as one token+final SSE.
        # True token streaming is traded for guaranteed completeness (no truncated tail).
        async def gen() -> AsyncIterator[bytes]:
            try:
                final, meta = await translate_assemble(body)
            except Exception:  # provider/runtime failure → visible error, not a torn stream
                logger.exception("chunked translate_stream failed")
                final, meta = STREAM_ERROR_MESSAGE, base_meta("translate", None)
                yield sse_frame("error", {"error": STREAM_ERROR_MESSAGE})
            yield sse_frame("token", {"delta": final})
            yield sse_frame("final", {"content": final, "translation": final,
                                      "translated_text": final, "metadata": meta})
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/ai/generate-report")
    async def report(body: LegacyRequest) -> dict[str, Any]:
        text, metadata = await complete("report", body, prompt_override=report_prompt(body))
        return {"report": text, "metadata": metadata}

    @app.post("/ai/generate-report/stream")
    async def report_stream(body: LegacyRequest) -> StreamingResponse:
        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            return {"content": final, "report": final, "metadata": base_meta("report", run_id)}
        return stream("report", report_prompt(body), None, build_final)

    @app.post("/ai/generate-statistical-report")
    async def statistical_report(body: LegacyRequest) -> dict[str, Any]:
        text, metadata = await complete("statistical_report", body, prompt_override=statistical_prompt(body))
        # Legacy gemini shape: report_title / report_summary / report_detail (parsed from JSON).
        return {**parse_statistical(text), "report": text, "statistical_report": text, "metadata": metadata}

    @app.post("/ai/generate-statistical-report/stream")
    async def statistical_stream(body: LegacyRequest) -> StreamingResponse:
        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            return {**parse_statistical(final), "content": final, "report": final, "statistical_report": final, "metadata": base_meta("statistical_report", run_id)}
        return stream("statistical_report", statistical_prompt(body), None, build_final)

    async def exploit_prefetch(prompt: str) -> list[dict[str, Any]]:
        """Deterministic evidence gathering (see poc/prefetch.py) — runs independently of,
        and concurrently with, the model's own agentic turn; its own scratch run_id keeps
        it fully separate from whatever run_id the model's session ends up using, so it
        can never collide with or be starved by the agentic path. Fail-open: any error
        yields no evidence, falling through to the agentic path + the RequireSearchBefore-
        AnswerHook nudge + the force_no_poc_notice backstop."""
        poc = getattr(state.runner, "poc", None)
        ledger = getattr(state.runner, "ledger", None)
        if poc is None or ledger is None:
            return []
        # Layer-4 exploit relevance gate (opt-in, EXPLOIT-ONLY): a FREE-TEXT prompt (no CVE
        # id) that maps to no sufficiently-relevant KB entry returns no evidence, so the
        # exploit path yields the canonical "no PoC" result instead of building one from a
        # weak keyword match (the "nhập linh tinh vẫn ra PoC" gap). Inert by default; a
        # named CVE is never gated; fail-open (None → not gated).
        from ..poc.prefetch import _CVE, exploit_relevance_ok
        if not _CVE.search(prompt or "") and await exploit_relevance_ok(prompt) is False:
            logger.info("exploit prefetch gated: low KB relevance for free-text prompt")
            return []
        run_id = f"prefetch:{uuid4().hex}"
        try:
            await prefetch_evidence(poc, ledger, prompt, run_id)
            return ledger.evidence_for_run(run_id)
        except Exception:
            logger.exception("deterministic PoC prefetch failed")
            return []

    def inject_reference_code(text: str, run_id: str | None,
                              pre_evidence: list[dict[str, Any]] | None = None) -> tuple[str, dict[str, Any]]:
        """Deterministic Section-1 placement: the model never writes Section 1 (see
        prompts/exploit.md) — this places it from the merged evidence (deterministic
        prefetch + whatever the model's own agentic run gathered, deduplicated by
        sha256). Real evidence → the exact fetched code (never the model's own words). No
        real evidence → the canonical "no PoC info" sentence, never fabricated/AI-
        illustrated code. Also strips any line where the model narrates the Section-1
        auto-insertion mechanism back to the user (observed live — a leak of the system
        prompt's own instruction wording into the visible answer). Fail-open — on any
        error the model's own text is returned so this never breaks the response."""
        ledger = getattr(state.runner, "ledger", None)
        try:
            text = strip_system_narration(text)
            run_evidence = ledger.evidence_for_run(run_id) if (ledger is not None and run_id) else []
            evidence = merge_evidence(pre_evidence or [], run_evidence)
            text, report = enforce_reference_code(text, evidence)
            if not report.get("reference_code_injected"):
                text, fallback = force_no_poc_notice(text, evidence)
                report.update(fallback)
            return text, report
        except Exception:
            logger.exception("reference-code injection failed run_id=%s", run_id)
            return text, {"reference_code_injected": False, "reason": "error"}

    def exploit_final_meta(run_id: str | None) -> dict[str, Any]:
        meta = base_meta("exploit_research", run_id)
        ledger = getattr(state.runner, "ledger", None)
        if ledger is not None and run_id:
            meta.update(ledger.exploit_metadata(run_id))
        meta.setdefault("evidence_ids", []); meta.setdefault("edb_ids", [])
        meta.setdefault("provenance", []); meta.setdefault("candidate_status", "not_generated")
        return meta

    @app.post("/ai/exploit")
    async def exploit(body: LegacyRequest) -> dict[str, Any]:
        prompt_text = require_public_input(body.supplied_text())
        prefetch_task = asyncio.create_task(exploit_prefetch(prompt_text))
        text, metadata = await complete("exploit_research", body)
        pre_evidence = await prefetch_task
        text, injected = inject_reference_code(text, metadata.get("run_id"), pre_evidence)
        no_poc = "No verified public PoC was found in the supplied evidence."
        metadata.setdefault("evidence_ids", []); metadata.setdefault("edb_ids", [])
        metadata.setdefault("provenance", []); metadata.setdefault("candidate_status", "not_generated")
        metadata.update(injected)
        return {"description": text or no_poc, "exploit_guide": text or no_poc, "metadata": metadata}

    @app.post("/ai/exploit/stream")
    async def exploit_stream(body: LegacyRequest) -> StreamingResponse:
        text = require_public_input(body.supplied_text())
        no_poc = "No verified public PoC was found in the supplied evidence."
        prefetch_task = asyncio.create_task(exploit_prefetch(text))

        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            pre_evidence = await prefetch_task
            final, injected = inject_reference_code(final, run_id, pre_evidence)
            meta = exploit_final_meta(run_id)
            meta.update(injected)
            return {"content": final or no_poc, "exploit_guide": final or no_poc, "description": final or no_poc, "metadata": meta}

        return stream("exploit_research", text, None, build_final)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        s = state.settings
        provider_kind = s.llm_provider.lower()
        if provider_kind == "deepseek":
            llm_base = s.deepseek_base_url.rstrip("/")
            llm_key = s.deepseek_api_key
        else:
            llm_base = s.qwen_base_url.rstrip("/")
            llm_key = s.qwen_api_key
        llm_headers = {"Authorization": f"Bearer {llm_key}"} if llm_key else {}
        qwen_ok = False
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                response = await client.get(llm_base + "/models", headers=llm_headers)
                qwen_ok = response.is_success
        except httpx.HTTPError:
            pass
        # PoC backend health — in-process searchsploit/Exploit-DB.
        searchsploit_ok = False
        commit = None
        poc = getattr(state.runner, "poc", None)
        if poc is not None and hasattr(poc, "health"):
            try:
                searchsploit_ok, commit = await poc.health()
            except Exception:
                pass
        store = getattr(state.runner, "session_store", None)
        if store is not None and hasattr(store, "ping"):
            redis_status = "connected" if await store.ping() else "unreachable"
        else:
            redis_status = "memory-dev"
        # Ư4: surface the input-classifier fail-open rate here too, not just as a WARNING log
        # line — this is exactly the CLAUDE.md-documented failure mode (guard silently disabled,
        # /health said ok) that fail-open observability was built to catch.
        sanitize_stats = verdict_stats()
        status = _health_status(qwen_ok, sanitize_stats)
        return {"status": status, "message": "secai gateway", "backend": provider_kind, "redis": redis_status, "postgres": "disabled", "llm": qwen_ok, "qwen": qwen_ok, "searchsploit": searchsploit_ok, "exploitdb_commit": commit, "input_sanitize": sanitize_stats}

    @app.post("/admin/refresh-models")
    async def refresh_models(x_api_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        expected = state.settings.admin_api_key
        if not expected or x_api_key != expected:
            raise HTTPException(401, "Invalid admin API key")
        return {"status": "ok", "refreshed": True}
