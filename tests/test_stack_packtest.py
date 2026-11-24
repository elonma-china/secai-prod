"""eval-vast.sh EVAL_DISK_GB / EVAL_MODE=packtest / `packtest` voi vastai, ssh, rsync, curl, df GIA (khong thue, khong cham Qdrant prod).

Phan tu xa (heredoc REMOTE_PACKTEST) chay THAT voi stub pack.sh/restore-data.sh/docker/time va mot Qdrant gia qua HTTP.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from test_stack_eval_vast import EVAL, assert_prod_alive, box, lines_with, log, set_mode, sh, stub, write_state  # noqa: F401

QKEY = "QKEY_MARK_4d2e"
OLD = [{"name": "c-1-2026-09-04-02-31-25.snapshot", "creation_time": "2026-09-04T02:32:05", "size": 4},
       {"name": "c-1-2026-08-23-04-03-33.snapshot", "creation_time": "2026-08-23T04:04:11", "size": 4}]

FAKE_CURL = r'''#!/usr/bin/env python3
# Qdrant prod gia: argv -> curl.log, stdin (cau hinh -K -) -> curl.stdin; snapshot trong snapshots.json; DELETE ghi deletes.log
import json, os, sys
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]
open(d + "/curl.log", "a").write(" ".join(a) + "\n")
if "-K" in a: open(d + "/curl.stdin", "a").write(sys.stdin.read())
if os.path.exists(d + "/qdrant_down"): sys.exit(7)
url = next(x for x in a if x.startswith("http"))
snaps = json.load(open(d + "/snapshots.json"))
if "-X" in a and a[a.index("-X") + 1] == "DELETE":
    name = url.rsplit("/", 1)[1]
    open(d + "/deletes.log", "a").write(name + "\n")
    json.dump([s for s in snaps if s["name"] != name], open(d + "/snapshots.json", "w")); sys.exit(0)
if url.endswith("/snapshots"):
    print(json.dumps({"result": snaps, "status": "ok"})); sys.exit(0)
print(json.dumps({"result": {"points_count": 842928, "config": {"params": {"vectors": {"dense": {"size": 1024}}}},
                             "payload_schema": {"cve_id": {}, "kind": {}}}}))
'''
FAKE_SSH = r'''#!/usr/bin/env python3
# ssh gia: -N = tunnel (ghi pid, ngu cho toi khi bi giet); 'cat >/dev/null' = do bang thong; COLL= = script packtest tu xa
import datetime as dt, json, os, sys, time
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]; cmd = " ".join(a)
open(d + "/ssh.log", "a").write(cmd + "\n")
if "-N" in a:
    open(d + "/tunnel.pid", "w").write(str(os.getpid())); time.sleep(300); sys.exit(0)
data = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
open(d + "/ssh.stdin", "ab").write(data if len(data) < 4096 else b"<blob %d>" % len(data))
if "cat >/dev/null" in cmd and os.path.exists(d + "/bw_sleep"): time.sleep(float(open(d + "/bw_sleep").read()))
if "COLL=" in cmd:
    # pack.sh tao snapshot tren prod (qua tunnel) va KHONG xoa duoc; cong them mot snapshot la (ten moi nhung tao truoc luc chay)
    s = json.load(open(d + "/snapshots.json"))
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    s += [{"name": "c-1-own-run.snapshot", "creation_time": now, "size": 4},
          {"name": "c-1-foreign-early.snapshot", "creation_time": "2026-01-01T00:00:00", "size": 4},
          {"name": "other-1-x.snapshot", "creation_time": now, "size": 4}]
    json.dump(s, open(d + "/snapshots.json", "w"))
    sys.exit(int(open(d + "/packtest_rc").read()) if os.path.exists(d + "/packtest_rc") else 0)
sys.exit(int(open(d + "/ssh_rc").read()) if os.path.exists(d + "/ssh_rc") else 0)
'''


@pytest.fixture
def pt(box):  # noqa: F811
    binp = box.tmp / "bin"
    for name, body in (("curl", FAKE_CURL), ("ssh", FAKE_SSH)):
        (binp / name).write_text(body)
        (binp / name).chmod(0o755)
    stub(binp / "df", 'echo Avail; cat "$FAKE_DIR/df_avail" 2>/dev/null || echo 107374182400')
    (box.fake / "snapshots.json").write_text(json.dumps(OLD))
    src = box.tmp / "src"
    for p in ("exploitdb/a", "poc/catalog.db", "poc/store/x", "poc-sources/y", "neo4j/neo4j.dump", "../exploitdb-mcp-server/z"):
        (src / p).parent.mkdir(parents=True, exist_ok=True)
        (src / p).write_text("x")  # mcp: thu muc anh em nhu mac dinh <src>/../exploitdb-mcp-server
    now = int(time.time())
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222, mode="packtest", created_at=now, deadline=now + 3 * 3600)
    box.env.update({"PACKTEST_SRC_ROOT": str(src), "PACKTEST_QDRANT_COLLECTION": "c", "PACKTEST_BW_MB": "1"})
    box.src = src
    return box


def snap_names(b) -> list[str]:
    return [s["name"] for s in json.loads((b.fake / "snapshots.json").read_text())]


def deleted(b) -> list[str]:
    p = b.fake / "deletes.log"
    return p.read_text().split() if p.exists() else []


def tunnel_dead(b) -> bool:
    pid = int((b.fake / "tunnel.pid").read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def argv_all(b) -> str:
    return "\n".join(log(b, "ssh") + log(b, "rsync") + log(b, "curl") + log(b, "scp"))


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
    assert log(box, "rsync") == []  # khong day goi du lieu
    assert len(log(box, "scp")) == 1  # chi git archive
    ssh_argv = "\n".join(log(box, "ssh"))
    assert ".env" not in ssh_argv
    script = (box.fake / "ssh.stdin").read_text()
    assert "docker build" not in script and "QWEN" not in script
    for needle in ("get.docker.com", "zstd", "rsync", "docker pull -q qdrant/qdrant:v1.10.1", "docker pull -q neo4j:5.20-community"):
        assert needle in script
    assert len(log(box, "systemd-run")) == 2  # timer deadline giu nguyen
    assert "cảnh báo" not in r.stdout.lower()  # khong co .env nao roi may


def test_up_full_mode_still_requires_env_and_pkg(box):  # noqa: F811
    env = {k: v for k, v in box.env.items() if k != "EVAL_DATA_PKG"}
    r = subprocess.run(["bash", str(EVAL), "up"], env=env, text=True, capture_output=True, timeout=60)
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")


# ------------------------------------------------------------------ packtest (cuc bo)

def test_packtest_requires_packtest_state(pt):
    write_state(pt, ssh_host="1.2.3.4", ssh_port=2222, deadline=int(time.time()) + 3600)  # khong co mode
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and log(pt, "ssh") == [] and log(pt, "curl") == []


def test_packtest_refuses_low_local_disk_before_anything(pt):
    (pt.fake / "df_avail").write_text(str(40 << 30))
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and "45" in r.stderr
    assert log(pt, "ssh") == [] and log(pt, "rsync") == [] and log(pt, "curl") == []
    assert snap_names(pt) == [s["name"] for s in OLD]


def test_packtest_refuses_missing_source(pt):
    (pt.src / "neo4j/neo4j.dump").unlink()
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and "neo4j.dump" in r.stderr and log(pt, "ssh") == []


def test_packtest_fails_closed_when_prod_qdrant_unreachable(pt):
    (pt.fake / "qdrant_down").write_text("1")
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and log(pt, "ssh") == [] and log(pt, "rsync") == []


@pytest.mark.parametrize("url", ["http://10.0.0.5:6333", "https://127.0.0.1:6333", "http://127.0.0.1:6333/x", "http://127.0.0.1"])
def test_packtest_rejects_non_local_qdrant_url(pt, url):
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_QDRANT_URL": url})
    assert r.returncode != 0 and log(pt, "curl") == [] and log(pt, "ssh") == []


def test_packtest_bandwidth_gate_refuses_before_transfer(pt):
    (pt.fake / "bw_sleep").write_text("1")  # 1 MB trong ~1 s < 8 MB/s
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and "BĂNG THÔNG" in r.stderr
    assert log(pt, "rsync") == [] and not any("-N" in x.split() for x in log(pt, "ssh"))
    assert "băng thông" in (pt.sdir / "ALERT.txt").read_text()
    assert deleted(pt) == [] and snap_names(pt) == [s["name"] for s in OLD]


def test_packtest_full_flow_tunnel_rsync_and_cleans_only_own_snapshot(pt):
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode == 0, r.stderr + r.stdout
    ssh = log(pt, "ssh")
    tun = [x for x in ssh if "-N" in x.split()]
    assert len(tun) == 1 and "-R 127.0.0.1:16333:127.0.0.1:6333" in tun[0] and "ExitOnForwardFailure=yes" in tun[0]
    assert "ServerAliveInterval=30" in tun[0] and "-p 2222" in tun[0]
    assert tunnel_dead(pt)
    rs = log(pt, "rsync")
    assert len(rs) == 3 and all("--no-links" in x and " -z" not in x and "1.2.3.4:/srv/src-root/" in x for x in rs)
    assert any("--exclude .env*" in x for x in rs if "exploitdb-mcp-server" in x)
    remote = [x for x in ssh if "COLL=c" in x][0]
    assert "bash -s" in remote and "BW_MBPS=" in remote
    # chi snapshot cua lan chay nay bi xoa; snapshot co san, snapshot ten moi nhung tao truoc, collection khac: giu nguyen
    assert deleted(pt) == ["c-1-own-run.snapshot"]
    left = snap_names(pt)
    assert all(s["name"] in left for s in OLD) and "c-1-foreign-early.snapshot" in left and "other-1-x.snapshot" in left
    pc = json.loads((pt.sdir / "packtest/prod-collection.json").read_text())
    assert pc["points_count"] == 842928 and pc["payload_schema_keys"] == ["cve_id", "kind"]
    assert "packtest XONG" in r.stdout
    assert_prod_alive(pt)


def test_packtest_remote_failure_still_kills_tunnel_and_cleans(pt):
    (pt.fake / "packtest_rc").write_text("1")
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0
    assert tunnel_dead(pt) and deleted(pt) == ["c-1-own-run.snapshot"]
    assert all(s["name"] in snap_names(pt) for s in OLD)


def test_packtest_alerts_when_local_disk_not_recovered(pt):
    script = (pt.tmp / "bin/df")
    # lan doc dau 100 GB, sau do 90 GB (snapshot con nam tren dia prod)
    stub(script, 'echo Avail; if [ -f "$FAKE_DIR/df_seen" ]; then echo 96636764160; else touch "$FAKE_DIR/df_seen"; echo 107374182400; fi')
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and "1 GB" in (pt.sdir / "ALERT.txt").read_text()


def test_packtest_key_via_env_never_in_argv_or_output(pt):
    r = sh(EVAL, "packtest", b=pt, extra={"QDRANT_API_KEY": QKEY})
    assert r.returncode == 0, r.stderr
    assert QKEY not in argv_all(pt) and QKEY not in r.stdout + r.stderr
    assert f'header = "api-key: {QKEY}"' in (pt.fake / "curl.stdin").read_text()  # curl -K -
    assert all("-K -" in x for x in log(pt, "curl"))
    assert QKEY in (pt.fake / "ssh.stdin").read_text()
    assert any("cat > /srv/packtest/qkey" in x and "umask 077" in x for x in log(pt, "ssh"))
    assert any("shred -u /srv/packtest/qkey" in x for x in log(pt, "ssh"))
    for f in (pt.sdir / "packtest").rglob("*"):
        assert QKEY not in f.read_text(), f


def test_packtest_key_file_must_be_0600(pt):
    kf = pt.tmp / "qkey"
    kf.write_text(QKEY + "\n")
    kf.chmod(0o644)
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_QDRANT_KEY_FILE": str(kf)})
    assert r.returncode != 0 and log(pt, "curl") == [] and QKEY not in r.stderr
    kf.chmod(0o600)
    r = sh(EVAL, "packtest", b=pt, extra={"PACKTEST_QDRANT_KEY_FILE": str(kf)})
    assert r.returncode == 0, r.stderr
    assert QKEY not in argv_all(pt) and QKEY in (pt.fake / "curl.stdin").read_text()


def test_packtest_refuses_past_deadline(pt):
    now = int(time.time())
    write_state(pt, ssh_host="1.2.3.4", ssh_port=2222, mode="packtest", created_at=now - 3 * 3600, deadline=now + 300)
    r = sh(EVAL, "packtest", b=pt)
    assert r.returncode != 0 and "deadline" in r.stderr and log(pt, "rsync") == []
    assert not (pt.fake / "tunnel.pid").exists()


def test_packtest_writes_only_under_stack_eval_dir(pt):
    before = {p for p in pt.tmp.rglob("*") if p.is_file()}
    assert sh(EVAL, "packtest", b=pt).returncode == 0
    new = {p for p in pt.tmp.rglob("*") if p.is_file()} - before
    assert all(p.is_relative_to(pt.sdir) or p.is_relative_to(pt.fake) for p in new), new


# ------------------------------------------------------------------ REMOTE_PACKTEST chay that voi stub

class FakeQdrant(BaseHTTPRequestHandler):
    points = 842928
    keys: list = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/collections/"):
            self._send({"result": {"points_count": FakeQdrant.points, "config": {"params": {
                "vectors": {"dense": {"size": 4}}, "sparse_vectors": {"bm25": {"modifier": "idf"}}}},
                "payload_schema": {"cve_id": {}}}})
        else:
            self._send({"title": "qdrant"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeQdrant.keys.append(self.headers.get("api-key"))
        if self.path.endswith("/scroll"):
            self._send({"result": {"points": [{"id": i} for i in range(1, 31)]}})
        else:
            self._send({"result": [{"id": i, "payload": {"cve_id": f"CVE-{i}"},
                                    "vector": {"dense": [0.1] * 4, "bm25": {"indices": [1], "values": [1.0]}}} for i in body["ids"]]})


@pytest.fixture
def qsrv():
    FakeQdrant.points, FakeQdrant.keys = 842928, []
    s = ThreadingHTTPServer(("127.0.0.1", 0), FakeQdrant)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s.server_address[1]
    s.shutdown()


def run_remote_packtest(tmp_path: Path, port: int, *, prod_points=842928, neo4j_expect="", pack_rc=0, drop_file=False, qkey=True):
    root = tmp_path / "vm"
    lines = EVAL.read_text().splitlines()
    start = next(i for i, x in enumerate(lines) if "<<'REMOTE_PACKTEST'" in x)
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "REMOTE_PACKTEST")
    body = "\n".join(lines[start + 1:end])
    binp = tmp_path / "bin"
    body = (body.replace("/opt/secai-stack", str(root / "opt")).replace("/srv", str(root / "srv"))
            .replace("/usr/bin/time", str(binp / "time")).replace("127.0.0.1:16333", f"127.0.0.1:{port}")
            .replace("127.0.0.1:16334", f"127.0.0.1:{port}"))
    script = tmp_path / "remote.sh"
    script.write_text(body + "\n")
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
    (srv / "packtest").mkdir(parents=True)
    (srv / "packtest/prod-collection.json").write_text(json.dumps({
        "points_count": prod_points, "params": {"vectors": {"dense": {"size": 4}}, "sparse_vectors": {"bm25": {"modifier": "idf"}}},
        "payload_schema_keys": ["cve_id"]}))
    if qkey:
        (srv / "packtest/qkey").write_text(QKEY)
    fake = tmp_path / "seen"
    fake.mkdir()
    stub(st / "pack.sh", f'echo "${{QDRANT_API_KEY-unset}}" > "{fake}/pack_key"\n[ {pack_rc} = 0 ] || exit {pack_rc}\n'
         'eval out=\\${$#}; eval s=\\${$(($#-1))}; mkdir -p "$out/qdrant" "$out/neo4j"; echo snap > "$out/qdrant/c.snapshot"\n'
         'tar -C "$s" -cf "$out/all.tar" exploitdb poc poc-sources exploitdb-mcp-server\n'
         '(cd "$out" && sha256sum qdrant/c.snapshot all.tar > SHA256SUMS)')
    stub(st / "restore-data.sh", f'echo "${{QDRANT_API_KEY-unset}}" > "{fake}/restore_key"\neval out=\\${{$#}}\n'
         'mkdir -p "$DATA_ROOT/_state/neo4j_data/databases"; tar -C "$DATA_ROOT" -xf "$out/all.tar"'
         + ('\nrm "$DATA_ROOT/poc/store/x"' if drop_file else ""))
    stub(binp / "docker", 'case "$1" in exec) printf "count(n)\\n42\\n" ;; *) echo cid ;; esac')
    stub(binp / "time", 'shift 2; f=$1; shift; printf "\\tMaximum resident set size (kbytes): 1234\\n\\tElapsed (wall clock) time (h:mm:ss or m:ss): 0:01.00\\n" > "$f"; exec "$@"')
    # chay khong root: gia lap quyen restore-data.sh dat (chown can root)
    stub(binp / "stat", 'case "$*" in *"%u:%g %a"*) echo "10001:10100 775" ;; *"%u"*) echo 7474 ;; *) exec /usr/bin/stat "$@" ;; esac')
    env = {"PATH": f"{binp}:/usr/bin:/bin", "COLL": "c", "NEO4J_EXPECT": neo4j_expect, "VM_MIN_FREE_GB": "0",
           "STAGE_SECS": "5", "STAGE_BYTES": "100", "BW_MBPS": "50.0", "HOME": str(tmp_path)}
    r = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True, timeout=120)
    return r, root / "opt/results", fake, srv


def summary(R: Path) -> dict:
    return json.loads((R / "packtest-summary.json").read_text())


def test_remote_packtest_pass_records_timings_disk_counts_and_no_secret(tmp_path, qsrv):
    r, R, seen, srv = run_remote_packtest(tmp_path, qsrv)
    s = summary(R)
    assert r.returncode == 0 and s["verdict"] == "pass", (r.stdout, r.stderr, s["checks"])
    ck = s["checks"]
    for name in ("pack", "restore", "package_sha256", "qdrant_points", "qdrant_vectors_config", "qdrant_payload_indexes",
                 "qdrant_sample_points", "files_exploitdb", "files_poc_store", "files_poc-sources", "files_exploitdb-mcp-server",
                 "files_catalog_rows", "perm_poc", "perm_poc_store", "perm_neo4j_data"):
        assert ck[name]["status"] == "pass", (name, ck[name])
    assert ck["neo4j_nodes"] == {"status": "recorded", "detail": "42"}  # khong co so mong doi -> chi ghi lai
    assert s["timings_s"]["stage_rsync"] == 5 and isinstance(s["timings_s"]["pack"], int) and isinstance(s["timings_s"]["restore"], int)
    assert s["time_v"]["pack"]["max_rss_kb"] == 1234
    assert s["disk"]["samples"] >= 1 and s["disk"]["used_peak"] >= s["disk"]["used_start"] > 0
    assert s["sizes_bytes"]["package"] > 0 and s["bandwidth_mbps"] == 50.0
    assert s["src_facts"]["catalog_rows"] == {"pocs": 3}
    # khoa: pack.sh nhan (qua env tu tep), restore thi khong; Qdrant prod duoc goi kem header; tep khoa bi shred
    assert (seen / "pack_key").read_text().strip() == QKEY and (seen / "restore_key").read_text().strip() == "unset"
    assert QKEY in FakeQdrant.keys and not (srv / "packtest/qkey").exists()
    for f in R.rglob("*"):
        if f.is_file():
            assert QKEY not in f.read_text(errors="replace"), f
    assert subprocess.run(["sha256sum", "-c", "--strict", "--quiet", "SHA256SUMS"], cwd=R).returncode == 0
    assert not (srv / "src-root").exists()  # nguon da do roi xoa de restore vua dia


@pytest.mark.parametrize("kw,check", [
    ({"prod_points": 842927}, "qdrant_points"),
    ({"neo4j_expect": "41"}, "neo4j_nodes"),
    ({"drop_file": True}, "files_poc_store"),
])
def test_remote_packtest_mismatch_is_fail(tmp_path, qsrv, kw, check):
    r, R, _, _ = run_remote_packtest(tmp_path, qsrv, **kw)
    s = summary(R)
    assert r.returncode != 0 and s["verdict"] == "fail" and s["checks"][check]["status"] == "fail", s["checks"]


def test_remote_packtest_neo4j_expected_count_pass(tmp_path, qsrv):
    r, R, _, _ = run_remote_packtest(tmp_path, qsrv, neo4j_expect="42", qkey=False)
    s = summary(R)
    assert r.returncode == 0 and s["checks"]["neo4j_nodes"]["status"] == "pass"
    assert None in FakeQdrant.keys  # khong co khoa thi khong gui header


def test_remote_packtest_pack_failure_skips_restore_but_writes_summary(tmp_path, qsrv):
    r, R, seen, srv = run_remote_packtest(tmp_path, qsrv, pack_rc=3)
    s = summary(R)
    assert r.returncode != 0 and s["verdict"] == "fail" and s["checks"]["pack"]["status"] == "fail"
    assert "restore" not in s["checks"] and not (seen / "restore_key").exists()
    assert not (srv / "packtest/qkey").exists() and (srv / "src-root").exists()  # khoa van shred; nguon giu de dieu tra
