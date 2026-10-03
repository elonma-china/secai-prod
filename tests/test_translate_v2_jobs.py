from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

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
    assert store.finish(job["job_id"], 1, result={}, now=1062) is False
    assert store.finish(job["job_id"], 2, result={"translated_text": "ok"}, now=1062) is True


def test_idempotency_tombstone_and_reuse(tmp_path):
    import sqlite3

    store = _store(tmp_path)
    key = "b" * 32
    job = store.admit("file", b"one", ".txt", "en", "vi", "a", key, now=1000)
    claim = store.claim(now=1000)
    store.finish(job["job_id"], claim["generation"], result={"ok": True}, now=1001)
    expired = 1001 + store.settings.result_ttl_seconds
    with pytest.raises(TranslationError) as e:
        store.admit("file", b"other", ".txt", "en", "vi", "b", key, now=expired)
    assert e.value.code == "JOB_EXPIRED"
    store.reap(now=expired)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE id=?", (job["job_id"],)).fetchone()[0] == 0
        assert db.execute("SELECT status FROM tombstones WHERE id=?",
                          (job["job_id"],)).fetchone()[0] == "succeeded"
    reused = store.admit("file", b"other", ".txt", "en", "vi", "b", key,
                         now=expired + store.settings.tombstone_ttl_seconds)
    assert reused["job_id"] != job["job_id"]


def test_expired_lease_cannot_publish_or_heartbeat(tmp_path):
    store = _store(tmp_path)
    job = store.admit("file", b"hello", ".txt", "en", "vi", "a", None, now=1000)
    claim = store.claim(now=1000)
    expired = 1000 + store.settings.lease_seconds + 1
    assert store.heartbeat(job["job_id"], claim["generation"], now=expired) is False
    assert store.finish(job["job_id"], claim["generation"], result={"x": 1}, now=expired) is False


def test_simultaneous_same_key_creates_one_job(tmp_path):
    store = _store(tmp_path)
    def submit(_):
        return store.admit("file", b"same", ".txt", "en", "vi", "peer", "a" * 32)
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = list(pool.map(submit, range(5)))
    assert len({job["job_id"] for job in jobs}) == 1


def test_rate_and_global_ip_limits_are_transactional(tmp_path):
    store = _store(tmp_path)
    for _ in range(store.settings.lookup_rate_per_minute):
        store.rate("peer", "lookup", store.settings.lookup_rate_per_minute, now=1000)
    with pytest.raises(TranslationError) as e:
        store.rate("peer", "lookup", store.settings.lookup_rate_per_minute, now=1000)
    assert e.value.code == "RATE_LIMITED"
    store.admit("file", b"a", ".txt", "en", "vi", "peer", None)
    store.admit("file", b"b", ".txt", "en", "vi", "peer", None)
    with pytest.raises(TranslationError) as e:
        store.admit("file", b"c", ".txt", "en", "vi", "peer", None)
    assert e.value.code == "QUEUE_FULL"


def test_private_storage_permissions(tmp_path):
    store = _store(tmp_path)
    assert store.db_path.parent.stat().st_mode & 0o077 == 0
    assert store.artifact_dir.stat().st_mode & 0o077 == 0
    assert store.db_path.stat().st_mode & 0o077 == 0
    job = store.admit("file", b"private", ".txt", "en", "vi", "peer", None)
    assert Path(job["input_path"]).stat().st_mode & 0o077 == 0
