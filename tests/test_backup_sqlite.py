"""scripts/backup-sqlite.py và scripts/install-backup-timer.sh, offline.

`docker` là stub trên PATH: `inspect` trả container giả từ file JSON, `run` ánh xạ các --mount sang thư mục tạm
rồi chạy đúng `backup-sqlite.py --inner` trên host (cùng code chạy trong container tạm thật), ghi lại mọi lệnh.
`systemctl` là stub ghi lệnh; git là repo tạm thật (origin bare + checkout chính). Không lệnh nào chạm docker/systemd thật.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backup-sqlite.py"
INSTALL = ROOT / "scripts" / "install-backup-timer.sh"
FAKE_SECRET = "canary-5e1f0c-not-a-key"
IMAGE = "secai-agent-gateway:main-abcdef12-20261127"

_spec = importlib.util.spec_from_file_location("backup_sqlite", SCRIPT)
bs = importlib.util.module_from_spec(_spec)
sys.modules["backup_sqlite"] = bs
_spec.loader.exec_module(bs)
_spec = importlib.util.spec_from_file_location("ops_check_bk", ROOT / "scripts" / "ops-check.py")
oc = importlib.util.module_from_spec(_spec)
sys.modules["ops_check_bk"] = oc
_spec.loader.exec_module(oc)

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
state = json.load(open(os.environ["FAKE_DOCKER_STATE"]))
args = sys.argv[1:]
stdin = sys.stdin.read() if args[:1] == ["run"] else ""
with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
    f.write(json.dumps({"argv": args, "stdin": stdin}) + "\n")
if args[:1] == ["inspect"]:
    found = [state["containers"][n] for n in args[1:] if n in state["containers"]]
    for n in args[1:]:
        if n not in state["containers"]:
            print(f"Error: No such object: {n}", file=sys.stderr)
    print(json.dumps(found))
    sys.exit(0 if len(found) == len(args) - 1 else 1)
if args[:1] == ["run"]:
    mounts, i = {}, 1
    while i < len(args):
        if args[i] == "--mount":
            spec = dict(kv.split("=", 1) if "=" in kv else (kv, True) for kv in args[i + 1].split(","))
            src = state["volumes"][spec["source"]] if spec["type"] == "volume" else spec["source"]
            mounts[spec["target"]] = src
            i += 2
        elif args[i] in ("--rm", "-i", "--read-only"):
            i += 1
        elif args[i].startswith("-"):
            i += 2
        else:
            break
    rest = args[i + 1:]
    def host(p):
        t = max((t for t in mounts if p == t or p.startswith(t + "/")), key=len)
        return mounts[t] + p[len(t):]
    spec = json.loads(stdin)
    for job in spec["jobs"]:
        job["src"], job["dst"] = host(job["src"]), host(job["dst"])
    script = host(rest[rest.index("-I") + 1])
    p = subprocess.run([sys.executable, "-I", script, *rest[rest.index("-I") + 2:]], input=json.dumps(spec),
                       capture_output=True, text=True)
    sys.stdout.write(p.stdout); sys.stderr.write(p.stderr)
    sys.exit(p.returncode)
sys.exit(0)
'''


def make_db(path: Path, mode: str, rows: int, table: str = "t") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, isolation_level=None)
    db.execute(f"PRAGMA journal_mode={mode}")
    db.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, v TEXT)")
    db.execute("BEGIN")
    db.executemany(f"INSERT INTO {table}(v) VALUES (?)", [(f"r{i}",) for i in range(rows)])
    db.execute("COMMIT")
    db.close()
    old = dt.datetime.now().timestamp() - 3600
    os.utime(path, (old, old))


def count(path: Path, table: str = "t") -> int:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        db.close()


def drop_backup_env(env: dict[str, str]) -> None:
    """Test kín: SECAI_BACKUP_* của shell chạy test (vd SECAI_BACKUP_IMAGE để chạy tay) không được lọt vào."""
    for k in [k for k in env if k.startswith("SECAI_BACKUP_")]:
        del env[k]


def old_run(sq: Path, days: float, errors: list[str] | None) -> str:
    """Thư mục backup giả cũ `days` ngày; errors=None thì không có MANIFEST.json."""
    name = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y%m%d-%H%M")
    (sq / name).mkdir(parents=True)
    if errors is not None:
        (sq / name / "MANIFEST.json").write_text(json.dumps({"errors": errors}))
    return name


def container(name: str, env: dict[str, str], mounts: list[dict], pid: int = 0) -> dict:
    return {"Name": "/" + name, "Image": "sha256:" + "ab" * 32, "State": {"Running": True, "Pid": pid},
            "Config": {"Image": IMAGE, "WorkingDir": "/app", "Cmd": ["uvicorn", "secai_gateway.app:app"],
                       "Env": [f"{k}={v}" for k, v in env.items()] + [f"REPORT_API_KEY={FAKE_SECRET}", "PATH=/usr/bin"]},
            "Mounts": mounts}


class World:
    """Ba kho: volume gateway (WAL có writer đang mở, WAL đã đóng, rollback), volume dịch dùng chung 2 container,
    bind report mà docker inspect ghi đường dẫn cũ (thư mục đã đổi chỗ, mountinfo trỏ chỗ thật)."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.log = tmp / "docker.log"
        self.state_file = tmp / "docker-state.json"
        self.backup = tmp / "backups"
        self.gw = tmp / "vol-gw"
        self.tr = tmp / "vol-tr"
        self.report_real = tmp / "workspaces" / "report-data"
        self.report_stale = tmp / "old" / "report-data"
        self.report_stale.mkdir(parents=True)
        make_db(self.gw / "gateway.db", "WAL", 50)
        make_db(self.gw / "sessions.db", "WAL", 30)
        make_db(self.gw / "reviews.db", "DELETE", 20)
        make_db(self.tr / "translate-v2" / "jobs.sqlite3", "WAL", 40)
        make_db(self.tr / "translation-worker.db", "WAL", 5)
        make_db(self.report_real / "reports.db", "DELETE", 7)
        # writer đang mở: 25 dòng chỉ nằm trong -wal (chưa checkpoint)
        self.writer = sqlite3.connect(self.gw / "gateway.db", isolation_level=None)
        self.writer.execute("PRAGMA wal_autocheckpoint=0")
        self.writer.execute("BEGIN")
        self.writer.executemany("INSERT INTO t(v) VALUES (?)", [(f"w{i}",) for i in range(25)])
        self.writer.execute("COMMIT")
        assert (self.gw / "gateway.db-wal").stat().st_size > 0
        proc = tmp / "proc"
        (proc / "self").mkdir(parents=True)
        (proc / "4242").mkdir()
        (proc / "self" / "mountinfo").write_text("32 2 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw\n")
        (proc / "4242" / "mountinfo").write_text(
            "900 800 0:61 / / rw,relatime - overlay overlay rw\n"
            f"9166 900 259:2 {self.report_real} /srv/reports rw,relatime - ext4 /dev/nvme0n1p2 rw\n")
        self.proc = proc
        self.containers = {
            "secai-docker-gateway": container("secai-docker-gateway", {
                "GATEWAY_DB_PATH": "/app/data/gateway.db", "SESSION_DB_PATH": "/app/data/sessions.db",
                "REVIEW_DB_PATH": "/app/data/reviews.db"},
                [{"Type": "volume", "Name": "gw", "Source": "/var/lib/docker/volumes/gw/_data", "Destination": "/app/data"}]),
            "secai-translation-gateway": container("secai-translation-gateway", {
                "TRANSLATE_V2_DB_PATH": "/app/data/translate-v2/jobs.sqlite3",
                "TRANSLATE_V2_WORKER_DB_PATH": "/app/data/translation-worker.db",
                "REVIEW_DB_PATH": "/app/review-data/reviews.db", "GATEWAY_DB_PATH": "/app/data/translation-worker.db"},
                [{"Type": "volume", "Name": "tr", "Source": "/x", "Destination": "/app/data"}]),
            "secai-translation-worker": container("secai-translation-worker", {
                "GATEWAY_DB_PATH": "/app/data/translation-worker.db",
                "TRANSLATE_V2_DB_PATH": "/app/data/translate-v2/jobs.sqlite3"},
                [{"Type": "volume", "Name": "tr", "Source": "/x", "Destination": "/app/data"}]),
            "secai-report-backend": container("secai-report-backend", {"REPORT_DB_PATH": "/srv/reports/reports.db"},
                [{"Type": "bind", "Source": str(self.report_stale), "Destination": "/srv/reports"}], pid=4242),
            "secai-review-frontend": container("secai-review-frontend", {"GATEWAY_DB_PATH": "/app/data/gateway.db"}, []),
        }
        self.volumes = {"gw": str(self.gw), "tr": str(self.tr)}
        (self.bin / "docker").write_text(FAKE_DOCKER)
        (self.bin / "docker").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_DOCKER_STATE": str(self.state_file),
                    "FAKE_DOCKER_LOG": str(self.log), "HOME": str(tmp / "home")}
        drop_backup_env(self.env)

    def run(self, *args: str, containers: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        self.state_file.write_text(json.dumps({"containers": self.containers, "volumes": self.volumes}))
        names = containers or list(self.containers)
        return subprocess.run([sys.executable, "-I", str(SCRIPT), "--backup-dir", str(self.backup),
                               "--containers", ",".join(names), "--proc-root", str(self.proc), *args],
                              env=self.env, capture_output=True, text=True, timeout=300)

    def calls(self) -> list[dict]:
        return [json.loads(ln) for ln in self.log.read_text().splitlines()] if self.log.exists() else []

    def runs(self) -> list[Path]:
        return sorted(p for p in (self.backup / "sqlite").iterdir() if bs.STAMP_RE.match(p.name))


@pytest.fixture
def w(tmp_path: Path):
    world = World(tmp_path)
    yield world
    world.writer.close()


def mode(p: Path) -> int:
    return p.stat().st_mode & 0o777


def test_backup_layout_integrity_checksums_and_modes(w: World) -> None:
    p = w.run()
    assert p.returncode == 0, p.stdout + p.stderr
    [run] = w.runs()
    files = sorted(str(f.relative_to(run)) for f in run.rglob("*") if f.is_file())
    assert files == ["MANIFEST.json", "SHA256SUMS", "gateway/gateway.sqlite", "gateway/reviews.sqlite",
                     "gateway/sessions.sqlite", "report/reports.sqlite", "translation/jobs.sqlite",
                     "translation/translation-worker.sqlite"]
    # nội dung: kể cả 25 dòng còn nằm trong -wal của writer đang mở
    assert count(run / "gateway/gateway.sqlite") == 75
    assert count(run / "gateway/sessions.sqlite") == 30
    assert count(run / "gateway/reviews.sqlite") == 20
    assert count(run / "translation/jobs.sqlite") == 40
    assert count(run / "translation/translation-worker.sqlite") == 5
    assert count(run / "report/reports.sqlite") == 7
    for f in run.rglob("*.sqlite"):
        assert f.read_bytes()[18:20] == b"\x01\x01", f  # bản sao tự đủ, không cần -wal
        assert not Path(str(f) + "-wal").exists() and not Path(str(f) + "-journal").exists()
    # quyền: thư mục 0700, file 0600
    for d in (w.backup, w.backup / "sqlite", run, *[x for x in run.iterdir() if x.is_dir()]):
        assert mode(d) == 0o700, d
    for f in run.rglob("*"):
        if f.is_file():
            assert mode(f) == 0o600, f
    # SHA256SUMS khớp, đủ 6 DB
    sums = dict(reversed(ln.split("  ", 1)) for ln in (run / "SHA256SUMS").read_text().splitlines())
    assert len(sums) == 6
    for rel, digest in sums.items():
        assert hashlib.sha256((run / rel).read_bytes()).hexdigest() == digest
    man = json.loads((run / "MANIFEST.json").read_text())
    assert man["errors"] == [] and man["image"] == IMAGE
    by = {d["file"]: d for d in man["databases"] if not d.get("skipped")}
    assert all(d["ok"] and d["integrity"] == "ok" for d in by.values())
    assert by["gateway/gateway.sqlite"]["method"] == "ro" and by["gateway/gateway.sqlite"]["source_journal"] == "wal"
    assert by["gateway/sessions.sqlite"]["method"] == "immutable-verified"  # WAL đã đóng, không có -wal/-shm
    assert by["gateway/reviews.sqlite"]["source_journal"] == "rollback"
    # DB dùng chung volume giữa translation-gateway và worker chỉ sao một lần, ghi đủ nơi dùng
    users = {(u["container"], u["var"]) for u in by["translation/translation-worker.sqlite"]["used_by"]}
    assert users == {("secai-translation-gateway", "TRANSLATE_V2_WORKER_DB_PATH"),
                     ("secai-translation-gateway", "GATEWAY_DB_PATH"),
                     ("secai-translation-worker", "GATEWAY_DB_PATH")}
    # nguồn DB prod không bị đổi: writer vẫn thấy đủ dữ liệu, DB rollback không sinh file phụ
    assert w.writer.execute("SELECT count(*) FROM t").fetchone()[0] == 75
    assert sorted(x.name for x in w.gw.iterdir()) == ["gateway.db", "gateway.db-shm", "gateway.db-wal",
                                                      "reviews.db", "sessions.db"]


def test_stale_bind_source_follows_mountinfo(w: World) -> None:
    p = w.run(containers=["secai-report-backend"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert "thư mục đã bị đổi chỗ" in p.stderr and str(w.report_real) in p.stderr
    [run] = w.runs()
    assert count(run / "report/reports.sqlite") == 7
    assert list(w.report_stale.iterdir()) == []  # thư mục cũ (rỗng) không bị đụng
    run_call = [c for c in w.calls() if c["argv"][0] == "run"][0]
    assert f"type=bind,source={w.report_real},target=/src/0,readonly" in run_call["argv"]


def test_deleted_bind_source_is_an_error_not_a_crash(w: World) -> None:
    (w.proc / "4242" / "mountinfo").write_text(
        f"9166 900 259:2 {w.report_real}//deleted /srv/reports rw - ext4 /dev/x rw\n")
    p = w.run(containers=["secai-report-backend", "secai-docker-gateway"])
    assert p.returncode == 1
    assert "đã bị xoá trên host" in p.stderr
    [run] = w.runs()
    assert (run / "gateway" / "gateway.sqlite").is_file() and not (run / "report").exists()


def test_container_is_sandboxed_and_only_inspect_and_run_are_called(w: World) -> None:
    assert w.run().returncode == 0
    verbs = [c["argv"][0] for c in w.calls()]
    assert verbs == ["inspect", "run"]
    argv = w.calls()[1]["argv"]
    joined = " ".join(argv)
    for flag in ("--rm", "--read-only", "--network none", "--cap-drop ALL", "--security-opt no-new-privileges"):
        assert flag in joined, flag
    assert re.fullmatch(r"secai-sqlite-backup-\d{8}-\d{4}", argv[argv.index("--name") + 1])
    assert "-v" not in argv and "--volume" not in argv and "-e" not in argv and "--env" not in argv
    srcs = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount" and "target=/src/" in argv[i + 1]]
    assert len(srcs) == 3 and all(s.endswith(",readonly") for s in srcs)  # gw, tr, report: mỗi nguồn một lần
    assert argv[argv.index(IMAGE) + 1:] == ["-I", bs.JOB_SCRIPT, "--inner"]


def test_secret_env_values_never_leak(w: World) -> None:
    p = w.run()
    assert p.returncode == 0, p.stderr
    assert FAKE_SECRET not in p.stdout + p.stderr
    assert FAKE_SECRET not in w.log.read_text()  # argv và stdin của container tạm
    [run] = w.runs()
    assert FAKE_SECRET not in (run / "MANIFEST.json").read_text()


def test_dry_run_starts_no_container_and_writes_nothing(w: World) -> None:
    p = w.run("--dry-run")
    assert p.returncode == 0, p.stderr
    assert [c["argv"][0] for c in w.calls()] == ["inspect"]
    assert not w.backup.exists()
    assert "gateway/gateway.sqlite" in p.stdout and "translation/jobs.sqlite" in p.stdout


def test_retention_removes_only_matching_old_dirs(w: World) -> None:
    sq = w.backup / "sqlite"
    sq.mkdir(parents=True)
    now = dt.datetime.now()
    old = (now - dt.timedelta(days=8)).strftime("%Y%m%d-%H%M")
    recent = (now - dt.timedelta(days=6)).strftime("%Y%m%d-%H%M")
    keep_names = [recent, f"manual-{old}", f"{old}.keep", "20261399-9999"]
    for n in [old, *keep_names]:
        (sq / n).mkdir()
        (sq / n / "x.sqlite").write_text("x")
    outside = w.tmp / "elsewhere"
    outside.mkdir()
    (outside / "precious").write_text("x")
    link_name = (now - dt.timedelta(days=9)).strftime("%Y%m%d-%H%M")
    (sq / link_name).symlink_to(outside, target_is_directory=True)
    file_name = (now - dt.timedelta(days=10)).strftime("%Y%m%d-%H%M")
    (sq / file_name).write_text("x")
    (w.backup / old).mkdir()  # ngoài sqlite/: không thuộc phạm vi dọn
    stale_staging = w.backup / ".staging" / f"sqlite-{old}"
    stale_staging.mkdir(parents=True)
    p = w.run()
    assert p.returncode == 0, p.stderr
    left = {x.name for x in sq.iterdir()}
    assert old not in left
    assert set(keep_names) | {link_name, file_name} <= left
    assert (outside / "precious").exists() and (w.backup / old).is_dir()
    assert not stale_staging.exists()
    new = {r.name for r in w.runs()} - set(keep_names) - {link_name, file_name}
    assert len(new) == 1 and (sq / new.pop() / "gateway" / "gateway.sqlite").is_file()


def test_failed_db_keeps_others_and_newest_complete_backup(w: World) -> None:
    (w.tr / "translate-v2" / "jobs.sqlite3").write_bytes(b"not a database" * 10)
    sq = w.backup / "sqlite"
    old = old_run(sq, 30, [])
    p = w.run()
    assert p.returncode == 1
    assert "translation/jobs.sqlite" in p.stderr and "header" in p.stderr
    assert (sq / old).is_dir()  # bản đủ mới nhất (còn jobs.sqlite) được giữ dù quá hạn
    run = [r for r in w.runs() if r.name != old][0]
    assert not (run / "translation" / "jobs.sqlite").exists()
    assert count(run / "gateway/gateway.sqlite") == 75
    man = json.loads((run / "MANIFEST.json").read_text())
    assert any("translation/jobs.sqlite" in e for e in man["errors"])
    assert "translation/jobs.sqlite" not in (run / "SHA256SUMS").read_text()


def test_persistent_error_still_prunes_but_keeps_newest_complete_backup(w: World) -> None:
    """Lỗi kéo dài (vd container trong danh sách đã bị gỡ) không được làm đĩa prod đầy dần: bản quá hạn vẫn bị
    dọn, chỉ giữ thêm bản đủ (MANIFEST errors rỗng) mới nhất làm lưới an toàn."""
    sq = w.backup / "sqlite"
    complete_old = old_run(sq, 20, [])
    complete_newest = old_run(sq, 12, [])
    partial_old = [old_run(sq, d, ["không thấy container secai-review-canary"]) for d in (8, 9, 10, 11)]
    no_manifest = old_run(sq, 9.5, None)
    partial_recent = old_run(sq, 3, ["x"])
    p = w.run(containers=[*w.containers, "secai-review-canary"])
    assert p.returncode == 1 and "không thấy container secai-review-canary" in p.stderr
    left = {r.name for r in w.runs()}
    assert {complete_newest, partial_recent} < left and len(left) == 3
    assert not {complete_old, no_manifest, *partial_old} & left


def test_missing_explicit_db_and_missing_container_are_errors(w: World) -> None:
    (w.gw / "reviews.db").unlink()
    p = w.run(containers=["secai-docker-gateway", "secai-ghost"])
    assert p.returncode == 1
    assert "không thấy container secai-ghost" in p.stderr
    assert "gateway/reviews.sqlite: không có file" in p.stderr
    # mặc định của config.py không có file (vd REPORT_DB_PATH) thì bỏ qua lặng lẽ
    assert "reports.sqlite" not in p.stderr


def test_nothing_copied_publishes_nothing(w: World) -> None:
    """Không DB nào sao được thì không tạo thư mục trong sqlite/ (OPS-BAK-2 sẽ tưởng backup còn hạn)."""
    (w.report_real / "reports.db").write_bytes(b"garbage" * 20)
    old = old_run(w.backup / "sqlite", 30, None)
    p = w.run(containers=["secai-report-backend"])
    assert p.returncode == 1 and "không sao được DB nào" in p.stderr
    assert [r.name for r in w.runs()] == [old]  # không công bố gì thì cũng không dọn gì
    [staging] = (w.backup / ".staging").iterdir()
    assert "report/reports.sqlite" in json.loads((staging / "MANIFEST.json").read_text())["errors"][0]


def test_dsn_like_db_path_is_ignored(w: World) -> None:
    w.containers["secai-docker-gateway"]["Config"]["Env"].append("ANALYTICS_DB_PATH=postgres://u@h/db")
    p = w.run("--dry-run", containers=["secai-docker-gateway"])
    assert p.returncode == 0 and "ANALYTICS_DB_PATH" not in p.stdout + p.stderr and "postgres" not in p.stdout


def test_same_db_name_on_two_mounts_gets_two_files(w: World) -> None:
    """Hai container cùng service `translation`, mỗi container một volume có gateway.db ở gốc: hai file riêng."""
    trw = w.tmp / "vol-trw"
    make_db(w.tr / "gateway.db", "DELETE", 3)
    make_db(trw / "gateway.db", "DELETE", 4)
    w.volumes["trw"] = str(trw)
    for name, vol in (("secai-translation-gateway", "tr"), ("secai-translation-worker", "trw")):
        w.containers[name] = container(name, {"GATEWAY_DB_PATH": "/app/data/gateway.db"},
                                       [{"Type": "volume", "Name": vol, "Source": "/x", "Destination": "/app/data"}])
    p = w.run(containers=["secai-translation-gateway", "secai-translation-worker"])
    assert p.returncode == 0, p.stdout + p.stderr
    [run] = w.runs()
    man = json.loads((run / "MANIFEST.json").read_text())
    gw = {d["mount"]["source"]: d for d in man["databases"] if d["path_in_mount"] == "gateway.db"}
    assert gw["tr"]["file"] != gw["trw"]["file"]
    assert count(run / gw["tr"]["file"]) == 3 and count(run / gw["trw"]["file"]) == 4
    assert gw["tr"]["sha256"] != gw["trw"]["sha256"]
    files = [ln.split("  ", 1)[1] for ln in (run / "SHA256SUMS").read_text().splitlines()]
    assert len(files) == len(set(files))


def test_plan_names_are_unique_per_service() -> None:
    vol = lambda v: [{"Type": "volume", "Name": v, "Source": "/x", "Destination": "/d"}]
    found = {"secai-x": container("secai-x", {"A_DB_PATH": "/d/a/jobs.db", "B_DB_PATH": "/d/jobs.db",
                                              "C_DB_PATH": "/d/a_jobs.db"}, vol("v1")),
             "secai-translation-gateway": container("secai-translation-gateway", {"GATEWAY_DB_PATH": "/d/g.db"}, vol("v2")),
             "secai-translation-worker": container("secai-translation-worker", {"GATEWAY_DB_PATH": "/d/g.db"}, vol("v3"))}
    plan, _, errs = bs.build_plan(found, list(found), Path("/nonexistent-proc"))
    assert errs == [] and len(plan) == 5
    names = [(e.service, e.name) for e in plan]
    assert len(names) == len(set(names)), names
    assert all(n.endswith(".sqlite") and "/" not in n for _, n in names)


def test_integrity_failure_is_an_error_and_not_published(w: World) -> None:
    """Header và trang đúng nhưng bảng lệch index: chỉ PRAGMA integrity_check trên bản sao bắt được."""
    src = w.gw / "reviews.db"
    db = sqlite3.connect(src, isolation_level=None)
    db.execute("CREATE TABLE iv(id INTEGER PRIMARY KEY, v TEXT)")
    db.executemany("INSERT INTO iv(v) VALUES (?)", [(f"value-{i:06d}",) for i in range(200)])
    db.execute("CREATE INDEX iv_v ON iv(v)")  # trang index cấp sau trang bảng: lần xuất hiện đầu là trong bảng
    db.close()
    data = bytearray(src.read_bytes())
    i = data.find(b"value-000100")
    data[i:i + 12] = b"value-000999"
    src.write_bytes(bytes(data))
    p = w.run()
    assert p.returncode == 1, p.stdout + p.stderr
    [run] = w.runs()
    man = json.loads((run / "MANIFEST.json").read_text())
    assert any(e.startswith("gateway/reviews.sqlite: integrity_check: ") for e in man["errors"]), man["errors"]
    [item] = [d for d in man["databases"] if d["file"] == "gateway/reviews.sqlite"]
    assert item["ok"] is False and "missing from index" in item["integrity"] and "sha256" not in item
    assert not (run / "gateway" / "reviews.sqlite").exists()
    assert "gateway/reviews.sqlite" not in (run / "SHA256SUMS").read_text()
    assert count(run / "gateway/gateway.sqlite") == 75
    assert src.read_bytes() == bytes(data)  # nguồn không bị đụng


def test_malformed_db_is_reported_per_db_not_crash(tmp_path: Path) -> None:
    """Hỏng nặng hơn: integrity_check ném DatabaseError; DB đó báo lỗi, DB khác vẫn sao được."""
    bad, good = tmp_path / "bad.db", tmp_path / "good.db"
    make_db(bad, "DELETE", 50)
    make_db(good, "DELETE", 5)
    data = bytearray(bad.read_bytes())
    ps = int.from_bytes(data[16:18], "big")
    data[100:ps] = b"\xff" * (ps - 100)  # header 100 byte còn đúng, trang 1 (sqlite_schema) hỏng
    bad.write_bytes(bytes(data))
    out = tmp_path / "out"
    out.mkdir()
    jobs = [{"id": i, "src": str(s), "dst": str(out / f"{s.stem}.sqlite")} for i, s in enumerate((bad, good))]
    buf = io.StringIO()
    spec = {"jobs": jobs, "uid": os.getuid(), "gid": os.getgid(), "min_free": 0}
    assert bs.inner_main(io.StringIO(json.dumps(spec)), buf) == 0
    r_bad, r_good = json.loads(buf.getvalue())["results"]
    assert r_bad["ok"] is False and r_bad["error"].startswith("DatabaseError: ") and "malformed" in r_bad["error"]
    assert r_good["ok"] is True and r_good["integrity"] == "ok" and count(out / "good.sqlite") == 5


def test_concurrent_run_is_refused(w: World) -> None:
    w.backup.mkdir(mode=0o700)
    with open(w.backup / ".sqlite-backup.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        p = w.run()
    assert p.returncode == 1 and "đang có lần backup khác" in p.stderr
    assert [c["argv"][0] for c in w.calls()] == ["inspect"]


def test_immutable_copy_retries_until_file_is_stable(tmp_path: Path) -> None:
    src = tmp_path / "s.db"
    make_db(src, "WAL", 3)
    (tmp_path / "out").mkdir()
    real = bs._sig(str(src))
    changed = (real[0], real[1] + 4096, real[2], real[3])
    seq = iter([real, changed, real, real])
    slept: list[float] = []
    job = {"id": 0, "src": str(src), "dst": str(tmp_path / "out" / "s.sqlite")}
    r = bs.backup_one(job, os.getuid(), os.getgid(), 0, stat_fn=lambda _: next(seq), sleep=slept.append)
    assert r["ok"] and r["method"] == "immutable-verified" and r["attempts"] == 2 and slept == [1]
    always = iter([real, changed] * bs.IMMUTABLE_ATTEMPTS)
    r = bs.backup_one(job, os.getuid(), os.getgid(), 0, stat_fn=lambda _: next(always), sleep=lambda s: None)
    assert not r["ok"] and "đổi liên tục" in r["error"]


def test_wal_with_only_one_sidecar_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "s.db"
    make_db(src, "WAL", 3)
    Path(str(src) + "-wal").write_bytes(b"")
    (tmp_path / "out").mkdir()
    r = bs.backup_one({"id": 0, "src": str(src), "dst": str(tmp_path / "out" / "s.sqlite")}, os.getuid(), os.getgid(), 0)
    assert not r["ok"] and "-wal/-shm" in r["error"]


def test_low_free_space_is_refused(tmp_path: Path) -> None:
    src = tmp_path / "s.db"
    make_db(src, "DELETE", 3)
    (tmp_path / "out").mkdir()
    r = bs.backup_one({"id": 0, "src": str(src), "dst": str(tmp_path / "out" / "s.sqlite")}, os.getuid(), os.getgid(),
                      min_free=1 << 62)
    assert not r["ok"] and "thiếu chỗ" in r["error"] and not (tmp_path / "out" / "s.sqlite").exists()


def test_layout_is_what_ops_check_bak2_measures(w: World) -> None:
    """OPS-BAK-2 đọc mtime trong <đích>/sqlite/ sâu tối đa 3 cấp: bản sao phải nằm trong tầm đó."""
    assert w.run().returncode == 0
    cfg = oc.Config(repo=ROOT, docker_dir=w.tmp, releases_dir=w.tmp, data_root=w.tmp, backup_dir=w.backup,
                    gh_repo=None, use_gh=False, cron_globs=[])
    r = oc.bak_fresh(oc.Ctx(cfg, oc.Runner(), dt.datetime.now().astimezone()))
    assert r.status == oc.FAIL  # chưa có qdrant/ neo4j/ trong bài test này
    assert "qdrant" in r.evidence and "neo4j" in r.evidence and "sqlite" not in r.evidence
    assert oc.DEFAULT_BACKUP_DIR == bs.DEFAULT_BACKUP_DIR == "~/secai-backups"  # checker mặc định nhìn đúng chỗ


# ------------------------------------------------------------------ install-backup-timer.sh
class TimerSandbox:
    def __init__(self, tmp: Path, with_script: bool = True) -> None:
        self.home = tmp / "home"
        self.home.mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.calls = tmp / "systemctl.log"
        (self.bin / "systemctl").write_text(f"#!/bin/sh\necho \"$@\" >> '{self.calls}'\nexit 0\n")
        (self.bin / "systemctl").chmod(0o755)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(self.home), "PATH": f"{self.bin}:{os.environ['PATH']}",
               "GIT_CONFIG_NOSYSTEM": "1"}
        drop_backup_env(env)
        self.env = env
        self.origin, self.main = tmp / "origin.git", tmp / "main"
        run = lambda *a, cwd=tmp: subprocess.run(a, cwd=cwd, env=env, check=True, capture_output=True, text=True)
        run("git", "init", "-q", "--bare", "-b", "main", str(self.origin))
        run("git", "init", "-q", "-b", "main", str(self.main))
        (self.main / "scripts").mkdir()
        (self.main / "README").write_text("x\n")
        if with_script:
            (self.main / "scripts" / "backup-sqlite.py").write_bytes(SCRIPT.read_bytes())
        run("git", "add", "-A", cwd=self.main)
        run("git", "commit", "-q", "-m", "init", cwd=self.main)
        run("git", "remote", "add", "origin", str(self.origin), cwd=self.main)
        run("git", "push", "-q", "origin", "main", cwd=self.main)
        run("git", "fetch", "-q", "origin", cwd=self.main)

    def install(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(INSTALL), *args], cwd=self.main, env=self.env, capture_output=True, text=True)


def test_install_timer_writes_units_without_touching_real_systemd(tmp_path: Path) -> None:
    sb = TimerSandbox(tmp_path)
    p = sb.install()
    assert p.returncode == 0, p.stderr
    units = sb.home / ".config" / "systemd" / "user"
    service = (units / "secai-sqlite-backup.service").read_text()
    timer = (units / "secai-sqlite-backup.timer").read_text()
    runner = sb.home / ".local" / "bin" / "secai-sqlite-backup-run"
    assert f"ExecStart={runner}" in service
    assert "SECAI_BACKUP_DIR" not in service  # đích chỉ đặt một chỗ: runner (biến môi trường thắng)
    cal = re.search(r"^OnCalendar=\*-\*-\* (\d\d):(\d\d):00$", timer, re.M)
    delay = int(re.search(r"^RandomizedDelaySec=(\d+)min$", timer, re.M).group(1))
    start = int(cal.group(1)) * 60 + int(cal.group(2))
    assert not (3 * 60 <= start + delay and start < 5 * 60)  # cả khung chạy nằm ngoài 03:00–05:00
    assert "Persistent=false" in timer
    # tên timer: OPS-BAK-1 nhận là timer backup cho sqlite (không mạo danh timer chung secai-backup*)
    assert re.search(oc.BACKUP_KEYWORDS["sqlite"], "secai-sqlite-backup.timer")
    assert not re.fullmatch(r"secai-backup[\w.-]*\.timer", "secai-sqlite-backup.timer")
    calls = sb.calls.read_text().splitlines()
    assert calls[:2] == ["--user daemon-reload", "--user enable --now secai-sqlite-backup.timer"]
    # runner lấy script từ origin/main và chuyển tham số: chạy thử --dry-run với docker giả không có container
    (sb.bin / "docker").write_text("#!/bin/sh\n[ \"$1\" = inspect ] && { echo '[]'; echo 'Error: No such object' >&2; exit 1; }\nexit 0\n")
    (sb.bin / "docker").chmod(0o755)
    r = subprocess.run([str(runner), "--dry-run"], env=sb.env, capture_output=True, text=True)
    lib = sb.home / ".local" / "lib" / "secai-sqlite-backup" / "scripts" / "backup-sqlite.py"
    assert lib.read_bytes() == SCRIPT.read_bytes()
    assert r.returncode == 1 and "không thấy container secai-docker-gateway" in r.stderr
    assert "secai-review-frontend" not in r.stderr  # frontend tĩnh không mount gì: không nằm trong danh sách mặc định
    # đích: mặc định là SECAI_BACKUP_DIR lúc cài; drop-in `systemctl --user edit` (Environment=) hay shell thắng
    fakepy = tmp_path / "fakepy"
    fakepy.mkdir()
    (fakepy / "python3").write_text('#!/bin/sh\necho "dest=$SECAI_BACKUP_DIR"\n')
    (fakepy / "python3").chmod(0o755)
    dest = lambda **kw: subprocess.run([str(runner)], env={**sb.env, "PATH": f"{fakepy}:{sb.env['PATH']}", **kw},
                                       capture_output=True, text=True).stdout.strip()
    assert dest() == f"dest={sb.home}/secai-backups"
    assert dest(SECAI_BACKUP_DIR="/mnt/bk") == "dest=/mnt/bk"
    p = sb.install("--remove")
    assert p.returncode == 0, p.stderr
    assert not (units / "secai-sqlite-backup.timer").exists() and not runner.exists() and not lib.exists()
    assert "--user disable --now secai-sqlite-backup.timer" in sb.calls.read_text()


def test_install_timer_refuses_before_merge(tmp_path: Path) -> None:
    sb = TimerSandbox(tmp_path, with_script=False)
    p = sb.install()
    assert p.returncode == 2 and "origin/main chưa có scripts/backup-sqlite.py" in p.stderr
    assert not (sb.home / ".config" / "systemd").exists() and not sb.calls.exists()
