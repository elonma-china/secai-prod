"""eval-vast.sh EVAL_DISK_GB / EVAL_MODE=packtest / `packtest` + stack/qdrant-guard-proxy.py.

Khong thue, khong cham Qdrant that: vastai/ssh/rsync/df GIA; Qdrant "prod" va Qdrant "VM" la hai server HTTP gia o hai cong khac
nhau, ghi lai moi yeu cau. Phan tu xa (REMOTE_PACKTEST_PACK/RESTORE) chay THAT voi stub pack.sh/restore-data.sh/docker/time.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import http.client
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from test_stack_eval_vast import EVAL, STACK, assert_prod_alive, box, lines_with, log, sh, stub, write_state  # noqa: F401
from test_stack_neo4j_export import PW, fake_neo4j, sample_graph

PROXY = STACK / "qdrant-guard-proxy.py"
QKEY = "QKEY_MARK_4d2e"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------ Qdrant gia (prod hoac VM), moi server mot trang thai rieng

def make_qdrant(n_points=50, snaps=None, create_delay=0.0, vec_shift=0.0, payload_tweak=False, bad_name=False, create_name="-"):
    st = {"n": n_points, "snaps": [dict(x) for x in snaps or []], "reqs": [], "seq": 0, "lock": threading.Lock()}

    def point(i):
        return {"id": i, "payload": {"cve_id": f"CVE-{i}" + ("x" if payload_tweak and i % 2 else "")},
                "vector": {"dense": [0.1 * (i % 7) + vec_shift] * 4, "bm25": {"indices": [1, i], "values": [1.0, 0.5]}}}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200, raw=None):
            b = raw if raw is not None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _do(self, m):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n)) if n else None
            st["reqs"].append((m, self.path, self.headers.get("api-key")))
            p = self.path
            if m == "GET" and p == "/":
                return self._send({"title": "qdrant"})
            if m == "GET" and p == "/collections/c":
                return self._send({"result": {"points_count": st["n"], "config": {"params": {
                    "vectors": {"dense": {"size": 4}}, "sparse_vectors": {"bm25": {"modifier": "idf"}}}},
                    "payload_schema": {"cve_id": {}}}})
            if p == "/collections/c/snapshots" and m == "GET":
                return self._send({"result": st["snaps"]})
            if p == "/collections/c/snapshots" and m == "POST":
                time.sleep(create_delay)
                with st["lock"]:
                    st["seq"] += 1
                    name = f"c-own{st['seq']}-{time.strftime('%Y-%m-%d-%H-%M-%S')}.snapshot" if not bad_name else "../evil name"
                    if create_name != "-":
                        name = create_name   # vd. None hoac ten snapshot co san
                    if isinstance(name, str) and name not in {x["name"] for x in st["snaps"]}:
                        st["snaps"].append({"name": name, "creation_time": now_iso(), "size": 4})
                return self._send({"result": {"name": name}})
            if p.startswith("/collections/c/snapshots/") and m == "GET":
                return self._send(None, raw=b"SNAPDATA" * 1000)
            if p.startswith("/collections/c/snapshots/") and m == "DELETE":
                name = p.rsplit("/", 1)[1]
                st["snaps"] = [s for s in st["snaps"] if s["name"] != name]
                return self._send({"result": True})
            if p == "/collections/c/points/scroll" and m == "POST":
                off = body.get("offset") or 1
                ids = list(range(off, min(off + body["limit"], st["n"] + 1)))
                nxt = off + body["limit"] if off + body["limit"] <= st["n"] else None
                return self._send({"result": {"points": [{"id": i} for i in ids], "next_page_offset": nxt}})
            if p == "/collections/c/points" and m == "POST":
                return self._send({"result": [point(i) for i in body["ids"] if 1 <= i <= st["n"]]})
            return self._send({"result": "ok"})  # nhung yeu cau khac van "thanh cong" de test bat duoc neu chung lot toi

        def do_GET(self): self._do("GET")
        def do_POST(self): self._do("POST")
        def do_PUT(self): self._do("PUT")
        def do_PATCH(self): self._do("PATCH")
        def do_DELETE(self): self._do("DELETE")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    st["port"], st["srv"] = srv.server_address[1], srv
    return st


def mutations(st, own: set[str] = frozenset()) -> list:
    """Moi yeu cau ghi toi Qdrant, tru tao snapshot va xoa dung snapshot cua lan chay."""
    out = []
    for m, p, _ in st["reqs"]:
        if m in ("GET",) or (m == "POST" and p in ("/collections/c/points/scroll", "/collections/c/points", "/collections/c/snapshots")):
            continue
        if m == "DELETE" and p.startswith("/collections/c/snapshots/") and p.rsplit("/", 1)[1] in own:
            continue
        out.append((m, p))
    return out


OLD = [{"name": "c-1-2026-09-04-02-31-25.snapshot", "creation_time": "2026-09-04T02:32:05", "size": 4},
       {"name": "c-1-2026-08-23-04-03-33.snapshot", "creation_time": "2026-08-23T04:04:11", "size": 4}]


@pytest.fixture
def prod():
    st = make_qdrant(snaps=OLD + [{"name": "c-recent-preexisting.snapshot", "creation_time": now_iso(), "size": 4}])
    yield st
    st["srv"].shutdown()


# ------------------------------------------------------------------ guard proxy (don vi)

class Proxy:
    def __init__(self, tmp: Path, upstream: int, key: str = "", parent: int | None = None, max_secs: int = 300, extra=(),
                 protect=None, wait_port=True):
        self.dir = tmp / "proxy"
        self.alert = tmp / "ALERT.txt"
        self.protect = tmp / "before.json"
        if protect is not False:
            self.protect.write_text(json.dumps(sorted(PRE) if protect is None else protect))
        self.p = subprocess.Popen([sys.executable, "-I", str(PROXY), "--upstream", f"http://127.0.0.1:{upstream}",
                                   "--collection", "c", "--state", str(self.dir), "--alert", str(self.alert),
                                   "--parent-pid", str(parent or os.getpid()), "--max-secs", str(max_secs), "--drain-secs", "20", "--protect", str(self.protect), *extra],
                                  stdin=subprocess.PIPE, text=True)
        self.p.stdin.write(key + "\n")
        self.p.stdin.close()
        if not wait_port:
            return
        for _ in range(100):
            if (self.dir / "port").exists():
                break
            time.sleep(0.05)
        self.port = int((self.dir / "port").read_text())

    def req(self, method, path, body=None, timeout=10, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        c.request(method, path, body=None if body is None else json.dumps(body), headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def created(self) -> list[dict]:
        f = self.dir / "created.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def stop(self):
        if self.p.poll() is None:
            self.p.terminate()
            self.p.wait(30)


@pytest.fixture
def proxy(tmp_path, prod):
    px = Proxy(tmp_path, prod["port"], QKEY)
    yield px
    px.stop()


@pytest.mark.parametrize("method,path,body", [
    ("PUT", "/collections/c/snapshots/recover", {"location": "file:///x"}),
    ("POST", "/collections/c/snapshots/recover", {"location": "file:///x"}),
    ("POST", "/collections/c/snapshots/upload", None),
    ("PUT", "/collections/c/points", {"points": []}),
    ("POST", "/collections/c/points/delete", {"points": [1]}),
    ("POST", "/collections/c/points/payload", {"payload": {}}),
    ("PATCH", "/collections/c", {}),
    ("DELETE", "/collections/c", None),
    ("PUT", "/collections/c", {}),
    ("DELETE", "/collections/c/snapshots/c-1-2026-09-04-02-31-25.snapshot", None),
    ("DELETE", "/collections/c/snapshots/c-recent-preexisting.snapshot", None),
    ("DELETE", "/collections/c/snapshots/..", None),
    ("GET", "/collections/other", None),
    ("POST", "/collections/other/snapshots", None),
    ("POST", "/collections/other/points/scroll", {"limit": 1}),
    ("GET", "/collections", None),
    ("POST", "/snapshots", None),
    ("GET", "/cluster", None),
    ("POST", "/collections/c/snapshots?wait=false", None),
    ("GET", "/collections/c?x=1", None),
    ("GET", "/collections/c/../other", None),
    ("POST", "/collections/c/points/search", {"vector": [0]}),
    ("TRACE", "/collections/c", None),
    ("CONNECT", "/collections/c", None),
    ("POST", "/collections/c/points/scroll", {"limit": 10001}),
    ("POST", "/collections/c/points/scroll", {"limit": 1001, "with_vector": True}),
    ("POST", "/collections/c/points/scroll", {"limit": 1001, "with_vectors": True}),
    ("POST", "/collections/c/points/scroll", {"limit": 1001, "with_vector": ["dense"]}),
    ("POST", "/collections/c/points/scroll", {"limit": 1001, "with_vector": {"x": 1}}),
    ("POST", "/collections/c/points/scroll", {"limit": 1001, "with_vector": "dense"}),
    ("POST", "/collections/c/points/scroll", [1]),
    ("POST", "/collections/c/points", {"ids": list(range(1001))}),
    ("POST", "/collections/c/points", {"ids": []}),
    ("POST", "/collections/c/snapshots", {"x": 1}),
])
def test_proxy_forbids_everything_off_the_allowlist(proxy, prod, method, path, body):
    code, _ = proxy.req(method, path, body)
    assert code == 403
    assert prod["reqs"] == []  # khong bao gio toi upstream
    assert "DENIED" in proxy.alert.read_text() and method in (proxy.dir / "denied.log").read_text()


def test_proxy_allows_reads_and_injects_key_replacing_client_header(proxy, prod):
    for m, p, b in (("GET", "/", None), ("GET", "/collections/c", None), ("GET", "/collections/c/snapshots", None),
                    ("POST", "/collections/c/points/scroll", {"limit": 5}), ("POST", "/collections/c/points", {"ids": [1]}),
                    ("GET", "/collections/c/snapshots/c-1-2026-09-04-02-31-25.snapshot", None)):
        code, _ = proxy.req(m, p, b, headers={"api-key": "CLIENT_FORGED"})
        assert code == 200, (m, p)
    assert all(k == QKEY for _, _, k in prod["reqs"])
    assert mutations(prod) == [] and not (proxy.dir / "denied.log").exists()


def test_proxy_delete_only_snapshot_it_saw_created(proxy, prod):
    code, data = proxy.req("POST", "/collections/c/snapshots")
    name = json.loads(data)["result"]["name"]
    rec = proxy.created()
    assert code == 200 and [r["name"] for r in rec] == [name]
    assert rec[0]["points_before"] == rec[0]["points_after"] == 50
    assert proxy.req("DELETE", "/collections/c/snapshots/c-1-2026-08-23-04-03-33.snapshot")[0] == 403
    assert proxy.req("DELETE", f"/collections/c/snapshots/{name}")[0] == 200
    assert mutations(prod, {name}) == []
    assert {s["name"] for s in OLD} <= {s["name"] for s in prod["snaps"]}


def test_proxy_finishes_and_records_create_even_if_client_disconnects(tmp_path):
    slow = make_qdrant(create_delay=1.5)
    px = Proxy(tmp_path, slow["port"])
    try:
        with pytest.raises(TimeoutError):
            px.req("POST", "/collections/c/snapshots", timeout=0.3)
        for _ in range(60):
            if px.created() and (px.dir / "inflight").read_text() == "0":
                break
            time.sleep(0.1)
        rec = px.created()
        assert len(rec) == 1 and rec[0]["name"] in {s["name"] for s in slow["snaps"]}
        assert (px.dir / "inflight").read_text() == "0"
    finally:
        px.stop()
        slow["srv"].shutdown()


# ------------------------------------------------------------------ up: EVAL_DISK_GB / EVAL_MODE

@pytest.mark.parametrize("bad", ["19", "601", "abc", "100.5", "0", "-5"])
def test_up_rejects_bad_disk_size_before_any_vastai_call(box, bad):  # noqa: F811
    r = sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_DISK_GB": bad})
    assert r.returncode != 0 and "EVAL_DISK_GB" in r.stderr and not log(box, "vastai")


def test_up_disk_size_drives_query_storage_filter_and_create(box):  # noqa: F811
    (box.fake / "offers.json").write_text(json.dumps([
        {"id": 2, "dph_total": 0.20, "cpu_ram": 65536, "disk_space": 120, "inet_down": 900, "reliability2": 0.99},
        {"id": 5, "dph_total": 0.28, "cpu_ram": 65536, "disk_space": 400, "inet_down": 900, "reliability2": 0.99}]))
    r = sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_DISK_GB": "150"})
    assert r.returncode == 0, r.stderr
    q = lines_with(box, "vastai", "search")[0]
    assert "disk_space>=150" in q and "--storage 150" in q
    assert "create instance 5 " in r.stdout and "--disk 150 " in r.stdout  # offer 2 (120 GB) bi loc o client


def test_up_disk_size_does_not_relax_budget(box):  # noqa: F811
    r = sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_DISK_GB": "300", "EVAL_MAX_DPH": "0.40"})
    assert r.returncode != 0 and not log(box, "vastai")


def test_up_rejects_unknown_mode(box):  # noqa: F811
    r = sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_MODE": "packtest;x"})
    assert r.returncode != 0 and not log(box, "vastai")


def test_up_packtest_mode_skips_env_data_and_image_builds(box):  # noqa: F811
    env = {k: v for k, v in box.env.items() if k not in ("EVAL_ENV_FILE", "EVAL_DATA_PKG")}
    r = subprocess.run(["bash", str(EVAL), "up"], env={**env, "EVAL_MODE": "packtest", "EVAL_DISK_GB": "150"},
                       text=True, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr + r.stdout
    st = json.loads((box.sdir / "state.json").read_text())
    assert st["mode"] == "packtest" and st["disk_gb"] == 150 and st["id"] == "777"
    assert any("--disk 150" in x for x in lines_with(box, "vastai", "create"))
    assert log(box, "rsync") == [] and len(log(box, "scp")) == 1  # khong day goi du lieu; chi git archive
    assert ".env" not in "\n".join(log(box, "ssh"))
    script = (box.fake / "ssh.stdin").read_text()
    assert "docker build" not in script and "QWEN" not in script
    for needle in ("get.docker.com", "zstd", "rsync", "docker pull -q qdrant/qdrant:v1.10.1", "docker pull -q neo4j:5.20-community"):
        assert needle in script
    assert len(log(box, "systemd-run")) == 2
    assert "cảnh báo" not in r.stdout.lower()


def test_up_full_mode_still_requires_env_and_pkg(box):  # noqa: F811
    env = {k: v for k, v in box.env.items() if k != "EVAL_DATA_PKG"}
    r = subprocess.run(["bash", str(EVAL), "up"], env=env, text=True, capture_output=True, timeout=60)
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")


# ------------------------------------------------------------------ packtest (cuc bo), prod = server gia, tunnel+pack gia qua proxy that

FAKE_SSH = r'''#!/usr/bin/env python3
# ssh gia. -N: tunnel (ghi cong proxy, ngu tới khi bi giet). 'cat >/dev/null': do bang thong. PHASE=pack: gia lap pack.sh
# goi QUA PROXY theo pack_mode; PHASE=restore: rc tu restore_rc.
import json, os, sys, time, urllib.request
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]; cmd = " ".join(a)
open(d + "/ssh.log", "a").write(cmd + "\n")
def rd(n, default=""):
    try: return open(f"{d}/{n}").read().strip()
    except OSError: return default
if "-N" in a:
    open(d + "/tunnel.pid", "w").write(str(os.getpid()))
    open(d + "/pport", "w").write(a[a.index("-R") + 1].rsplit(":", 1)[1])
    time.sleep(300); sys.exit(0)
data = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
open(d + "/ssh.stdin", "ab").write(data if len(data) < 4096 else b"<blob %d>" % len(data))
if "cat >/dev/null" in cmd and rd("bw_sleep"): time.sleep(float(rd("bw_sleep")))
def call(port, method, path, timeout=10):
    rq = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"" if method == "POST" else None)
    try: return urllib.request.urlopen(rq, timeout=timeout).status, None
    except urllib.error.HTTPError as e: return e.code, None
if "PHASE=pack" in cmd:
    px, prodp = rd("pport"), rd("prod_port")
    modes = rd("pack_mode", "create download delete_own").split()
    if "foreign" in modes:   # ai khac tao snapshot TRUC TIEP tren prod trong luc chay
        call(prodp, "POST", "/collections/c/snapshots")
    if "evil" in modes:
        for m, p in (("PUT", "/collections/c/snapshots/recover"), ("DELETE", "/collections/c/snapshots/c-recent-preexisting.snapshot"),
                     ("DELETE", "/collections/c"), ("PATCH", "/collections/c")):
            open(d + "/evil.codes", "a").write(f"{m} {p} {call(px, m, p)[0]}\n")
    if "inflight" in modes:  # tunnel chet giua luc tao snapshot
        try: call(px, "POST", "/collections/c/snapshots", timeout=0.3)
        except Exception: pass
        sys.exit(255)
    if "create" in modes:
        rq = urllib.request.Request(f"http://127.0.0.1:{px}/collections/c/snapshots", method="POST", data=b"")
        name = json.load(urllib.request.urlopen(rq, timeout=10))["result"]["name"]
        if "kill9" in modes:
            gp = int(open(f"/proc/{os.getppid()}/stat").read().rsplit(")", 1)[1].split()[1])
            os.kill(gp, 9); sys.exit(0)
        if "download" in modes: call(px, "GET", f"/collections/c/snapshots/{name}")
        if "delete_own" in modes: call(px, "DELETE", f"/collections/c/snapshots/{name}")
    sys.exit(int(rd("pack_rc", "0")))
if "PHASE=restore" in cmd: sys.exit(int(rd("restore_rc", "0")))
sys.exit(int(rd("ssh_rc", "0")))
'''


# python3 that, for every call, records whether NEO4J_PASSWORD was in its env, its argv, and the exporter's env names
FAKE_PY = """#!/bin/bash
printf '%s\\t%s\\n' "${NEO4J_PASSWORD+PW}" "$*" >> "$FAKE_DIR/python3.log"
case "$*" in *neo4j-export.py*) env | cut -d= -f1 | sort > "$FAKE_DIR/exporter.envnames" ;; esac
exec /usr/bin/python3 "$@"
"""


def setup_pt(box, prod, neo):  # noqa: F811
    binp = box.tmp / "bin"
    (binp / "ssh").write_text(FAKE_SSH)
    (binp / "ssh").chmod(0o755)
    stub(binp / "df", 'echo Avail; cat "$FAKE_DIR/df_avail" 2>/dev/null || echo 107374182400')
    (binp / "python3").write_text(FAKE_PY)
    (binp / "python3").chmod(0o755)
    (box.fake / "prod_port").write_text(str(prod["port"]))
    pwf = box.tmp / "neo4j.pw"
    pwf.write_text(PW + "\n")
    pwf.chmod(0o600)
    src = box.tmp / "src"
    for p in ("exploitdb/a", "poc/store/x", "poc-sources/y", "../exploitdb-mcp-server/z"):
        (src / p).parent.mkdir(parents=True, exist_ok=True)
        (src / p).write_text("x")  # mcp: thu muc anh em nhu mac dinh <src>/../exploitdb-mcp-server
    c = sqlite3.connect(src / "poc/catalog.db")
    c.execute("CREATE TABLE t (i)")
    c.commit()
    c.close()
    now = int(time.time())
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222, mode="packtest", created_at=now, deadline=now + 3 * 3600)
    box.env.update({"PACKTEST_SRC_ROOT": str(src), "PACKTEST_QDRANT_COLLECTION": "c", "PACKTEST_BW_MB": "1",
                    "PACKTEST_QDRANT_URL": f"http://127.0.0.1:{prod['port']}", "PACKTEST_DRAIN_SECS": "20",
                    "PACKTEST_NEO4J_URL": neo[0], "PACKTEST_NEO4J_PASSWORD_FILE": str(pwf)})
    box.src, box.prod, box.neo, box.pwf = src, prod, neo[1], pwf
    return box


@pytest.fixture
def neo():
    with fake_neo4j(sample_graph()) as n:
        yield n


@pytest.fixture
def pt(box, prod, neo):  # noqa: F811
    return setup_pt(box, prod, neo)


def snap_names(st) -> set[str]:
    return {s["name"] for s in st["snaps"]}


def own_names(b) -> set[str]:
    f = b.sdir / "packtest/proxy/created.jsonl"
    return {json.loads(x)["name"] for x in f.read_text().splitlines()} if f.exists() else set()


def tunnel_dead(b) -> bool:
    try:
        os.kill(int((b.fake / "tunnel.pid").read_text()), 0)
    except ProcessLookupError:
        return True
    return False


PRE = {s["name"] for s in OLD} | {"c-recent-preexisting.snapshot"}


def test_packtest_requires_packtest_state(pt):
    write_state(pt, ssh_host="1.2.3.4", ssh_port=2222, deadline=int(time.time()) + 3600)  # khong co mode
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and log(pt, "ssh") == [] and pt.prod["reqs"] == []


def test_packtest_refuses_low_local_disk_before_anything(pt):
    (pt.fake / "df_avail").write_text(str(40 << 30))
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and "45" in r.stderr
    assert log(pt, "ssh") == [] and log(pt, "rsync") == [] and pt.prod["reqs"] == []


def test_packtest_refuses_missing_source(pt):
    (pt.src / "poc/catalog.db").unlink()
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and "catalog.db" in r.stderr and log(pt, "ssh") == [] and pt.neo["reqs"] == []


@pytest.mark.parametrize("how", ["unset", "missing", "0644", "empty"])
def test_packtest_neo4j_password_file_required_0600_before_anything(pt, how):
    extra = {}
    if how == "unset":
        del pt.env["PACKTEST_NEO4J_PASSWORD_FILE"]
    elif how == "missing":
        extra["PACKTEST_NEO4J_PASSWORD_FILE"] = str(pt.tmp / "nope")
    elif how == "0644":
        pt.pwf.chmod(0o644)
    else:
        pt.pwf.write_text("\n")
    r = sh(EVAL, "packtest", b=pt, extra=extra)
    assert r.returncode != 0 and "PACKTEST_NEO4J_PASSWORD_FILE" in r.stderr and PW not in r.stderr + r.stdout
    assert log(pt, "ssh") == [] and log(pt, "rsync") == [] and pt.prod["reqs"] == [] and pt.neo["reqs"] == []
    assert not (pt.sdir / "packtest").exists()


def test_packtest_neo4j_url_must_be_local(pt):
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_NEO4J_URL": "http://10.0.0.5:7474"})
    assert r.returncode != 0 and "PACKTEST_NEO4J_URL" in r.stderr and pt.neo["reqs"] == [] and log(pt, "ssh") == []


# rsync gia chep lai thu muc export/image da gui (ban cuc bo bi xoa khi packtest ket thuc)
SHIP_RSYNC = """printf '%s\\n' "$*" >> "$FAKE_DIR/rsync.log"
for a; do case "$a" in /*/neo4j-export/) cp -r "$a" "$FAKE_DIR/shipped-neo4j" ;; /*/images-pkg/images/) cp -r "$a" "$FAKE_DIR/shipped-images" ;; esac; done"""


def test_packtest_exports_neo4j_locally_with_password_in_env_only(pt):
    stub(pt.tmp / "bin/rsync", SHIP_RSYNC)
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode == 0, r.stderr + r.stdout
    exp = pt.sdir / "packtest/neo4j-export"
    assert json.loads((pt.fake / "shipped-neo4j/counts.json").read_text())["nodes"] == {"CVE": 3, "CWE": 2}
    assert not exp.exists()   # do lieu prod khong nam lai tren dia may dev
    # password reached the exporter (Basic auth), through env only
    want = "Basic " + base64.b64encode(f"neo4j:{PW}".encode()).decode()
    assert pt.neo["reqs"] and {a for a, _ in pt.neo["reqs"]} == {want}
    py = (pt.fake / "python3.log").read_text().splitlines()
    exp_calls = [x for x in py if "neo4j-export.py" in x]
    assert len(exp_calls) == 1 and exp_calls[0].startswith("PW\t") and "-I " in exp_calls[0] and str(exp) in exp_calls[0]
    assert all(not x.startswith("PW") for x in py if "neo4j-export.py" not in x)   # no other child sees it
    assert {"NEO4J_PASSWORD", "NEO4J_URL"} <= set((pt.fake / "exporter.envnames").read_text().split())
    # never in argv, output, logs, the VM or files under the eval dir
    assert PW not in "\n".join(py + log(pt, "ssh") + log(pt, "rsync")) and PW not in r.stdout + r.stderr
    assert PW not in (pt.fake / "ssh.stdin").read_text()
    for f in pt.sdir.rglob("*"):
        if f.is_file():
            assert PW.encode() not in f.read_bytes(), f
    rs = log(pt, "rsync")
    assert any(f"{exp}/ " in x and x.endswith(":/srv/src-root/neo4j-export/") for x in rs)
    assert not any("images-pkg" in x for x in rs)
    rest = next(x for x in log(pt, "ssh") if "PHASE=restore" in x)
    assert "GATEWAY_IMAGE=''" in rest and "NEO4J_PASSWORD" not in rest and "NEO4J_EXPECT" not in rest


def test_packtest_neo4j_export_drift_stops_before_transfer(box, prod):  # noqa: F811
    g = sample_graph()
    g["drift"] = {"CVE": 1}
    with fake_neo4j(g) as n:
        b = setup_pt(box, prod, n)
        r = sh(EVAL, "packtest", b=b)
    assert r.returncode != 0 and "neo4j-export" in r.stderr and PW not in r.stderr
    assert not any("cat >/dev/null" in x for x in log(b, "ssh")) and log(b, "rsync") == []
    assert not (b.sdir / "packtest/neo4j-export").exists()


FAKE_DOCKER_IMAGES = """echo "$*" >> "$FAKE_DIR/docker.log"
case "$*" in
  *"config --images"*) echo reg/gw:1 ;;
  "image inspect --format {{json .RootFS.Layers}} reg/gw:1") echo '["sha256:l0"]' ;;
  "image inspect"*) exit 1 ;;
  save*) head -c 3000000 /dev/urandom ;;
esac"""


def test_packtest_images_env_packs_ships_and_counts_in_estimate(pt):
    stub(pt.tmp / "bin/docker", FAKE_DOCKER_IMAGES)
    stub(pt.tmp / "bin/rsync", SHIP_RSYNC)
    ienv = pt.tmp / "img.env"
    ienv.write_text("GATEWAY_IMAGE=old:0\nGATEWAY_IMAGE='reg/gw:1'\n")
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_IMAGES_ENV": str(ienv)})
    assert r.returncode == 0, r.stderr + r.stdout
    imgs = pt.sdir / "packtest/images-pkg/images"
    assert not (pt.sdir / "packtest/images-pkg").exists() and not (pt.sdir / "packtest/neo4j-export").exists()
    shipped = pt.fake / "shipped-images"
    assert (shipped / "images.txt").read_text() == "reg/gw:1 layers-sha256:" + hashlib.sha256(b'["sha256:l0"]').hexdigest() + "\n"
    rs = log(pt, "rsync")
    assert len(rs) == 5 and any(f"{imgs}/ " in x and x.endswith(":/srv/images-pkg/images/") for x in rs)
    assert any("mkdir -p" in x and "/srv/images-pkg" in x for x in log(pt, "ssh"))
    rest = next(x for x in log(pt, "ssh") if "PHASE=restore" in x)
    assert "GATEWAY_IMAGE='reg/gw:1'" in rest
    nb = int(re.search(r"nguồn (\d+) B", r.stdout).group(1))
    exp = sum(f.stat().st_size for f in (pt.fake / "shipped-neo4j").rglob("*") if f.is_file())
    assert nb >= (shipped / "images.tar.zst").stat().st_size + exp and nb > 3_000_000
    # image dong truoc export Neo4j: ref hong dung truoc khi doc Neo4j nguon
    py = (pt.fake / "python3.log").read_text()
    assert (pt.fake / "docker.log").exists() and "neo4j-export.py" in py


def test_packtest_bad_images_env_fails_before_neo4j_export_and_transfer(pt):
    stub(pt.tmp / "bin/docker", FAKE_DOCKER_IMAGES.replace("echo reg/gw:1 ;;", "echo reg/gw:1; echo reg/gone:9 ;;"))
    ienv = pt.tmp / "img.env"
    ienv.write_text("GATEWAY_IMAGE=reg/gw:1\n")
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_IMAGES_ENV": str(ienv)})
    assert r.returncode != 0 and "pack.sh images" in r.stderr, r.stderr
    assert "reg/gone:9" in (pt.sdir / "packtest/pack-images.log").read_text()
    assert pt.neo["reqs"] == [] and log(pt, "ssh") == [] and log(pt, "rsync") == []
    assert not (pt.sdir / "packtest/images-pkg").exists()


def test_packtest_images_env_needs_docker_and_zstd(pt):
    sysbin = pt.tmp / "sysbin"
    sysbin.mkdir()
    for f in Path("/usr/bin").iterdir():
        if f.name not in ("docker", "zstd"):
            (sysbin / f.name).symlink_to(f)
    ienv = pt.tmp / "img.env"
    ienv.write_text("GATEWAY_IMAGE=reg/gw:1\n")
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_IMAGES_ENV": str(ienv), "PATH": f"{pt.tmp / 'bin'}:{sysbin}"})
    assert r.returncode != 0 and "'docker'" in r.stderr, r.stderr
    assert pt.neo["reqs"] == [] and log(pt, "ssh") == []


@pytest.mark.parametrize("line", ["GATEWAY_IMAGE=", "GATEWAY_IMAGE=a b;rm", "X=1"])
def test_packtest_images_env_needs_sane_gateway_image(pt, line):
    ienv = pt.tmp / "img.env"
    ienv.write_text(line + "\n")
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_IMAGES_ENV": str(ienv)})
    assert r.returncode != 0 and "GATEWAY_IMAGE" in r.stderr and log(pt, "ssh") == [] and pt.neo["reqs"] == []


def test_packtest_fails_closed_when_prod_qdrant_unreachable(pt):
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_QDRANT_URL": "http://127.0.0.1:9"})
    assert r.returncode != 0 and log(pt, "ssh") == [] and log(pt, "rsync") == []


@pytest.mark.parametrize("url", ["http://10.0.0.5:6333", "https://127.0.0.1:6333", "http://127.0.0.1:6333/x", "http://127.0.0.1"])
def test_packtest_rejects_non_local_qdrant_url(pt, url):
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_QDRANT_URL": url})
    assert r.returncode != 0 and log(pt, "ssh") == [] and pt.prod["reqs"] == []


def test_packtest_bandwidth_floor_refuses_before_transfer(pt):
    (pt.fake / "bw_sleep").write_text("1")  # 1 MB trong ~1 s < 8 MB/s
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and "BĂNG THÔNG" in r.stderr
    assert log(pt, "rsync") == [] and not (pt.fake / "tunnel.pid").exists()
    assert "băng thông" in (pt.sdir / "ALERT.txt").read_text()
    assert not (pt.sdir / "packtest/neo4j-export").exists()   # don ca khi loi
    assert mutations(pt.prod) == []


def test_packtest_bandwidth_required_time_uses_snapshot_estimate_and_deadline(pt):
    pt.prod["snaps"][0]["size"] = 1 << 50  # snapshot cu 1 PB: (nguon + snapshot)/toc do vuot thoi gian con lai
    pt.env["PACKTEST_BW_MB"] = "40"  # khong ngu: toc do do duoc vuot san 8 MB/s
    r = sh(EVAL, "packtest", b=pt)
    assert "BĂNG THÔNG" in r.stderr and "MB/s: cần" in r.stderr
    assert r.returncode != 0 and "cần" in r.stderr and log(pt, "rsync") == []
    pt.prod["snaps"][0]["size"] = 4
    now = int(time.time())  # con 40 phut truoc deadline-10: khong du 45 phut pack/restore
    write_state(pt, ssh_host="1.2.3.4", ssh_port=2222, mode="packtest", created_at=now, deadline=now + 50 * 60)
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and log(pt, "rsync") == []
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_PACK_RESTORE_MIN": "10"})
    assert r.returncode == 0, r.stderr


def test_packtest_full_flow_tunnel_to_proxy_and_cleans_only_recorded_snapshot(pt):
    (pt.fake / "pack_mode").write_text("foreign create download")  # pack khong xoa duoc snapshot cua no
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode == 0, r.stderr + r.stdout
    ssh = log(pt, "ssh")
    tun = [x for x in ssh if "-N" in x.split()]
    pport = (pt.fake / "pport").read_text()
    assert len(tun) == 1 and f"-R 127.0.0.1:16333:127.0.0.1:{pport}" in tun[0] and "ExitOnForwardFailure=yes" in tun[0]
    assert pport != str(pt.prod["port"])  # tunnel KHONG tro thang vao prod
    assert tunnel_dead(pt)
    # tunnel dong truoc pha restore
    i_pack = next(i for i, x in enumerate(ssh) if "PHASE=pack" in x)
    i_rest = next(i for i, x in enumerate(ssh) if "PHASE=restore" in x)
    assert i_pack < i_rest and "PROD_CLEAN=pass" in ssh[i_rest]
    rs = log(pt, "rsync")
    assert len(rs) == 4 and all("--no-links" in x and " -z" not in x for x in rs)
    assert any("--exclude /poc/catalog.db*" in x for x in rs) and any("packtest/catalog.db" in x for x in rs)
    assert not (pt.sdir / "packtest/catalog.db").exists()
    own = own_names(pt)
    assert len(own) == 1
    left = snap_names(pt.prod)
    assert PRE <= left and not (own & left)
    foreign = {n for n in left if n not in PRE}
    assert len(foreign) == 1  # snapshot nguoi khac tao dong thoi (ten moi, thoi gian moi) van con
    assert next(iter(foreign)) in (pt.sdir / "ALERT.txt").read_text()  # bao, khong xoa
    assert mutations(pt.prod, own) == []
    assert '"points_before": 50' in (pt.fake / "ssh.stdin").read_text()  # so diem cua chinh snapshot gui len VM
    assert "packtest XONG" in r.stdout
    assert_prod_alive(pt)


def test_packtest_pack_deleting_its_own_snapshot_is_fine(pt):
    r = sh(EVAL, "packtest", b=pt)  # mac dinh: create download delete_own
    assert r.returncode == 0, r.stderr
    assert snap_names(pt.prod) == PRE and len(own_names(pt)) == 1


def test_packtest_denied_mutation_fails_run_and_never_reaches_prod(pt):
    (pt.fake / "pack_mode").write_text("evil create download delete_own")
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0
    codes = (pt.fake / "evil.codes").read_text().splitlines()
    assert len(codes) == 4 and all(x.endswith(" 403") for x in codes)
    assert mutations(pt.prod, own_names(pt)) == [] and PRE <= snap_names(pt.prod)
    assert "DENIED" in (pt.sdir / "ALERT.txt").read_text()
    assert any("PROD_CLEAN=fail" in x for x in log(pt, "ssh"))


def test_packtest_inflight_create_when_tunnel_dies_is_recorded_and_deleted(tmp_path, box, neo):  # noqa: F811
    slow = make_qdrant(snaps=OLD, create_delay=1.5)
    b = setup_pt(box, slow, neo)
    (b.fake / "pack_mode").write_text("inflight")
    r = sh(EVAL, "packtest", b=b)
    assert r.returncode != 0  # pha pack chet
    own = own_names(b)
    assert len(own) == 1 and not (own & snap_names(slow)) and {s["name"] for s in OLD} <= snap_names(slow)
    assert mutations(slow, own) == [] and tunnel_dead(b)
    slow["srv"].shutdown()


def test_packtest_pack_failure_still_kills_tunnel_and_cleans(pt):
    (pt.fake / "pack_mode").write_text("create")
    (pt.fake / "pack_rc").write_text("1")
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and tunnel_dead(pt)
    assert snap_names(pt.prod) == PRE and any("PHASE=restore" in x for x in log(pt, "ssh"))  # restore van ghi summary


def test_packtest_local_disk_drop_is_only_a_warning(pt):
    stub(pt.tmp / "bin/df", 'echo Avail; if [ -f "$FAKE_DIR/df_seen" ]; then echo 96636764160; else touch "$FAKE_DIR/df_seen"; echo 107374182400; fi')
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode == 0 and "CẢNH BÁO" in r.stderr


def test_packtest_unreadable_prelist_deletes_nothing(pt):
    (pt.fake / "pack_mode").write_text("create")
    # sau khi lay danh sach truoc, gia lap tep danh sach bi mat: cleanup khong duoc xoa gi
    real = (pt.tmp / "bin/ssh").read_text()
    (pt.tmp / "bin/ssh").write_text(real.replace('if "PHASE=pack" in cmd:',
        'if "PHASE=pack" in cmd:\n    os.remove(os.environ["STACK_EVAL_DIR"] + "/packtest/snapshots-before.json")', 1))
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and len(own_names(pt)) == 1
    assert not (pt.sdir / "packtest/snapshots-deleted.log").exists()  # script khong xoa gi; proxy tu xoa dung ten no ghi
    assert "không xoá gì" in (pt.sdir / "ALERT.txt").read_text()
    assert PRE <= snap_names(pt.prod) and mutations(pt.prod, own_names(pt)) == []


def test_packtest_key_never_in_argv_or_on_vm(pt):
    r = sh(EVAL, "packtest", b=pt, extra={"QDRANT_API_KEY": QKEY})
    assert r.returncode == 0, r.stderr
    argv = "\n".join(log(pt, "ssh") + log(pt, "rsync"))
    assert QKEY not in argv and QKEY not in r.stdout + r.stderr
    assert QKEY not in (pt.fake / "ssh.stdin").read_text()  # khoa khong len VM: proxy gan khoa
    assert {k for _, _, k in pt.prod["reqs"]} == {QKEY}
    for f in (pt.sdir / "packtest").rglob("*"):
        if f.is_file():
            assert QKEY not in f.read_text(errors="replace"), f
    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    assert QKEY not in ps


def test_packtest_key_file_must_be_0600(pt):
    kf = pt.tmp / "qkey"
    kf.write_text(QKEY + "\n")
    kf.chmod(0o644)
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_QDRANT_KEY_FILE": str(kf)})
    assert r.returncode != 0 and pt.prod["reqs"] == [] and QKEY not in r.stderr
    kf.chmod(0o600)
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_QDRANT_KEY_FILE": str(kf)})
    assert r.returncode == 0, r.stderr
    assert {k for _, _, k in pt.prod["reqs"]} == {QKEY}


def test_packtest_refuses_near_deadline(pt):
    now = int(time.time())
    write_state(pt, ssh_host="1.2.3.4", ssh_port=2222, mode="packtest", created_at=now - 3 * 3600, deadline=now + 300)
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and "deadline" in r.stderr and log(pt, "ssh") == []


def test_packtest_writes_only_under_stack_eval_dir(pt):
    before = {p for p in pt.tmp.rglob("*") if p.is_file()}
    assert sh(EVAL, "packtest", b=pt).returncode == 0
    new = {p for p in pt.tmp.rglob("*") if p.is_file()} - before
    assert all(p.is_relative_to(pt.sdir) or p.is_relative_to(pt.fake) for p in new), new


# ------------------------------------------------------------------ REMOTE_PACKTEST_PACK + RESTORE chay that voi stub

def heredoc(marker: str) -> str:
    lines = EVAL.read_text().splitlines()
    start = next(i for i, x in enumerate(lines) if f"<<'{marker}'" in x)
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == marker)
    return "\n".join(lines[start + 1:end])


def run_remote(tmp_path: Path, prod_st, vm_st, *, snap_points=(50, 50), neo4j_restored=(5, 3), gateway_image="", bm25_out="17",
               images_rc=0, pack_rc=0, drop_file=False, prod_clean="pass"):
    root, binp, seen = tmp_path / "vm", tmp_path / "bin", tmp_path / "seen"
    seen.mkdir()

    def fix(body: str) -> str:
        return (body.replace("/opt/secai-stack", str(root / "opt")).replace("/srv", str(root / "srv"))
                .replace("/usr/bin/time", str(binp / "time")).replace("127.0.0.1:16333", f"127.0.0.1:{prod_st['port']}")
                .replace("127.0.0.1:16334", f"127.0.0.1:{vm_st['port']}"))
    srv, st = root / "srv", root / "opt/stack"
    src = srv / "src-root"
    for p in ("exploitdb/a", "poc/store/x", "poc-sources/y", "exploitdb-mcp-server/z", "neo4j-export/schema.cypher"):
        (src / p).parent.mkdir(parents=True, exist_ok=True)
        (src / p).write_text("data-" + p)
    (src / "neo4j-export/counts.json").write_text(json.dumps({"nodes": {"CVE": 3, "CWE": 2}, "relationships": {"HAS_CWE": 3}}))
    c = sqlite3.connect(src / "poc/catalog.db")
    c.execute("CREATE TABLE pocs (id INTEGER)")
    c.executemany("INSERT INTO pocs VALUES (?)", [(1,), (2,), (3,)])
    c.commit()
    c.close()
    stub(st / "pack.sh", f'echo "${{QDRANT_API_KEY-unset}} $QDRANT_URL" > "{seen}/pack_env"\necho "$*" > "{seen}/pack_args"\n'
         f'[ {pack_rc} = 0 ] || exit {pack_rc}\n'
         'eval out=\\${$#}; eval s=\\${$(($#-1))}; mkdir -p "$out/qdrant" "$out/neo4j"; echo snap > "$out/qdrant/c.snapshot"\n'
         'tar -C "$s" -cf "$out/all.tar" exploitdb poc poc-sources exploitdb-mcp-server\n'
         '(cd "$out" && sha256sum qdrant/c.snapshot all.tar > SHA256SUMS)')
    stub(st / "restore-data.sh", f'echo "${{QDRANT_API_KEY-unset}}" > "{seen}/restore_key"\neval out=\\${{$#}}\n'
         f'echo "${{#NEO4J_PASSWORD}} $*" > "{seen}/restore_npw"\n'
         'mkdir -p "$DATA_ROOT/_state/neo4j_data/databases"; tar -C "$DATA_ROOT" -xf "$out/all.tar"'
         + ('\nrm "$DATA_ROOT/poc/store/x"' if drop_file else "")
         + (f'\necho "==> neo4j: counts match ({neo4j_restored[0]} nodes, {neo4j_restored[1]} relationships)"' if neo4j_restored else ""))
    stub(st / "bootstrap.sh", f'echo "$*" >> "{seen}/bootstrap.log"; echo all images present; exit {images_rc}')
    stub(binp / "docker", f'echo "$*" >> "{seen}/docker.log"; case "$*" in *"--network none"*) echo "{bm25_out}" ;; *) echo cid ;; esac')
    stub(binp / "time", 'shift 2; f=$1; shift; printf "\\tMaximum resident set size (kbytes): 1234\\n\\tElapsed (wall clock) time (h:mm:ss or m:ss): 0:01.00\\n" > "$f"; exec "$@"')
    # chay khong root: gia lap quyen restore-data.sh dat (chown can root); ghi thoi diem goi de kiem thu tu so voi docker
    stub(binp / "stat", f'echo "stat $*" >> "{seen}/docker.log"\n'
         'case "$*" in *"%u:%g %a"*) echo "10001:10100 775" ;; *"%u"*) echo 7474 ;; *) exec /usr/bin/stat "$@" ;; esac')
    env = {"PATH": f"{binp}:/usr/bin:/bin", "COLL": "c", "VM_MIN_FREE_GB": "0", "HOME": str(tmp_path), "QDRANT_API_KEY": "LEAKED_IF_USED"}
    pack = tmp_path / "pack.sh"
    pack.write_text(fix(heredoc("REMOTE_PACKTEST_PACK")) + "\n")
    r1 = subprocess.run(["bash", str(pack)], env=env, text=True, capture_output=True, timeout=120)
    lo, hi = snap_points
    (srv / "packtest/snapshot-points.json").write_text(json.dumps({"name": "x", "points_before": lo, "points_after": hi}))
    rest = tmp_path / "restore.sh"
    rest.write_text(fix(heredoc("REMOTE_PACKTEST_RESTORE")) + "\n")
    r2 = subprocess.run(["bash", str(rest)], env={**env, "GATEWAY_IMAGE": gateway_image, "PROD_CLEAN": prod_clean, "STAGE_SECS": "5",
                                                   "STAGE_BYTES": "100", "BW_MBPS": "50.0"}, text=True, capture_output=True, timeout=120)
    return r1, r2, root / "opt/results", seen, srv


@pytest.fixture
def two():
    p, v = make_qdrant(n_points=25000), make_qdrant(n_points=25000)
    yield p, v
    p["srv"].shutdown()
    v["srv"].shutdown()


def summary(R: Path) -> dict:
    return json.loads((R / "packtest-summary.json").read_text())


def test_remote_packtest_pass(tmp_path, two):
    prod_st, vm_st = two
    r1, r2, R, seen, srv = run_remote(tmp_path, prod_st, vm_st, snap_points=(25000, 25000))
    s = summary(R)
    assert r1.returncode == 0 and r2.returncode == 0 and s["verdict"] == "pass", (r1.stderr, r2.stdout, r2.stderr, s["checks"])
    ck = s["checks"]
    for name in ("pack", "restore", "package_sha256", "qdrant_reference", "qdrant_points", "qdrant_vectors_config", "qdrant_payload_indexes",
                 "qdrant_sample_points", "files_exploitdb", "files_poc_store", "files_poc-sources", "files_exploitdb-mcp-server",
                 "files_catalog_rows", "perm_poc", "perm_poc_store", "perm_neo4j_data", "prod_snapshot_cleanup"):
        assert ck[name]["status"] == "pass", (name, ck[name])
    assert ck["neo4j_counts"]["status"] == "pass", ck["neo4j_counts"]
    assert s["not_verified"] == ["bm25_offline", "images_load"]
    args = (seen / "pack_args").read_text()
    assert f"--neo4j-export {srv}/src-root/neo4j-export " in args and "--neo4j-dump" not in args
    npw = (seen / "restore_npw").read_text().split()
    assert npw[0] == "32" and not any(re.fullmatch(r"[0-9a-f]{32}", x) for x in npw[1:])   # random password in env, not argv
    assert not (seen / "bootstrap.log").exists()
    assert not re.search(r"env [^\n]*NEO4J_PASSWORD=", heredoc("REMOTE_PACKTEST_RESTORE"))
    assert s["timings_s"]["stage_rsync"] == 5 and isinstance(s["timings_s"]["pack"], int) and isinstance(s["timings_s"]["restore"], int)
    assert s["time_v"]["pack"]["max_rss_kb"] == 1234
    assert s["disk"]["used_peak"] >= s["disk"]["used_start"] > 0 and s["sizes_bytes"]["package"] > 0
    assert s["src_facts"]["catalog_rows"] == {"pocs": 3}
    # khong khoa nao tren VM; pack.sh qua tunnel (16333), restore khong co khoa
    assert (seen / "pack_env").read_text().split()[0] == "unset" and (seen / "restore_key").read_text().strip() == "unset"
    # Qdrant prod: chi doc (scroll het id + lay 20 diem); mau trai deu ca dai id (khong chi trang dau)
    assert mutations(prod_st) == []
    ref = json.loads((srv / "packtest/prod-ref.json").read_text())
    assert ref["ids_total"] == 25000 and max(p["id"] for p in ref["sample"]) > 10000
    # VM Qdrant: khong ghi gi ngoai doc
    assert all(m in ("GET", "POST") for m, _, _ in vm_st["reqs"])
    dl = (seen / "docker.log").read_text()
    assert "packtest-neo4j" not in dl and "NEO4J_AUTH" not in dl and "--network none" not in dl
    assert subprocess.run(["sha256sum", "-c", "--strict", "--quiet", "SHA256SUMS"], cwd=R).returncode == 0
    assert not (srv / "src-root").exists()
    for f in R.rglob("*"):
        if f.is_file():
            assert "LEAKED_IF_USED" not in f.read_text(errors="replace"), f


@pytest.mark.parametrize("vm_kw,kw,check", [
    ({}, {"snap_points": (24990, 24995)}, "qdrant_points"),          # khac so diem cua chinh snapshot
    ({"vec_shift": 1e-3}, {}, "qdrant_sample_points"),               # vector dense lech > 1e-6
    ({"payload_tweak": True}, {}, "qdrant_sample_points"),
    ({}, {"neo4j_restored": (4, 3)}, "neo4j_counts"),                 # totals differ from the export's counts.json
    ({}, {"neo4j_restored": None}, "neo4j_counts"),                   # restore never printed the counts line
    ({}, {"gateway_image": "reg/gw:1", "bm25_out": "Traceback"}, "bm25_offline"),
    ({}, {"gateway_image": "reg/gw:1", "images_rc": 1}, "images_load"),
    ({}, {"drop_file": True}, "files_poc_store"),
    ({}, {"prod_clean": "fail"}, "prod_snapshot_cleanup"),
])
def test_remote_packtest_mismatch_is_fail(tmp_path, vm_kw, kw, check):
    prod_st, vm_st = make_qdrant(n_points=200), make_qdrant(n_points=200, **vm_kw)
    try:
        kw.setdefault("snap_points", (200, 200))
        _, r2, R, _, _ = run_remote(tmp_path, prod_st, vm_st, **kw)
        s = summary(R)
        assert r2.returncode != 0 and s["verdict"] == "fail" and s["checks"][check]["status"] == "fail", s["checks"]
    finally:
        prod_st["srv"].shutdown()
        vm_st["srv"].shutdown()


def test_remote_packtest_tolerates_tiny_float_noise_and_ingest_within_snapshot_range(tmp_path):
    prod_st, vm_st = make_qdrant(n_points=200), make_qdrant(n_points=200, vec_shift=1e-8)
    try:
        _, r2, R, seen, srv = run_remote(tmp_path, prod_st, vm_st, snap_points=(190, 205), gateway_image="reg/gw:1")
        s = summary(R)
        assert r2.returncode == 0 and s["verdict"] == "pass" and s["not_verified"] == [], s["checks"]
        assert s["checks"]["images_load"]["status"] == s["checks"]["bm25_offline"]["status"] == "pass"
        assert (seen / "bootstrap.log").read_text() == f"load-images {srv}/images-pkg/images\n"
        bm = [x for x in (seen / "docker.log").read_text().splitlines() if "--network none" in x]
        assert len(bm) == 1 and bm[0].startswith("run --rm --pull never --network none -e HF_HUB_OFFLINE=1 --entrypoint python reg/gw:1 -c ")
        assert "bm25._load_sparse_model()" in bm[0]
    finally:
        prod_st["srv"].shutdown()
        vm_st["srv"].shutdown()


def test_remote_packtest_pack_failure_skips_restore_but_writes_summary(tmp_path, two):
    prod_st, vm_st = two
    r1, r2, R, seen, srv = run_remote(tmp_path, prod_st, vm_st, pack_rc=3)
    s = summary(R)
    assert r1.returncode != 0 and r2.returncode != 0 and s["verdict"] == "fail" and s["checks"]["pack"]["status"] == "fail"
    assert "restore" not in s["checks"] and not (seen / "restore_key").exists() and vm_st["reqs"] == []
    assert (srv / "src-root").exists()  # nguon giu lai de dieu tra


# ------------------------------------------------------------------ guard proxy: vong 3 (than yeu cau, ket noi, mot lan tao, tu don)

def raw(port: int, data: bytes, wait: float = 5.0) -> bytes:
    import socket
    s = socket.create_connection(("127.0.0.1", port), timeout=wait)
    try:
        s.sendall(data)
    except OSError:
        pass
    out = b""
    try:
        while chunk := s.recv(65536):
            out += chunk
    except OSError:
        pass
    s.close()
    return out


@pytest.mark.parametrize("cl", [b"-1", b"abc", b"99999999999", b"1048577"])
def test_proxy_bad_content_length_denied_without_reading_body(proxy, prod, cl):
    t = time.monotonic()
    out = raw(proxy.port, b"POST /collections/c/points/scroll HTTP/1.1\r\nHost: x\r\nContent-Length: " + cl + b"\r\n\r\n" + b"A" * (4 << 20))
    assert out.startswith(b"HTTP/1.0 403") and time.monotonic() - t < 4
    assert prod["reqs"] == [] and "Content-Length" in (proxy.dir / "denied.log").read_text()


def test_proxy_chunked_body_denied(proxy, prod):
    out = raw(proxy.port, b"POST /collections/c/points/scroll HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n")
    assert out.startswith(b"HTTP/1.0 403") and prod["reqs"] == []


def test_proxy_hides_python_version(proxy):
    out = raw(proxy.port, b"GET / HTTP/1.0\r\n\r\n")
    server = [x for x in out.split(b"\r\n") if x.lower().startswith(b"server:")]
    assert server == [b"Server: qdrant-guard"]


def test_proxy_caps_concurrent_connections(tmp_path, prod):
    import socket
    px = Proxy(tmp_path, prod["port"], extra=("--client-timeout", "30"))
    idle = [socket.create_connection(("127.0.0.1", px.port)) for _ in range(16)]
    try:
        time.sleep(0.5)
        s = socket.create_connection(("127.0.0.1", px.port), timeout=3)
        s.sendall(b"GET / HTTP/1.0\r\n\r\n")
        assert s.recv(100) == b""  # thu 17: dong ngay, khong xu ly
        s.close()
    finally:
        for s in idle:
            s.close()
    time.sleep(0.5)
    assert px.req("GET", "/")[0] == 200  # giai phong slot khi client dong
    px.stop()


def test_proxy_drops_slow_client_after_timeout(tmp_path, prod):
    import socket
    px = Proxy(tmp_path, prod["port"], extra=("--client-timeout", "1"))
    s = socket.create_connection(("127.0.0.1", px.port), timeout=5)
    s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n")  # khong bao gio xong header
    t = time.monotonic()
    assert s.recv(100) == b"" and time.monotonic() - t < 4
    s.close()
    px.stop()


def test_proxy_allows_exactly_one_create_even_concurrently(tmp_path):
    slow = make_qdrant(create_delay=0.8)
    px = Proxy(tmp_path, slow["port"])
    codes = []
    th = [threading.Thread(target=lambda: codes.append(px.req("POST", "/collections/c/snapshots")[0])) for _ in range(3)]
    try:
        for t in th:
            t.start()
        for t in th:
            t.join()
        assert sorted(codes) == [200, 403, 403]
        assert px.req("POST", "/collections/c/snapshots")[0] == 403
        assert len([1 for m, p, _ in slow["reqs"] if m == "POST" and p.endswith("/snapshots")]) == 1
        assert "DENIED POST /collections/c/snapshots" in px.alert.read_text()
    finally:
        px.stop()
        slow["srv"].shutdown()


def test_proxy_alerts_on_created_snapshot_with_bad_name(tmp_path):
    odd = make_qdrant(bad_name=True)
    px = Proxy(tmp_path, odd["port"])
    try:
        assert px.req("POST", "/collections/c/snapshots")[0] == 200
        assert px.created() == [] and "tên lạ" in px.alert.read_text()
    finally:
        px.stop()
        odd["srv"].shutdown()


def _wait_exit(px, secs=30):
    t = time.monotonic() + secs
    while px.p.poll() is None and time.monotonic() < t:
        time.sleep(0.2)
    return px.p.poll() is not None


@pytest.mark.parametrize("how", ["parent_killed", "max_secs", "sigterm"])
def test_proxy_exits_and_deletes_its_own_snapshot(tmp_path, prod, how):
    parent = subprocess.Popen(["sleep", "60"])
    px = Proxy(tmp_path, prod["port"], parent=parent.pid, max_secs=3 if how == "max_secs" else 300)
    try:
        code, data = px.req("POST", "/collections/c/snapshots")
        name = json.loads(data)["result"]["name"]
        assert code == 200 and name in snap_names(prod)
        if how == "parent_killed":
            parent.kill()
            parent.wait()
        elif how == "sigterm":
            px.p.terminate()
        assert _wait_exit(px)
        assert name not in snap_names(prod) and PRE <= snap_names(prod)
        assert mutations(prod, {name}) == []
        assert f"{name} gone" in (px.dir / "selfclean.log").read_text()
    finally:
        parent.kill()
        px.stop()


def test_proxy_sigterm_waits_for_inflight_create_then_deletes_it(tmp_path):
    slow = make_qdrant(snaps=OLD, create_delay=1.5)
    px = Proxy(tmp_path, slow["port"])
    try:
        t = threading.Thread(target=lambda: px.req("POST", "/collections/c/snapshots", timeout=10))
        t.start()
        time.sleep(0.4)
        px.p.terminate()
        assert _wait_exit(px)
        t.join()
        assert len(px.created()) == 1 and snap_names(slow) == {s["name"] for s in OLD}
    finally:
        px.stop()
        slow["srv"].shutdown()


def test_packtest_kill9_of_script_still_deletes_recorded_snapshot(pt):
    (pt.fake / "pack_mode").write_text("create kill9")
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode == -9 or r.returncode == 137
    try:
        own = set()
        for _ in range(100):
            own = own_names(pt)
            if own and not (own & snap_names(pt.prod)):
                break
            time.sleep(0.2)
        assert len(own) == 1 and not (own & snap_names(pt.prod)) and PRE <= snap_names(pt.prod)
        assert mutations(pt.prod, own) == []
    finally:
        try:
            os.kill(int((pt.fake / "tunnel.pid").read_text()), 9)
        except (OSError, ValueError):
            pass


def test_packtest_corrupt_prelist_script_deletes_nothing(pt):
    (pt.fake / "pack_mode").write_text("create")
    real = (pt.tmp / "bin/ssh").read_text()
    (pt.tmp / "bin/ssh").write_text(real.replace('if "PHASE=pack" in cmd:',
        'if "PHASE=pack" in cmd:\n    open(os.environ["STACK_EVAL_DIR"] + "/packtest/snapshots-before.json", "w").write("{not json")', 1))
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and "không đọc được" in (pt.sdir / "ALERT.txt").read_text()
    assert not (pt.sdir / "packtest/snapshots-deleted.log").exists()  # script khong xoa gi (proxy tu xoa dung ten no ghi)
    assert PRE <= snap_names(pt.prod)


def test_proxy_caps_buffered_responses_but_streams_snapshot_download(tmp_path, prod):
    px = Proxy(tmp_path, prod["port"], extra=("--max-resp-bytes", "300"))
    try:
        assert px.req("POST", "/collections/c/points/scroll", {"limit": 100})[0] == 502   # 50 id ~ 600 B > 300 B
        assert px.req("POST", "/collections/c/points/scroll", {"limit": 1})[0] == 200
        code, data = px.req("GET", "/collections/c/snapshots/c-1-2026-09-04-02-31-25.snapshot")
        assert code == 200 and len(data) == 8000   # tai snapshot khong bi gioi han, duoc stream
    finally:
        px.stop()


# ------------------------------------------------------------------ vong cuoi: --protect, ten None, shutdown luon thoat, drain <= du tru

def test_proxy_refuses_to_start_without_readable_protect_list(tmp_path, prod):
    for bad in (False, "x"):   # thieu tep / khong phai danh sach
        if bad == "x":
            (tmp_path / "before.json").write_text("{not json")
        px = Proxy(tmp_path, prod["port"], protect=False, wait_port=False)
        assert px.p.wait(10) != 0 and not (px.dir / "port").exists()


def test_proxy_never_records_a_protected_name(tmp_path):
    clash = make_qdrant(snaps=OLD, create_name=OLD[0]["name"])
    px = Proxy(tmp_path, clash["port"])
    try:
        assert px.req("POST", "/collections/c/snapshots")[0] == 200
        assert px.created() == [] and "TRÙNG snapshot có sẵn" in px.alert.read_text()
        assert px.req("DELETE", f"/collections/c/snapshots/{OLD[0]['name']}")[0] == 403
        px.p.terminate()
        assert _wait_exit(px)
        assert {s["name"] for s in OLD} <= snap_names(clash) and mutations(clash) == []
    finally:
        px.stop()
        clash["srv"].shutdown()


def test_proxy_never_records_null_name(tmp_path):
    q = make_qdrant(create_name=None)
    px = Proxy(tmp_path, q["port"])
    try:
        assert px.req("POST", "/collections/c/snapshots")[0] == 200
        assert px.created() == [] and "tên lạ" in px.alert.read_text()
        assert px.req("DELETE", "/collections/c/snapshots/None")[0] == 403
    finally:
        px.stop()
        q["srv"].shutdown()


def test_proxy_scroll_without_vectors_allows_full_page(proxy, prod):
    assert proxy.req("POST", "/collections/c/points/scroll", {"limit": 10000, "with_vector": False, "with_payload": False})[0] == 200


def test_proxy_exits_even_if_shutdown_logic_raises(tmp_path, prod):
    (tmp_path / "proxy").mkdir()
    (tmp_path / "proxy/selfclean.log").mkdir()   # open(..., "a") se loi trong logic don
    px = Proxy(tmp_path, prod["port"])
    try:
        assert px.req("POST", "/collections/c/snapshots")[0] == 200
        px.p.terminate()
        assert _wait_exit(px, 15)
    finally:
        px.stop()


@pytest.mark.parametrize("env,want", [("5000", 400), ("1", 30), ("120", 120)])
def test_packtest_caps_proxy_drain_to_reserve(pt, env, want):
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_DRAIN_SECS": env})
    assert r.returncode == 0, r.stderr
    cfg = json.loads((pt.sdir / "packtest/proxy/config.json").read_text())
    assert cfg["drain_secs"] == want and cfg["protected"] == len(PRE)


def test_packtest_poc_sources_excludes_match_pack_and_du(pt):
    big = "x" * (1 << 20)
    (pt.src / "exploitdb/.git").mkdir(); (pt.src / "exploitdb/.git/pack").write_text(big)  # .git of any tree: rsync, du and pack all drop it
    for p in ("nuclei-templates/.git/pack", "trickest-cve/x", "PoC-in-GitHub/y", "_manual-untracked-backup-1/z"):
        (pt.src / "poc-sources" / p).parent.mkdir(parents=True, exist_ok=True)
        (pt.src / "poc-sources" / p).write_text(big)
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode == 0, r.stderr + r.stdout
    poc = next(x for x in log(pt, "rsync") if "poc-sources" in x and "/srv/src-root/" in x)
    for ex in (".git", "/poc-sources/trickest-cve", "/poc-sources/PoC-in-GitHub", "/poc-sources/_manual-untracked-backup-*"):
        assert f"--exclude {ex}" in poc
    assert any("--exclude .git" in x and "/poc-sources/" not in x.split("--exclude .git")[0] for x in log(pt, "rsync") if "exploitdb" in x)
    nb = int(re.search(r"nguồn (\d+) B", r.stdout).group(1))
    assert nb < 1 << 20  # 5 MB excluded trees are not counted
