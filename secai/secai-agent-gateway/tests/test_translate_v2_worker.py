import asyncio

import pytest

from secai_gateway.translation.jobs import JobStore
from secai_gateway.translation.settings import TranslationSettings


class EchoRunner:
    def __init__(self):
        self.calls = []

    async def complete(self, profile, prompt, session_id=None):
        self.calls.append((profile, session_id))
        return prompt.rsplit("<source>\n", 1)[1].split("\n</source>", 1)[0].replace("Hello", "Xin chào"), {}


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
    assert runner.calls == [("translate", None)]


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
