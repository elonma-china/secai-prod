import httpx
import pytest

from secai_gateway.app import create_app
from secai_gateway.translation.settings import TranslationSettings


class FakeRunner:
    async def complete(self, *args, **kwargs):
        return "", {}


def test_v2_access_log_filter_removes_capability_id():
    import logging
    from secai_gateway.app import _TranslationV2AccessFilter

    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
        '"GET /ai/v2/translate/jobs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa HTTP/1.1" 200',
        (), None)
    assert _TranslationV2AccessFilter().filter(record) is False


@pytest.mark.asyncio
async def test_anonymous_admission_replay_lookup_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_app(FakeRunner())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        headers = {"Idempotency-Key": "a" * 32}
        upload = {"file": ("note.TXT", b"Hello", "text/plain")}
        form = {"source_lang": "en", "target_lang": "vi"}
        first = await client.post("/ai/v2/translate/files", files=upload, data=form, headers=headers)
        assert first.status_code == 202, first.text
        assert first.headers["cache-control"] == "no-store"
        job = first.json()
        assert len(job["job_id"]) == 32
        assert (await client.post("/ai/v2/translate/files", files=upload, data=form,
                                  headers=headers)).json()["job_id"] == job["job_id"]
        assert (await client.get(job["status_url"])).status_code == 200
        assert (await client.delete(job["status_url"])).status_code == 202
        assert (await client.get(job["result_url"])).json()["error"]["code"] == "JOB_CANCELLED"


@pytest.mark.asyncio
async def test_unknown_field_and_unsupported_type_are_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_app(FakeRunner())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        bad = await client.post("/ai/v2/translate/files", files={"file": ("x.txt", b"abc")},
                                data={"target_lang": "vi", "user_id": "intruder"})
        assert (bad.status_code, bad.json()["error"]["code"]) == (400, "INVALID_REQUEST")
        assert bad.headers["cache-control"] == "no-store"
        pdf = await client.post("/ai/v2/translate/files", files={"file": ("x.pdf", b"%PDF-1.7")},
                                data={"target_lang": "vi"})
        assert (pdf.status_code, pdf.json()["error"]["code"]) == (415, "UNSUPPORTED_FILE_TYPE")


@pytest.mark.asyncio
async def test_multilingual_pair_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_app(FakeRunner())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        accepted = await client.post("/ai/v2/translate/files", files={"file": ("x.txt", "Xin chào".encode())},
                                     data={"source_lang": "vi", "target_lang": "zh"})
        assert accepted.status_code == 202, accepted.text
        unsupported = await client.post("/ai/v2/translate/files", files={"file": ("x.txt", b"Hello")},
                                        data={"source_lang": "en", "target_lang": "fr"})
        assert (unsupported.status_code, unsupported.json()["error"]["code"]) == (
            400, "UNSUPPORTED_LANGUAGE_PAIR")
        invalid = await client.post("/ai/v2/translate/files", files={"file": ("x.txt", b"Hello")},
                                    data={"source_lang": "zh-CN", "target_lang": "vi"})
        assert (invalid.status_code, invalid.json()["error"]["code"]) == (400, "INVALID_LANGUAGE")


@pytest.mark.asyncio
async def test_unavailable_v2_storage_does_not_break_legacy_health(tmp_path, monkeypatch):
    from secai_gateway.translation import jobs

    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    def unavailable(*args, **kwargs):
        raise OSError("private path canary")
    monkeypatch.setattr(jobs, "JobStore", unavailable)
    app = create_app(FakeRunner())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        response = await client.post("/ai/v2/translate/files",
                                     files={"file": ("x.txt", b"Hello")}, data={"target_lang": "vi"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
        assert "private path canary" not in response.text


@pytest.mark.asyncio
async def test_v2_cors_preflight_is_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSLATE_V2_ENABLED", "true")
    monkeypatch.setenv("TRANSLATE_V2_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("TRANSLATE_V2_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://example.test")
    app = create_app(FakeRunner())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        response = await client.options("/ai/v2/translate/files", headers={
            "Origin": "https://example.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Idempotency-Key",
        })
        assert response.status_code == 204
        assert response.headers["access-control-allow-headers"] == "Content-Type, Idempotency-Key"
        assert response.headers["cache-control"] == "no-store"
        assert response.content == b""
