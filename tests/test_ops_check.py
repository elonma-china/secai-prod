"""scripts/ops-check.py với output docker/gh/systemctl/df giả (OPS-DOC-1 và các tiêu chí tự động).

Runner giả trả dữ liệu từ một "thế giới" dựng trong test; git chạy thật trên repo tạm. Mọi lệnh
checker gọi vẫn đi qua assert_read_only, nên test cũng chứng minh checker không gọi lệnh ghi.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops-check.py"
spec = importlib.util.spec_from_file_location("ops_check", SCRIPT)
oc = importlib.util.module_from_spec(spec)
sys.modules["ops_check"] = oc
spec.loader.exec_module(oc)

NOW = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=7)))
GH = "acme/demo"
SECRET_VALUES = ("sk-live-THISMUSTNOTLEAK-01234", "neo4j/Sup3rS3cretPass", "nvdkey-ABCDEF0123456789XYZ",
                 "argvsecret-9876543210", "imgbaked-SECRETVALUE-42", "DefaultLiteralPw-77")
DAY = dt.timedelta(days=1)
TCP_HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


def hid(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def git(*a: str, cwd: Path) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *a], cwd=cwd, env=env, check=True, text=True, capture_output=True).stdout.strip()


# ------------------------------------------------------------------ thế giới giả
class World:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.repo, self.releases, self.docker_dir = tmp / "repo", tmp / "releases", tmp / "secai-docker"
        self.data, self.proc_net = tmp / "secai-data", tmp / "proc-net"
        for d in (self.releases, self.docker_dir, self.data, tmp / "cron", self.proc_net):
            d.mkdir(parents=True)
        self.set_listen([])
        self._repo()
        self.sha8 = git("rev-parse", "HEAD", cwd=self.repo)[:8]
        self.tag = f"secai-agent-gateway:main-{self.sha8}-20261127"
        self.prev = "secai-agent-gateway:main-0ab1c2d3-20261120"
        self.images: dict[str, dict] = {}
        self.containers: list[dict] = []
        self._releases()
        self._docker()
        self.df_pct = 50
        self.docker_err: str | None = None       # đặt để mọi lệnh docker lỗi
        self.systemctl_user_rc = 0                # khác 0: bus của user không truy cập được
        self.system_df = [{"Type": "Images", "Size": "40GB", "Reclaimable": "2.5GB (6%)"},
                          {"Type": "Build Cache", "Size": "1.2GB", "Reclaimable": "1.2GB"}]
        self.units = [
            {"Id": "secai-wt-prune.timer", "ActiveState": "active", "SubState": "waiting", "NRestarts": "0", "Result": "success"},
            {"Id": "secai-wt-prune.service", "ActiveState": "inactive", "SubState": "dead", "NRestarts": "0", "Result": "success"},
            {"Id": "secai-vast-llm-tunnel.service", "ActiveState": "active", "SubState": "running", "NRestarts": "2",
             "Result": "success", "ExecStart": "{ path=/usr/bin/ssh ; argv[]=/usr/bin/ssh -N host ; }"},
        ]
        self.timers = ["secai-wt-prune.timer", "secai-backup.timer", "secai-monitor.timer"]
        self.gh = {
            f"repos/{GH}": (0, {"private": False, "delete_branch_on_merge": True, "permissions": {"admin": True},
                                "security_and_analysis": {
                                    "secret_scanning": {"status": "enabled"}, "secret_scanning_push_protection": {"status": "enabled"},
                                    "dependabot_security_updates": {"status": "enabled"}}}),
            f"repos/{GH}/branches/main/protection": (0, {"required_status_checks": {"contexts": ["ci"]},
                                                       "required_pull_request_reviews": {"required_approving_review_count": 0},
                                                       "allow_force_pushes": {"enabled": False}}),
            f"repos/{GH}/rules/branches/main": (0, []),
            f"repos/{GH}/branches/main": (0, {"protected": True}),
            f"repos/{GH}/actions/workflows": (0, {"total_count": 1, "workflows": [
                {"path": ".github/workflows/ci.yml", "state": "active"}]}),
        }

    def set_listen(self, ports: list[tuple[int, str]]) -> None:
        """Socket LISTEN giả: (port, '0.0.0.0'|'::'|'127.0.0.1')."""
        v4 = v6 = TCP_HEADER
        for i, (port, ip) in enumerate(ports):
            if ip == "::":
                v6 += f"   {i}: {'0' * 32}:{port:04X} {'0' * 32}:0000 0A 0 0 0 0 0 0 0\n"
            else:
                addr = "00000000" if ip == "0.0.0.0" else "0100007F"
                v4 += f"   {i}: {addr}:{port:04X} 00000000:0000 0A 0 0 0 0 0 0 0\n"
        (self.proc_net / "tcp").write_text(v4)
        (self.proc_net / "tcp6").write_text(v6)

    def _repo(self) -> None:
        r = self.repo
        (r / "scripts").mkdir(parents=True)
        (r / "tests").mkdir()
        (r / "docs" / "ops").mkdir(parents=True)
        (r / "Dockerfile").write_text(
            "FROM python:3.12-slim@sha256:" + "a" * 64 + " AS deps\nCOPY pyproject.toml uv.lock ./\n"
            "RUN pip install uv==0.5.0 \\\n    && uv sync --frozen --no-dev --no-install-project\n")
        (r / ".dockerignore").write_text(".env\n.env.*\n.venv/\ndata/\n*.db\n")
        (r / ".gitignore").write_text(".env\n.env.*\ndata/\nlogs/\n*.log\n.venv/\n")
        shutil.copy(ROOT / "scripts" / "release-prod.sh", r / "scripts" / "release-prod.sh")
        (r / "tests" / "test_release_prod.py").write_text("".join(f"def {t}(): pass\n" for t in oc.REQUIRED_RELEASE_TESTS))
        (r / "README.md").write_text("x\n")
        (r / "demo").mkdir()
        (r / "demo" / "index.html").write_text("<p>demo</p>\n")
        (r / ".github" / "workflows").mkdir(parents=True)
        (r / ".github" / "workflows" / "ci.yml").write_text("on: pull_request\njobs:\n  t:\n    steps:\n      - run: pytest -q\n")
        git("init", "-q", "-b", "main", cwd=r)
        git("add", "-A", cwd=r)
        git("commit", "-q", "-m", "init", cwd=r)
        bare = self.tmp / "origin.git"
        git("init", "-q", "--bare", "-b", "main", str(bare), cwd=self.tmp)
        git("remote", "add", "origin", str(bare), cwd=r)
        git("push", "-q", "origin", "main", cwd=r)
        git("fetch", "-q", "origin", cwd=r)
        git("remote", "set-url", "origin", f"https://github.com/{GH}.git", cwd=r)

    def _releases(self) -> None:
        self.rel = rel = self.releases / f"main-{self.sha8}-20261127"
        rel.mkdir(mode=0o700)
        prev_rel = self.releases / "main-0ab1c2d3-20261120"
        prev_rel.mkdir(mode=0o700)
        self.prev_profile = prev_rel / "translation-main.json"
        self.prev_profile.write_text("{}")
        self.prev_profile.chmod(0o600)
        self.profile = rel / "translation-main.json"
        self.profile.write_text("{}")
        self.profile.chmod(0o600)
        bak = rel / "docker-compose.yml.bak-before"
        bak.write_text("services: {}\n")
        bak.chmod(0o600)
        for f in ("RELEASE-NOTES.md", "health-8131-before.json", "health-8131-after.json"):
            (rel / f).write_text("{}\n")
        (rel / "pytest.log").write_text("==== 12 passed, 1 xfailed in 3.00s ====\n")
        self.write_state(PREV_TAG=self.prev, NEW_TAG=self.tag, COMPOSE_BAK=str(bak),
                         TRANS_PREV_PROFILE=str(self.prev_profile), TRANS_NEW_PROFILE=str(self.profile))
        (self.releases / "LATEST-PROD").write_text(f"{rel}\n")

    def write_state(self, **kv: str) -> None:
        (self.rel / "state.env").write_text("".join(f"{k}={v}\n" for k, v in kv.items()))

    def add_image(self, ref: str, created: dt.datetime, labels: dict | None = None, env: list | None = None) -> str:
        iid = "sha256:" + hid(ref)
        self.images[ref] = {"Id": iid, "RepoTags": [ref], "Created": iso(created),
                            "Config": {"Labels": labels if labels is not None else {"org.opencontainers.image.revision": self.sha8},
                                       "Env": env or ["PATH=/usr/bin", "GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305"]}}
        return iid

    def add(self, name: str, ref: str, **kw) -> dict:
        iid = self.images[ref]["Id"] if ref in self.images else ref
        hc = kw.get("hc", True)
        c = {"Id": hid(name), "Name": "/" + name, "Image": iid, "Created": iso(NOW - 3 * DAY), "Args": [],
             "State": {"Status": kw.get("status", "running"), "Health": {"Status": "healthy"} if hc else None,
                       "FinishedAt": "0001-01-01T00:00:00Z"},
             "Config": {"Image": ref, "User": kw.get("user", "app"), "Labels": kw.get("labels", {}), "WorkingDir": "/app",
                        "Env": kw.get("env", []), "Cmd": kw.get("cmd", ["python", "serve.py"]), "Entrypoint": ["tini", "--"],
                        "Healthcheck": {"Test": ["CMD", "true"]} if hc else None},
             "HostConfig": {"RestartPolicy": {"Name": kw.get("restart", "unless-stopped")}, "PortBindings": kw.get("ports", {}),
                            "Memory": kw.get("mem", 1 << 30), "NetworkMode": kw.get("net", "bridge"),
                            "LogConfig": {"Type": "json-file", "Config": {"max-size": "50m"}}},
             "Mounts": kw.get("mounts", [])}
        self.containers.append(c)
        return c

    def _docker(self) -> None:
        self.compose = self.docker_dir / "docker-compose.yml"
        self.compose.write_text("services:\n  neo4j:\n    environment:\n      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}\n")
        self.compose.chmod(0o640)
        (self.docker_dir / ".env").write_text("NEO4J_AUTH=x\n")
        (self.docker_dir / ".env").chmod(0o600)
        main_lab = {"com.docker.compose.project": "secai-docker", "com.docker.compose.project.config_files": str(self.compose)}
        tr_lab = {"com.docker.compose.project": "secai-translation-release",
                  "com.docker.compose.project.config_files": str(self.profile)}
        data = [{"Type": "volume", "Source": "/var/lib/docker/volumes/x", "Destination": "/app/data"}]
        self.add_image(self.tag, NOW - 0.2 * DAY)
        self.add_image(self.prev, NOW - 7 * DAY)
        app_env = ["GATEWAY_DB_PATH=/app/data/gateway.db", "SESSION_STORE_BACKEND=sqlite", "SESSION_DB_PATH=/app/data/sessions.db",
                   f"GIT_SHA={self.sha8}", f"OPENAI_API_KEY={SECRET_VALUES[0]}"]
        self.add("secai-docker-gateway", self.tag, labels=main_lab, env=app_env, mounts=list(data),
                 cmd=["uvicorn", "secai_gateway.unified_app:app"], ports={"8131/tcp": [{"HostIp": "", "HostPort": "8131"}]})
        for n in ("secai-translation-gateway", "secai-translation-worker"):
            self.add(n, self.tag, labels=tr_lab, env=list(app_env), mounts=list(data), net="host",
                     cmd=["uvicorn", "secai_gateway.multilang_app:app"])
        for n in ("secai-translation-frontend", "secai-report-backend", "secai-review-frontend", "secai-review-canary"):
            ref = f"secai-agent-gateway:{n.split('-', 1)[1]}-{self.sha8}-20261125"
            self.add_image(ref, NOW - 2 * DAY)
            self.add(n, ref, labels=main_lab, ports={"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5174"}]})
        conv = "secai-doc-convert-svc:conv-1a2b3c4d-20261120"
        self.add_image(conv, NOW - 7 * DAY)
        self.add("secai-translation-converter", conv, labels=main_lab)
        pool = "secai-pentestcode:pool-1a2b3c4d-20261120"
        self.add_image(pool, NOW - 7 * DAY)
        for i in [""] + [f"-{k}" for k in range(1, 25)]:
            self.add(f"secai-docker-pentestcode{i}", pool, labels=main_lab)
        for n, ref, port in (("secai-docker-qdrant", "qdrant/qdrant:v1.10.1", "6333"), ("secai-docker-neo4j", "neo4j:5.20.1-community", "7687"),
                             ("secai-docker-redis", "redis:7.2.5-alpine", "6379"),
                             ("secai-docker-es", "docker.elastic.co/elasticsearch/elasticsearch:8.15.3", "9200")):
            self.add_image(ref, NOW - 60 * DAY, labels={})
            self.add(n, ref, labels=main_lab, user="1000", ports={f"{port}/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]})

    def c(self, name: str) -> dict:
        return next(c for c in self.containers if c["Name"] == "/" + name)

    def gw_images(self) -> list[dict]:
        return [{"Tag": ref.split(":", 1)[1], "ID": v["Id"], "CreatedAt": v["Created"]}
                for ref, v in self.images.items() if ref.startswith("secai-agent-gateway:")]


class FakeRunner(oc.Runner):
    def __init__(self, w: World) -> None:
        self.w = w
        self.calls: list[list[str]] = []
        self.real = oc.Runner()

    def run(self, argv: list[str], timeout: int = 60):
        oc.assert_read_only(argv)
        self.calls.append(list(argv))
        w, tool, a = self.w, argv[0], argv[1:]
        ok = lambda out="": oc.Proc(0, out, "")  # noqa: E731
        if tool == "git":
            return self.real.run(argv)
        if tool == "docker":
            if w.docker_err is not None:
                return oc.Proc(1, "", w.docker_err)
            if a[0] == "ps":
                return ok("".join(json.dumps({"ID": c["Id"], "Names": c["Name"][1:]}) + "\n" for c in w.containers))
            if a[0] == "inspect":
                return ok(json.dumps([c for c in w.containers if c["Id"] in a[1:]]))
            if a[:2] == ["image", "inspect"]:
                found = [im for ref, im in w.images.items() if im["Id"] in a[2:] or ref in a[2:]]
                return oc.Proc(0 if found else 1, json.dumps(found), "" if found else "Error: No such image: x")
            if a[0] == "images":
                return ok("".join(json.dumps(x) + "\n" for x in w.gw_images()))
            if a[:2] == ["system", "df"]:
                return ok("".join(json.dumps(x) + "\n" for x in w.system_df))
        if tool == "systemctl":
            user = a[:1] == ["--user"]
            if user and w.systemctl_user_rc:
                return oc.Proc(w.systemctl_user_rc, "", "Failed to connect to bus: No medium found")
            verb = (a[1:] if user else a)[0]
            if verb == "list-units":
                return ok("".join(f"{u['Id']} loaded {u['ActiveState']} {u['SubState']} x\n" for u in w.units) if user else "")
            if verb == "show":
                want = [x for x in a[a.index("show") + 1:] if not x.startswith("-") and x not in
                        ("Id", "ActiveState", "SubState", "NRestarts", "Result", "ExecStart")]
                return ok("\n\n".join("\n".join(f"{k}={v}" for k, v in u.items()) for u in w.units if u["Id"] in want))
            if verb == "list-timers":
                return ok("".join(f"Fri - - - {t} x.service\n" for t in w.timers) if user else "")
        if tool == "gh":
            rc, data = w.gh.get(a[1], (404, None))
            if rc == 404:
                return oc.Proc(1, "", "gh: Not Found (HTTP 404)")
            return oc.Proc(rc, json.dumps(data), "" if rc == 0 else "gh: error")
        if tool == "df":
            return ok(f"Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/x 100 50 50 {w.df_pct}% /\n")
        return oc.Proc(127, "", "not found")


def make_ctx(w: World, runner: FakeRunner | None = None, **cfg) -> oc.Ctx:
    conf = oc.Config(repo=w.repo, docker_dir=w.docker_dir, releases_dir=w.releases, data_root=w.data,
                     backup_dir=None, gh_repo=GH, use_gh=True, cron_globs=[str(w.tmp / "cron" / "*")], proc_net=w.proc_net)
    for k, v in cfg.items():
        setattr(conf, k, v)
    return oc.Ctx(conf, runner or FakeRunner(w), NOW)


def results(w: World, *ids: str, **cfg) -> dict[str, oc.R]:
    ctx = make_ctx(w, **cfg)
    return {r.check.id: r.r for r in oc.evaluate(ctx) if not ids or r.check.id in ids}


def one(w: World, cid: str, **cfg) -> oc.R:
    return results(w, cid, **cfg)[cid]


def cli(w: World, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    rc = oc.main(["--repo", str(w.repo), "--docker-dir", str(w.docker_dir), "--releases-dir", str(w.releases),
                  "--data-root", str(w.data), "--gh-repo", GH, "--cron-glob", str(w.tmp / "cron" / "*"),
                  "--proc-net", str(w.proc_net), *args], runner=FakeRunner(w), now=NOW, out=out)
    return rc, out.getvalue()


@pytest.fixture
def w(tmp_path: Path) -> World:
    return World(tmp_path)


# ------------------------------------------------------------------ thế giới tốt
HEALTHY = ["OPS-BLD-1", "OPS-BLD-3", "OPS-BLD-5", "OPS-REL-1", "OPS-REL-2", "OPS-REL-3", "OPS-REL-5", "OPS-REL-8",
           "OPS-VER-1", "OPS-VER-2", "OPS-VER-3", "OPS-VER-4", "OPS-VER-5", "OPS-VER-6", "OPS-VER-7", "OPS-CI-1",
           "OPS-CI-2", "OPS-CI-3", "OPS-CI-4", "OPS-CI-5", "OPS-CI-6", "OPS-BAK-1", "OPS-BAK-5", "OPS-MON-1",
           "OPS-MON-2", "OPS-MON-3", "OPS-MON-4", "OPS-MON-5", "OPS-MON-6", "OPS-SEC-1", "OPS-SEC-2", "OPS-SEC-3",
           "OPS-SEC-4", "OPS-SEC-5", "OPS-SEC-7", "OPS-SEC-8", "OPS-SEC-9", "OPS-SEC-10", "OPS-RES-1", "OPS-RES-2",
           "OPS-RES-3", "OPS-RES-4", "OPS-RES-5", "OPS-RES-6", "OPS-RES-7", "OPS-RES-8", "OPS-GIT-1", "OPS-GIT-3",
           "OPS-GIT-5"]


def test_healthy_world_passes(w: World) -> None:
    rs = results(w)
    bad = {i: (rs[i].status, rs[i].evidence) for i in HEALTHY if rs[i].status != oc.PASS}
    assert not bad, bad


def test_catalog_is_consistent() -> None:
    ids = [c.id for c in oc.CHECKS]
    assert len(ids) == len(set(ids))
    assert {c.group for c in oc.CHECKS} == set(oc.GROUPS)
    assert {c.level for c in oc.CHECKS} == {oc.MUST, oc.SHOULD}
    for c in oc.CHECKS:
        assert c.id.startswith(f"OPS-{c.group}-") and c.title


# ------------------------------------------------------------------ BLD / REL
def test_unlocked_dockerfile_fails_bld1_multiline_lock_passes(w: World) -> None:
    assert one(w, "OPS-BLD-1").status == oc.PASS  # thế giới tốt dùng RUN nhiều dòng với `\`
    (w.repo / "Dockerfile").write_text("FROM python:3.12-slim\nCOPY pyproject.toml ./\nRUN pip install .\n")
    assert one(w, "OPS-BLD-1").status == oc.FAIL
    assert oc.installs_from_lock("COPY uv.lock ./\nRUN uv export --frozen -o r.txt && uv pip sync r.txt\n")
    assert not oc.installs_from_lock("COPY uv.lock ./\nRUN uv pip sync r.txt\n")  # thiếu bước export từ lock


def test_dependency_drift_without_lock_change_fails(w: World) -> None:
    w.write_state(PREV_TAG=f"secai-agent-gateway:main-{w.sha8}-20261120", NEW_TAG=w.tag,
                  COMPOSE_BAK=str(w.rel / "docker-compose.yml.bak-before"), TRANS_PREV_PROFILE=str(w.prev_profile))
    (w.rel / "pip-freeze.diff").write_text("5c5\n< redis==8.1.0\n---\n> redis==8.2.0\n"
                                           "54c54\n< secai-agent-gateway @ file:///a.whl\n---\n> secai-agent-gateway @ file:///b.whl\n")
    r = one(w, "OPS-BLD-2")
    assert r.status == oc.FAIL and "redis" in r.evidence and "secai-agent-gateway" not in r.evidence.split("(")[1]


def test_release_record_missing_pieces(w: World) -> None:
    (w.rel / "RELEASE-NOTES.md").unlink()
    r = one(w, "OPS-REL-1")
    assert r.status == oc.FAIL and "RELEASE-NOTES.md" in r.evidence
    w.write_state(PREV_TAG=w.prev, NEW_TAG=w.tag, TRANS_PREV_PROFILE=str(w.prev_profile))  # không có COMPOSE_BAK
    assert "COMPOSE_BAK" in one(w, "OPS-REL-1").evidence
    del w.images[w.prev]
    r = one(w, "OPS-REL-2")
    assert r.status == oc.FAIL and w.prev in r.evidence


def test_rolled_back_prod_is_warn_not_fail(w: World) -> None:
    for n in oc.GATEWAY_TRIO:
        w.c(n)["Config"]["Image"] = w.prev
        w.c(n)["Image"] = w.images[w.prev]["Id"]
    (w.rel / "RELEASE-NOTES.md").unlink()
    assert one(w, "OPS-REL-1").status == oc.WARN


def test_container_outside_compose_fails(w: World) -> None:
    w.c("secai-translation-converter")["Config"]["Labels"] = {}
    r = one(w, "OPS-REL-5")
    assert r.status == oc.FAIL and "secai-translation-converter" in r.evidence


def _mount(w: World, name: str, src: Path, dest: str) -> None:
    w.c(name)["Mounts"].append({"Type": "bind", "Source": str(src), "Destination": dest})


def test_live_mounts_from_checkouts(w: World) -> None:
    _mount(w, "secai-docker-gateway", w.repo / "demo", "/app/demo")
    r = one(w, "OPS-REL-8")
    assert r.status == oc.WARN and "demo" in r.evidence  # checkout chính ở main
    _mount(w, "secai-review-canary", w.repo / "src", "/app/src")
    assert one(w, "OPS-REL-8").status == oc.FAIL


def test_mount_from_feature_worktree_fails_and_deploy_main_passes(w: World) -> None:
    feat = w.tmp / "wt-feat"
    git("worktree", "add", "-q", "-b", "feat/x", str(feat), cwd=w.repo)
    _mount(w, "secai-docker-gateway", feat / "demo", "/app/demo")
    r = one(w, "OPS-REL-8")
    assert r.status == oc.FAIL and "wt-feat@feat/x" in r.evidence
    w.c("secai-docker-gateway")["Mounts"].pop()
    dm = w.tmp / "secai-agent-gateway-deploy-main"
    git("worktree", "add", "-q", "--detach", str(dm), "origin/main", cwd=w.repo)
    _mount(w, "secai-docker-gateway", dm / "demo", "/app/demo")
    r = one(w, "OPS-REL-8")
    assert r.status == oc.PASS and "deploy-main" in r.evidence


def test_dirty_file_inside_mount_fails_git1(w: World) -> None:
    _mount(w, "secai-docker-gateway", w.repo / "demo", "/app/demo")
    (w.repo / "README.md").write_text("sửa dở\n")
    assert one(w, "OPS-GIT-1").status == oc.WARN  # sửa dở ngoài thư mục được mount
    (w.repo / "demo" / "index.html").write_text("<p>sửa dở</p>\n")
    r = one(w, "OPS-GIT-1")
    assert r.status == oc.FAIL and "demo/index.html" in r.evidence


# ------------------------------------------------------------------ VER
def test_bare_image_id_fails_and_sets_exit_code(w: World) -> None:
    c = w.c("secai-report-backend")
    c["Config"]["Image"] = c["Image"]  # tạo từ sha256:... như prod 2026-11-27
    r = one(w, "OPS-VER-1")
    assert r.status == oc.FAIL and "secai-report-backend" in r.evidence and "tag có sẵn: secai-agent-gateway:" in r.evidence
    rc, out = cli(w, "--group", "VER")
    assert rc == 1 and "FAIL  OPS-VER-1" in out


def test_trio_on_different_images_fails(w: World) -> None:
    w.c("secai-translation-worker")["Config"]["Image"] = w.prev
    w.c("secai-translation-worker")["Image"] = w.images[w.prev]["Id"]
    assert one(w, "OPS-VER-5").status == oc.FAIL


def test_revision_label_missing_inherited_or_unknown(w: World) -> None:
    w.images[w.tag]["Config"]["Labels"] = None
    r = one(w, "OPS-VER-4")
    assert r.status == oc.FAIL and "secai-docker-gateway" in r.evidence
    w.images[w.tag]["Config"]["Labels"] = {"org.opencontainers.image.revision": w.sha8}
    pool = "secai-pentestcode:pool-1a2b3c4d-20261120"
    w.images[pool]["Config"]["Labels"] = {"org.opencontainers.image.revision": "0d9b296af33f2b851fcbf4df3e9ec89751734ba4",
                                          "org.opencontainers.image.source": "https://github.com/oven-sh/bun"}
    r = one(w, "OPS-VER-4")
    assert r.status == oc.FAIL and "oven-sh/bun" in r.evidence
    w.images[pool]["Config"]["Labels"] = {"org.opencontainers.image.revision": "c4c3c8022165085a97d28ca0aa310de4109a7f90"}
    r = one(w, "OPS-VER-4")
    assert r.status == oc.WARN and "không có trong repo" in r.evidence


def test_prod_tag_must_match_latest_release_and_git_sha(w: World) -> None:
    w.write_state(PREV_TAG="x", NEW_TAG=w.prev)
    r = one(w, "OPS-VER-6")
    assert r.status == oc.FAIL and "NEW_TAG" in r.evidence
    w.write_state(PREV_TAG=w.prev, NEW_TAG=w.tag)
    c = w.c("secai-translation-worker")
    c["Config"]["Env"] = [e for e in c["Config"]["Env"] if not e.startswith("GIT_SHA=")]
    r = one(w, "OPS-VER-6")
    assert r.status == oc.FAIL and "GIT_SHA=(không có)" in r.evidence


@pytest.mark.parametrize("tag,want", [
    ("main-2dcff27c-20261127", "PASS"), ("report-c4c3c80-20261005", "PASS"),
    ("review-mvp-20261004", "WARN"), ("review-mvp-20261027b", "WARN"), ("1.3.14-tools", "WARN"),
    ("1", "FAIL"), ("latest", "FAIL"), ("", "FAIL"), ("ab-ui-restyle", "FAIL"),
])
def test_first_party_tag_classification(tag: str, want: str) -> None:
    assert oc.classify_first_party_tag(tag)[0] == want


@pytest.mark.parametrize("ref,want", [
    ("qdrant/qdrant:v1.10.1", "PASS"), ("neo4j:5.20-community", "WARN"), ("redis:7-alpine", "FAIL"),
    ("redis:latest", "FAIL"), ("redis", "FAIL"), ("redis:7-alpine@sha256:" + "b" * 64, "PASS"),
])
def test_third_party_tag_classification(ref: str, want: str) -> None:
    _, tag, digest = oc.image_repo_tag(ref)
    assert oc.classify_third_party_tag(tag, digest)[0] == want


# ------------------------------------------------------------------ CI
def test_ci_workflow_must_run_on_pull_request(w: World) -> None:
    (w.repo / ".github" / "workflows" / "ci.yml").write_text("on: schedule\njobs:\n  t:\n    steps:\n      - run: pytest -q\n")
    assert one(w, "OPS-CI-1").status == oc.FAIL


def test_failing_release_pytest_log_fails_ci4(w: World) -> None:
    (w.rel / "pytest.log").write_text("== 1 failed, 11 passed in 3.0s ==\n")
    assert one(w, "OPS-CI-4").status == oc.FAIL


def test_branch_protection_cases(w: World) -> None:
    w.gh[f"repos/{GH}/branches/main/protection"] = (0, {"required_status_checks": {"contexts": [], "checks": []},
                                                      "required_pull_request_reviews": {}, "allow_force_pushes": {"enabled": False}})
    r = one(w, "OPS-CI-2")
    assert r.status == oc.FAIL and "status check" in r.evidence
    w.gh[f"repos/{GH}/branches/main/protection"] = (404, None)
    assert one(w, "OPS-CI-2").status == oc.FAIL  # admin: 404 nghĩa là chưa bật
    w.gh[f"repos/{GH}"][1]["permissions"] = {"admin": False}
    r = one(w, "OPS-CI-2")  # không phải admin, nhánh báo protected: không kết luận được
    assert r.status == oc.SKIP and not r.unmeasured


def test_gh_unreadable_repo_is_soft_skip(w: World) -> None:
    w.gh = {}
    rs = results(w, "OPS-CI-2", "OPS-CI-3", "OPS-SEC-10")
    assert {r.status for r in rs.values()} == {oc.SKIP}
    assert not any(r.unmeasured for r in rs.values())
    assert one(w, "OPS-CI-1").status == oc.PASS  # workflow local vẫn đo được
    rc, out = cli(w, "--group", "CI")
    assert rc == 0, out
    assert one(w, "OPS-CI-3", use_gh=False).status == oc.SKIP


def test_public_repo_without_secret_scanning_fails(w: World) -> None:
    w.gh[f"repos/{GH}"][1]["security_and_analysis"]["secret_scanning"] = {"status": "disabled"}
    assert one(w, "OPS-SEC-10").status == oc.FAIL


# ------------------------------------------------------------------ BAK
def test_backup_schedule_must_cover_each_store(w: World) -> None:
    w.timers = ["secai-wt-prune.timer", "secai-qdrant-snapshot.timer"]
    r = one(w, "OPS-BAK-1")
    assert r.status == oc.FAIL and "neo4j" in r.evidence and "sqlite" in r.evidence
    w.timers = ["dpkg-db-backup.timer"]
    assert one(w, "OPS-BAK-1").status == oc.FAIL  # timer hệ thống không phải backup của secai


def test_backup_freshness_and_device(w: World, tmp_path: Path) -> None:
    b = tmp_path / "bk"
    for store in ("qdrant", "neo4j", "sqlite"):
        (b / store).mkdir(parents=True)
        (b / store / "x.bak").write_text("x")
    old = (NOW - 3 * DAY).timestamp()
    os.utime(b / "sqlite" / "x.bak", (old, old))
    r = results(w, "OPS-BAK-2", "OPS-BAK-3", backup_dir=b)
    assert r["OPS-BAK-2"].status == oc.FAIL and "sqlite" in r["OPS-BAK-2"].evidence
    assert r["OPS-BAK-3"].status == oc.FAIL and "cùng thiết bị" in r["OPS-BAK-3"].evidence  # tmp cùng đĩa
    assert one(w, "OPS-BAK-2").status == oc.FAIL  # không khai báo đích backup


def test_db_defaults_and_relative_paths(w: World) -> None:
    c = w.c("secai-docker-gateway")
    c["Config"]["Env"] = [f"GIT_SHA={w.sha8}"]  # không đặt gì: config.py mặc định sqlite ở ./data
    c["Mounts"] = []
    r = one(w, "OPS-BAK-5")
    assert r.status == oc.FAIL and "SESSION_DB_PATH=/app/data/sessions.db (mặc định)" in r.evidence
    c["Mounts"] = [{"Type": "volume", "Source": "/v", "Destination": "/app/data"}]
    c["Config"]["Env"] += ["GATEWAY_DB_PATH=data/gateway.db", "REVIEW_ENABLED=yes", "REVIEW_DB_PATH=/srv/review/r.db"]
    r = one(w, "OPS-BAK-5")
    assert r.status == oc.FAIL and "REVIEW_DB_PATH=/srv/review/r.db" in r.evidence and "GATEWAY_DB_PATH" not in r.evidence


# ------------------------------------------------------------------ MON / công cụ lỗi
def test_stopped_or_unhealthy_containers_and_short_pool(w: World) -> None:
    w.c("secai-docker-redis")["State"]["Status"] = "exited"
    w.c("secai-review-canary")["State"]["Health"] = {"Status": "unhealthy"}
    w.containers = [c for c in w.containers if c["Name"] != "/secai-docker-pentestcode-24"]
    r = one(w, "OPS-MON-1")
    assert r.status == oc.FAIL and "secai-docker-redis (exited" in r.evidence and "unhealthy" in r.evidence and "24/25" in r.evidence


def test_restart_policy_and_unbounded_logs(w: World) -> None:
    w.c("secai-report-backend")["HostConfig"]["RestartPolicy"] = {"Name": "no"}
    w.c("secai-docker-es")["HostConfig"]["LogConfig"] = {"Type": "json-file", "Config": {}}
    rs = results(w, "OPS-MON-3", "OPS-MON-6")
    assert rs["OPS-MON-3"].status == oc.FAIL and "secai-report-backend" in rs["OPS-MON-3"].evidence
    assert rs["OPS-MON-6"].status == oc.FAIL and "secai-docker-es" in rs["OPS-MON-6"].evidence


def test_restart_loop_failed_and_noisy_units(w: World) -> None:
    w.units.append({"Id": "secai-translation-ocr-model.service", "ActiveState": "active", "SubState": "running",
                    "NRestarts": "717", "Result": "success"})
    assert one(w, "OPS-MON-5").status == oc.WARN
    w.units += [{"Id": "secai-5090-tunnel.service", "ActiveState": "activating", "SubState": "auto-restart",
                 "NRestarts": "240821", "Result": "exit-code"},
                {"Id": "secai-mlops-pipeline.service", "ActiveState": "failed", "SubState": "failed", "NRestarts": "0",
                 "Result": "exit-code"}]
    r = one(w, "OPS-MON-5")
    assert r.status == oc.FAIL and "secai-5090-tunnel" in r.evidence and "secai-mlops-pipeline" in r.evidence


def test_user_bus_unavailable_skips_instead_of_passing(w: World) -> None:
    w.systemctl_user_rc = 1
    rs = results(w, "OPS-MON-4", "OPS-MON-5", "OPS-BAK-1", "OPS-RES-5", "OPS-SEC-7")
    for cid in ("OPS-MON-4", "OPS-MON-5", "OPS-BAK-1", "OPS-RES-5"):
        assert rs[cid].status == oc.SKIP and rs[cid].unmeasured, (cid, rs[cid])
    assert rs["OPS-SEC-7"].status == oc.WARN and "unit" in rs["OPS-SEC-7"].evidence
    rc, out = cli(w, "--group", "MON")
    assert rc == 3 and "mã thoát 3" in out


def test_docker_daemon_down_fails_mon1(w: World) -> None:
    w.docker_err = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
    rs = results(w, "OPS-MON-1", "OPS-VER-1", "OPS-REL-2")
    assert rs["OPS-MON-1"].status == oc.FAIL
    assert rs["OPS-VER-1"].status == oc.SKIP and rs["OPS-VER-1"].unmeasured
    assert rs["OPS-REL-2"].status == oc.SKIP, "docker lỗi không được hiểu là image PREV_TAG đã mất"
    assert cli(w, "--group", "VER,MON")[0] == 1


def test_docker_permission_denied_is_unmeasured_exit_3(w: World) -> None:
    w.docker_err = "permission denied while trying to connect to the Docker daemon socket"
    r = one(w, "OPS-MON-1")
    assert r.status == oc.SKIP and r.unmeasured
    rc, _ = cli(w, "--group", "VER")
    assert rc == 3


# ------------------------------------------------------------------ SEC
def test_publicly_bound_datastore_and_app_ports_fail(w: World) -> None:
    w.c("secai-docker-redis")["HostConfig"]["PortBindings"] = {"6379/tcp": [{"HostIp": "", "HostPort": "6379"}]}
    w.c("secai-review-frontend")["HostConfig"]["PortBindings"] = {"5174/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5174"}]}
    rs = results(w, "OPS-SEC-1", "OPS-SEC-2")
    assert rs["OPS-SEC-1"].status == oc.FAIL and "secai-docker-redis" in rs["OPS-SEC-1"].evidence
    assert rs["OPS-SEC-2"].status == oc.FAIL and "secai-review-frontend" in rs["OPS-SEC-2"].evidence


@pytest.mark.parametrize("ip,want", [("0.0.0.0", "FAIL"), ("::", "FAIL"), ("127.0.0.1", "PASS")])
def test_host_network_listener_on_all_interfaces_fails(w: World, ip: str, want: str) -> None:
    w.set_listen([(8131, "0.0.0.0"), (18185, ip)])
    r = one(w, "OPS-SEC-2")
    assert r.status == want, r.evidence
    if want == "FAIL":
        assert "secai-translation-gateway :18185" in r.evidence


def plant_secrets(w: World) -> None:
    w.compose.write_text(f"services:\n  neo4j:\n    environment:\n      NEO4J_AUTH: {SECRET_VALUES[1]}\n"
                         f"      NEO4J_PASSWORD: ${{NEO4J_PASSWORD:-{SECRET_VALUES[5]}}}\n"
                         "      REVIEW_CREDENTIALS_FILE: /run/creds.json\n")
    w.compose.chmod(0o644)
    script = w.tmp / "cron" / "run_sync.sh"
    script.write_text(f"#!/bin/bash\nexport NVD_API_KEY={SECRET_VALUES[2]}\nexport CVE_DB=/x/cve.db\n")
    keyfile = w.tmp / "cron" / "NVD_API_KEY"
    keyfile.write_text(SECRET_VALUES[2])
    keyfile.chmod(0o664)
    w.c("secai-review-canary")["Config"]["Cmd"] = ["uvicorn", "app", "--api-key", SECRET_VALUES[3]]
    w.images[w.tag]["Config"]["Env"].append(f"SERVICE_TOKEN={SECRET_VALUES[4]}")
    leak = w.releases / "old-release"
    leak.mkdir(mode=0o755)
    (leak / "profile.json").write_text(json.dumps({"environment": [f"OPENAI_API_KEY={SECRET_VALUES[0]}x"]}))
    (leak / "profile.json").chmod(0o644)
    (w.docker_dir / ".env.bak-old").write_text("NEO4J_AUTH=x\n")
    (w.docker_dir / ".env.bak-old").chmod(0o664)


def test_world_readable_follows_parent_dirs(tmp_path: Path) -> None:
    d = tmp_path / "locked"
    d.mkdir(mode=0o700)
    (d / "f").write_text("x")
    (d / "f").chmod(0o644)
    assert not oc.world_readable(d / "f")  # file 644 nhưng thư mục cha 700
    assert oc.world_readable(Path("/etc/passwd"))
    assert not oc.world_readable(tmp_path / "không-có")


def test_secret_checks_report_names_only(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    # tmp của pytest nằm dưới thư mục 700: coi "world-readable" theo mode file để thử logic OPS-SEC-3.
    monkeypatch.setattr(oc, "world_readable", lambda p: bool(Path(p).stat().st_mode & 0o004))
    plant_secrets(w)
    rs = results(w, "OPS-SEC-3", "OPS-SEC-4", "OPS-SEC-5", "OPS-SEC-7", "OPS-SEC-9")
    assert all(r.status == oc.FAIL for r in rs.values()), {k: (v.status, v.evidence) for k, v in rs.items()}
    ev4 = rs["OPS-SEC-4"].evidence
    assert "NEO4J_AUTH" in ev4 and "NEO4J_PASSWORD" in ev4 and "REVIEW_CREDENTIALS_FILE" not in ev4
    assert "NVD_API_KEY" in rs["OPS-SEC-5"].evidence and "mode 664" in rs["OPS-SEC-5"].evidence
    assert "secai-review-canary (api-key)" in rs["OPS-SEC-7"].evidence
    assert "SERVICE_TOKEN" in rs["OPS-SEC-9"].evidence and "GPG_KEY" not in rs["OPS-SEC-9"].evidence
    assert "profile.json" in rs["OPS-SEC-3"].evidence and ".env.bak-old" in rs["OPS-SEC-3"].evidence
    for r in rs.values():
        assert not any(s in r.evidence + r.fix for s in SECRET_VALUES), r.evidence


def test_compose_reference_without_default_passes_sec4(w: World) -> None:
    assert one(w, "OPS-SEC-4").status == oc.PASS  # NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
    assert oc.literal_secret("${X:-hunter2}") == "hunter2"
    assert oc.literal_secret("${X}") is None and oc.literal_secret("neo4j/${X}") is None


@pytest.mark.parametrize("fmt", [[], ["--json"]])
def test_no_secret_value_reaches_output(w: World, fmt: list[str]) -> None:
    plant_secrets(w)
    rc, out = cli(w, *fmt)
    for s in SECRET_VALUES:
        assert s not in out
    assert "NEO4J_AUTH" in out


def test_scrub_removes_remembered_values(w: World) -> None:
    ctx = make_ctx(w)
    ctx.remember_secret("'hunter2-very-secret'")
    ctx.remember_secret("abc")  # quá ngắn: bỏ qua, không làm nát output
    assert ctx.scrub("x hunter2-very-secret y abc") == "x *** y abc"


def test_fifo_in_scanned_tree_does_not_hang(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oc, "world_readable", lambda p: True)
    os.mkfifo(w.releases / "inspect-before.json", 0o644)

    def timeout(*_):
        raise TimeoutError("checker treo trên FIFO")
    old = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(20)
    try:
        r = one(w, "OPS-SEC-3")
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert r.status in (oc.PASS, oc.FAIL)


def test_tracked_secret_allowlist_is_per_file_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "r"
    (repo / "fixtures").mkdir(parents=True)
    key = "AKIA" + "Z" * 16
    (repo / "fixtures" / "fake.json").write_text(f'{{"k": "{key}"}}\n')
    (repo / "data-dir").mkdir()
    (repo / "data-dir" / "bench.jsonl").write_text(f"{key}\n{key}\n")
    git("init", "-q", "-b", "main", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "x", cwd=repo)
    monkeypatch.setattr(oc, "FAKE_SECRET_FIXTURES", {"data-dir/": None, "fixtures/fake.json": {"aws_access_key": 1}})
    assert oc.scan_tracked_secrets(repo) == []
    (repo / "fixtures" / "fake.json").write_text(f'{{"k": "{key}", "k2": "{key}"}}\n')   # thêm match vào file được miễn
    (repo / "conftest.py").write_text(f"T = '{key}'\n")                                   # file mới
    git("add", "-A", cwd=repo)
    hits = oc.scan_tracked_secrets(repo)
    assert ("conftest.py", 1, "aws_access_key") in hits
    assert any(f == "fixtures/fake.json" and "x2 > 1" in k for f, _, k in hits)
    assert all(key not in str(h) for h in hits)


# ------------------------------------------------------------------ RES / GIT
@pytest.mark.parametrize("pct,want", [(50, "PASS"), (85, "WARN"), (93, "FAIL")])
def test_disk_thresholds(w: World, pct: int, want: str) -> None:
    w.df_pct = pct
    assert one(w, "OPS-RES-1").status == want


def test_old_unmanaged_gateway_tags_fail(w: World) -> None:
    w.add_image("secai-agent-gateway:translate-opt-20261011", NOW - 40 * DAY)
    w.add_image("secai-agent-gateway:main-deadbeef-20261001", NOW - 40 * DAY)  # main-* thì gc lo (đo ở RES-5)
    r = one(w, "OPS-RES-3")
    assert r.status == oc.FAIL and "translate-opt-20261011" in r.evidence and "main-deadbeef" not in r.evidence


def test_gc_backlog_fails_res5(w: World) -> None:
    c = w.add("secai-docker-gateway-rollback-20261101", w.prev, status="exited")
    c["State"]["FinishedAt"] = iso(NOW - 10 * DAY)
    r = one(w, "OPS-RES-5")
    assert r.status == oc.FAIL and "rollback-20261101" in r.evidence
    w.containers.remove(c)
    w.add_image("secai-agent-gateway:main-deadbeef-20261001", NOW - 40 * DAY)
    r = one(w, "OPS-RES-5")
    assert r.status == oc.FAIL and "main-deadbeef" in r.evidence
    w.write_state(PREV_TAG="secai-agent-gateway:main-deadbeef-20261001", NEW_TAG=w.tag)
    del w.images[w.prev]  # PREV cũ của thế giới tốt giờ không còn là PREV: gc đã xoá
    assert one(w, "OPS-RES-5").status == oc.PASS  # image của lần release liền trước được giữ


def test_versions_ledger_is_skipped_when_absent(w: World) -> None:
    r = one(w, "OPS-VER-8")
    assert r.status == oc.SKIP and "chưa có trên origin/main" in r.evidence


# ------------------------------------------------------------------ chỉ đọc
@pytest.mark.parametrize("argv", [
    ["docker", "run", "x"], ["docker", "rm", "x"], ["docker", "rmi", "x"], ["docker", "compose", "up"],
    ["docker", "image", "rm", "x"], ["docker", "system", "prune"], ["docker", "restart", "x"],
    ["docker", "inspect", "--format", "{{.Id}}", "x"], ["docker", "--host", "tcp://x", "ps"],
    ["gh", "api", "-X", "PATCH", "repos/a/b"], ["gh", "api", "repos/a/b", "-f", "x=1"], ["gh", "api", "--method=PUT", "x"],
    ["gh", "pr", "merge", "1"], ["gh", "api", "graphql"], ["gh", "api", "repos/o/r", "-iXDELETE"],
    ["gh", "api", "repos/o/r/labels", "-fname=x"], ["gh", "api", "--paginate", "graphql", "-fquery=mutation{x}"],
    ["systemctl", "--user", "restart", "x"], ["systemctl", "reset-failed"], ["systemctl", "--user", "daemon-reload"],
    ["systemctl", "--user", "-p", "show", "restart", "secai-x.service"], ["systemctl", "--property", "show", "stop", "x"],
    ["systemctl", "--user", "show", "x", "--root", "/"], ["systemctl", "-H", "host", "show", "x"],
    ["git", "-C", "/r", "fetch"], ["git", "--no-optional-locks", "-C", "/r", "push"],
    ["git", "-C", "/r", "status", "--porcelain", "--untracked-files=no"],  # thiếu --no-optional-locks: status có thể ghi index
    ["git", "-C", "--no-optional-locks", "status", "--porcelain", "--untracked-files=no"],
    ["git", "--no-optional-locks", "-C", "/r", "diff", "--output=/tmp/x", "HEAD"],
    ["git", "--no-optional-locks", "-C", "/r", "diff", "--quiet", "a", "b", "--", "uv.lock"],  # thiếu --no-ext-diff
    ["git", "--no-optional-locks", "-C", "/r", "log", "--output=/tmp/x", "-1"],
    ["git", "--no-optional-locks", "-C", "/r", "rev-list", "--output=/tmp/x", "HEAD"],
    ["git", "--no-optional-locks", "-C", "/r", "branch", "--no-color", "newb"],
    ["git", "--no-optional-locks", "-C", "/r", "branch", "-D", "x"],
    ["git", "--no-optional-locks", "-C", "/r", "worktree", "prune"],
    ["git", "--no-optional-locks", "-C", "/r", "remote", "add", "x", "y"],
    ["git", "--no-optional-locks", "-C", "/r", "config", "core.x", "y"],
    ["rm", "-rf", "/"], ["chmod", "600", "x"], ["curl", "http://x"], [],
])
def test_read_only_guard_rejects(argv: list[str]) -> None:
    with pytest.raises(oc.ReadOnlyViolation):
        oc.assert_read_only(argv)


@pytest.mark.parametrize("argv", [
    ["docker", "ps", "-a", "--no-trunc", "--format", "{{json .}}"], ["docker", "inspect", "ab" * 32],
    ["docker", "image", "inspect", "secai-agent-gateway:main-2dcff27c-20261127"],
    ["docker", "images", "--no-trunc", "secai-agent-gateway", "--format", "{{json .}}"],
    ["docker", "system", "df", "--format", "{{json .}}"], ["gh", "api", "repos/a/b/branches/main/protection"],
    ["df", "-P", "/"], ["systemctl", "--user", "show", "x.service", "--no-pager", "-p", "Id", "-p", "NRestarts"],
    ["systemctl", "list-timers", "--all", "--plain", "--no-legend", "--no-pager"],
    ["git", "--no-optional-locks", "-C", "/r", "status", "--porcelain", "--untracked-files=no"],
    ["git", "--no-optional-locks", "-C", "/r", "branch", "-r", "--merged", "origin/main"],
    ["git", "--no-optional-locks", "-C", "/r", "worktree", "list", "--porcelain"],
    ["git", "--no-optional-locks", "-C", "/r", "diff", "--quiet", "--no-ext-diff", "--no-textconv", "a1b2", "c3d4", "--", "uv.lock"],
    ["git", "--no-optional-locks", "-C", "/r", "cat-file", "-e", "c4c3c80^{commit}"],
])
def test_read_only_guard_allows(argv: list[str]) -> None:
    oc.assert_read_only(argv)


def test_full_run_issues_only_read_only_commands(w: World) -> None:
    plant_secrets(w)
    runner = FakeRunner(w)
    out = io.StringIO()
    oc.main(["--repo", str(w.repo), "--docker-dir", str(w.docker_dir), "--releases-dir", str(w.releases),
             "--data-root", str(w.data), "--gh-repo", GH, "--proc-net", str(w.proc_net)], runner=runner, now=NOW, out=out)
    tools = {c[0] for c in runner.calls}
    assert tools <= {"docker", "gh", "git", "systemctl", "df", "stat"}, tools
    for c in runner.calls:
        oc.assert_read_only(c)


def test_checker_bug_becomes_warn_not_crash(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(ctx):
        raise KeyError("x")
    ck = next(c for c in oc.CHECKS if c.id == "OPS-RES-5")
    monkeypatch.setattr(ck, "fn", boom)
    r = one(w, "OPS-RES-5")
    assert r.status == oc.WARN and "lỗi checker" in r.evidence and r.unmeasured


# ------------------------------------------------------------------ output + mã thoát
def test_json_output_shape_and_exit_code(w: World) -> None:
    w.c("secai-docker-redis")["HostConfig"]["PortBindings"] = {"6379/tcp": [{"HostIp": "", "HostPort": "6379"}]}
    rc, out = cli(w, "--json")
    doc = json.loads(out)
    assert rc == doc["exit_code"] == 1
    ids = [r["id"] for r in doc["results"]]
    assert ids == [c.id for c in oc.CHECKS]
    assert {r["status"] for r in doc["results"]} <= {"PASS", "FAIL", "WARN", "SKIP"}
    assert set(doc["summary"]) == set(oc.GROUPS)
    sec = next(r for r in doc["results"] if r["id"] == "OPS-SEC-1")
    assert sec["status"] == "FAIL" and sec["level"] == "bắt buộc" and sec["fix"] and sec["unmeasured"] is False


def test_exit_code_rules() -> None:
    must = next(c for c in oc.CHECKS if c.level == oc.MUST and c.fn)
    should = next(c for c in oc.CHECKS if c.level == oc.SHOULD and c.fn)
    manual_must = next(c for c in oc.CHECKS if c.level == oc.MUST and c.fn is None)
    assert oc.exit_code([oc.Result(should, oc.R(oc.FAIL, "x")), oc.Result(must, oc.R(oc.WARN, "x"))]) == 0
    assert oc.exit_code([oc.Result(must, oc.R(oc.FAIL, "x"))]) == 1
    assert oc.exit_code([oc.Result(must, oc.R(oc.SKIP, "x", unmeasured=True))]) == 3
    assert oc.exit_code([oc.Result(must, oc.R(oc.SKIP, "x", unmeasured=True)), oc.Result(must, oc.R(oc.FAIL, "x"))]) == 1
    assert oc.exit_code([oc.Result(should, oc.R(oc.SKIP, "x", unmeasured=True))]) == 0
    assert oc.exit_code([oc.Result(manual_must, oc.R(oc.SKIP, "kiểm tay"))]) == 0


def test_summary_score() -> None:
    a, b, c, d = [x for x in oc.CHECKS if x.group == "BLD"][:4]
    s = oc.summarize([oc.Result(a, oc.R(oc.PASS, "")), oc.Result(b, oc.R(oc.WARN, "")),
                      oc.Result(c, oc.R(oc.FAIL, "")), oc.Result(d, oc.R(oc.SKIP, ""))])["BLD"]
    assert (s["PASS"], s["WARN"], s["FAIL"], s["SKIP"], s["score"]) == (1, 1, 1, 1, 50)
    assert s["fail_must"] == (1 if c.level == oc.MUST else 0)


@pytest.mark.parametrize("text,want", [
    ("set -euo pipefail\n", {"e", "u", "pipefail"}), ("set -uo pipefail\n", {"u", "pipefail"}),
    ("set -e\nset -u\nset -o pipefail\n", {"e", "u", "pipefail"}), ("set -eu # sh\n", {"e", "u"}),
    ("# set -e trong comment không tính\necho\n", set()),
])
def test_parse_set_flags(text: str, want: set[str]) -> None:
    assert oc.parse_set_flags(text) == want


def test_cli_runs_with_isolated_stdlib_python() -> None:
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), "--list"], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert all(c.id in p.stdout for c in oc.CHECKS)
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), "--group", "NOPE"], capture_output=True, text=True, timeout=60)
    assert p.returncode == 2 and "nhóm không có" in p.stderr


def test_cli_end_to_end_with_fake_binaries(w: World, tmp_path: Path) -> None:
    """Đường subprocess thật: docker/gh/systemctl/df giả trên PATH, gọi bằng python3 -I."""
    fb = tmp_path / "fakebin"
    fb.mkdir()
    state = tmp_path / "world.json"
    state.write_text(json.dumps({"containers": w.containers, "images": list(w.images.values()), "gw": w.gw_images(),
                                 "df": w.system_df}))
    stub = f"#!{sys.executable}\n" + r'''
import json, os, sys
w = json.load(open(os.environ["FAKE_WORLD"]))
a = sys.argv[1:]
if a[0] == "ps":
    print("\n".join(json.dumps({"ID": c["Id"], "Names": c["Name"][1:]}) for c in w["containers"]))
elif a[0] == "inspect":
    print(json.dumps([c for c in w["containers"] if c["Id"] in a[1:]]))
elif a[:2] == ["image", "inspect"]:
    f = [i for i in w["images"] if i["Id"] in a[2:] or set(i["RepoTags"]) & set(a[2:])]
    print(json.dumps(f))
    if not f:
        print("Error: No such image", file=sys.stderr)
    sys.exit(0 if f else 1)
elif a[0] == "images":
    print("\n".join(json.dumps(x) for x in w["gw"]))
elif a[:2] == ["system", "df"]:
    print("\n".join(json.dumps(x) for x in w["df"]))
'''
    (fb / "docker").write_text(stub)
    (fb / "gh").write_text("#!/bin/sh\necho 'gh: not logged in' >&2\nexit 4\n")
    (fb / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    (fb / "df").write_text("#!/bin/sh\necho 'Filesystem 1024-blocks Used Available Capacity Mounted on'\necho '/dev/x 1 1 1 42% /'\n")
    for f in fb.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{fb}:{os.environ['PATH']}", "FAKE_WORLD": str(state)}
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), "--json", "--repo", str(w.repo), "--docker-dir", str(w.docker_dir),
                        "--releases-dir", str(w.releases), "--data-root", str(w.data), "--cron-glob", str(tmp_path / "none"),
                        "--proc-net", str(w.proc_net)],
                       env=env, capture_output=True, text=True, timeout=120)
    doc = json.loads(p.stdout)
    st = {r["id"]: r["status"] for r in doc["results"]}
    for cid in ("OPS-VER-1", "OPS-VER-5", "OPS-VER-6", "OPS-RES-1", "OPS-SEC-1", "OPS-SEC-2", "OPS-MON-1", "OPS-REL-2"):
        assert st[cid] == "PASS", (cid, doc["results"])
    assert st["OPS-CI-2"] == "SKIP" and st["OPS-MON-5"] == "PASS"  # gh chưa login: SKIP mềm; systemctl rỗng: 0 unit
    assert p.returncode == doc["exit_code"]
    assert SECRET_VALUES[0] not in p.stdout


def test_real_tracked_tree_has_no_secret_outside_fixtures() -> None:
    """OPS-SEC-6 trên chính repo này: chuỗi giống khoá chỉ được nằm trong vùng fixture giả đã khai báo."""
    if not (ROOT / ".git").exists():
        pytest.skip("không có checkout git (gate release chạy trên git archive)")
    hits = oc.scan_tracked_secrets(ROOT)
    assert not hits, hits[:10]
