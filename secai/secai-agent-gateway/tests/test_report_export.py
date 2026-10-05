from io import BytesIO

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader

from secai_gateway.config import Settings
from secai_gateway.report_v2 import api, store

CONTENT = '''# Báo cáo an toàn

## Tóm tắt
Tiếng Việt: **kiểm tra** CVE-2026-1234 tại `192.0.2.15`.

- Hành động thứ nhất
- Hành động thứ hai

[Tài liệu](https://example.test/reference)

| Máy chủ | Kết quả |
| --- | --- |
| 192.0.2.15 | Đã kiểm tra |

```text
curl https://example.test/a_b
```
'''


@pytest.fixture
def report_client(tmp_path, monkeypatch):
    settings = Settings(report_feature_enabled=True, report_api_key='export-key',
                        report_db_path=str(tmp_path / 'reports.db'))
    monkeypatch.setattr(api, 'get_settings', lambda: settings)
    template = store.create_template(settings.report_db_path, dict(name='Mẫu', description='',
                                    sections=['Tóm tắt'], instructions='', example=''))
    report = store.create_report(settings.report_db_path, template, 'source', CONTENT)
    store.edit_report(settings.report_db_path, report['id'], CONTENT + '\nBản thứ hai.', 1, 'edit')
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app), settings.report_db_path, report['id']


def download(fixture, format, revision=None):
    c, _, rid = fixture
    return c.get(f'/ai/v2/reports/{rid}/export', params={'format': format, **(
        {'revision': revision} if revision is not None else {})}, headers={'X-API-Key': 'export-key'})


def test_export_auth_and_validation(report_client):
    c, _, rid = report_client
    url = f'/ai/v2/reports/{rid}/export'
    assert c.get(url + '?format=bad&revision=no').status_code == 401
    assert download(report_client, 'html').status_code == 422
    assert download(report_client, 'txt', 0).status_code == 422
    assert download(report_client, 'txt', 99).status_code == 404
    assert c.get('/ai/v2/reports/missing/export?format=txt',
                 headers={'X-API-Key': 'export-key'}).status_code == 404


def test_plaintext_current_history_and_no_mutation(report_client):
    _, db, rid = report_client
    before = store.list_revisions(db, rid)
    current = download(report_client, 'txt')
    assert current.status_code == 200
    assert current.headers['content-type'] == 'text/plain; charset=utf-8'
    assert current.headers['cache-control'] == 'no-store'
    assert current.headers['content-disposition'] == f'attachment; filename="report-{rid}-v2.txt"'
    assert 'Bản thứ hai.' in current.text
    assert 'Bản thứ hai.' not in download(report_client, 'txt', 1).text
    assert '**' not in current.text and '```' not in current.text
    for value in ['Tiếng Việt', 'CVE-2026-1234', '192.0.2.15', 'curl https://example.test/a_b', 'https://example.test/reference', 'Đã kiểm tra']:
        assert value in current.text
    assert store.list_revisions(db, rid) == before


def test_docx_document_and_table(report_client):
    response = download(report_client, 'docx', 1)
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    doc = Document(BytesIO(response.content))
    text = '\n'.join(p.text for p in doc.paragraphs)
    assert 'Báo cáo an toàn' in text and 'CVE-2026-1234' in text
    assert 'curl https://example.test/a_b' in text
    assert doc.tables[0].cell(1, 1).text == 'Đã kiểm tra'


def test_pdf_unicode_and_history(report_client):
    response = download(report_client, 'pdf', 1)
    assert response.status_code == 200 and response.content.startswith(b'%PDF-')
    assert response.headers['content-type'] == 'application/pdf'
    text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
    for value in ['Báo cáo an toàn', 'Tiếng Việt', 'CVE-2026-1234', '192.0.2.15', 'Đã kiểm tra']:
        assert value in text
    assert 'Bản thứ hai.' not in text


def test_long_pdf_wraps_and_repeats_table_header(report_client):
    _, db, rid = report_client
    rows = '\n'.join(f'| {n} | ' + ('Nội dung tiếng Việt ' * 12) + f'cuối-{n} |' for n in range(60))
    content = '# Báo cáo dài\n\n| STT | Nội dung |\n| --- | --- |\n' + rows + '\n\nKẾT THÚC'
    store.edit_report(db, rid, content, 2, 'long')
    response = download(report_client, 'pdf')
    assert response.status_code == 200, response.text[:200] if response.status_code != 200 else ''
    reader = PdfReader(BytesIO(response.content))
    assert len(reader.pages) > 2
    pages = [page.extract_text() for page in reader.pages]
    assert all('STT' in text for text in pages[:-1])
    assert 'cuối-59' in '\n'.join(pages) and 'KẾT THÚC' in pages[-1]


def test_exports_treat_html_and_images_as_text_without_fetching(report_client, monkeypatch):
    import httpx
    def no_network(*args, **kwargs):
        raise AssertionError('Export must not fetch external resources')
    monkeypatch.setattr(httpx.Client, 'get', no_network)
    _, db, rid = report_client
    content = '# Kiểm tra\n\n<script>alert("x")</script>\n\n![Ảnh ngoài](https://invalid.test/image.jpg)\n\n<font color="red">literal</font>'
    store.edit_report(db, rid, content, 2, 'html')
    assert '<script>alert("x")</script>' in download(report_client, 'txt').text
    for format in ['docx', 'pdf']:
        response = download(report_client, format)
        assert response.status_code == 200
    text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(download(report_client, 'pdf').content)).pages)
    assert '<script>alert("x")</script>' in text and 'Ảnh ngoài' in text


def test_pdf_wide_table_and_oversized_cell_not_truncated(report_client):
    _, db, rid = report_client
    header = '| A | B | C | D | E | F | G | H |\n|---|---|---|---|---|---|---|---|'
    row = '| ' + ' | '.join(['mã-không-ngắt-' * 20] * 8) + ' |'
    content = '# Bảng rộng\n\n' + header + '\n' + row + '\n\n| Trường | Giá trị |\n|---|---|\n| Lớn | ' + ('Nội dung ' * 1400) + 'KẾT-CELL |'
    store.edit_report(db, rid, content, 2, 'wide')
    response = download(report_client, 'pdf')
    assert response.status_code == 200
    pages = PdfReader(BytesIO(response.content)).pages
    assert len(pages) > 3
    text = '\n'.join(page.extract_text() for page in pages)
    assert 'KẾT-CELL' in text
