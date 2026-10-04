from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from secai_gateway.config import Settings
from secai_gateway.report_v2 import api


def client(tmp_path, monkeypatch, *, enabled=True, key='test-key'):
    settings = Settings(
        report_feature_enabled=enabled, report_api_key=key,
        report_db_path=str(tmp_path / 'reports.db'),
    )
    monkeypatch.setattr(api, 'get_settings', lambda: settings)
    monkeypatch.setattr(api, 'generate_text', fake_generate)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


async def fake_generate(system, user, settings):
    return '# Báo cáo\n## Tóm tắt\nCVE-2026-1234\n## Kết luận\nCVE-2026-1234'


def auth():
    return {'X-API-Key': 'test-key'}


def template():
    return {'name': 'Mẫu A', 'description': '', 'sections': ['Tóm tắt', 'Kết luận'], 'instructions': 'Ngắn gọn', 'example': ''}


def test_disabled_and_auth_fail_closed(tmp_path, monkeypatch):
    disabled = client(tmp_path, monkeypatch, enabled=False)
    assert disabled.get('/ai/v2/reports/templates', headers=auth()).status_code == 404
    enabled = client(tmp_path, monkeypatch)
    assert enabled.get('/ai/v2/reports/templates').status_code == 401
    assert enabled.get('/ai/v2/reports/templates', headers={'X-API-Key': 'wrong'}).status_code == 401
    no_key = client(tmp_path, monkeypatch, key='')
    assert no_key.get('/ai/v2/reports/templates', headers=auth()).status_code == 404


def test_template_generate_edit_revision_and_conflicts(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    t = c.post('/ai/v2/reports/templates', json=template(), headers=auth())
    assert t.status_code == 200, t.text
    created = t.json()
    assert created['version'] == 1
    assert c.get('/ai/v2/reports/templates', headers=auth()).json()[0]['id'] == created['id']
    updated = c.patch(f"/ai/v2/reports/templates/{created['id']}", json={**template(), 'name': 'Mẫu B', 'expected_version': 1}, headers=auth())
    assert updated.status_code == 200 and updated.json()['version'] == 2
    stale = c.patch(f"/ai/v2/reports/templates/{created['id']}", json={**template(), 'expected_version': 1}, headers=auth())
    assert stale.status_code == 409
    report = c.post('/ai/v2/reports/generate', json={'template_id': created['id'], 'data': 'CVE-2026-1234'}, headers=auth())
    assert report.status_code == 200, report.text
    r = report.json()
    assert (r['revision'], r['template_version']) == (1, 2)
    assert c.get(f"/ai/v2/reports/{r['id']}", headers=auth()).json()['content'] == r['content']
    edited = c.post(f"/ai/v2/reports/{r['id']}/edit", json={'instruction': 'Viết rõ hơn', 'expected_revision': 1}, headers=auth())
    assert edited.status_code == 200, edited.text
    assert edited.json()['revision'] == 2
    assert c.post(f"/ai/v2/reports/{r['id']}/edit", json={'instruction': 'Sai phiên bản', 'expected_revision': 1}, headers=auth()).status_code == 409
    assert [v['revision'] for v in c.get(f"/ai/v2/reports/{r['id']}/revisions", headers=auth()).json()] == [1, 2]


def test_import_preview_docx_and_unsafe_archive(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    bio = BytesIO()
    with ZipFile(bio, 'w') as z:
        z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Tiêu đề</w:t></w:r></w:p><w:p><w:r><w:t>Chi tiết</w:t></w:r></w:p></w:document>')
    imported = c.post('/ai/v2/reports/templates/import', files={'file': ('sample.docx', bio.getvalue())}, headers=auth())
    assert imported.status_code == 200, imported.text
    assert 'Tiêu đề' in imported.json()['example']
    assert c.get('/ai/v2/reports/templates', headers=auth()).json() == []
    bad = BytesIO()
    with ZipFile(bad, 'w') as z:
        z.writestr('word/document.xml', 'safe')
        z.writestr('word/vbaProject.bin', 'malicious')
    assert c.post('/ai/v2/reports/templates/import', files={'file': ('macro.docx', bad.getvalue())}, headers=auth()).status_code == 422
    assert c.post('/ai/v2/reports/templates/import', files={'file': ('too.txt', b'x' * (2 * 1024 * 1024 + 1))}, headers=auth()).status_code == 413


def test_unverified_model_text_does_not_persist(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    template_id = c.post('/ai/v2/reports/templates', json=template(), headers=auth()).json()['id']

    async def hallucination(system, user, settings):
        return '## Tóm tắt\nCVE-2026-9999\n## Kết luận\nSai dữ kiện'

    monkeypatch.setattr(api, 'generate_text', hallucination)
    response = c.post('/ai/v2/reports/generate', json={'template_id': template_id, 'data': 'CVE-2026-1234'}, headers=auth())
    assert response.status_code == 422
    assert c.get('/ai/v2/reports/templates', headers=auth()).status_code == 200

    async def valid(system, user, settings):
        return '## Tóm tắt\nCVE-2026-1234\n## Kết luận\nCVE-2026-1234'

    monkeypatch.setattr(api, 'generate_text', valid)
    report = c.post('/ai/v2/reports/generate', json={'template_id': template_id, 'data': 'CVE-2026-1234'}, headers=auth()).json()
    monkeypatch.setattr(api, 'generate_text', hallucination)
    edit = c.post(f"/ai/v2/reports/{report['id']}/edit", json={'instruction': 'Bổ sung', 'expected_revision': 1}, headers=auth())
    assert edit.status_code == 422
    assert c.get(f"/ai/v2/reports/{report['id']}", headers=auth()).json()['revision'] == 1


def test_missing_section_and_new_number_rejected(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    template_id = c.post('/ai/v2/reports/templates', json=template(), headers=auth()).json()['id']

    async def missing(system, user, settings):
        return '## Tóm tắt\nCVE-2026-1234'

    monkeypatch.setattr(api, 'generate_text', missing)
    assert c.post('/ai/v2/reports/generate', json={'template_id': template_id, 'data': 'CVE-2026-1234'}, headers=auth()).status_code == 422

    async def invented_number(system, user, settings):
        return '## Tóm tắt\nCó 99 sự kiện\n## Kết luận\nCVE-2026-1234'

    monkeypatch.setattr(api, 'generate_text', invented_number)
    assert c.post('/ai/v2/reports/generate', json={'template_id': template_id, 'data': 'CVE-2026-1234'}, headers=auth()).status_code == 422


def test_edit_uses_original_source_and_template_snapshot(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    original = c.post('/ai/v2/reports/templates', json=template(), headers=auth()).json()
    report = c.post('/ai/v2/reports/generate', json={'template_id': original['id'], 'data': 'CVE-2026-1234'}, headers=auth()).json()
    changed = {**template(), 'sections': ['Mục mới'], 'expected_version': 1}
    assert c.patch(f"/ai/v2/reports/templates/{original['id']}", json=changed, headers=auth()).status_code == 200

    async def follows_changed_template(system, user, settings):
        return '## Mục mới\nCVE-2026-1234'

    monkeypatch.setattr(api, 'generate_text', follows_changed_template)
    response = c.post(f"/ai/v2/reports/{report['id']}/edit", json={'instruction': 'Đổi bố cục', 'expected_revision': 1}, headers=auth())
    assert response.status_code == 422
    assert c.get(f"/ai/v2/reports/{report['id']}", headers=auth()).json()['revision'] == 1


def test_model_truncation_is_rejected(monkeypatch):
    import asyncio
    import httpx
    from fastapi import HTTPException

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]}, request=httpx.Request('POST', 'http://model/chat/completions'))

    monkeypatch.setattr(api.httpx, 'AsyncClient', lambda **kwargs: FakeClient())
    try:
        asyncio.run(api.generate_text('system', 'user', Settings()))
    except HTTPException as exc:
        assert exc.status_code == 502
    else:
        assert False, 'Truncated output must be rejected'
