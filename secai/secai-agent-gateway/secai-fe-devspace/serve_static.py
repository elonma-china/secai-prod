#!/usr/bin/env python3
"""Static FE + reverse-proxy to gateway (no inotify / no Vite watch).

SSE (/ai/*/stream) is streamed chunk-by-chunk — never buffered with resp.read().
"""
from __future__ import annotations

import http.client
import mimetypes
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent / "dist"
GATEWAY = os.environ.get("VITE_GATEWAY_PROXY", "http://127.0.0.1:8131")
HOST = os.environ.get("FE_HOST", "0.0.0.0")
PORT = int(os.environ.get("FE_PORT", "5173"))
PROXY_PREFIXES = ("/ai", "/health", "/poc", "/projects", "/sessions", "/admin", "/review-api")
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
}


def _gw_parts():
    u = urlsplit(GATEWAY)
    return u.hostname or "127.0.0.1", u.port or 80, u.scheme == "https"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Long hunts / SSE
    timeout = 600

    def log_message(self, fmt: str, *args) -> None:
        print(f"[fe] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health" or path.startswith(PROXY_PREFIXES):
            self._proxy()
            return
        self._static(path)

    def _static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        rel = path.lstrip("/")
        fp = (ROOT / rel).resolve()
        if not str(fp).startswith(str(ROOT.resolve())) or not fp.is_file():
            fp = ROOT / "index.html"
            if not fp.is_file():
                self.send_error(404, "dist missing — run npm run build")
                return
        data = fp.read_bytes()
        ctype = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self) -> None:
        host, port, tls = _gw_parts()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        if tls:
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                host, port, timeout=600
            )
        else:
            conn = http.client.HTTPConnection(host, port, timeout=600)
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower() not in {"host", "content-length", "connection", "accept-encoding"}
        }
        headers["Host"] = f"{host}:{port}"
        # Avoid compressed bodies so we can stream easily
        headers["Accept-Encoding"] = "identity"
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except Exception as exc:  # noqa: BLE001
            try:
                self.send_error(502, f"gateway proxy error: {exc}")
            except Exception:  # noqa: BLE001
                pass
            return

        ct = (resp.getheader("content-type") or "").lower()
        is_sse = "text/event-stream" in ct or "/stream" in urlsplit(self.path).path

        try:
            if is_sse:
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() in HOP_BY_HOP:
                        continue
                    self.send_header(k, v)
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()

                # Stream — never resp.read() whole body (breaks SSE). read1() returns as soon as
                # *some* bytes are available instead of blocking for a full 4KB, so tokens reach
                # the browser as the gateway emits them and a client disconnect is noticed almost
                # immediately (read() would otherwise sit blocked up to the next full chunk).
                while True:
                    chunk = resp.read1(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                # Plain JSON CRUD response — buffer it fully so we can send a real
                # Content-Length. Without it (HOP_BY_HOP strips the upstream one) the
                # keep-alive connection has no framing and the client can hang waiting
                # for more bytes that never come.
                body = resp.read()
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() in HOP_BY_HOP:
                        continue
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def main() -> None:
    if not ROOT.is_dir():
        raise SystemExit(f"missing {ROOT} — run: npm run build")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    print(f"FE static+proxy http://{HOST}:{PORT}/ → {GATEWAY}  (root={ROOT})")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
