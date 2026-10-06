#!/usr/bin/env python3
"""Static FE + reverse-proxy to gateway (no inotify / no Vite watch).

SSE (/ai/*/stream) is streamed chunk-by-chunk — never buffered with resp.read().
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import mimetypes
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent / "dist"
GATEWAY = os.environ.get("VITE_GATEWAY_PROXY", "http://127.0.0.1:8131")
REPORT_UPSTREAM_FILE = Path(__file__).resolve().parent / ".report-upstream"
REPORT_GATEWAY = os.environ.get("REPORT_GATEWAY_PROXY", REPORT_UPSTREAM_FILE.read_text().strip() if REPORT_UPSTREAM_FILE.exists() else GATEWAY)
TRANSLATION_UPSTREAM_FILE = Path(__file__).resolve().parent / ".translation-upstream"
TRANSLATION_GATEWAY = os.environ.get("TRANSLATION_GATEWAY_PROXY", TRANSLATION_UPSTREAM_FILE.read_text().strip() if TRANSLATION_UPSTREAM_FILE.exists() else GATEWAY)
TRAINING_UPSTREAM_FILE = Path(__file__).resolve().parent / ".reward-training-upstream"
TRAINING_GATEWAY = os.environ.get("REWARD_TRAINING_GATEWAY_PROXY", TRAINING_UPSTREAM_FILE.read_text().strip() if TRAINING_UPSTREAM_FILE.exists() else GATEWAY)
REPORT_KEY_FILE = Path(os.environ.get("REPORT_KEY_FILE", str(Path(__file__).resolve().parent / ".report-api-key.local")))
REVIEW_TOKENS_FILE = Path(__file__).resolve().parent / ".review-devspace-tokens.local"
REVIEW_ALLOW_LOOPBACK = os.environ.get("DEVSPACE_REVIEW_ALLOW_LOOPBACK", "false").lower() == "true"
REVIEW_LOOPBACK_FILE = Path(__file__).resolve().parent / ".review-loopback.local"
TAILNET = ipaddress.ip_network("100.64.0.0/10")
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


def _gw_parts(target=GATEWAY):
    u = urlsplit(target)
    return u.hostname or "127.0.0.1", u.port or 80, u.scheme == "https"


def _tailnet_connection(peer: str, local: str) -> bool:
    try:
        return ipaddress.ip_address(peer) in TAILNET and ipaddress.ip_address(local) in TAILNET
    except ValueError:
        return False


def _review_token(role: str) -> str | None:
    if role not in {"admin", "reviewer"}:
        return None
    try:
        tokens = json.loads(REVIEW_TOKENS_FILE.read_text(encoding="utf-8"))
        value = tokens.get(role)
        return value if isinstance(value, str) and value else None
    except (OSError, ValueError, AttributeError):
        return None


def _review_connection(peer: str, local: str) -> bool:
    if _tailnet_connection(peer, local):
        return True
    if not (REVIEW_ALLOW_LOOPBACK or REVIEW_LOOPBACK_FILE.exists()):
        return False
    try:
        return ipaddress.ip_address(peer).is_loopback and ipaddress.ip_address(local).is_loopback
    except ValueError:
        return False


def _report_key() -> str | None:
    try:
        value = REPORT_KEY_FILE.read_text(encoding="utf-8").strip()
        return value if value and "\n" not in value and "\r" not in value else None
    except (OSError, UnicodeError):
        return None


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
        path = urlsplit(self.path).path
        is_report = path == "/ai/v2/reports" or path.startswith("/ai/v2/reports/")
        report_workspace = self.headers.get("X-Report-Workspace") if is_report else None
        if report_workspace is not None:
            if report_workspace != "shared":
                self.send_error(400, "Unsupported report workspace")
                return
            if not _review_connection(self.client_address[0], self.connection.getsockname()[0]):
                self.send_error(403, "Devspace report access requires a configured connection")
                return
            report_key = _report_key()
            if report_key is None:
                self.send_error(503, "Devspace report access is not configured")
                return
        review_role = self.headers.get("X-Review-Role") if path == "/review-api" or path.startswith("/review-api/") else None
        if review_role is not None:
            if not _review_connection(self.client_address[0], self.connection.getsockname()[0]):
                self.send_error(403, "Devspace review access requires a configured connection")
                return
            review_token = _review_token(review_role)
            if review_token is None:
                self.send_error(503, "Devspace review access is not configured")
                return
        target = REPORT_GATEWAY if is_report else GATEWAY
        if path.startswith("/review-api/reward/training/"):
            target = TRAINING_GATEWAY
        if any(path == prefix or path.startswith(prefix + "/")
               for prefix in ("/ai/v2/translate", "/ai/v3/translate")):
            target = TRANSLATION_GATEWAY
        host, port, tls = _gw_parts(target)
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
            if k.lower() not in {"host", "content-length", "connection", "accept-encoding", "x-review-role", "x-report-workspace"}
        }
        if report_workspace is not None:
            # Replace any caller key, including differently cased header names.
            headers = {k: v for k, v in headers.items() if k.lower() != "x-api-key"}
            headers["X-API-Key"] = report_key
        if review_role is not None:
            headers["Authorization"] = f"Bearer {review_token}"
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
