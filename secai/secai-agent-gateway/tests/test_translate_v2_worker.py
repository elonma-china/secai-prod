import asyncio

import pytest

from secai_gateway.translation.jobs import JobStore
from secai_gateway.translation.settings import TranslationSettings


class EchoAgent:
    calls = []
    def __init__(self, settings):
        pass
    async def translate(self, text, source_lang, target_lang, *, timeout_seconds=None):
        self.calls.append((text, source_lang, target_lang))
        return text.replace('Hello', 'Xin chào')


@pytest.mark.asyncio
async def test_worker_publishes_file_result_without_duyta(tmp_path, monkeypatch):
    from secai_gateway.translation import worker
    monkeypatch.setattr(worker, 'TranslationAgent', EchoAgent)
    EchoAgent.calls = []
    settings = TranslationSettings(db_path=str(tmp_path / 'jobs.db'),
                                   artifact_dir=str(tmp_path / 'artifacts'), _env_file=None)
    store = JobStore(settings)
    admitted = store.admit('file', b'Hello CVE-2024-12345', '.txt', 'en', 'vi', '127.0.0.1', None)
    assert await worker.Worker(store, settings, None).run_once()
    row = store.get(admitted['job_id'])
    assert row['status'] == 'succeeded'
    assert 'Xin chào CVE-2024-12345' in row['result_json']
    assert len(EchoAgent.calls) == 1
    assert 'CVE-2024-12345' not in EchoAgent.calls[0][0]


@pytest.mark.asyncio
async def test_running_cancel_aborts_specialist_and_becomes_terminal(tmp_path, monkeypatch):
    from secai_gateway.translation import worker
    class SlowAgent:
        started = asyncio.Event()
        cancelled = False
        def __init__(self, settings):
            pass
        async def translate(self, text, source_lang, target_lang, *, timeout_seconds=None):
            self.started.set()
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                type(self).cancelled = True
                raise
    monkeypatch.setattr(worker, 'TranslationAgent', SlowAgent)
    settings = TranslationSettings(db_path=str(tmp_path / 'jobs.db'),
                                   artifact_dir=str(tmp_path / 'artifacts'), _env_file=None)
    store = JobStore(settings)
    job = store.admit('file', b'Hello', '.txt', 'en', 'vi', '127.0.0.1', None)
    work = asyncio.create_task(worker.Worker(store, settings, None).run_once())
    await asyncio.wait_for(SlowAgent.started.wait(), 2)
    store.cancel(job['job_id'])
    await asyncio.wait_for(work, 3)
    assert SlowAgent.cancelled
    assert store.get(job['job_id'])['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_auto_non_vi_cross_pair_fails_after_admission(tmp_path, monkeypatch):
    from secai_gateway.translation import worker
    monkeypatch.setattr(worker, 'TranslationAgent', EchoAgent)
    settings = TranslationSettings(db_path=str(tmp_path / 'jobs.db'),
                                   artifact_dir=str(tmp_path / 'artifacts'), _env_file=None)
    store = JobStore(settings)
    job = store.admit('file', b'The system found a critical vulnerability.',
                      '.txt', 'auto', 'fr', '127.0.0.1', None)
    assert await worker.Worker(store, settings, None).run_once()
    row = store.get(job['job_id'])
    assert row['status'] == 'failed'
    assert row['error_code'] == 'UNSUPPORTED_LANGUAGE_PAIR'
