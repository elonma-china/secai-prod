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
        assert result["succeeded"] == result["total"] == 18
        assert result["failed"] == result["unfinished"] == 0
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
    assert result["succeeded"] == result["failed"] == 1
    assert result["total"] == 2 and result["unfinished"] == 0
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


@pytest.mark.parametrize("body,content_type", [
    (b"<html>upstream unavailable</html>", "text/html"),
    (b"", "text/plain"),
])
@pytest.mark.asyncio
async def test_non_json_post_503_preserves_key_and_halts_admission(tmp_path, body, content_type):
    entries = []
    for index in range(3):
        source = tmp_path / f"{index}.txt"
        source.write_text("Hello")
        entries.append(BatchEntry(source, "en", "vi"))
    posts = []

    async def unavailable(request):
        posts.append(request.headers["Idempotency-Key"])
        return httpx.Response(503, content=body, headers={"content-type": content_type})

    manifest = tmp_path / "manifest.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable), base_url="http://test") as client:
        counts = await run_batch(client, entries, manifest, tmp_path / "out",
                                 max_inflight=1, upload_interval=0, poll_interval=0.01, job_timeout=5)
    rows = json.loads(manifest.read_text())["entries"]
    assert len(posts) == 1
    assert counts["interrupted"] == 1 and counts["pending"] == 2
    assert counts["failed"] == 0 and counts["total"] == counts["unfinished"] == 3
    assert rows[0]["status"] == "interrupted" and rows[0]["error_code"] == "HTTP_503"
    assert rows[0]["key"] == posts[0]


@pytest.mark.asyncio
async def test_halted_known_jobs_remain_visible_in_complete_summary(tmp_path):
    from evals.multilang.multifile_batch import create_manifest
    entries = []
    for index in range(3):
        source = tmp_path / f"{index}.txt"
        source.write_text("Hello")
        entries.append(BatchEntry(source, "en", "vi"))
    manifest = tmp_path / "manifest.json"
    saved = create_manifest(entries, manifest)
    for index, row in enumerate(saved["entries"]):
        row.update(job_id=f"{index + 1:032x}", status="queued")
    manifest.write_text(json.dumps(saved))

    async def unavailable(request):
        return httpx.Response(503, content=b"", headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable), base_url="http://test") as client:
        counts = await run_batch(client, None, manifest, tmp_path / "out",
                                 max_inflight=1, upload_interval=0, poll_interval=0.01, job_timeout=5)
    assert counts["total"] == counts["unfinished"] == 3
    assert counts["interrupted"] == 1 and counts["queued"] == 2
    assert sum(counts[key] for key in ("succeeded", "failed", "cancelled", "interrupted",
                                       "pending", "queued", "running", "cancelling", "other")) == 3


def test_cli_exits_nonzero_for_unfinished_known_jobs(tmp_path, monkeypatch):
    from evals.multilang import multifile_batch
    import sys

    async def unfinished(*args, **kwargs):
        return {"succeeded": 0, "failed": 0, "cancelled": 0, "interrupted": 0,
                "pending": 0, "queued": 2, "running": 0, "cancelling": 0,
                "other": 0, "total": 2, "unfinished": 2}

    monkeypatch.setattr(multifile_batch, "run_batch", unfinished)
    monkeypatch.setattr(sys, "argv", ["multifile_batch", "resume", "--base-url", "http://test",
                                  "--manifest", str(tmp_path / "manifest.json"),
                                  "--output-dir", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as exc:
        multifile_batch.main()
    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_mixed_documents_and_images_use_own_routes_and_preserve_ocr_result(tmp_path, monkeypatch):
    from secai_gateway.translation import ocr_client
    from secai_gateway.translation.types import CorrectedOcr, DocumentBlock, WarningItem

    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("TRANSLATE_V2_OCR_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_OCR_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("TRANSLATE_V2_UPLOAD_RATE_PER_MINUTE", "100")
    # Fast polling here tests mixed-file routing, not the separately tested quota.
    monkeypatch.setenv("TRANSLATE_V2_LOOKUP_RATE_PER_MINUTE", "1000")

    async def fake_ocr(self, input_path, *, work_dir, context):
        block = DocumentBlock(id="b000001", kind="ocr_text", order=0,
                              text="Corrected OCR", metadata={})
        return CorrectedOcr(blocks=(block,), raw_text="Raw OCR",
                            corrected_text="Corrected OCR", needs_review=True,
                            warnings=(WarningItem(code="AMBIGUOUS_TECHNICAL_TOKEN",
                                                  message="Review token."),))

    monkeypatch.setattr(ocr_client.ImageOcrPipeline, "process", fake_ocr)
    app = create_app(NoModel())
    text = tmp_path / "note.txt"
    text.write_text("Hello")
    docx = tmp_path / "report.docx"
    document = Document()
    document.add_paragraph("Hello")
    document.save(docx)
    png = tmp_path / "scan.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"mocked-image")
    jpeg = tmp_path / "photo.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff" + b"mocked-image")
    entries = [BatchEntry(path, "en", "en") for path in (text, docx, png, jpeg)]
    worker = Worker(app.state.translation_v2_store, app.state.translation_v2_settings, None)

    async def consume():
        for _ in range(150):
            await worker.run_once()
            await asyncio.sleep(0.005)

    task = asyncio.create_task(consume())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        counts = await run_batch(client, entries, tmp_path / "manifest.json", tmp_path / "out",
                                 max_inflight=2, upload_interval=0, poll_interval=0.01,
                                 job_timeout=10)
    await task
    assert counts["succeeded"] == counts["total"] == 4
    saved = json.loads((tmp_path / "manifest.json").read_text())["entries"]
    assert [row["kind"] for row in saved] == ["file", "file", "image", "image"]
    for index in (2, 3):
        result = json.loads((tmp_path / "out" / saved[index]["output_json"]).read_text())
        assert result["kind"] == "image"
        assert result["ocr"]["raw_text"] == "Raw OCR"
        assert result["ocr"]["corrected_text"] == "Corrected OCR"
        assert result["ocr"]["needs_review"] is True
        assert result["warnings"][0]["code"] == "AMBIGUOUS_TECHNICAL_TOKEN"


@pytest.mark.asyncio
async def test_ambiguous_image_upload_replays_image_route_for_resume_and_cancel(tmp_path, monkeypatch):
    from evals.multilang.multifile_batch import create_manifest
    from secai_gateway.translation import ocr_client
    from secai_gateway.translation.types import CorrectedOcr, DocumentBlock

    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("TRANSLATE_V2_OCR_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_OCR_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("TRANSLATE_V2_UPLOAD_RATE_PER_MINUTE", "100")

    async def fake_ocr(self, input_path, *, work_dir, context):
        block = DocumentBlock(id="b000001", kind="ocr_text", order=0,
                              text="Visible", metadata={})
        return CorrectedOcr(blocks=(block,), raw_text="Visible", corrected_text="Visible")

    monkeypatch.setattr(ocr_client.ImageOcrPipeline, "process", fake_ocr)
    app = create_app(NoModel())
    image = tmp_path / "scan.jpeg"
    image.write_bytes(b"\xff\xd8\xff" + b"mocked-image")
    manifest = tmp_path / "manifest.json"
    saved = create_manifest([BatchEntry(image, "en", "en")], manifest)
    saved["entries"][0]["status"] = "uploading"
    manifest.write_text(json.dumps(saved))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        admitted = await client.post("/ai/v2/translate/images",
                                     files={"file": (image.name, image.read_bytes())},
                                     data={"source_lang": "en", "target_lang": "en"},
                                     headers={"Idempotency-Key": saved["entries"][0]["key"]})
        assert admitted.status_code == 202
        worker = Worker(app.state.translation_v2_store, app.state.translation_v2_settings, None)
        task = asyncio.create_task(worker.run_once())
        counts = await run_batch(client, None, manifest, tmp_path / "out",
                                 max_inflight=1, upload_interval=0, poll_interval=0.01,
                                 job_timeout=10)
        await task
        assert counts["succeeded"] == 1
        assert json.loads(manifest.read_text())["entries"][0]["job_id"] == admitted.json()["job_id"]

        second = tmp_path / "cancel.png"
        second.write_bytes(b"\x89PNG\r\n\x1a\n" + b"mocked-image")
        cancel_manifest = tmp_path / "cancel-manifest.json"
        cancelling = create_manifest([BatchEntry(second, "en", "vi")], cancel_manifest)
        cancelling["entries"][0]["status"] = "uploading"
        cancel_manifest.write_text(json.dumps(cancelling))
        second_job = await client.post("/ai/v2/translate/images",
                                       files={"file": (second.name, second.read_bytes())},
                                       data={"source_lang": "en", "target_lang": "vi"},
                                       headers={"Idempotency-Key": cancelling["entries"][0]["key"]})
        assert second_job.status_code == 202
        cancelled = await cancel_batch(client, cancel_manifest)
        assert cancelled["cancel_requested"] == 1 and cancelled["unresolved"] == 0
        assert (await client.get(second_job.json()["status_url"])).json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_existing_file_manifest_migrates_kind_without_replacing_key(tmp_path, monkeypatch):
    from evals.multilang.multifile_batch import create_manifest, _binding

    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_app(NoModel())
    path = tmp_path / "old.txt"
    path.write_text("Hello")
    manifest = tmp_path / "manifest.json"
    saved = create_manifest([BatchEntry(path, "en", "en")], manifest)
    original_key = saved["entries"][0]["key"]
    saved["entries"][0].pop("kind")
    saved["entries"][0]["binding"] = _binding(saved["entries"][0])
    manifest.write_text(json.dumps(saved))
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
    row = json.loads(manifest.read_text())["entries"][0]
    assert counts["succeeded"] == 1
    assert row["kind"] == "file" and row["key"] == original_key
