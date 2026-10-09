import pytest

from secai_gateway.translation.jobs import JobStore
from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.types import TranslationError


def test_five_file_batch_keeps_queue_and_worker_limits(tmp_path):
    settings = TranslationSettings(
        _env_file=None, db_path=str(tmp_path / "jobs.db"),
        artifact_dir=str(tmp_path / "artifacts"),
        max_active_per_ip=5, max_queued_jobs=20, max_running_jobs=1,
    )
    store = JobStore(settings)
    jobs = [store.admit("file", str(i).encode(), ".txt", "en", "vi", "peer", f"{i:032x}")
            for i in range(5)]
    with pytest.raises(TranslationError) as error:
        store.admit("file", b"sixth", ".txt", "en", "vi", "peer", None)
    assert error.value.code == "QUEUE_FULL"
    assert store.admit("file", b"0", ".txt", "en", "vi", "peer", "0" * 32)["job_id"] == jobs[0]["job_id"]
    store.cancel(jobs[0]["job_id"])
    assert store.admit("file", b"sixth", ".txt", "en", "vi", "peer", None)["status"] == "queued"
    assert store.claim() is not None
    assert store.claim() is None
