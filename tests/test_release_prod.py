"""Hành vi của scripts/release-prod.sh, chạy hoàn toàn offline (tiêu chí OPS-REL-3/OPS-REL-4/OPS-VER-4).

Sandbox mỗi test: repo git tạm (origin bare + clone), thư mục release và secai-docker tạm, và các
lệnh giả đứng đầu PATH:
  docker  Python, giữ trạng thái container/image trong một file JSON, ghi lại mọi lệnh;
          `compose up` đọc image từ compose YAML/profile JSON và đổi container tương ứng.
  curl    trả /health của container đang chạy (git_sha lấy từ tag main-<sha8>-...).
  git     ghi log rồi gọi git thật.
  sleep   không chờ (để vòng chờ health 45 lần chạy ngay).
Script được chép vào sandbox (`$0 rollback --yes` khi tự rollback cũng gọi bản chép).

Không test nào phụ thuộc lời gọi ghi sổ phiên bản (scripts/versions.py, nhánh feat/svc-versions):
sandbox không có versions.py nên nếu script gọi nó thì lời gọi đó hỏng, và theo thiết kế không được
làm hỏng release. Test chỉ khẳng định các thay đổi lên prod (lệnh docker có tác dụng, compose,
profile, LATEST-PROD), không khẳng định thứ tự hay số lượng lệnh chỉ đọc.

Hành vi hiện tại chưa đạt tiêu chí: xfail(strict=True, raises=OpsGap). Lỗi harness là AssertionError
thường nên vẫn FAIL thật; khi script được sửa đúng, test XPASS và strict buộc gỡ marker.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release-prod.sh"
REAL_GIT = shutil.which("git")
IMG = "secai-agent-gateway"
TRIO = ("secai-docker-gateway", "secai-translation-gateway", "secai-translation-worker")
TODAY = dt.date.today().strftime("%Y%m%d")
MUTATING = {"run", "rm", "rmi", "build", "create", "cp", "tag", "compose", "stop", "start", "restart",
            "rename", "kill", "pull", "push", "commit", "load", "import"}

pytestmark = pytest.mark.skipif(not (REAL_GIT and shutil.which("flock") and shutil.which("bash")),
                                reason="cần git, flock, bash")


class OpsGap(AssertionError):
    """Script chạy đúng như hiện tại nhưng chưa đạt tiêu chí ops (dùng cho xfail strict)."""


def gap(ok: bool, msg: str) -> None:
    if not ok:
        raise OpsGap(msg)


def xfail_gap(reason: str):
    return pytest.mark.xfail(strict=True, raises=OpsGap, reason=reason)


def ts(days_ago: float = 0) -> str:
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


# ------------------------------------------------------------------ lệnh giả
DOCKER_STUB = r'''
import datetime, json, os, re, shutil, sys
STATE = os.environ["FAKE_STATE"]
argv = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": argv, "cwd": os.getcwd()}) + "\n")
s = json.load(open(STATE))
C, I = s["containers"], s["images"]


def save():
    json.dump(s, open(STATE, "w"), indent=1)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


def die(msg, rc=1):
    print(msg, file=sys.stderr)
    sys.exit(rc)


def seq():
    s["seq"] = s.get("seq", 0) + 1
    return s["seq"]


def img_id(ref):
    if ref in I:
        return I[ref]["id"]
    for v in I.values():
        if v["id"] == ref or v["id"].replace("sha256:", "").startswith(ref.replace("sha256:", "")):
            return v["id"]
    return None


def find_c(key):
    if key in C:
        return key
    for n, c in C.items():
        if c["id"].startswith(key):
            return n
    return None


def split_fmt(args):
    f, pos, i = None, [], 0
    while i < len(args):
        a = args[i]
        if a == "--format":
            f = args[i + 1]; i += 2; continue
        if a.startswith("--format="):
            f = a.split("=", 1)[1]; i += 1; continue
        if not a.startswith("-"):
            pos.append(a)
        i += 1
    return f, pos


def render_c(n, f):
    c = C[n]
    if f is None:
        return {"Name": "/" + n, "Id": c["id"], "Image": c["image_id"], "Created": c["created"],
                "State": {"Status": c["status"]},
                "Config": {"Image": c["image"], "Labels": c.get("labels", {}), "Env": ["OPENAI_API_KEY=sk-fake-not-real"]}}
    return {"{{.Config.Image}}": c["image"], "{{.Created}}": c["created"], "{{.Image}}": c["image_id"],
            "{{.State.Status}}": c["status"], "{{.Name}}": "/" + n}.get(
        f, c.get("labels", {}).get("com.docker.compose.project.config_files", "") if "config_files" in f else "")


def new_container(name, image, status, labels=None):
    if name in C:
        die(f"Conflict. The container name \"/{name}\" is already in use", 125)
    C[name] = {"id": "%064x" % (0xc0ffee000 + seq()), "image": image, "image_id": img_id(image),
               "status": status, "created": now(), "labels": labels or {}}
    return C[name]["id"]


def compose_services(path):
    text = open(path).read()
    if path.endswith(".json") or text.lstrip().startswith("{"):
        return json.loads(text).get("services", {})
    out, cur, inside = {}, None, False
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            inside = True; continue
        if inside and re.match(r"^\S", line):
            inside = False
        if not inside:
            continue
        m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if m:
            cur = m.group(1); out[cur] = {}; continue
        m = re.match(r"^\s{4}(image|container_name):\s*(\S+)\s*$", line)
        if m and cur:
            out[cur][m.group(1)] = m.group(2)
    return out


cmd = argv[0] if argv else ""
if cmd == "inspect":
    f, names = split_fmt(argv[1:])
    found = [find_c(n) for n in names]
    if f is None:
        print(json.dumps([render_c(n, None) for n in found if n]))
    else:
        for n in found:
            if n:
                print(render_c(n, f))
    if not all(found):
        die("Error: No such object: " + ",".join(n for n, k in zip(names, found) if not k))
elif cmd == "image" and argv[1:2] == ["inspect"]:
    f, refs = split_fmt(argv[2:])
    for r in refs:
        iid = img_id(r)
        if not iid:
            die(f"Error: No such image: {r}")
        meta = next(v for v in I.values() if v["id"] == iid)
        if f == "{{.Created}}":
            print(meta["created"])
        elif f == "{{.Id}}":
            print(iid)
        else:
            print(json.dumps([{"Id": iid, "Created": meta["created"], "RepoTags": [k for k, v in I.items() if v["id"] == iid]}]))
elif cmd == "images":
    f, pos = split_fmt(argv[1:])
    for ref, v in I.items():
        if pos and ref.rsplit(":", 1)[0] != pos[0]:
            continue
        print(v["id"] if "-q" in argv else ref)
elif cmd == "ps":
    f, _ = split_fmt(argv[1:])
    statuses = [argv[i + 1].split("=", 1)[1] for i, a in enumerate(argv) if a == "--filter" and argv[i + 1].startswith("status=")]
    for n, c in C.items():
        if statuses and c["status"] not in statuses:
            continue
        if not statuses and "-a" not in argv and "-aq" not in argv and c["status"] != "running":
            continue
        print(c["id"] if ("-q" in argv or "-aq" in argv) else n)
elif cmd == "rm":
    force = "-f" in argv
    for key in [a for a in argv[1:] if not a.startswith("-")]:
        n = find_c(key)
        if not n:
            die(f"Error response from daemon: No such container: {key}")
        if C[n]["status"] == "running" and not force:
            die(f"cannot remove running container {n}")
        del C[n]
    save()
elif cmd == "rmi":
    for ref in [a for a in argv[1:] if not a.startswith("-")]:
        iid = img_id(ref)
        if not iid:
            die(f"Error: No such image: {ref}")
        tags = [k for k, v in I.items() if v["id"] == iid]
        users = [n for n, c in C.items() if c["image_id"] == iid]
        if users and (ref not in I or len(tags) == 1):
            die(f"Error response from daemon: conflict: unable to remove repository reference \"{ref}\" "
                f"(must force) - container {users[0]} is using its referenced image")
        for k in ([ref] if ref in I else tags):
            del I[k]
    save()
elif cmd == "build":
    tag = argv[argv.index("-t") + 1]
    I[tag] = {"id": "sha256:%064x" % (0xb1d000 + seq()), "created": now()}
    save()
    print(I[tag]["id"])
    sys.exit(int(os.environ.get("FAKE_BUILD_RC", "0")))
elif cmd == "run":
    val = {"--name", "--network", "--add-host", "--env-file", "-p", "-e", "-v", "--entrypoint", "-w", "-u", "--user", "--label", "-l", "--mount"}
    opts, flags, i = {}, set(), 1
    while i < len(argv):
        a = argv[i]
        if a in val:
            opts.setdefault(a, []).append(argv[i + 1]); i += 2; continue
        if a.startswith("-"):
            flags.add(a); i += 1; continue
        break
    image, rest = argv[i], argv[i + 1:]
    if not img_id(image):
        die(f"Unable to find image '{image}' locally", 125)
    if "--rm" in flags:
        ep = opts.get("--entrypoint", [""])[0]
        if ep == "pip":
            print("fastapi==0.115.0\nredis==8.1.0\nsecai-agent-gateway @ file:///tmp/wheels/x.whl")
        sys.exit(int(os.environ.get("FAKE_IMPORT_RC", "0")) if ep == "python" else 0)
    print(new_container(opts.get("--name", [f"anon-{seq()}"])[0], image, "running"))
    save()
elif cmd == "create":
    image = [a for a in argv[1:] if not a.startswith("-")][0]
    if not img_id(image):
        die(f"Unable to find image '{image}' locally", 125)
    print(new_container(f"created-{seq()}", image, "created"))
    save()
elif cmd == "cp":
    src, dest = [a for a in argv[1:] if not a.startswith("-")][:2]
    shutil.copytree(os.environ["FAKE_IMGSRC"], dest)
elif cmd == "compose":
    a, proj, files, i = argv[1:], None, [], 0
    while i < len(a) and a[i].startswith("-"):
        if a[i] in ("-p", "--project-name"):
            proj = a[i + 1]; i += 2; continue
        if a[i] in ("-f", "--file"):
            files.append(a[i + 1]); i += 2; continue
        if a[i] in ("--env-file", "--project-directory"):
            i += 2; continue
        i += 1
    sub, rest = a[i], a[i + 1:]
    if sub != "up":
        sys.exit(0)
    path = os.path.abspath(files[0]) if files else os.path.join(os.getcwd(), "docker-compose.yml")
    proj = proj or os.path.basename(os.getcwd())
    svcs = compose_services(path)
    for svc in [x for x in rest if not x.startswith("-")]:
        spec = svcs[svc]
        if not img_id(spec["image"]):
            die(f"Error response from daemon: pull access denied for {spec['image']}")
        name = spec.get("container_name") or f"{proj}-{svc}-1"
        C.pop(name, None)
        new_container(name, spec["image"], "running",
                      {"com.docker.compose.project": proj, "com.docker.compose.project.config_files": path})
    save()
'''

CURL_STUB = r'''
import json, os, re, sys
argv = sys.argv[1:]
url = next((a for a in reversed(argv) if a.startswith("http")), "")
with open(os.environ["FAKE_CURL_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": argv}) + "\n")
if any(d and d in url for d in os.environ.get("FAKE_CURL_DOWN", "").split(",")):
    sys.exit(7)
C = json.load(open(os.environ["FAKE_STATE"]))["containers"]
m = re.match(r"https?://[^/:]+:(\d+)(/[^?]*)?", url)
port, path = (m.group(1), m.group(2) or "/") if m else ("", "/")


def sha(name, override):
    c = C.get(name)
    if not c or c["status"] != "running":
        sys.exit(7)
    if os.environ.get(override):
        return os.environ[override]
    mm = re.search(r":main-([0-9a-f]{8})-", c["image"])
    return mm.group(1) if mm else None


if path == "/health":
    if port == "8131":
        g = sha("secai-docker-gateway", "FAKE_GATEWAY_SHA")
    elif port == "18185":
        g = sha("secai-translation-gateway", "FAKE_TRANSLATION_SHA")
    else:
        smoke = [n for n, c in C.items() if "-smoke-" in n and c["status"] == "running"]
        if not smoke:
            sys.exit(7)
        g = sha(smoke[0], "FAKE_SMOKE_SHA")
    print(json.dumps({"status": "ok", "git_sha": g, "serving": {"llm": {"ok": True}}, "redis": "ok"}, separators=(",", ":")))
else:
    print("{}")
'''


def sh(*args: str, cwd: Path, env: dict | None = None) -> str:
    return subprocess.run(args, cwd=cwd, env=env, check=True, text=True, capture_output=True).stdout.strip()


class Sandbox:
    """Prod giả: 3 container gateway chạy tag main-<c1>-20261120; origin/main đi trước một commit (c2)."""

    def __init__(self, root: Path, *, deps_change: bool = False) -> None:
        self.root = root
        self.bin, self.home = root / "bin", root / "home"
        self.releases, self.docker_dir = root / "releases", root / "secai-docker"
        self.repo, self.origin = root / "repo", root / "origin.git"
        self.state_file, self.docker_log = root / "docker-state.json", root / "docker.log"
        self.curl_log, self.git_log = root / "curl.log", root / "git.log"
        for d in (self.bin, self.home, self.releases, self.docker_dir, root / "scripts", root / "data"):
            d.mkdir(parents=True)
        for f in (self.docker_log, self.curl_log, self.git_log):
            f.write_text("")
        self._stub("docker", f"#!{sys.executable}\n" + DOCKER_STUB)
        self._stub("curl", f"#!{sys.executable}\n" + CURL_STUB)
        self._stub("sleep", "#!/bin/sh\nexit 0\n")
        self._stub("git", f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.git_log}"\nexec "{REAL_GIT}" "$@"\n')
        self.script = root / "scripts" / "release-prod.sh"
        shutil.copy(SCRIPT, self.script)

        genv = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        self.genv = genv
        sh(REAL_GIT, "init", "-q", "--bare", "-b", "main", str(self.origin), cwd=root)
        sh(REAL_GIT, "init", "-q", "-b", "main", str(self.repo), cwd=root)
        (self.repo / "src" / "secai_gateway").mkdir(parents=True)
        (self.repo / "src" / "secai_gateway" / "__init__.py").write_text("VERSION = 1\n")
        (self.repo / "pyproject.toml").write_text('[project]\nname = "secai-agent-gateway"\ndependencies = ["fastapi"]\n')
        shutil.copy(ROOT / "Dockerfile", self.repo / "Dockerfile")  # Dockerfile thật: để test label revision
        (self.repo / ".gitignore").write_text(".venv/\n.env\n")
        self.c1 = self._commit("feat: bản đang chạy prod")
        (self.repo / "src" / "secai_gateway" / "__init__.py").write_text("VERSION = 2\n")
        if deps_change:
            (self.repo / "pyproject.toml").write_text('[project]\nname = "secai-agent-gateway"\ndependencies = ["fastapi", "httpx"]\n')
        self.c2 = self._commit("feat: tính năng mới trên main")
        sh(REAL_GIT, "remote", "add", "origin", str(self.origin), cwd=self.repo)
        sh(REAL_GIT, "push", "-q", "origin", "main", cwd=self.repo, env=genv)
        py = self.repo / ".venv" / "bin" / "python"   # gate 1: "pytest" giả
        py.parent.mkdir(parents=True)
        py.write_text('#!/bin/sh\nif [ -n "$FAKE_PYTEST_OUT" ]; then printf "%s\\n" "$FAKE_PYTEST_OUT"; '
                      'else echo "12 passed in 0.10s"; fi\n')
        py.chmod(0o755)

        self.prod_tag = f"{IMG}:main-{self.c1[:8]}-20261120"
        self.new_tag = f"{IMG}:main-{self.c2[:8]}-{TODAY}"
        self.compose = self.docker_dir / "docker-compose.yml"
        self.compose.write_text(
            "services:\n"
            "  gateway:\n"
            f"    image: {self.prod_tag}\n"
            "    container_name: secai-docker-gateway\n"
            "  qdrant:\n"
            "    image: qdrant/qdrant:v1.10.1\n"
            "    container_name: secai-docker-qdrant\n")
        (self.docker_dir / ".env").write_text("OPENAI_API_KEY=sk-fake-not-real\n")
        (self.docker_dir / ".env").chmod(0o600)
        prev_rel = self.releases / f"main-{self.c1[:8]}-20261120"
        prev_rel.mkdir(mode=0o700)
        self.prev_profile = prev_rel / "translation-main.json"
        self.prev_profile.write_text(json.dumps({"name": "secai-translation-release", "services": {
            "gateway": {"container_name": "secai-translation-gateway", "image": self.prod_tag},
            "worker": {"container_name": "secai-translation-worker", "image": self.prod_tag}}}, indent=1))
        self.prev_profile.chmod(0o600)

        self.state = {"seq": 0, "images": {}, "containers": {}}
        self.add_image(self.prod_tag, days_ago=7)
        self.add_image("qdrant/qdrant:v1.10.1", days_ago=90)
        self.add_container("secai-docker-gateway", self.prod_tag,
                           labels={"com.docker.compose.project": "secai-docker",
                                   "com.docker.compose.project.config_files": str(self.compose)})
        for n in TRIO[1:]:
            self.add_container(n, self.prod_tag, labels={"com.docker.compose.project": "secai-translation-release",
                                                         "com.docker.compose.project.config_files": str(self.prev_profile)})
        self.add_container("secai-docker-qdrant", "qdrant/qdrant:v1.10.1")
        self.save()

        self.env = {**genv, "PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.home),
                    "SECAI_RELEASES_DIR": str(self.releases), "SECAI_DOCKER_DIR": str(self.docker_dir),
                    "SECAI_REPO": str(self.repo), "SECAI_DATA_ROOT": str(root / "data"),
                    "FAKE_STATE": str(self.state_file), "FAKE_DOCKER_LOG": str(self.docker_log),
                    "FAKE_CURL_LOG": str(self.curl_log), "FAKE_GIT_LOG": str(self.git_log),
                    "FAKE_IMGSRC": str(self.releases / f"main-{self.c2[:8]}-{TODAY}" / "tree" / "src")}

    # ---- dựng trạng thái
    def _stub(self, name: str, body: str) -> None:
        p = self.bin / name
        p.write_text(body)
        p.chmod(0o755)

    def _commit(self, msg: str) -> str:
        sh(REAL_GIT, "add", "-A", cwd=self.repo, env=self.genv)
        sh(REAL_GIT, "commit", "-q", "-m", msg, cwd=self.repo, env=self.genv)
        return sh(REAL_GIT, "rev-parse", "HEAD", cwd=self.repo)

    def add_image(self, ref: str, days_ago: float = 0, iid: str | None = None) -> str:
        self.state["seq"] += 1
        iid = iid or "sha256:%064x" % (0xa000 + self.state["seq"])
        self.state["images"][ref] = {"id": iid, "created": ts(days_ago)}
        return iid

    def add_container(self, name: str, image: str, status: str = "running", days_ago: float = 1,
                      labels: dict | None = None) -> None:
        self.state["seq"] += 1
        iid = self.state["images"][image]["id"] if image in self.state["images"] else image
        self.state["containers"][name] = {"id": "%064x" % (0xc000 + self.state["seq"]), "image": image,
                                          "image_id": iid, "status": status, "created": ts(days_ago),
                                          "labels": labels or {}}

    def set_image(self, name: str, image: str) -> None:
        self.state["containers"][name]["image"] = image
        self.state["containers"][name]["image_id"] = self.state["images"][image]["id"]

    def save(self) -> None:
        self.state_file.write_text(json.dumps(self.state, indent=1))

    def load(self) -> dict:
        return json.loads(self.state_file.read_text())

    def released(self) -> Path:
        """Trạng thái ngay sau một release thành công prod_tag → new_tag (để thử rollback/gc)."""
        rel = self.releases / f"main-{self.c2[:8]}-{TODAY}"
        rel.mkdir(mode=0o700)
        bak = rel / f"docker-compose.yml.bak-before-img{self.c2[:8]}-{TODAY}"
        shutil.copy2(self.compose, bak)
        self.compose.write_text(self.compose.read_text().replace(self.prod_tag, self.new_tag))
        new_profile = rel / "translation-main.json"
        new_profile.write_text(self.prev_profile.read_text().replace(self.prod_tag, self.new_tag))
        new_profile.chmod(0o600)
        (rel / "state.env").write_text(
            f"PREV_TAG={self.prod_tag}\nNEW_TAG={self.new_tag}\nCOMPOSE_BAK={bak}\n"
            f"TRANS_PREV_PROFILE={self.prev_profile}\nTRANS_NEW_PROFILE={new_profile}\n")
        (self.releases / "LATEST-PROD").write_text(f"{rel}\n")
        self.add_image(self.new_tag, days_ago=0)
        for n in TRIO:
            self.set_image(n, self.new_tag)
        self.state["containers"]["secai-docker-gateway"]["labels"]["com.docker.compose.project.config_files"] = str(self.compose)
        for n in TRIO[1:]:
            self.state["containers"][n]["labels"]["com.docker.compose.project.config_files"] = str(new_profile)
        self.save()
        return rel

    # ---- chạy + quan sát
    def run(self, *args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(self.script), *args], cwd=self.root, env={**self.env, **extra_env},
                              text=True, capture_output=True, timeout=180)

    def docker_calls(self) -> list[list[str]]:
        return [json.loads(x)["argv"] for x in self.docker_log.read_text().splitlines() if x.strip()]

    def compose_calls(self) -> list[dict]:
        return [json.loads(x) for x in self.docker_log.read_text().splitlines()
                if x.strip() and json.loads(x)["argv"][:1] == ["compose"]]

    def mutating(self) -> list[list[str]]:
        return [a for a in self.docker_calls() if a and a[0] in MUTATING]

    def curl_urls(self) -> list[str]:
        return [next((a for a in reversed(json.loads(x)["argv"]) if a.startswith("http")), "")
                for x in self.curl_log.read_text().splitlines() if x.strip()]

    def images_of(self) -> dict[str, str]:
        return {n: c["image"] for n, c in self.load()["containers"].items()}


def snapshot(*roots: Path) -> dict:
    out = {}
    for r in roots:
        for p in sorted(r.rglob("*")):
            st = p.lstat()
            out[str(p)] = (stat.S_IMODE(st.st_mode), p.read_bytes() if p.is_file() else None)
    return out


@pytest.fixture
def sb(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


def assert_prod_untouched(sb: Sandbox, compose_before: str) -> None:
    assert sb.compose.read_text() == compose_before, "compose prod bị sửa"
    assert not (sb.releases / "LATEST-PROD").exists(), "LATEST-PROD bị ghi"
    assert all(sb.images_of()[n] == sb.prod_tag for n in TRIO), sb.images_of()
    muts = [a for a in sb.mutating() if a[0] in ("compose", "rmi", "tag")]
    assert not muts, muts


# ------------------------------------------------------------------ plan / --yes
def test_plan_changes_nothing(sb: Sandbox) -> None:
    before = snapshot(sb.releases, sb.docker_dir)
    state_before = sb.state_file.read_text()
    res = sb.run("plan")
    assert res.returncode == 0, res.stderr + res.stdout
    assert f"prod:   {sb.prod_tag}" in res.stdout and f"target: {sb.new_tag}" in res.stdout
    assert "feat: tính năng mới trên main" in res.stdout
    assert "build: OVERLAY" in res.stdout
    assert snapshot(sb.releases, sb.docker_dir) == before
    assert sb.state_file.read_text() == state_before
    assert sb.mutating() == []
    assert not list(sb.releases.glob(f"main-{sb.c2[:8]}-*"))


def test_plan_flags_full_build_when_deps_change(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, deps_change=True)
    res = sb.run("plan")
    assert res.returncode == 0, res.stderr
    assert "build: FULL" in res.stdout
    assert sb.mutating() == []


def test_plan_when_prod_already_runs_main(sb: Sandbox) -> None:
    tag = f"{IMG}:main-{sb.c2[:8]}-20261121"
    sb.add_image(tag)
    for n in TRIO:
        sb.set_image(n, tag)
    sb.save()
    res = sb.run("plan")
    assert res.returncode == 0, res.stderr
    assert "không có gì để release" in res.stdout
    assert sb.mutating() == []


@pytest.mark.parametrize("mode", ["run", "rollback"])
def test_mutating_modes_require_yes(sb: Sandbox, mode: str) -> None:
    if mode == "rollback":
        sb.released()
    compose_before = sb.compose.read_text()
    images_before = sb.images_of()
    res = sb.run(mode)
    assert res.returncode != 0
    assert "cần --yes" in res.stderr
    assert sb.mutating() == []
    assert sb.compose.read_text() == compose_before
    assert sb.images_of() == images_before
    assert "fetch" not in sb.git_log.read_text()  # từ chối trước cả khi đụng git


def test_unknown_mode_is_rejected(sb: Sandbox) -> None:
    res = sb.run("deploy")
    assert res.returncode != 0 and "usage" in res.stderr
    assert sb.mutating() == []


# ------------------------------------------------------------------ chặn trước khi build/chuyển prod
@pytest.mark.parametrize("odd", ["secai-translation-gateway", "secai-translation-worker"])
@pytest.mark.parametrize("mode", [["plan"], ["run", "--yes"]])
def test_stops_when_gateway_containers_disagree(sb: Sandbox, odd: str, mode: list[str]) -> None:
    other = f"{IMG}:main-{sb.c1[:8]}-20261119"
    sb.add_image(other, days_ago=8)
    sb.set_image(odd, other)
    sb.save()
    compose_before = sb.compose.read_text()
    res = sb.run(*mode)
    assert res.returncode != 0
    assert f"{odd} không chạy cùng image" in res.stderr and "invariant 3 container" in res.stderr
    assert sb.mutating() == []
    assert sb.compose.read_text() == compose_before
    assert not list(sb.releases.glob(f"main-{sb.c2[:8]}-*")), "đã tạo release dir dù invariant lệch"


@pytest.mark.parametrize("kind", ["side-branch", "unknown-sha"])
@pytest.mark.parametrize("mode", [["plan"], ["run", "--yes"]])
def test_stops_when_prod_not_ancestor_of_main(sb: Sandbox, kind: str, mode: list[str]) -> None:
    if kind == "side-branch":  # prod chạy commit có thật nhưng nằm ngoài main
        sh(REAL_GIT, "checkout", "-q", "-b", "hotfix", sb.c1, cwd=sb.repo, env=sb.genv)
        (sb.repo / "hotfix.txt").write_text("x\n")
        sha = sb._commit("hotfix tay trên prod")
        sh(REAL_GIT, "checkout", "-q", "main", cwd=sb.repo, env=sb.genv)
    else:
        sha = "0123abcd" + "0" * 32
    tag = f"{IMG}:main-{sha[:8]}-20261125"
    sb.add_image(tag)
    for n in TRIO:
        sb.set_image(n, tag)
    sb.save()
    compose_before = sb.compose.read_text()
    res = sb.run(*mode)
    assert res.returncode != 0
    assert "không phải tổ tiên của origin/main" in res.stderr
    assert sb.mutating() == []
    assert sb.compose.read_text() == compose_before
    assert not list(sb.releases.glob(f"main-{sb.c2[:8]}-*"))


def test_stops_when_prod_tag_is_not_immutable_main_tag(sb: Sandbox) -> None:
    tag = f"{IMG}:review-mvp-20261004"
    sb.add_image(tag)
    for n in TRIO:
        sb.set_image(n, tag)
    sb.save()
    res = sb.run("run", "--yes")
    assert res.returncode != 0 and "release tay" in res.stderr
    assert sb.mutating() == []


# ------------------------------------------------------------------ run --yes đầy đủ
def test_run_switches_all_three_and_records_release(sb: Sandbox) -> None:
    compose_before = sb.compose.read_text()
    prev_profile_before = sb.prev_profile.read_text()
    res = sb.run("run", "--yes")
    assert res.returncode == 0, res.stderr[-3000:]
    assert f"RELEASE OK {sb.new_tag}" in res.stderr
    imgs = sb.images_of()
    assert all(imgs[n] == sb.new_tag for n in TRIO), imgs
    assert sb.compose.read_text() == compose_before.replace(sb.prod_tag, sb.new_tag)
    assert sb.prev_profile.read_text() == prev_profile_before
    rel = sb.releases / f"main-{sb.c2[:8]}-{TODAY}"
    assert (sb.releases / "LATEST-PROD").read_text().strip() == str(rel)
    state = dict(ln.split("=", 1) for ln in (rel / "state.env").read_text().splitlines())
    assert state["PREV_TAG"] == sb.prod_tag and state["NEW_TAG"] == sb.new_tag
    assert Path(state["COMPOSE_BAK"]).read_text() == compose_before
    new_profile = rel / "translation-main.json"
    assert sb.new_tag in new_profile.read_text() and stat.S_IMODE(new_profile.stat().st_mode) == 0o600
    assert stat.S_IMODE(rel.stat().st_mode) == 0o700
    assert (rel / "RELEASE-NOTES.md").is_file() and "12 passed" in (rel / "pytest.log").read_text()
    builds = [a for a in sb.docker_calls() if a[:1] == ["build"]]
    assert len(builds) == 1 and "-f" in builds[0], builds  # deps không đổi → build OVERLAY
    overlay = (rel / "tree" / "Dockerfile.overlay").read_text()
    assert f"FROM {sb.prod_tag}" in overlay and f"org.opencontainers.image.revision={sb.c2[:8]}" in overlay
    assert not [n for n in sb.load()["containers"] if "-smoke-" in n], "container smoke còn sót"
    assert not (rel / "tree" / ".env").exists()
    assert sb.prod_tag in sb.load()["images"], "image prod cũ phải được giữ để rollback"


def test_run_blocks_release_when_pytest_gate_is_red(sb: Sandbox) -> None:
    compose_before = sb.compose.read_text()
    res = sb.run("run", "--yes", FAKE_PYTEST_OUT="1 failed, 11 passed in 0.50s")
    assert res.returncode != 0 and "pytest không xanh" in res.stderr
    assert not [a for a in sb.docker_calls() if a[:1] in (["build"], ["run"])]
    assert_prod_untouched(sb, compose_before)


def test_run_blocks_release_when_smoke_reports_wrong_sha(sb: Sandbox) -> None:
    compose_before = sb.compose.read_text()
    res = sb.run("run", "--yes", FAKE_SMOKE_SHA="deadbeef")
    assert res.returncode != 0 and "smoke: /health sai" in res.stderr
    assert not [n for n in sb.load()["containers"] if "-smoke-" in n], "trap EXIT phải xoá container smoke"
    assert_prod_untouched(sb, compose_before)


def test_run_rolls_back_automatically_when_health_wrong_after_switch(sb: Sandbox) -> None:
    compose_before = sb.compose.read_text()
    res = sb.run("run", "--yes", FAKE_GATEWAY_SHA="0badc0de")
    assert res.returncode != 0
    assert "HEALTH SAI" in res.stderr and "đã rollback" in res.stderr
    assert sb.compose.read_text() == compose_before
    assert all(sb.images_of()[n] == sb.prod_tag for n in TRIO), sb.images_of()


# ------------------------------------------------------------------ rollback
def test_rollback_restores_backup_compose_and_previous_profile(sb: Sandbox) -> None:
    rel = sb.released()
    compose_bak = next(rel.glob("docker-compose.yml.bak-*")).read_text()
    res = sb.run("rollback", "--yes")
    assert res.returncode == 0, res.stderr
    assert sb.compose.read_text() == compose_bak
    calls = sb.compose_calls()
    gw = [c for c in calls if c["cwd"] == str(sb.docker_dir) and c["argv"][-1] == "gateway"]
    tr = [c for c in calls if "-p" in c["argv"] and str(sb.prev_profile) in c["argv"]]
    assert gw and tr, calls
    assert all("--no-build" in c["argv"] and "--no-deps" in c["argv"] for c in gw + tr)
    assert all(sb.images_of()[n] == sb.prod_tag for n in TRIO), sb.images_of()


def test_rollback_refuses_while_release_lock_is_held(sb: Sandbox) -> None:
    sb.released()
    compose_now = sb.compose.read_text()
    with open(sb.releases / "gateway-deploy.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        res = sb.run("rollback", "--yes")
    assert res.returncode != 0 and "giữ lock" in res.stderr
    assert sb.compose.read_text() == compose_now
    assert sb.mutating() == []


@xfail_gap("OPS-REL-4: rollback chỉ cp compose + compose up rồi in 'rollback xong', không chờ /health")
def test_rollback_waits_for_health_after_switch(sb: Sandbox) -> None:
    sb.released()
    res = sb.run("rollback", "--yes")
    assert res.returncode == 0, res.stderr
    gap(any(u.startswith("http://127.0.0.1:8131/health") for u in sb.curl_urls()),
        "rollback không kiểm /health 8131 sau khi chuyển")
    gap(any(u.startswith("http://127.0.0.1:18185/health") for u in sb.curl_urls()),
        "rollback không kiểm /health 18185 sau khi chuyển")


@xfail_gap("OPS-REL-4: rollback chép compose bak trước, image PREV_TAG mất thì compose lệch container đang chạy")
def test_rollback_refuses_before_touching_compose_when_previous_image_is_gone(sb: Sandbox) -> None:
    sb.released()
    del sb.state["images"][sb.prod_tag]
    sb.save()
    compose_now = sb.compose.read_text()
    res = sb.run("rollback", "--yes")
    assert res.returncode != 0, "rollback về image không còn mà vẫn báo thành công"
    gap(sb.compose.read_text() == compose_now, "compose đã bị đổi sang PREV_TAG dù image không còn")
    assert all(sb.images_of()[n] == sb.new_tag for n in TRIO)


# ------------------------------------------------------------------ gc
def _gc_world(sb: Sandbox) -> dict[str, str]:
    rel = sb.releases / "main-prevxxxx-20261115"
    rel.mkdir(mode=0o700)
    t = {k: f"{IMG}:{v}" for k, v in {
        "old": "main-0ld0ld00-20261110", "prev": "main-9e590000-20261115", "running": "main-12345678-20261116",
        "stopped": "main-5709ed00-20261112", "fresh": "main-f2e50000-20261126", "smoke_old": "smoke-0ld-20261110",
        "other": "review-mvp-20261001"}.items()}
    (rel / "state.env").write_text(f"PREV_TAG={t['prev']}\nNEW_TAG={t['running']}\n")
    (sb.releases / "LATEST-PROD").write_text(f"{rel}\n")
    sb.state["images"].clear()
    sb.state["containers"].clear()
    for k in ("old", "prev", "running", "stopped", "smoke_old", "other"):
        sb.add_image(t[k], days_ago=10)
    sb.add_image(t["fresh"], days_ago=1)
    sb.add_container("secai-docker-gateway", t["running"])
    sb.add_container("legacy-report", t["stopped"], status="exited", days_ago=10)
    sb.add_container("secai-docker-gateway-smoke-20261115", t["running"], status="exited", days_ago=10)
    sb.add_container("secai-docker-gateway-rollback-20261126", t["prev"], status="exited", days_ago=1)
    sb.add_container("secai-translation-gateway-rollback-20261101", t["prev"], status="running", days_ago=20)
    sb.save()
    return t


def _deleted(sb: Sandbox, verb: str) -> set[str]:
    return {x for a in sb.docker_calls() if a[:1] == [verb] for x in a[1:] if not x.startswith("-")}


def test_gc_keeps_previous_release_and_in_use_images(sb: Sandbox) -> None:
    t = _gc_world(sb)
    res = sb.run("gc", "--yes")
    assert res.returncode == 0, res.stderr
    assert _deleted(sb, "rmi") == {t["old"], t["smoke_old"]}
    assert _deleted(sb, "rm") == {"secai-docker-gateway-smoke-20261115"}
    left = sb.load()["images"]
    for k in ("prev", "running", "stopped", "fresh", "other"):
        assert t[k] in left, k


def test_gc_without_yes_only_lists(sb: Sandbox) -> None:
    t = _gc_world(sb)
    res = sb.run("gc")
    assert res.returncode == 0, res.stderr
    assert "gc dry-run: 3 mục" in res.stdout
    assert t["old"] in res.stdout and "secai-docker-gateway-smoke-20261115" in res.stdout
    assert not [a for a in sb.docker_calls() if a[:1] in (["rm"], ["rmi"])]


@xfail_gap("gc so tag theo .Config.Image; container tạo từ image ID trần (như secai-report-backend) "
           "không giữ được tag main-* của chính image đó")
def test_gc_keeps_image_of_container_created_from_bare_id(sb: Sandbox) -> None:
    t = _gc_world(sb)
    bare = f"{IMG}:main-ba2e1d00-20261101"
    iid = sb.add_image(bare, days_ago=20)
    sb.add_container("secai-report-backend", iid)  # Config.Image = sha256:..., đúng như prod hiện tại
    sb.save()
    res = sb.run("gc", "--yes")
    gap(bare not in _deleted(sb, "rmi"), f"gc gọi docker rmi {bare} dù secai-report-backend đang chạy image đó")
    assert res.returncode == 0, res.stderr
    assert t["prev"] in sb.load()["images"]


# ------------------------------------------------------------------ build FULL
@xfail_gap("OPS-VER-4: build FULL (deps đổi) không gắn label org.opencontainers.image.revision; "
           "chỉ build OVERLAY có LABEL")
def test_full_build_labels_revision(tmp_path: Path) -> None:
    sb = Sandbox(tmp_path, deps_change=True)
    res = sb.run("gates")
    assert res.returncode == 0, res.stderr[-3000:]
    assert "GATES OK" in res.stdout
    builds = [a for a in sb.docker_calls() if a[:1] == ["build"]]
    assert len(builds) == 1 and "-f" not in builds[0], builds  # FULL: Dockerfile của repo
    rel = sb.releases / f"main-{sb.c2[:8]}-{TODAY}"
    assert (rel / "pip-freeze.diff").exists()
    assert all(sb.images_of()[n] == sb.prod_tag for n in TRIO), "gates không được chuyển prod"
    dockerfile = (rel / "tree" / "Dockerfile").read_text()
    gap("org.opencontainers.image.revision" in " ".join(builds[0])
        or "LABEL org.opencontainers.image.revision" in dockerfile,
        "image FULL không có label revision")
