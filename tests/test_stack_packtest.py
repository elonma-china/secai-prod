"""eval-vast.sh EVAL_DISK_GB / EVAL_MODE=packtest / `packtest` + stack/qdrant-guard-proxy.py.

Khong thue, khong cham Qdrant that: vastai/ssh/rsync/df GIA; Qdrant "prod" va Qdrant "VM" la hai server HTTP gia o hai cong khac
nhau, ghi lai moi yeu cau. Phan tu xa (REMOTE_PACKTEST_PACK/RESTORE) chay THAT voi stub pack.sh/restore-data.sh/docker/time.
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from test_stack_eval_vast import EVAL, STACK, assert_prod_alive, box, lines_with, log, sh, stub, write_state  # noqa: F401

PROXY = STACK / "qdrant-guard-proxy.py"
QKEY = "QKEY_MARK_4d2e"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------ Qdrant gia (prod hoac VM), moi server mot trang thai rieng

def make_qdrant(n_points=50, snaps=None, create_delay=0.0, vec_shift=0.0, payload_tweak=False):
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
                    name = f"c-own{st['seq']}-{time.strftime('%Y-%m-%d-%H-%M-%S')}.snapshot"
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
    def __init__(self, tmp: Path, upstream: int, key: str = ""):
        self.dir = tmp / "proxy"
        self.alert = tmp / "ALERT.txt"
        self.p = subprocess.Popen([sys.executable, "-I", str(PROXY), "--upstream", f"http://127.0.0.1:{upstream}",
                                   "--collection", "c", "--state", str(self.dir), "--alert", str(self.alert)],
                                  stdin=subprocess.PIPE, text=True)
        self.p.stdin.write(key + "\n")
        self.p.stdin.close()
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
        self.p.terminate()
        self.p.wait(5)


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
        if "download" in modes: call(px, "GET", f"/collections/c/snapshots/{name}")
        if "delete_own" in modes: call(px, "DELETE", f"/collections/c/snapshots/{name}")
    sys.exit(int(rd("pack_rc", "0")))
if "PHASE=restore" in cmd: sys.exit(int(rd("restore_rc", "0")))
sys.exit(int(rd("ssh_rc", "0")))
'''


def setup_pt(box, prod):  # noqa: F811
    binp = box.tmp / "bin"
    (binp / "ssh").write_text(FAKE_SSH)
    (binp / "ssh").chmod(0o755)
    stub(binp / "df", 'echo Avail; cat "$FAKE_DIR/df_avail" 2>/dev/null || echo 107374182400')
    (box.fake / "prod_port").write_text(str(prod["port"]))
    src = box.tmp / "src"
    for p in ("exploitdb/a", "poc/store/x", "poc-sources/y", "neo4j/neo4j.dump", "../exploitdb-mcp-server/z"):
        (src / p).parent.mkdir(parents=True, exist_ok=True)
        (src / p).write_text("x")  # mcp: thu muc anh em nhu mac dinh <src>/../exploitdb-mcp-server
    c = sqlite3.connect(src / "poc/catalog.db")
    c.execute("CREATE TABLE t (i)")
    c.commit()
    c.close()
    now = int(time.time())
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222, mode="packtest", created_at=now, deadline=now + 3 * 3600)
    box.env.update({"PACKTEST_SRC_ROOT": str(src), "PACKTEST_QDRANT_COLLECTION": "c", "PACKTEST_BW_MB": "1",
                    "PACKTEST_QDRANT_URL": f"http://127.0.0.1:{prod['port']}", "PACKTEST_DRAIN_SECS": "20"})
    box.src, box.prod = src, prod
    return box


@pytest.fixture
def pt(box, prod):  # noqa: F811
    return setup_pt(box, prod)


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
    (pt.src / "neo4j/neo4j.dump").unlink()
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and "neo4j.dump" in r.stderr and log(pt, "ssh") == []


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


def test_packtest_inflight_create_when_tunnel_dies_is_recorded_and_deleted(tmp_path, box):  # noqa: F811
    slow = make_qdrant(snaps=OLD, create_delay=1.5)
    b = setup_pt(box, slow)
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
    assert own_names(pt) <= snap_names(pt.prod)  # khong xoa gi
    assert "không xoá gì" in (pt.sdir / "ALERT.txt").read_text()


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


def run_remote(tmp_path: Path, prod_st, vm_st, *, snap_points=(50, 50), neo4j_expect="", pack_rc=0, drop_file=False, prod_clean="pass"):
    root, binp, seen = tmp_path / "vm", tmp_path / "bin", tmp_path / "seen"
    seen.mkdir()

    def fix(body: str) -> str:
        return (body.replace("/opt/secai-stack", str(root / "opt")).replace("/srv", str(root / "srv"))
                .replace("/usr/bin/time", str(binp / "time")).replace("127.0.0.1:16333", f"127.0.0.1:{prod_st['port']}")
                .replace("127.0.0.1:16334", f"127.0.0.1:{vm_st['port']}"))
    srv, st = root / "srv", root / "opt/stack"
    src = srv / "src-root"
    for p in ("exploitdb/a", "poc/store/x", "poc-sources/y", "exploitdb-mcp-server/z", "neo4j/neo4j.dump"):
        (src / p).parent.mkdir(parents=True, exist_ok=True)
        (src / p).write_text("data-" + p)
    c = sqlite3.connect(src / "poc/catalog.db")
    c.execute("CREATE TABLE pocs (id INTEGER)")
    c.executemany("INSERT INTO pocs VALUES (?)", [(1,), (2,), (3,)])
    c.commit()
    c.close()
    stub(st / "pack.sh", f'echo "${{QDRANT_API_KEY-unset}} $QDRANT_URL" > "{seen}/pack_env"\n[ {pack_rc} = 0 ] || exit {pack_rc}\n'
         'eval out=\\${$#}; eval s=\\${$(($#-1))}; mkdir -p "$out/qdrant" "$out/neo4j"; echo snap > "$out/qdrant/c.snapshot"\n'
         'tar -C "$s" -cf "$out/all.tar" exploitdb poc poc-sources exploitdb-mcp-server\n'
         '(cd "$out" && sha256sum qdrant/c.snapshot all.tar > SHA256SUMS)')
    stub(st / "restore-data.sh", f'echo "${{QDRANT_API_KEY-unset}}" > "{seen}/restore_key"\neval out=\\${{$#}}\n'
         'mkdir -p "$DATA_ROOT/_state/neo4j_data/databases"; tar -C "$DATA_ROOT" -xf "$out/all.tar"'
         + ('\nrm "$DATA_ROOT/poc/store/x"' if drop_file else ""))
    stub(binp / "docker", f'echo "$*" >> "{seen}/docker.log"; case "$1" in exec) printf "count(n)\\n42\\n" ;; *) echo cid ;; esac')
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
    r2 = subprocess.run(["bash", str(rest)], env={**env, "NEO4J_EXPECT": neo4j_expect, "PROD_CLEAN": prod_clean, "STAGE_SECS": "5",
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
    assert ck["neo4j_nodes"]["status"] == "not_verified" and s["not_verified"] == ["neo4j_nodes"]
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
    dl = (seen / "docker.log").read_text().splitlines()
    first_neo = next(i for i, x in enumerate(dl) if "packtest-neo4j" in x and x.startswith("run"))
    assert max(i for i, x in enumerate(dl) if x.startswith("stat") and "%u" in x) < first_neo  # quyen do truoc khi Neo4j chay
    assert subprocess.run(["sha256sum", "-c", "--strict", "--quiet", "SHA256SUMS"], cwd=R).returncode == 0
    assert not (srv / "src-root").exists()
    for f in R.rglob("*"):
        if f.is_file():
            assert "LEAKED_IF_USED" not in f.read_text(errors="replace"), f


@pytest.mark.parametrize("vm_kw,kw,check", [
    ({}, {"snap_points": (24990, 24995)}, "qdrant_points"),          # khac so diem cua chinh snapshot
    ({"vec_shift": 1e-3}, {}, "qdrant_sample_points"),               # vector dense lech > 1e-6
    ({"payload_tweak": True}, {}, "qdrant_sample_points"),
    ({}, {"neo4j_expect": "41"}, "neo4j_nodes"),
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
        _, r2, R, _, _ = run_remote(tmp_path, prod_st, vm_st, snap_points=(190, 205), neo4j_expect="42")
        s = summary(R)
        assert r2.returncode == 0 and s["verdict"] == "pass" and s["not_verified"] == [], s["checks"]
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
