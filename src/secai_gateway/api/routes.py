from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
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
from ..security import require_public_input, sanitize_output_language
from .messages import parse_statistical, report_prompt, statistical_prompt, translate_prompt
from .schemas import LegacyRequest
from .sse import event_to_sse, sse_frame

logger = logging.getLogger(__name__)

# Shown to the user when a run fails. Delivered on the *visible* channels (token +
# final), not only as an `event: error` frame — some UIs render assistant text but
# ignore the error event, and a silent empty bubble is worse than a plain apology.
STREAM_ERROR_MESSAGE = (
    "Xin lỗi, trợ lý đang gặp sự cố tạm thời nên chưa thể trả lời. Vui lòng thử lại sau giây lát."
)


def _chat_session_id(body: LegacyRequest) -> str:
    # Stable per-user session id → linch loads/compacts the prior conversation from its
    # (Redis) session store. Only the new message is sent; history is NOT re-stuffed.
    user_id = str(body.user_id or "").strip()
    if not user_id or user_id == "anonymous":
        # No stable identity → a fresh id per request, so anonymous callers never
        # share (and read back) one another's conversation history.
        return f"chat:anon:{uuid4().hex}"
    return f"chat:{hashlib.sha256(user_id.encode()).hexdigest()}"


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

    def stream(profile: str, prompt: str, session_id: str | None, build_final) -> StreamingResponse:
        """Shared SSE runner for every agentic/text endpoint: streams tool_call /
        tool_result / token events as they happen, then one final event built by the
        endpoint's ``build_final(final_text, run_id)`` (which may also persist state)."""

        async def gen() -> AsyncIterator[bytes]:
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

    @app.post("/chat_no_stream")
    @app.post("/ai/chat")
    async def chat(body: LegacyRequest) -> dict[str, Any]:
        text, metadata = await complete("chat", body, chat=True)
        return {"response": text, "answer": text, "message": text, "metadata": metadata}

    @app.post("/ai/chat/stream")
    async def chat_stream(body: LegacyRequest) -> StreamingResponse:
        text = require_public_input(body.supplied_text())
        session_id = _chat_session_id(body)

        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            # linch persists the turn to its session store during the run — nothing to do here.
            return {"content": final, "answer": final, "message": final, "metadata": base_meta("chat", run_id)}

        return stream("chat", text, session_id, build_final)

    @app.post("/ai/translate")
    async def translate(body: LegacyRequest) -> dict[str, Any]:
        text, metadata = await complete("translate", body, prompt_override=translate_prompt(body))
        return {"translated_text": text, "translation": text, "metadata": metadata}

    @app.post("/ai/translate/stream")
    async def translate_stream(body: LegacyRequest) -> StreamingResponse:
        async def build_final(final: str, run_id: str | None) -> dict[str, Any]:
            return {"content": final, "translation": final, "translated_text": final, "metadata": base_meta("translate", run_id)}
        return stream("translate", translate_prompt(body), None, build_final)

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
        return {"status": "ok" if qwen_ok else "degraded", "message": "secai gateway", "backend": provider_kind, "redis": redis_status, "postgres": "disabled", "llm": qwen_ok, "qwen": qwen_ok, "searchsploit": searchsploit_ok, "exploitdb_commit": commit}

    @app.post("/admin/refresh-models")
    async def refresh_models(x_api_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        expected = state.settings.admin_api_key
        if not expected or x_api_key != expected:
            raise HTTPException(401, "Invalid admin API key")
        return {"status": "ok", "refreshed": True}
