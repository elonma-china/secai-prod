import asyncio
import time
from types import SimpleNamespace

import pytest
from linch.errors import ProviderError

from secai_gateway.translation.jobs import JobStore
from secai_gateway.translation.settings import TranslationSettings


class EchoRunner:
    def __init__(self):
        self.calls = []

    async def complete(self, profile, prompt, session_id=None):
        self.calls.append((profile, session_id))
        source = prompt.rsplit("<source>\n", 1)[1].split("\n</source>", 1)[0]
        source = source.split("\n", 1)[1].rsplit("\n</untrusted_retrieved_content>", 1)[0]
        return source.replace("Hello", "Xin chào"), {}


@pytest.mark.asyncio
async def test_worker_publishes_file_result_without_session_id(tmp_path):
    from secai_gateway.translation.worker import Worker

    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"), _env_file=None)
    store = JobStore(settings)
    runner = EchoRunner()
    admitted = store.admit("file", b"Hello CVE-2024-12345", ".txt", "en", "vi",
                           "127.0.0.1", None)
    assert await Worker(store, settings, runner).run_once()
    row = store.get(admitted["job_id"])
    assert row["status"] == "succeeded"
    assert "Xin chào CVE-2024-12345" in row["result_json"]
    assert runner.calls == [("translate_v2", None)]


@pytest.mark.asyncio
async def test_running_cancel_aborts_llm_and_becomes_terminal(tmp_path):
    from secai_gateway.translation.worker import Worker

    class SlowRunner:
        def __init__(self):
            self.started = asyncio.Event()
            self.cancelled = False

        async def complete(self, profile, prompt, session_id=None):
            self.started.set()
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"), _env_file=None)
    store = JobStore(settings)
    runner = SlowRunner()
    job = store.admit("file", b"Hello", ".txt", "en", "vi", "127.0.0.1", None)
    work = asyncio.create_task(Worker(store, settings, runner).run_once())
    await asyncio.wait_for(runner.started.wait(), 2)
    store.cancel(job["job_id"])
    await asyncio.wait_for(work, 3)
    assert runner.cancelled
    assert store.get(job["job_id"])["status"] == "cancelled"


@pytest.mark.asyncio
async def test_auto_non_vi_cross_pair_fails_after_admission(tmp_path):
    from secai_gateway.translation.worker import Worker

    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"), _env_file=None)
    store = JobStore(settings)
    job = store.admit("file", b"The system found a critical vulnerability.",
                      ".txt", "auto", "fr", "127.0.0.1", None)
    assert await Worker(store, settings, EchoRunner()).run_once()
    row = store.get(job["job_id"])
    assert row["status"] == "failed"
    assert row["error_code"] == "UNSUPPORTED_LANGUAGE_PAIR"


@pytest.mark.asyncio
async def test_llm_retries_stop_at_job_deadline(tmp_path):
    from secai_gateway.translation.worker import WorkerContext
    from secai_gateway.translation.types import TranslationError

    class SlowRunner:
        async def complete(self, profile, prompt, session_id=None):
            await asyncio.sleep(1)
            return "late", {}

    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"),
                                   llm_timeout_seconds=0.12, llm_max_retries=3, _env_file=None)
    store = JobStore(settings)
    job = store.admit("file", b"Hello", ".txt", "en", "vi", "127.0.0.1", None)
    row = store.claim()
    row["deadline"] = time.time() + 0.19
    context = WorkerContext(store, settings, SlowRunner(), row, asyncio.Semaphore(1))
    start = time.monotonic()
    with pytest.raises(TranslationError) as error:
        await context.complete("translate_v2", "Hello")
    assert error.value.code == "PROCESSING_TIMEOUT"
    assert time.monotonic() - start < 0.32


@pytest.mark.asyncio
async def test_running_cancel_aborts_ocr_before_remote_completion(tmp_path, monkeypatch):
    from secai_gateway.translation.ocr_client import ImageOcrPipeline
    from secai_gateway.translation.worker import Worker

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_ocr(self, input_path, *, work_dir, context):
        started.set()
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(ImageOcrPipeline, "process", slow_ocr)
    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"), _env_file=None)
    store = JobStore(settings)
    job = store.admit("image", b"image", ".png", "en", "vi", "127.0.0.1", None)
    work = asyncio.create_task(Worker(store, settings, EchoRunner()).run_once())
    await asyncio.wait_for(started.wait(), 2)
    store.cancel(job["job_id"])
    await asyncio.sleep(1.1)
    stopped = work.done()
    if not stopped:
        work.cancel()
    await asyncio.gather(work, return_exceptions=True)
    assert stopped, "cancelled OCR should stop without an external task timeout"
    assert cancelled.is_set()
    assert store.get(job["job_id"])["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("first_error,expected_calls,expected_status", [
    (ProviderError("disconnected", retryable=True), 2, "succeeded"),
    (ProviderError("unauthorized", status=401, retryable=False), 1, "failed"),
])
async def test_worker_provider_error_retry_contract(tmp_path, first_error, expected_calls,
                                                    expected_status):
    from secai_gateway.translation.worker import Worker

    class RecoveringRunner(EchoRunner):
        async def complete(self, profile, prompt, session_id=None):
            if not self.calls:
                self.calls.append((profile, session_id))
                raise first_error
            return await super().complete(profile, prompt, session_id)

    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"),
                                   llm_max_retries=1, _env_file=None)
    store = JobStore(settings)
    job = store.admit("file", b"Hello world", ".txt", "en", "vi", "127.0.0.1", None)
    runner = RecoveringRunner()
    assert await Worker(store, settings, runner).run_once()
    row = store.get(job["job_id"])
    assert len(runner.calls) == expected_calls
    assert row["status"] == expected_status
    if expected_status == "failed":
        assert row["error_code"] == "TRANSLATION_UNAVAILABLE"


@pytest.mark.asyncio
async def test_worker_exhausted_retryable_provider_error_has_safe_code(tmp_path):
    from secai_gateway.translation.worker import Worker

    class DisconnectedRunner:
        def __init__(self):
            self.calls = 0

        async def complete(self, profile, prompt, session_id=None):
            self.calls += 1
            raise ProviderError("private service address", retryable=True)

    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"),
                                   llm_max_retries=1, _env_file=None)
    store = JobStore(settings)
    job = store.admit("file", b"Hello world", ".txt", "en", "vi", "127.0.0.1", None)
    runner = DisconnectedRunner()
    assert await Worker(store, settings, runner).run_once()
    row = store.get(job["job_id"])
    assert runner.calls == 2
    assert row["status"] == "failed"
    assert row["error_code"] == "TRANSLATION_UNAVAILABLE"


@pytest.mark.asyncio
async def test_worker_provider_disables_sdk_retries_on_actual_wire():
    import httpx2
    from openai import AsyncOpenAI, InternalServerError
    from linch.providers.vllm import VLLMProvider, VLLMProviderOptions
    from secai_gateway.translation.worker import _disable_provider_retries

    calls = 0

    def unavailable(request):
        nonlocal calls
        calls += 1
        return httpx2.Response(503, json={"error": {"message": "unavailable"}})

    provider = VLLMProvider(VLLMProviderOptions(api_key="test", base_url="http://127.0.0.1:18083/v1"))
    provider._client = AsyncOpenAI(api_key="test", base_url="http://127.0.0.1:18083/v1",
                                   http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(unavailable)))
    await _disable_provider_retries(SimpleNamespace(agent=SimpleNamespace(provider=provider)))
    assert provider._client.max_retries == 0
    with pytest.raises(InternalServerError):
        await provider._client.chat.completions.create(model="test", messages=[
            {"role": "user", "content": "Hello"}])
    assert calls == 1
    await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected_calls", [("503", 2), ("401", 1), ("disconnect", 2)])
async def test_actual_linch_worker_wire_retry_budget(tmp_path, failure, expected_calls):
    import httpx2
    from openai import AsyncOpenAI
    from secai_gateway.agent.runner import LinchRunner
    from secai_gateway.translation.worker import Worker, _disable_provider_retries

    calls = 0

    def unavailable(request):
        nonlocal calls
        calls += 1
        if failure == "disconnect":
            raise httpx2.ConnectError("disconnected", request=request)
        return httpx2.Response(int(failure), json={"error": {"message": "unavailable"}})

    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"),
                                   llm_max_retries=1, _env_file=None)
    store = JobStore(settings)
    admitted = store.admit("file", b"Hello world", ".txt", "en", "vi", "127.0.0.1", None)
    runner = LinchRunner(tmp_path / "runner.db", strict_transform_errors=True)
    provider = runner.agent.provider
    provider._client = AsyncOpenAI(api_key="test", base_url="http://127.0.0.1:18083/v1",
                                   http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(unavailable)))
    await _disable_provider_retries(runner)
    assert await Worker(store, settings, runner).run_once()
    row = store.get(admitted["job_id"])
    assert calls == expected_calls
    assert row["status"] == "failed"
    assert row["error_code"] == "TRANSLATION_UNAVAILABLE"
    await provider.aclose()


@pytest.mark.asyncio
async def test_ocr_trickle_fails_job_with_bounded_error(tmp_path, monkeypatch):
    import io
    import httpx
    from PIL import Image
    from secai_gateway.translation.worker import Worker

    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.025)
                yield b" "

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Trickle()))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    data = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(data, format="PNG")
    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"),
                                   ocr_enabled=True, ocr_base_url="http://127.0.0.1:18083",
                                   ocr_api_key="secret", ocr_model="test",
                                   ocr_timeout_seconds=0.05, ocr_max_retries=0, _env_file=None)
    store = JobStore(settings)
    admitted = store.admit("image", data.getvalue(), ".png", "en", "vi", "127.0.0.1", None)
    start = time.monotonic()
    assert await Worker(store, settings, EchoRunner()).run_once()
    assert time.monotonic() - start < 0.3
    row = store.get(admitted["job_id"])
    assert row["status"] == "failed"
    assert row["error_code"] == "OCR_UNAVAILABLE"


@pytest.mark.asyncio
async def test_actual_linch_worker_recovers_after_one_503(tmp_path):
    import httpx2
    from openai import AsyncOpenAI
    from secai_gateway.agent.runner import LinchRunner
    from secai_gateway.translation.worker import Worker, _disable_provider_retries

    calls = 0
    stream = (
        'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"test",'
        '"choices":[{"index":0,"delta":{"content":"Xin chào"},"finish_reason":null}]}\n\n'
        'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"test",'
        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        'data: [DONE]\n\n'
    )

    def respond(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(503, json={"error": {"message": "unavailable"}})
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=stream)

    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"),
                                   llm_max_retries=1, _env_file=None)
    store = JobStore(settings)
    admitted = store.admit("file", b"Hello", ".txt", "en", "vi", "127.0.0.1", None)
    runner = LinchRunner(tmp_path / "runner.db", strict_transform_errors=True)
    provider = runner.agent.provider
    provider._client = AsyncOpenAI(api_key="test", base_url="http://127.0.0.1:18083/v1",
                                   http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)))
    await _disable_provider_retries(runner)
    assert await Worker(store, settings, runner).run_once()
    row = store.get(admitted["job_id"])
    assert calls == 2
    assert row["status"] == "succeeded"
    assert '"translated_text": "Xin chào"' in row["result_json"]
    await provider.aclose()
