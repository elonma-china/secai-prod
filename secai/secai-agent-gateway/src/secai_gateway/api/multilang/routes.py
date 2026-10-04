from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable

import httpx
import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError
from starlette.middleware.cors import CORSMiddleware

from secai_gateway.config import Settings as LegacySettings
from secai_gateway.multilang.config import (MultilangSettings, incumbent_ok,
                                            verified_counter, verified_specialist_counter)
from secai_gateway.multilang.errors import MESSAGES, STATUS, TranslationError
from secai_gateway.multilang.provider import HttpLlm, LlmPort, SpecialistLlm
from secai_gateway.multilang.registry import capabilities, resolve_pair
from secai_gateway.multilang.service import TranslationRequest, translate


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: StrictStr
    source_lang: StrictStr
    target_lang: StrictStr


class Admission:
    def __init__(self) -> None:
        self.active = 0
        self.translate_hits: dict[str, deque[float]] = defaultdict(deque)
        self.capability_hits: dict[str, deque[float]] = defaultdict(deque)

    def enter(self, peer: str, *, cap: int, quota: int, capability: bool = False) -> bool:
        now = time.monotonic()
        hits = (self.capability_hits if capability else self.translate_hits)[peer]
        while hits and hits[0] <= now - 60:
            hits.popleft()
        if len(hits) >= (max(60, quota) if capability else quota):
            return False
        if not capability and self.active >= cap:
            return False
        hits.append(now)
        if not capability:
            self.active += 1
        return True

    def leave(self) -> None:
        self.active -= 1


def _headers(request_id: str, retry: bool = False) -> dict[str, str]:
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
               "X-Request-ID": request_id}
    if retry:
        headers["Retry-After"] = "60"
    return headers


def _error(code: str, request_id: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": MESSAGES[code],
                                   "request_id": request_id}}, status_code=STATUS[code],
                        headers=_headers(request_id, code == "RATE_LIMITED"))


async def _disconnect(request: Request) -> None:
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


async def _read_json(request: Request, max_bytes: int) -> Input:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise TranslationError("INVALID_REQUEST")
    if request.headers.get("content-length"):
        try:
            if int(request.headers["content-length"]) > max_bytes:
                raise TranslationError("TEXT_TOO_LONG")
        except ValueError:
            raise TranslationError("INVALID_REQUEST") from None
    body = bytearray()
    async for part in request.stream():
        if len(body) + len(part) > max_bytes:
            raise TranslationError("TEXT_TOO_LONG")
        body.extend(part)
    try:
        data = json.loads(body)
        parsed = Input.model_validate(data)
    except (ValueError, UnicodeDecodeError, ValidationError):
        raise TranslationError("INVALID_REQUEST") from None
    if not parsed.text.strip() or not parsed.source_lang.strip() or not parsed.target_lang.strip():
        raise TranslationError("INVALID_REQUEST")
    if len(parsed.source_lang) > 16 or len(parsed.target_lang) > 16:
        raise TranslationError("INVALID_REQUEST")
    return parsed


def create_v3_app(*, settings_factory: Callable[[], MultilangSettings], legacy: LegacySettings,
                  llm: LlmPort | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url="/ai/v3/translate/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=legacy.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"],
                       expose_headers=["X-Request-ID", "Retry-After"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Request-ID", uuid.uuid4().hex)
        return response
    admission = Admission()
    app.state.llm = llm
    app.state.http_client = None
    app.state.specialist_client = None
    model_semaphore = asyncio.Semaphore(settings_factory().max_concurrent_model_calls)

    @app.get("/ai/v3/translate/languages")
    async def languages(request: Request):
        request_id = uuid.uuid4().hex
        settings = await asyncio.to_thread(settings_factory)
        peer = request.client.host if request.client else "unknown"
        if not admission.enter(peer, cap=settings.max_active_requests,
                               quota=settings.rate_limit_per_minute, capability=True):
            return _error("RATE_LIMITED", request_id)
        return JSONResponse(capabilities(settings), headers=_headers(request_id))

    @app.post("/ai/v3/translate")
    async def post_translate(request: Request):
        started = asyncio.get_running_loop().time()
        request_id = uuid.uuid4().hex
        settings = await asyncio.to_thread(settings_factory)
        peer = request.client.host if request.client else "unknown"
        if not admission.enter(peer, cap=settings.max_active_requests,
                               quota=settings.rate_limit_per_minute):
            return _error("RATE_LIMITED", request_id)
        try:
            with anyio.fail_after(max(0.0, started + settings.request_timeout_seconds
                                      - asyncio.get_running_loop().time())):
                parsed = await _read_json(request, settings.max_body_bytes)
                resolve_pair(parsed.source_lang, parsed.target_lang, settings)
                # Identity check is server-side and cannot be influenced by JSON or headers.
                if settings.backend == "specialist":
                    counter = await asyncio.to_thread(verified_specialist_counter, settings)
                    if counter is None:
                        raise TranslationError("MODEL_UNAVAILABLE")
                    active_llm = app.state.llm
                    if active_llm is None:
                        # Specialist owns a no-proxy client so environment proxy variables
                        # cannot redirect private translation data outside the host.
                        active_llm = SpecialistLlm(app.state.specialist_client, settings)
                else:
                    if not incumbent_ok(legacy):
                        raise TranslationError("MODEL_UNAVAILABLE")
                    counter = await asyncio.to_thread(verified_counter, settings)
                    active_llm = app.state.llm
                    if active_llm is None:
                        client: httpx.AsyncClient | None = app.state.http_client
                        if client is None:
                            raise TranslationError("MODEL_UNAVAILABLE")
                        active_llm = HttpLlm(client, legacy.qwen_base_url, legacy.qwen_api_key)
                task = asyncio.create_task(translate(
                    TranslationRequest(parsed.text, parsed.source_lang, parsed.target_lang),
                    llm=active_llm, settings=settings, token_count=counter,
                    model_semaphore=model_semaphore))
                watcher = asyncio.create_task(_disconnect(request))
                try:
                    done, _ = await asyncio.wait((task, watcher), return_when=asyncio.FIRST_COMPLETED)
                    if watcher in done:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        raise asyncio.CancelledError
                    result = await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                return JSONResponse({"translated_text": result.translated_text,
                                     "source_lang": result.source_lang,
                                     "target_lang": result.target_lang,
                                     "warnings": list(result.warnings),
                                     "metadata": {"request_id": request_id, "chunks": result.chunks,
                                                  "attempts": result.attempts}},
                                    headers=_headers(request_id))
        except TranslationError as exc:
            return _error(exc.code, request_id)
        except TimeoutError:
            return _error("TRANSLATION_TIMEOUT", request_id)
        finally:
            admission.leave()

    return app
