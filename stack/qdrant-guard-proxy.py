#!/usr/bin/env python3
"""Proxy chặn ghi đứng giữa tunnel packtest và Qdrant NGUỒN (prod). Chỉ stdlib.

    qdrant-guard-proxy.py --upstream http://127.0.0.1:6333 --collection C --state DIR --alert FILE \
        --parent-pid PID --max-secs N --protect BEFORE.json [--drain-secs 600] [--client-timeout 120] [--max-resp-bytes 268435456]   (khoá: stdin)

Nghe 127.0.0.1:<cổng ngẫu nhiên> (ghi vào DIR/port). Chỉ cho đúng collection C, không query string:
  GET  /, /collections/C, /collections/C/snapshots, /collections/C/snapshots/<tên>   (đọc; tải snapshot được stream)
  POST /collections/C/points/scroll (limit <= 10000; <= 1000 nếu kèm vector) | /points (<= 1000 id) | /points/count
  POST /collections/C/snapshots, body rỗng, ĐÚNG MỘT lần mỗi lần chạy (tên ghi vào DIR/created.jsonl)
  DELETE /collections/C/snapshots/<tên>  chỉ khi <tên> do chính proxy này thấy được tạo
Mọi thứ khác: 403, ghi DIR/denied.log và FILE (ALERT), không tới upstream.
Lệnh gọi upstream chạy tới hết dù client ngắt. DIR/inflight = số yêu cầu đang chạy. Tối đa 16 kết nối cùng lúc.
Proxy tự kết thúc khi tiến trình cha chết (kể cả kill -9), khi hết --max-secs, hoặc khi nhận SIGTERM: ngừng nhận yêu cầu,
chờ yêu cầu đang chạy (tối đa --drain-secs), XOÁ các snapshot đã ghi tên trên upstream, rồi thoát.
Khoá (nếu có) do proxy gắn vào upstream; header api-key của client bị bỏ. Không in khoá.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import signal
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = r"[A-Za-z0-9_][A-Za-z0-9._-]*"
MAX_BODY = 1 << 20
MAX_CONN = 16


def arg(name: str, default: str | None = None) -> str:
    a = sys.argv[1:]
    if name in a and a.index(name) + 1 < len(a):
        return a[a.index(name) + 1]
    if default is None:
        sys.exit(f"usage: {sys.argv[0]} --upstream URL --collection C --state DIR --alert FILE --parent-pid PID --max-secs N")
    return default


UP = urllib.parse.urlsplit(arg("--upstream"))
COLL, STATE, ALERT = arg("--collection"), arg("--state"), arg("--alert")
PARENT, MAX_SECS = int(arg("--parent-pid")), int(arg("--max-secs"))
DRAIN, CLIENT_TIMEOUT = int(arg("--drain-secs", "600")), float(arg("--client-timeout", "120"))
MAX_RESP = int(arg("--max-resp-bytes", str(256 << 20)))   # trừ tải snapshot (stream)
if UP.scheme != "http" or not UP.hostname or not re.fullmatch(r"[A-Za-z0-9_-]+", COLL) or MAX_SECS <= 0:
    sys.exit("qdrant-guard: upstream phải là http://host:port, collection chỉ gồm [A-Za-z0-9_-], --max-secs > 0")
try:   # snapshot có sẵn trước khi chạy: không bao giờ ghi tên (nên không bao giờ xoá). Không đọc được thì không chạy.
    PROTECT = set(json.load(open(arg("--protect"))))
    assert all(isinstance(x, str) for x in PROTECT)
except Exception:
    sys.exit("qdrant-guard: --protect phải là tệp JSON danh sách tên snapshot có sẵn; không chạy")
KEY = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.readline().strip()
BASE = f"/collections/{COLL}"
LOCK = threading.Lock()
CREATED: list[str] = []
CREATE_USED = False
INFLIGHT = 0
CLOSING = threading.Event()
STOP = threading.Event()


def write_atomic(path: str, text: str) -> None:
    tmp = f"{path}.tmp{threading.get_ident()}"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def alert(msg: str) -> None:
    line = f"{time.strftime('%F %T')} qdrant-guard: {msg}\n"
    for f in (os.path.join(STATE, "denied.log"), ALERT):
        with open(f, "a") as fh:
            fh.write(line)


def set_inflight(delta: int) -> None:
    global INFLIGHT
    with LOCK:
        INFLIGHT += delta
        write_atomic(os.path.join(STATE, "inflight"), str(INFLIGHT))


def body_ok(path: str, body: bytes) -> bool:
    """Giới hạn kích thước truy vấn đọc để không kéo cả collection về một lần."""
    try:
        d = json.loads(body) if body else {}
    except ValueError:
        return False
    if not isinstance(d, dict):
        return False
    if path.endswith("/points/scroll"):
        lim = d.get("limit", 10)
        vec = any(d.get(k) not in (None, False) for k in ("with_vector", "with_vectors"))   # mọi giá trị khác false = có vector
        return isinstance(lim, int) and 0 < lim <= 10000 and not (vec and lim > 1000)
    if path.endswith("/points"):
        ids = d.get("ids")
        return isinstance(ids, list) and 0 < len(ids) <= 1000
    return True   # /points/count


def classify(method: str, raw: str, body: bytes) -> str | None:
    global CREATE_USED
    path, sep, _ = raw.partition("?")
    if sep:
        return None
    snap = re.fullmatch(re.escape(BASE) + "/snapshots/(" + NAME + ")", path)
    if method == "GET" and (path in ("/", BASE, BASE + "/snapshots") or snap):
        return "download" if snap else "read"
    if method == "POST" and path in (BASE + "/points/scroll", BASE + "/points", BASE + "/points/count"):
        return "read" if body_ok(path, body) else None
    if method == "POST" and path == BASE + "/snapshots" and not body:
        with LOCK:   # đúng một lần tạo mỗi lần chạy (pack.sh tạo một), kể cả khi gọi đồng thời
            if CREATE_USED:
                return None
            CREATE_USED = True
            return "create"
    if method == "DELETE" and snap:
        with LOCK:
            return "delete" if snap.group(1) in CREATED else None
    return None


def upstream(method: str, path: str, body: bytes | None, timeout: float = 120):
    c = http.client.HTTPConnection(UP.hostname, UP.port or 80, timeout=timeout)
    h = {"Content-Type": "application/json"}
    if KEY:
        h["api-key"] = KEY
    c.request(method, path, body=body, headers=h)
    return c, c.getresponse()


def points_count() -> int | None:
    try:
        c, r = upstream("GET", BASE, None)
        n = json.loads(r.read(MAX_BODY))["result"]["points_count"]
        c.close()
        return n
    except Exception:
        return None


class Guard(BaseHTTPRequestHandler):
    def version_string(self):  # không lộ phiên bản Python
        return "qdrant-guard"
    timeout = CLIENT_TIMEOUT   # socket client: chặn kết nối treo

    def log_message(self, fmt, *a):  # chỉ method + path, không header
        with open(os.path.join(STATE, "access.log"), "a") as f:
            f.write("%s %s\n" % (time.strftime("%F %T"), fmt % a))

    def _deny(self, method: str, why: str = "") -> None:
        p = "".join(ch if ch.isprintable() else "?" for ch in self.path)[:200]
        alert(f"DENIED {method[:16]} {p} {why}(không chuyển tới Qdrant nguồn)")
        self.close_connection = True
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
        if CLOSING.is_set():
            self.close_connection = True
            return self._reply(503, b'{"status":{"error":"qdrant-guard closing"}}')
        if self.headers.get("Transfer-Encoding"):
            return self._deny(method, "(Transfer-Encoding) ")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 <= n <= MAX_BODY:   # không bao giờ đọc thân không giới hạn
            return self._deny(method, "(Content-Length) ")
        body = self.rfile.read(n) if n else b""
        kind = classify(method, self.path, body)
        if kind is None:
            return self._deny(method)
        set_inflight(+1)
        try:
            before = points_count() if kind == "create" else None
            c, r = upstream(method, self.path, body or None, 3600 if kind in ("create", "download") else 120)
            if kind == "download":   # snapshot vài GB: stream, không giữ trong RAM
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
            data = r.read(MAX_RESP + 1)
            c.close()
            if len(data) > MAX_RESP:
                return self._reply(502, b'{"status":{"error":"upstream response too large"}}')
            if kind == "create" and r.status == 200:
                name = (json.loads(data).get("result") or {}).get("name")
                if isinstance(name, str) and name in PROTECT:
                    alert(f"tạo snapshot trả về tên TRÙNG snapshot có sẵn {name[:100]!r}: không ghi, không bao giờ xoá; KIỂM TAY")
                elif isinstance(name, str) and re.fullmatch(NAME, name):
                    with LOCK:
                        CREATED.append(name)
                        with open(os.path.join(STATE, "created.jsonl"), "a") as f:
                            f.write(json.dumps({"name": name, "points_before": before, "points_after": points_count()}) + "\n")
                else:
                    alert(f"snapshot tạo ra có tên lạ {str(name)[:100]!r}: không ghi, không tự xoá được; KIỂM TAY Qdrant nguồn")
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
    def do_TRACE(self): self._any("TRACE")
    def do_CONNECT(self): self._any("CONNECT")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    slots = threading.BoundedSemaphore(MAX_CONN)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):   # quá MAX_CONN: đóng ngay, không tạo thread
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def parent_alive() -> bool:
    try:
        os.kill(PARENT, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def watcher() -> None:
    try:
        shutdown_logic()
    finally:   # dù logic dọn lỗi thế nào, proxy không bao giờ phục vụ tiếp
        os._exit(0)


def shutdown_logic() -> None:
    ppid0, t_end = os.getppid(), time.monotonic() + MAX_SECS
    while not STOP.is_set():
        if os.getppid() != ppid0 or not parent_alive():
            reason = "tiến trình cha đã chết"
            break
        if time.monotonic() >= t_end:
            reason = "hết --max-secs"
            break
        STOP.wait(1)
    else:
        reason = "SIGTERM"
    CLOSING.set()
    t = time.monotonic() + DRAIN
    while time.monotonic() < t:
        with LOCK:
            if INFLIGHT == 0:
                break
        time.sleep(0.5)
    with LOCK:
        left, names = INFLIGHT, list(CREATED)
    if left:
        alert(f"thoát ({reason}) khi còn {left} yêu cầu đang chạy tới Qdrant nguồn: KIỂM snapshot Qdrant nguồn bằng tay")
    log = open(os.path.join(STATE, "selfclean.log"), "a")
    for name in names:   # chỉ tên do chính proxy ghi lại (đã qua regex)
        gone = False
        for _ in range(3):
            try:
                c, r = upstream("DELETE", f"{BASE}/snapshots/{name}", None, 30)   # 3 x 2 x 30 s: dọn gọn trong dự trữ 10 phút
                r.read(MAX_BODY)
                c.close()
                c, r = upstream("GET", f"{BASE}/snapshots", None, 30)
                listed = {s.get("name") for s in json.loads(r.read(MAX_RESP))["result"]}
                c.close()
                if name not in listed:
                    gone = True
                    break
            except Exception:
                pass
            time.sleep(1)
        log.write(f"{time.strftime('%F %T')} {reason}: {name} {'gone' if gone else 'NOT DELETED'}\n")
        if not gone:
            alert(f"không xoá được snapshot {name} khi thoát ({reason}): curl -X DELETE <qdrant>{BASE}/snapshots/{name}")
    log.close()


def main() -> None:
    os.makedirs(STATE, exist_ok=True)
    set_inflight(0)
    srv = Server(("127.0.0.1", 0), Guard)
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    threading.Thread(target=watcher, daemon=True).start()
    write_atomic(os.path.join(STATE, "config.json"), json.dumps({"drain_secs": DRAIN, "max_secs": MAX_SECS, "protected": len(PROTECT)}))
    write_atomic(os.path.join(STATE, "port"), str(srv.server_address[1]))
    srv.serve_forever()


if __name__ == "__main__":
    main()
