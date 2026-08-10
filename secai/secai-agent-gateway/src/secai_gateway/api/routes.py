from __future__ import annotations

import asyncio
import hashlib
import json
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
    canonical_block,
    enforce_reference_code,
    force_no_poc_notice,
    merge_evidence,
    prefetch_evidence,
    strip_system_narration,
)
from ..poc.backend import PocBadRequest, PocError, PocForbidden, PocNotFound
from ..poc.kt_client import KtClientError
from ..poc.kt_exploit import parse_exploit_intent, run_kt_exploit
from ..poc.runners.toolbox import run_raw_command, run_tool
from ..poc.verify import verify_poc
from ..security import require_public_input, sanitize_output_language
from .messages import parse_statistical, report_prompt, statistical_prompt, translate_prompt
from .schemas import (
    BughuntRequest,
    ExecRequest,
    ExploitRunRequest,
    LegacyRequest,
    ToolRequest,
    VerifyRequest,
)
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


def _exploit_session_id(body: LegacyRequest) -> str:
    # Separate memory thread from chat (own prefix) so a PoC-verify conversation ("giờ verify
    # vào target X") remembers the CVE discussed 1-2 turns earlier — exploit_research was
    # stateless (session_id always None) until poc_verify/poc_scan/poc_exec became agent tools,
    # at which point "interactive" chaining across turns needs real conversation memory.
    user_id = str(body.user_id or "").strip()
    if not user_id or user_id == "anonymous":
        return f"exploit:anon:{uuid4().hex}"
    return f"exploit:{hashlib.sha256(user_id.encode()).hexdigest()}"


def _kt_slots_from_request(body: LegacyRequest, text: str) -> dict[str, Any] | None:
    """Structured fields win; else fail-closed free-text parse (CVE + IP + intent)."""
    cve = (getattr(body, "cve", None) or "").strip()
    target = (getattr(body, "target", None) or getattr(body, "target_system", None) or "").strip()
    port = getattr(body, "port", None)
    if cve and target:
        # strip host:port into parts if needed
        ip, p = target, port
        if "://" not in target and target.count(":") == 1:
            left, right = target.rsplit(":", 1)
            if right.isdigit():
                ip, p = left, p or right
        return {"ip": ip, "cve": cve, "port": str(p) if p not in (None, "") else None,
                "command": (getattr(body, "command", None) or "id")}
    return parse_exploit_intent(text)


def _kt_prompt_appendix(result: dict[str, Any]) -> str:
    """Compact JSON for the model — full log stays in metadata / SSE for the FE."""
    scan = result.get("scan") or {}
    preview = (scan.get("cleaned_output") or "")[:2000]
    slim = {
        "exploit_verdict": result.get("exploit_verdict"),
        "scanner_finished": result.get("scanner_finished"),
        "has_poc": result.get("has_poc"),
        "has_msf": result.get("has_msf"),
        "target_ip": result.get("target_ip"),
        "target_port": result.get("target_port"),
        "cve": result.get("cve"),
        "command": result.get("command"),
        "error": result.get("error"),
        "cleaned_output_preview": preview,
    }
    return ("\n\n[Hệ thống đã chạy deterministic poc_kt_exploit — CHỈ tóm tắt JSON sau, "
            "không bịa, không gọi lại trừ khi user yêu cầu chạy lại:\n"
            + json.dumps(slim, ensure_ascii=False) + "\n]")


async def _run_kt_if_applicable(settings: Any, body: LegacyRequest, text: str,
                                run_id: str) -> dict[str, Any] | None:
    if not getattr(settings, "poc_exploit_enabled", False):
        return None
    if not (getattr(settings, "poc_exploit_api_url", "") or "").strip():
        return None
    slots = _kt_slots_from_request(body, text)
    if not slots:
        return None
    try:
        return await run_kt_exploit(
            settings,
            ip=slots["ip"], cve=slots["cve"], port=slots.get("port"),
            command=slots.get("command") or "id",
            use_proxy=True, run_id=run_id,
        )
    except (PocError, KtClientError) as exc:
        return {
            "ok": False,
            "run_id": run_id,
            "error": str(exc),
            "exploit_verdict": "error",
            "target_ip": slots.get("ip"),
            "cve": slots.get("cve"),
            "target_port": slots.get("port"),
        }


# Total chars of FULL PoC source rendered into ONE exploit Section 1. The model fetches
# ≤ _MAX_FETCH_PER_RUN PoCs/run so the real set is small; this only backstops a pathological
# repo. Fullening happens AFTER the model runs → it never enters the model context.
_MAX_DISPLAY_CHARS = 2_000_000


async def _fullen_evidence_for_display(evidence: list[dict[str, Any]], poc: Any,
                                       budget: int = _MAX_DISPLAY_CHARS) -> list[dict[str, Any]]:
    """Swap each evidence file's context-truncated code for its FULL, untruncated bytes,
    read back from the content store via the PoC backend (same license gate + path-escape
    guard as poc_fetch). This runs at Section-1 injection time — AFTER the model produced its
    answer — so the DISPLAYED PoC (in `description`/`exploit_guide`, which the FE renders) is
    complete, while the model's own context stays bounded by the fetch-time caps. Bounded by
    `budget`; fail-open per file (a file that can't be re-read keeps its truncated block)."""
    if poc is None:
        return evidence
    total = 0
    for ev in evidence or []:
        files = ev.get("source_files")
        if files:
            # First try to render the WHOLE repo, not just the files the fetch budget let in:
            # resolve the owning PoC from any file's hash and pull its complete set, full.
            entry_sha = next((f.get("sha256") for f in files if f.get("sha256")), None)
            full_set = None
            if entry_sha:
                try:
                    full_set = await poc.read_full_fileset(entry_sha)
                except Exception:
                    full_set = None
            if full_set:
                set_chars = sum(len(f.get("code") or "") for f in full_set)
                if total + set_chars <= budget:
                    ev["source_files"] = full_set          # ALL files, full content
                    total += set_chars
                    continue
            # Fallback (no stored set, or over budget): fullen each present file's content.
            for f in files:
                sha = f.get("sha256")
                if not sha:
                    continue
                try:
                    full = await poc.read_artifact(sha)
                except Exception:
                    continue
                content = full.get("content") or ""
                if not content or total + len(content) > budget:
                    continue
                f["code"], f["truncated"] = content, bool(full.get("truncated"))
                total += len(content)
        elif ev.get("source_code") and ev.get("sha256"):
            try:
                full = await poc.read_artifact(ev["sha256"])
            except Exception:
                continue
            content = full.get("content") or ""
            if not content or total + len(content) > budget:
                continue
            ev["source_code"], ev["code_truncated"] = content, bool(full.get("truncated"))
            total += len(content)
    return evidence


def register_routes(app: FastAPI, state: Any) -> None:
    """Attach every gateway endpoint to ``app`` (closures capture the shared state)."""

    async def complete(profile: str, body: LegacyRequest, *, prompt_override: str | None = None,
                       session_id: str | None = None) -> tuple[str, dict[str, Any]]:
        if prompt_override is not None:
            # Endpoint already built the task-specific user message (translate/report/…).
            prompt = prompt_override
        else:
            prompt = require_public_input(body.supplied_text())
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
        endpoint's ``build_final(final_text, run_id)`` (which may also persist state).

        A ``regenerate`` event (see api/sse.py) can arrive mid-stream when a hook forces
        the model to redo its answer (e.g. it answered before calling required tools) —
        clients accumulating `token` deltas into one buffer MUST clear that buffer on
        ``regenerate``, or the rejected draft and the corrected one render concatenated
        with no boundary between them. The final `final` event is always the corrected,
        authoritative text regardless — only the raw `token` stream is affected."""

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
        text, metadata = await complete("chat", body, session_id=_chat_session_id(body))
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

    async def exploit_prefetch(prompt: str, session_id: str | None = None) -> list[dict[str, Any]]:
        """Deterministic evidence gathering (see poc/prefetch.py) — runs independently of,
        and concurrently with, the model's own agentic turn; its own scratch run_id keeps
        it fully separate from whatever run_id the model's session ends up using, so it
        can never collide with or be starved by the agentic path. Records prefetch
        ``coverage`` on ``session_id`` so RequireSearchBeforeAnswerHook can skip
        force_continue when coverage is already ``enough``|``empty``. Fail-open: any
        error → coverage ``failed`` + empty evidence (hook + force_no_poc_notice backstop)."""
        from ..poc.prefetch import set_session_coverage
        poc = getattr(state.runner, "poc", None)
        ledger = getattr(state.runner, "ledger", None)
        if poc is None or ledger is None:
            set_session_coverage(session_id, "failed")
            return []
        run_id = f"prefetch:{uuid4().hex}"
        try:
            report = await prefetch_evidence(poc, ledger, prompt, run_id)
            cov = report.get("coverage") or "failed"
            if cov not in ("enough", "empty", "partial", "failed"):
                cov = "failed"
            set_session_coverage(session_id, cov)
            return ledger.evidence_for_run(run_id)
        except Exception:
            logger.exception("deterministic PoC prefetch failed")
            set_session_coverage(session_id, "failed")
            return []

    async def inject_reference_code(text: str, run_id: str | None,
                                    pre_evidence: list[dict[str, Any]] | None = None) -> tuple[str, dict[str, Any]]:
        """Deterministic Section-1 placement: the model never writes Section 1 (see
        prompts/exploit.md) — this places it from the merged evidence (deterministic
        prefetch + whatever the model's own agentic run gathered, deduplicated by
        sha256). Real evidence → the exact fetched code (never the model's own words). No
        real evidence → the canonical "no PoC info" sentence, never fabricated/AI-
        illustrated code. Also strips any line where the model narrates the Section-1
        auto-insertion mechanism back to the user (observed live — a leak of the system
        prompt's own instruction wording into the visible answer). Fail-open — on any
        error the model's own text is returned so this never breaks the response.

        Before rendering, each evidence file's context-truncated code is swapped for its FULL
        store bytes (`_fullen_evidence_for_display`) — the model already ran on the bounded
        slice, so the DISPLAYED Section 1 (which the FE renders) shows the complete PoC while
        the model context stays capped. No new endpoint, no FE change needed."""
        ledger = getattr(state.runner, "ledger", None)
        try:
            text = strip_system_narration(text)
            run_evidence = ledger.evidence_for_run(run_id) if (ledger is not None and run_id) else []
            evidence = merge_evidence(pre_evidence or [], run_evidence)
            evidence = await _fullen_evidence_for_display(evidence, getattr(state.runner, "poc", None))
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
        session_id = _exploit_session_id(body)
        kt_run_id = f"kt:{uuid4().hex}"
        kt_result = await _run_kt_if_applicable(state.settings, body, prompt_text, kt_run_id)
        agent_prompt = prompt_text + (_kt_prompt_appendix(kt_result) if kt_result else "")
        prefetch_task = asyncio.create_task(exploit_prefetch(prompt_text, session_id))
        text, metadata = await complete("exploit_research", body, session_id=session_id,
                                        prompt_override=agent_prompt)
        pre_evidence = await prefetch_task
        text, injected = await inject_reference_code(text, metadata.get("run_id"), pre_evidence)
        no_poc = "No verified public PoC was found in the supplied evidence."
        metadata.setdefault("evidence_ids", []); metadata.setdefault("edb_ids", [])
        metadata.setdefault("provenance", []); metadata.setdefault("candidate_status", "not_generated")
        metadata.update(injected)
        if kt_result is not None:
            metadata["poc_kt_exploit"] = kt_result
            _audit(kt_run_id, "poc_kt_exploit_http",
                   f"{kt_result.get('cve')}:{kt_result.get('target_ip')}",
                   {"user_id": body.user_id, "exploit_verdict": kt_result.get("exploit_verdict"),
                    "ok": kt_result.get("ok"), "error": kt_result.get("error")})
        return {"description": text or no_poc, "exploit_guide": text or no_poc, "metadata": metadata}

    @app.post("/ai/exploit/stream")
    async def exploit_stream(body: LegacyRequest) -> StreamingResponse:
        """Own generator (not the shared `stream()`) so it can emit a `section1` PREVIEW
        event as soon as the deterministic prefetch resolves — which runs concurrently
        with, and often finishes well before, the model's own turn. Without this, Section
        1 (often the largest part of the answer, sometimes >10k chars) only ever appears
        in one lump inside the final `final` event, which — even though the model's own
        `token` deltas WERE streaming smoothly the whole time — reads as "not streaming"
        to anyone watching the message reflow. The model still never writes Section 1
        (unchanged invariant); this only changes WHEN the already-fetched real evidence is
        pushed to the client, not what generates it. `final` remains authoritative — it
        re-merges prefetch + any evidence the model's own tool calls gathered, so a preview
        that was incomplete (or arrived too early to have anything yet) gets corrected.

        When POC_EXPLOIT_ENABLED and the request parses to target+CVE+intent, also runs
        deterministic ``poc_kt_exploit`` (5568), emits tool_call/tool_result SSE, and
        appends a compact JSON appendix to the model prompt so it summarizes real logs."""
        text = require_public_input(body.supplied_text())
        no_poc = "No verified public PoC was found in the supplied evidence."
        session_id = _exploit_session_id(body)
        prefetch_task = asyncio.create_task(exploit_prefetch(text, session_id))
        kt_run_id = f"kt:{uuid4().hex}"
        kt_task: asyncio.Task | None = asyncio.create_task(
            _run_kt_if_applicable(state.settings, body, text, kt_run_id))

        async def build_final(final: str, run_id: str | None,
                              kt_result: dict[str, Any] | None = None) -> dict[str, Any]:
            pre_evidence = await prefetch_task
            final, injected = await inject_reference_code(final, run_id, pre_evidence)
            meta = exploit_final_meta(run_id)
            meta.update(injected)
            if kt_result is not None:
                meta["poc_kt_exploit"] = kt_result
            return {"content": final or no_poc, "exploit_guide": final or no_poc,
                    "description": final or no_poc, "metadata": meta}

        async def gen() -> AsyncIterator[bytes]:
            final = ""
            run_id = None
            failed = False
            section1_sent = False
            kt_result: dict[str, Any] | None = None
            kt_emitted = False
            # Wait up to 2s for KT so we can append appendix before the model starts when
            # intent is clear; if slower, start model and emit tool SSE when KT finishes.
            agent_text = text
            pending_kt: asyncio.Task | None = kt_task
            if pending_kt is not None:
                done, _ = await asyncio.wait({pending_kt}, timeout=2.0)
                if pending_kt in done:
                    try:
                        kt_result = pending_kt.result()
                    except Exception:
                        logger.exception("kt exploit early wait failed")
                        kt_result = None
                    pending_kt = None
                    if kt_result is not None:
                        agent_text = text + _kt_prompt_appendix(kt_result)

            model_agen = state.runner.stream("exploit_research", agent_text, session_id).__aiter__()
            model_next = asyncio.ensure_future(model_agen.__anext__())
            pending_prefetch: asyncio.Task | None = prefetch_task
            try:
                if kt_result is not None and not kt_emitted:
                    kt_emitted = True
                    tid = kt_run_id
                    yield sse_frame("tool_call", {
                        "id": tid, "name": "poc_kt_exploit",
                        "input": {"ip": kt_result.get("target_ip"), "cve": kt_result.get("cve"),
                                  "port": kt_result.get("target_port")},
                        "summary": "poc_kt_exploit(deterministic)",
                    })
                    payload = json.dumps(kt_result, ensure_ascii=False)
                    yield sse_frame("tool_result", {
                        "id": tid, "name": "poc_kt_exploit",
                        "is_error": not kt_result.get("ok", True),
                        "result": payload[:8000],
                        "summary": f"poc_kt_exploit {kt_result.get('exploit_verdict')}",
                    })
                    _audit(kt_run_id, "poc_kt_exploit_http",
                           f"{kt_result.get('cve')}:{kt_result.get('target_ip')}",
                           {"user_id": body.user_id,
                            "exploit_verdict": kt_result.get("exploit_verdict"),
                            "ok": kt_result.get("ok")})

                while True:
                    waiting = {model_next}
                    if pending_prefetch is not None:
                        waiting.add(pending_prefetch)
                    if pending_kt is not None:
                        waiting.add(pending_kt)
                    done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

                    if pending_kt is not None and pending_kt in done:
                        try:
                            kt_result = pending_kt.result()
                        except Exception:
                            logger.exception("kt exploit task failed")
                            kt_result = None
                        pending_kt = None
                        if kt_result is not None and not kt_emitted:
                            kt_emitted = True
                            tid = kt_run_id
                            yield sse_frame("tool_call", {
                                "id": tid, "name": "poc_kt_exploit",
                                "input": {"ip": kt_result.get("target_ip"),
                                          "cve": kt_result.get("cve"),
                                          "port": kt_result.get("target_port")},
                                "summary": "poc_kt_exploit(deterministic)",
                            })
                            payload = json.dumps(kt_result, ensure_ascii=False)
                            yield sse_frame("tool_result", {
                                "id": tid, "name": "poc_kt_exploit",
                                "is_error": not kt_result.get("ok", True),
                                "result": payload[:8000],
                                "summary": f"poc_kt_exploit {kt_result.get('exploit_verdict')}",
                            })
                            _audit(kt_run_id, "poc_kt_exploit_http",
                                   f"{kt_result.get('cve')}:{kt_result.get('target_ip')}",
                                   {"user_id": body.user_id,
                                    "exploit_verdict": kt_result.get("exploit_verdict"),
                                    "ok": kt_result.get("ok")})

                    if pending_prefetch is not None and pending_prefetch in done:
                        pre_evidence = pending_prefetch.result()
                        pending_prefetch = None
                        if not section1_sent:
                            fullened = await _fullen_evidence_for_display(
                                list(pre_evidence), getattr(state.runner, "poc", None))
                            preview_body = canonical_block(fullened)
                            if preview_body:
                                section1_sent = True
                                yield sse_frame("section1", {"body": preview_body, "preview": True})

                    if model_next in done:
                        try:
                            event = model_next.result()
                        except StopAsyncIteration:
                            break
                        model_next = asyncio.ensure_future(model_agen.__anext__())
                        if isinstance(event, SystemEvent):
                            run_id = getattr(event, "run_id", None) or run_id
                        if isinstance(event, ResultEvent):
                            final = sanitize_output_language(event.final_text or "")
                        frame = event_to_sse(event)
                        if frame:
                            yield frame
            except Exception:
                failed = True
                logger.exception("stream failed for profile=exploit_research")
                yield sse_frame("error", {"error": STREAM_ERROR_MESSAGE})
                yield sse_frame("token", {"delta": STREAM_ERROR_MESSAGE})
                if pending_kt is not None:
                    try:
                        kt_result = await pending_kt
                    except Exception:
                        pass
                yield sse_frame("final", await build_final(STREAM_ERROR_MESSAGE, run_id, kt_result))
            finally:
                model_next.cancel()
            if not failed:
                # Ensure KT finished for metadata even if SSE already emitted
                if pending_kt is not None:
                    try:
                        kt_result = await pending_kt
                    except Exception:
                        pass
                yield sse_frame("final", await build_final(final, run_id, kt_result))
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    def _audit(run_id: str, tool_name: str, key_material: str, meta: dict[str, Any]) -> None:
        """Ghi 1 dòng audit cho lệnh gọi HTTP trực tiếp vào Kali (verify/tool/exec) — song song
        với ledger.audit_once mà agent-tool path (poc/tools.py) đã dùng, để MỌI cách gọi (LLM lẫn
        API trực tiếp) đều để lại vết ai/khi nào/gọi gì/kết quả gì. Cần khi nhiều người dùng gọi
        đồng thời: đây là nguồn duy nhất phân xử "ai đã ra lệnh cho container nào". Fail-open —
        audit hỏng không được làm rớt request thật."""
        ledger = getattr(state.runner, "ledger", None)
        if ledger is None:
            return
        try:
            ledger.audit_once(f"{run_id}:audit", run_id, tool_name,
                              hashlib.sha256(key_material.encode()).hexdigest(), meta)
        except Exception:
            logger.exception("audit log failed run_id=%s tool=%s", run_id, tool_name)

    @app.post("/ai/verify")
    async def verify(body: VerifyRequest) -> dict[str, Any]:
        """Mode A — kiểm chứng PoC vào target qua secai-kali-executor (verify-only).
        Tắt mặc định: trả 404 khi VERIFY_ENABLED chưa bật (không lộ endpoint). Deterministic,
        không qua LLM. Xem poc/verify.py + docs/poc-verify-architecture.md."""
        settings = state.settings
        if not settings.verify_enabled:
            raise HTTPException(404, "verify disabled (đặt VERIFY_ENABLED=true để bật)")
        poc = getattr(state.runner, "poc", None)
        if poc is None:
            raise HTTPException(503, "PoC backend unavailable")
        run_id = f"verify:{uuid4().hex}"
        try:
            result = await verify_poc(poc, settings, target=body.target, run_id=run_id,
                                      cve=body.cve, ref=body.ref, source=body.source, rport=body.rport)
        except PocForbidden as exc:
            raise HTTPException(403, str(exc)) from exc
        except PocBadRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        except PocNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except PocError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:  # docker/runtime lỗi → 503, không phải 500 mờ
            logger.exception("verify failed target=%s", body.target)
            raise HTTPException(503, f"verify runtime error: {exc}") from exc
        _audit(run_id, "poc_verify_http", f"{body.cve or body.ref}:{body.target}",
              {"user_id": body.user_id, "target": body.target, "cve": body.cve, "ref": body.ref,
               "verdict": result.get("verdict"), "source": result.get("source")})
        return {**result, "run_id": run_id}

    @app.post("/ai/bughunt/stream")
    async def bughunt_stream(body: BughuntRequest) -> StreamingResponse:
        """BugHunter-style agent hunt via Linch ``run_workflow`` (AgentHunt → Validate).

        LLM ReAct tool loop inside AgentHunt (``run_recon``, ``run_vuln_scan``, …);
        Validate is a deterministic lead gate. User supplies ``target`` (not CVE).
        """
        target = (body.target or "").strip()
        if not target:
            raise HTTPException(422, "target is required")
        # Soft authz preview: when verify allowlist is active, reject early (same policy
        # scanners will use in Phase 1+). VERIFY_ALLOW_ALL / empty allowlist → pass.
        from ..poc.verify import target_allowed
        settings = state.settings
        if not target_allowed(settings, target) and not getattr(settings, "verify_allow_all", False):
            # If allowlist empty and allow_all false, target_allowed is False — for Phase 0
            # skeleton we only enforce when an explicit allowlist is configured.
            allow = getattr(settings, "verify_targets", None) or []
            if allow:
                raise HTTPException(403, f"Target '{target}' ngoài allowlist (VERIFY_ALLOWED_TARGETS)")

        scope_note = (body.scope_note or "").strip()
        run_id = (body.run_id or "").strip() or f"bh:{uuid4().hex}"

        async def gen() -> AsyncIterator[bytes]:
            result: dict[str, Any] | None = None
            failed = False
            try:
                stream_fn = getattr(state.runner, "stream_bughunt", None)
                if stream_fn is None:
                    raise RuntimeError("runner does not support stream_bughunt")
                async for event in stream_fn(
                    target=target, scope_note=scope_note, run_id=run_id,
                ):
                    if isinstance(event, dict) and event.get("type") == "bughunt_result":
                        result = event.get("result") if isinstance(event.get("result"), dict) else event
                        continue
                    if isinstance(event, dict) and event.get("type") == "log":
                        yield sse_frame("log", {
                            "stream": event.get("stream") or "stdout",
                            "line": event.get("line") or "",
                            "run_id": event.get("run_id") or run_id,
                        })
                        continue
                    frame = event_to_sse(event)
                    if frame:
                        yield frame
                        # Let the event loop flush to the socket between heavy steps.
                        await asyncio.sleep(0)
            except Exception:
                failed = True
                logger.exception("bughunt stream failed target=%s", target)
                yield sse_frame("error", {"error": STREAM_ERROR_MESSAGE})
                yield sse_frame("final", {
                    "ok": False,
                    "error": STREAM_ERROR_MESSAGE,
                    "metadata": {"run_id": run_id, "profile": "bughunt"},
                })
            if not failed:
                payload = result or {"status": "empty", "target": target, "phases": []}
                msg = payload.get("message") if isinstance(payload, dict) else None
                yield sse_frame("final", {
                    "ok": True,
                    "content": msg or json.dumps(payload, ensure_ascii=False),
                    "result": payload,
                    "metadata": {
                        "run_id": run_id,
                        "profile": "bughunt",
                        "phases": (payload.get("phases") if isinstance(payload, dict) else None),
                    },
                })
            yield b"data: [DONE]\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/ai/exploit/run")
    async def exploit_run(body: ExploitRunRequest) -> dict[str, Any]:
        """Deterministic PoC run qua phân hệ 5568 — không LLM. Tắt → 404.
        Trả scan.output (log pocsuite) + exploit_verdict (parse từ bảng, không tin scan.success)."""
        settings = state.settings
        if not settings.poc_exploit_enabled or not (settings.poc_exploit_api_url or "").strip():
            raise HTTPException(404, "poc exploit disabled (POC_EXPLOIT_ENABLED + POC_EXPLOIT_API_URL)")
        run_id = f"kt:{uuid4().hex}"
        try:
            result = await run_kt_exploit(
                settings, ip=body.target, cve=body.cve, port=body.port,
                command=body.command or "id", use_proxy=body.use_proxy, run_id=run_id,
            )
        except PocForbidden as exc:
            raise HTTPException(403, str(exc)) from exc
        except PocBadRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        except PocNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (PocError, KtClientError) as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:
            logger.exception("exploit/run failed target=%s cve=%s", body.target, body.cve)
            raise HTTPException(503, f"exploit runtime error: {exc}") from exc
        _audit(run_id, "poc_kt_exploit_http", f"{body.cve}:{body.target}",
              {"user_id": body.user_id, "target": body.target, "cve": body.cve,
               "port": body.port, "exploit_verdict": result.get("exploit_verdict"),
               "ok": result.get("ok")})
        return result

    @app.post("/ai/tool")
    async def tool(body: ToolRequest) -> dict[str, Any]:
        """Scoped pentest tool (nmap/searchsploit/gobuster/sqlmap/hydra) chạy trong Kali executor.
        Tắt mặc định (404 khi VERIFY_ENABLED chưa bật). argv do gateway dựng từ tham số đã validate."""
        settings = state.settings
        if not settings.verify_enabled:
            raise HTTPException(404, "tool disabled (đặt VERIFY_ENABLED=true để bật)")
        run_id = f"tool:{uuid4().hex}"
        params = body.model_dump(exclude_none=True, exclude={"user_id"})
        try:
            result = await run_tool(settings, body.tool, params, run_id=run_id)
        except PocForbidden as exc:
            raise HTTPException(403, str(exc)) from exc
        except PocBadRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        except PocError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            logger.exception("tool failed tool=%s", body.tool)
            raise HTTPException(503, f"tool runtime error: {exc}") from exc
        _audit(run_id, "poc_scan_http", f"{body.tool}:{body.target or body.query}",
              {"user_id": body.user_id, "tool": body.tool, "target": body.target,
               "rc": result.get("rc")})
        return {**result, "run_id": run_id}

    @app.post("/ai/exec")
    async def exec_raw(body: ExecRequest,
                       x_api_key: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        """RAW exec (RCE có chủ đích): chạy lệnh BẤT KỲ trong Kali với --privileged + root.
        Tắt mặc định (404). LUÔN yêu cầu admin API key để không thành RCE mở toang cho mọi caller."""
        settings = state.settings
        if not settings.exec_enabled:
            raise HTTPException(404, "exec disabled (đặt EXEC_ENABLED=true để bật)")
        if not settings.admin_api_key or x_api_key != settings.admin_api_key:
            raise HTTPException(401, "exec cần header X-API-Key == ADMIN_API_KEY")
        run_id = f"exec:{uuid4().hex}"
        try:
            result = await run_raw_command(settings, argv=body.cmd, shell=body.shell,
                                           timeout_s=body.timeout_s, privileged=body.privileged, run_id=run_id)
        except PocForbidden as exc:
            raise HTTPException(403, str(exc)) from exc
        except PocBadRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            logger.exception("exec failed")
            raise HTTPException(503, f"exec runtime error: {exc}") from exc
        # Luôn audit đầy đủ (kể cả không có ledger.evidence-style dedup) — /ai/exec là bề mặt
        # nguy hiểm nhất, audit là cơ chế trách nhiệm giải trình duy nhất khi có sự cố.
        _audit(run_id, "poc_exec_http", body.shell or json.dumps(body.cmd or []),
              {"user_id": body.user_id, "cmd": body.cmd, "shell": body.shell, "rc": result.get("rc")})
        return {**result, "run_id": run_id}

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
