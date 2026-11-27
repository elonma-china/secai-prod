#!/usr/bin/env python3
"""Backup hằng ngày các DB SQLite của service secai trên cùng máy (online backup API, chỉ đọc DB prod).

  python3 -I scripts/backup-sqlite.py --dry-run     # chỉ docker inspect + in kế hoạch, không chạy container
  python3 -I scripts/backup-sqlite.py               # backup thật vào $SECAI_BACKUP_DIR/sqlite/<YYYYMMDD-HHMM>/

DB nào được sao, cách sao, lưu giữ, mã thoát và khôi phục: docs/operations/sqlite-backup.md.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

DEFAULT_CONTAINERS = (
    "secai-docker-gateway", "secai-translation-gateway", "secai-translation-worker",
    "secai-report-backend", "secai-review-canary",
)
SERVICE_NAMES = {
    "secai-docker-gateway": "gateway",
    "secai-translation-gateway": "translation",
    "secai-translation-worker": "translation",
    "secai-report-backend": "report",
    "secai-review-canary": "review-canary",
}
# Mặc định khi biến không đặt: src/secai_gateway/config.py và translation/settings.py (tính từ WorkingDir).
# DB theo mặc định chỉ được backup khi file có thật; DB khai báo rõ bằng biến mà không có file là lỗi.
DB_DEFAULTS = {
    "GATEWAY_DB_PATH": "./data/gateway.db",
    "SESSION_DB_PATH": "./data/sessions.db",
    "REVIEW_DB_PATH": "data/reviews.db",
    "REPORT_DB_PATH": "./data/reports.db",
    "TRANSLATE_V2_DB_PATH": "./data/translate-v2/jobs.sqlite3",
    "TRANSLATE_V2_WORKER_DB_PATH": "./data/translate-v2/worker-gateway.sqlite3",
}
DEFAULT_BACKUP_DIR = "~/secai-backups"  # = DEFAULT_BACKUP_DIR của scripts/ops-check.py (OPS-BAK-2)
STAMP_RE = re.compile(r"^\d{8}-\d{4}$")
STAGING_RE = re.compile(r"^sqlite-\d{8}-\d{4}$")
CONTAINER_PREFIX = "secai-sqlite-backup-"
IMAGE_PREFIX = "secai-agent-gateway:"
JOB_SCRIPT = "/job/backup-sqlite.py"
RUN_TIMEOUT = 2 * 3600
BUSY_TIMEOUT = 60.0
IMMUTABLE_ATTEMPTS = 5


class BackupError(Exception):
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"backup-sqlite: {msg}", file=sys.stderr, flush=True)


# ================================================================ phần chạy TRONG container tạm (--inner)
def _sig(path: str) -> tuple:
    st = os.stat(path)
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _new_dst(dst: str, uid: int, gid: int) -> None:
    for suffix in ("", "-journal", "-wal", "-shm"):
        try:
            os.unlink(dst + suffix)
        except FileNotFoundError:
            pass
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(fd, uid, gid)  # trước khi SQLite ghi: journal tạm của bản sao cũng theo chủ này
    finally:
        os.close(fd)


def _copy(src_uri: str, dst: str) -> None:
    s = sqlite3.connect(src_uri, uri=True, timeout=BUSY_TIMEOUT)
    try:
        d = sqlite3.connect(dst)
        try:
            s.backup(d)  # pages=-1: một bước, trong một read transaction -> snapshot nhất quán
        finally:
            d.close()
    finally:
        s.close()


def backup_one(job: dict, uid: int, gid: int, min_free: int, stat_fn=_sig, sleep=time.sleep) -> dict:
    """Sao một DB bằng online backup API. `src` mount read-only, `dst` nằm trong thư mục staging."""
    src, dst = job["src"], job["dst"]
    res: dict = {"id": job["id"], "ok": False}
    try:
        st = os.stat(src)
    except FileNotFoundError:
        res.update(absent=True, error="không có file")
        return res
    if not stat.S_ISREG(st.st_mode):
        res["error"] = "không phải file thường"
        return res
    with open(src, "rb") as f:
        hdr = f.read(100)
    if not hdr.startswith(b"SQLite format 3\x00"):
        res["error"] = "không phải file SQLite (header)"
        return res
    wal_mode = hdr[18] == 2 or hdr[19] == 2
    side = {s: os.path.exists(src + s) for s in ("-wal", "-shm")}
    res["source_journal"] = "wal" if wal_mode else "rollback"
    res["source_bytes"] = st.st_size
    vfs = os.statvfs(os.path.dirname(dst))
    need = st.st_size + (os.path.getsize(src + "-wal") if side["-wal"] else 0)
    if vfs.f_bavail * vfs.f_frsize - need < min_free:
        res["error"] = f"thiếu chỗ ở đích: cần {need} byte và giữ lại {min_free} byte trống"
        return res
    quoted = urllib.parse.quote(src, safe="/")
    if wal_mode and not side["-wal"] and not side["-shm"]:
        # WAL đã checkpoint hết, không ai mở DB: trên mount read-only SQLite không tạo được -shm nên phải
        # đọc immutable (không khoá). Nhất quán khi file DB không đổi trong lúc đọc: so inode/size/mtime/ctime
        # trước và sau; đổi (có checkpoint chen vào) thì đọc lại.
        res["method"] = "immutable-verified"
        for attempt in range(1, IMMUTABLE_ATTEMPTS + 1):
            res["attempts"] = attempt
            before = stat_fn(src)
            if time.time_ns() - before[2] < 2_000_000_000:  # vừa ghi xong: chờ cho mtime ổn định
                sleep(2)
                continue
            _new_dst(dst, uid, gid)
            _copy(f"file:{quoted}?mode=ro&immutable=1", dst)
            if stat_fn(src) == before:
                break
            sleep(attempt)
        else:
            res["error"] = f"DB đổi liên tục trong lúc đọc immutable ({IMMUTABLE_ATTEMPTS} lần)"
            return res
    elif wal_mode and not (side["-wal"] and side["-shm"]):
        res["error"] = "DB WAL thiếu một trong hai file -wal/-shm; không đọc read-only an toàn được"
        return res
    else:
        # Khoá chuẩn của SQLite: WAL đọc -shm read-only (giữ read-lock, chặn checkpoint ghi đè), rollback giữ
        # SHARED lock. Đọc cả frame WAL chưa checkpoint.
        res["method"] = "ro"
        res["attempts"] = 1
        _new_dst(dst, uid, gid)
        _copy(f"file:{quoted}?mode=ro", dst)
    d = sqlite3.connect(dst)
    try:
        d.execute("PRAGMA journal_mode=DELETE")  # bản sao là một file tự đủ, không cần -wal
        rows = [r[0] for r in d.execute("PRAGMA integrity_check").fetchall()]
        res["integrity"] = "ok" if rows == ["ok"] else "; ".join(map(str, rows[:5]))
        res["page_size"] = d.execute("PRAGMA page_size").fetchone()[0]
        res["page_count"] = d.execute("PRAGMA page_count").fetchone()[0]
        res["freelist_count"] = d.execute("PRAGMA freelist_count").fetchone()[0]
        res["tables"] = d.execute("SELECT count(*) FROM sqlite_schema WHERE type='table'").fetchone()[0]
    finally:
        d.close()
    for suffix in ("-journal", "-wal", "-shm"):
        if os.path.exists(dst + suffix):
            os.unlink(dst + suffix)
    res["bytes"] = os.stat(dst).st_size
    res["ok"] = res["integrity"] == "ok"
    if not res["ok"]:
        res["error"] = f"integrity_check: {res['integrity']}"
    return res


def inner_main(stdin=None, stdout=None) -> int:
    spec = json.load(stdin or sys.stdin)
    os.umask(0o077)
    out = []
    for job in spec["jobs"]:
        try:
            out.append(backup_one(job, spec["uid"], spec["gid"], spec["min_free"]))
        except Exception as e:  # một DB lỗi không chặn DB khác
            out.append({"id": job["id"], "ok": False, "error": f"{type(e).__name__}: {e}"})
    json.dump({"results": out, "sqlite_version": sqlite3.sqlite_version}, stdout or sys.stdout)
    return 0


# ================================================================ phần chạy trên host
@dataclasses.dataclass
class Mount:
    kind: str            # volume | bind
    source: str          # tên volume hoặc đường dẫn host thật
    dest: str            # đường dẫn trong container prod
    note: str = ""       # vd: nguồn bind trong docker inspect đã cũ

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.source)


@dataclasses.dataclass
class Entry:
    service: str
    mount: Mount
    rel: str
    refs: list = dataclasses.field(default_factory=list)  # (container, biến, đường dẫn trong container, khai báo?)
    name: str = ""

    @property
    def explicit(self) -> bool:
        return any(r[3] for r in self.refs)


def docker(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kw)


def inspect_containers(names: list[str]) -> tuple[dict[str, dict], list[str]]:
    """docker inspect; trả (tên -> object, tên thiếu). Không in output (có env đầy đủ)."""
    p = docker(["inspect", *names], timeout=120)
    try:
        objs = json.loads(p.stdout) if p.stdout.strip() else []
    except json.JSONDecodeError:
        raise BackupError(f"docker inspect trả output không phải JSON (mã {p.returncode})")
    found = {o.get("Name", "").lstrip("/"): o for o in objs}
    missing = [n for n in names if n not in found]
    if missing and not found and p.returncode != 0 and "no such object" not in p.stderr.lower():
        raise BackupError(f"docker inspect lỗi: {p.stderr.strip()[:200]}")
    return found, missing


def db_vars(c: dict) -> dict[str, tuple[str, bool]]:
    """Chỉ lấy biến *_DB_PATH (giá trị là đường dẫn); bổ sung mặc định của config.py."""
    out: dict[str, tuple[str, bool]] = {}
    for e in (c.get("Config") or {}).get("Env") or []:
        k, sep, v = e.partition("=")
        if sep and k.endswith("_DB_PATH") and v.strip() and "://" not in v:  # URL/DSN không phải file SQLite
            out[k] = (v.strip(), True)
    for k, v in DB_DEFAULTS.items():
        out.setdefault(k, (v, False))
    return out


def _unescape(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def parse_mountinfo(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        out.append({"dev": f[2], "root": _unescape(f[3]), "mountpoint": _unescape(f[4])})
    return out


def real_bind_source(pid: int, dest: str, inspect_src: str, proc: Path) -> tuple[str, str]:
    """Nguồn bind thật theo kernel (/proc/<pid>/mountinfo). `docker inspect` giữ đường dẫn lúc tạo container:
    thư mục bị đổi tên sau đó thì inspect trỏ vào chỗ cũ (có khi là thư mục rỗng tạo lại), còn container vẫn
    thấy inode cũ. Không đọc được mountinfo thì dùng nguồn trong inspect."""
    if not pid:
        return inspect_src, ""
    try:
        cm = parse_mountinfo((proc / str(pid) / "mountinfo").read_text())
        hm = parse_mountinfo((proc / "self" / "mountinfo").read_text())
    except OSError:
        return inspect_src, ""
    hit = [m for m in cm if m["mountpoint"] == dest]
    if not hit:
        return inspect_src, ""
    m = hit[-1]
    root = m["root"]
    if root.endswith("//deleted"):
        raise BackupError(f"nguồn bind của {dest} đã bị xoá trên host ({root}); không backup được từ host")
    best = None
    for h in hm:
        hr = h["root"].rstrip("/")
        if h["dev"] == m["dev"] and (root == h["root"] or root.startswith(hr + "/")):
            if best is None or len(h["root"]) > len(best["root"]):
                best = h
    if best is None:
        return inspect_src, ""
    rel = root[len(best["root"].rstrip("/")):]
    real = os.path.normpath(best["mountpoint"].rstrip("/") + "/" + rel.lstrip("/"))
    if real != os.path.normpath(inspect_src):
        return real, f"docker inspect ghi {inspect_src} nhưng container đang mount {real} (thư mục đã bị đổi chỗ)"
    return real, ""


def find_mount(c: dict, path: str, proc: Path) -> Mount | None:
    best = None
    for m in c.get("Mounts") or []:
        d = m.get("Destination") or ""
        if path == d or path.startswith(d.rstrip("/") + "/"):
            if best is None or len(d) > len(best.get("Destination") or ""):
                best = m
    if best is None or best.get("Type") not in ("volume", "bind"):
        return None
    if best["Type"] == "volume":
        return Mount("volume", best["Name"], best["Destination"])
    pid = int(((c.get("State") or {}).get("Pid")) or 0)
    src, note = real_bind_source(pid, best["Destination"], best["Source"], proc)
    return Mount("bind", src, best["Destination"], note)


def build_plan(found: dict[str, dict], order: list[str], proc: Path) -> tuple[list[Entry], list[str], list[str]]:
    """(kế hoạch, ghi chú, lỗi). Một DB cùng volume/bind + đường dẫn chỉ sao một lần dù nhiều container dùng."""
    entries: dict[tuple, Entry] = {}
    notes: list[str] = []
    errs: list[str] = []
    for cname in order:
        c = found.get(cname)
        if c is None:
            continue
        wd = (c.get("Config") or {}).get("WorkingDir") or "/"
        service = SERVICE_NAMES.get(cname) or cname.removeprefix("secai-").removeprefix("docker-")
        for var, (val, explicit) in sorted(db_vars(c).items()):
            cpath = os.path.normpath(os.path.join(wd, val))
            try:
                m = find_mount(c, cpath, proc)
            except BackupError as e:
                errs.append(f"{cname}: {var}: {e}")
                continue
            if m is None:
                if explicit:
                    notes.append(f"{cname}: {var}={cpath} không nằm dưới volume/bind (lớp container): bỏ qua")
                continue
            if m.note and f"{cname}: {m.note}" not in notes:
                notes.append(f"{cname}: {m.note}")
            rel = os.path.relpath(cpath, m.dest)
            key = (*m.key, rel)
            e = entries.setdefault(key, Entry(service, m, rel))
            e.refs.append((cname, var, cpath, explicit))
    plan = sorted(entries.values(), key=lambda e: (e.service, e.rel))
    used: dict[str, set[str]] = {}
    for e in plan:
        names = used.setdefault(e.service, set())
        base = re.sub(r"\.(db|sqlite3?)$", "", os.path.basename(e.rel)) or "db"
        name = f"{base}.sqlite"
        if name in names:  # trùng tên trong service: lấy cả thư mục trong mount, vẫn trùng thì đánh số
            base = re.sub(r"[^A-Za-z0-9._-]+", "_", re.sub(r"\.(db|sqlite3?)$", "", e.rel))
            name, n = f"{base}.sqlite", 1
            while name in names:
                n += 1
                name = f"{base}-{n}.sqlite"
        names.add(name)
        e.name = name
    return plan, notes, errs


def pick_image(found: dict[str, dict], order: list[str]) -> str | None:
    for n in order:
        img = ((found.get(n) or {}).get("Config") or {}).get("Image") or ""
        if img.startswith(IMAGE_PREFIX):
            return img
    return None


def describe(e: Entry) -> str:
    refs = ", ".join(sorted({f"{r[0]}:{r[1]}{'' if r[3] else ' (mặc định)'}" for r in e.refs}))
    src = f"volume {e.mount.source}" if e.mount.kind == "volume" else f"bind {e.mount.source}"
    return f"{e.service + '/' + e.name:<40} <- {src} :: {e.rel}  [{refs}]"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dir(p: Path) -> None:
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(p, 0o700)


def _complete(d: Path) -> bool:
    """Bản đủ: MANIFEST.json của lần chạy ghi errors rỗng."""
    try:
        return json.loads((d / "MANIFEST.json").read_text()).get("errors") == []
    except (OSError, ValueError, AttributeError):
        return False


def prune(sqlite_dir: Path, staging_dir: Path, keep_days: int, now: dt.datetime, keep: set[str]) -> list[str]:
    """Xoá thư mục khớp đúng YYYYMMDD-HHMM trong sqlite/ cũ hơn keep_days (tính theo tên), trừ bản đủ mới nhất
    (lỗi kéo dài thì đó là bản cuối còn mọi DB), và staging sqlite-YYYYMMDD-HHMM sót lại quá 1 ngày.
    Bỏ qua symlink, file, tên khác mẫu, ngày không hợp lệ."""
    removed = []
    for base, rx, max_age, fmt_prefix in ((sqlite_dir, STAMP_RE, dt.timedelta(days=keep_days), ""),
                                          (staging_dir, STAGING_RE, dt.timedelta(days=1), "sqlite-")):
        if not base.is_dir():
            continue
        dirs = []
        for p in sorted(base.iterdir()):
            if not rx.match(p.name):
                continue
            try:
                when = dt.datetime.strptime(p.name[len(fmt_prefix):], "%Y%m%d-%H%M")
            except ValueError:
                continue
            if stat.S_ISDIR(os.lstat(p).st_mode):
                dirs.append((p, when))
        protect = set(keep)
        if base == sqlite_dir:
            complete = [p.name for p, _ in dirs if _complete(p)]  # tên YYYYMMDD-HHMM: thứ tự tên = thứ tự thời gian
            protect |= set(complete[-1:])
        for p, when in dirs:
            if p.name not in protect and now - when > max_age:
                shutil.rmtree(p)
                removed.append(str(p))
    return removed


def run_backup(a: argparse.Namespace) -> int:
    proc = Path(a.proc_root)
    order = [n.strip() for n in a.containers.split(",") if n.strip()]
    found, missing = inspect_containers(order)
    plan, notes, errs = build_plan(found, order, proc)
    errors = [f"không thấy container {n}" for n in missing] + errs
    for n in notes:
        warn(n)
    image = a.image or pick_image(found, order)
    log(f"backup-sqlite: {len(plan)} DB từ {len(found)}/{len(order)} container; image tạm: {image or '(không có)'}")
    for e in plan:
        log("  " + describe(e))
    if not plan:
        for msg in errors:
            warn(msg)
        warn("không có DB nào để backup")
        return 1
    if not image:
        warn(f"không tìm thấy image {IMAGE_PREFIX}* trong các container; truyền --image")
        return 2
    if a.dry_run:
        for msg in errors:
            warn(msg)
        return 1 if errors else 0

    root = Path(a.backup_dir).expanduser().absolute()
    bad = [str(x) for x in [root, *(e.mount.source for e in plan)] if "," in str(x)]
    if bad:
        warn(f"đường dẫn có dấu phẩy, không truyền được qua --mount: {bad}")
        return 2
    ensure_dir(root)
    sqlite_dir, staging_root = root / "sqlite", root / ".staging"
    ensure_dir(sqlite_dir)
    ensure_dir(staging_root)
    lock = open(root / ".sqlite-backup.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (errno.EAGAIN, errno.EACCES):
            warn(f"đang có lần backup khác chạy (khoá {root / '.sqlite-backup.lock'})")
            return 1
        raise
    now = dt.datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M")
    final = sqlite_dir / stamp
    staging = staging_root / f"sqlite-{stamp}"
    if final.exists() or staging.exists():
        warn(f"đã có {final} hoặc {staging}; chạy lại sau một phút")
        return 1
    ensure_dir(staging)
    for svc in {e.service for e in plan}:
        ensure_dir(staging / svc)

    mounts: dict[tuple, str] = {}
    argv = ["run", "--rm", "-i", "--name", CONTAINER_PREFIX + stamp, "--label", "secai.backup=sqlite",
            "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=256m",
            "--user", "0:0", "--cap-drop", "ALL", "--cap-add", "DAC_OVERRIDE", "--cap-add", "CHOWN",
            "--security-opt", "no-new-privileges", "--memory", "1g", "--entrypoint", "python3"]
    for e in plan:
        if e.mount.key not in mounts:
            target = f"/src/{len(mounts)}"
            mounts[e.mount.key] = target
            argv += ["--mount", f"type={e.mount.kind},source={e.mount.source},target={target},readonly"]
    argv += ["--mount", f"type=bind,source={staging},target=/out",
             "--mount", f"type=bind,source={Path(__file__).resolve()},target={JOB_SCRIPT},readonly",
             image, "-I", JOB_SCRIPT, "--inner"]
    jobs = [{"id": i, "src": f"{mounts[e.mount.key]}/{e.rel}", "dst": f"/out/{e.service}/{e.name}"}
            for i, e in enumerate(plan)]
    spec = {"jobs": jobs, "uid": os.getuid(), "gid": os.getgid(), "min_free": int(a.min_free_gb * 2**30)}
    t0 = time.monotonic()
    try:
        p = docker(argv, input=json.dumps(spec), timeout=a.timeout)
    except subprocess.TimeoutExpired:
        docker(["kill", CONTAINER_PREFIX + stamp], timeout=60)
        warn(f"container tạm quá {a.timeout}s, đã kill (--rm tự xoá); staging giữ ở {staging}")
        return 1
    if p.returncode != 0:
        warn(f"container tạm lỗi (mã {p.returncode}): {p.stderr.strip()[-400:]}")
        return 1
    try:
        inner = json.loads(p.stdout)
        results = {r["id"]: r for r in inner["results"]}
    except (json.JSONDecodeError, KeyError, TypeError):
        warn(f"output của container tạm không đọc được: {p.stdout[:200]!r}")
        return 1

    manifest = {"stamp": stamp, "created": now.astimezone().isoformat(timespec="seconds"),
                "image": image, "sqlite_version": inner.get("sqlite_version"),
                "seconds": round(time.monotonic() - t0, 1), "databases": [], "errors": []}
    sums = []
    for i, e in enumerate(plan):
        r = results.get(i) or {"ok": False, "error": "container không trả kết quả"}
        rel_out = f"{e.service}/{e.name}"
        out = staging / rel_out
        item = {"file": rel_out, "service": e.service, "mount": dataclasses.asdict(e.mount), "path_in_mount": e.rel,
                "used_by": [{"container": c, "var": v, "path": p_, "explicit": x} for c, v, p_, x in e.refs],
                **{k: v for k, v in r.items() if k != "id"}}
        if r.get("ok"):
            st = os.stat(out)
            if st.st_uid != os.getuid():
                r = {**r, "ok": False, "error": f"bản sao thuộc uid {st.st_uid}, không phải uid chạy backup"}
                item.update(ok=False, error=r["error"])
            else:
                os.chmod(out, 0o600)
                item["sha256"] = sha256(out)
                sums.append(f"{item['sha256']}  {rel_out}")
        if r.get("absent") and not e.explicit:
            item["skipped"] = "mặc định của config.py, không có file"
            manifest["databases"].append(item)
            continue
        if not r.get("ok"):
            if out.exists():
                out.unlink()
            manifest["errors"].append(f"{rel_out}: {r.get('error')}")
        manifest["databases"].append(item)
    manifest["errors"] = errors + manifest["errors"]
    for svc in {e.service for e in plan}:
        d = staging / svc
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    (staging / "SHA256SUMS").write_text("".join(s + "\n" for s in sums))
    (staging / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    for f in ("SHA256SUMS", "MANIFEST.json"):
        os.chmod(staging / f, 0o600)
    if not sums:  # không công bố thư mục rỗng: OPS-BAK-2 đo mtime trong sqlite/, sẽ tưởng là còn hạn
        for msg in manifest["errors"]:
            warn(msg)
        warn(f"không sao được DB nào; manifest ở {staging}")
        return 1
    os.rename(staging, final)

    total = 0
    for item in manifest["databases"]:
        if item.get("skipped"):
            continue
        if item.get("ok"):
            total += item["bytes"]
            log(f"  OK   {item['file']:<44} {item['bytes']:>12} byte  integrity={item['integrity']}  "
                f"({item.get('method')}, nguồn {item.get('source_journal')})")
        else:
            log(f"  LỖI  {item['file']:<44} {item.get('error')}")
    for msg in manifest["errors"]:
        warn(msg)
    log(f"backup-sqlite: {len(sums)} bản sao, {total} byte -> {final} ({manifest['seconds']}s)")
    # Dọn cả khi có lỗi: lỗi kéo dài (vd container trong danh sách bị gỡ) không được làm đầy dần đĩa prod.
    for r in prune(sqlite_dir, staging_root, a.keep_days, now, {stamp}):
        log(f"  dọn {r}")
    if manifest["errors"]:
        warn("có lỗi: bản đủ (không lỗi) mới nhất được giữ cả khi quá hạn")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backup-sqlite.py", description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="chỉ docker inspect và in kế hoạch")
    ap.add_argument("--backup-dir", default=os.environ.get("SECAI_BACKUP_DIR") or DEFAULT_BACKUP_DIR,
                    help="gốc backup (mặc định $SECAI_BACKUP_DIR hoặc ~/secai-backups); DB vào <gốc>/sqlite/")
    ap.add_argument("--containers", default=os.environ.get("SECAI_BACKUP_CONTAINERS") or ",".join(DEFAULT_CONTAINERS),
                    help="danh sách container, phân cách bằng dấu phẩy")
    ap.add_argument("--image", default=os.environ.get("SECAI_BACKUP_IMAGE"),
                    help=f"image cho container tạm (mặc định image {IMAGE_PREFIX}* của container đầu tiên)")
    ap.add_argument("--keep-days", type=int, default=7)
    ap.add_argument("--min-free-gb", type=float, default=5.0, help="chừa lại tối thiểu bấy nhiêu GB trống ở đích")
    ap.add_argument("--timeout", type=int, default=RUN_TIMEOUT, help="giây tối đa cho container tạm")
    ap.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    ap.add_argument("--inner", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.inner:
        return inner_main()
    os.umask(0o077)
    if a.keep_days < 1:
        warn("--keep-days phải ≥ 1")
        return 2
    if not shutil.which("docker"):
        warn("không có lệnh docker")
        return 2
    try:
        return run_backup(a)
    except BackupError as e:
        warn(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
