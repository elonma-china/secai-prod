"""End-to-end batch behavior through the v2 ASGI routes and durable job store."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from docx import Document

from secai_gateway.app import create_app
from secai_gateway.translation.worker import Worker
from evals.multilang.multifile_batch import BatchEntry, BatchError, run_batch, cancel_batch


class NoModel:
    async def complete(self, *args, **kwargs):
        raise AssertionError("specialist calls are mocked")


@pytest.mark.asyncio
async def test_eighteen_directions_mixed_files_have_bounded_jobs_and_real_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("TRANSLATE_V2_UPLOAD_RATE_PER_MINUTE", "100")
    monkeypatch.setenv("TRANSLATE_V2_LOOKUP_RATE_PER_MINUTE", "1000")
    monkeypatch.setenv("TRANSLATE_V2_SPECIALIST_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("TRANSLATE_V2_SPECIALIST_API_KEY", "test-only")
    from secai_gateway.translation import specialist

    async def fake_translate(self, text, source_lang, target_lang, **kwargs):
        return text

    monkeypatch.setattr(specialist.TranslationAgent, "translate", fake_translate)
    app = create_app(NoModel())
    store = app.state.translation_v2_store
    worker = Worker(store, app.state.translation_v2_settings, None)
    languages = ["en", "zh", "ja", "ko", "fr", "de", "es", "ru", "ar"]
    entries = []
    for index, (source, target) in enumerate(
        [("vi", language) for language in languages] +
        [(language, "vi") for language in languages]
    ):
        path = tmp_path / (f"sample-{index}.docx" if index % 3 == 0 else f"sample-{index}.txt")
        if path.suffix == ".docx":
            doc = Document()
            doc.add_paragraph("A sample sentence")
            doc.save(path)
        else:
            path.write_text("A sample sentence", encoding="utf-8")
        entries.append(BatchEntry(path, source, target))

    active_high = 0
    stop = False

    async def consume():
        nonlocal active_high
        while not stop:
            with store._tx() as db:
                active = db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0]
            active_high = max(active_high, active)
            if not await worker.run_once():
                await asyncio.sleep(0.005)

    task = asyncio.create_task(consume())
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            result = await run_batch(client, entries, tmp_path / "manifest.json", tmp_path / "out",
                                     max_inflight=2, upload_interval=0, poll_interval=0.01,
                                     job_timeout=30)
        assert result == {"succeeded": 18, "failed": 0, "cancelled": 0,
                          "interrupted": 0, "pending": 0}
        assert active_high <= 2
        saved = json.loads((tmp_path / "manifest.json").read_text())
        assert len(saved["entries"]) == 18
        assert all(len(row["job_id"]) == 32 for row in saved["entries"])
        assert all((tmp_path / "out" / row["output_txt"]).read_text().strip()
                   for row in saved["entries"])
        assert all((tmp_path / "out" / row["output_json"]).exists()
                   for row in saved["entries"])
    finally:
        stop = True
        await task


@pytest.mark.asyncio
async def test_resume_replays_saved_key_and_failed_file_does_not_stop_other_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("TRANSLATE_V2_UPLOAD_RATE_PER_MINUTE", "100")
    app = create_app(NoModel())
    good = tmp_path / "good.txt"
    bad = tmp_path / "bad.txt"
    good.write_text("Hello", encoding="utf-8")
    bad.write_bytes(b"\x00\x01")
    entries = [BatchEntry(good, "en", "en"), BatchEntry(bad, "en", "en")]
    manifest = tmp_path / "manifest.json"
    # An interruption after durable admission leaves a key but no saved job ID.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        from evals.multilang.multifile_batch import create_manifest
        create_manifest(entries, manifest)
        saved = json.loads(manifest.read_text())
        accepted = await client.post("/ai/v2/translate/files", files={"file": (good.name, good.read_bytes())},
                                     data={"source_lang": "en", "target_lang": "en"},
                                     headers={"Idempotency-Key": saved["entries"][0]["key"]})
        assert accepted.status_code == 202
        worker = Worker(app.state.translation_v2_store, app.state.translation_v2_settings, None)
        async def consume():
            for _ in range(80):
                await worker.run_once()
                await asyncio.sleep(0.005)
        task = asyncio.create_task(consume())
        result = await run_batch(client, None, manifest, tmp_path / "out", max_inflight=2,
                                 upload_interval=0, poll_interval=0.01, job_timeout=10)
        await task
    assert result == {"succeeded": 1, "failed": 1, "cancelled": 0,
                      "interrupted": 0, "pending": 0}
    saved = json.loads(manifest.read_text())
    assert saved["entries"][0]["job_id"] == accepted.json()["job_id"]
    assert (tmp_path / "out" / saved["entries"][0]["output_txt"]).read_text() == "Hello"
    assert saved["entries"][1]["error_code"] == "UNSUPPORTED_ENCODING"
    assert len(list((tmp_path / "out").glob("*.txt"))) == 1


@pytest.mark.asyncio
async def test_cancel_manifest_cancels_admitted_job(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_app(NoModel())
    source = tmp_path / "x.txt"
    source.write_text("Hello")
    from evals.multilang.multifile_batch import create_manifest
    manifest = tmp_path / "manifest.json"
    create_manifest([BatchEntry(source, "en", "vi")], manifest)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        saved = json.loads(manifest.read_text())
        response = await client.post("/ai/v2/translate/files", files={"file": ("x.txt", b"Hello")},
                                     data={"source_lang": "en", "target_lang": "vi"},
                                     headers={"Idempotency-Key": saved["entries"][0]["key"]})
        saved["entries"][0]["job_id"] = response.json()["job_id"]
        manifest.write_text(json.dumps(saved))
        source.unlink()  # Cancellation must not depend on the original input still existing.
        await cancel_batch(client, manifest)
        assert (await client.get(response.json()["status_url"])).json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_resume_rejects_changed_bytes_and_language_binding(tmp_path):
    from evals.multilang.multifile_batch import create_manifest
    source = tmp_path / "x.txt"
    source.write_text("Hello")
    manifest = tmp_path / "manifest.json"
    create_manifest([BatchEntry(source, "en", "vi")], manifest)
    async with httpx.AsyncClient(base_url="http://test") as client:
        source.write_text("Changed")
        counts = await run_batch(client, None, manifest, tmp_path / "out")
        assert counts["failed"] == 1
        assert json.loads(manifest.read_text())["entries"][0]["job_id"] is None
        source.write_text("Hello")
        other = tmp_path / "other-manifest.json"
        saved = create_manifest([BatchEntry(source, "en", "vi")], other)
        saved["entries"][0]["target_lang"] = "zh"
        other.write_text(json.dumps(saved))
        counts = await run_batch(client, None, other, tmp_path / "out")
        assert counts["failed"] == 1
        assert json.loads(other.read_text())["entries"][0]["job_id"] is None


@pytest.mark.asyncio
async def test_retry_after_replays_complete_upload_bytes(tmp_path):
    from evals.multilang.multifile_batch import _request
    import time

    source = tmp_path / "x.txt"
    source.write_bytes(b"complete bytes")
    seen = []

    async def respond(request):
        seen.append(await request.aread())
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(202, json={"job_id": "a" * 32})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url="http://test") as client:
        with source.open("rb") as stream:
            response = await _request(client, "POST", "/ai/v2/translate/files", time.monotonic() + 5,
                                      files={"file": (source.name, stream)},
                                      headers={"Idempotency-Key": "b" * 32})
    assert response.status_code == 202
    assert len(seen) == 2 and all(b"complete bytes" in body for body in seen)


@pytest.mark.asyncio
async def test_accepted_post_with_lost_response_resumes_same_job(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("TRANSLATE_V2_UPLOAD_RATE_PER_MINUTE", "100")
    app = create_app(NoModel())
    source = tmp_path / "x.txt"
    source.write_text("Hello")
    manifest = tmp_path / "manifest.json"

    class LosePostResponse(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.ASGITransport(app)

        async def handle_async_request(self, request):
            response = await self.inner.handle_async_request(request)
            if request.method == "POST":
                await response.aclose()
                raise httpx.ReadError("response lost")
            return response

        async def aclose(self):
            await self.inner.aclose()

    async with httpx.AsyncClient(transport=LosePostResponse(), base_url="http://test") as client:
        result = await run_batch(client, [BatchEntry(source, "en", "en")], manifest,
                                 tmp_path / "out", upload_interval=0, poll_interval=0.01,
                                 job_timeout=15)
    assert result["interrupted"] == 1 and result["succeeded"] == 0
    saved = json.loads(manifest.read_text())
    assert saved["entries"][0]["job_id"] is None
    with app.state.translation_v2_store._tx() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        original_job_id = db.execute("SELECT id FROM jobs").fetchone()[0]

    worker = Worker(app.state.translation_v2_store, app.state.translation_v2_settings, None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        task = asyncio.create_task(worker.run_once())
        result = await run_batch(client, None, manifest, tmp_path / "out",
                                 upload_interval=0, poll_interval=0.01, job_timeout=10)
        await task
    assert result["succeeded"] == 1 and result["interrupted"] == 0
    assert json.loads(manifest.read_text())["entries"][0]["job_id"] == original_job_id
    with app.state.translation_v2_store._tx() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_manifest_rejects_concurrent_resume_or_cancel(tmp_path):
    from evals.multilang.multifile_batch import create_manifest, _manifest_lock
    source = tmp_path / "x.txt"
    source.write_text("Hello")
    manifest = tmp_path / "manifest.json"
    create_manifest([BatchEntry(source, "en", "vi")], manifest)
    async with httpx.AsyncClient(base_url="http://test") as client:
        with _manifest_lock(manifest):
            with pytest.raises(BatchError, match="manifest is in use"):
                await run_batch(client, None, manifest, tmp_path / "out")
            with pytest.raises(BatchError, match="manifest is in use"):
                await cancel_batch(client, manifest)


@pytest.mark.asyncio
async def test_cancel_recovers_unknown_accepted_post_and_skips_unsubmitted(tmp_path, monkeypatch):
    from evals.multilang.multifile_batch import create_manifest
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_app(NoModel())
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("Hello")
    second.write_text("World")
    manifest = tmp_path / "manifest.json"
    saved = create_manifest([BatchEntry(first, "en", "vi"),
                             BatchEntry(second, "en", "vi")], manifest)
    saved["entries"][0]["status"] = "uploading"
    manifest.write_text(json.dumps(saved))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        admitted = await client.post("/ai/v2/translate/files",
                                     files={"file": (first.name, first.read_bytes())},
                                     data={"source_lang": "en", "target_lang": "vi"},
                                     headers={"Idempotency-Key": saved["entries"][0]["key"]})
        assert admitted.status_code == 202
        result = await cancel_batch(client, manifest)
        assert result == {"cancel_requested": 1, "not_submitted": 1, "unresolved": 0}
        assert (await client.get(admitted.json()["status_url"])).json()["status"] == "cancelled"
    saved = json.loads(manifest.read_text())
    assert saved["entries"][0]["job_id"] == admitted.json()["job_id"]
    assert [row["status"] for row in saved["entries"]] == ["cancelled", "cancelled"]
    with app.state.translation_v2_store._tx() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_request_has_whole_response_deadline():
    from evals.multilang.multifile_batch import _request
    import time

    async def never_finishes(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(never_finishes),
                                 base_url="http://test") as client:
        started = time.monotonic()
        with pytest.raises(BatchError, match="deadline exceeded"):
            await _request(client, "GET", "/slow", started + 0.05)
        assert time.monotonic() - started < 0.5


def test_cli_returns_nonzero_for_failed_manifest(tmp_path, monkeypatch):
    from evals.multilang.multifile_batch import create_manifest, main
    import sys

    source = tmp_path / "x.txt"
    source.write_text("Hello")
    manifest = tmp_path / "manifest.json"
    saved = create_manifest([BatchEntry(source, "en", "vi")], manifest)
    saved["entries"][0]["status"] = "failed"
    manifest.write_text(json.dumps(saved))
    monkeypatch.setattr(sys, "argv", ["multifile_batch", "resume", "--base-url", "http://test",
                                  "--manifest", str(manifest), "--output-dir", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_resume_known_job_after_source_deleted(tmp_path, monkeypatch):
    from evals.multilang.multifile_batch import create_manifest
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_app(NoModel())
    source = tmp_path / "gone.txt"
    source.write_text("Hello")
    manifest = tmp_path / "manifest.json"
    saved = create_manifest([BatchEntry(source, "en", "en")], manifest)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/ai/v2/translate/files",
            files={"file": (source.name, source.read_bytes())},
            data={"source_lang": "en", "target_lang": "en"},
            headers={"Idempotency-Key": saved["entries"][0]["key"]})
        saved["entries"][0].update(job_id=response.json()["job_id"], status="queued")
        manifest.write_text(json.dumps(saved))
        source.unlink()
        worker = Worker(app.state.translation_v2_store, app.state.translation_v2_settings, None)
        task = asyncio.create_task(worker.run_once())
        result = await run_batch(client, None, manifest, tmp_path / "out",
                                 upload_interval=0, poll_interval=0.01, job_timeout=10)
        await task
    assert result["succeeded"] == 1
    assert (tmp_path / "out" / "0001.txt").read_text() == "Hello"


@pytest.mark.asyncio
async def test_changed_pending_file_fails_only_its_own_row(tmp_path, monkeypatch):
    from evals.multilang.multifile_batch import create_manifest
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_app(NoModel())
    bad, good = tmp_path / "changed.txt", tmp_path / "good.txt"
    bad.write_text("original")
    good.write_text("Hello")
    manifest = tmp_path / "manifest.json"
    create_manifest([BatchEntry(bad, "en", "en"), BatchEntry(good, "en", "en")], manifest)
    bad.write_text("changed")
    worker = Worker(app.state.translation_v2_store, app.state.translation_v2_settings, None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        async def consume():
            for _ in range(100):
                await worker.run_once()
                await asyncio.sleep(0.005)
        task = asyncio.create_task(consume())
        result = await run_batch(client, None, manifest, tmp_path / "out",
                                 upload_interval=0, poll_interval=0.01, job_timeout=10)
        await task
    assert result["failed"] == result["succeeded"] == 1
    assert (tmp_path / "out" / "0002.txt").read_text() == "Hello"


@pytest.mark.asyncio
async def test_status_outage_does_not_admit_beyond_two_remote_jobs(tmp_path):
    paths = []
    for index in range(5):
        path = tmp_path / f"{index}.txt"
        path.write_text("Hello")
        paths.append(BatchEntry(path, "en", "vi"))
    submitted = []

    async def respond(request):
        if request.method == "POST":
            submitted.append(request.headers["Idempotency-Key"])
            job_id = f"{len(submitted):032x}"
            return httpx.Response(202, json={"job_id": job_id})
        return httpx.Response(503, json={"error": {"code": "DEPENDENCY_UNAVAILABLE"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url="http://test") as client:
        counts = await run_batch(client, paths, tmp_path / "manifest.json", tmp_path / "out",
                                 max_inflight=2, upload_interval=0, poll_interval=0.01,
                                 job_timeout=5)
    assert len(submitted) <= 2
    assert counts["interrupted"] == len(submitted)
    assert counts["pending"] == 5 - len(submitted)


@pytest.mark.asyncio
async def test_post_503_is_retryable_on_resume_with_same_key(tmp_path, monkeypatch):
    from evals.multilang.multifile_batch import create_manifest
    source = tmp_path / "x.txt"
    source.write_text("Hello")
    manifest = tmp_path / "manifest.json"
    original = create_manifest([BatchEntry(source, "en", "en")], manifest)

    async def unavailable(request):
        return httpx.Response(503, json={"error": {"code": "DEPENDENCY_UNAVAILABLE"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable), base_url="http://test") as client:
        counts = await run_batch(client, None, manifest, tmp_path / "out",
                                 upload_interval=0, poll_interval=0.01, job_timeout=5)
    assert counts["interrupted"] == 1 and counts["failed"] == 0
    assert json.loads(manifest.read_text())["entries"][0]["key"] == original["entries"][0]["key"]

    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_app(NoModel())
    worker = Worker(app.state.translation_v2_store, app.state.translation_v2_settings, None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        async def consume():
            for _ in range(80):
                await worker.run_once()
                await asyncio.sleep(0.005)
        task = asyncio.create_task(consume())
        counts = await run_batch(client, None, manifest, tmp_path / "out",
                                 upload_interval=0, poll_interval=0.01, job_timeout=10)
        await task
    assert counts["succeeded"] == 1
