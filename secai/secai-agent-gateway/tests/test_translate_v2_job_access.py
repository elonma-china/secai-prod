import httpx
import pytest

from secai_gateway.app import create_app
from secai_gateway.translation.settings import TranslationSettings


class FakeRunner:
    async def complete(self, *args, **kwargs):
        return "", {}


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
