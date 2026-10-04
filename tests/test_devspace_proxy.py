"""Exercise the actual Devspace HTTP proxy against local upstreams."""
import importlib.util
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

import pytest


@contextmanager
def serving(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def upstream(name):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"upstream": name, "path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


@pytest.mark.parametrize("separate_reports", [False, True])
def test_report_proxy_preserves_gateway_routes(monkeypatch, separate_reports, tmp_path):
    proxy_source = Path(__file__).parents[1] / "secai-fe-devspace" / "serve_static.py"
    # Load beside an empty config directory so a developer's local marker is ignored.
    module_path = tmp_path / "serve_static.py"
    module_path.write_bytes(proxy_source.read_bytes())
    with serving(upstream("gateway")) as gateway, serving(upstream("reports")) as reports, \
            serving(upstream("translation")) as translation:
        monkeypatch.setenv("VITE_GATEWAY_PROXY", gateway)
        if separate_reports:
            monkeypatch.setenv("REPORT_GATEWAY_PROXY", reports)
            monkeypatch.setenv("TRANSLATION_GATEWAY_PROXY", translation)
        else:
            monkeypatch.delenv("REPORT_GATEWAY_PROXY", raising=False)
            monkeypatch.delenv("TRANSLATION_GATEWAY_PROXY", raising=False)
        spec = importlib.util.spec_from_file_location("devspace_proxy", module_path)
        proxy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(proxy)
        with serving(proxy.Handler) as base:
            for path in ["/ai/v2/reports", "/ai/v2/reports/templates?limit=2",
                         "/ai/v2/reports-other", "/ai/v2/translate/files",
                         "/ai/v3/translate/languages", "/ai/v2/translate-other",
                         "/ai/v3/translate-other", "/review-api/health"]:
                with urlopen(base + path, timeout=5) as response:
                    result = json.load(response)
                expected = "reports" if separate_reports and (
                    path == "/ai/v2/reports" or path.startswith("/ai/v2/reports/")
                ) else "gateway"
                if separate_reports and path in (
                    "/ai/v2/translate/files", "/ai/v3/translate/languages"
                ):
                    expected = "translation"
                assert result == {"upstream": expected, "path": path}
