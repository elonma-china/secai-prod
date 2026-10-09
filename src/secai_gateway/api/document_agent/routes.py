"""Scoped HTTP facade over the accepted OCR job backend protocol."""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...document_agent.contracts import ImageAttachment, OcrBackend
from ...document_agent.runner import DocumentRunner
from ...document_agent.scope import (DocumentScopeStore, ExpiredContext, InvalidContext,
                                     TaskNotFound, TooManyContexts, TurnInProgress,
                                     TurnPayloadConflict)
from ...document_agent.tools import DocumentRunDeps
from ...state import FixedWindowRateLimiter
from .schemas import _bad, parse_turn

MAX_OCR_RESULT_BYTES = 10 * 1024 * 1024


def _sse(kind: str, data: dict) -> bytes:
    return ("event: " + kind + "\ndata: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode()


def build_router(scope_store: DocumentScopeStore, backend: OcrBackend, runner: DocumentRunner) -> APIRouter:
    router = APIRouter(prefix="/ai/v3/document-agent")
    limiter = FixedWindowRateLimiter()

    async def scope_for(request: Request):
        token = request.headers.get("X-Document-Context", "")
        try:
            return await asyncio.to_thread(scope_store.resolve, token, time.time())
        except ExpiredContext:
            raise _bad("CONTEXT_EXPIRED", 410)
        except InvalidContext:
            raise _bad("CONTEXT_REQUIRED", 401)

    async def task_for(request: Request, handle: str):
        scope = await scope_for(request)
        try:
            await asyncio.to_thread(scope_store.resolve_task, scope, handle)
        except ExpiredContext:
            raise _bad("CONTEXT_EXPIRED", 410)
        except TaskNotFound:
            raise _bad("TASK_NOT_FOUND", 404)
        return scope

    @router.post("/contexts", status_code=201)
    async def create_context(request: Request):
        peer = request.client.host if request.client else "unknown"
        try:
            limiter.check(peer, "document-context", 5)
            grant = await asyncio.to_thread(scope_store.create, peer, time.time())
        except (RuntimeError, TooManyContexts):
            raise _bad("CONTEXT_LIMIT", 429)
        return JSONResponse({"context_token": grant.context_token, "expires_at": grant.expires_at}, status_code=201)

    @router.post("/turns")
    async def turn(request: Request):
        scope = await scope_for(request)
        parsed = await parse_turn(request)
        key = request.headers.get("Idempotency-Key", "")
        peer = request.client.host if request.client else "unknown"
        try:
            start = await asyncio.to_thread(scope_store.begin_turn, scope, key, parsed.digest(), time.time())
        except ValueError as exc:
            if isinstance(exc, TurnPayloadConflict):
                raise _bad("TURN_PAYLOAD_CONFLICT", 409)
            if isinstance(exc, TurnInProgress):
                raise _bad("TURN_IN_PROGRESS", 409)
            raise _bad("IDEMPOTENCY_KEY_INVALID")
        if start.response is not None:
            saved = start.response
            if parsed.stream:
                async def replay():
                    kind = "error" if "code" in saved else "final"
                    yield _sse(kind, saved)
                return StreamingResponse(replay(), media_type="text/event-stream")
            return JSONResponse(saved, status_code=200 if "code" not in saved else 500)

        allowed = set()
        if parsed.task_handle:
            try:
                await asyncio.to_thread(scope_store.resolve_task, scope, parsed.task_handle)
                allowed.add(parsed.task_handle)
            except TaskNotFound:
                await asyncio.to_thread(scope_store.abort_turn, scope, key, start.reservation, time.time())
                raise _bad("TASK_NOT_FOUND", 404)
        attachment = ImageAttachment(secrets.token_hex(16), parsed.media_type, parsed.file_bytes) if parsed.file_bytes is not None else None
        # Stable across retries even when the ephemeral attachment_id changes. The
        # durable backend must deduplicate this key and verify image/OCR config identity.
        submission_key = hashlib.sha256(
            (scope.context_id + ":" + key + ":" + parsed.digest()).encode("ascii")
        ).hexdigest()
        deps = DocumentRunDeps(
            scope, {attachment.attachment_id: attachment} if attachment else {},
            backend, peer, submission_key=submission_key, allowed_task_handles=allowed,
        )
        # Opaque attachment_id is supplied only to this turn. The caller never supplies a path or URL.
        prompt = parsed.prompt + (f"\nAttachment ID: {attachment.attachment_id}" if attachment else "") + (f"\nTask handle: {parsed.task_handle}" if parsed.task_handle else "")
        request_id = secrets.token_hex(16)

        async def events() -> AsyncIterator[dict]:
            try:
                async for item in runner.stream(prompt, deps):
                    if item["type"] == "final":
                        safe = {"answer": item["answer"], "tasks": item["tasks"], "request_id": request_id}
                        await asyncio.to_thread(scope_store.complete_turn, scope, key, start.reservation, safe, time.time())
                        yield {"type": "final", **safe}
                    elif item["type"] == "error":
                        safe = {"code": "DOCUMENT_RUN_FAILED", "request_id": request_id}
                        await asyncio.to_thread(scope_store.complete_turn, scope, key, start.reservation, safe, time.time())
                        yield {"type": "error", **safe}
                    else:
                        yield item
            except asyncio.CancelledError:
                await asyncio.to_thread(scope_store.abort_turn, scope, key, start.reservation, time.time())
                raise
            except Exception:
                safe = {"code": "DOCUMENT_RUN_FAILED", "request_id": request_id}
                await asyncio.to_thread(scope_store.complete_turn, scope, key, start.reservation, safe, time.time())
                yield {"type": "error", **safe}

        if parsed.stream:
            async def stream_events():
                async for event in events():
                    kind = event.pop("type")
                    yield _sse(kind, event)
            return StreamingResponse(stream_events(), media_type="text/event-stream")
        last = None
        async for event in events():
            if event["type"] in {"final", "error"}:
                last = {k: v for k, v in event.items() if k != "type"}
        return JSONResponse(last or {"code": "DOCUMENT_RUN_FAILED"}, status_code=200 if last and "code" not in last else 500)

    @router.get("/tasks/{task_handle}")
    async def status(request: Request, task_handle: str):
        scope = await task_for(request, task_handle)
        return (await backend.status(scope, task_handle)).public()

    @router.get("/tasks/{task_handle}/result")
    async def result(request: Request, task_handle: str):
        scope = await task_for(request, task_handle)
        state = await backend.status(scope, task_handle)
        if state.status != "succeeded":
            raise _bad("RESULT_NOT_READY", 409)
        ocr = await backend.result(scope, task_handle)
        payload = ocr.bounded_public(MAX_OCR_RESULT_BYTES)
        if payload is None:
            raise _bad("RESULT_TOO_LARGE", 413)
        return JSONResponse(payload)

    @router.delete("/tasks/{task_handle}")
    async def cancel(request: Request, task_handle: str):
        scope = await task_for(request, task_handle)
        state = await backend.cancel(scope, task_handle)
        return JSONResponse(state.public(), status_code=202 if state.status == "cancelling" else 409)

    return router
