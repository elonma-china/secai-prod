"""Devspace workspace report access keeps the credential on the server."""
import http.client
import importlib.util
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def load_server():
    path = Path(__file__).resolve().parents[1] / 'secai-fe-devspace' / 'serve_static.py'
    spec = importlib.util.spec_from_file_location('report_proxy_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def running(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(port, path, headers=None, *, headers_only=False):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
    if headers_only:
        conn.putrequest('POST', path)
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.putheader('Content-Length', '999')
        conn.endheaders()  # No body: guards must reject before waiting to read it.
    else:
        conn.request('GET', path, headers=headers or {})
    response = conn.getresponse()
    result = (response.status, response.read())
    conn.close()
    return result


def test_report_workspace_guards_before_body_read(tmp_path, monkeypatch):
    module = load_server()
    monkeypatch.setattr(module, 'REPORT_KEY_FILE', tmp_path / 'missing-key')
    monkeypatch.setattr(module, '_review_connection', lambda *_: False)
    with running(module.Handler) as port:
        assert request(port, '/ai/v2/reports/generate', {'X-Report-Workspace': 'other'}, headers_only=True)[0] == 400
        assert request(port, '/ai/v2/reports/generate', {'X-Report-Workspace': 'shared'}, headers_only=True)[0] == 403
        monkeypatch.setattr(module, '_review_connection', lambda *_: True)
        assert request(port, '/ai/v2/reports/generate', {'X-Report-Workspace': 'shared'}, headers_only=True)[0] == 503


def test_report_workspace_injects_private_key_and_preserves_legacy_auth(tmp_path, monkeypatch):
    module = load_server()
    key_file = tmp_path / 'key'
    key_file.write_text('server-private-key\n')
    monkeypatch.setattr(module, 'REPORT_KEY_FILE', key_file)
    monkeypatch.setattr(module, '_review_connection', lambda *_: True)
    seen = []

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get('X-API-Key'), self.headers.get('X-Report-Workspace')))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True}).encode())

        def log_message(self, *_):
            pass

    with running(Upstream) as upstream, running(module.Handler) as port:
        target = f'http://127.0.0.1:{upstream}'
        monkeypatch.setattr(module, 'REPORT_GATEWAY', target)
        monkeypatch.setattr(module, 'GATEWAY', target)
        status, body = request(port, '/ai/v2/reports/templates?limit=2', {'X-Report-Workspace': 'shared', 'x-api-key': 'caller-key'})
        assert status == 200 and b'server-private-key' not in body
        assert seen[-1] == ('/ai/v2/reports/templates?limit=2', 'server-private-key', None)
        request(port, '/ai/v2/reports/templates', {'X-API-Key': 'explicit-key'})
        assert seen[-1][1] == 'explicit-key'
        request(port, '/ai/v2/reports-other', {'X-Report-Workspace': 'shared'})
        assert seen[-1][1:] == (None, None)


def test_report_key_unavailable_or_invalid_returns_none(tmp_path, monkeypatch):
    module = load_server()
    path = tmp_path / 'key'
    monkeypatch.setattr(module, 'REPORT_KEY_FILE', path)
    assert module._report_key() is None
    for value in ('', 'one\ntwo', 'one\rtwo'):
        path.write_text(value)
        assert module._report_key() is None
