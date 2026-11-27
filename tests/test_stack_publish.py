"""stack/publish.sh: gate -> build -> manifest -> bundle -> kiểm public -> push. Repo git tạm, docker/gh/curl/python/registry giả.
Không push thật, không chạy Docker thật, không cần checkout git của repo (chạy được trên cây git archive)."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1] / "stack"
PUBLISH = STACK / "publish.sh"
TOKEN = "gho_FAKEpublishTOKEN0123456789abcdefghijklm"   # token gh giả: chỉ được đi qua stdin của docker login
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
if c == "create": print(cidof(argv[-1])); sys.exit(0)
if c == "cp":
    cid, path = argv[1].split(":", 1)
    tag = [t for t in imgs if cidof(t) == cid][0]
    shutil.copytree(os.path.join(imgs[tag]["snap"], "src"), argv[2])
    if flag("src_drift"): open(os.path.join(argv[2], "extra.py"), "w").write("x=1\n")
    sys.exit(0)
if c in ("rm", "logs"): sys.exit(0)
if c == "login":
    open(os.path.join(F, "login.stdin"), "a").write(sys.stdin.read()); sys.exit(0)
if c == "logout": sys.exit(0)
if c == "tag":
    imgs[argv[2]] = dict(imgs[argv[1]]); put("images.json", imgs); sys.exit(0)
if c == "pull":
    remote = st("remote.json", {})
    if argv[-1] in remote and argv[-1] in imgs: sys.exit(0)
    sys.exit(1)
if c == "push":
    tag = argv[1]
    if flag("push_fail") and open(os.path.join(F, "push_fail")).read().strip() in tag:
        print("failed: NotFound: content digest sha256:" + "e" * 64); sys.exit(1)
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
cmd, ref = sys.argv[1], sys.argv[2]
if cmd == "tags":
    print("\n".join(json.load(open(os.path.join(F, "data_tags.json"))))); sys.exit(0)
remote = json.load(open(os.path.join(F, "remote.json"))) if os.path.exists(os.path.join(F, "remote.json")) else {}
if ref.startswith("ghcr.io/"):
    if ref in remote: print(remote[ref]); sys.exit(0)
    if "secai-data:" in ref and ref.split(":")[-1] in json.load(open(os.path.join(F, "data_tags.json"))):
        print("sha256:" + hashlib.sha256(ref.encode()).hexdigest()); sys.exit(0)
    sys.exit(1)
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
        (self.fake / "data_tags.json").write_text(json.dumps(["2026.11.20", "2026.11.27", "init"]))
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
        vis = {n: v for n in ("secai-agent-gateway", "secai-pentestcode-vm", "secai-stack", "secai-data")}
        vis.update(over)
        (self.fake / "visibility.json").write_text(json.dumps(vis))

    def env(self, **kw):
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp / "home"), "PUBLISH_REPO_ROOT": str(self.repo),
             "PUBLISH_PYTHON": str(self.tmp / "fakepython"), "PUBLISH_WORK_ROOT": str(self.work), "PUBLISH_MIN_FREE_GB": "0",
             "SECAI_DOCKER_DIR": str(self.dockerdir), "SECAI_DATA_ROOT": str(self.tmp / "data"),
             "SECAI_REGISTRY_HELPER": str(self.tmp / "registry-fake"), "SECAI_SEED_BLOB": str(self.tmp / "no-seed.py"),
             "LANG": "C.UTF-8"}
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
        import datetime
        return datetime.date.today().strftime("%Y.%m.%d") + "-" + self.sha[:8]

    def set(self, name, text="1"):
        (self.fake / name).write_text(text)


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


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


def test_private_package_stops_before_gates(box):
    box.set_visibility("public", **{"secai-data": "private"})
    r = box.run("publish")
    assert r.returncode == 5
    assert "secai-data=private" in r.stderr
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


def make_pkg(d, secret=False):
    """Gói dữ liệu nhỏ đúng định dạng thật: tar.zst cho các cây, snapshot Qdrant là tar, neo4j CSV."""
    (d / "qdrant").mkdir(parents=True)
    (d / "neo4j").mkdir()
    _tar(d / "qdrant" / "cve_kb_hybrid.snapshot", {"0/segments/a.bin": b"\x00payload CVE-2021-44228"})
    (d / "neo4j" / "counts.json").write_text("{}")
    for n in ("exploitdb", "poc", "poc-sources", "exploitdb-mcp-server"):
        body = b"print('hi')\n" + (b"password=hunter2-real\n" if secret and n == "poc-sources" else b"")
        _tar(d / f"{n}.tar", {f"{n}/x.py": body})
        subprocess.run(["zstd", "-q", "--rm", str(d / f"{n}.tar"), "-o", str(d / f"{n}.tar.zst")], check=True)
    (d / "MANIFEST.md").write_text("# SecAI data package\n")
    files = sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file())
    (d / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((d / f).read_bytes()).hexdigest()}  {f}\n" for f in files))


def test_publish_data_from_pkg(box):
    if not (STACK / "lib" / "secret_scan.py").exists():
        pytest.skip("secret_scan.py chưa có")
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg))
    assert r.returncode == 0, r.stderr
    assert box.pushes() == [f"{REG}/secai-data:2026.11.28"]
    df = (box.work / "data-2026.11.28" / "Dockerfile.data").read_text()
    assert df.startswith("FROM scratch") and "COPY qdrant /secai-data/qdrant" in df
    assert pkg.exists()   # gói có sẵn không bị xoá


def test_publish_data_secret_blocks(box):
    if not (STACK / "lib" / "secret_scan.py").exists():
        pytest.skip("secret_scan.py chưa có")
    pkg = box.tmp / "pkg"; make_pkg(pkg, secret=True)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg))
    assert r.returncode == 1 and "password-assign" in r.stderr and "poc-sources.tar.zst!poc-sources/x.py:2" in r.stderr
    assert "hunter2" not in r.stdout + r.stderr
    assert not box.pushes() and not any(c[0] == "build" for c in box.docker_calls())


def test_publish_data_private_refused(box):
    box.set_visibility("public", **{"secai-data": "private"})
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    r = box.run("publish-data", "--version", "2026.11.28", "--from-pkg", str(pkg))
    assert r.returncode == 5 and not box.pushes()


def test_publish_data_rejects_existing_and_bad_version(box):
    pkg = box.tmp / "pkg"; make_pkg(pkg)
    r = box.run("publish-data", "--version", "2026.11.27", "--from-pkg", str(pkg))
    assert r.returncode == 1 and "bất biến" in r.stderr
    r = box.run("publish-data", "--version", "2026-11-28", "--from-pkg", str(pkg))
    assert r.returncode == 1 and "không hợp lệ" in r.stderr


def test_publish_data_requires_neo4j_password_when_packing(box):
    r = box.run("publish-data", "--version", "2026.11.28")
    assert r.returncode == 1 and ("NEO4J_PASSWORD" in r.stderr or "03:00-05:00" in r.stderr)
    assert not box.pushes()


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
