#!/usr/bin/env python3
"""ops-check: chấm phần vận hành của secai-agent-gateway theo docs/criteria/ops.md. CHỈ ĐỌC.

    python3 -I scripts/ops-check.py                 # in từng tiêu chí PASS/FAIL/WARN/SKIP + bằng chứng
    python3 -I scripts/ops-check.py --json          # cùng kết quả dạng JSON
    python3 -I scripts/ops-check.py --group VER,SEC # chỉ vài nhóm
    python3 -I scripts/ops-check.py --list          # in danh mục tiêu chí, không đo gì

Mã thoát: 1 khi có FAIL ở tiêu chí bắt buộc; 3 khi không có FAIL nhưng có tiêu chí bắt buộc tự động
không đo được (docker/systemctl lỗi, lỗi checker; thiếu GitHub không tính); 2 khi sai tham số; 0 còn lại.

Checker không ghi, không restart, không đổi gì. `assert_read_only` kiểm MỌI lệnh trước khi chạy theo
danh sách trắng đúng dạng (cờ lạ, kể cả dạng dính liền như -iXDELETE hay --output=, đều bị từ chối):
  docker ps -a --no-trunc --format '{{json .}}' | inspect <id>… | image inspect <ref>…
         | images --no-trunc <repo>|'secai-*' --format '{{json .}}' | system df --format '{{json .}}'
  gh api repos/<owner>/<repo>[/…]          (GET, không cờ nào khác)
  df -P <path>, stat, systemctl [--user] show <unit>… -p <Prop> | list-units | list-timers
  git --no-optional-locks -C <dir> rev-parse|merge-base --is-ancestor|cat-file -e|ls-files -z|
      diff --quiet --no-ext-diff --no-textconv|status --porcelain|worktree list|branch (liệt kê)|
      remote get-url origin|rev-list --count     (không fetch nên origin/main là bản fetch gần nhất)
  tailscale serve status --json            (đọc cấu hình serve/funnel, không đổi gì)
Ngoài ra chỉ đọc file thường (Dockerfile, compose, state.env, pip-freeze.diff, /proc/net/tcp…) và os.stat.

Secret: chỉ in tên biến, đường dẫn, quyền file. Giá trị đọc được trong bộ nhớ (env container,
compose, script cron) được ghi vào một tập và xoá khỏi mọi dòng output trước khi in.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
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
FIRST_PARTY_IMAGES = "secai-*"  # pattern repo cho `docker images` (OPS-RES-4)

# Script đổi trạng thái prod/máy: phải có `set -e` ngoài -u + pipefail (OPS-CI-6).
MUTATING_SCRIPTS = (
    "scripts/release-prod.sh", "scripts/wt-prune.sh", "scripts/wt-rm.sh", "scripts/wt-new.sh",
    "scripts/dev.sh", "scripts/install-wt-prune-timer.sh", "scripts/install-backup-timer.sh",
    "stack/bootstrap.sh", "stack/pack.sh",
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

# File tracked chứa chuỗi giống secret GIẢ có chủ đích (dữ liệu test/benchmark của bộ sanitize, demo
# redaction). Thư mục (giá trị None) được miễn cả thư mục; file thì chỉ miễn đúng số match đã khai báo
# cho từng loại, thêm một match mới vào chính file đó vẫn bị báo. Chưa xác minh từng giá trị là giả;
# lịch sử Git cần quét riêng bằng gitleaks (kiểm tay, OPS-SEC-6).
FAKE_SECRET_FIXTURES: dict[str, dict[str, int] | None] = {
    "evals/sanitize/": None,
    "demo/kpi-studio/kpi-studio-standalone.html": {"github_token": 4, "openai_like": 2, "aws_access_key": 2,
                                                   "slack_token": 10, "google_api_key": 6},
    "demo/showcase/tabs/sanitize-demo.js": {"github_token": 1, "aws_access_key": 1},
    "evidence/dong-534.md": {"aws_access_key": 2},
    "evidence/dong-554.md": {"aws_access_key": 1},
    "kpi_bench/sanitizer/detector.py": {"aws_access_key": 1},
    "pentestcode/packages/llm/test/provider/bedrock-converse.test.ts": {"aws_access_key": 1},
    "scripts/nghiem-thu-rows-ai.json": {"aws_access_key": 1},
    "tests/test_stack_scripts.py": {"private_key": 1},
}
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
# NAME=value / NAME: value / "NAME=value" (dump docker inspect). Chỉ tên được in ra; tên có phải secret
# không do is_secret_name quyết định (cùng bộ từ vựng với OPS-SEC-4). Lookahead độ rộng 0 để key
# `environment:` không nuốt key kế tiếp; `[ \t]*` để match không vượt sang dòng sau (`if X_KEY:\n ...`).
SECRET_ASSIGN_RE = re.compile(
    r"""(?=(?P<q1>["']?)\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b["']?[ \t]*[:=][ \t]*(?P<q2>["']?)(?P<val>[^"'\s,}\]]+))""")
# Tên khớp SECRET_NAME_RE nhưng không phải secret: đường dẫn/tên file, fingerprint GPG công khai của image python.
NOT_SECRET_NAME_RE = re.compile(r"(_FILE|_PATH|_DIR|_FILES)$", re.I)
# `.get("X_PASSWORD", "literal")` / `getenv("X_KEY", "literal")`: mặc định literal nằm thẳng trong script.
GET_DEFAULT_RE = re.compile(
    r"""(?:\.get|\bgetenv)\(\s*(?P<q1>["'])(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P=q1)\s*,\s*(?P<q2>["'])(?P<val>[^"'\n]*)(?P=q2)""")
# Giá trị giữ chỗ hiển nhiên, không phải secret: sk-placeholder, change-me, <mật-khẩu>, xxxx, ****.
# So với TOÀN BỘ phần literal (fullmatch): `Changeme2026!` hay `sk-live-… # placeholder` vẫn là secret.
PLACEHOLDER_RE = re.compile(r"(?i)(?:sk-)?(?:placeholder|change[-_]?me|x{4,}|\*{4,})|<[^<>]*>")
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
    """Không đo được (thiếu docker/gh/quyền...). Tiêu chí thành SKIP kèm lý do.

    soft=True: chỉ thiếu dữ liệu GitHub (gh chưa login, --no-gh, không đủ quyền đọc). Không tính vào
    mã thoát 3 vì đó là cài đặt repo, không phải trạng thái máy."""

    def __init__(self, msg: str, soft: bool = False) -> None:
        super().__init__(msg)
        self.soft = soft


# ------------------------------------------------------------------ chặn lệnh ghi
# Danh sách TRẮNG theo đúng dạng lệnh checker dùng, không phải danh sách đen theo cờ: getopt của
# systemctl/gh/git nhận giá trị dính liền (-iXDELETE), giá trị tách rời (-p show restart) và
# --output=<file>, nên mọi cờ không liệt kê ở đây đều bị từ chối.
_REF = re.compile(r"^[A-Za-z0-9_./~^{}@:-]+$")      # sha, origin/main, a..b, <sha>^{commit}
_HEX = re.compile(r"^[0-9a-f]{4,64}$")
_UNIT = re.compile(r"^[A-Za-z0-9@._:*\\-]+$")
_GH_PATH = re.compile(r"^repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(/[A-Za-z0-9_./-]*)?$")
_SYSTEMCTL_FLAGS = {"--all", "--plain", "--no-legend", "--no-pager", "--value"}


def _ref(x: str) -> bool:
    return bool(_REF.match(x)) and not x.startswith("-")


def _git_shape_ok(sub: str, r: list[str]) -> bool:
    if sub == "rev-parse":
        return r in (["--is-inside-work-tree"], ["HEAD"])
    if sub == "merge-base":
        return len(r) == 3 and r[0] == "--is-ancestor" and _ref(r[1]) and _ref(r[2])
    if sub == "cat-file":
        return len(r) == 2 and r[0] == "-e" and _ref(r[1])
    if sub == "ls-files":
        return r == ["-z"]
    if sub == "diff":
        if r[:3] != ["--quiet", "--no-ext-diff", "--no-textconv"] or "--" not in r:
            return False
        k = r.index("--")
        return all(_ref(x) for x in r[3:k]) and all(not x.startswith("-") for x in r[k + 1:])
    if sub == "status":
        return r == ["--porcelain", "--untracked-files=no"]
    if sub == "worktree":
        return r == ["list", "--porcelain"]
    if sub == "branch":
        return r in (["--show-current"], ["-r"]) or (len(r) == 3 and r[:2] == ["-r", "--merged"] and _ref(r[2]))
    if sub == "remote":
        return r == ["get-url", "origin"]
    if sub == "rev-list":
        return len(r) == 2 and r[0] == "--count" and _ref(r[1])
    return False


def assert_read_only(argv: list[str]) -> None:
    """Từ chối mọi lệnh không đúng dạng chỉ đọc checker dùng. Gọi trước MỌI subprocess."""
    if not argv:
        raise ReadOnlyViolation("lệnh rỗng")
    tool, a = os.path.basename(argv[0]), list(argv[1:])
    ok = False
    if tool == "docker":
        if a[:1] == ["ps"]:
            ok = a == ["ps", "-a", "--no-trunc", "--format", "{{json .}}"]
        elif a[:1] == ["inspect"]:
            ok = len(a) > 1 and all(_HEX.match(x) for x in a[1:])
        elif a[:2] == ["image", "inspect"]:
            ok = len(a) > 2 and all(_ref(x) for x in a[2:])
        elif a[:1] == ["images"]:
            ok = (len(a) == 5 and a[1] == "--no-trunc" and (_ref(a[2]) or a[2] == FIRST_PARTY_IMAGES)
                  and a[3:] == ["--format", "{{json .}}"])
        elif a[:2] == ["system", "df"]:
            ok = a == ["system", "df", "--format", "{{json .}}"]
    elif tool == "gh":
        ok = len(a) == 2 and a[0] == "api" and bool(_GH_PATH.match(a[1]))
    elif tool == "df":
        ok = len(a) == 2 and a[0] == "-P" and not a[1].startswith("-")
    elif tool == "stat":
        ok = bool(a) and all(not x.startswith("-") for x in a)
    elif tool == "systemctl":
        r = a[1:] if a[:1] == ["--user"] else a
        verb, rest = (r[0] if r else ""), r[1:]
        ok = verb == "show" or verb in ("list-units", "list-timers")
        i = 0
        while ok and i < len(rest):
            x = rest[i]
            if x == "-p" and i + 1 < len(rest) and re.fullmatch(r"[A-Za-z]+", rest[i + 1]):
                i += 2
                continue
            ok = x in _SYSTEMCTL_FLAGS or (not x.startswith("-") and bool(_UNIT.match(x)))
            i += 1
    elif tool == "tailscale":
        ok = a == ["serve", "status", "--json"]
    elif tool == "git":
        ok = (len(a) >= 4 and a[0] == "--no-optional-locks" and a[1] == "-C" and not a[2].startswith("-")
              and _git_shape_ok(a[3], a[4:]))
    if not ok:
        raise ReadOnlyViolation(f"lệnh không thuộc danh sách chỉ đọc: {argv[:6]}")


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


def regular_file(p: Path) -> bool:
    """File thường, không phải symlink/FIFO/socket/thiết bị (open() FIFO sẽ treo checker)."""
    try:
        return stat.S_ISREG(os.lstat(p).st_mode)
    except OSError:
        return False


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
# <kênh>-<sha>-<ngày> theo vị trí: sha toàn chữ số (~2% sha8) hay dạng 20xxxxxx vẫn là sha.
CHANNEL_SHA_DATE_TAG = re.compile(r"^[a-z][a-z0-9]*-([0-9a-f]{7,40})-(20\d{6})[a-z]?$")


def classify_first_party_tag(tag: str) -> tuple[str, str]:
    """PASS: có sha commit + ngày; WARN: chỉ ngày hoặc semver đầy đủ; FAIL: tag trôi/mô tả."""
    if not tag or tag == "latest":
        return FAIL, "latest/không tag"
    if CHANNEL_SHA_DATE_TAG.match(tag):
        return PASS, "sha+ngày"
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
    proc_net: Path = Path("/proc/net")


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
            if "no such image" in p.err.lower():
                return None
            raise Unavailable(f"docker image inspect: {p.err.strip()[:160] or p.rc}")
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
            raise Unavailable("đã tắt gh (--no-gh)", soft=True)
        if not self.cfg.gh_repo:
            raise Unavailable("không xác định được repo GitHub (origin không phải github.com)", soft=True)
        key = "gh:" + path

        def f():
            if path != "repos/{repo}":
                self.gh("repos/{repo}")  # repo không đọc được thì mọi 404 phía sau là thiếu quyền, không phải "chưa cấu hình"
            p = self.r.run(["gh", "api", path.format(repo=self.cfg.gh_repo)])
            if p.rc == 0:
                return 200, json.loads(p.out or "null")
            if "HTTP 404" in p.err and path != "repos/{repo}":
                return 404, None
            raise Unavailable(f"gh api {path.format(repo=self.cfg.gh_repo)}: {p.err.strip()[:160] or p.rc}", soft=True)
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
                    raise Unavailable(f"systemctl {' '.join(scope) or '--system'} list-units lỗi: "
                                      f"{p.err.strip()[:120] or p.rc} (thiếu XDG_RUNTIME_DIR/bus của user?)")
                names = [ln.split()[0] for ln in p.out.splitlines() if ln.strip()]
                if not names:
                    continue
                q = self.r.run(["systemctl", *scope, "show", *names, "--no-pager", "-p", "Id", "-p", "ActiveState",
                                "-p", "SubState", "-p", "NRestarts", "-p", "Result", "-p", "ExecStart"])
                if q.rc != 0:
                    raise Unavailable(f"systemctl show lỗi: {q.err.strip()[:120] or q.rc}")
                for block in q.out.split("\n\n"):
                    props = dict(ln.split("=", 1) for ln in block.splitlines() if "=" in ln)
                    if props.get("Id"):
                        props["scope"] = "user" if scope else "system"
                        out.append(props)
            return out
        return self._memo("units", f)

    def timers(self) -> list[str]:
        def f():
            names = []
            for scope in (["--user"], []):
                p = self.r.run(["systemctl", *scope, "list-timers", "--all", "--plain", "--no-legend", "--no-pager"])
                if p.rc != 0:
                    raise Unavailable(f"systemctl {' '.join(scope) or '--system'} list-timers lỗi: {p.err.strip()[:120] or p.rc}")
                names += re.findall(r"(\S+\.timer)", p.out)
            return names
        return self._memo("timers", f)

    # ---- tailscale
    def tailscale_serve(self) -> dict | None:
        """Cấu hình `tailscale serve` (ServeConfig JSON). None khi máy không có tailscale/tailscaled."""
        def f():
            p = self.r.run(["tailscale", "serve", "status", "--json"], timeout=20)
            if p.rc == 127:
                return None
            if p.rc != 0:
                if re.search(r"(?i)tailscaled|not running|failed to connect|connection refused|no such file", p.err):
                    return None
                raise Unavailable(f"tailscale serve status: {p.err.strip()[:120] or p.rc}")
            try:
                cfg = json.loads(p.out or "{}")
            except json.JSONDecodeError as e:
                raise Unavailable(f"tailscale serve status: JSON lỗi ({e.msg})") from None
            return cfg if isinstance(cfg, dict) else {}
        return self._memo("ts_serve", f)

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
        return p.read_text(errors="replace") if regular_file(p) else ""


def in_scope(name: str) -> bool:
    return name in APP_CONTAINERS or name in DATASTORES or bool(POOL_RE.match(name))


def first_party(ref: str) -> bool:
    return image_repo_tag(ref)[0].rsplit("/", 1)[-1].startswith("secai-")


def group_pool(names: list[str]) -> list[str]:
    """Gom container pool thành một mục; nhận cả tên trần lẫn tên kèm hậu tố "<tên> (…)"."""
    def is_pool(n: str) -> bool:
        return bool(POOL_RE.match(n.split(" ", 1)[0]))
    pool = [n for n in names if is_pool(n)]
    rest = [n for n in names if not is_pool(n)]
    tails = {n.split(" ", 1)[1] if " " in n else "" for n in pool}
    tail = f" {next(iter(tails))}" if len(tails) == 1 and "" not in tails else ""  # cả pool cùng lý do thì giữ lý do
    return rest + ([f"pool pentestcode ({len(pool)}){tail}"] if pool else [])


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
    unmeasured: bool = False  # không đo được (hết phần) dù checker chạy được: tính vào mã thoát 3


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

def dockerfile(ctx: Ctx) -> str:
    """Dockerfile với dòng nối bằng `\\` đã gộp, để regex thấy cả lệnh RUN nhiều dòng."""
    return re.sub(r"\\\n\s*", " ", ctx.read_repo("Dockerfile"))


def installs_from_lock(df: str) -> bool:
    """Dạng được chấp nhận (khớp ngưỡng OPS-BLD-1 trong tài liệu)."""
    if not re.search(r"^\s*COPY\b[^\n]*\buv\.lock\b", df, re.M):
        return False
    return bool(re.search(r"\buv\s+sync\b[^\n]*--(frozen|locked)\b", df)
                or (re.search(r"\buv\s+export\b[^\n]*--(frozen|locked)\b", df)
                    and re.search(r"\buv\s+pip\s+sync\b|\bpip\s+install\b[^\n]*--require-hashes", df)))


@check("OPS-BLD-1", MUST, "Image cài dependency từ uv.lock, không resolve lại từ pyproject.toml")
def bld_lock(ctx: Ctx) -> R:
    df = dockerfile(ctx)
    if not df:
        raise Unavailable("không có Dockerfile trong repo")
    if installs_from_lock(df):
        return R(PASS, "Dockerfile COPY uv.lock và cài theo lock")
    return R(FAIL, "Dockerfile chỉ COPY pyproject.toml rồi pip install theo [project].dependencies; uv.lock bị bỏ qua",
             "deps stage: COPY pyproject.toml uv.lock, cài uv ghim version rồi `uv sync --locked --no-dev --extra redis --no-install-project`")


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
        p = ctx.git("diff", "--quiet", "--no-ext-diff", "--no-textconv", prev.group(1), new.group(1), "--", "uv.lock", check=False)
        lock_changed = {0: False, 1: True}.get(p.rc)
    ev = f"{rel.name}: {len(changed)} package đổi version ({short(changed)})"
    if lock_changed is False:
        return R(FAIL, ev + "; uv.lock KHÔNG đổi giữa hai commit → trôi do build không dùng lock",
                 "sửa OPS-BLD-1; tới lúc đó đọc pip-freeze.diff trước mỗi `release-prod.sh run`")
    return R(WARN, ev + ("; uv.lock có đổi, đối chiếu tay" if lock_changed else "; không so được uv.lock"))


@check("OPS-BLD-3", SHOULD, "Base image trong Dockerfile ghim digest")
def bld_digest(ctx: Ctx) -> R:
    bases = re.findall(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", dockerfile(ctx), re.M | re.I)
    if not bases:
        raise Unavailable("không có Dockerfile")
    stages = {m.lower() for m in re.findall(r"^\s*FROM\s+\S+\s+AS\s+(\S+)", dockerfile(ctx), re.M | re.I)}
    loose = [b for b in bases if "@sha256:" not in b and b.lower() not in stages | {"scratch"}]
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

def rolled_back(ctx: Ctx, st: dict[str, str]) -> bool:
    """Prod gateway đang chạy PREV_TAG của release gần nhất (đã rollback tay hoặc tự rollback)."""
    try:
        c = ctx.containers().get("secai-docker-gateway")
    except Unavailable:
        return False
    return bool(c) and bool(st.get("PREV_TAG")) and c["Config"]["Image"] == st.get("PREV_TAG")


@check("OPS-REL-1", MUST, "Release gần nhất có đủ hồ sơ (state.env, RELEASE-NOTES, pytest.log, health trước/sau, compose bak)")
def rel_record(ctx: Ctx) -> R:
    rel, st = ctx.latest_release()
    need = ["state.env", "RELEASE-NOTES.md", "pytest.log", "health-8131-before.json", "health-8131-after.json"]
    miss = [n for n in need if not (rel / n).is_file()]
    if not st.get("COMPOSE_BAK") or not Path(st["COMPOSE_BAK"]).is_file():
        miss.append("COMPOSE_BAK")
    if miss and rolled_back(ctx, st):
        return R(WARN, f"{rel.name} thiếu {', '.join(miss)}; prod đang chạy PREV_TAG {st['PREV_TAG']} (release này đã bị rollback)",
                 "ghi chú rollback vào release dir; release lại khi đã sửa")
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
    return R(PASS, f"có đủ {len(REQUIRED_RELEASE_TESTS)} test bắt buộc (checker chỉ kiểm có test; "
                   "kết quả: scripts/dev.sh test tests/test_release_prod.py)")


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
        return R(FAIL, f"{len(bad)} container: {short(group_pool(bad))}",
                 "đưa service vào compose/profile có trong release dir; ghi lệnh tạo lại vào runbook")
    return R(PASS, f"{len(ctx.containers())} container đều có compose project và file cấu hình còn")


@check("OPS-REL-6", MUST, "Cấu hình deploy prod (compose secai-docker) được quản lý phiên bản (Git private)")
def rel_compose_vcs(ctx: Ctx) -> R:
    d = ctx.cfg.docker_dir
    if not d.is_dir():
        raise Unavailable(f"không có {d}")
    p = ctx.git("rev-parse", "--is-inside-work-tree", repo=d, check=False)
    if p.rc != 0 or p.out.strip() != "true":
        return R(FAIL, f"{d}/docker-compose.yml nằm ngoài Git (lịch sử chỉ là file .bak)",
                 "git init repo private cho secai-docker; .gitignore chặn .env* và *.bak* TRƯỚC lần add đầu; commit mỗi lần đổi compose")
    tracked = [f for f in ctx.git("ls-files", "-z", repo=d).out.split("\0") if f]  # đường dẫn tính từ d
    leaked = [f for f in tracked if re.search(r"(^|/)\.env($|\.)", f) and not f.endswith(".example")]
    if leaked:
        return R(FAIL, f"{d} trong Git nhưng track file env: {short(leaked, 3)}", "git rm --cached; xoay secret đã commit")
    if "docker-compose.yml" not in tracked:
        return R(FAIL, f"{d} trong Git nhưng docker-compose.yml chưa được track/commit (không có lịch sử)",
                 "git add docker-compose.yml && git commit (sau khi .gitignore đã chặn .env* và *.bak*)")
    # porcelain luôn in đường dẫn tính từ gốc repo, có thể khác d
    top = next((x for x in (d, *d.parents) if (x / ".git").exists()), d)
    rel = os.path.relpath(d / "docker-compose.yml", top)
    dirty = [ln[3:] for ln in ctx.git("status", "--porcelain", "--untracked-files=no", repo=d).out.splitlines() if ln.strip()]
    if rel in dirty:
        return R(WARN, f"{d}/docker-compose.yml đã track nhưng đang có sửa chưa commit",
                 "commit thay đổi compose (kèm lý do) ngay sau mỗi lần sửa")
    return R(PASS, f"{d} nằm trong Git, docker-compose.yml đã commit và sạch, không track .env*")


@check("OPS-REL-7", SHOULD, "Thư mục compose prod không tích file sao lưu (≤ 3 file docker-compose.yml.bak*)")
def rel_bak_count(ctx: Ctx) -> R:
    d = ctx.cfg.docker_dir
    if not d.is_dir():
        raise Unavailable(f"không có {d}")
    baks = sorted(p.name for p in d.glob("docker-compose.yml.bak*"))
    if len(baks) > 3:
        return R(FAIL, f"{len(baks)} file .bak cạnh compose prod", "chuyển vào release dir tương ứng hoặc Git; giữ ≤ 3")
    return R(PASS, f"{len(baks)} file .bak")


def checkout_mounts(ctx: Ctx) -> list[tuple[str, Path, str]]:
    """(container, gốc worktree, đường dẫn tương đối) của mọi bind mount lấy từ một worktree của repo này."""
    roots = sorted((Path(w["worktree"]).resolve() for w in ctx.worktrees()), key=lambda p: -len(str(p)))
    out = []
    for name, c in sorted(ctx.containers().items()):
        for m in c.get("Mounts") or []:
            if m.get("Type") != "bind":
                continue
            src = str(Path(m["Source"]))
            root = next((r for r in roots if src == str(r) or src.startswith(str(r) + "/")), None)
            if root:
                out.append((name, root, src[len(str(root)):].lstrip("/") or "."))
    return out


def worktree_info(ctx: Ctx, root: Path) -> dict:
    return next((w for w in ctx.worktrees() if Path(w["worktree"]).resolve() == root), {})


def is_deploy_main(root: Path) -> bool:
    """Worktree "main" cố định để mount cho prod (CLAUDE.md): tên kết thúc -deploy-main."""
    return root.name.endswith("-deploy-main")


@check("OPS-REL-8", MUST, "Không bind-mount sống từ Git checkout vào container prod (chỉ worktree *-deploy-main hoặc checkout chính ở main; không bao giờ src/)")
def rel_live_mounts(ctx: Ctx) -> R:
    main_root = ctx.main_checkout().resolve()
    fails, warns, ok = [], [], []
    for name, root, rel in checkout_mounts(ctx):
        who = "pool pentestcode" if POOL_RE.match(name) else name
        w = worktree_info(ctx, root)
        branch = (w.get("branch") or "").replace("refs/heads/", "") or "(detached)"
        if rel == "src" or rel.startswith("src/"):
            fails.append(f"{who}:{rel} (mount src sống)")
        elif is_deploy_main(root):
            on_main = ctx.git("merge-base", "--is-ancestor", str(w.get("HEAD", "HEAD")), "origin/main",
                              repo=main_root, check=False).rc == 0
            (ok if on_main else fails).append(f"{who}:{rel} ({root.name}" + ("" if on_main else ", HEAD ngoài origin/main") + ")")
        elif root == main_root and branch == "main":
            warns.append((rel, who))
        else:
            fails.append(f"{who}:{rel} (checkout {root.name}@{branch})")
    if fails:
        return R(FAIL, "mount từ checkout không được phép: " + short(sorted(set(fails))),
                 "mount từ worktree *-deploy-main ở origin/main, hoặc đóng gói vào image")
    if warns:
        by_rel: dict[str, set[str]] = {}
        for rel, who in warns:
            by_rel.setdefault(rel, set()).add(who)
        desc = [f"{rel} → {', '.join(sorted(w))}" for rel, w in sorted(by_rel.items())]
        return R(WARN, f"{len(warns)} mount sống từ checkout chính {main_root} (đang ở main, nơi agent có thể đổi nhánh): {short(desc, 6)}",
                 "chuyển nguồn mount sang worktree *-deploy-main cố định, hoặc đóng gói demo/evidence/helm/pentestcode vào image")
    return R(PASS, f"{len(ok)} mount từ worktree deploy-main ở origin/main" if ok else "không mount từ Git checkout")


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
    for _, c in sorted(ctx.containers().items()):
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
        _, tag, digest = image_repo_tag(ref)
        st, why = classify_third_party_tag(tag, digest)
        res[st].append(f"{ref} ({why})")
    st = worst([s for s in res if res[s]])
    ev = "; ".join(f"{s}: {short(v, 4)}" for s, v in res.items() if v and s != PASS) or f"{len(res[PASS])} image đạt"
    return R(st, ev, "" if st == PASS else "ghim redis:7.x.y-alpine, neo4j:5.20.z-community (hoặc @sha256) trong compose prod")


def commit_known(ctx: Ctx, sha: str) -> bool:
    key = "commit:" + sha
    if key not in ctx._c:
        ok = bool(re.fullmatch(r"[0-9a-f]{7,40}", sha))
        ctx._c[key] = ok and ctx.git("cat-file", "-e", f"{sha}^{{commit}}", check=False).rc == 0
    return ctx._c[key]


@check("OPS-VER-4", MUST, "Image first-party có label org.opencontainers.image.revision trỏ tới commit của repo này")
def ver_revision_label(ctx: Ctx) -> R:
    miss, foreign, unknown, ok = [], [], [], 0
    ours = ("secai",) + ((ctx.cfg.gh_repo.split("/")[-1],) if ctx.cfg.gh_repo else ())
    for name, c in sorted(ctx.containers().items()):
        ref = c["Config"]["Image"]
        im = ctx.images([c["Image"]]).get(c["Image"]) or {}
        tags = im.get("RepoTags") or []
        if not (first_party(ref) or any(first_party(t) for t in tags)):
            continue
        labels = {**((im.get("Config") or {}).get("Labels") or {}), **((c.get("Config") or {}).get("Labels") or {})}
        rev = labels.get("org.opencontainers.image.revision", "")
        src = labels.get("org.opencontainers.image.source", "")
        if not rev:
            miss.append(name)
        elif src and not any(o in src for o in ours):
            foreign.append(f"{name} (kế thừa từ {src})")
        elif commit_known(ctx, rev):
            ok += 1
        else:
            unknown.append(f"{name} ({rev[:12]} không có trong repo)")
    parts = []
    if miss:
        parts.append(f"thiếu label: {len(miss)} container: {short(group_pool(miss))}")
    if foreign:
        parts.append(f"label của base image: {len(foreign)} container: {short(group_pool(foreign), 3)}")
    if unknown:
        parts.append(f"không truy được: {short(unknown, 4)}")
    fix = ("stage cuối của Dockerfile image đó: ARG GIT_SHA rồi LABEL org.opencontainers.image.revision=$GIT_SHA, "
           "build với --build-arg GIT_SHA (image gateway: release lại bằng release-prod.sh); label kế thừa từ "
           "base image của repo khác thì thêm org.opencontainers.image.source=<repo này>")
    if miss or foreign:
        return R(FAIL, "; ".join(parts), fix)
    if unknown:
        return R(WARN, "; ".join(parts) + f"; {ok} đạt", "push commit đã build image, hoặc build lại từ commit trên origin/main")
    return R(PASS, f"{ok} container first-party có label revision là commit của repo")


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
    tag = cs["secai-docker-gateway"]["Config"]["Image"]
    m = re.fullmatch(rf"{GATEWAY_IMAGE}:main-([0-9a-f]{{8}})-(\d{{8}})", tag)
    if not m:
        return R(FAIL, f"tag prod {tag} không theo main-<sha8>-<ngày>")
    sha, problems, note = m.group(1), [], ""
    if ctx.git("merge-base", "--is-ancestor", sha, "origin/main", check=False).rc != 0:
        problems.append(f"{sha} không phải tổ tiên origin/main (bản fetch local)")
    try:
        _, st = ctx.latest_release()
        if st.get("NEW_TAG") != tag:
            if rolled_back(ctx, st):
                note = f"prod đã rollback về PREV_TAG; NEW_TAG trong state.env là {st.get('NEW_TAG')}"
            else:
                problems.append(f"state.env NEW_TAG={st.get('NEW_TAG')}")
    except Unavailable as e:
        problems.append(str(e))
    for n in GATEWAY_TRIO:
        if n in cs:
            g = ctx.env(cs[n]).get("GIT_SHA")
            if g != sha:
                problems.append(f"{n} GIT_SHA={g if g is not None else '(không có)'}")
    if problems:
        return R(FAIL, f"{tag}: " + "; ".join(problems))
    if note:
        return R(WARN, f"{tag}: {note}")
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
    wf = [Path(w) for w in sorted(glob.glob(str(ctx.cfg.repo / ".github" / "workflows" / "*.y*ml"))) if regular_file(Path(w))]
    good = [w for w in wf if re.search(r"\bpytest\b", w.read_text(errors="replace"))
            and re.search(r"\bpull_request\b", w.read_text(errors="replace"))]
    remote, note = None, ""
    try:
        code, data = ctx.gh("repos/{repo}/actions/workflows")
        if code == 200:
            remote = {Path(x.get("path", "")).name for x in (data or {}).get("workflows", []) if x.get("state") == "active"}
    except Unavailable as e:
        note = f"; GitHub: {e}"
    ev = f"repo: {len(wf)} workflow, {len(good)} chạy pytest trên pull_request" + (
        f"; GitHub: {len(remote)} workflow active" if remote is not None else note)
    if good and (remote is None or any(w.name in remote for w in good)):
        return R(PASS, ev)
    return R(FAIL, ev, "thêm .github/workflows/ci.yml: on pull_request, uv sync --frozen + pytest -q (job không cần secret)")


@check("OPS-CI-2", MUST, "main có branch protection/ruleset: bắt PR, bắt check CI xanh, cấm force-push")
def ci_protection(ctx: Ctx) -> R:
    _, repo = ctx.gh("repos/{repo}")
    code, prot = ctx.gh("repos/{repo}/branches/main/protection")
    _, rules = ctx.gh("repos/{repo}/rules/branches/main")
    kinds = {r.get("type") for r in (rules or [])} if isinstance(rules, list) else set()
    if code == 200 and isinstance(prot, dict):
        rsc = prot.get("required_status_checks") or {}
        need = []  # (thiếu gì ở classic protection, loại rule tương ứng trong ruleset)
        if not (rsc.get("contexts") or rsc.get("checks")):
            need.append(("status check bắt buộc (contexts/checks rỗng)", "required_status_checks"))
        if not prot.get("required_pull_request_reviews"):
            need.append(("bắt PR", "pull_request"))
        if (prot.get("allow_force_pushes") or {}).get("enabled"):
            need.append(("cấm force-push", "non_fast_forward"))
        missing = [msg for msg, kind in need if kind not in kinds]  # ruleset bù được thì không thiếu
        if not missing:
            via = f" (ruleset bù: {', '.join(k for _, k in need)})" if need else ""
            return R(PASS, "branch protection/ruleset bắt PR + status check + cấm force-push" + via)
        return R(FAIL, f"branch protection thiếu (ruleset cũng không có): {', '.join(missing)}")
    if {"pull_request", "required_status_checks", "non_fast_forward"} <= kinds:
        return R(PASS, "ruleset bắt PR + status check + cấm force-push")
    if code == 404 and not ((repo or {}).get("permissions") or {}).get("admin"):
        _, br = ctx.gh("repos/{repo}/branches/main")
        if (br or {}).get("protected"):
            raise Unavailable("main có protection nhưng tài khoản gh không đủ quyền admin để đọc chi tiết", soft=True)
    return R(FAIL, f"main không có branch protection; ruleset áp lên main: {sorted(kinds) or 'không'}",
             "Settings → Rules: bắt PR, bắt check CI, chặn force-push/xoá cho main (sau khi có CI)")


@check("OPS-CI-3", SHOULD, "Repo bật delete_branch_on_merge")
def ci_delete_branch(ctx: Ctx) -> R:
    _, repo = ctx.gh("repos/{repo}")
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


def security_and_analysis(ctx: Ctx) -> dict:
    """Trường security_and_analysis của repo. GitHub chỉ trả cho admin/owner/security manager: thiếu
    nghĩa là token không đủ quyền đọc (SKIP mềm, không tính mã thoát 3), không phải tính năng đang tắt."""
    _, repo = ctx.gh("repos/{repo}")
    sa = (repo or {}).get("security_and_analysis")
    if not sa:
        raise Unavailable("không đọc được security_and_analysis (cần quyền admin)", soft=True)
    return sa


@check("OPS-CI-5", SHOULD, "Bật Dependabot security updates hoặc có .github/dependabot.yml")
def ci_dependabot(ctx: Ctx) -> R:
    for name in ("dependabot.yml", "dependabot.yaml"):
        if (ctx.cfg.repo / ".github" / name).is_file():
            return R(PASS, f"có .github/{name}")
    sa = security_and_analysis(ctx)
    st = (sa.get("dependabot_security_updates") or {}).get("status")
    if st == "enabled":
        return R(PASS, "dependabot_security_updates=enabled")
    return R(FAIL, f"dependabot_security_updates={st or 'không có'}; không có .github/dependabot.y(a)ml",
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
DEFAULT_BACKUP_DIR = "~/secai-backups"  # = mặc định của scripts/backup-sqlite.py (DB vào sqlite/)
BACKUP_KEYWORDS = {"qdrant": r"qdrant", "neo4j": r"neo4j", "sqlite": r"sqlite|report|review|session|gateway-db"}


@check("OPS-BAK-1", MUST, "Có lịch backup định kỳ phủ Qdrant, Neo4j và DB SQLite (report/review/session)")
def bak_schedule(ctx: Ctx) -> R:
    timers = ctx.timers()
    bk = [t for t in timers if re.search(r"backup|snapshot|dump", t, re.I)
          and re.search(r"secai|qdrant|neo4j|sqlite|report|review", t, re.I)]
    generic = [t for t in bk if re.fullmatch(r"secai-backup[\w.-]*\.timer", t)]
    uncovered = [k for k, rx in BACKUP_KEYWORDS.items() if not any(re.search(rx, t, re.I) for t in bk)]
    if bk and (generic or not uncovered):
        return R(PASS, f"timer backup: {short(bk)}" + (" (timer chung; độ phủ từng kho đo ở OPS-BAK-2)" if generic else ""))
    if bk:
        return R(FAIL, f"timer backup {short(bk)} chưa phủ: {', '.join(uncovered)}")
    return R(FAIL, f"không có systemd timer backup/snapshot/dump cho secai ({len(timers)} timer); cron: kiểm tay `crontab -l`",
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


# biến -> (mặc định trong src/secai_gateway/config.py, điều kiện bật: (biến, kiểu, mặc định))
DB_VARS = {
    "GATEWAY_DB_PATH": ("./data/gateway.db", None),
    "SESSION_DB_PATH": ("./data/sessions.db", ("SESSION_STORE_BACKEND", "eq:sqlite", "sqlite")),
    "REVIEW_DB_PATH": ("data/reviews.db", ("REVIEW_ENABLED", "bool", "false")),
    "REPORT_DB_PATH": ("./data/reports.db", ("REPORT_FEATURE_ENABLED", "bool", "false")),
    "TRANSLATE_V2_DB_PATH": (None, None),
    "TRANSLATE_V2_WORKER_DB_PATH": (None, None),
}
TRUTHY = {"1", "true", "yes", "on", "y", "t"}


@check("OPS-BAK-5", MUST, "DB SQLite của service gateway nằm trên volume/bind (giữ qua rollback, backup được)")
def bak_db_on_volume(ctx: Ctx) -> R:
    bad, n = [], 0
    for name, c in sorted(ctx.containers().items()):
        if not is_gateway_app(c):
            continue
        env = ctx.env(c)
        wd = (c.get("Config") or {}).get("WorkingDir") or "/"
        for var, (default, cond) in DB_VARS.items():
            if cond:
                cvar, kind, cdef = cond
                val = env.get(cvar, cdef).strip().lower()
                on = val in TRUTHY if kind == "bool" else val == kind.split(":", 1)[1]
                if not on:
                    continue
            path = env.get(var) or default
            if not path:
                continue
            path = os.path.normpath(os.path.join(wd, path))
            n += 1
            if not mounted(c, path):
                bad.append(f"{name}:{var}={path}" + ("" if var in env else " (mặc định)"))
    if bad:
        return R(FAIL, "DB nằm trong lớp container: " + short(bad), "mount volume cho thư mục DB")
    return R(PASS, f"{n} DB đang dùng (tính cả mặc định của config.py) đều nằm trên mount")


# ================================================================== MON

@check("OPS-MON-1", MUST, "Mọi container trong phạm vi đang chạy; container có healthcheck ở trạng thái healthy")
def mon_running(ctx: Ctx) -> R:
    try:
        cs = ctx.containers()
    except Unavailable as e:
        if "permission denied" in str(e).lower():
            raise  # thiếu quyền đọc docker: không đo được, không phải prod chết
        return R(FAIL, f"không đọc được trạng thái container ({e}); docker daemon có thể đã dừng",
                 "systemctl status docker; kiểm prod ngay")
    bad = [f"{n} (thiếu)" for n in APP_CONTAINERS + DATASTORES if n not in cs]
    pool = [n for n in cs if POOL_RE.match(n)]
    for name, c in sorted(cs.items()):
        s_ = c.get("State") or {}
        h = (s_.get("Health") or {}).get("Status")
        if s_.get("Status") != "running" or (h and h != "healthy"):
            bad.append(f"{name} ({s_.get('Status')}{'/' + h if h else ''})")
    if len(pool) < POOL_EXPECTED:
        bad.append(f"pool pentestcode {len(pool)}/{POOL_EXPECTED}")
    if bad:
        return R(FAIL, short(bad))
    return R(PASS, f"{len(cs)} container chạy (pool {len(pool)}), có healthcheck thì healthy "
                   "(healthcheck chỉ là liveness: /health trả 200 cả khi degraded, xem OPS-MON-7)")


@check("OPS-MON-2", SHOULD, "Mọi container trong phạm vi có HEALTHCHECK")
def mon_healthcheck(ctx: Ctx) -> R:
    miss = [n for n, c in sorted(ctx.containers().items())
            if not ((c.get("Config") or {}).get("Healthcheck") or {}).get("Test")
            or ((c.get("Config") or {}).get("Healthcheck") or {}).get("Test") == ["NONE"]]
    if miss:
        return R(FAIL, f"không healthcheck: {len(miss)} container: {short(group_pool(miss))}", "thêm healthcheck trong compose")
    return R(PASS, "đều có healthcheck")


@check("OPS-MON-3", MUST, "Restart policy unless-stopped/always cho container trong phạm vi")
def mon_restart(ctx: Ctx) -> R:
    bad = [f"{n} ({(c.get('HostConfig') or {}).get('RestartPolicy', {}).get('Name') or 'no'})"
           for n, c in sorted(ctx.containers().items())
           if (c.get("HostConfig") or {}).get("RestartPolicy", {}).get("Name") not in ("unless-stopped", "always")]
    return R(FAIL, f"{len(bad)} container: {short(group_pool(bad))}") if bad else R(PASS, "đều unless-stopped/always")


@check("OPS-MON-4", MUST, "Có cảnh báo chủ động khi dịch vụ chết/health xấu")
def mon_alerting(ctx: Ctx) -> R:
    units = [u["Id"] for u in ctx.units()]
    timers = ctx.timers()
    hit = [u for u in units + timers if re.search(r"secai.*(alert|monitor|watchdog|healthcheck|uptime)", u, re.I)]
    if hit:
        return R(PASS, f"có: {short(hit)} (kênh nhận cảnh báo: kiểm tay)")
    return R(FAIL, "không có unit/timer giám sát hay cảnh báo cho secai-*; hệ thống ngoài: kiểm tay",
             "timer 5 phút: docker health + /health 8131/18185 + unit failed → gửi cảnh báo (Telegram/email)")


@check("OPS-MON-5", MUST, "Không unit systemd secai-* nào failed hoặc đang khởi động lại liên tục")
def mon_units(ctx: Ctx) -> R:
    bad, warn = [], []
    units = ctx.units()
    for u in units:
        nres = int(u.get("NRestarts") or 0)
        if u.get("ActiveState") == "failed" or u.get("SubState") == "auto-restart":
            bad.append(f"{u['Id']} ({u.get('ActiveState')}/{u.get('SubState')}, {nres} lần restart)")
        elif nres > 100:
            warn.append(f"{u['Id']} ({u.get('ActiveState')}/{u.get('SubState')}, {nres} lần restart kể từ lần start tay gần nhất)")
    if bad:
        return R(FAIL, short(bad + warn, 6), "sửa đích tunnel, hoặc disable unit của instance đã huỷ; reset-failed sau khi xử lý")
    if warn:
        return R(WARN, "đang chạy nhưng từng restart nhiều: " + short(warn, 4), "xem journalctl --user -u <unit>; restart tay để reset bộ đếm")
    return R(PASS, f"{len(units)} unit secai-* không failed/restart loop")


@check("OPS-MON-6", MUST, "Log container có giới hạn dung lượng (max-size/max-file hoặc driver local/journald)")
def mon_logs(ctx: Ctx) -> R:
    bad = []
    for n, c in sorted(ctx.containers().items()):
        lc = (c.get("HostConfig") or {}).get("LogConfig") or {}
        if lc.get("Type") == "json-file" and not (lc.get("Config") or {}).get("max-size"):
            bad.append(n)
    if bad:
        return R(FAIL, f"json-file không giới hạn: {len(bad)} container: {short(group_pool(bad))}",
                 "daemon.json log-opts max-size=50m,max-file=3 (áp khi tạo lại container) hoặc logging: trong compose")
    return R(PASS, "log có giới hạn")


manual("OPS-MON-7", MUST, "/health prod báo status ok và không có problems (readiness, không chỉ liveness)",
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


# Cổng của container network=host (không có PortBindings để đọc): lấy từ bảng service trong tài liệu.
HOST_NET_PORTS = {"secai-translation-gateway": (18185,), "secai-translation-frontend": (5179,)}
# Unit systemd chạy thẳng trên host: devspace proxy sang prod :8131 và gắn token review cho request loopback.
HOST_UNIT_PORTS = {"secai-devspace.service": (5173,)}
# ExecStart mở service nội bộ ra Internet (request đi qua tunnel tới service với peer 127.0.0.1).
PUBLIC_TUNNEL_RE = re.compile(r"\bcloudflared\b[^;]*\btunnel\b|\bngrok\b|\btailscale\b[^;]*\bfunnel\b")
# Cổng service trong phạm vi (app, devspace, kho dữ liệu). `tailscale serve`/funnel trỏ vào đó thì request
# tới service qua tailscaled với peer loopback; funnel = mở ra Internet.
TAILSCALE_SCOPE_PORTS = frozenset({8131, 18183, 18185, 8189, 5173, 5174, 5179, 6333, 6334, 7474, 7687, 6379, 9200})
# Devspace (5173) và frontend dịch (5179) tin request loopback (gắn token): serve trong tailnet vào đó là WARN.
TAILSCALE_WARN_PORTS = frozenset({5173, 5179})


def _target_port(target: str) -> int | None:
    """Cổng đích của Proxy/TCPForward: 'http://127.0.0.1:5173', 'localhost:5733', '5173', 'https+insecure://h'."""
    t = (target or "").strip()
    if t.isdigit():
        return int(t)
    m = re.match(r"^(?:(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://)?(?P<host>\[[^\]]*\]|[^/:]*)(?::(?P<port>\d+))?", t)
    if m and m["port"]:
        return int(m["port"])
    return {"http": 80, "https": 443}.get(((m and m["scheme"]) or "").split("+")[0].lower())


def tailscale_serve_entries(cfg: dict) -> list[tuple[str, bool, int | None, str]]:
    """(lối vào ':<cổng>[path]', có funnel?, cổng đích, đích) của mọi handler Proxy và TCPForward trong
    ServeConfig của `tailscale serve status --json`, kể cả phiên Foreground và Services. Không trả tên máy."""
    out: list[tuple[str, bool, int | None, str]] = []
    funnel = {k for k, v in (cfg.get("AllowFunnel") or {}).items() if v}
    funnel_ports = {k.rsplit(":", 1)[-1] for k in funnel}
    for hp, web in (cfg.get("Web") or {}).items():
        port = str(hp).rsplit(":", 1)[-1]
        for path, h in ((web or {}).get("Handlers") or {}).items():
            tgt = (h or {}).get("Proxy") if isinstance(h, dict) else None
            if tgt:
                out.append((f":{port}{path}", hp in funnel, _target_port(tgt), tgt))
    for port, t in (cfg.get("TCP") or {}).items():
        fwd = (t or {}).get("TCPForward") if isinstance(t, dict) else None
        if fwd:
            out.append((f":{port}", str(port) in funnel_ports, _target_port(fwd), fwd))
    for sub in [*(cfg.get("Foreground") or {}).values(), *(cfg.get("Services") or {}).values()]:
        if isinstance(sub, dict):
            out += tailscale_serve_entries(sub)
    return out


def listening_any(proc_net: Path) -> dict[int, set[str]]:
    """port -> {0.0.0.0, ::} của socket TCP LISTEN bind mọi interface, đọc /proc/net/tcp{,6} (file, không gọi lệnh)."""
    out: dict[int, set[str]] = {}
    for name, anyaddr in (("tcp", "0" * 8), ("tcp6", "0" * 32)):
        try:
            lines = (proc_net / name).read_text().splitlines()[1:]
        except OSError:
            continue
        for ln in lines:
            f = ln.split()
            if len(f) < 4 or f[3] != "0A":
                continue
            addr, _, port = f[1].partition(":")
            if addr == anyaddr:
                out.setdefault(int(port, 16), set()).add("0.0.0.0" if name == "tcp" else "::")
    return out


@check("OPS-SEC-2", MUST, "Chỉ gateway :8131 publish công khai; service khác bind 127.0.0.1/LAN")
def sec_app_ports(ctx: Ctx) -> R:
    bad, unknown = [], []
    listen = listening_any(ctx.cfg.proc_net)
    for n in APP_CONTAINERS + tuple(sorted(x for x in ctx.containers() if POOL_RE.match(x))):
        c = ctx.containers().get(n)
        if not c:
            continue
        if (c.get("HostConfig") or {}).get("NetworkMode") == "host":
            ports = HOST_NET_PORTS.get(n)
            if not ports:
                unknown.append(n)
            for port in ports or ():
                if port in listen:
                    bad.append(f"{n} :{port} ({'/'.join(sorted(listen[port]))}, network=host)")
        for ip, p in _published(c):
            if ip in ("0.0.0.0", "::", "") and not (n == "secai-docker-gateway" and p.startswith("8131->")):
                bad.append(f"{n} {p}")
    for unit, ports in HOST_UNIT_PORTS.items():
        bad += [f"{unit} :{port} ({'/'.join(sorted(listen[port]))}, unit systemd)" for port in ports if port in listen]
    tunnels, idle, gaps = [], [], []
    try:
        for u in ctx.units():
            if PUBLIC_TUNNEL_RE.search(u.get("ExecStart", "")):
                running = u.get("ActiveState") in ("active", "activating", "reloading")
                (tunnels if running else idle).append(f"{u['Id']} ({u.get('ActiveState')})")
    except Unavailable as e:
        gaps.append(f"unit: {e}")
    if tunnels:
        bad.append("tunnel công khai đang chạy: " + ", ".join(tunnels))
    ts_warn, ts_note, ts = [], "", None
    try:
        ts = ctx.tailscale_serve()
        if ts is None:
            ts_note = "; tailscale: không có trên máy (bỏ qua)"
    except Unavailable as e:
        gaps.append(str(e))
    if ts is not None:
        entries = tailscale_serve_entries(ts)
        for entry, is_funnel, port, tgt in entries:
            if is_funnel and port in TAILSCALE_SCOPE_PORTS:
                bad.append(f"tailscale funnel {entry} -> {tgt} (Internet vào cổng nội bộ :{port})")
            elif not is_funnel and port in TAILSCALE_WARN_PORTS:
                ts_warn.append(f"{entry} -> {tgt}")
        ts_note = f"; tailscale serve: {len(entries)} lối vào, không funnel nào vào cổng trong phạm vi"
    note = f"; network=host chưa biết cổng (kiểm tay `ss -ltnp`): {short(unknown, 4)}" if unknown else ""
    note += f"; unit còn cài tunnel cloudflared/ngrok/tailscale funnel (không chạy): {', '.join(idle)}" if idle else ""
    note += (f"; tailscale serve trong tailnet vào devspace/frontend dịch (request tới với peer loopback): "
             f"{', '.join(ts_warn)}") if ts_warn else ""
    note += ("; không đọc được " + "; ".join(gaps)) if gaps else ""
    fix = ("bind 127.0.0.1 hoặc IP LAN; public chỉ :8131; gỡ tunnel trỏ vào service nội bộ "
           "(`tailscale funnel reset` / `tailscale serve reset`)")
    if bad:
        return R(FAIL, "nghe trên mọi interface hoặc mở qua tunnel: " + "; ".join(bad) + note, fix)
    note += ts_note if not ts_warn else ""
    if idle or ts_warn:
        return R(WARN, "không service nào ngoài :8131 nghe trên mọi interface" + note, fix)
    if gaps:
        return R(WARN, "không service nào ngoài :8131 nghe trên mọi interface" + note, unmeasured=True)
    return R(PASS, "không service nào ngoài :8131 nghe trên mọi interface, không tunnel công khai" + note)


DEFAULT_RE = re.compile(r"\$\{[A-Za-z0-9_]+:?-([^}]*)\}?")


def literal_secret(v: str) -> str | None:
    """Phần literal của giá trị gán cho biến secret: cả giá trị nếu không có ${...}; giá trị mặc định
    của ${X:-lit} (mặc định nằm thẳng trong file nên cũng là secret lộ). None nếu chỉ tham chiếu."""
    v = (v or "").strip().strip("'\"")
    if not v or v.lower() in ("required", "optional", "none", "null", "false", "true"):
        return None
    defaults = [d.strip("}") for d in DEFAULT_RE.findall(v) if d.strip("}")]
    if defaults:
        lit = defaults[0]
    elif "$" in v or v.startswith(("<", "{")) or "(" in v or "[" in v:  # tham chiếu, lời gọi hàm, subscript
        return None
    else:
        lit = v
    return None if PLACEHOLDER_RE.fullmatch(lit.strip().strip("'\"")) else lit


def _secret_names(text: str, ctx: Ctx) -> list[str]:
    names = []
    for m in SECRET_ASSIGN_RE.finditer(text):
        name, val = m["name"], m["val"]
        if not is_secret_name(name):
            continue
        if text[max(0, m.start("name") - 2):m.start("name")] == "${":
            # Tham chiếu ${NAME...}: ${NAME:?msg} chỉ là thông báo, không phải literal; mặc định của
            # ${NAME:-x}/${NAME:=x} và giá trị thay thế của ${NAME:+x} nằm thẳng trong file nên vẫn xét.
            if val[:1] == "?":
                continue
            val = val.lstrip("+-=")
        # Tên chữ thường không có ngoặc ở tên lẫn giá trị là code/chú thích (`llm_key = s.x`, `a token: …`),
        # không phải cấu hình; env/compose/shell dùng tên viết hoa, JSON/Python literal có ngoặc.
        if not name.isupper() and not (m["q1"] or m["q2"]):
            continue
        lit = literal_secret(val)
        if not lit or len(lit) < 8:
            continue
        ctx.remember_secret(lit)
        ctx.remember_secret(val)
        names.append(name)
    for m in GET_DEFAULT_RE.finditer(text):
        lit = literal_secret(m["val"]) if is_secret_name(m["name"]) else None
        if lit and len(lit) >= 8:
            ctx.remember_secret(lit)
            names.append(m["name"])
    return sorted(set(names))


def _walk_files(root: Path, depth: int, skip_dirs=("tree", "imgsrc", "node_modules", ".git", "__pycache__", ".venv")):
    base = len(root.parts)
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in skip_dirs] if len(Path(d).parts) - base < depth else []
        for f in files:
            p = Path(d) / f
            if regular_file(p):
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


@check("OPS-SEC-4", MUST, "Compose prod không chứa secret dạng literal (chỉ ${VAR} không mặc định, hoặc env_file)")
def sec_compose_literal(ctx: Ctx) -> R:
    comp = ctx.cfg.docker_dir / "docker-compose.yml"
    if not regular_file(comp):
        raise Unavailable(f"không có {comp}")
    names = []
    for line in comp.read_text(errors="replace").splitlines():
        m = re.match(r"""^\s*-?\s*["']?([A-Za-z0-9_]+)["']?\s*[:=]\s*(.*)$""", line)
        if not m or not is_secret_name(m.group(1)):
            continue
        lit = literal_secret(m.group(2))
        if lit:
            ctx.remember_secret(lit)
            ctx.remember_secret(m.group(2).strip().strip("'\""))
            names.append(m.group(1))
    mode = oct(comp.stat().st_mode & 0o777)[2:]
    if names:
        wr = "world-readable" if world_readable(comp) else "không world-readable"
        return R(FAIL, f"{comp.name} (mode {mode}, {wr}) có literal (kể cả mặc định ${{X:-...}}): {', '.join(sorted(set(names)))}",
                 "chuyển sang ${VAR} không mặc định, giá trị trong .env 600; đổi mật khẩu đã lộ")
    return R(PASS, f"{comp.name}: biến secret đều qua ${{VAR}}/env_file, không có mặc định literal")


@check("OPS-SEC-5", MUST, "Script cron/đồng bộ (CVE-DB) không chứa khoá API plaintext; file khoá mode 600")
def sec_cron_scripts(ctx: Ctx) -> R:
    hits, n = [], 0
    for pat in ctx.cfg.cron_globs:
        for f in sorted(glob.glob(os.path.expanduser(pat))):
            p = Path(f)
            if not regular_file(p):
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
    """(path, line, kind) của chuỗi giống secret trong file tracked; không trả giá trị.

    File trong FAKE_SECRET_FIXTURES chỉ được miễn đúng số match đã khai báo cho từng loại; thêm một
    match mới vào chính file đó vẫn bị báo (line=0, kind ghi số lượng)."""
    r = runner or Runner()
    p = r.run(["git", "--no-optional-locks", "-C", str(repo), "ls-files", "-z"])
    dirs = tuple(k for k, v in FAKE_SECRET_FIXTURES.items() if v is None)
    out = []
    for f in p.out.split("\0"):
        if not f or f.startswith(dirs):
            continue
        fp = repo / f
        try:
            if not regular_file(fp) or fp.stat().st_size > 2_000_000:
                continue
            b = fp.read_bytes()
        except OSError:
            continue
        if b"\0" in b[:4096]:
            continue
        allowed = FAKE_SECRET_FIXTURES.get(f) or {}
        for kind, rx in TRACKED_SECRET_PATTERNS.items():
            ms = list(rx.finditer(b))
            if f in FAKE_SECRET_FIXTURES:
                if len(ms) > allowed.get(kind, 0):
                    out.append((f, 0, f"{kind} x{len(ms)} > {allowed.get(kind, 0)} đã khai báo"))
                continue
            out += [(f, b.count(b"\n", 0, m.start()) + 1, kind) for m in ms]
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
    hits, gaps, n_c, n_u = [], [], 0, 0
    try:
        for n, c in sorted(ctx.containers().items()):
            n_c += 1
            cfg = c.get("Config") or {}
            argv = " ".join((cfg.get("Entrypoint") or []) + (cfg.get("Cmd") or []) + (c.get("Args") or []))
            for m in ARGV_SECRET_RE.finditer(argv):
                hits.append(f"{n} ({m.group(1) or m.group(2)})")
                ctx.remember_secret(re.split(r"=|\s+", m.group(0), maxsplit=1)[-1])
            for m in URL_CRED_RE.finditer(argv):
                hits.append(f"{n} (URL có mật khẩu)")
                ctx.remember_secret(m.group(1))
    except Unavailable as e:
        gaps.append(f"container: {e}")
    try:
        for u in ctx.units():
            n_u += 1
            for m in ARGV_SECRET_RE.finditer(u.get("ExecStart", "")):
                hits.append(f"{u['Id']} ({m.group(1) or m.group(2)})")
                ctx.remember_secret(re.split(r"=|\s+", m.group(0), maxsplit=1)[-1])
    except Unavailable as e:
        gaps.append(f"unit: {e}")
    gap_note = ("; không đọc được " + "; ".join(gaps)) if gaps else ""
    if hits:
        return R(FAIL, short(sorted(set(hits))) + gap_note, "truyền qua env_file/EnvironmentFile hoặc file 600")
    if gaps:
        return R(WARN, f"{n_c} container + {n_u} unit sạch" + gap_note, unmeasured=True)
    return R(PASS, f"{n_c} container + {n_u} unit: không thấy secret trong argv")


@check("OPS-SEC-8", SHOULD, "Container chạy user không phải root")
def sec_nonroot(ctx: Ctx) -> R:
    bad = [n for n, c in sorted(ctx.containers().items())
           if ((c.get("Config") or {}).get("User") or "root").split(":")[0] in ("", "root", "0")]
    if bad:
        return R(FAIL, f"chạy root: {len(bad)} container: {short(group_pool(bad))}",
                 "user: trong compose (pool pentestcode cần xét quyền tool)")
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
    _, repo = ctx.gh("repos/{repo}")
    if (repo or {}).get("private"):
        return R(PASS, "repo private")
    sa = security_and_analysis(ctx)
    st = {k: (sa.get(k) or {}).get("status") for k in ("secret_scanning", "secret_scanning_push_protection")}
    if all(v == "enabled" for v in st.values()):
        return R(PASS, "public; secret scanning + push protection bật")
    return R(FAIL, "repo PUBLIC; " + ", ".join(f"{k}={v}" for k, v in st.items()),
             "bật Secret scanning + Push protection (miễn phí cho repo public) hoặc chuyển private")


manual("OPS-SEC-11", MUST, "Cổng public :8131 có TLS hoặc chỉ mở cho nguồn tin cậy; endpoint ghi/quản trị từ chối request không khoá",
       "từ máy ngoài LAN: curl -sI http://<IP public>:8131/health (phải bị chặn hoặc chỉ có qua https); "
       "curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8131/ai/v2/reports và GET /review-api/... không kèm khoá: 401/403; "
       "từ máy khác trong LAN: GET http://<IP LAN>:5173/review-api/... kèm -H 'X-Review-Role: admin' (devspace): 403")


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


@check("OPS-RES-4", SHOULD, "Image secai-* không container nào dùng chiếm ≤ 30 GB")
def res_reclaimable(ctx: Ctx) -> R:
    """Chỉ image của dự án (repo `secai-*`): image của project khác trên máy không thuộc phạm vi chấm.
    Cộng cột Size theo image ID (mỗi ID một lần); layer chung bị cộng trùng nên là cận trên."""
    used = {c.get("Image") for c in ctx.inspect_all()}
    p = ctx.docker("images", "--no-trunc", FIRST_PARTY_IMAGES, "--format", "{{json .}}")
    unused: dict[str, tuple[str, float]] = {}
    for im in (json.loads(x) for x in p.out.splitlines() if x.strip()):
        iid = im.get("ID", "")
        if iid and iid not in used and iid not in unused:
            unused[iid] = (f"{im.get('Repository')}:{im.get('Tag')}", size_gb(im.get("Size", "")))
    gb = sum(g for _, g in unused.values())
    top = [f"{ref} ({g:.1f} GB)" for ref, g in sorted(unused.values(), key=lambda x: -x[1])]
    ev = f"{gb:.1f} GB trong {len(unused)} image {FIRST_PARTY_IMAGES} không container nào dùng (cận trên: layer chung cộng trùng)"
    ev += f": {short(top, 5)}" if top else ""
    return R(PASS, ev) if gb <= 30 else R(
        FAIL, ev, "release-prod.sh gc dọn main-/smoke-; tag secai-* khác rà rồi docker rmi (giữ PREV_TAG); không prune volume")


@check("OPS-RES-5", MUST, "gc chạy tốt: không còn container *-rollback-*/*-smoke-* dừng > 3 ngày, image main-/smoke- quá hạn; timer active")
def res_gc(ctx: Ctx) -> R:
    old = []
    for c in ctx.inspect_all():
        n = c["Name"].lstrip("/")
        s_ = c.get("State") or {}
        if not re.search(r"-(rollback|smoke)-", n) or s_.get("Status") not in ("exited", "created", "dead"):
            continue
        t = parse_ts(s_.get("FinishedAt", "")) or parse_ts(c.get("Created", ""))
        if t and (ctx.now - t).days > 3:
            old.append(n)
    probs = [f"{len(old)} container quá hạn: {short(old, 3)}"] if old else []
    try:
        prev = ctx.latest_release()[1].get("PREV_TAG", "")
    except Unavailable:
        prev = ""
    used = {c.get("Image") for c in ctx.inspect_all()} | {(c.get("Config") or {}).get("Image") for c in ctx.inspect_all()}
    stale = []
    for im in ctx.gateway_images():
        tag = im.get("Tag", "")
        t = parse_ts(im.get("CreatedAt", ""))
        ref = f"{GATEWAY_IMAGE}:{tag}"
        if (re.match(r"^(main|smoke)-", tag) and t and (ctx.now - t).days > 4 and ref != prev
                and im.get("ID") not in used and ref not in used):
            stale.append(tag)
    if stale:
        probs.append(f"{len(stale)} image main-/smoke- quá hạn mà gc chưa xoá: {short(stale, 3)}")
    try:
        units = {u["Id"]: u for u in ctx.units()}
        svc = units.get("secai-wt-prune.service")
        tim = units.get("secai-wt-prune.timer")
        if not tim or tim.get("ActiveState") != "active":
            probs.append("secai-wt-prune.timer không active")
        if svc and svc.get("Result") not in ("success", None):
            probs.append(f"secai-wt-prune.service Result={svc.get('Result')}")
    except Unavailable as e:
        if not probs:
            raise
        probs.append(str(e))
    if probs:
        return R(FAIL, "; ".join(probs), "scripts/install-wt-prune-timer.sh; journalctl --user -u secai-wt-prune "
                                         "(runner gọi gc với `|| true` nên gc lỗi vẫn ra Result=success)")
    return R(PASS, "không container rollback/smoke hay image main-/smoke- quá hạn; secai-wt-prune.timer active")


@check("OPS-RES-6", SHOULD, "Container trong phạm vi có giới hạn bộ nhớ")
def res_mem(ctx: Ctx) -> R:
    bad = [n for n, c in sorted(ctx.containers().items()) if not (c.get("HostConfig") or {}).get("Memory")]
    if bad:
        return R(FAIL, f"không mem_limit: {len(bad)} container: {short(group_pool(bad))}", "mem_limit trong compose (đo RSS thực tế trước)")
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

@check("OPS-GIT-1", MUST, "Checkout làm nguồn mount prod ở main/origin/main, không ahead, không sửa dở trong thư mục được mount")
def git_main_checkout(ctx: Ctx) -> R:
    main_root = ctx.main_checkout().resolve()
    probs, warns = [], []
    try:
        mounts = checkout_mounts(ctx)
        known = True
    except Unavailable:
        mounts, known = [], False
    roots = sorted({r for _, r, _ in mounts} | {main_root})
    for root in roots:
        rels = [rel for _, r, rel in mounts if r == root]
        w = worktree_info(ctx, root)
        if is_deploy_main(root):
            if ctx.git("merge-base", "--is-ancestor", str(w.get("HEAD", "HEAD")), "origin/main", repo=main_root, check=False).rc != 0:
                probs.append(f"{root.name}: HEAD không nằm trên origin/main")
        elif root == main_root:
            br = ctx.git("branch", "--show-current", repo=root).out.strip()
            if br != "main":
                (probs if rels else warns).append(f"{root.name} đang ở nhánh {br or '(detached)'}")
            ahead = ctx.git("rev-list", "--count", "origin/main..HEAD", repo=root, check=False).out.strip()
            if ahead not in ("", "0"):
                (probs if rels else warns).append(f"{root.name} ahead origin/main {ahead} commit")
        dirty = [ln[3:] for ln in ctx.git("status", "--porcelain", "--untracked-files=no", repo=root).out.splitlines() if ln.strip()]
        hot = [d for d in dirty if any(d == s_ or d.startswith(s_.rstrip("/") + "/") for s_ in rels)]
        if hot:
            probs.append(f"{root.name}: {len(hot)} file sửa chưa commit nằm trong mount prod ({short(hot, 3)})")
        elif dirty:
            warns.append(f"{root.name}: {len(dirty)} file sửa chưa commit ({short(dirty, 3)}) "
                         + ("ngoài thư mục được mount" if known else "— không đọc được docker nên chưa biết có nằm trong mount không"))
    if probs:
        return R(FAIL, "; ".join(probs + warns))
    if warns:
        return R(WARN, "; ".join(warns))
    return R(PASS, f"{', '.join(r.name for r in roots)}: đúng nhánh, sạch, không ahead")


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
    stale, busy = [], []
    wts = ctx.worktrees()
    for w in wts[1:]:
        path, head = Path(w["worktree"]), w.get("HEAD")
        if not head or not w.get("branch") or not path.is_dir():
            continue
        if ctx.git("merge-base", "--is-ancestor", head, "origin/main", repo=main_root, check=False).rc != 0:
            continue
        age_h = (ctx.now.timestamp() - path.stat().st_mtime) / 3600
        if age_h <= 24:
            continue
        # có sửa chưa commit: đang làm dở, wt-prune.sh cũng bỏ qua, không phải "bị bỏ quên"
        if ctx.git("status", "--porcelain", "--untracked-files=no", repo=path, check=False).out.strip():
            busy.append(path.name)
            continue
        stale.append(f"{path.name} ({age_h / 24:.0f} ngày)")
    ev = f"{len(wts)} worktree; đã merge, cũ > 24h: {len(stale)}" + (f" ({short(stale, 4)})" if stale else "")
    ev += f"; bỏ qua {len(busy)} worktree có sửa chưa commit ({short(busy, 4)})" if busy else ""
    return R(FAIL, ev, "scripts/wt-prune.sh --yes (timer hằng giờ lẽ ra đã dọn; xem log)") if stale else R(PASS, ev)


@check("OPS-GIT-5", SHOULD, ".gitignore chặn .env, .env.*, data/, logs/, *.log, .venv/")
def git_ignore(ctx: Ctx) -> R:
    lines = {ln.strip() for ln in ctx.read_repo(".gitignore").splitlines()}
    need = [".env", ".env.*", "data/", "logs/", "*.log", ".venv/"]
    miss = [n for n in need if n not in lines]
    return R(FAIL, f"thiếu {', '.join(miss)}") if miss else R(PASS, "đủ " + ", ".join(need))


# ================================================================== DOC
CRITERIA_DOC = "docs/criteria/ops.md"                         # tiêu chí: docs/criteria/ (docs/README.md)
RESTORE_DOC = "docs/operations/restore-from-backup.md"       # runbook: docs/operations/
INCIDENT_DOC = "docs/operations/incident-runbook.md"


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


@check("OPS-DOC-4", MUST, f"Có runbook khôi phục dữ liệu prod từ backup ({RESTORE_DOC}) cho Qdrant, Neo4j, SQLite")
def doc_restore(ctx: Ctx) -> R:
    txt = ctx.read_repo(RESTORE_DOC).lower()
    if not txt:
        return R(FAIL, f"chưa có {RESTORE_DOC} (stack/restore-data.sh chỉ dựng máy mới từ gói, không phải khôi phục backup)",
                 f"viết {RESTORE_DOC}: Qdrant snapshot recover, neo4j-admin load, sqlite .restore, thứ tự dừng/khởi động")
    miss = [w for w in ("qdrant", "neo4j", "sqlite") if w not in txt]
    return R(FAIL, f"{RESTORE_DOC} chưa nói tới: {', '.join(miss)}") if miss else R(
        PASS, f"{RESTORE_DOC} có Qdrant, Neo4j, SQLite (lệnh: kiểm tay)")


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


@check("OPS-DOC-7", MUST, f"Có runbook sự cố ({INCIDENT_DOC}): người nhận cảnh báo và cách xử lý các lỗi đã gặp")
def doc_incident(ctx: Ctx) -> R:
    txt = ctx.read_repo(INCIDENT_DOC).lower()
    if not txt:
        return R(FAIL, f"chưa có {INCIDENT_DOC}",
                 "ghi người trực/kênh nhận cảnh báo và bước xử lý: tunnel LLM/serving chết, đĩa > 90%, /health degraded, KB sync cũ, rollback")
    need = {"người nhận": r"người (nhận|trực)", "tunnel": r"tunnel", "đĩa": r"đĩa|disk", "health": r"/health", "kb": r"kb[_ ]sync|kb"}
    miss = [k for k, rx in need.items() if not re.search(rx, txt)]
    return R(FAIL, f"{INCIDENT_DOC} thiếu: {', '.join(miss)}") if miss else R(PASS, f"{INCIDENT_DOC} có người nhận và các tình huống chính")


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
            r = R(SKIP, f"không đo được: {e}", unmeasured=not e.soft)
        except Exception as e:  # lỗi của checker: hiện ra, không giả là PASS/FAIL
            r = R(WARN, f"lỗi checker khi đo: {type(e).__name__}: {e}", unmeasured=True)
        out.append(Result(ck, r))
    return out


def summarize(results: list[Result]) -> dict[str, dict]:
    s: dict[str, dict] = {}
    for res in results:
        g = s.setdefault(res.check.group, {"title": GROUPS[res.check.group], PASS: 0, FAIL: 0, WARN: 0, SKIP: 0,
                                           "fail_must": 0, "unmeasured_must": 0})
        g[res.r.status] += 1
        if res.r.status == FAIL and res.check.level == MUST:
            g["fail_must"] += 1
        if res.r.unmeasured and res.check.level == MUST:
            g["unmeasured_must"] += 1
    for g in s.values():
        n = g[PASS] + g[FAIL] + g[WARN]
        g["score"] = round(100 * (g[PASS] + 0.5 * g[WARN]) / n) if n else None
    return s


def exit_code(results: list[Result]) -> int:
    """1: có FAIL bắt buộc. 3: không FAIL nhưng có tiêu chí bắt buộc tự động không đo được
    (docker/systemctl/file lỗi, checker lỗi; thiếu GitHub không tính). 0: còn lại."""
    if any(r.r.status == FAIL and r.check.level == MUST for r in results):
        return 1
    if any(r.r.unmeasured and r.check.level == MUST for r in results):
        return 3
    return 0


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
    um = sum(v["unmeasured_must"] for v in summary.values())
    lines.append("")
    lines.append(f"Kết luận: {fm} FAIL ở tiêu chí bắt buộc, {um} tiêu chí bắt buộc không đo được → mã thoát {exit_code(results)}"
                 " (0 đạt, 1 có FAIL bắt buộc, 3 có tiêu chí bắt buộc không đo được)")
    return ctx.scrub("\n".join(lines))


def render_json(results: list[Result], summary: dict, ctx: Ctx) -> str:
    doc = {
        "generated_at": ctx.now.isoformat(timespec="seconds"),
        "repo": str(ctx.cfg.repo),
        "results": [{"id": r.check.id, "group": r.check.group, "level": r.check.level, "title": r.check.title,
                     "status": r.r.status, "evidence": r.r.evidence, "fix": r.r.fix, "unmeasured": r.r.unmeasured}
                    for r in results],
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
    # mặc định trùng scripts/backup-sqlite.py; thư mục chưa có thì OPS-BAK-2/3 vẫn FAIL như chưa khai báo
    backup = a.backup_dir or os.environ.get("SECAI_BACKUP_DIR") or os.path.expanduser(DEFAULT_BACKUP_DIR)
    return Config(
        repo=repo,
        docker_dir=Path(a.docker_dir or os.environ.get("SECAI_DOCKER_DIR") or "/home/ict/AIEngines/secai/secai-docker"),
        releases_dir=Path(a.releases_dir or os.environ.get("SECAI_RELEASES_DIR") or os.path.expanduser("~/secai-releases")),
        data_root=Path(a.data_root or os.environ.get("SECAI_DATA_ROOT") or "/home/ict/AIEngines/secai/secai-data"),
        backup_dir=Path(backup) if backup else None,
        gh_repo=a.gh_repo or (None if a.no_gh else gh_repo_from_remote(runner, repo)),
        use_gh=not a.no_gh,
        cron_globs=a.cron_glob or ["~/Downloads/CVE-DB/*"],
        proc_net=Path(a.proc_net),
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
    ap.add_argument("--backup-dir", help=f"đích backup (mặc định $SECAI_BACKUP_DIR, không có thì {DEFAULT_BACKUP_DIR}); "
                                         "mỗi kho một thư mục con")
    ap.add_argument("--gh-repo", help="owner/repo trên GitHub (mặc định lấy từ origin)")
    ap.add_argument("--no-gh", action="store_true", help="không gọi gh (tiêu chí GitHub thành SKIP)")
    ap.add_argument("--cron-glob", action="append", help="file script cron cần quét khoá (lặp được)")
    ap.add_argument("--proc-net", default="/proc/net", help=argparse.SUPPRESS)  # cho test: thư mục chứa tcp, tcp6
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
