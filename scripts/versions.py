#!/usr/bin/env python3
"""Phiên bản container của các service secai-agent-gateway: xem, ghi sổ, trang web chỉ xem. Chỉ stdlib.

    python3 -I scripts/versions.py status [--json] [--role ROLE ...] [--strict]
    python3 -I scripts/versions.py history [SERVICE] [-n N] [--json]
    python3 -I scripts/versions.py snapshot [--dry-run]
    python3 -I scripts/versions.py record --service ID --action release|rollback --from REF --to REF
                                          [--release-dir D] [--note T]
    python3 -I scripts/versions.py backfill [--yes] [--extra-dir D ...]
    python3 -I scripts/versions.py serve [--port 18190]

Catalog: deploy/services.json. Sổ: $SECAI_RELEASES_DIR/versions.jsonl (mặc định ~/secai-releases), 0600, chỉ
nối thêm, dưới flock. Docker chỉ được đọc (`ps -a`, `inspect`, `image inspect`). Từ env container/image chỉ lấy
GIT_SHA và GIT_BRANCH; từ profile JSON chỉ lấy `image` và `container_name`; không in, không lưu env.
`serve` chỉ bind 127.0.0.1 và chỉ nhận GET. SECAI_REPO (tuỳ chọn): repo git dùng cho cờ `behind-main`.
Thoát 0 ok; 1 `status --strict` thấy cờ ở service prod, hoặc không ghi được sổ; 2 lỗi dùng/catalog/docker.
Tài liệu: docs/ops/VERSIONS.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import getpass
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "deploy" / "services.json"
ROLES = ("prod", "canary", "dependency", "dev")
ENTRY_KEYS = ("ts", "service", "containers", "action", "from", "to", "release_dir", "by", "note")
REV_LABEL = "org.opencontainers.image.revision"
SAFE_ENV = ("GIT_SHA", "GIT_BRANCH")
HEX_ID = re.compile(r"(?:sha256:)?([0-9a-f]{12,64})")
SHA_DATE_TAG = re.compile(r"(?:^|[-_.])[0-9a-f]{7,40}-\d{8}(?!\d)")
SEMVER = re.compile(r"v?\d+\.\d+\.\d+")
SEMVER_SUFFIX = re.compile(r"v?\d+\.\d+\.\d+[-+._][0-9A-Za-z.+-]+")
# Cờ làm `status --strict` thất bại; `floating-tag:minor` và `behind-main:<n>` chỉ để biết.
STRICT_FLAGS = {"bare-id", "tag-moved", "floating-tag", "no-revision", "group-drift", "missing"}
FLAG_HELP = {
    "bare-id": "container tạo từ ID image trần, không có tag",
    "tag-moved": "tag trong ref không còn trỏ image đang chạy (đã trỏ image khác hoặc bị xoá)",
    "floating-tag": "tag không bất biến (latest, 7-alpine, 1, review-mvp-…): cùng tên có thể là image khác",
    "floating-tag:minor": "semver kèm hậu tố (1.3.14-tools): gần bất biến nhưng vẫn ghi đè được",
    "no-revision": "image dựng từ repo này nhưng không có label revision hay env GIT_SHA",
    "group-drift": "các container phải cùng image nhưng đang chạy image khác nhau",
    "behind-main": "revision đang chạy kém origin/main <n> commit",
    "missing": "không thấy container hoặc container không chạy",
}
DOCKER_TIMEOUT = 60
CACHE_SECONDS = 10
BACKFILL_MAX_BYTES = 1 << 20
BACKFILL_SKIP_NAME = re.compile(r"credential|token|secret|passw|\.env|key", re.I)  # không mở các file này
BACKFILL_BEFORE = re.compile(r"before|prev|old|rollback|backup|bak", re.I)  # profile trạng thái trước
BACKFILL_MERGE = dt.timedelta(seconds=120)  # các container đổi cùng image trong khoảng này là một lần đổi
NOTES_TS = re.compile(r"\((\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})?)\)")


class Fail(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------- tiện ích


def now_iso() -> str:
    return iso_utc(dt.datetime.now(dt.timezone.utc))


def parse_ts(value) -> dt.datetime | None:
    """ISO 8601 của Docker (có thể tới nano giây) hoặc của sổ; '0001-…' là chưa từng chạy."""
    s = str(value or "")
    if not s or s.startswith("0001-"):
        return None
    s = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], s.replace("Z", "+00:00"), count=1)
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def iso_utc(t: dt.datetime | None) -> str | None:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if t else None


def local(value, fmt: str = "%Y-%m-%d %H:%M") -> str:
    t = parse_ts(value)
    return t.astimezone().strftime(fmt) if t else "-"


def short_id(image_id: str | None) -> str:
    m = HEX_ID.fullmatch(image_id or "")
    return m.group(1)[:12] if m else (image_id or "-")


def short_ref(ref: str | None) -> str:
    return short_id(ref) if ref and HEX_ID.fullmatch(ref) else (ref or "-")


def short_rev(rev: str | None) -> str:
    return rev[:8] if rev else "-"


def releases_dir() -> Path:
    return Path(os.environ.get("SECAI_RELEASES_DIR") or Path.home() / "secai-releases")


def ledger_path() -> Path:
    return releases_dir() / "versions.jsonl"


def repo_dir() -> Path:
    return Path(os.environ.get("SECAI_REPO") or ROOT)


def user() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "?"


# ---------------------------------------------------------------- catalog


def load_catalog(path: Path = CATALOG) -> list[dict]:
    try:
        services = json.loads(path.read_text(encoding="utf-8"))["services"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Fail(2, f"không đọc được catalog {path}: {e}") from None
    seen = set()
    for s in services:
        sid = s.get("id")
        if not isinstance(sid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", sid) or sid in seen:
            raise Fail(2, f"catalog: id không hợp lệ hoặc trùng: {sid!r}")
        seen.add(sid)
        if s.get("role") not in ROLES:
            raise Fail(2, f"catalog {sid}: role phải là {'|'.join(ROLES)}")
        if ("containers" in s) == ("container_pattern" in s):
            raise Fail(2, f"catalog {sid}: cần đúng một trong containers/container_pattern")
        if "containers" in s and (not s["containers"] or not all(isinstance(c, str) and c for c in s["containers"])):
            raise Fail(2, f"catalog {sid}: containers phải là danh sách tên")
        for k in ("title", "upgrade", "rollback"):
            if not isinstance(s.get(k), str) or not s[k]:
                raise Fail(2, f"catalog {sid}: thiếu {k}")
        if not isinstance(s.get("built_from_repo"), bool):
            raise Fail(2, f"catalog {sid}: built_from_repo phải là bool")
        if "container_pattern" in s:
            try:
                s["_re"] = re.compile(s["container_pattern"])
            except (re.error, TypeError) as e:
                raise Fail(2, f"catalog {sid}: container_pattern lỗi: {e}") from None
    return services


def service_names(svc: dict, all_names: list[str]) -> list[str]:
    if "containers" in svc:
        return list(svc["containers"])
    return sorted(n for n in all_names if svc["_re"].fullmatch(n))


def find_service(services: list[dict], sid: str) -> dict:
    for s in services:
        if s["id"] == sid:
            return s
    raise Fail(2, f"không có service '{sid}' trong catalog (có: {', '.join(s['id'] for s in services)})")


def owner_of(services: list[dict], name: str) -> str | None:
    """Service (không phải dev) sở hữu container tên `name`."""
    for s in services:
        if s["role"] != "dev" and (name in s.get("containers", ()) or ("_re" in s and s["_re"].fullmatch(name))):
            return s["id"]
    return None


# ---------------------------------------------------------------- docker (chỉ đọc)


def docker(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=DOCKER_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Fail(2, f"không chạy được docker {args[0]}: {type(e).__name__}") from None


def docker_json(*args: str) -> list[dict]:
    """`docker inspect` in JSON các object tìm thấy và thoát 1 nếu thiếu object: vẫn dùng phần tìm thấy."""
    r = docker(*args)
    try:
        data = json.loads(r.stdout or "[]")
    except ValueError:
        raise Fail(2, f"docker {' '.join(args[:2])}: đầu ra không phải JSON") from None
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def container_names() -> list[str]:
    r = docker("ps", "-a", "--format", "{{.Names}}")
    if r.returncode:
        first = (r.stderr.strip().splitlines() or ["?"])[0][:200]
        raise Fail(2, f"docker ps lỗi: {first}")
    return [n.strip() for n in r.stdout.splitlines() if n.strip()]


def safe_env(env) -> dict:
    """Chỉ giữ GIT_SHA/GIT_BRANCH; mọi biến khác bị bỏ ngay tại đây."""
    out = {}
    for item in env or []:
        key, sep, value = str(item).partition("=")
        if sep and key in SAFE_ENV:
            out[key] = value
    return out


def container_facts(raw: dict) -> dict:
    cfg, state = raw.get("Config") or {}, raw.get("State") or {}
    labels, env = cfg.get("Labels") or {}, safe_env(cfg.get("Env"))
    return {
        "name": str(raw.get("Name") or "").lstrip("/"),
        "ref": str(cfg.get("Image") or ""),
        "image_id": str(raw.get("Image") or ""),
        "state": state.get("Status") or "",
        "running": bool(state.get("Running")),
        "started_at": iso_utc(parse_ts(state.get("StartedAt"))),
        "health": (state.get("Health") or {}).get("Status"),
        "label_revision": labels.get(REV_LABEL) or None,
        "env_revision": env.get("GIT_SHA") or None,
        "branch": env.get("GIT_BRANCH") or None,
        "compose_project": labels.get("com.docker.compose.project"),
        "compose_files": labels.get("com.docker.compose.project.config_files"),
    }


def image_facts(raw: dict) -> dict:
    cfg = raw.get("Config") or {}
    return {
        "id": str(raw.get("Id") or ""),
        "tags": [norm_ref(t) for t in raw.get("RepoTags") or []],
        "digests": [norm_ref(d) for d in raw.get("RepoDigests") or []],
        "created": iso_utc(parse_ts(raw.get("Created"))),
        "label_revision": (cfg.get("Labels") or {}).get(REV_LABEL) or None,
        "env_revision": safe_env(cfg.get("Env")).get("GIT_SHA") or None,
    }


class Images:
    """Chỉ mục image theo id, tag, digest (đã chuẩn hoá) từ một lần `docker image inspect`."""

    def __init__(self, refs) -> None:
        refs = sorted({r for r in refs if r and not r.startswith("-")})
        self.items = [image_facts(d) for d in docker_json("image", "inspect", *refs)] if refs else []

    def by_id(self, image_id: str | None) -> dict | None:
        m = HEX_ID.fullmatch(image_id or "")
        return next((i for i in self.items if m and i["id"].removeprefix("sha256:").startswith(m.group(1))), None)

    def by_ref(self, ref: str | None) -> dict | None:
        if not ref or HEX_ID.fullmatch(ref):
            return self.by_id(ref)
        n = norm_ref(ref)
        return next((i for i in self.items if n in i["tags"] or n in i["digests"]), None)


# ---------------------------------------------------------------- phân loại ref/tag


def norm_ref(ref: str) -> str:
    name, at, digest = ref.partition("@")
    if ":" not in name.rsplit("/", 1)[-1] and not at:
        name += ":latest"
    for prefix in ("docker.io/library/", "docker.io/", "library/"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return f"{name}@{digest}" if at else name


def ref_tag(ref: str) -> str:
    last = ref.partition("@")[0].rsplit("/", 1)[-1]
    return last.rsplit(":", 1)[1] if ":" in last else "latest"


def tag_class(ref: str) -> str:
    """immutable (…-<sha>-<yyyymmdd>…, @sha256:, semver đủ 3 số) | minor (semver + hậu tố) | floating."""
    if "@sha256:" in ref:
        return "immutable"
    tag = ref_tag(ref)
    if SHA_DATE_TAG.search(tag) or SEMVER.fullmatch(tag):
        return "immutable"
    return "minor" if SEMVER_SUFFIX.fullmatch(tag) else "floating"


def pinned(ref: str) -> bool:
    """Ref đủ chắc để gán image_id hiện tại cho một thời điểm trong quá khứ (backfill)."""
    return bool(HEX_ID.fullmatch(ref)) or tag_class(ref) == "immutable"


# ---------------------------------------------------------------- phân tích version


def analyse(c: dict, images: Images, svc: dict) -> dict:
    img = images.by_id(c["image_id"]) or {}
    tags, ref, flags = img.get("tags") or [], c["ref"], []
    bare = bool(HEX_ID.fullmatch(ref))
    if bare:
        flags.append("bare-id")
    else:
        if "@" not in ref and norm_ref(ref) not in tags:
            flags.append("tag-moved")
        cls = tag_class(ref)
        if cls != "immutable":
            flags.append("floating-tag:minor" if cls == "minor" else "floating-tag")
    from_label = c["label_revision"] or img.get("label_revision")
    revision = from_label or c["env_revision"] or img.get("env_revision")
    if svc["built_from_repo"] and not revision:
        flags.append("no-revision")
    if not c["running"]:
        flags.append("missing")
    stable = next((t for t in tags if tag_class(t) == "immutable"), None)
    version = ref if not bare and "tag-moved" not in flags else stable or short_id(c["image_id"])
    out = {k: c[k] for k in ("name", "state", "running", "ref", "image_id")}
    out.update(tags=tags, version=version, revision=revision,
               revision_source=("label" if from_label else "env") if revision else None, branch=c["branch"],
               image_created=img.get("created"), started_at=c["started_at"], health=c["health"],
               compose_project=c["compose_project"], compose_files=c["compose_files"], flags=flags)
    return out


def missing_container(name: str) -> dict:
    return {"name": name, "state": "không có", "running": False, "ref": None, "image_id": None, "tags": [],
            "version": "-", "revision": None, "revision_source": None, "branch": None, "image_created": None,
            "started_at": None, "health": None, "compose_project": None, "compose_files": None,
            "flags": ["missing"]}


def behind_main(revision: str | None, cache: dict) -> int | None:
    """`git rev-list --count <rev>..origin/main`; None khi không có git hoặc commit không có trong repo."""
    if not revision or not re.fullmatch(r"[0-9a-f]{7,40}", revision):
        return None
    if revision not in cache:
        cmd = ["git", "-C", str(repo_dir()), "rev-list", "--count", f"{revision}..origin/main"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            cache[revision] = int(r.stdout.strip()) if r.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            cache[revision] = None
    return cache[revision]


def health(running: list[dict]) -> str:
    checked = [c for c in running if c["health"]]
    if not checked:
        return "-"
    ok = sum(1 for c in checked if c["health"] == "healthy")
    return f"{'healthy' if ok == len(running) else 'unhealthy'} {ok}/{len(running)}"


def summarise(svc: dict, conts: list[dict], git_cache: dict) -> dict:
    running = [c for c in conts if c["running"]]
    ids = {c["image_id"] for c in running}
    flags = {f for c in conts for f in c["flags"]}
    if svc["role"] != "dev":
        flags |= {"group-drift"} if len(ids) > 1 else set()
        flags |= {"missing"} if not running else set()
    one = len(ids) == 1
    head = running[0] if one else {}
    if svc["built_from_repo"] and one:
        n = behind_main(head["revision"], git_cache)
        flags |= {f"behind-main:{n}"} if n else set()
    return {
        "id": svc["id"], "title": svc["title"], "role": svc["role"], "group": svc.get("group"),
        "built_from_repo": svc["built_from_repo"], "upgrade": svc["upgrade"], "rollback": svc["rollback"],
        "version": head["version"] if one else f"{len(ids)} bản khác nhau" if ids else "-",
        "revision": head.get("revision"), "image_created": head.get("image_created"),
        "started_at": max((c["started_at"] for c in running if c["started_at"]), default=None),
        "running": len(running), "total": len(conts), "health": health(running),
        "flags": sorted(flags), "containers": conts,
    }


def mark_group_drift(rows: list[dict]) -> None:
    groups: dict[str, set] = {}
    for r in rows:
        if r["group"]:
            groups.setdefault(r["group"], set()).update(c["image_id"] for c in r["containers"] if c["running"])
    for r in rows:
        if r["group"] and len(groups[r["group"]]) > 1 and "group-drift" not in r["flags"]:
            r["flags"].append("group-drift")


def collect(services: list[dict], roles: set[str] | None = None) -> dict:
    """Toàn bộ trạng thái, mỗi lệnh `docker ps -a`, `docker inspect`, `docker image inspect` chạy một lần."""
    chosen = [s for s in services if roles is None or s["role"] in roles]
    all_names = container_names()
    wanted = {s["id"]: service_names(s, all_names) for s in chosen}
    present = set(all_names)
    names = sorted({n for ns in wanted.values() for n in ns if n in present})
    raw = docker_json("inspect", "--type", "container", *names) if names else []
    facts = {f["name"]: f for f in map(container_facts, raw)}
    images = Images(f["image_id"] for f in facts.values())
    rows, git_cache = [], {}
    for s in chosen:
        conts = [analyse(facts[n], images, s) if n in facts else missing_container(n) for n in wanted[s["id"]]]
        rows.append(summarise(s, conts, git_cache))
    mark_group_drift(rows)
    for r in rows:
        r["flags"] = sorted(set(r["flags"]), key=lambda f: (f.startswith("behind-main"), f))
    return {"generated_at": now_iso(), "services": rows}


def strict_flags(row: dict) -> list[str]:
    return [f for f in row["flags"] if f in STRICT_FLAGS]


# ---------------------------------------------------------------- sổ phiên bản


def ref_obj(ref: str | None, image: dict | None = None) -> dict | None:
    if not ref:
        return None
    image = image or {}
    own_id = ref if ref.startswith("sha256:") and HEX_ID.fullmatch(ref) else None
    return {"ref": ref, "image_id": image.get("id") or own_id,
            "revision": image.get("label_revision") or image.get("env_revision")}


def make_entry(service: str, containers, action: str, frm: dict | None, to: dict | None, *,
               release_dir: str | None = None, note: str | None = None, ts: str | None = None) -> dict:
    return {"ts": ts or now_iso(), "service": service, "containers": sorted(set(containers)), "action": action,
            "from": frm, "to": to, "release_dir": release_dir, "by": user(), "note": note}


def clean_entry(e: dict) -> dict:
    """Chỉ các trường đã biết, kể cả khi dòng trong sổ bị sửa tay thêm gì khác."""
    out = {k: e.get(k) for k in ENTRY_KEYS}
    for k in ("from", "to"):
        out[k] = {x: out[k].get(x) for x in ("ref", "image_id", "revision")} if isinstance(out[k], dict) else None
    conts = out["containers"] if isinstance(out["containers"], list) else []
    out["containers"] = [c for c in conts if isinstance(c, str)]
    return out


def read_ledger(path: Path | None = None) -> list[dict]:
    path = path or ledger_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as e:
        raise Fail(1, f"không đọc được sổ {path}: {e.strerror}") from None
    out = []
    for i, line in enumerate(lines, 1):
        try:
            e = json.loads(line)
        except ValueError:
            print(f"versions: bỏ qua dòng {i} hỏng trong {path.name}", file=sys.stderr)
            continue
        if isinstance(e, dict) and isinstance(e.get("service"), str):
            out.append(clean_entry(e))
    return sorted(out, key=lambda e: str(e["ts"] or ""))  # sort ổn định: cùng ts giữ thứ tự ghi


@contextmanager
def ledger_locked():
    """Mở sổ để nối thêm (0600, không theo symlink), giữ flock suốt đọc-so-ghi."""
    path = ledger_path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as e:
        raise Fail(1, f"không mở được sổ {path}: {e.strerror}") from None
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    except OSError as e:
        raise Fail(1, f"không ghi được sổ {path}: {e.strerror}") from None
    finally:
        os.close(fd)


def append(fd: int, entries: list[dict]) -> None:
    data = "".join(json.dumps(clean_entry(e), ensure_ascii=False) + "\n" for e in entries).encode()
    while data:
        data = data[os.write(fd, data):]
    os.fsync(fd)


def last_state(entries: list[dict], service: str) -> dict[str, dict]:
    """Container -> `to` của dòng mới nhất (theo ts) của service có chứa container đó."""
    state = {}
    for e in entries:
        if e["service"] == service and e["to"]:
            for c in e["containers"]:
                state[c] = e["to"]
    return state


def same(a: dict | None, b: dict | None) -> bool:
    if not a or not b:
        return False
    if a.get("image_id") and b.get("image_id"):
        return a["image_id"] == b["image_id"]
    return bool(a.get("ref")) and norm_ref(a["ref"]) == norm_ref(b.get("ref") or "")


# ---------------------------------------------------------------- lệnh: status


def parse_roles(values: list[str] | None) -> set[str]:
    roles = {r.strip() for v in values or [] for r in v.split(",") if r.strip()}
    if not roles:
        return set(ROLES) - {"dev"}
    if "all" in roles:
        return set(ROLES)
    bad = roles - set(ROLES)
    if bad:
        raise Fail(2, f"--role không hợp lệ: {', '.join(sorted(bad))} (dùng {'|'.join(ROLES)}|all)")
    return roles


def table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    return "\n".join("  ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip() for r in [headers, *rows])


def status_rows(row: dict) -> list[list[str]]:
    """Một dòng mỗi service; nếu container chạy nhiều image khác nhau thì một dòng mỗi image."""
    groups: dict[str, list[dict]] = {}
    for c in row["containers"]:
        groups.setdefault(c["image_id"] or c["name"], []).append(c)
    single, out = len(groups) == 1, []
    for conts in groups.values():
        c = conts[0]
        own = {f for x in conts for f in x["flags"]} | ({"group-drift"} & set(row["flags"]))
        flags = row["flags"] if single else sorted(own)
        out.append([row["id"] + (f" ×{len(conts)}" if len(conts) > 1 else ""), row["role"], c["version"],
                    short_rev(c["revision"]), local(c["image_created"]), local(c["started_at"]),
                    row["health"] if single else c["health"] or "-", ", ".join(flags) or "-"])
    return out


def render_status(data: dict) -> str:
    rows = [r for row in data["services"] for r in status_rows(row)]
    out = [f"Phiên bản container secai-agent-gateway — {local(data['generated_at'])} (giờ máy)",
           table(["SERVICE", "VAI TRÒ", "VERSION", "REVISION", "IMAGE TẠO", "KHỞI ĐỘNG", "HEALTH", "CỜ"], rows)]
    seen = sorted({f.split(":")[0] if f.startswith("behind-main") else f for r in data["services"] for f in r["flags"]})
    if seen:
        out += ["", "Cờ:", *(f"  {f}: {FLAG_HELP[f]}" for f in seen if f in FLAG_HELP)]
    bad = [r["id"] for r in data["services"] if r["role"] == "prod" and strict_flags(r)]
    out += ["", f"Service prod có cờ cảnh báo: {', '.join(bad)}" if bad else "Service prod: không có cờ cảnh báo.",
            "Chi tiết: status --json; lịch sử: history <service>; đổi phiên bản chỉ qua script release có gate."]
    return "\n".join(out)


def cmd_status(a, services) -> int:
    data = collect(services, parse_roles(a.role))
    print(json.dumps(data, ensure_ascii=False, indent=2) if a.json else render_status(data))
    return 1 if a.strict and any(r["role"] == "prod" and strict_flags(r) for r in data["services"]) else 0


# ---------------------------------------------------------------- lệnh: history


def history(services: list[dict], service: str | None, n: int) -> list[dict]:
    """N dòng mới nhất (mới trước), lọc theo service nếu có."""
    entries = read_ledger()
    if service:
        if service not in {s["id"] for s in services} | {e["service"] for e in entries}:
            raise Fail(2, f"không có service '{service}' trong catalog hay trong sổ")
        entries = [e for e in entries if e["service"] == service]
    return entries[::-1][:n]


def render_history(entries: list[dict]) -> str:
    if not entries:
        return f"Sổ chưa có dòng nào ({ledger_path()})."
    rows = [[local(e["ts"]), e["service"], e["action"], short_ref((e["from"] or {}).get("ref")),
             short_ref((e["to"] or {}).get("ref")), short_rev((e["to"] or {}).get("revision")), e["by"] or "-",
             (e["note"] or "")[:60]] for e in entries]
    return table(["THỜI ĐIỂM", "SERVICE", "HÀNH ĐỘNG", "TỪ", "SANG", "REVISION", "BỞI", "GHI CHÚ"], rows)


def cmd_history(a, services) -> int:
    if a.n < 1:
        raise Fail(2, "-n phải >= 1")
    entries = history(services, a.service, a.n)
    print(json.dumps(entries, ensure_ascii=False, indent=2) if a.json else render_history(entries))
    return 0


# ---------------------------------------------------------------- lệnh: snapshot


def snapshot_entries(services: list[dict], entries: list[dict]) -> list[dict]:
    """So image đang chạy với trạng thái cuối trong sổ, theo từng container; bỏ qua role dev."""
    out = []
    for row in collect([s for s in services if s["role"] != "dev"])["services"]:
        known = last_state(entries, row["id"])
        action = "observed" if any(e["service"] == row["id"] for e in entries) else "baseline"
        by_image: dict[str, list[dict]] = {}
        for c in row["containers"]:
            if c["running"]:
                by_image.setdefault(c["image_id"], []).append(c)
        for conts in by_image.values():
            c = conts[0]
            to = {"ref": c["ref"], "image_id": c["image_id"], "revision": c["revision"]}
            changed = [x["name"] for x in conts if not same(known.get(x["name"]), to)]
            if not changed:
                continue
            prev = {json.dumps(known.get(n), sort_keys=True) for n in changed}
            note = "snapshot: đổi ngoài release đã ghi" if action == "observed" else "snapshot đầu tiên"
            out.append(make_entry(row["id"], changed, action, known.get(changed[0]) if len(prev) == 1 else None, to,
                                  note=note))
    return out


def describe(e: dict) -> str:
    frm = short_ref((e["from"] or {}).get("ref")) if e["from"] else "∅"
    who = ",".join(e["containers"]) if len(e["containers"]) <= 3 else f"{len(e['containers'])} container"
    where = f" ({e['release_dir']})" if e["release_dir"] else ""
    return f"{e['action']:<9} {e['service']:<22} {frm} → {short_ref((e['to'] or {}).get('ref'))} [{who}]{where}"


def write_or_plan(planned_fn, write: bool, label: str, hint: str = "") -> int:
    if not write:
        planned = planned_fn(read_ledger())
        for e in planned:
            print(f"(kế hoạch) {e['ts']} {describe(e)}")
        print(f"{label} dry-run: {len(planned)} dòng sẽ được ghi vào {ledger_path()}{hint}", file=sys.stderr)
        return 0
    with ledger_locked() as fd:
        planned = planned_fn(read_ledger())
        append(fd, planned)
    for e in planned:
        print(f"{e['ts']} {describe(e)}")
    print(f"{label}: đã ghi {len(planned)} dòng vào {ledger_path()}", file=sys.stderr)
    return 0


def cmd_snapshot(a, services) -> int:
    return write_or_plan(lambda entries: snapshot_entries(services, entries), not a.dry_run, "snapshot")


# ---------------------------------------------------------------- lệnh: record


def cmd_record(a, services) -> int:
    svc = find_service(services, a.service)
    try:
        images = Images([a.from_ref, a.to_ref])
        names = service_names(svc, container_names() if "container_pattern" in svc else [])
    except Fail as e:  # thiếu docker vẫn ghi được ref
        print(f"versions: cảnh báo: {e} (chỉ ghi ref)", file=sys.stderr)
        images, names = None, list(svc.get("containers") or [])
    frm = ref_obj(a.from_ref, images and images.by_ref(a.from_ref))
    to = ref_obj(a.to_ref, images and images.by_ref(a.to_ref))
    e = make_entry(svc["id"], names, a.action, frm, to, release_dir=a.release_dir, note=a.note)
    with ledger_locked() as fd:
        append(fd, [e])
    print(f"versions: đã ghi {describe(e)}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- lệnh: backfill


def notes_time(d: Path) -> dt.datetime | None:
    """Thời điểm trong tiêu đề RELEASE-NOTES.md (`# Release … (ISO)`), không có thì mtime của file."""
    p = d / "RELEASE-NOTES.md"
    try:
        head = (p.read_text(encoding="utf-8", errors="replace").splitlines() or [""])[0]
        mtime = p.stat().st_mtime
    except OSError:
        return None
    m = NOTES_TS.search(head)
    return (parse_ts(m.group(1)) if m else None) or dt.datetime.fromtimestamp(mtime, dt.timezone.utc)


def state_env_tags(p: Path) -> dict:
    out = {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in ("PREV_TAG", "NEW_TAG"):
            out[key] = value.strip().strip("'\"")
    return out


def profile_images(p: Path) -> list[tuple[str, str]]:
    """[(container_name, image)] của một profile dạng compose; mọi khoá khác (environment, ...) bị bỏ qua."""
    try:
        if p.stat().st_size > BACKFILL_MAX_BYTES:
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    svcs = data.get("services") if isinstance(data, dict) else None
    if not isinstance(svcs, dict):
        return []
    return [(cfg["container_name"], cfg["image"]) for cfg in svcs.values() if isinstance(cfg, dict)
            and isinstance(cfg.get("image"), str) and isinstance(cfg.get("container_name"), str)]


def profile_files(d: Path) -> list[Path]:
    """*.json ở thư mục release và một tầng con (trừ tree/ của git archive), bỏ file có tên nhạy cảm."""
    out = []
    for sub in (d, *sorted(x for x in d.iterdir() if x.is_dir() and not x.is_symlink() and x.name != "tree")):
        try:
            out += sorted(p for p in sub.glob("*.json")
                          if p.is_file() and not p.is_symlink() and not BACKFILL_SKIP_NAME.search(p.name))
        except OSError:
            continue
    return out


def dir_observations(d: Path, services: list[dict]) -> list[dict]:
    found = []  # (t, container, ref, là-trạng-thái-trước, nguồn)
    nt = notes_time(d) if (d / "state.env").is_file() else None
    if nt:  # state.env của release-prod.sh chỉ tính khi đã có RELEASE-NOTES (switch xong)
        tags = state_env_tags(d / "state.env")
        for c in next((s for s in services if s["id"] == "gateway"), {}).get("containers", []):
            if tags.get("PREV_TAG"):
                found.append((nt - dt.timedelta(seconds=1), c, tags["PREV_TAG"], True, "state.env"))
            if tags.get("NEW_TAG"):
                found.append((nt, c, tags["NEW_TAG"], False, "state.env"))
    for p in profile_files(d):
        before = bool(BACKFILL_BEFORE.search(p.stem))
        # Profile "trước" chỉ dùng khi ref ghim được: tag trôi ở đây hay trỏ cùng image với một ID ở profile khác,
        # so không được nên sinh ra các lần đổi qua lại giả.
        pairs = [(c, img) for c, img in profile_images(p) if owner_of(services, c) and (not before or pinned(img))]
        if pairs:
            t = dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc)
            found += [(t, c, img, before, str(p.relative_to(d))) for c, img in pairs]
    # Profile "trước/rollback" được ghi cùng lúc với bản mới: xếp nó ngay trước bản mới của cùng container.
    first_new: dict[str, dt.datetime] = {}
    for t, c, _, before, _ in found:
        if not before:
            first_new[c] = min(t, first_new.get(c, t))
    out = []
    for t, c, ref, before, src in found:
        if before and c in first_new:
            t = min(t, first_new[c] - dt.timedelta(seconds=1))
        out.append({"t": t, "container": c, "ref": ref, "release_dir": str(d), "source": f"{d.name}/{src}"})
    return out


def observations(root: Path, services: list[dict]) -> list[dict]:
    try:
        dirs = sorted(d for d in root.iterdir() if d.is_dir() and not d.is_symlink())
    except OSError:
        return []
    out = []
    for d in dirs:
        try:
            out += dir_observations(d, services)
        except OSError:
            continue
    return out


def backfill_entries(services: list[dict], roots: list[Path], existing: list[dict]) -> list[dict]:
    """Chuỗi image theo thời gian của từng container → các lần đổi; gộp container cùng service/thư mục/lần đổi."""
    obs = sorted((o for r in roots for o in observations(r, services)), key=lambda o: (o["t"], o["source"]))
    try:
        images = Images(o["ref"] for o in obs if pinned(o["ref"]))
    except Fail:
        images = None
    last: dict[str, dict] = {}
    merged: dict[tuple, dict] = {}
    out = []
    for o in obs:
        to = ref_obj(o["ref"], images.by_ref(o["ref"]) if images and pinned(o["ref"]) else None)
        prev, last[o["container"]] = last.get(o["container"]), to
        if same(prev, to):
            continue
        sid = owner_of(services, o["container"])
        key = (sid, o["release_dir"], json.dumps(prev, sort_keys=True), json.dumps(to, sort_keys=True))
        e = merged.get(key)
        if e and o["t"] - parse_ts(e["ts"]) <= BACKFILL_MERGE:
            # Cùng một lần đổi của nhiều container: lấy mốc muộn nhất để replay theo ts không xếp nó trước
            # quan sát "trước" của container vào sau.
            e["containers"], e["ts"] = sorted({*e["containers"], o["container"]}), iso_utc(o["t"])
            continue
        merged[key] = make_entry(sid, [o["container"]], "backfill", prev, to, release_dir=o["release_dir"],
                                 note=f"backfill từ {o['source']}", ts=iso_utc(o["t"]))
        out.append(merged[key])

    def key(e: dict) -> tuple:
        return e["service"], Path(e["release_dir"] or "").name, norm_ref((e["to"] or {}).get("ref") or "")

    done = {key(e) for e in existing}
    return sorted((e for e in out if key(e) not in done), key=lambda e: e["ts"])


def cmd_backfill(a, services) -> int:
    roots = [releases_dir(), *map(Path, a.extra_dir or [])]
    return write_or_plan(lambda entries: backfill_entries(services, roots, entries), a.yes, "backfill",
                         " (thêm --yes để ghi)")


# ---------------------------------------------------------------- lệnh: serve (chỉ xem)

CSS = """
:root{--bg:#f7f7f5;--fg:#1d1d1b;--mut:#666;--card:#fff;--line:#ddd;
  --warn:#b54708;--warnbg:#fff4e5;--info:#175cd3;--infobg:#eef4ff;--ok:#067647}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#e8e8e6;--mut:#9a9a9a;--card:#1e1e1e;--line:#333;
  --warn:#fdb022;--warnbg:#3a2a0c;--info:#84adff;--infobg:#132040;--ok:#47cd89}}
*{box-sizing:border-box}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
h1{font-size:18px;margin:0 0 4px}h2{font-size:15px;margin:0;display:inline}.mut{color:var(--mut)}
code,pre{font:12px ui-monospace,monospace}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card);margin:12px 0}
table{border-collapse:collapse;width:100%}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}
th{font-weight:600;color:var(--mut)}tr:last-child td{border-bottom:0}
.f{display:inline-block;padding:0 6px;border-radius:9px;margin:1px 2px 1px 0;font-size:12px;
  background:var(--warnbg);color:var(--warn)}
.f.i{background:var(--infobg);color:var(--info)}.ok{color:var(--ok)}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:8px 0;padding:8px 12px}
summary{cursor:pointer}
pre{white-space:pre-wrap;word-break:break-all;background:var(--bg);padding:6px 8px;border-radius:6px;margin:4px 0;
  user-select:all}
"""
CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def esc(v) -> str:
    return html.escape("-" if v is None or v == "" else str(v))


def flag_html(f: str) -> str:
    tip = FLAG_HELP.get("behind-main" if f.startswith("behind-main") else f, "")
    return f'<span class="{"f" if f in STRICT_FLAGS else "f i"}" title="{esc(tip)}">{esc(f)}</span>'


def tr(*cells: str) -> str:
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def thead(*names: str) -> str:
    return "<thead><tr>" + "".join(f"<th>{n}</th>" for n in names) + "</tr></thead>"


def render_page(data: dict, hist: dict[str, list[dict]]) -> str:
    rows = "".join(tr(
        f"<b>{esc(r['id'])}</b><br><span class=mut>{esc(r['title'])}</span>", esc(r["role"]),
        f"<code>{esc(r['version'])}</code>", f"<code>{esc(short_rev(r['revision']))}</code>",
        esc(local(r["image_created"])), esc(local(r["started_at"])),
        f"{r['running']}/{r['total']} · {esc(r['health'])}",
        "".join(map(flag_html, r["flags"])) or "<span class=ok>ổn</span>") for r in data["services"])
    cards = "".join(service_card(r, hist.get(r["id"], [])) for r in data["services"])
    when = esc(local(data["generated_at"], "%Y-%m-%d %H:%M:%S"))
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60">
<meta name="color-scheme" content="light dark"><title>Phiên bản container</title><style>{CSS}</style></head><body>
<h1>Phiên bản container secai-agent-gateway</h1>
<div class=mut>Cập nhật {when} · tự làm mới 60 s · trang chỉ xem: đổi phiên bản chỉ qua script release có gate
(<code>scripts/release-prod.sh</code>).</div>
<div class=wrap><table>{thead("Service", "Vai trò", "Version", "Revision", "Image tạo", "Khởi động", "Chạy", "Cờ")}
<tbody>{rows}</tbody></table></div>{cards}
<p class=mut>JSON: <code>/api/status</code>, <code>/api/history?service=ID</code>. Gồm stack dev:
<code>/?role=all</code>.</p></body></html>"""


def service_card(r: dict, entries: list[dict]) -> str:
    conts = "".join(tr(
        esc(c["name"]), esc(c["state"]), f"<code>{esc(c['ref'] and short_ref(c['ref']))}</code>",
        f"<code>{esc(c['image_id'] and short_id(c['image_id']))}</code>", esc(c["revision_source"]),
        "".join(map(flag_html, c["flags"]))) for c in r["containers"])
    hist = "".join(tr(
        esc(local(e["ts"])), esc(e["action"]), f"<code>{esc(short_ref((e['from'] or {}).get('ref')))}</code>",
        f"<code>{esc(short_ref((e['to'] or {}).get('ref')))}</code>", esc(e["by"]), esc(e["note"])) for e in entries)
    hist = hist or "<tr><td colspan=6 class=mut>Sổ chưa có dòng nào cho service này.</td></tr>"
    return f"""<details><summary><h2>{esc(r['id'])}</h2> <span class=mut>{esc(r['title'])}</span></summary>
<div>Nâng cấp:</div><pre>{esc(r['upgrade'])}</pre><div>Rollback:</div><pre>{esc(r['rollback'])}</pre>
<div class=wrap><table>{thead("Container", "Trạng thái", "Ref", "Image", "Revision từ", "Cờ")}
<tbody>{conts}</tbody></table></div>
<div class=wrap><table>{thead("Thời điểm", "Hành động", "Từ", "Sang", "Bởi", "Ghi chú")}
<tbody>{hist}</tbody></table></div>
</details>"""


class Cache:
    """Kết quả docker giữ CACHE_SECONDS giây: tải trang liên tục không gọi docker liên tục."""

    def __init__(self, services: list[dict]) -> None:
        self.services, self.lock, self.at, self.data = services, threading.Lock(), 0.0, None

    def status(self) -> dict:
        with self.lock:
            if self.data is None or time.monotonic() - self.at > CACHE_SECONDS:
                self.data, self.at = collect(self.services, set(ROLES)), time.monotonic()
            return self.data


def make_handler(cache: Cache):
    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version = "secai-versions", ""

        def __getattr__(self, name: str):
            if name.startswith("do_"):  # mọi method khác GET (POST, HEAD, ...) đều 405
                return self.not_allowed
            raise AttributeError(name)

        def not_allowed(self) -> None:
            self.reply(405, "text/plain", "405: chỉ nhận GET\n".encode(), {"Allow": "GET"})

        def reply(self, code: int, ctype: str, body: bytes, extra: dict | None = None) -> None:
            self.send_response(code)
            headers = {"Content-Type": f"{ctype}; charset=utf-8", "Content-Length": str(len(body)),
                       "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                       "Referrer-Policy": "no-referrer", "Content-Security-Policy": CSP, **(extra or {})}
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_json(self, code: int, obj) -> None:
            self.reply(code, "application/json", (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode())

        def loopback(self) -> bool:
            """Chống DNS rebinding: Host phải là tên loopback, client phải từ loopback."""
            host = (self.headers.get("Host") or "").strip().lower()
            name = host if host.endswith("]") else host.rsplit(":", 1)[0]
            return self.client_address[0] in ("127.0.0.1", "::1") and name in LOOPBACK_HOSTS

        def do_GET(self) -> None:
            if not self.loopback():
                return self.reply(403, "text/plain", "403: chỉ phục vụ 127.0.0.1/localhost\n".encode())
            url = urlsplit(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/api/history":
                    n = max(1, int(q.get("n") or 50))
                    entries = history(cache.services, q.get("service"), n)
                    return self.send_json(200, {"service": q.get("service"), "entries": entries})
                if url.path not in ("/", "/api/status"):
                    return self.reply(404, "text/plain", b"404\n")
                data = cache.status()
                roles = parse_roles([q["role"]] if q.get("role") else None)
                data = {**data, "services": [r for r in data["services"] if r["role"] in roles]}
                if url.path == "/api/status":
                    return self.send_json(200, data)
                entries = read_ledger()
                hist = {r["id"]: [e for e in entries if e["service"] == r["id"]][::-1][:10] for r in data["services"]}
                return self.reply(200, "text/html", render_page(data, hist).encode())
            except Fail as e:
                code = 404 if url.path == "/api/history" and e.code == 2 else 400 if "--role" in str(e) else 503
                return self.send_json(code, {"error": str(e)})
            except ValueError:
                return self.send_json(400, {"error": "tham số không hợp lệ"})

    return Handler


def cmd_serve(a, services) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(Cache(services)))
    server.daemon_threads = True
    print(f"versions: phục vụ http://127.0.0.1:{server.server_address[1]}/ (chỉ loopback, chỉ GET; Ctrl-C để dừng)",
          file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------- main


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="bảng phiên bản đang chạy")
    p.add_argument("--json", action="store_true")
    p.add_argument("--role", action="append", help="prod|canary|dependency|dev|all (lặp hoặc phẩy); mặc định trừ dev")
    p.add_argument("--strict", action="store_true", help="thoát 1 nếu service prod có cờ cảnh báo")
    p = sub.add_parser("history", help="lịch sử phiên bản trong sổ, mới trước")
    p.add_argument("service", nargs="?")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("snapshot", help="ghi baseline/observed khi image đổi ngoài release đã ghi")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("record", help="ghi một lần release/rollback (release-prod.sh gọi)")
    p.add_argument("--service", required=True)
    p.add_argument("--action", required=True, choices=("release", "rollback"))
    p.add_argument("--from", dest="from_ref", required=True)
    p.add_argument("--to", dest="to_ref", required=True)
    p.add_argument("--release-dir")
    p.add_argument("--note")
    p = sub.add_parser("backfill", help="dựng lịch sử từ state.env/profile JSON trong thư mục release")
    p.add_argument("--yes", action="store_true", help="ghi vào sổ (mặc định chỉ in kế hoạch)")
    p.add_argument("--extra-dir", action="append", help="thêm thư mục gốc chứa các thư mục release")
    p = sub.add_parser("serve", help="trang web chỉ xem, chỉ trên 127.0.0.1")
    p.add_argument("--port", type=int, default=18190)
    return ap


COMMANDS = {"status": cmd_status, "history": cmd_history, "snapshot": cmd_snapshot, "record": cmd_record,
            "backfill": cmd_backfill, "serve": cmd_serve}


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        return COMMANDS[a.cmd](a, load_catalog())
    except Fail as e:
        print(f"versions: {e}", file=sys.stderr)
        return e.code
    except OSError as e:
        print(f"versions: {type(e).__name__}: {e.strerror}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
