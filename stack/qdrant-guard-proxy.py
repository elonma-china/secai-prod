#!/usr/bin/env python3
"""Proxy chặn ghi đứng giữa tunnel packtest và Qdrant NGUỒN (prod). Chỉ stdlib.

    qdrant-guard-proxy.py --upstream http://127.0.0.1:6333 --collection C --state DIR --alert FILE   (khoá: dòng đầu stdin)

Nghe 127.0.0.1:<cổng ngẫu nhiên> (ghi vào DIR/port). Chỉ cho đúng collection C, không query string:
  GET  /, /collections/C, /collections/C/snapshots, /collections/C/snapshots/<tên>   (đọc, tải snapshot)
  POST /collections/C/points/scroll | /points | /points/count                        (đọc)
  POST /collections/C/snapshots                                                       (tạo; tên ghi vào DIR/created.jsonl)
  DELETE /collections/C/snapshots/<tên>  chỉ khi <tên> do chính proxy này thấy được tạo trong lần chạy này
Mọi thứ khác: 403, ghi DIR/denied.log và FILE (ALERT), không tới upstream.
Lệnh gọi upstream chạy tới hết dù client ngắt (tạo snapshot vẫn được ghi tên). DIR/inflight = số yêu cầu đang chạy.
Khoá (nếu có) do proxy gắn vào upstream; header api-key của client bị bỏ. Không in khoá.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = r"[A-Za-z0-9_][A-Za-z0-9._-]*"


def arg(name: str) -> str:
    a = sys.argv[1:]
    if name not in a or a.index(name) + 1 >= len(a):
        sys.exit(f"usage: {sys.argv[0]} --upstream URL --collection C --state DIR --alert FILE")
    return a[a.index(name) + 1]


UP = urllib.parse.urlsplit(arg("--upstream"))
COLL, STATE, ALERT = arg("--collection"), arg("--state"), arg("--alert")
if UP.scheme != "http" or not UP.hostname or not re.fullmatch(r"[A-Za-z0-9_-]+", COLL):
    sys.exit("qdrant-guard: upstream phải là http://host:port, collection chỉ gồm [A-Za-z0-9_-]")
KEY = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.readline().strip()
BASE = f"/collections/{COLL}"
LOCK = threading.Lock()
CREATED: set[str] = set()
INFLIGHT = 0


def write_atomic(path: str, text: str) -> None:
    tmp = f"{path}.tmp{threading.get_ident()}"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def set_inflight(delta: int) -> None:
    global INFLIGHT
    with LOCK:
        INFLIGHT += delta
        write_atomic(os.path.join(STATE, "inflight"), str(INFLIGHT))


def classify(method: str, raw: str) -> str | None:
    path, sep, _ = raw.partition("?")
    if sep:
        return None
    snap = re.fullmatch(re.escape(BASE) + "/snapshots/(" + NAME + ")", path)
    if method == "GET" and (path in ("/", BASE, BASE + "/snapshots") or snap):
        return "download" if snap else "read"
    if method == "POST" and path in (BASE + "/points/scroll", BASE + "/points", BASE + "/points/count"):
        return "read"
    if method == "POST" and path == BASE + "/snapshots":
        return "create"
    if method == "DELETE" and snap:
        with LOCK:
            return "delete" if snap.group(1) in CREATED else None
    return None


def upstream(method: str, path: str, body: bytes | None, timeout: float = 3600):
    c = http.client.HTTPConnection(UP.hostname, UP.port or 80, timeout=timeout)
    h = {"Content-Type": "application/json"}
    if KEY:
        h["api-key"] = KEY
    c.request(method, path, body=body, headers=h)
    return c, c.getresponse()


def points_count() -> int | None:
    try:
        c, r = upstream("GET", BASE, None, 120)
        n = json.loads(r.read())["result"]["points_count"]
        c.close()
        return n
    except Exception:
        return None


class Guard(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):  # chỉ method + path, không header
        with open(os.path.join(STATE, "access.log"), "a") as f:
            f.write("%s %s\n" % (time.strftime("%F %T"), fmt % a))

    def _deny(self, method: str) -> None:
        p = "".join(ch if ch.isprintable() else "?" for ch in self.path)[:200]
        line = f"{time.strftime('%F %T')} qdrant-guard: DENIED {method} {p} (không chuyển tới Qdrant nguồn)\n"
        for f in (os.path.join(STATE, "denied.log"), ALERT):
            with open(f, "a") as fh:
                fh.write(line)
        self._reply(403, b'{"status":{"error":"forbidden by qdrant-guard-proxy"}}')

    def _reply(self, status: int, data: bytes, ctype: str = "application/json") -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass  # client đã ngắt; kết quả upstream đã được ghi trước khi trả

    def _any(self, method: str) -> None:
        kind = classify(method, self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if kind is None or self.headers.get("Transfer-Encoding") or n > 1 << 20:
            return self._deny(method)
        body = self.rfile.read(n) if n else None
        set_inflight(+1)
        try:
            before = points_count() if kind == "create" else None
            c, r = upstream(method, self.path, body)
            if kind == "download":   # snapshot vài GB: chuyển dần, không giữ trong RAM
                try:
                    self.send_response(r.status)
                    for k in ("Content-Type", "Content-Length"):
                        if r.getheader(k):
                            self.send_header(k, r.getheader(k))
                    self.end_headers()
                    while chunk := r.read(1 << 20):
                        self.wfile.write(chunk)
                except OSError:
                    pass
                c.close()
                return
            data = r.read()
            c.close()
            if kind == "create" and r.status == 200:
                name = json.loads(data)["result"]["name"]
                if re.fullmatch(NAME, name):
                    with LOCK:
                        CREATED.add(name)
                        with open(os.path.join(STATE, "created.jsonl"), "a") as f:
                            f.write(json.dumps({"name": name, "points_before": before, "points_after": points_count()}) + "\n")
            self._reply(r.status, data, r.getheader("Content-Type") or "application/json")
        except Exception as e:  # upstream lỗi: báo 502, không đoán
            self._reply(502, json.dumps({"status": {"error": type(e).__name__}}).encode())
        finally:
            set_inflight(-1)

    def do_GET(self): self._any("GET")
    def do_POST(self): self._any("POST")
    def do_DELETE(self): self._any("DELETE")
    def do_PUT(self): self._any("PUT")
    def do_PATCH(self): self._any("PATCH")
    def do_HEAD(self): self._any("HEAD")
    def do_OPTIONS(self): self._any("OPTIONS")


def main() -> None:
    os.makedirs(STATE, exist_ok=True)
    set_inflight(0)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Guard)
    srv.daemon_threads = True
    write_atomic(os.path.join(STATE, "port"), str(srv.server_address[1]))
    srv.serve_forever()


if __name__ == "__main__":
    main()
