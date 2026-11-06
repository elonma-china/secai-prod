#!/usr/bin/env python3
"""Lab test TỐI GIẢN cho exploit-v2 pipeline — lỗi command injection cổ điển qua /ping?host=
(không dùng framework ngoài, chỉ stdlib, tránh phụ thuộc pip). KHÔNG phải CVE thật, chỉ để
verify pipeline UI -> gateway -> pentestcode -> parse-result hoạt động đúng end-to-end."""
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

FLAG = "FLAG{exploit_v2_pipeline_smoke_test_ok}"
with open("/tmp/exploit-v2-lab/flag.txt", "w") as f:
    f.write(FLAG + "\n")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = b"Network Diagnostic Tool v1.0 -- use /ping?host=<host>"
        elif parsed.path == "/ping":
            host = parse_qs(parsed.query).get("host", ["127.0.0.1"])[0]
            # Deliberately vulnerable: unsanitized shell=True.
            out = subprocess.run(f"ping -c 1 {host}", shell=True, capture_output=True,
                                  text=True, timeout=5)
            body = (out.stdout + out.stderr).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9191), Handler).serve_forever()
