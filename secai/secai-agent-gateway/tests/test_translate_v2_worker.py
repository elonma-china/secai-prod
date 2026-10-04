import asyncio
import time

import pytest

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
