"""stack/publish.sh: gate -> build -> manifest -> bundle -> kiểm public -> push. Repo git tạm, docker/gh/curl/python/registry giả.
Không push thật, không chạy Docker thật, không cần checkout git của repo (chạy được trên cây git archive)."""
import hashlib
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1] / "stack"
PUBLISH = STACK / "publish.sh"
TOKEN = "gho_FAKEpublishTOKEN0123456789abcdefghijklm"   # token gh giả: chỉ được đi qua stdin của docker login
REALSECRET = "real-" + "SECRETvalue-77aa"   # "secret thật" giả của máy (nguồn ks.env)
HUBTOKEN = "dckr_pat_" + "FAKEwriteToken0123456789"   # token Docker Hub giả: chỉ được đi qua stdin của docker login
DATA = "docker.io/testns/secai-data"
REG = "ghcr.io/elonma-china"


def h(s):
    return "sha256:" + hashlib.sha256(s.encode()).hexdigest()


FAKE_DOCKER = r'''#!/usr/bin/env python3
import hashlib, json, os, shutil, sys
F = "@FAKE@"
argv = sys.argv[1:]
open(os.path.join(F, "docker.log"), "a").write(json.dumps(argv) + "\n")
def st(name, default):
    p = os.path.join(F, name)
    return json.load(open(p)) if os.path.exists(p) else default
def put(name, v): json.dump(v, open(os.path.join(F, name), "w"))
imgs = st("images.json", {})
def flag(n): return os.path.exists(os.path.join(F, n))
def iid(tag): return imgs[tag]["id"]
c = argv[0]
if c == "info": print("/"); sys.exit(0)
if c == "build":
    tag = argv[argv.index("-t") + 1]; ctx = argv[-1]
    labels = dict(a.split("=", 1) for i, a in enumerate(argv) if i and argv[i - 1] == "--label")
    bargs = dict(a.split("=", 1) for i, a in enumerate(argv) if i and argv[i - 1] == "--build-arg")
    if "GIT_SHA" in bargs and "pentestcode" not in "".join(argv): labels.setdefault("gitsha", bargs["GIT_SHA"])
    if "--build-arg" in argv and "GIT_SHA" in bargs and "-f" in argv and "pentestcode-vm" in argv[argv.index("-f") + 1]:
        labels["org.opencontainers.image.revision"] = bargs["GIT_SHA"]
    if flag("build_fail") and open(os.path.join(F, "build_fail")).read().strip() in tag: sys.exit(1)
    df = argv[argv.index("-f") + 1] if "-f" in argv else os.path.join(ctx, "Dockerfile")
    imgs[tag] = {"id": "sha256:" + hashlib.sha256((tag + open(df).read()).encode()).hexdigest(), "ctx": ctx, "labels": labels,
                 "gitsha": bargs.get("GIT_SHA", "")}
    snap = os.path.join(F, "ctx-" + tag.replace("/", "_").replace(":", "_"))
    shutil.rmtree(snap, ignore_errors=True); shutil.copytree(ctx, snap, symlinks=True); imgs[tag]["snap"] = snap
    put("images.json", imgs); print(imgs[tag]["id"]); sys.exit(0)
if c == "image" and argv[1] == "inspect":
    tag = argv[-1]
    if tag not in imgs: sys.exit(1)
    fmt = argv[argv.index("--format") + 1] if "--format" in argv else (argv[argv.index("-f") + 1] if "-f" in argv else "")
    if "revision" in fmt: print(imgs[tag]["labels"].get("org.opencontainers.image.revision", ""))
    else: print(iid(tag))
    sys.exit(0)
if c == "image" and argv[1] == "rm": sys.exit(0)
if c == "run":
    if "-d" in argv:
        name = argv[argv.index("--name") + 1]; tag = argv[argv.index("uvicorn") - 1]
        put("smoke.json", {"name": name, "tag": tag, "gitsha": imgs[tag]["gitsha"]}); print("smokeid"); sys.exit(0)
    sys.exit(1 if flag("run_fail") else 0)
if c == "inspect": sys.exit(1)
def cidof(t): return "cid-" + t.replace("/", "_").replace(":", "_")
if c == "create":
    ref = argv[-2] if argv[-1] == "/none" else argv[-1]
    if ref not in imgs: sys.exit(1)
    print(cidof(ref)); sys.exit(0)
if c == "cp":
    cid, path = argv[1].split(":", 1)
    tag = [t for t in imgs if cidof(t) == cid][0]
    if path.endswith("secai-stack.json"):
        shutil.copy(os.path.join(imgs[tag]["snap"], "stack", "secai-stack.json"), argv[2]); sys.exit(0)
    shutil.copytree(os.path.join(imgs[tag]["snap"], "src"), argv[2])
    if flag("src_drift"): open(os.path.join(argv[2], "extra.py"), "w").write("x=1\n")
    sys.exit(0)
if c in ("rm", "logs"): sys.exit(0)
if c == "login":
    open(os.path.join(F, "login.stdin"), "a").write(sys.stdin.read() + "\n")
    cfg = os.environ.get("DOCKER_CONFIG", "")
    cj = os.path.join(cfg, "config.json") if cfg else ""
    seed = {"exists": bool(cj) and os.path.exists(cj)}
    if seed["exists"]:
        seed["content"] = json.load(open(cj)); seed["mode"] = oct(os.stat(cj).st_mode & 0o777)
        seed["dir_mode"] = oct(os.stat(cfg).st_mode & 0o777)
    open(os.path.join(F, "login.seed"), "a").write(json.dumps(seed) + "\n")
    open(os.path.join(F, "login.cfg"), "a").write(cfg + "\n"); sys.exit(0)
if c == "logout": sys.exit(0)
def by_digest(ref):
    d = ref.split("@", 1)[1]
    return next((t for t in imgs if imgs[t]["id"] == d), None)
if c == "tag":
    src = by_digest(argv[1]) if "@" in argv[1] else argv[1]
    imgs[argv[2]] = dict(imgs[src]); put("images.json", imgs); sys.exit(0)
if c == "pull":
    remote = st("remote.json", {})
    ref = argv[-1]
    if ref.endswith("secai-stack:stable") and flag("stable_fs"):
        imgs[ref] = {"id": remote[ref], "snap": open(os.path.join(F, "stable_fs")).read(), "labels": {}, "gitsha": ""}
        put("images.json", imgs); sys.exit(0)
    if "@" in ref:   # kéo theo digest: có trên "GHCR" (remote) và đã có nội dung cục bộ
        d = ref.split("@", 1)[1]
        sys.exit(0 if d in remote.values() and by_digest(ref) else 1)
    if ref in remote and ref in imgs: sys.exit(0)
    sys.exit(1)
if c == "push":
    tag = argv[1]
    if flag("push_fail") and open(os.path.join(F, "push_fail")).read().strip() in tag:
        print("failed: NotFound: content digest sha256:" + "e" * 64); sys.exit(1)
    if flag("push_quota") and "secai-data" in tag:
        print("denied: insufficient storage quota for this account (billing)"); sys.exit(1)
    if flag("push_timeout"):
        print("4021abcdef01: Retrying in 5 seconds\nerror: context deadline exceeded (sha256:" + "4" * 60 + "402a)"); sys.exit(1)
    d = iid(tag) if not flag("push_wrong_digest") else "sha256:" + "d" * 64
    remote = st("remote.json", {}); remote[tag] = d; put("remote.json", remote)
    print(f"{tag.split(':')[-1]}: digest: {d} size: 1234"); sys.exit(0)
sys.stderr.write("fake docker: unknown " + " ".join(argv) + "\n"); sys.exit(9)
'''

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
F = "@FAKE@"
open(os.path.join(F, "gh.log"), "a").write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1:3] == ["auth", "token"]: print("@TOKEN@"); sys.exit(0)
if sys.argv[1] == "api":
    name = sys.argv[2].rstrip("/").split("/")[-1]
    vis = json.load(open(os.path.join(F, "visibility.json")))
    print(vis.get(name, "private")); sys.exit(0)
sys.exit(9)
'''

FAKE_REG = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
F = "@FAKE@"
args = sys.argv[1:]
auth = None
if args[:1] == ["--auth"]:
    user, path = args[1].split(":", 1); auth = open(path).read().strip(); args = args[2:]
open(os.path.join(F, "reg.log"), "a").write(json.dumps(["--auth"] if auth else []) + json.dumps(args) + "\n")
cmd, ref = args[0], args[1]
def st(n, d): p = os.path.join(F, n); return json.load(open(p)) if os.path.exists(p) else d
hub = st("hub.json", {"visibility": "private", "tags": ["2026.11.20", "2026.11.27", "init"]})
remote = st("remote.json", {})
if "secai-data" in ref and auth != open(os.path.join(F, "hub_token")).read().strip(): sys.exit(1)   # private: cần token
if cmd == "hub-repo":
    if hub["visibility"] == "missing": sys.exit(1)
    print(hub["visibility"]); sys.exit(0)
if cmd == "hub-tags":
    print("\n".join(hub["tags"] + [r.split(":")[-1] for r in remote if r.startswith("docker.io/")])); sys.exit(0)
if cmd == "hub-delete-tag":
    open(os.path.join(F, "deleted.log"), "a").write(args[2] + "\n"); sys.exit(0)
if os.path.exists(os.path.join(F, "reg_error")): sys.exit(2)
hide = st("reg_hide.json", {})   # ref -> "missing" | digest khác (registry không thấy / trả sai sau push)
if ref in hide:
    if hide[ref] == "missing": sys.exit(3)
    if hide[ref] == "error": sys.exit(2)
    print(hide[ref]); sys.exit(0)
if ref in remote: print(remote[ref]); sys.exit(0)
if ref.startswith("docker.io/") and ref.split(":")[-1] in hub["tags"]:
    print("sha256:" + hashlib.sha256(ref.encode()).hexdigest()); sys.exit(0)
if ref.startswith("docker.io/"): sys.exit(3)   # repo private đọc được bằng token, tag chưa có
if ref.startswith("ghcr.io/"): sys.exit(1 if os.path.exists(os.path.join(F, "ghcr_no_pkg")) else 3)
print("sha256:" + hashlib.sha256(ref.encode()).hexdigest())
'''

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
F = "@FAKE@"
url = [a for a in sys.argv[1:] if a.startswith("http")][-1]
if url == "http://127.0.0.1:8131/health": print("{}"); sys.exit(0)
if url.endswith("/health"):
    s = json.load(open(os.path.join(F, "smoke.json")))
    sha = "deadbeef" if os.path.exists(os.path.join(F, "smoke_bad_sha")) else s["gitsha"]
    print(json.dumps({"status": "ok", "git_sha": sha})); sys.exit(0)
if url.endswith("/ai/v3/translate/languages"): sys.exit(0)
sys.exit(7)
'''

FAKE_PY = r'''#!/bin/sh
echo "pytest $*" >> "@FAKE@/pytest.calls"
[ -f .env ] && echo "dotenv-present" >> "@FAKE@/pytest.calls"
if [ -f "@FAKE@/pytest_fail" ]; then echo "1 failed, 9 passed in 1.00s"; exit 1; fi
echo "10 passed in 1.00s"
'''


def write_exec(p, text):
    p.write_text(text)
    p.chmod(0o755)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True, text=True)


class Box:
    def __init__(self, tmp):
        self.tmp = tmp
        self.fake = tmp / "fake"; self.fake.mkdir()
        self.bin = tmp / "bin"; self.bin.mkdir()
        sub = lambda t: t.replace("@FAKE@", str(self.fake)).replace("@TOKEN@", TOKEN)
        write_exec(self.bin / "docker", sub(FAKE_DOCKER))
        write_exec(self.bin / "gh", sub(FAKE_GH))
        write_exec(self.bin / "curl", sub(FAKE_CURL))
        write_exec(tmp / "registry-fake", sub(FAKE_REG))
        write_exec(tmp / "fakepython", sub(FAKE_PY))
        self.set_visibility("public")
        self.hubtok = tmp / "hub.token"
        self.hubtok.write_text(HUBTOKEN + "\n"); self.hubtok.chmod(0o600)
        (self.fake / "hub_token").write_text(HUBTOKEN)
        # nguồn secret thật giả (không bao giờ đọc secret thật của máy chạy test)
        (tmp / "ks.env").write_text(f"FAKE_API_KEY={REALSECRET}\nQDRANT_COLLECTION=cve_kb_hybrid\n")
        # repo git tạm: src/, Dockerfile, stack/ (compose thật để đọc image nền), pentestcode/
        self.repo = tmp / "repo"
        (self.repo / "src" / "secai_gateway").mkdir(parents=True)
        (self.repo / "src" / "secai_gateway" / "__init__.py").write_text("")
        (self.repo / "Dockerfile").write_text("FROM python:3.12-slim\n")
        (self.repo / "pentestcode").mkdir()
        (self.repo / "pentestcode" / "x.ts").write_text("x\n")
        st = self.repo / "stack"
        (st / "images" / "pentestcode-vm").mkdir(parents=True)
        (st / "images" / "pentestcode-vm" / "Dockerfile").write_text("FROM oven/bun:1\n")
        shutil.copy(STACK / "compose.yml", st / "compose.yml")
        (st / "README.md").write_text("bundle\n")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-qm", "init")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.sha = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.dockerdir = tmp / "secai-docker"; self.dockerdir.mkdir()
        (self.dockerdir / ".env").write_text("X=1\n")
        self.work = tmp / "releases"

    def set_visibility(self, v, **over):
        vis = {n: v for n in ("secai-agent-gateway", "secai-pentestcode-vm", "secai-stack")}
        vis.update(over)
        (self.fake / "visibility.json").write_text(json.dumps(vis))

    def env(self, **kw):
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp / "home"), "PUBLISH_REPO_ROOT": str(self.repo),
             "PUBLISH_PYTHON": str(self.tmp / "fakepython"), "PUBLISH_WORK_ROOT": str(self.work), "PUBLISH_MIN_FREE_GB": "0",
             "SECAI_DOCKER_DIR": str(self.dockerdir), "SECAI_DATA_ROOT": str(self.tmp / "data"),
             "SECAI_REGISTRY_HELPER": str(self.tmp / "registry-fake"), "SECAI_SEED_BLOB": str(self.tmp / "no-seed.py"),
             "PUBLISH_KNOWN_SECRETS": f"env:{self.tmp / 'ks.env'}", "PUBLISH_NO_FETCH": "1", "LANG": "C.UTF-8",
             "PUBLISH_NOW": "2026-11-28 12:00:00",
             "SECAI_DATA_REPO": DATA, "SECAI_DOCKERHUB_TOKEN_FILE": str(self.hubtok)}
        e.update(kw)
        return e

    def run(self, *args, **kw):
        return subprocess.run(["bash", str(PUBLISH), *args], text=True, capture_output=True, env=self.env(**kw),
                              stdin=subprocess.DEVNULL, timeout=300)

    def docker_calls(self):
        p = self.fake / "docker.log"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def pushes(self):
        return [c[1] for c in self.docker_calls() if c[0] == "push"]

    @property
    def version(self):
        return "2026.11.28-" + self.sha[:8]   # PUBLISH_NOW cố định: không phụ thuộc ngày giờ chạy test

    def set(self, name, text="1"):
        (self.fake / name).write_text(text)

    def hub(self, visibility="private", tags=("2026.11.20", "2026.11.27", "init")):
        (self.fake / "hub.json").write_text(json.dumps({"visibility": visibility, "tags": list(tags)}))


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


class _Q(BaseHTTPRequestHandler):
    points = [{"id": 1, "payload": {"text": "clean"}}, {"id": 2, "payload": {"text": "password=admin1 public poc"}}]

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        data = json.dumps({"result": {"points": _Q.points, "next_page_offset": None}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def qurl():
    _Q.points = [{"id": 1, "payload": {"text": "clean"}}, {"id": 2, "payload": {"text": "password=admin1 public poc"}}]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Q)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def manifest(box):
    return json.loads((box.work / f"stack-{box.version}" / "secai-stack.json").read_text())


def test_bash_syntax():
    assert subprocess.run(["bash", "-n", str(PUBLISH)]).returncode == 0


def test_dry_run_builds_everything_pushes_nothing(box):
    r = box.run("publish", "--dry-run")
    assert r.returncode == 0, r.stderr
    calls = box.docker_calls()
    assert not any(c[0] in ("push", "login") for c in calls)
    assert not (box.fake / "gh.log").exists() or "auth" not in (box.fake / "gh.log").read_text()
    m = manifest(box)
    v = box.version
    assert m["schema"] == 1 and m["version"] == v and m["git_sha"] == box.sha
    assert m["images"]["GATEWAY_IMAGE"]["ref"] == f"{REG}/secai-agent-gateway:{v}"
    assert m["images"]["VM_IMAGE"]["ref"] == f"{REG}/secai-pentestcode-vm:{v}"
    assert m["images"]["QDRANT_IMAGE"] == {"ref": "qdrant/qdrant:v1.10.1", "digest": h("qdrant/qdrant:v1.10.1")}
    assert m["images"]["ES_IMAGE"]["ref"] == "docker.elastic.co/elasticsearch/elasticsearch:8.15.3"
    assert m["data"]["default_version"] == "2026.11.27"   # bản mới nhất theo tag, bỏ 'init'
    assert m["data"]["image"] == DATA and m["data"]["digest"].startswith("sha256:")
    assert m["profiles"] == ["exploit"]
    builds = [c for c in calls if c[0] == "build"]
    tags = [c[c.index("-t") + 1] for c in builds]
    assert tags == [f"{REG}/secai-agent-gateway:{v}", f"{REG}/secai-pentestcode-vm:{v}", f"{REG}/secai-stack:{v}"]
    gw = builds[0]
    assert f"GIT_SHA={box.sha[:8]}" in gw and "GIT_BRANCH=main" in gw
    assert f"org.opencontainers.image.revision={box.sha}" in gw
    assert (box.work / f"stack-{v}" / "Dockerfile.bundle").read_text().startswith("FROM scratch")
    # gate pytest chạy trên cây archive, có .env tạm (như release-prod) và đã xoá sau đó
    calls_py = (box.fake / "pytest.calls").read_text()
    assert "pytest -m pytest -q -p no:cacheprovider" in calls_py
    assert not list(box.work.rglob(".env"))
    # smoke đúng tên và chỉ bind 127.0.0.1
    smoke = [c for c in calls if c[0] == "run" and "-d" in c][0]
    assert smoke[smoke.index("--name") + 1].startswith("secai-stack-gateway-smoke-")
    p = smoke[smoke.index("-p") + 1]
    assert p.startswith("127.0.0.1:")


def test_publish_real_order_and_token_only_on_stdin(box):
    r = box.run("publish", "--stable")
    assert r.returncode == 0, r.stderr
    v = box.version
    assert box.pushes() == [f"{REG}/secai-agent-gateway:{v}", f"{REG}/secai-pentestcode-vm:{v}", f"{REG}/secai-stack:{v}",
                            f"{REG}/secai-stack:stable"]
    assert TOKEN in (box.fake / "login.stdin").read_text()
    argv_all = (box.fake / "docker.log").read_text() + (box.fake / "gh.log").read_text()
    assert TOKEN not in argv_all and TOKEN not in r.stdout + r.stderr
    for p in box.work.rglob("*"):
        if p.is_file():
            assert TOKEN not in p.read_text(errors="replace"), p
    login = [c for c in box.docker_calls() if c[0] == "login"][0]
    assert "--password-stdin" in login and "ghcr.io" in login


def test_private_image_package_stops_before_gates(box):
    box.set_visibility("public", **{"secai-pentestcode-vm": "private"})
    r = box.run("publish")
    assert r.returncode == 5
    assert "secai-pentestcode-vm=private" in r.stderr and "secai-data" not in r.stderr
    assert not (box.fake / "pytest.calls").exists()
    assert not any(c[0] in ("build", "push", "login") for c in box.docker_calls())


def test_pytest_failure_blocks_everything(box):
    box.set("pytest_fail")
    r = box.run("publish")
    assert r.returncode == 1 and "pytest không xanh" in r.stderr
    assert not any(c[0] in ("build", "push", "login") for c in box.docker_calls())


def test_smoke_sha_mismatch_blocks_push(box):
    box.set("smoke_bad_sha")
    r = box.run("publish")
    assert r.returncode == 1 and "smoke" in r.stderr
    assert not box.pushes()


def test_src_drift_blocks_push(box):
    box.set("src_drift")
    r = box.run("publish")
    assert r.returncode == 1 and "/app/src" in r.stderr
    assert not box.pushes()


def test_push_digest_mismatch_stops_before_bundle(box):
    box.set("push_wrong_digest")
    r = box.run("publish")
    assert r.returncode == 1
    assert box.pushes() == [f"{REG}/secai-agent-gateway:{box.version}"]


def test_push_failure_without_seed_stops(box):
    box.set("push_fail", "secai-pentestcode-vm")
    r = box.run("publish")
    assert r.returncode == 1
    assert f"{REG}/secai-stack:{box.version}" not in box.pushes()


def test_existing_version_refused(box):
    (box.fake / "remote.json").write_text(json.dumps({f"{REG}/secai-stack:{box.version}": "sha256:" + "1" * 64}))
    r = box.run("publish")
    assert r.returncode == 1 and "bất biến" in r.stderr
    assert not any(c[0] == "build" for c in box.docker_calls())


def test_non_main_ref_warns_and_sets_branch(box):
    git(box.repo, "checkout", "-q", "-b", "feat/x")
    (box.repo / "src" / "secai_gateway" / "y.py").write_text("y=1\n")
    git(box.repo, "add", "-A"); git(box.repo, "commit", "-qm", "y")
    r = box.run("publish", "--ref", "feat/x", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "không phải origin/main" in r.stderr
    gw = [c for c in box.docker_calls() if c[0] == "build"][0]
    assert "GIT_BRANCH=feat-x" in gw


def test_bundle_secret_scan_blocks_push(box):
    if not (STACK / "lib" / "secret_scan.py").exists():
        pytest.skip("secret_scan.py chưa có")
    (box.repo / "stack" / "leak.txt").write_text("hf_" + "A" * 34 + "\n")
    git(box.repo, "add", "-A"); git(box.repo, "commit", "-qm", "leak")
    git(box.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    r = box.run("publish")
    assert r.returncode == 1 and "quét secret" in r.stderr
    assert "hf-token" in r.stderr and "A" * 34 not in r.stdout + r.stderr
    assert not box.pushes()


def _tar(path, files):
    import io, tarfile
    with tarfile.open(path, "w") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name); ti.size = len(data); t.addfile(ti, io.BytesIO(data))


def make_pkg(d, secret=False, extra=None, members=None):
    """Gói dữ liệu nhỏ đúng định dạng thật: tar.zst cho các cây, snapshot Qdrant là tar, neo4j CSV.
    members: {cây: {tên member: bytes}} thêm vào tarball của cây đó."""
    (d / "qdrant").mkdir(parents=True)
    (d / "neo4j").mkdir()
    _tar(d / "qdrant" / "cve_kb_hybrid.snapshot", {"0/segments/a.bin": b"\x00payload CVE-2021-44228"})
    (d / "neo4j" / "counts.json").write_text("{}")
    for n in ("exploitdb", "poc", "poc-sources", "exploitdb-mcp-server"):
        body = b"print('hi')\n" + (b"password=hunter2-real\n" if secret and n == "poc-sources" else b"") + (extra or {}).get(n, b"")
        _tar(d / f"{n}.tar", {f"{n}/x.py": body, **(members or {}).get(n, {})})
        subprocess.run(["zstd", "-q", "--rm", str(d / f"{n}.tar"), "-o", str(d / f"{n}.tar.zst")], check=True)
    (d / "MANIFEST.md").write_text("# SecAI data package\n\n- qdrant collection: cve_kb_hybrid (2 points)\n")
    files = sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file())
    (d / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((d / f).read_bytes()).hexdigest()}  {f}\n" for f in files))


def test_publish_data_from_pkg(box, qurl):
    if not (STACK / "lib" / "secret_scan.py").exists():
        pytest.skip("secret_scan.py chưa có")
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 0, r.stderr
    assert box.pushes() == [f"{DATA}:2026.11.28"]
    df = (box.work / "data-2026.11.28" / "Dockerfile.data").read_text()
    assert df.startswith("FROM scratch") and "COPY qdrant /secai-data/qdrant" in df
    assert pkg.exists()   # gói có sẵn không bị xoá


def test_publish_data_corpus_counted_not_blocking(box, qurl):
    # password= trong poc-sources (corpus công khai đã duyệt) chỉ được đếm và ghi vào hồ sơ phát hành
    pkg = box.tmp / "pkg"; make_pkg(pkg, secret=True)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 0, r.stderr
    rec = json.loads((box.work / "data-2026.11.28" / "data-release.json").read_text())
    assert rec["secret_scan"]["policy"] == "data"
    assert rec["secret_scan"]["corpus_counts"] == {"poc-sources": {"password-assign": 1}, "qdrant payload": {"password-assign": 1}}
    assert rec["secret_scan"]["qdrant_points"] == 2
    assert "hunter2" not in json.dumps(rec) + r.stdout + r.stderr
    # repo private trên Docker Hub: kiểm visibility và digest bằng token, token chỉ trong stdin của docker login
    reglog = (box.fake / "reg.log").read_text()
    assert '["--auth"]["hub-repo", "testns/secai-data"]' in reglog
    assert HUBTOKEN in (box.fake / "login.stdin").read_text() and HUBTOKEN not in reglog + (box.fake / "docker.log").read_text()
    login = [c for c in box.docker_calls() if c[0] == "login"][-1]
    assert login == ["login", "-u", "testns", "--password-stdin"]
    assert all(not Path(x).exists() for x in (box.fake / "login.cfg").read_text().split())


def test_publish_data_secret_outside_corpus_blocks(box, qurl):
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    (pkg / "MANIFEST.md").write_text("# SecAI data package\npassword=hunter2-real\n")
    sums = "".join(f"{hashlib.sha256((pkg / f).read_bytes()).hexdigest()}  {f}\n"
                   for f in sorted(str(p.relative_to(pkg)) for p in pkg.rglob("*") if p.is_file() and p.name != "SHA256SUMS"))
    (pkg / "SHA256SUMS").write_text(sums)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 1 and "password-assign\tMANIFEST.md:2" in r.stderr
    assert "hunter2" not in r.stdout + r.stderr
    assert not box.pushes() and not any(c[0] == "build" for c in box.docker_calls())


def test_publish_data_real_secret_stops_with_6(box, qurl):
    pkg = box.tmp / "pkg"; make_pkg(pkg, extra={"poc-sources": f"leak {REALSECRET}\n".encode()})
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 6, r.stderr
    assert "SECRET THẬT" in r.stderr and "poc-sources.tar.zst!poc-sources/x.py" in r.stderr and "ks.env#FAKE_API_KEY" in r.stderr
    assert REALSECRET not in r.stdout + r.stderr
    for f in box.work.rglob("*"):
        if f.is_file():
            assert REALSECRET not in f.read_text(errors="replace")
    assert not box.pushes()


def test_bundle_real_secret_stops_with_6(box):
    (box.repo / "stack" / "notes.txt").write_text(f"oops {REALSECRET}\n")
    git(box.repo, "add", "-A"); git(box.repo, "commit", "-qm", "leak")
    git(box.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    r = box.run("publish")
    assert r.returncode == 6 and "notes.txt:1" in r.stderr and REALSECRET not in r.stdout + r.stderr
    assert not box.pushes()


@pytest.mark.parametrize("vis", ["public", "missing"])
def test_publish_data_requires_existing_private_hub_repo(box, qurl, vis):
    box.hub(visibility=vis)   # public, hoặc chưa có (push sẽ tự tạo repo, có thể public) => từ chối
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 5 and "KHÔNG push" in r.stderr and not box.pushes()
    assert not any(c[0] == "login" for c in box.docker_calls())


def test_publish_data_requires_data_repo_and_token(box, qurl):
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl, SECAI_DATA_REPO="docker.io/testns/other")
    assert r.returncode == 1 and "SECAI_DATA_REPO" in r.stderr
    e = box.env(); del e["SECAI_DOCKERHUB_TOKEN_FILE"]
    r = subprocess.run(["bash", str(PUBLISH), "publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl],
                       text=True, capture_output=True, env=e, stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 5 and "SECAI_DOCKERHUB_TOKEN_FILE" in r.stderr and not box.pushes()
    box.hubtok.chmod(0o644)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 5 and "mode 600/400" in r.stderr


def test_publish_data_warns_over_three_tags_and_prune(box, qurl):
    box.hub(tags=["2026.11.01", "2026.11.10", "2026.11.20", "2026.11.27", "init"])
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 0, r.stderr
    assert "prune-data" in r.stderr
    r = box.run("prune-data")
    assert r.returncode == 0 and "2026.11.01" in r.stderr and not (box.fake / "deleted.log").exists()
    r = box.run("prune-data", "--keep", "3", "--yes")
    assert r.returncode == 0, r.stderr
    # 5 bản (4 cũ + 2026.11.28 vừa push), giữ 3 mới nhất
    assert (box.fake / "deleted.log").read_text().split() == ["2026.11.01", "2026.11.10"]


def test_publish_data_quota_stops_with_7(box, qurl):
    box.set("push_quota")
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 7 and "quota/billing" in r.stderr and "KHÔNG thử lách" in r.stderr
    assert len([c for c in box.docker_calls() if c[0] == "push"]) == 1   # không thử lại


def test_publish_data_rejects_existing_and_bad_version(box, qurl):
    pkg = box.tmp / "pkg"; make_pkg(pkg)   # 2026.11.27 đã có trong repo Docker Hub giả
    r = box.run("publish-data", "--version", "2026.11.27", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 1 and "bất biến" in r.stderr
    r = box.run("publish-data", "--version", "2026-11-28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 1 and "không hợp lệ" in r.stderr


def test_publish_data_requires_neo4j_password_when_packing(box):
    r = box.run("publish-data", "--version", "2026.11.28")
    assert r.returncode == 1 and "NEO4J_PASSWORD" in r.stderr and "03:00-05:00" not in r.stderr
    assert not box.pushes()


def test_publish_data_refuses_autoscan_window(box):
    r = box.run("publish-data", "--version", "2026.11.28", PUBLISH_NOW="2026-11-28 03:30:00", NEO4J_PASSWORD="x" * 12)
    assert r.returncode == 1 and "03:00-05:00" in r.stderr and not box.pushes()


def test_promote(box):
    assert box.run("publish").returncode == 0
    v = box.version
    r = box.run("promote", v)
    assert r.returncode == 0, r.stderr
    assert box.pushes()[-1] == f"{REG}/secai-stack:stable"
    remote = json.loads((box.fake / "remote.json").read_text())
    assert remote[f"{REG}/secai-stack:stable"] == remote[f"{REG}/secai-stack:{v}"]


def test_promote_bad_version(box):
    r = box.run("promote", "stable")
    assert r.returncode == 1 and not box.pushes()



def test_publish_data_from_pkg_requires_payload_scan(box):
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg))
    assert r.returncode == 1 and "--qdrant-url" in r.stderr and not box.pushes()


def test_publish_data_point_count_mismatch_blocks(box, qurl):
    _Q.points = _Q.points[:1]   # collection nguồn đã đổi so với gói (MANIFEST ghi 2 điểm)
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 1 and "số điểm Qdrant" in r.stderr and not box.pushes()


def test_transient_push_error_is_not_quota(box):
    box.set("push_timeout")
    r = box.run("publish")
    assert r.returncode == 1 and "quota" not in r.stderr.split("publish: docker push")[0].lower().split("==>")[-1]
    assert "KHÔNG thử lách" not in r.stderr


def test_seed_blob_once_per_digest_then_stops(box):
    seed = box.tmp / "seed.py"
    seed.write_text("import sys\nopen(sys.argv[0] + '.calls', 'a').write(' '.join(sys.argv[1:]) + '\\n')\n")
    box.set("push_fail", "secai-agent-gateway")
    r = box.run("publish", SECAI_SEED_BLOB=str(seed))
    assert r.returncode == 1
    calls = (box.tmp / "seed.py.calls").read_text().splitlines()
    assert calls == [f"sha256:{'e' * 64} library/python elonma-china/secai-agent-gateway"]
    assert len([c for c in box.docker_calls() if c[0] == "push"]) == 2


def test_existing_tag_check_fails_closed(box):
    reg = box.tmp / "registry-fake"
    reg.write_text("import sys\nsys.exit(2)\n")
    r = box.run("publish", "--dry-run")
    assert r.returncode == 1 and "không kiểm được" in r.stderr
    assert not any(c[0] == "build" for c in box.docker_calls())



def test_dry_run_without_data_repo_leaves_data_empty(box):
    e = box.env(); del e["SECAI_DATA_REPO"]
    r = subprocess.run(["bash", str(PUBLISH), "publish", "--dry-run"], text=True, capture_output=True, env=e,
                       stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, r.stderr
    assert manifest(box)["data"] == {"image": "", "default_version": "", "digest": ""}


def test_real_publish_requires_data_repo(box):
    e = box.env(); del e["SECAI_DATA_REPO"]
    r = subprocess.run(["bash", str(PUBLISH), "publish"], text=True, capture_output=True, env=e, stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 1 and "SECAI_DATA_REPO" in r.stderr and not box.pushes()



def test_real_publish_with_explicit_unverified_data_version(box):
    # repo dữ liệu private, chưa có token đọc: chỉ nhận --data-version tường minh, digest để trống
    e = box.env(); del e["SECAI_DOCKERHUB_TOKEN_FILE"]
    r = subprocess.run(["bash", str(PUBLISH), "publish"], text=True, capture_output=True, env=e, stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 1 and not box.pushes()
    r = subprocess.run(["bash", str(PUBLISH), "publish", "--data-version", "2026.11.27"], text=True, capture_output=True, env=e,
                       stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, r.stderr
    assert "chưa kiểm được" in r.stderr
    assert manifest(box)["data"] == {"image": DATA, "default_version": "2026.11.27", "digest": ""}



def test_hub_tag_check_only_404_means_absent(box, qurl):
    # registry trả 1 (401/403) cho repo dữ liệu đã kiểm private: không được hiểu là "tag chưa có"
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    reg = box.tmp / "registry-fake"
    reg.write_text(reg.read_text().replace('if ref.startswith("docker.io/"): sys.exit(3)', 'if ref.startswith("docker.io/"): sys.exit(1)'))
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 1 and "không kiểm được" in r.stderr and not box.pushes()


def test_ghcr_new_package_counts_as_absent(box):
    box.set("ghcr_no_pkg")
    r = box.run("publish", "--dry-run")
    assert r.returncode == 0, r.stderr


def test_prune_protects_stable_data_version(box, qurl):
    # bundle stable trỏ tới 2026.11.01: không được xoá dù cũ nhất
    box.hub(tags=["2026.11.01", "2026.11.10", "2026.11.20", "2026.11.27", "2026.11.28"])
    fs = box.tmp / "stablefs" / "stack"; fs.mkdir(parents=True)
    (fs / "secai-stack.json").write_text(json.dumps({"data": {"default_version": "2026.11.01"}}))
    remote = {f"{REG}/secai-stack:stable": "sha256:" + "5" * 64}
    (box.fake / "remote.json").write_text(json.dumps(remote))
    (box.fake / "stable_fs").write_text(str(fs.parent))
    r = box.run("prune-data", "--keep", "3", "--yes")
    assert r.returncode == 0, r.stderr
    assert (box.fake / "deleted.log").read_text().split() == ["2026.11.10"]


def test_prune_fails_closed_when_stable_unreadable(box):
    box.hub(tags=["2026.11.01", "2026.11.10", "2026.11.20", "2026.11.27", "2026.11.28"])
    (box.fake / "remote.json").write_text(json.dumps({f"{REG}/secai-stack:stable": "sha256:" + "5" * 64}))
    r = box.run("prune-data", "--keep", "3", "--yes")   # không có nội dung stable để đọc
    assert r.returncode == 1 and "KHÔNG xoá gì" in r.stderr and not (box.fake / "deleted.log").exists()



# ───────────── kiểm sau push: đọc lại ẩn danh từ GHCR, digest phải khớp manifest ─────────────
def test_published_json_has_registry_digests(box):
    r = box.run("publish", "--stable")
    assert r.returncode == 0, r.stderr
    v = box.version
    pub = json.loads((box.work / f"stack-{v}" / "published.json").read_text())
    remote = json.loads((box.fake / "remote.json").read_text())
    m = manifest(box)
    assert pub["GATEWAY_IMAGE"] == {"ref": f"{REG}/secai-agent-gateway:{v}", "registry_digest": remote[f"{REG}/secai-agent-gateway:{v}"]}
    assert pub["GATEWAY_IMAGE"]["registry_digest"] == m["images"]["GATEWAY_IMAGE"]["digest"]
    assert pub["VM_IMAGE"]["registry_digest"] == m["images"]["VM_IMAGE"]["digest"]
    assert pub["BUNDLE"]["registry_digest"] == remote[f"{REG}/secai-stack:{v}"] == pub["STABLE"]["registry_digest"]
    # các lần đọc lại là ẩn danh (không --auth) và nằm sau lần push cuối
    reg = [json.loads(l.split("]", 1)[1]) if l.startswith("[]") else None for l in (box.fake / "reg.log").read_text().splitlines()]
    assert any(x == ["digest", f"{REG}/secai-stack:{v}"] for x in reg if x)


@pytest.mark.parametrize("which", ["secai-agent-gateway", "secai-pentestcode-vm", "secai-stack"])
@pytest.mark.parametrize("how", ["missing", "sha256:" + "9" * 64])
def test_publish_fails_when_registry_does_not_show_pushed_image(box, which, how):
    v = box.version
    (box.fake / "reg_hide.json").write_text(json.dumps({f"{REG}/{which}:{v}": how}))
    r = box.run("publish")
    assert r.returncode != 0
    assert "GHCR" in r.stderr or "registry" in r.stderr
    assert not (box.work / f"stack-{v}" / "published.json").exists()


def test_publish_data_records_registry_digest(box, qurl):
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 0, r.stderr
    rec = json.loads((box.work / "data-2026.11.28" / "data-release.json").read_text())
    remote = json.loads((box.fake / "remote.json").read_text())
    assert rec["registry_digest"] == remote[f"{DATA}:2026.11.28"]



# ───────────── sửa sau review PR #73 ─────────────
def test_temp_docker_config_is_seeded_before_login(box, qurl):
    # config.json mồi có "auths": thiếu nó docker có thể ghi token vào credential helper hệ thống (còn lại sau khi xoá thư mục tạm)
    assert box.run("publish").returncode == 0
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    assert box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl).returncode == 0
    seeds = [json.loads(l) for l in (box.fake / "login.seed").read_text().splitlines()]
    assert len(seeds) >= 2
    for s in seeds:
        assert s["exists"] and "auths" in s["content"] and "credsStore" not in s["content"], s
        assert s["mode"] == "0o600" and s["dir_mode"] == "0o700", s


@pytest.mark.parametrize("how", ["env", "file"])
def test_publish_data_neo4j_password_is_a_known_secret(box, qurl, how):
    # mật khẩu Neo4j nguồn (ngắn, 9 ký tự) lọt vào poc/store của gói => mã 6, dù poc/store là corpus và luật chỉ đếm
    pw = "Qz9mPw4kL"
    pkg = box.tmp / "pkg"; make_pkg(pkg, members={"poc": {"poc/store/run1/env.txt": f"NEO4J_AUTH=neo4j/{pw}\n".encode()}})
    kw = {"NEO4J_PASSWORD": pw}
    args = ["publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl]
    if how == "file":
        f = box.tmp / "neo4j.pw"; f.write_text(pw + "\n"); f.chmod(0o600)
        args += ["--neo4j-password-file", str(f)]; kw = {}
    r = box.run(*args, **kw)
    assert r.returncode == 6, r.stderr
    assert "poc.tar.zst!poc/store/run1/env.txt:1" in r.stderr and pw not in r.stdout + r.stderr
    assert not box.pushes()
    assert not list(box.work.rglob(".neo4j-password"))   # tệp tạm chứa mật khẩu đã xoá
    for f in box.work.rglob("*"):
        if f.is_file():
            assert pw not in f.read_text(errors="replace"), f


def test_publish_data_scans_git_dirs_inside_package(box, qurl):
    # gói cũ/ghép tay có .git/config chứa URL kèm token: được COPY vào image nên phải được quét, không --exclude .git
    tok = "gh" + "p_" + "Z" * 36
    pkg = box.tmp / "pkg"
    make_pkg(pkg, members={"exploitdb-mcp-server": {"exploitdb-mcp-server/.git/config":
                                                    f"[remote]\n\turl = https://u:{tok}@github.com/x/y\n".encode()}})
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl)
    assert r.returncode == 1 and "github-token\texploitdb-mcp-server.tar.zst!exploitdb-mcp-server/.git/config:2" in r.stderr
    assert tok not in r.stdout + r.stderr and not box.pushes()


def test_default_known_sources_include_env_variants_and_config_envs(box, qurl):
    # không đặt PUBLISH_KNOWN_SECRETS: danh sách mặc định phải có .env.pentest, .env.bak*, ~/.config/*/*.env; bỏ .env.example
    home = box.tmp / "home"
    (home / "Downloads" / "CVE-DB").mkdir(parents=True)
    (home / "Downloads" / "CVE-DB" / "run_sync.sh").write_text("#!/bin/sh\n")
    (box.dockerdir / "docker-compose.yml").write_text("services: {}\n")
    (box.repo / ".env").write_text("X=1\n")
    pentest = "pentest-" + "AdminKey7731zz"
    (box.repo / ".env.pentest").write_text(f"ADMIN_API_KEY={pentest}\n")
    (box.dockerdir / ".env.bak-old-1").write_text("OLD_API_KEY=" + "oldkey-" + "Zq81PwXm0000" + "\n")
    (box.dockerdir / ".env.example").write_text("EXAMPLE_API_KEY=" + "example-" + "NotSecret999" + "\n")
    (home / ".config" / "secai-mlops").mkdir(parents=True)
    (home / ".config" / "secai-mlops" / "mlops.env").write_text("JUDGE_API_KEY=" + "judge-" + "Kk3Lm9Qq7Ww1" + "\n")
    pkg = box.tmp / "pkg"
    make_pkg(pkg, extra={"poc-sources": f"a {pentest}\nb oldkey-Zq81PwXm0000\nc judge-Kk3Lm9Qq7Ww1\nd example-NotSecret999\n".encode()})
    e = box.env(); del e["PUBLISH_KNOWN_SECRETS"]
    r = subprocess.run(["bash", str(PUBLISH), "publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg), "--qdrant-url", qurl,
                        "--dry-run"], text=True, capture_output=True, env=e, stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 6, r.stderr
    rows = [l.split("\t") for l in (box.work / "data-2026.11.28" / "scan-data.txt").read_text().splitlines()
            if l.startswith("FINDING\tknown-secret")]
    srcs = sorted(x[3].rsplit("/", 1)[-1] for x in rows)
    assert srcs == [".env.bak-old-1#OLD_API_KEY", ".env.pentest#ADMIN_API_KEY", "mlops.env#JUDGE_API_KEY"], srcs
    assert ".env.example" not in r.stderr


def test_publish_data_digest_read_error_is_not_reported_as_missing(box):
    # lỗi tạm (429/5xx, registry.py rc=2) khi đọc digest dữ liệu: báo "không đọc được", không phải "không thấy ... publish-data trước"
    (box.fake / "reg_hide.json").write_text(json.dumps({f"{DATA}:2026.11.27": "error"}))
    r = box.run("publish")
    assert r.returncode == 1 and "không đọc được digest" in r.stderr and "publish-data trước" not in r.stderr
    assert not box.pushes()


def test_usage_prints_whole_header(box):
    r = box.run()
    assert r.returncode == 2
    for needle in ("PUBLISH_KNOWN_SECRETS", "PUBLISH_NO_FETCH", "--protect", "Mã thoát"):
        assert needle in r.stderr, needle
    assert "set -euo" not in r.stderr
