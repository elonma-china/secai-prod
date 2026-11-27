#!/usr/bin/env python3
"""ops-check: chấm phần vận hành của secai-agent-gateway theo docs/ops/OPS-CRITERIA.md. CHỈ ĐỌC.

    python3 -I scripts/ops-check.py                 # in từng tiêu chí PASS/FAIL/WARN/SKIP + bằng chứng
    python3 -I scripts/ops-check.py --json          # cùng kết quả dạng JSON
    python3 -I scripts/ops-check.py --group VER,SEC # chỉ vài nhóm
    python3 -I scripts/ops-check.py --list          # in danh mục tiêu chí, không đo gì

Mã thoát: 0 khi không có FAIL ở tiêu chí bắt buộc; 1 khi có; 2 khi sai tham số.

Checker không ghi, không restart, không đổi gì. Lệnh ngoài duy nhất được gọi (kiểm bằng
`assert_read_only` trước MỌI lần gọi, sai là dừng hẳn):
  docker ps | inspect | images | image inspect | system df
  gh api <path>        (chỉ GET: không -X/--method/-f/-F/--input)
  df, systemctl [--user] show | list-*, stat
  git chỉ đọc (rev-parse, merge-base, cat-file, ls-files, log, diff, status --porcelain,
  worktree list, branch -r/--merged/--show-current, remote get-url, rev-list), luôn kèm
  --no-optional-locks; không fetch nên origin/main có thể cũ.
Ngoài ra chỉ đọc file (Dockerfile, compose, state.env, pip-freeze.diff...) và os.stat.

Secret: chỉ in tên biến, đường dẫn, quyền file. Giá trị đọc được trong bộ nhớ (env container,
compose, script cron) được ghi vào một tập và xoá khỏi mọi dòng output trước khi in.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import fnmatch
import glob
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
MUST, SHOULD = "bắt buộc", "nên có"

GROUPS = {
    "BLD": "Build tái lập được",
    "REL": "Release/rollback",
    "VER": "Quản lý phiên bản container",
    "CI": "CI và chất lượng",
    "BAK": "Backup/khôi phục dữ liệu",
    "MON": "Giám sát/cảnh báo",
    "SEC": "Bảo mật và secret",
    "RES": "Tài nguyên/đĩa/dọn dẹp",
    "GIT": "Git hygiene",
    "DOC": "Tài liệu/runbook",
}

GATEWAY_IMAGE = "secai-agent-gateway"
GATEWAY_TRIO = ("secai-docker-gateway", "secai-translation-gateway", "secai-translation-worker")
APP_CONTAINERS = GATEWAY_TRIO + (
    "secai-translation-frontend", "secai-report-backend", "secai-review-frontend",
    "secai-review-canary", "secai-translation-converter",
)
DATASTORES = ("secai-docker-qdrant", "secai-docker-neo4j", "secai-docker-redis", "secai-docker-es")
POOL_RE = re.compile(r"^secai-docker-pentestcode(-\d+)?$")
POOL_EXPECTED = 25
DEV_PROJECT_PREFIX = "secai-gw-dev-"

# Script đổi trạng thái prod/máy: phải có `set -e` ngoài -u + pipefail (OPS-CI-6).
MUTATING_SCRIPTS = (
    "scripts/release-prod.sh", "scripts/wt-prune.sh", "scripts/wt-rm.sh", "scripts/wt-new.sh",
    "scripts/dev.sh", "scripts/install-wt-prune-timer.sh", "stack/bootstrap.sh", "stack/pack.sh",
    "stack/restore-data.sh", "deploy/up.sh", "deploy/apply_firewall.sh",
)
SHELL_SCRIPT_GLOBS = ("scripts/*.sh", "stack/*.sh", "stack/lib/*.sh", "deploy/*.sh")

# Test hành vi bắt buộc của release-prod.sh (OPS-REL-3); tên hàm trong tests/test_release_prod.py.
REQUIRED_RELEASE_TESTS = (
    "test_plan_changes_nothing",
    "test_mutating_modes_require_yes",
    "test_stops_when_gateway_containers_disagree",
    "test_stops_when_prod_not_ancestor_of_main",
    "test_gc_keeps_previous_release_and_in_use_images",
)

# File tracked chứa secret GIẢ có chủ đích (dữ liệu test/benchmark của bộ sanitize, demo redaction).
# Chưa xác minh từng giá trị; lịch sử Git cần quét riêng bằng gitleaks (kiểm tay, OPS-SEC-6).
FAKE_SECRET_FIXTURES = (
    "evals/sanitize/", "tests/", "demo/kpi-studio/kpi-studio-standalone.html",
    "demo/showcase/tabs/sanitize-demo.js", "evidence/dong-534.md", "evidence/dong-554.md",
    "kpi_bench/sanitizer/detector.py", "pentestcode/packages/llm/test/", "scripts/nghiem-thu-rows-ai.json",
)
TRACKED_SECRET_PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "github_token": re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|github_pat_[A-Za-z0-9_]{40,}"),
    "openai_like": re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}\b"),
    "aws_access_key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "hf_token": re.compile(rb"\bhf_[A-Za-z0-9]{30,}\b"),
    "slack_token": re.compile(rb"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    "google_api_key": re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}\b"),
}

SECRET_NAME_RE = re.compile(r"(API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|_PASS$|_AUTH$|^AUTH|CREDENTIAL|PRIVATE_KEY|_KEY$)", re.I)
# NAME=value / NAME: value / "NAME=value" (dump docker inspect). Chỉ tên được in ra.
SECRET_ASSIGN_RE = re.compile(
    r"""["']?\b([A-Z][A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|_PASS|_AUTH|_KEY))\b["']?\s*[:=]\s*["']?([^"'\s,}\]]+)""")
# Tên khớp SECRET_NAME_RE nhưng không phải secret: đường dẫn/tên file, fingerprint GPG công khai của image python.
NOT_SECRET_NAME_RE = re.compile(r"(_FILE|_PATH|_DIR|_FILES)$", re.I)
PUBLIC_ENV_NAMES = {"GPG_KEY"}


def is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME_RE.search(name)) and not NOT_SECRET_NAME_RE.search(name) and name not in PUBLIC_ENV_NAMES


URL_CRED_RE = re.compile(r"://[^/\s:@]+:([^@\s/]+)@")
ARGV_SECRET_RE = re.compile(
    r"(?i)(?:--?(api[-_]?key|token|password|passwd|secret)(?:=|\s+)(?!\$)\S{6,})"
    r"|(?:\b(api[-_]?key|access[-_]?token|token|password|secret)=(?!\$)[^\s&\"']{8,})")


class ReadOnlyViolation(RuntimeError):
    """Checker định gọi một lệnh không nằm trong danh sách chỉ đọc. Không bao giờ được bắt rồi bỏ qua."""


class Unavailable(Exception):
    """Không đo được (thiếu docker/gh/quyền...). Tiêu chí thành SKIP kèm lý do."""


# ------------------------------------------------------------------ chặn lệnh ghi
_GIT_READONLY = {"rev-parse", "merge-base", "cat-file", "ls-files", "log", "diff", "status", "worktree",
                 "branch", "remote", "rev-list", "show-ref", "for-each-ref", "config"}
_GIT_BRANCH_OK = {"-r", "--remotes", "-a", "--all", "--show-current", "--list", "--no-color", "--merged", "--contains"}
_GH_FORBIDDEN = ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input")


def assert_read_only(argv: list[str]) -> None:
    if not argv:
        raise ReadOnlyViolation("lệnh rỗng")
    tool, a = os.path.basename(argv[0]), list(argv[1:])
    ok = False
    if tool == "docker":
        ok = (a[:1] in (["ps"], ["inspect"], ["images"], ["version"], ["info"])
              or a[:2] in (["image", "inspect"], ["image", "ls"], ["system", "df"],
                           ["container", "inspect"], ["container", "ls"]))
    elif tool == "gh":
        ok = (a[:1] == ["api"] and len(a) >= 2 and "graphql" not in a[1]
              and not any(x in _GH_FORBIDDEN or x.startswith(("--method=", "-X", "--field=", "--raw-field=", "--input="))
                          for x in a[1:]))
    elif tool in ("df", "stat"):
        ok = True
    elif tool == "systemctl":
        verbs = [x for x in a if not x.startswith("-")]
        ok = bool(verbs) and (verbs[0] == "show" or verbs[0].startswith("list-"))
    elif tool == "git":
        i = 0
        while i < len(a) and a[i].startswith("-"):
            if a[i] == "-C":
                i += 2
            elif a[i] == "--no-optional-locks":
                i += 1
            else:
                break
        sub, rest = (a[i] if i < len(a) else ""), a[i + 1:]
        ok = sub in _GIT_READONLY and "--no-optional-locks" in a[:i]
        if sub == "worktree":
            ok = ok and rest[:1] == ["list"]
        elif sub == "remote":
            ok = ok and rest[:1] == ["get-url"]
        elif sub == "config":
            ok = ok and rest[:1] == ["--get"]
        elif sub == "branch":
            flags = [x for x in rest if x.startswith("-")]
            ok = ok and bool(flags) and all(f in _GIT_BRANCH_OK or f.startswith("--format=") for f in flags)
    if not ok:
        raise ReadOnlyViolation(f"lệnh không thuộc danh sách chỉ đọc: {argv[:4]}")


@dataclasses.dataclass
class Proc:
    rc: int
    out: str
    err: str


class Runner:
    """Gọi lệnh thật; luôn qua assert_read_only."""

    def run(self, argv: list[str], timeout: int = 60) -> Proc:
        assert_read_only(argv)
        env = {**os.environ, "LC_ALL": "C", "GH_PAGER": "", "PAGER": "cat", "SYSTEMD_PAGER": "",
               "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1", "GIT_TERMINAL_PROMPT": "0"}
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env,
                               stdin=subprocess.DEVNULL, errors="replace")
        except FileNotFoundError:
            return Proc(127, "", f"{argv[0]}: không có trên PATH")
        except subprocess.TimeoutExpired:
            return Proc(124, "", f"{argv[0]}: quá {timeout}s")
        return Proc(p.returncode, p.stdout, p.stderr)


# ------------------------------------------------------------------ tiện ích
def parse_ts(s: str) -> dt.datetime | None:
    """Thời điểm của docker (`2026-11-27T07:32:31.29Z`, `...+07:00`, `2026-11-27 13:40:01 +0700 +07`)."""
    if not s or s.startswith("0001-"):
        return None
    s = s.strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) ([+-]\d{4})", s)
    if m:
        return dt.datetime.strptime(" ".join(m.groups()), "%Y-%m-%d %H:%M:%S %z")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s).replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def size_gb(s: str) -> float:
    m = re.match(r"^\s*([\d.]+)\s*([kKMGT]?B)", s or "")
    if not m:
        return 0.0
    mult = {"B": 1e-9, "kB": 1e-6, "KB": 1e-6, "MB": 1e-3, "GB": 1.0, "TB": 1e3}[m.group(2)]
    return float(m.group(1)) * mult


def world_readable(p: Path) -> bool:
    """File đọc được bởi 'others' theo đường dẫn thực: file có o+r và mọi thư mục cha có o+x."""
    try:
        p = p.resolve()
        if not p.stat().st_mode & stat.S_IROTH:
            return False
        for d in p.parents:
            if not d.stat().st_mode & stat.S_IXOTH:
                return False
    except OSError:
        return False
    return True


def short(items: list[str], n: int = 6) -> str:
    items = list(items)
    return ", ".join(items[:n]) + (f" … (+{len(items) - n})" if len(items) > n else "")


def image_repo_tag(ref: str) -> tuple[str, str, str]:
    """(repo, tag, digest) của một image ref."""
    digest = ""
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    last = ref.rsplit("/", 1)[-1]
    if ":" in last:
        repo, tag = ref.rsplit(":", 1)
    else:
        repo, tag = ref, ""
    return repo, tag, digest


def is_bare_id(ref: str) -> bool:
    return ref.startswith("sha256:") or bool(re.fullmatch(r"[0-9a-f]{12,64}", ref))


DATE_TOKEN = re.compile(r"^20\d{6}[a-z]?$")
SHA_TOKEN = re.compile(r"^[0-9a-f]{7,40}$")
SEMVER_TOKEN = re.compile(r"^v?\d+\.\d+\.\d+")


def classify_first_party_tag(tag: str) -> tuple[str, str]:
    """PASS: có sha commit + ngày; WARN: chỉ ngày hoặc semver đầy đủ; FAIL: tag trôi/mô tả."""
    if not tag or tag == "latest":
        return FAIL, "latest/không tag"
    tokens = re.split(r"[-_]", tag)
    has_date = any(DATE_TOKEN.match(t) for t in tokens)
    has_sha = any(SHA_TOKEN.match(t) and not DATE_TOKEN.match(t) and not t.isdigit() for t in tokens)
    if has_sha and has_date:
        return PASS, "sha+ngày"
    if SEMVER_TOKEN.match(tag) or has_date or has_sha:
        return WARN, "không đủ sha+ngày"
    return FAIL, "tag trôi"


def classify_third_party_tag(tag: str, digest: str) -> tuple[str, str]:
    if digest:
        return PASS, "digest"
    m = re.match(r"^v?(\d+(?:\.\d+)*)", tag or "")
    if not m or tag == "latest":
        return FAIL, "latest/không version"
    parts = m.group(1).split(".")
    if len(parts) >= 3:
        return PASS, "x.y.z"
    if len(parts) == 2:
        return WARN, "chỉ x.y"
    return FAIL, "chỉ major"


def parse_set_flags(text: str) -> set[str]:
    """Gộp cờ của mọi dòng `set -...` trong script: {'e','u','pipefail',...}."""
    flags: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\s*set\s+(.*)$", line)
        if not m:
            continue
        toks = m.group(1).split("#")[0].split()
        i = 0
        while i < len(toks):
            t = toks[i]
            if t == "-o" and i + 1 < len(toks):
                flags.add(toks[i + 1]); i += 2; continue
            if re.fullmatch(r"-[a-zA-Z]+", t):
                flags.update(c for c in t[1:] if c != "o")
                if "o" in t[1:] and i + 1 < len(toks):
                    flags.add(toks[i + 1]); i += 2; continue
            i += 1
    return flags


# ------------------------------------------------------------------ ngữ cảnh đo
@dataclasses.dataclass
class Config:
    repo: Path
    docker_dir: Path
    releases_dir: Path
    data_root: Path
    backup_dir: Path | None
    gh_repo: str | None
    use_gh: bool
    cron_globs: list[str]


class Ctx:
    def __init__(self, cfg: Config, runner: Runner, now: dt.datetime) -> None:
        self.cfg, self.r, self.now = cfg, runner, now
        self.secrets: set[str] = set()
        self._c: dict = {}

    # ---- secret
    def remember_secret(self, value: str) -> None:
        v = (value or "").strip().strip("'\"")
        if len(v) >= 6:
            self.secrets.add(v)

    def scrub(self, text: str) -> str:
        for s in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(s, "***")
        return text

    def _memo(self, key, fn):
        if key not in self._c:
            try:
                self._c[key] = ("ok", fn())
            except Unavailable as e:
                self._c[key] = ("err", e)
        kind, val = self._c[key]
        if kind == "err":
            raise val
        return val

    # ---- docker
    def docker(self, *args: str, allow_fail: bool = False) -> Proc:
        p = self.r.run(["docker", *args])
        if p.rc != 0 and not allow_fail:
            raise Unavailable(f"docker {args[0]}: {p.err.strip()[:160] or p.rc}")
        return p

    def ps_all(self) -> list[dict]:
        def f():
            p = self.docker("ps", "-a", "--no-trunc", "--format", "{{json .}}")
            return [json.loads(x) for x in p.out.splitlines() if x.strip()]
        return self._memo("ps", f)

    def inspect_all(self) -> list[dict]:
        """docker inspect mọi container (để biết image ID nào đang được dùng)."""
        def f():
            ids = [c.get("ID") for c in self.ps_all() if c.get("ID")]
            out: list[dict] = []
            for i in range(0, len(ids), 100):
                p = self.docker("inspect", *ids[i:i + 100], allow_fail=True)
                out += json.loads(p.out or "[]")
            for c in out:
                self.env(c)  # nạp secret để scrub
            return out
        return self._memo("inspect_all", f)

    def containers(self) -> dict[str, dict]:
        """Container trong phạm vi: tên -> inspect."""
        def f():
            return {c["Name"].lstrip("/"): c for c in self.inspect_all() if in_scope(c["Name"].lstrip("/"))}
        return self._memo("containers", f)

    def images(self, ids: list[str]) -> dict[str, dict]:
        want = sorted({i for i in ids if i} - set(self._c.setdefault("img", {})))
        if want:
            p = self.docker("image", "inspect", *want, allow_fail=True)
            for im in json.loads(p.out or "[]"):
                self._c["img"][im["Id"]] = im
                for e in (im.get("Config") or {}).get("Env") or []:
                    k, _, v = e.partition("=")
                    if is_secret_name(k):
                        self.remember_secret(v)
        return {i: self._c["img"][i] for i in ids if i in self._c["img"]}

    def image(self, ref: str) -> dict | None:
        p = self.docker("image", "inspect", ref, allow_fail=True)
        if p.rc != 0:
            return None
        lst = json.loads(p.out or "[]")
        return lst[0] if lst else None

    def gateway_images(self) -> list[dict]:
        def f():
            p = self.docker("images", "--no-trunc", GATEWAY_IMAGE, "--format", "{{json .}}")
            return [json.loads(x) for x in p.out.splitlines() if x.strip()]
        return self._memo("gw_images", f)

    def system_df(self) -> list[dict]:
        def f():
            p = self.docker("system", "df", "--format", "{{json .}}")
            return [json.loads(x) for x in p.out.splitlines() if x.strip()]
        return self._memo("sysdf", f)

    def env(self, c: dict) -> dict[str, str]:
        env: dict[str, str] = {}
        for e in (c.get("Config") or {}).get("Env") or []:
            k, _, v = e.partition("=")
            env[k] = v
            if is_secret_name(k):
                self.remember_secret(v)
            for m in URL_CRED_RE.finditer(v):
                self.remember_secret(m.group(1))
        return env

    # ---- git
    def git(self, *args: str, repo: Path | None = None, check: bool = True) -> Proc:
        p = self.r.run(["git", "--no-optional-locks", "-C", str(repo or self.cfg.repo), *args])
        if check and p.rc != 0:
            raise Unavailable(f"git {args[0]}: {p.err.strip()[:160]}")
        return p

    def worktrees(self) -> list[dict]:
        def f():
            out, cur = [], {}
            for line in self.git("worktree", "list", "--porcelain").out.splitlines() + [""]:
                if not line.strip():
                    if cur:
                        out.append(cur)
                    cur = {}
                    continue
                k, _, v = line.partition(" ")
                cur[k] = v or True
            return out
        return self._memo("worktrees", f)

    def main_checkout(self) -> Path:
        return Path(self.worktrees()[0]["worktree"])

    # ---- gh
    def gh(self, path: str) -> tuple[int, object]:
        if not self.cfg.use_gh:
            raise Unavailable("đã tắt gh (--no-gh)")
        if not self.cfg.gh_repo:
            raise Unavailable("không xác định được repo GitHub (origin không phải github.com)")
        key = "gh:" + path

        def f():
            p = self.r.run(["gh", "api", path.format(repo=self.cfg.gh_repo)])
            if p.rc == 0:
                return 200, json.loads(p.out or "null")
            if "HTTP 404" in p.err:
                return 404, None
            raise Unavailable(f"gh api lỗi: {p.err.strip()[:160] or p.rc}")
        return self._memo(key, f)

    # ---- systemd
    def units(self) -> list[dict]:
        """Unit secai-* (user + system) kèm thuộc tính của `systemctl show`."""
        def f():
            out = []
            for scope in (["--user"], []):
                p = self.r.run(["systemctl", *scope, "list-units", "--all", "--plain", "--no-legend",
                                "--no-pager", "secai-*"])
                if p.rc != 0:
                    continue
                names = [ln.split()[0] for ln in p.out.splitlines() if ln.strip()]
                if not names:
                    continue
                q = self.r.run(["systemctl", *scope, "show", *names, "--no-pager", "-p", "Id", "-p", "ActiveState",
                                "-p", "SubState", "-p", "NRestarts", "-p", "Result", "-p", "ExecStart"])
                for block in q.out.split("\n\n"):
                    props = dict(ln.split("=", 1) for ln in block.splitlines() if "=" in ln)
                    if props.get("Id"):
                        props["scope"] = "user" if scope else "system"
                        out.append(props)
            if not out:
                raise Unavailable("systemctl không trả unit secai-* nào")
            return out
        return self._memo("units", f)

    def timers(self) -> list[str]:
        def f():
            names = []
            for scope in (["--user"], []):
                p = self.r.run(["systemctl", *scope, "list-timers", "--all", "--plain", "--no-legend", "--no-pager"])
                names += re.findall(r"(\S+\.timer)", p.out)
            return names
        return self._memo("timers", f)

    # ---- df
    def df(self, path: Path) -> tuple[str, int]:
        p = self.r.run(["df", "-P", str(path)])
        lines = [ln for ln in p.out.splitlines()[1:] if ln.strip()]
        if p.rc != 0 or not lines:
            raise Unavailable(f"df {path}: {p.err.strip()[:120]}")
        f = lines[-1].split()
        return f[5], int(f[4].rstrip("%"))

    # ---- release ghi nhận gần nhất
    def latest_release(self) -> tuple[Path, dict[str, str]]:
        def f():
            ptr = self.cfg.releases_dir / "LATEST-PROD"
            if not ptr.is_file():
                raise Unavailable(f"không có {ptr}")
            rel = Path(ptr.read_text().strip())
            st = rel / "state.env"
            if not st.is_file():
                raise Unavailable(f"thiếu {st}")
            state = dict(ln.split("=", 1) for ln in st.read_text().splitlines() if "=" in ln)
            return rel, state
        return self._memo("latest", f)

    def read_repo(self, rel: str) -> str:
        p = self.cfg.repo / rel
        return p.read_text(errors="replace") if p.is_file() else ""


def in_scope(name: str) -> bool:
    return name in APP_CONTAINERS or name in DATASTORES or bool(POOL_RE.match(name))


def first_party(ref: str) -> bool:
    return image_repo_tag(ref)[0].rsplit("/", 1)[-1].startswith("secai-")


def group_pool(names: list[str]) -> list[str]:
    pool = [n for n in names if POOL_RE.match(n)]
    rest = [n for n in names if not POOL_RE.match(n)]
    return rest + ([f"pool pentestcode ({len(pool)})"] if pool else [])


def is_gateway_app(c: dict) -> bool:
    cmd = " ".join((c.get("Config") or {}).get("Cmd") or []) + " " + " ".join((c.get("Config") or {}).get("Entrypoint") or [])
    return "uvicorn" in cmd or "secai_gateway" in cmd


def mounted(c: dict, path: str) -> bool:
    return any(path == m["Destination"] or path.startswith(m["Destination"].rstrip("/") + "/")
               for m in c.get("Mounts") or [])


# ------------------------------------------------------------------ danh mục tiêu chí
@dataclasses.dataclass
class R:
    status: str
    evidence: str
    fix: str = ""


@dataclasses.dataclass
class Check:
    id: str
    level: str
    title: str
    fn: object = None
    manual: str = ""

    @property
    def group(self) -> str:
        return self.id.split("-")[1]


CHECKS: list[Check] = []


def check(cid: str, level: str, title: str):
    def deco(fn):
        CHECKS.append(Check(cid, level, title, fn))
        return fn
    return deco


def manual(cid: str, level: str, title: str, how: str) -> None:
    CHECKS.append(Check(cid, level, title, None, how))


def worst(statuses: list[str]) -> str:
    for s in (FAIL, WARN, PASS):
        if s in statuses:
            return s
    return PASS


# ================================================================== BLD
@check("OPS-BLD-1", MUST, "Image cài dependency từ uv.lock, không resolve lại từ pyproject.toml")
def bld_lock(ctx: Ctx) -> R:
    df = ctx.read_repo("Dockerfile")
    if not df:
        raise Unavailable("không có Dockerfile trong repo")
    copies_lock = re.search(r"^\s*COPY\b[^\n]*\buv\.lock\b", df, re.M)
    locked = re.search(r"uv\s+(sync|pip\s+sync)\b[^\n]*--(frozen|locked)|uv\s+sync\b[^\n]*--frozen|--require-hashes", df)
    if copies_lock and locked:
        return R(PASS, "Dockerfile COPY uv.lock và cài theo lock")
    return R(FAIL, "Dockerfile chỉ COPY pyproject.toml rồi pip install theo [project].dependencies; uv.lock bị bỏ qua",
             "deps stage: COPY pyproject.toml uv.lock, cài uv ghim version rồi `uv sync --frozen --no-dev --extra redis --no-install-project`")


@check("OPS-BLD-2", MUST, "Lần release gần nhất không trôi version package bên thứ ba khi uv.lock không đổi")
def bld_drift(ctx: Ctx) -> R:
    rel, st = ctx.latest_release()
    diff = rel / "pip-freeze.diff"
    if not diff.is_file():
        return R(PASS, f"{rel.name}: build OVERLAY (deps giữ nguyên image trước), không có pip-freeze.diff")
    changed = sorted({m.group(1).lower() for m in re.finditer(r"^[<>]\s*([A-Za-z0-9_.\-]+)\s*(?:==|@)", diff.read_text(), re.M)}
                     - {GATEWAY_IMAGE})
    if not changed:
        return R(PASS, f"{rel.name}: pip freeze chỉ khác gói secai-agent-gateway")
    prev = re.search(r":main-([0-9a-f]{8})-", st.get("PREV_TAG", ""))
    new = re.search(r":main-([0-9a-f]{8})-", st.get("NEW_TAG", ""))
    lock_changed = None
    if prev and new:
        p = ctx.git("diff", "--quiet", prev.group(1), new.group(1), "--", "uv.lock", check=False)
        lock_changed = {0: False, 1: True}.get(p.rc)
    ev = f"{rel.name}: {len(changed)} package đổi version ({short(changed)})"
    if lock_changed is False:
        return R(FAIL, ev + "; uv.lock KHÔNG đổi giữa hai commit → trôi do build không dùng lock",
                 "sửa OPS-BLD-1; tới lúc đó đọc pip-freeze.diff trước mỗi `release-prod.sh run`")
    return R(WARN, ev + ("; uv.lock có đổi, đối chiếu tay" if lock_changed else "; không so được uv.lock"))


@check("OPS-BLD-3", SHOULD, "Base image trong Dockerfile ghim digest")
def bld_digest(ctx: Ctx) -> R:
    df = ctx.read_repo("Dockerfile")
    bases = re.findall(r"^\s*FROM\s+(\S+)", df, re.M | re.I)
    loose = [b for b in bases if "@sha256:" not in b and b.lower() not in ("scratch",)]
    if not bases:
        raise Unavailable("không có Dockerfile")
    if loose:
        return R(FAIL, f"FROM chưa ghim digest: {short(sorted(set(loose)))}",
                 "FROM python:3.12-slim@sha256:<digest>; cập nhật digest có chủ đích (PR riêng)")
    return R(PASS, "mọi FROM có @sha256")


@check("OPS-BLD-4", MUST, "Image prod build từ git archive và không mount src/ sống vào container")
def bld_archive(ctx: Ctx) -> R:
    rp = ctx.read_repo("scripts/release-prod.sh")
    static_ok = bool(re.search(r"git -C \"\$REPO\" archive", rp)) and "diff -rq" in rp
    bad = []
    for name, c in ctx.containers().items():
        for m in c.get("Mounts") or []:
            if m["Destination"].rstrip("/") == "/app/src" or (m.get("Type") == "bind" and re.search(r"/src/?$", m.get("Source", ""))
                                                               and m["Destination"].startswith("/app")):
                bad.append(f"{name}:{m['Destination']}")
    if bad:
        return R(FAIL, f"mount src sống: {short(bad)}", "bỏ mount; đổi code = release image mới")
    if not static_ok:
        return R(FAIL, "release-prod.sh không còn build từ git archive / so /app/src với cây git")
    return R(PASS, "release-prod.sh build từ `git archive` và so /app/src; không container nào mount /app/src")


@check("OPS-BLD-5", MUST, ".dockerignore loại .env*, .venv/, data/, *.db khỏi build context")
def bld_dockerignore(ctx: Ctx) -> R:
    txt = ctx.read_repo(".dockerignore")
    lines = {ln.strip() for ln in txt.splitlines()}
    need = [".env", ".env.*", ".venv/", "data/", "*.db"]
    miss = [n for n in need if n not in lines]
    if not txt:
        return R(FAIL, "không có .dockerignore", "thêm .dockerignore loại secret/venv/data")
    return R(FAIL, f".dockerignore thiếu: {', '.join(miss)}") if miss else R(PASS, "có đủ " + ", ".join(need))


manual("OPS-BLD-6", SHOULD, "Build lại image prod từ commit trong tag ra cùng pip freeze",
       "git archive <sha8> | docker build (stack dev, tag dev-*) rồi so `pip freeze` với image prod: 0 dòng khác")


# ================================================================== REL
@check("OPS-REL-1", MUST, "Release gần nhất có đủ hồ sơ (state.env, RELEASE-NOTES, pytest.log, health trước/sau, compose bak)")
def rel_record(ctx: Ctx) -> R:
    rel, st = ctx.latest_release()
    need = ["state.env", "RELEASE-NOTES.md", "pytest.log", "health-8131-before.json", "health-8131-after.json"]
    miss = [n for n in need if not (rel / n).is_file()]
    if st.get("COMPOSE_BAK") and not Path(st["COMPOSE_BAK"]).is_file():
        miss.append("COMPOSE_BAK")
    if miss:
        return R(FAIL, f"{rel.name} thiếu: {', '.join(miss)}")
    return R(PASS, f"{rel.name}: đủ {len(need)} file + compose bak")


@check("OPS-REL-2", MUST, "Rollback khả dụng: image PREV_TAG, compose bak và profile translation trước còn trên máy")
def rel_rollback_ready(ctx: Ctx) -> R:
    rel, st = ctx.latest_release()
    miss = []
    prev = st.get("PREV_TAG", "")
    if not prev or ctx.image(prev) is None:
        miss.append(f"image {prev or '(PREV_TAG rỗng)'}")
    for k in ("COMPOSE_BAK", "TRANS_PREV_PROFILE"):
        if not st.get(k) or not Path(st[k]).is_file():
            miss.append(k)
    if miss:
        return R(FAIL, f"{rel.name}: không rollback được, thiếu {', '.join(miss)}",
                 "giữ image của lần release liền trước; không xoá release dir cũ khi còn là PREV")
    return R(PASS, f"PREV_TAG {prev} còn; compose bak + profile trước còn")


@check("OPS-REL-3", MUST, "release-prod.sh có test hành vi offline cho plan/--yes/invariant/tổ tiên/gc")
def rel_tests(ctx: Ctx) -> R:
    txt = ctx.read_repo("tests/test_release_prod.py")
    if not txt:
        return R(FAIL, "không có tests/test_release_prod.py", "viết test với docker/git/curl giả")
    miss = [t for t in REQUIRED_RELEASE_TESTS if f"def {t}" not in txt]
    if miss:
        return R(FAIL, f"thiếu test: {', '.join(miss)}")
    return R(PASS, f"có {len(REQUIRED_RELEASE_TESTS)} test bắt buộc; chạy: scripts/dev.sh test tests/test_release_prod.py")


@check("OPS-REL-4", MUST, "Rollback tự kiểm: từ chối khi image đích mất; chờ /health và xác nhận 3 container về PREV_TAG")
def rel_rollback_verifies(ctx: Ctx) -> R:
    rp = ctx.read_repo("scripts/release-prod.sh")
    m = re.search(r'if \[ "\$MODE" = rollback \]; then(.*?)\nfi\n', rp, re.S)
    if not m:
        raise Unavailable("không tìm thấy nhánh rollback trong release-prod.sh")
    body = m.group(1)
    gaps = []
    if not re.search(r"wait_health|curl\b[^\n]*/health", body):
        gaps.append("không chờ /health")
    if "image inspect" not in body:
        gaps.append("không kiểm image PREV_TAG còn trước khi sửa compose")
    if "prod_tag" not in body:
        gaps.append("không xác nhận 3 container về PREV_TAG")
    if gaps:
        return R(FAIL, "nhánh rollback: " + "; ".join(gaps) + " (test xfail trong tests/test_release_prod.py)",
                 "rollback: docker image inspect PREV_TAG trước cp; sau compose up gọi wait_health 8131+18185 và so prod_tag")
    return R(PASS, "rollback kiểm image, health và tag")


@check("OPS-REL-5", MUST, "Mọi container trong phạm vi do compose quản lý và file cấu hình của nó còn tồn tại")
def rel_compose_managed(ctx: Ctx) -> R:
    bad = []
    for name, c in sorted(ctx.containers().items()):
        lab = (c.get("Config") or {}).get("Labels") or {}
        proj, files = lab.get("com.docker.compose.project"), lab.get("com.docker.compose.project.config_files", "")
        if not proj:
            bad.append(f"{name} (docker run tay, không compose)")
            continue
        lost = [f for f in files.split(",") if f and not os.path.exists(f)]
        if lost:
            bad.append(f"{name} (mất {short(lost, 2)})")
    if bad:
        return R(FAIL, short(group_pool(bad)), "đưa service vào compose/profile có trong release dir; ghi lệnh tạo lại vào runbook")
    return R(PASS, f"{len(ctx.containers())} container đều có compose project và file cấu hình còn")


@check("OPS-REL-6", MUST, "Cấu hình deploy prod (compose secai-docker) được quản lý phiên bản (Git private)")
def rel_compose_vcs(ctx: Ctx) -> R:
    d = ctx.cfg.docker_dir
    if not d.is_dir():
        raise Unavailable(f"không có {d}")
    p = ctx.git("rev-parse", "--is-inside-work-tree", repo=d, check=False)
    if p.rc == 0 and p.out.strip() == "true":
        return R(PASS, f"{d} nằm trong Git")
    return R(FAIL, f"{d}/docker-compose.yml nằm ngoài Git (lịch sử chỉ là file .bak)",
             "git init repo private cho secai-docker (bỏ .env/secret qua .gitignore), commit mỗi lần đổi compose")


@check("OPS-REL-7", SHOULD, "Thư mục compose prod không tích file sao lưu (≤ 3 file docker-compose.yml.bak*)")
def rel_bak_count(ctx: Ctx) -> R:
    d = ctx.cfg.docker_dir
    if not d.is_dir():
        raise Unavailable(f"không có {d}")
    baks = sorted(p.name for p in d.glob("docker-compose.yml.bak*"))
    if len(baks) > 3:
        return R(FAIL, f"{len(baks)} file .bak cạnh compose prod", "chuyển vào release dir tương ứng hoặc Git; giữ ≤ 3")
    return R(PASS, f"{len(baks)} file .bak")


@check("OPS-REL-8", MUST, "Không bind-mount sống từ Git checkout vào container prod (trừ checkout chính ở main; không bao giờ src/)")
def rel_live_mounts(ctx: Ctx) -> R:
    roots = {}
    for w in ctx.worktrees():
        roots[str(Path(w["worktree"]).resolve())] = (w.get("branch") or "").replace("refs/heads/", "") or "(detached)"
    main_root = str(ctx.main_checkout().resolve())
    fails, warns = [], []
    for name, c in sorted(ctx.containers().items()):
        for m in c.get("Mounts") or []:
            if m.get("Type") != "bind":
                continue
            src = str(Path(m["Source"]))
            root = next((r for r in roots if src == r or src.startswith(r + "/")), None)
            if not root:
                continue
            rel = src[len(root):].lstrip("/") or "."
            if rel == "src" or rel.startswith("src/") or root != main_root or roots[root] != "main":
                fails.append(f"{name}:{rel} (checkout {Path(root).name}@{roots[root]})")
            else:
                warns.append((rel, "pool pentestcode" if POOL_RE.match(name) else name))
    if fails:
        return R(FAIL, "mount từ checkout không phải main/có src: " + short(fails))
    if warns:
        by_rel: dict[str, set[str]] = {}
        for rel, who in warns:
            by_rel.setdefault(rel, set()).add(who)
        desc = [f"{rel} → {', '.join(sorted(w))}" for rel, w in sorted(by_rel.items())]
        return R(WARN, f"{len(warns)} mount sống từ checkout chính {main_root} (đang ở main), bỏ qua image bất biến: {short(desc, 6)}",
                 "đóng gói demo/evidence/helm/pentestcode vào image hoặc copy sang thư mục release riêng")
    return R(PASS, "không mount từ Git checkout")


# ================================================================== VER
@check("OPS-VER-1", MUST, "Container trong phạm vi được tạo từ tag, không từ image ID trần")
def ver_no_bare_id(ctx: Ctx) -> R:
    bad = []
    for name, c in sorted(ctx.containers().items()):
        ref = (c.get("Config") or {}).get("Image", "")
        if is_bare_id(ref):
            im = ctx.images([c["Image"]]).get(c["Image"]) or {}
            tags = im.get("RepoTags") or []
            bad.append(f"{name} ({ref.replace('sha256:', '')[:12]}" + (f", tag có sẵn: {tags[0]}" if tags else ", không tag") + ")")
    if bad:
        return R(FAIL, "; ".join(bad), "tạo lại container bằng tag bất biến (đúng image ID hiện tại) qua compose/profile")
    return R(PASS, f"{len(ctx.containers())} container đều tạo từ tag")


@check("OPS-VER-2", MUST, "Tag image first-party bất biến, truy được về commit (<kênh>-<sha>-<YYYYMMDD>)")
def ver_first_party_tags(ctx: Ctx) -> R:
    res: dict[str, list[str]] = {PASS: [], WARN: [], FAIL: []}
    seen = set()
    for name, c in sorted(ctx.containers().items()):
        ref = c["Config"]["Image"]
        if is_bare_id(ref) or not first_party(ref) or ref in seen:
            continue
        seen.add(ref)
        st, why = classify_first_party_tag(image_repo_tag(ref)[1])
        res[st].append(f"{ref} ({why})")
    st = worst([s for s in res if res[s]])
    ev = "; ".join(f"{s}: {short(v, 4)}" for s, v in res.items() if v and s != PASS) or f"{len(res[PASS])} tag đạt"
    return R(st, ev, "" if st == PASS else "tag lại theo <kênh>-<sha8>-<YYYYMMDD> (vd main-2dcff27c-20261127); bỏ tag số trần như :1")


@check("OPS-VER-3", MUST, "Image bên thứ ba ghim version đầy đủ x.y.z hoặc digest")
def ver_third_party_tags(ctx: Ctx) -> R:
    res: dict[str, list[str]] = {PASS: [], WARN: [], FAIL: []}
    for ref in sorted({c["Config"]["Image"] for c in ctx.containers().values()}):
        if is_bare_id(ref) or first_party(ref):
            continue
        repo, tag, digest = image_repo_tag(ref)
        st, why = classify_third_party_tag(tag, digest)
        res[st].append(f"{ref} ({why})")
    st = worst([s for s in res if res[s]])
    ev = "; ".join(f"{s}: {short(v, 4)}" for s, v in res.items() if v and s != PASS) or f"{len(res[PASS])} image đạt"
    return R(st, ev, "" if st == PASS else "ghim redis:7.x.y-alpine, neo4j:5.20.z-community (hoặc @sha256) trong compose prod")


@check("OPS-VER-4", MUST, "Image first-party có label org.opencontainers.image.revision")
def ver_revision_label(ctx: Ctx) -> R:
    miss, ok = [], 0
    for name, c in sorted(ctx.containers().items()):
        ref = c["Config"]["Image"]
        im = ctx.images([c["Image"]]).get(c["Image"]) or {}
        tags = im.get("RepoTags") or []
        if not (first_party(ref) or any(first_party(t) for t in tags)):
            continue
        labels = {**((im.get("Config") or {}).get("Labels") or {}), **((c.get("Config") or {}).get("Labels") or {})}
        if labels.get("org.opencontainers.image.revision"):
            ok += 1
        else:
            miss.append(name)
    if miss:
        return R(FAIL, f"thiếu label revision: {short(group_pool(miss))}",
                 "Dockerfile: ARG GIT_SHA + LABEL org.opencontainers.image.revision=$GIT_SHA (build FULL của release-prod.sh hiện không gắn)")
    return R(PASS, f"{ok} container first-party có label revision")


@check("OPS-VER-5", MUST, "3 container gateway chạy cùng tag và cùng image ID")
def ver_trio(ctx: Ctx) -> R:
    cs = ctx.containers()
    miss = [n for n in GATEWAY_TRIO if n not in cs]
    if miss:
        return R(FAIL, f"thiếu container: {', '.join(miss)}")
    tags = {n: cs[n]["Config"]["Image"] for n in GATEWAY_TRIO}
    ids = {n: cs[n]["Image"] for n in GATEWAY_TRIO}
    if len(set(tags.values())) == 1 and len(set(ids.values())) == 1:
        return R(PASS, f"cả 3 = {tags[GATEWAY_TRIO[0]]} ({ids[GATEWAY_TRIO[0]].replace('sha256:', '')[:12]})")
    return R(FAIL, "lệch: " + "; ".join(f"{n}={tags[n]}@{ids[n].replace('sha256:', '')[:12]}" for n in GATEWAY_TRIO),
             "release lại cả 3 bằng release-prod.sh; không đổi lẻ một container")


@check("OPS-VER-6", MUST, "Tag prod gateway dạng main-<sha8>-<ngày>, sha8 nằm trên origin/main, khớp state.env và GIT_SHA")
def ver_prod_tag(ctx: Ctx) -> R:
    cs = ctx.containers()
    if "secai-docker-gateway" not in cs:
        return R(FAIL, "không thấy secai-docker-gateway")
    c = cs["secai-docker-gateway"]
    tag = c["Config"]["Image"]
    m = re.fullmatch(rf"{GATEWAY_IMAGE}:main-([0-9a-f]{{8}})-(\d{{8}})", tag)
    if not m:
        return R(FAIL, f"tag prod {tag} không theo main-<sha8>-<ngày>")
    sha, problems = m.group(1), []
    anc = ctx.git("merge-base", "--is-ancestor", sha, "origin/main", check=False)
    if anc.rc != 0:
        problems.append(f"{sha} không phải tổ tiên origin/main (local, chưa fetch)")
    try:
        _, st = ctx.latest_release()
        if st.get("NEW_TAG") != tag:
            problems.append(f"state.env NEW_TAG={st.get('NEW_TAG')}")
    except Unavailable as e:
        problems.append(str(e))
    for n in GATEWAY_TRIO:
        if n in cs and ctx.env(cs[n]).get("GIT_SHA") not in (sha, None):
            problems.append(f"{n} GIT_SHA={ctx.env(cs[n]).get('GIT_SHA')}")
    if problems:
        return R(FAIL, f"{tag}: " + "; ".join(problems))
    return R(PASS, f"{tag}: trên origin/main, khớp LATEST-PROD và GIT_SHA")


@check("OPS-VER-7", SHOULD, "Service chạy code gateway không cũ hơn image prod gateway quá 30 ngày")
def ver_stale_code(ctx: Ctx) -> R:
    cs = ctx.containers()
    if "secai-docker-gateway" not in cs:
        raise Unavailable("không thấy secai-docker-gateway")
    ims = ctx.images([c["Image"] for c in cs.values()])
    ref_t = parse_ts((ims.get(cs["secai-docker-gateway"]["Image"]) or {}).get("Created", ""))
    if not ref_t:
        raise Unavailable("không đọc được Created của image prod")
    old = []
    for name, c in sorted(cs.items()):
        if name in GATEWAY_TRIO:
            continue
        im = ims.get(c["Image"]) or {}
        tags = im.get("RepoTags") or [c["Config"]["Image"]]
        if not any(t.startswith(GATEWAY_IMAGE + ":") for t in tags):
            continue
        t = parse_ts(im.get("Created", ""))
        if t and (ref_t - t).days > 30:
            old.append(f"{name} ({t:%Y-%m-%d}, cũ hơn {(ref_t - t).days} ngày)")
    if old:
        return R(FAIL, "; ".join(old), "release lại các service này từ main (hoặc ghi rõ lý do giữ bản cũ trong runbook)")
    return R(PASS, "mọi service chạy code gateway trong vòng 30 ngày so với prod")


@check("OPS-VER-8", SHOULD, "Sổ phiên bản dịch vụ (scripts/versions.py) có và khớp trạng thái thật")
def ver_ledger(ctx: Ctx) -> R:
    p = ctx.git("cat-file", "-e", "origin/main:scripts/versions.py", check=False)
    if p.rc != 0:
        return R(SKIP, "scripts/versions.py chưa có trên origin/main")
    return R(SKIP, "scripts/versions.py đã có trên origin/main; checker chưa tích hợp, đối chiếu tay với docker inspect")


# ================================================================== CI
@check("OPS-CI-1", MUST, "Có workflow CI chạy pytest cho mọi PR vào main")
def ci_workflow(ctx: Ctx) -> R:
    wf = sorted(glob.glob(str(ctx.cfg.repo / ".github" / "workflows" / "*.y*ml")))
    with_pytest = [w for w in wf if "pytest" in Path(w).read_text(errors="replace")]
    remote = None
    try:
        code, data = ctx.gh("repos/{repo}/actions/workflows")
        remote = (data or {}).get("total_count", 0) if code == 200 else 0
    except Unavailable:
        pass
    ev = f"repo: {len(wf)} workflow ({len(with_pytest)} chạy pytest)" + (f"; GitHub: {remote} workflow" if remote is not None else "")
    if with_pytest and (remote is None or remote > 0):
        return R(PASS, ev)
    return R(FAIL, ev, "thêm .github/workflows/ci.yml: uv sync --frozen + pytest -q trên pull_request (job không cần secret)")


@check("OPS-CI-2", MUST, "main có branch protection/ruleset: bắt PR, bắt check CI xanh, cấm force-push")
def ci_protection(ctx: Ctx) -> R:
    code, prot = ctx.gh("repos/{repo}/branches/main/protection")
    rcode, rules = ctx.gh("repos/{repo}/rules/branches/main")
    kinds = {r.get("type") for r in (rules or [])} if isinstance(rules, list) else set()
    if code == 200 and isinstance(prot, dict):
        need = [k for k in ("required_status_checks", "required_pull_request_reviews") if not prot.get(k)]
        if (prot.get("allow_force_pushes") or {}).get("enabled"):
            need.append("cấm force-push")
        if not need:
            return R(PASS, "branch protection bắt PR + status check")
        return R(FAIL, f"branch protection thiếu: {', '.join(need)}")
    if {"pull_request", "required_status_checks", "non_fast_forward"} <= kinds:
        return R(PASS, "ruleset bắt PR + status check + cấm force-push")
    return R(FAIL, f"main không có branch protection; ruleset áp lên main: {sorted(kinds) or 'không'}",
             "Settings → Rules: bắt PR, bắt check CI, chặn force-push/xoá cho main (sau khi có CI)")


@check("OPS-CI-3", SHOULD, "Repo bật delete_branch_on_merge")
def ci_delete_branch(ctx: Ctx) -> R:
    code, repo = ctx.gh("repos/{repo}")
    v = (repo or {}).get("delete_branch_on_merge")
    return R(PASS, "delete_branch_on_merge=true") if v else R(
        FAIL, f"delete_branch_on_merge={str(v).lower()}", "bật trong Settings → General → Pull Requests")


@check("OPS-CI-4", MUST, "Gate release chạy toàn bộ pytest và lần release gần nhất xanh")
def ci_release_gate(ctx: Ctx) -> R:
    rp = ctx.read_repo("scripts/release-prod.sh")
    if "-m pytest" not in rp:
        return R(FAIL, "release-prod.sh không còn gate pytest")
    rel, _ = ctx.latest_release()
    log = rel / "pytest.log"
    if not log.is_file():
        return R(FAIL, f"{rel.name}: thiếu pytest.log")
    last = (log.read_text(errors="replace").strip().splitlines() or [""])[-1]
    if " passed" in last and not re.search(r" (failed|error)", last):
        return R(PASS, f"{rel.name}: {last.strip('= ')}")
    return R(FAIL, f"{rel.name}: {last[:120]}")


@check("OPS-CI-5", SHOULD, "Bật Dependabot security updates hoặc có .github/dependabot.yml")
def ci_dependabot(ctx: Ctx) -> R:
    if (ctx.cfg.repo / ".github" / "dependabot.yml").is_file():
        return R(PASS, "có .github/dependabot.yml")
    code, repo = ctx.gh("repos/{repo}")
    sa = (repo or {}).get("security_and_analysis") or {}
    st = (sa.get("dependabot_security_updates") or {}).get("status")
    if st == "enabled":
        return R(PASS, "dependabot_security_updates=enabled")
    return R(FAIL, f"dependabot_security_updates={st or 'không đọc được'}; không có dependabot.yml",
             "bật Dependabot security updates (pip + docker)")


@check("OPS-CI-6", SHOULD, "Script shell vận hành có `set -u` + pipefail; script đổi prod có thêm `-e`")
def ci_shell(ctx: Ctx) -> R:
    bad, n = [], 0
    for pat in SHELL_SCRIPT_GLOBS:
        for f in sorted(ctx.cfg.repo.glob(pat)):
            txt = f.read_text(errors="replace")
            if not txt.startswith("#!"):
                continue  # file chỉ để source
            n += 1
            rel = str(f.relative_to(ctx.cfg.repo))
            fl = parse_set_flags(txt)
            need = {"u"} | ({"pipefail"} if "bash" in txt.splitlines()[0] else set())
            if rel in MUTATING_SCRIPTS:
                need.add("e")
            if need - fl:
                bad.append(f"{rel} thiếu {'/'.join(sorted(need - fl))}")
    if bad:
        return R(FAIL, short(bad))
    return R(PASS, f"{n} script đạt (cú pháp kiểm bằng bash -n trong tests/test_ops_repo.py)")


# ================================================================== BAK
BACKUP_STORES = {"qdrant": 7 * 24, "neo4j": 26, "sqlite": 26}


@check("OPS-BAK-1", MUST, "Có lịch backup định kỳ cho Qdrant, Neo4j và DB SQLite (report/review/session)")
def bak_schedule(ctx: Ctx) -> R:
    timers = ctx.timers()
    hit = [t for t in timers if re.search(r"backup|snapshot|dump", t, re.I)
           and re.search(r"secai|qdrant|neo4j|sqlite|report|review", t, re.I)]
    if hit:
        return R(PASS, f"timer backup: {short(hit)}")
    return R(FAIL, f"không có systemd timer backup/snapshot/dump ({len(timers)} timer); cron: kiểm tay `crontab -l`",
             "timer hằng ngày: snapshot Qdrant qua API, neo4j-admin dump (container tạm), sqlite3 .backup cho DB report/review/session")


def _newest_mtime(d: Path, depth: int = 3) -> float | None:
    best = None
    base = len(d.parts)
    for root, dirs, files in os.walk(d):
        if len(Path(root).parts) - base >= depth:
            dirs[:] = []
        for f in files:
            try:
                t = os.stat(os.path.join(root, f)).st_mtime
            except OSError:
                continue
            best = t if best is None or t > best else best
    return best


@check("OPS-BAK-2", MUST, "Backup mới nhất mỗi kho: SQLite/Neo4j ≤ 26 giờ, Qdrant ≤ 7 ngày")
def bak_fresh(ctx: Ctx) -> R:
    b = ctx.cfg.backup_dir
    if not b or not b.is_dir():
        return R(FAIL, f"chưa khai báo/không có thư mục backup ({b or 'SECAI_BACKUP_DIR/--backup-dir trống'})",
                 "đặt đích backup, mỗi kho một thư mục con: qdrant/ neo4j/ sqlite/")
    stale = []
    for store, max_h in BACKUP_STORES.items():
        t = _newest_mtime(b / store) if (b / store).is_dir() else None
        age_h = None if t is None else (ctx.now.timestamp() - t) / 3600
        if age_h is None or age_h > max_h:
            stale.append(f"{store} ({'không có' if age_h is None else f'{age_h:.0f}h'} > {max_h}h)")
    if stale:
        return R(FAIL, "backup cũ/thiếu: " + ", ".join(stale))
    return R(PASS, f"{', '.join(BACKUP_STORES)} đều trong hạn")


@check("OPS-BAK-3", MUST, "Bản backup nằm ngoài đĩa chứa dữ liệu prod")
def bak_offdisk(ctx: Ctx) -> R:
    data = ctx.cfg.data_root
    snap_note = ""
    qd = data / "qdrant"
    if qd.is_dir():
        snaps = sorted(qd.glob("*.snapshot"), key=lambda p: p.stat().st_mtime)
        if snaps:
            age = (ctx.now.timestamp() - snaps[-1].stat().st_mtime) / 86400
            snap_note = f"; snapshot Qdrant mới nhất {snaps[-1].name} ({age:.0f} ngày) nằm cùng đĩa"
    b = ctx.cfg.backup_dir
    if not b or not b.is_dir():
        return R(FAIL, "không có đích backup ngoài máy/đĩa" + snap_note,
                 "đẩy backup sang đĩa khác hoặc máy khác (rsync/rclone, mã hoá); khác máy kiểm tay")
    try:
        same = os.stat(b).st_dev == os.stat(data).st_dev
    except OSError as e:
        raise Unavailable(str(e))
    if same:
        return R(FAIL, f"{b} cùng thiết bị với {data}" + snap_note)
    return R(PASS, f"{b} khác thiết bị với {data} (khác máy: kiểm tay)")


@check("OPS-BAK-4", MUST, "Có biên bản diễn tập khôi phục trong 92 ngày gần nhất")
def bak_drill(ctx: Ctx) -> R:
    files = sorted(ctx.cfg.repo.glob("docs/archive/test-results/restore-drill-*.md"))
    dates = []
    for f in files:
        m = re.search(r"(\d{8})", f.name)
        if m:
            dates.append(dt.datetime.strptime(m.group(1), "%Y%m%d").date())
    if dates and (ctx.now.date() - max(dates)).days <= 92:
        return R(PASS, f"diễn tập gần nhất {max(dates)}")
    return R(FAIL, "chưa có docs/archive/test-results/restore-drill-YYYYMMDD.md" if not dates else f"diễn tập gần nhất {max(dates)}",
             "khôi phục backup vào stack dev/máy tạm, đo thời gian, ghi biên bản")


DB_VARS = {  # biến -> điều kiện bật (None = luôn kiểm nếu là app gateway)
    "GATEWAY_DB_PATH": None, "TRANSLATE_V2_DB_PATH": None, "TRANSLATE_V2_WORKER_DB_PATH": None,
    "SESSION_DB_PATH": ("SESSION_STORE_BACKEND", "sqlite"),
    "REVIEW_DB_PATH": ("REVIEW_ENABLED", "true"),
    "REPORT_DB_PATH": ("REPORT_FEATURE_ENABLED", "true"),
}


@check("OPS-BAK-5", MUST, "DB SQLite của service gateway nằm trên volume/bind (giữ qua rollback, backup được)")
def bak_db_on_volume(ctx: Ctx) -> R:
    bad, n = [], 0
    for name, c in sorted(ctx.containers().items()):
        if not is_gateway_app(c):
            continue
        env = ctx.env(c)
        for var, cond in DB_VARS.items():
            path = env.get(var)
            if not path or (cond and env.get(cond[0], "").lower() != cond[1]):
                continue
            n += 1
            if not mounted(c, path):
                bad.append(f"{name}:{var}={path}")
    if bad:
        return R(FAIL, "DB nằm trong lớp container: " + short(bad), "mount volume cho thư mục DB")
    return R(PASS, f"{n} DB đang dùng đều nằm trên mount")


# ================================================================== MON
@check("OPS-MON-1", MUST, "Mọi container trong phạm vi đang chạy; container có healthcheck ở trạng thái healthy")
def mon_running(ctx: Ctx) -> R:
    cs = ctx.containers()
    bad = [f"{n} (thiếu)" for n in APP_CONTAINERS + DATASTORES if n not in cs]
    pool = [n for n in cs if POOL_RE.match(n)]
    for name, c in sorted(cs.items()):
        s = c.get("State") or {}
        h = (s.get("Health") or {}).get("Status")
        if s.get("Status") != "running" or (h and h != "healthy"):
            bad.append(f"{name} ({s.get('Status')}{'/' + h if h else ''})")
    if len(pool) < POOL_EXPECTED:
        bad.append(f"pool pentestcode {len(pool)}/{POOL_EXPECTED}")
    if bad:
        return R(FAIL, short(bad))
    return R(PASS, f"{len(cs)} container chạy (pool {len(pool)}), có healthcheck thì healthy")


@check("OPS-MON-2", SHOULD, "Mọi container trong phạm vi có HEALTHCHECK")
def mon_healthcheck(ctx: Ctx) -> R:
    miss = [n for n, c in sorted(ctx.containers().items())
            if not ((c.get("Config") or {}).get("Healthcheck") or {}).get("Test")
            or ((c.get("Config") or {}).get("Healthcheck") or {}).get("Test") == ["NONE"]]
    if miss:
        return R(FAIL, f"không healthcheck: {short(group_pool(miss))}", "thêm healthcheck trong compose")
    return R(PASS, "đều có healthcheck")


@check("OPS-MON-3", MUST, "Restart policy unless-stopped/always cho container trong phạm vi")
def mon_restart(ctx: Ctx) -> R:
    bad = [f"{n} ({(c.get('HostConfig') or {}).get('RestartPolicy', {}).get('Name') or 'no'})"
           for n, c in sorted(ctx.containers().items())
           if (c.get("HostConfig") or {}).get("RestartPolicy", {}).get("Name") not in ("unless-stopped", "always")]
    return R(FAIL, short(group_pool(bad))) if bad else R(PASS, "đều unless-stopped/always")


@check("OPS-MON-4", MUST, "Có cảnh báo chủ động khi dịch vụ chết/health xấu")
def mon_alerting(ctx: Ctx) -> R:
    units = [u["Id"] for u in ctx.units()]
    timers = ctx.timers()
    hit = [u for u in units + timers if re.search(r"secai.*(alert|monitor|watchdog|healthcheck|uptime)", u, re.I)]
    if hit:
        return R(PASS, f"có: {short(hit)} (kênh nhận cảnh báo: kiểm tay)")
    return R(FAIL, "không có unit/timer giám sát hay cảnh báo cho secai-*; hệ thống ngoài: kiểm tay",
             "timer 5 phút: docker health + /health 8131/18185 + unit failed → gửi cảnh báo (Telegram/email)")


@check("OPS-MON-5", MUST, "Không unit systemd secai-* nào failed hoặc khởi động lại liên tục")
def mon_units(ctx: Ctx) -> R:
    bad = []
    for u in ctx.units():
        nres = int(u.get("NRestarts") or 0)
        if u.get("ActiveState") == "failed" or u.get("SubState") == "auto-restart" or nres > 100:
            bad.append(f"{u['Id']} ({u.get('ActiveState')}/{u.get('SubState')}, {nres} lần restart)")
    if bad:
        return R(FAIL, short(bad, 5), "sửa đích tunnel hoặc disable unit của instance đã huỷ; reset-failed sau khi xử lý")
    return R(PASS, f"{len(ctx.units())} unit secai-* không failed/restart loop")


@check("OPS-MON-6", SHOULD, "Log container có giới hạn dung lượng (max-size/max-file hoặc driver local/journald)")
def mon_logs(ctx: Ctx) -> R:
    bad = []
    for n, c in sorted(ctx.containers().items()):
        lc = (c.get("HostConfig") or {}).get("LogConfig") or {}
        if lc.get("Type") == "json-file" and not (lc.get("Config") or {}).get("max-size"):
            bad.append(n)
    if bad:
        return R(FAIL, f"json-file không giới hạn: {short(group_pool(bad))}",
                 "daemon.json log-opts max-size=50m,max-file=3 (áp khi tạo lại container) hoặc logging: trong compose")
    return R(PASS, "log có giới hạn")


manual("OPS-MON-7", SHOULD, "/health prod báo status ok và không có problems",
       "curl -fsS http://127.0.0.1:8131/health và :18185/health: status ok, problems rỗng, git_sha khớp tag")


# ================================================================== SEC
def _published(c: dict) -> list[tuple[str, str]]:
    out = []
    for port, binds in ((c.get("HostConfig") or {}).get("PortBindings") or {}).items():
        for b in binds or []:
            out.append((b.get("HostIp") or "0.0.0.0", f"{b.get('HostPort')}->{port}"))
    return out


@check("OPS-SEC-1", MUST, "Kho dữ liệu (Qdrant, Neo4j, Redis, ES) không publish cổng ra mọi interface")
def sec_datastore_ports(ctx: Ctx) -> R:
    bad = []
    for n in DATASTORES:
        c = ctx.containers().get(n)
        if not c:
            continue
        pub = [p for ip, p in _published(c) if ip in ("0.0.0.0", "::", "")]
        if pub:
            bad.append(f"{n} {','.join(pub)}")
    if bad:
        return R(FAIL, "publish 0.0.0.0: " + "; ".join(bad) + " (firewall DOCKER-USER: kiểm tay)",
                 "bỏ ports hoặc bind 127.0.0.1:...; gateway/dev stack đã đi qua mạng secai-net")
    return R(PASS, "kho dữ liệu chỉ bind loopback hoặc mạng nội bộ")


@check("OPS-SEC-2", MUST, "Chỉ gateway :8131 publish công khai; service khác bind 127.0.0.1/LAN")
def sec_app_ports(ctx: Ctx) -> R:
    bad, host_net = [], []
    for n in APP_CONTAINERS + tuple(sorted(x for x in ctx.containers() if POOL_RE.match(x))):
        c = ctx.containers().get(n)
        if not c:
            continue
        if (c.get("HostConfig") or {}).get("NetworkMode") == "host":
            host_net.append(n)
        for ip, p in _published(c):
            if ip in ("0.0.0.0", "::", "") and not (n == "secai-docker-gateway" and p.startswith("8131->")):
                bad.append(f"{n} {p}")
    note = f"; network=host (kiểm tay `ss -ltnp`): {short(host_net, 4)}" if host_net else ""
    if bad:
        return R(FAIL, "publish 0.0.0.0: " + "; ".join(bad) + note, "bind 127.0.0.1 hoặc IP LAN; public chỉ :8131")
    return R(PASS, "không service nào ngoài :8131 publish 0.0.0.0" + note)


def _secret_names(text: str, ctx: Ctx) -> list[str]:
    names = []
    for m in SECRET_ASSIGN_RE.finditer(text):
        if not is_secret_name(m.group(1)):
            continue
        v = m.group(2)
        if v.startswith(("$", "<", "{")) or len(v) < 8 or v.lower() in ("changeme", "required", "optional", "none", "null"):
            continue
        ctx.remember_secret(v)
        names.append(m.group(1))
    return sorted(set(names))


def _walk_files(root: Path, depth: int, skip_dirs=("tree", "imgsrc", "node_modules", ".git", "__pycache__", ".venv")):
    base = len(root.parts)
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in skip_dirs] if len(Path(d).parts) - base < depth else []
        for f in files:
            p = Path(d) / f
            if not p.is_symlink():
                yield p


@check("OPS-SEC-3", MUST, "File chứa secret (profile, .env, compose bak, inspect dump) chỉ chủ sở hữu đọc được")
def sec_file_perms(ctx: Ctx) -> R:
    envs, leaks, n = [], [], 0
    for root in (ctx.cfg.releases_dir, ctx.cfg.docker_dir):
        if not root.exists():
            continue
        for p in _walk_files(root.resolve(), 3):
            try:
                st_ = p.stat()
            except OSError:
                continue
            if st_.st_size > 4_000_000:
                continue
            n += 1
            mode = oct(st_.st_mode & 0o777)[2:]
            if (p.name == ".env" or p.name.startswith(".env.")) and not p.name.endswith(".example"):
                if st_.st_mode & 0o077:  # .env* phải 600 dù nội dung là gì
                    envs.append(f"{p} ({mode}{', world-readable' if world_readable(p) else ''})")
                continue
            if not world_readable(p):
                continue
            try:
                names = _secret_names(p.read_text(errors="ignore"), ctx)
            except OSError:
                continue
            if names:
                leaks.append(f"{p} [{short(names, 3)}] ({mode})")
    if envs or leaks:
        parts = []
        if leaks:
            parts.append(f"{len(leaks)} file có secret mà mọi user đọc được: {short(leaks, 3)}")
        if envs:
            parts.append(f"{len(envs)} file .env* không phải 600: {short(envs, 3)}")
        return R(FAIL, "; ".join(parts), "chmod 600 các file này, 700 thư mục release; tạo profile/bak bằng umask 077")
    return R(PASS, f"đã quét {n} file trong releases + secai-docker: .env* đều 600, không file nào lộ secret")


@check("OPS-SEC-4", MUST, "Compose prod không chứa secret dạng literal (chỉ ${VAR}/env_file)")
def sec_compose_literal(ctx: Ctx) -> R:
    comp = ctx.cfg.docker_dir / "docker-compose.yml"
    if not comp.is_file():
        raise Unavailable(f"không có {comp}")
    names = []
    for line in comp.read_text(errors="replace").splitlines():
        m = re.match(r"""^\s*-?\s*["']?([A-Za-z0-9_]+)["']?\s*[:=]\s*(.*)$""", line)
        if not m or not is_secret_name(m.group(1)):
            continue
        v = m.group(2).strip().strip("'\"")
        if v and not v.startswith("$") and v.lower() not in ("none", "false", "true"):
            ctx.remember_secret(v)
            names.append(m.group(1))
    mode = oct(comp.stat().st_mode & 0o777)[2:]
    if names:
        wr = "world-readable" if world_readable(comp) else "không world-readable"
        return R(FAIL, f"{comp.name} (mode {mode}, {wr}) có literal: {', '.join(sorted(set(names)))}",
                 "chuyển sang ${VAR} trong .env 600; đổi mật khẩu đã lộ")
    return R(PASS, f"{comp.name}: biến secret đều qua ${{VAR}}/env_file")


@check("OPS-SEC-5", MUST, "Script cron/đồng bộ (CVE-DB) không chứa khoá API plaintext; file khoá mode 600")
def sec_cron_scripts(ctx: Ctx) -> R:
    hits, n = [], 0
    for pat in ctx.cfg.cron_globs:
        for f in sorted(glob.glob(os.path.expanduser(pat))):
            p = Path(f)
            if not p.is_file() or p.is_symlink():
                continue
            mode = p.stat().st_mode & 0o777
            if is_secret_name(p.name) and mode & 0o077:
                hits.append(f"{p} (file khoá, mode {oct(mode)[2:]})")
                continue
            if p.suffix in (".log", ".csv", ".json", ".md", ".db", ".old") or p.stat().st_size > 1_000_000:
                continue
            raw = p.read_bytes()
            if b"\0" in raw[:4096]:
                continue
            n += 1
            names = _secret_names(raw.decode(errors="ignore"), ctx)
            if names:
                hits.append(f"{p} [{', '.join(names)}]")
    if hits:
        return R(FAIL, f"{len(hits)} chỗ: " + short(hits, 4),
                 "đưa khoá vào file 600 (EnvironmentFile/`. ~/.config/...`), xoá khỏi script và các bản .bak, xoay khoá")
    return R(PASS, f"đã quét {n} file: không có khoá plaintext")


def scan_tracked_secrets(repo: Path, runner: Runner | None = None) -> list[tuple[str, int, str]]:
    """(path, line, kind) của chuỗi giống secret trong file tracked, ngoài FAKE_SECRET_FIXTURES. Không trả giá trị."""
    r = runner or Runner()
    p = r.run(["git", "--no-optional-locks", "-C", str(repo), "ls-files", "-z"])
    out = []
    for f in p.out.split("\0"):
        if not f or f.startswith(FAKE_SECRET_FIXTURES):
            continue
        fp = repo / f
        try:
            if fp.is_symlink() or fp.stat().st_size > 2_000_000:
                continue
            b = fp.read_bytes()
        except OSError:
            continue
        if b"\0" in b[:4096]:
            continue
        for kind, rx in TRACKED_SECRET_PATTERNS.items():
            for m in rx.finditer(b):
                out.append((f, b.count(b"\n", 0, m.start()) + 1, kind))
    return out


@check("OPS-SEC-6", MUST, "Không secret thật trong file tracked (ngoài fixture giả đã khai báo)")
def sec_tracked(ctx: Ctx) -> R:
    hits = scan_tracked_secrets(ctx.cfg.repo, ctx.r)
    if hits:
        return R(FAIL, f"{len(hits)} chỗ: " + short([f"{f}:{ln} ({k})" for f, ln, k in hits], 5),
                 "xoá khỏi cây + xoay khoá; nếu là fixture giả thì thêm vào FAKE_SECRET_FIXTURES")
    return R(PASS, f"không có ngoài {len(FAKE_SECRET_FIXTURES)} vùng fixture giả; lịch sử Git: kiểm tay bằng gitleaks")


@check("OPS-SEC-7", MUST, "Không truyền secret qua argv (Cmd/Entrypoint container, ExecStart unit secai-*)")
def sec_argv(ctx: Ctx) -> R:
    hits = []
    for n, c in sorted(ctx.containers().items()):
        cfg = c.get("Config") or {}
        argv = " ".join((cfg.get("Entrypoint") or []) + (cfg.get("Cmd") or []) + (c.get("Args") or []))
        for m in ARGV_SECRET_RE.finditer(argv):
            hits.append(f"{n} ({m.group(1) or m.group(2)})")
            ctx.remember_secret(m.group(0).split("=", 1)[-1].split()[-1])
        for m in URL_CRED_RE.finditer(argv):
            hits.append(f"{n} (URL có mật khẩu)")
            ctx.remember_secret(m.group(1))
    try:
        units = ctx.units()
    except Unavailable:
        units = []
    for u in units:
        ex = u.get("ExecStart", "")
        for m in ARGV_SECRET_RE.finditer(ex):
            hits.append(f"{u['Id']} ({m.group(1) or m.group(2)})")
            ctx.remember_secret(m.group(0).split("=", 1)[-1].split()[-1])
    if hits:
        return R(FAIL, short(sorted(set(hits))), "truyền qua env_file/EnvironmentFile hoặc file 600")
    return R(PASS, f"{len(ctx.containers())} container + {len(units)} unit: không thấy secret trong argv")


@check("OPS-SEC-8", SHOULD, "Container chạy user không phải root")
def sec_nonroot(ctx: Ctx) -> R:
    bad = [n for n, c in sorted(ctx.containers().items())
           if ((c.get("Config") or {}).get("User") or "root").split(":")[0] in ("", "root", "0")]
    if bad:
        return R(FAIL, f"chạy root: {short(group_pool(bad))}", "user: trong compose (pool pentestcode cần xét quyền tool)")
    return R(PASS, "không container nào chạy root")


@check("OPS-SEC-9", SHOULD, "Image không bake secret vào ENV")
def sec_image_env(ctx: Ctx) -> R:
    cs = ctx.containers()
    ims = ctx.images(sorted({c["Image"] for c in cs.values()}))
    bad = []
    for iid, im in ims.items():
        for e in (im.get("Config") or {}).get("Env") or []:
            k, _, v = e.partition("=")
            if is_secret_name(k) and v:
                tag = (im.get("RepoTags") or [iid[7:19]])[0]
                bad.append(f"{tag}:{k}")
    if bad:
        return R(FAIL, short(sorted(set(bad))), "bỏ ENV secret khỏi Dockerfile; truyền lúc chạy")
    return R(PASS, f"{len(ims)} image không có ENV dạng secret")


@check("OPS-SEC-10", MUST, "Repo public phải bật secret scanning và push protection")
def sec_github_scanning(ctx: Ctx) -> R:
    code, repo = ctx.gh("repos/{repo}")
    repo = repo or {}
    if repo.get("private"):
        return R(PASS, "repo private")
    sa = repo.get("security_and_analysis")
    if not sa:
        raise Unavailable("không đọc được security_and_analysis (cần quyền admin)")
    st = {k: (sa.get(k) or {}).get("status") for k in ("secret_scanning", "secret_scanning_push_protection")}
    if all(v == "enabled" for v in st.values()):
        return R(PASS, "public; secret scanning + push protection bật")
    return R(FAIL, "repo PUBLIC; " + ", ".join(f"{k}={v}" for k, v in st.items()),
             "bật Secret scanning + Push protection (miễn phí cho repo public) hoặc chuyển private")


# ================================================================== RES
@check("OPS-RES-1", MUST, "Đĩa chứa Docker/secai-data dùng ≤ 80% (80–90% cảnh báo, > 90% không đạt)")
def res_disk(ctx: Ctx) -> R:
    seen = {}
    for p in (Path("/"), ctx.cfg.data_root, ctx.cfg.releases_dir):
        if p.exists():
            mnt, pct = ctx.df(p)
            seen[mnt] = pct
    if not seen:
        raise Unavailable("không df được")
    mnt, pct = max(seen.items(), key=lambda kv: kv[1])
    ev = ", ".join(f"{m} {v}%" for m, v in seen.items())
    if pct > 90:
        return R(FAIL, ev, "dọn image/build cache (OPS-RES-3/4), chuyển snapshot/backup sang đĩa khác")
    return R(WARN, ev) if pct > 80 else R(PASS, ev)


@check("OPS-RES-2", SHOULD, f"Image {GATEWAY_IMAGE} ≤ 20 tag")
def res_tag_count(ctx: Ctx) -> R:
    n = len(ctx.gateway_images())
    return R(PASS, f"{n} tag") if n <= 20 else R(FAIL, f"{n} tag {GATEWAY_IMAGE}", "xoá tag cũ ngoài quy ước (OPS-RES-3)")


@check("OPS-RES-3", MUST, f"Không có image {GATEWAY_IMAGE} ngoài quy ước gc (main-/smoke-/dev-) cũ > 14 ngày mà không container nào dùng")
def res_untracked_images(ctx: Ctx) -> R:
    used_ids = {c.get("Image") for c in ctx.inspect_all()}
    used_refs = {(c.get("Config") or {}).get("Image") for c in ctx.inspect_all()}
    stray = []
    for im in ctx.gateway_images():
        tag, iid = im.get("Tag", ""), im.get("ID", "")
        if re.match(r"^(main|smoke|dev)-", tag) or tag == "<none>":
            continue
        t = parse_ts(im.get("CreatedAt", ""))
        if not t or (ctx.now - t).days <= 14:
            continue
        if iid in used_ids or f"{GATEWAY_IMAGE}:{tag}" in used_refs:
            continue
        stray.append(tag)
    if stray:
        return R(FAIL, f"{len(stray)} tag gc không dọn: {short(stray, 5)}",
                 "rà danh sách, `docker rmi` tag không còn dùng; tag mới theo <kênh>-<sha8>-<ngày> để gc nhận")
    return R(PASS, "mọi tag cũ đều thuộc quy ước gc hoặc đang dùng")


@check("OPS-RES-4", SHOULD, "Dung lượng image thu hồi được ≤ 30 GB")
def res_reclaimable(ctx: Ctx) -> R:
    row = next((r for r in ctx.system_df() if r.get("Type") == "Images"), None)
    if not row:
        raise Unavailable("docker system df không có dòng Images")
    gb = size_gb(row.get("Reclaimable", ""))
    ev = f"images {row.get('Size')} tổng, thu hồi được {row.get('Reclaimable')}"
    return R(PASS, ev) if gb <= 30 else R(FAIL, ev, "docker image prune (dangling) + xoá tag cũ; không prune volume")


@check("OPS-RES-5", MUST, "Không còn container *-rollback-*/*-smoke-* đã dừng quá 3 ngày; timer gc chạy tốt")
def res_gc(ctx: Ctx) -> R:
    old = []
    for c in ctx.inspect_all():
        n = c["Name"].lstrip("/")
        s = c.get("State") or {}
        if not re.search(r"-(rollback|smoke)-", n) or s.get("Status") not in ("exited", "created", "dead"):
            continue
        t = parse_ts(s.get("FinishedAt", "")) or parse_ts(c.get("Created", ""))
        if t and (ctx.now - t).days > 3:
            old.append(n)
    probs = [f"{len(old)} container quá hạn: {short(old, 3)}"] if old else []
    try:
        units = {u["Id"]: u for u in ctx.units()}
        svc = units.get("secai-wt-prune.service")
        tim = units.get("secai-wt-prune.timer")
        if not tim or tim.get("ActiveState") != "active":
            probs.append("secai-wt-prune.timer không active")
        if svc and svc.get("Result") not in ("success", None):
            probs.append(f"secai-wt-prune.service Result={svc.get('Result')}")
    except Unavailable as e:
        probs.append(str(e))
    if probs:
        return R(FAIL, "; ".join(probs), "scripts/install-wt-prune-timer.sh; xem journalctl --user -u secai-wt-prune")
    return R(PASS, "không container rollback/smoke quá hạn; secai-wt-prune.timer active")


@check("OPS-RES-6", SHOULD, "Container trong phạm vi có giới hạn bộ nhớ")
def res_mem(ctx: Ctx) -> R:
    bad = [n for n, c in sorted(ctx.containers().items()) if not (c.get("HostConfig") or {}).get("Memory")]
    if bad:
        return R(FAIL, f"không mem_limit: {short(group_pool(bad))}", "mem_limit trong compose (đo RSS thực tế trước)")
    return R(PASS, "đều có mem_limit")


@check("OPS-RES-7", SHOULD, "Không có stack dev secai-gw-dev-* mồ côi (worktree đã mất) còn chạy")
def res_dev_orphans(ctx: Ctx) -> R:
    running, orphans = set(), set()
    for c in ctx.inspect_all():
        lab = (c.get("Config") or {}).get("Labels") or {}
        proj = lab.get("com.docker.compose.project", "")
        if not proj.startswith(DEV_PROJECT_PREFIX) or (c.get("State") or {}).get("Status") != "running":
            continue
        running.add(proj)
        wd = lab.get("com.docker.compose.project.working_dir", "")
        if wd and not os.path.isdir(wd):
            orphans.add(proj)
    if orphans:
        return R(FAIL, f"mồ côi: {short(sorted(orphans))}", "scripts/wt-prune.sh --yes (dọn stack mồ côi)")
    return R(PASS, f"{len(running)} stack dev đang chạy (chỉ tham chiếu), không mồ côi")


@check("OPS-RES-8", SHOULD, "Build cache Docker ≤ 20 GB")
def res_build_cache(ctx: Ctx) -> R:
    row = next((r for r in ctx.system_df() if r.get("Type") == "Build Cache"), None)
    if not row:
        raise Unavailable("docker system df không có dòng Build Cache")
    gb = size_gb(row.get("Size", ""))
    return R(PASS, f"build cache {row.get('Size')}") if gb <= 20 else R(FAIL, f"build cache {row.get('Size')}", "docker builder prune")


# ================================================================== GIT
@check("OPS-GIT-1", MUST, "Checkout chính (nguồn mount prod) ở main, không ahead origin/main, không sửa dở trong thư mục được mount")
def git_main_checkout(ctx: Ctx) -> R:
    root = ctx.main_checkout()
    br = ctx.git("branch", "--show-current", repo=root).out.strip()
    probs, warns = [], []
    if br != "main":
        probs.append(f"đang ở nhánh {br or '(detached)'}")
    ahead = ctx.git("rev-list", "--count", "origin/main..HEAD", repo=root, check=False).out.strip()
    if ahead not in ("", "0"):
        probs.append(f"ahead origin/main {ahead} commit")
    dirty = [ln[3:] for ln in ctx.git("status", "--porcelain", "--untracked-files=no", repo=root).out.splitlines() if ln.strip()]
    if dirty:
        sources = []
        try:
            for c in ctx.containers().values():
                for m in c.get("Mounts") or []:
                    if m.get("Type") == "bind" and m["Source"].startswith(str(root) + "/"):
                        sources.append(m["Source"][len(str(root)) + 1:])
        except Unavailable:
            pass
        hot = [d for d in dirty if any(d == s or d.startswith(s.rstrip("/") + "/") for s in sources)]
        (probs if hot else warns).append(f"{len(dirty)} file sửa chưa commit ({short(hot or dirty, 3)})" + (" nằm trong mount prod" if hot else ""))
    if probs:
        return R(FAIL, f"{root}: " + "; ".join(probs + warns))
    if warns:
        return R(WARN, f"{root}@main: " + "; ".join(warns) + " (ngoài thư mục được mount)")
    return R(PASS, f"{root}@main sạch, không ahead")


@check("OPS-GIT-2", SHOULD, "Nhánh remote đã merge vào main còn lại ≤ 10")
def git_merged_branches(ctx: Ctx) -> R:
    merged = [b.strip() for b in ctx.git("branch", "-r", "--merged", "origin/main").out.splitlines()
              if b.strip() and not re.search(r"origin/(HEAD|main)\b", b)]
    total = len([b for b in ctx.git("branch", "-r").out.splitlines() if "->" not in b])
    ev = f"{len(merged)}/{total} nhánh remote đã merge (squash merge không đếm được)"
    return R(PASS, ev) if len(merged) <= 10 else R(FAIL, ev, "bật delete_branch_on_merge; wt-prune.sh --remote")


FORBIDDEN_TRACKED = re.compile(r"(^|/)\.env$|(^|/)\.env\.(?!.*example$)[^/]+$|\.(pem|key|db|sqlite3?)$|(^|/)id_(rsa|ed25519|ecdsa)[^/]*$|\.log$")


@check("OPS-GIT-3", MUST, "Không track .env, khoá, DB, log (log bằng chứng trong docs/archive, evidence/ được phép)")
def git_forbidden(ctx: Ctx) -> R:
    files = [f for f in ctx.git("ls-files", "-z").out.split("\0") if f]
    bad = [f for f in files if FORBIDDEN_TRACKED.search(f) and not (f.endswith(".log") and f.startswith(("docs/archive/", "evidence/")))]
    if bad:
        return R(FAIL, short(bad), "git rm --cached + .gitignore; xoay khoá nếu là secret")
    return R(PASS, f"{len(files)} file tracked, không có file cấm")


@check("OPS-GIT-4", SHOULD, "Không có worktree đã merge (đỉnh nằm trong origin/main) cũ hơn 24 giờ")
def git_stale_worktrees(ctx: Ctx) -> R:
    main_root = ctx.main_checkout()
    stale = []
    wts = ctx.worktrees()
    for w in wts[1:]:
        path, head = Path(w["worktree"]), w.get("HEAD")
        if not head or not w.get("branch") or not path.is_dir():
            continue
        if ctx.git("merge-base", "--is-ancestor", head, "origin/main", repo=main_root, check=False).rc != 0:
            continue
        age_h = (ctx.now.timestamp() - path.stat().st_mtime) / 3600
        if age_h > 24:
            stale.append(f"{path.name} ({age_h / 24:.0f} ngày)")
    ev = f"{len(wts)} worktree; đã merge, cũ > 24h: {len(stale)}" + (f" ({short(stale, 4)})" if stale else "")
    return R(FAIL, ev, "scripts/wt-prune.sh --yes (timer hằng giờ lẽ ra đã dọn; xem log)") if stale else R(PASS, ev)


@check("OPS-GIT-5", SHOULD, ".gitignore chặn .env, .env.*, data/, logs/, *.log, .venv/")
def git_ignore(ctx: Ctx) -> R:
    lines = {ln.strip() for ln in ctx.read_repo(".gitignore").splitlines()}
    need = [".env", ".env.*", "data/", "logs/", "*.log", ".venv/"]
    miss = [n for n in need if n not in lines]
    return R(FAIL, f"thiếu {', '.join(miss)}") if miss else R(PASS, "đủ " + ", ".join(need))


# ================================================================== DOC
CRITERIA_DOC = "docs/ops/OPS-CRITERIA.md"


def doc_ids(text: str) -> list[str]:
    """ID tiêu chí trong cột đầu của các bảng tiêu chí."""
    return re.findall(r"^\|\s*(OPS-[A-Z]+-\d+)\s*\|", text, re.M)


@check("OPS-DOC-1", MUST, "Có tài liệu tiêu chí ops và ID khớp với scripts/ops-check.py")
def doc_criteria(ctx: Ctx) -> R:
    txt = ctx.read_repo(CRITERIA_DOC)
    if not txt:
        return R(FAIL, f"không có {CRITERIA_DOC}")
    ids = doc_ids(txt)
    mine = {c.id for c in CHECKS}
    dup = sorted({i for i in ids if ids.count(i) > 1})
    a, b = sorted(mine - set(ids)), sorted(set(ids) - mine)
    if dup or a or b:
        return R(FAIL, f"lệch: thiếu trong tài liệu {a}, thiếu trong checker {b}, trùng {dup}")
    return R(PASS, f"{len(ids)} tiêu chí khớp giữa tài liệu và checker")


@check("OPS-DOC-2", MUST, "Runbook release/rollback nhóm gateway (CLAUDE.md + scripts/README.md)")
def doc_release_runbook(ctx: Ctx) -> R:
    miss = []
    if "release-prod.sh rollback --yes" not in ctx.read_repo("CLAUDE.md"):
        miss.append("CLAUDE.md không còn lệnh rollback")
    if "release-prod.sh" not in ctx.read_repo("scripts/README.md"):
        miss.append("scripts/README.md không nhắc release-prod.sh")
    return R(FAIL, "; ".join(miss)) if miss else R(PASS, "CLAUDE.md có quy trình release/rollback; scripts/README.md có release-prod.sh")


def service_runbooks(text: str) -> list[tuple[str, list[str], bool]]:
    """(container, [link], thiếu?) từ bảng service của tài liệu tiêu chí."""
    rows = []
    for line in text.splitlines():
        m = re.match(r"^\|\s*`(secai-[^`]+)`", line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rb = cells[-1]
        rows.append((m.group(1), re.findall(r"\]\(([^)#]+)", rb), "chưa có" in rb.lower()))
    return rows


@check("OPS-DOC-3", MUST, "Mỗi service trong phạm vi có runbook release/rollback được liên kết trong bảng service")
def doc_service_runbooks(ctx: Ctx) -> R:
    txt = ctx.read_repo(CRITERIA_DOC)
    rows = service_runbooks(txt)
    if not rows:
        return R(FAIL, "tài liệu tiêu chí không có bảng service")
    base = (ctx.cfg.repo / CRITERIA_DOC).parent
    bad = []
    for name, links, missing in rows:
        if missing or not links:
            bad.append(f"{name} (chưa có)")
        bad += [f"{name} (link hỏng {ln})" for ln in links if not (base / ln).resolve().exists()]
    if bad:
        return R(FAIL, short(bad), "viết runbook ngắn: tạo lại container, release, rollback, nơi để profile")
    return R(PASS, f"{len(rows)} service đều có runbook")


@check("OPS-DOC-4", MUST, "Có runbook khôi phục dữ liệu prod từ backup (docs/ops/RESTORE.md)")
def doc_restore(ctx: Ctx) -> R:
    if (ctx.cfg.repo / "docs/ops/RESTORE.md").is_file():
        return R(PASS, "có docs/ops/RESTORE.md")
    return R(FAIL, "chưa có docs/ops/RESTORE.md (stack/restore-data.sh chỉ dựng máy mới từ gói, không phải khôi phục backup)",
             "viết RESTORE.md: Qdrant snapshot recover, neo4j-admin load, sqlite .restore, thứ tự dừng/khởi động")


@check("OPS-DOC-5", SHOULD, "scripts/README.md nhắc mọi scripts/*.sh và ops-check.py")
def doc_scripts_readme(ctx: Ctx) -> R:
    txt = ctx.read_repo("scripts/README.md")
    names = sorted(p.name for p in (ctx.cfg.repo / "scripts").glob("*.sh")) + ["ops-check.py"]
    miss = [n for n in names if n not in txt]
    return R(FAIL, f"chưa nhắc: {', '.join(miss)}") if miss else R(PASS, f"nhắc đủ {len(names)} script")


@check("OPS-DOC-6", SHOULD, "Baseline ops đo lại trong 31 ngày gần nhất")
def doc_baseline(ctx: Ctx) -> R:
    dates = []
    for f in ctx.cfg.repo.glob("docs/archive/test-results/ops-baseline-*.md"):
        m = re.search(r"(\d{8})", f.name)
        if m:
            dates.append(dt.datetime.strptime(m.group(1), "%Y%m%d").date())
    if dates and (ctx.now.date() - max(dates)).days <= 31:
        return R(PASS, f"baseline gần nhất {max(dates)}")
    return R(FAIL, f"baseline gần nhất {max(dates) if dates else 'không có'}", "chạy ops-check, ghi docs/archive/test-results/ops-baseline-YYYYMMDD.md")


# ------------------------------------------------------------------ chạy + in
@dataclasses.dataclass
class Result:
    check: Check
    r: R


def evaluate(ctx: Ctx, groups: set[str] | None = None) -> list[Result]:
    out = []
    for ck in CHECKS:
        if groups and ck.group not in groups:
            continue
        if ck.fn is None:
            out.append(Result(ck, R(SKIP, "kiểm tay: " + ck.manual)))
            continue
        try:
            r = ck.fn(ctx)
        except ReadOnlyViolation:
            raise
        except Unavailable as e:
            r = R(SKIP, f"không đo được: {e}")
        except Exception as e:  # lỗi của checker: hiện ra, không giả là PASS/FAIL
            r = R(WARN, f"lỗi checker khi đo: {type(e).__name__}: {e}")
        out.append(Result(ck, r))
    return out


def summarize(results: list[Result]) -> dict[str, dict]:
    s: dict[str, dict] = {}
    for res in results:
        g = s.setdefault(res.check.group, {"title": GROUPS[res.check.group], PASS: 0, FAIL: 0, WARN: 0, SKIP: 0,
                                           "fail_must": 0})
        g[res.r.status] += 1
        if res.r.status == FAIL and res.check.level == MUST:
            g["fail_must"] += 1
    for g in s.values():
        n = g[PASS] + g[FAIL] + g[WARN]
        g["score"] = round(100 * (g[PASS] + 0.5 * g[WARN]) / n) if n else None
    return s


def exit_code(results: list[Result]) -> int:
    return 1 if any(r.r.status == FAIL and r.check.level == MUST for r in results) else 0


def render_text(results: list[Result], summary: dict, ctx: Ctx) -> str:
    lines = [f"ops-check (chỉ đọc) — {ctx.now:%Y-%m-%d %H:%M %z} — repo {ctx.cfg.repo}", ""]
    cur = None
    for res in results:
        if res.check.group != cur:
            cur = res.check.group
            lines += ["", f"[{cur}] {GROUPS[cur]}"]
        lines.append(f"  {res.r.status:<4}  {res.check.id:<11} {res.check.level:<8}  {res.check.title}")
        lines.append(f"        {res.r.evidence}")
        if res.r.fix and res.r.status in (FAIL, WARN):
            lines.append(f"        sửa: {res.r.fix}")
    lines += ["", "Tổng kết theo nhóm (điểm = (PASS + 0.5·WARN) / số tiêu chí đo được):"]
    for g, v in summary.items():
        sc = "—" if v["score"] is None else f"{v['score']}%"
        lines.append(f"  {g:<4} {v['title']:<30} PASS {v[PASS]:>2}  FAIL {v[FAIL]:>2}  WARN {v[WARN]:>2}  SKIP {v[SKIP]:>2}  điểm {sc}")
    fm = sum(v["fail_must"] for v in summary.values())
    lines.append("")
    lines.append(f"Kết luận: {fm} FAIL ở tiêu chí bắt buộc → mã thoát {1 if fm else 0}")
    return ctx.scrub("\n".join(lines))


def render_json(results: list[Result], summary: dict, ctx: Ctx) -> str:
    doc = {
        "generated_at": ctx.now.isoformat(timespec="seconds"),
        "repo": str(ctx.cfg.repo),
        "results": [{"id": r.check.id, "group": r.check.group, "level": r.check.level, "title": r.check.title,
                     "status": r.r.status, "evidence": r.r.evidence, "fix": r.r.fix} for r in results],
        "summary": summary,
        "exit_code": exit_code(results),
    }
    return ctx.scrub(json.dumps(doc, ensure_ascii=False, indent=2))


def gh_repo_from_remote(runner: Runner, repo: Path) -> str | None:
    p = runner.run(["git", "--no-optional-locks", "-C", str(repo), "remote", "get-url", "origin"])
    m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?\s*$", p.out.strip()) if p.rc == 0 else None
    return m.group(1) if m else None


def build_config(a: argparse.Namespace, runner: Runner) -> Config:
    repo = Path(a.repo).resolve()
    backup = a.backup_dir or os.environ.get("SECAI_BACKUP_DIR") or None
    return Config(
        repo=repo,
        docker_dir=Path(a.docker_dir or os.environ.get("SECAI_DOCKER_DIR") or "/home/ict/AIEngines/secai/secai-docker"),
        releases_dir=Path(a.releases_dir or os.environ.get("SECAI_RELEASES_DIR") or os.path.expanduser("~/secai-releases")),
        data_root=Path(a.data_root or os.environ.get("SECAI_DATA_ROOT") or "/home/ict/AIEngines/secai/secai-data"),
        backup_dir=Path(backup) if backup else None,
        gh_repo=a.gh_repo or (None if a.no_gh else gh_repo_from_remote(runner, repo)),
        use_gh=not a.no_gh,
        cron_globs=a.cron_glob or ["~/Downloads/CVE-DB/*"],
    )


def main(argv: list[str] | None = None, runner: Runner | None = None, now: dt.datetime | None = None,
         out=None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="ops-check.py", description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="in JSON")
    ap.add_argument("--group", help="chỉ các nhóm, vd VER,SEC")
    ap.add_argument("--list", action="store_true", help="in danh mục tiêu chí rồi thoát")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--docker-dir")
    ap.add_argument("--releases-dir")
    ap.add_argument("--data-root")
    ap.add_argument("--backup-dir", help="đích backup (mặc định $SECAI_BACKUP_DIR); mỗi kho một thư mục con")
    ap.add_argument("--gh-repo", help="owner/repo trên GitHub (mặc định lấy từ origin)")
    ap.add_argument("--no-gh", action="store_true", help="không gọi gh (tiêu chí GitHub thành SKIP)")
    ap.add_argument("--cron-glob", action="append", help="file script cron cần quét khoá (lặp được)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0) and 2
    if a.list:
        for c in CHECKS:
            print(f"{c.id:<11} {c.level:<8} {'tay' if c.fn is None else 'tự động':<7} {c.title}", file=out)
        return 0
    groups = None
    if a.group:
        groups = {g.strip().upper() for g in a.group.split(",") if g.strip()}
        bad = groups - set(GROUPS)
        if bad:
            print(f"ops-check: nhóm không có: {', '.join(sorted(bad))} (có: {', '.join(GROUPS)})", file=sys.stderr)
            return 2
    runner = runner or Runner()
    ctx = Ctx(build_config(a, runner), runner, now or dt.datetime.now().astimezone())
    results = evaluate(ctx, groups)
    summary = summarize(results)
    print(render_json(results, summary, ctx) if a.json else render_text(results, summary, ctx), file=out)
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
