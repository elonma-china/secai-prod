"""Integration checks for the specialist/file worker and optional OCR correction merge."""
import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from linch import InMemorySessionStore
from linch.events import ToolCallEndEvent

from secai_gateway.translation.jobs import JobStore
from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.types import TranslationError


@pytest.mark.asyncio
async def test_specialist_call_uses_bounded_timeout_and_cancels_child(tmp_path, monkeypatch):
    from secai_gateway.translation import worker

    cancelled = asyncio.Event()

    class SlowAgent:
        def __init__(self, settings):
            pass

        async def translate(self, text, source_lang, target_lang, *, timeout_seconds=None):
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.set()
                raise

    monkeypatch.setattr(worker, "TranslationAgent", SlowAgent)
    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"),
                                   specialist_timeout_seconds=0.05, _env_file=None)
    store = JobStore(settings)
    store.admit("file", b"Hello", ".txt", "en", "vi", "127.0.0.1", None)
    context = worker.WorkerContext(store, settings, None, store.claim(), asyncio.Semaphore(1))
    start = time.monotonic()
    with pytest.raises(TranslationError) as error:
        await context.translate("Hello", "en", "vi")
    assert error.value.code == "PROCESSING_TIMEOUT"
    assert error.value.retryable is True
    assert cancelled.is_set()
    assert time.monotonic() - start < 0.3


@pytest.mark.asyncio
async def test_file_worker_retries_transient_specialist_once_without_linch(tmp_path, monkeypatch):
    from secai_gateway.translation import worker

    class FlakyAgent:
        calls = 0

        def __init__(self, settings):
            pass

        async def translate(self, text, source_lang, target_lang, *, timeout_seconds=None):
            type(self).calls += 1
            if self.calls == 1:
                raise TranslationError("TRANSLATION_UNAVAILABLE", "unavailable", retryable=True)
            return "Xin chào"

    monkeypatch.setattr(worker, "TranslationAgent", FlakyAgent)
    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"),
                                   ocr_correction_supported=False, _env_file=None)
    store = JobStore(settings)
    job = store.admit("file", b"Hello", ".txt", "en", "vi", "127.0.0.1", None)
    assert await worker.Worker(store, settings, None).run_once()
    assert FlakyAgent.calls == 2
    assert store.get(job["job_id"])["status"] == "succeeded"


@pytest.mark.asyncio
async def test_file_worker_specialist_http_retry_is_bounded(tmp_path, monkeypatch):
    from secai_gateway.translation import worker

    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"translated_text": "Xin chào",
                                         "model": "tencent/Hy-MT2-1.8B"})

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"),
                                   specialist_base_url="http://127.0.0.1:18404",
                                   specialist_api_key="test-secret", _env_file=None)
    store = JobStore(settings)
    job = store.admit("file", b"Hello", ".txt", "en", "vi", "127.0.0.1", None)
    assert await worker.Worker(store, settings, None).run_once()
    assert store.get(job["job_id"])["status"] == "succeeded"
    assert len(sent) == 2
    assert all(row["source_lang"] == "en" and row["target_lang"] == "vi" for row in sent)


@pytest.mark.asyncio
async def test_specialist_http_child_stops_on_job_cancel(tmp_path, monkeypatch):
    from secai_gateway.translation import worker

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def respond(request):
        started.set()
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"),
                                   specialist_base_url="http://127.0.0.1:18404",
                                   specialist_api_key="test-secret", _env_file=None)
    store = JobStore(settings)
    job = store.admit("file", b"Hello", ".txt", "en", "vi", "127.0.0.1", None)
    context = worker.WorkerContext(store, settings, None, store.claim(), asyncio.Semaphore(1))
    call = asyncio.create_task(context.translate("Hello", "en", "vi"))
    await asyncio.wait_for(started.wait(), 2)
    store.cancel(job["job_id"])
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(call, 2)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_worker_main_skips_linch_when_ocr_correction_is_off(tmp_path, monkeypatch):
    from secai_gateway.translation import worker
    from secai_gateway.agent import runner as runner_module

    settings = TranslationSettings(enabled=True, ocr_correction_supported=False,
                                   db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"), _env_file=None)
    monkeypatch.setenv("SESSION_STORE_BACKEND", "memory")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(worker, "TranslationSettings", lambda: settings)
    monkeypatch.setattr(runner_module, "LinchRunner", lambda *args, **kwargs: pytest.fail("LinchRunner constructed"))
    seen = []

    async def serve(self):
        seen.append(self.runner)

    monkeypatch.setattr(worker.Worker, "serve", serve)
    await worker.main()
    assert seen == [None]


@pytest.mark.asyncio
async def test_orchestrator_keeps_specialist_tool_result_with_strict_ocr_errors():
    from secai_gateway.agent.runner import LinchRunner

    class Session:
        active_run_id = "run-1"

        async def run(self, prompt, *args, **kwargs):
            yield ToolCallEndEvent(tool_use_id="tool-1", tool_name="translation_specialist",
                                   result="Xin chào", is_error=False)

    async def session(**kwargs):
        return Session()

    runner = object.__new__(LinchRunner)
    runner.strict_transform_errors = True
    runner.session_store = InMemorySessionStore()
    runner.agent = SimpleNamespace(session=session)
    runner._session_locks = {}
    runner._apply_profile = lambda *args, **kwargs: None
    result, metadata = await runner.complete("translation_orchestrator", "Hello")
    assert result == "Xin chào"
    assert "translation_specialist" in metadata["tool_registry"]
