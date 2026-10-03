from pathlib import Path

import pytest

from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.types import TranslationError


def _store(tmp_path):
    from secai_gateway.translation.jobs import JobStore

    settings = TranslationSettings(db_path=str(tmp_path / "jobs.db"),
                                   artifact_dir=str(tmp_path / "artifacts"), _env_file=None)
    return JobStore(settings)


def test_idempotency_atomic_conflict_and_replay(tmp_path):
    store = _store(tmp_path)
    key = "a" * 32
    first = store.admit("file", b"hello", ".txt", "auto", "vi", "127.0.0.1", key)
    replay = store.admit("file", b"hello", ".txt", "auto", "vi", "127.0.0.2", key)
    assert first["job_id"] == replay["job_id"]
    with pytest.raises(TranslationError) as e:
        store.admit("file", b"different", ".txt", "auto", "vi", "127.0.0.3", key)
    assert e.value.code == "IDEMPOTENCY_CONFLICT"


def test_claim_fence_and_cancel_prevents_publish(tmp_path):
    store = _store(tmp_path)
    job = store.admit("file", b"hello", ".txt", "auto", "vi", "127.0.0.1", None)
    claim = store.claim()
    assert claim["job_id"] == job["job_id"]
    assert claim["generation"] == 1
    cancelled = store.cancel(job["job_id"])
    assert cancelled["status"] in {"cancelling", "cancelled"}
    assert store.finish(job["job_id"], 1, result={"translated_text": "bad"}) is False
    assert store.get(job["job_id"])["status"] == "cancelled"


def test_lease_reclaim_invalidates_stale_worker(tmp_path):
    store = _store(tmp_path)
    job = store.admit("file", b"hello", ".txt", "auto", "vi", "127.0.0.1", None)
    first = store.claim(now=1000)
    assert first["job_id"] == job["job_id"]
    second = store.claim(now=1061)
    assert second["generation"] == 2
    assert store.finish(job["job_id"], 1, result={}) is False
    assert store.finish(job["job_id"], 2, result={"translated_text": "ok"}) is True


def test_idempotency_tombstone_and_reuse(tmp_path):
    store = _store(tmp_path)
    key = "b" * 32
    job = store.admit("file", b"one", ".txt", "en", "vi", "a", key, now=1000)
    claim = store.claim(now=1000)
    store.finish(job["job_id"], claim["generation"], result={"ok": True}, now=1001)
    expired = 1001 + store.settings.result_ttl_seconds
    with pytest.raises(TranslationError) as e:
        store.admit("file", b"other", ".txt", "en", "vi", "b", key, now=expired)
    assert e.value.code == "JOB_EXPIRED"
    reused = store.admit("file", b"other", ".txt", "en", "vi", "b", key,
                         now=expired + store.settings.tombstone_ttl_seconds)
    assert reused["job_id"] != job["job_id"]
