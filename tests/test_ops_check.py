"""scripts/ops-check.py với output docker/gh/systemctl/df giả (OPS-DOC-1 và các tiêu chí tự động).

Runner giả trả dữ liệu từ một "thế giới" dựng trong test; git chạy thật trên repo tạm. Mọi lệnh
checker gọi vẫn đi qua assert_read_only, nên test cũng chứng minh checker không gọi lệnh ghi.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import shutil
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
SECRET_VALUES = ("sk-live-THISMUSTNOTLEAK-0123456789abcdef", "neo4j/Sup3rS3cretPass", "nvdkey-ABCDEF0123456789XYZ",
                 "argvsecret-9876543210", "imgbaked-SECRETVALUE-42")
DAY = dt.timedelta(days=1)


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
        self.data = tmp / "secai-data"
        for d in (self.releases, self.docker_dir, self.data, tmp / "cron"):
            d.mkdir(parents=True)
        self._repo()
        self.sha8 = git("rev-parse", "HEAD", cwd=self.repo)[:8]
        self.tag = f"secai-agent-gateway:main-{self.sha8}-20261127"
        self.prev = "secai-agent-gateway:main-0ab1c2d3-20261120"
        self.images: dict[str, dict] = {}
        self.containers: list[dict] = []
        self._releases()
        self._docker()
        self.df_pct = 50
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
            f"repos/{GH}": (0, {"private": False, "delete_branch_on_merge": True, "security_and_analysis": {
                "secret_scanning": {"status": "enabled"}, "secret_scanning_push_protection": {"status": "enabled"},
                "dependabot_security_updates": {"status": "enabled"}}}),
            f"repos/{GH}/branches/main/protection": (0, {"required_status_checks": {"contexts": ["ci"]},
                                                       "required_pull_request_reviews": {"required_approving_review_count": 0},
                                                       "allow_force_pushes": {"enabled": False}}),
            f"repos/{GH}/rules/branches/main": (0, []),
            f"repos/{GH}/actions/workflows": (0, {"total_count": 1}),
        }

    def _repo(self) -> None:
        r = self.repo
        (r / "scripts").mkdir(parents=True)
        (r / "tests").mkdir()
        (r / "docs" / "ops").mkdir(parents=True)
        (r / "Dockerfile").write_text(
            "FROM python:3.12-slim@sha256:" + "a" * 64 + " AS deps\nCOPY pyproject.toml uv.lock ./\n"
            "RUN pip install uv==0.5.0 && uv sync --frozen --no-dev --no-install-project\n")
        (r / ".dockerignore").write_text(".env\n.env.*\n.venv/\ndata/\n*.db\n")
        (r / ".gitignore").write_text(".env\n.env.*\ndata/\nlogs/\n*.log\n.venv/\n")
        shutil.copy(ROOT / "scripts" / "release-prod.sh", r / "scripts" / "release-prod.sh")
        (r / "tests" / "test_release_prod.py").write_text("".join(f"def {t}(): pass\n" for t in oc.REQUIRED_RELEASE_TESTS))
        (r / "README.md").write_text("x\n")
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
        rel = self.releases / f"main-{self.sha8}-20261127"
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
        (rel / "state.env").write_text(f"PREV_TAG={self.prev}\nNEW_TAG={self.tag}\nCOMPOSE_BAK={bak}\n"
                                       f"TRANS_PREV_PROFILE={self.prev_profile}\nTRANS_NEW_PROFILE={self.profile}\n")
        (self.releases / "LATEST-PROD").write_text(f"{rel}\n")

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
             "Config": {"Image": ref, "User": kw.get("user", "app"), "Labels": kw.get("labels", {}),
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
        self.compose.write_text("services:\n  neo4j:\n    environment:\n      NEO4J_AUTH: ${NEO4J_AUTH}\n")
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
        self.add("secai-docker-gateway", self.tag, labels=main_lab, env=app_env, mounts=data,
                 cmd=["uvicorn", "secai_gateway.unified_app:app"], ports={"8131/tcp": [{"HostIp": "", "HostPort": "8131"}]})
        for n in ("secai-translation-gateway", "secai-translation-worker"):
            self.add(n, self.tag, labels=tr_lab, env=app_env, mounts=data, net="host", cmd=["uvicorn", "secai_gateway.multilang_app:app"])
        for n in ("secai-translation-frontend", "secai-report-backend", "secai-review-frontend", "secai-review-canary"):
            ref = f"secai-agent-gateway:{n.split('-', 1)[1]}-{self.sha8}-20261125"
            self.add_image(ref, NOW - 2 * DAY)
            self.add(n, ref, labels=main_lab, ports={"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5174"}]})
        conv = "secai-doc-convert-svc:conv-1a2b3c4d-20261120"
        self.add_image(conv, NOW - 7 * DAY)
        self.add("secai-translation-converter", conv, labels=main_lab, net="host")
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
            if a[0] == "ps":
                return ok("".join(json.dumps({"ID": c["Id"], "Names": c["Name"][1:]}) + "\n" for c in w.containers))
            if a[0] == "inspect":
                return ok(json.dumps([c for c in w.containers if c["Id"] in a[1:]]))
            if a[:2] == ["image", "inspect"]:
                found = [im for ref, im in w.images.items() if im["Id"] in a[2:] or ref in a[2:]]
                return oc.Proc(0 if found else 1, json.dumps(found), "" if found else "No such image")
            if a[0] == "images":
                return ok("".join(json.dumps(x) + "\n" for x in w.gw_images()))
            if a[:2] == ["system", "df"]:
                return ok("".join(json.dumps(x) + "\n" for x in w.system_df))
        if tool == "systemctl":
            verbs = [x for x in a if not x.startswith("-")]
            user = "--user" in a
            if verbs[0] == "list-units":
                return ok("".join(f"{u['Id']} loaded {u['ActiveState']} {u['SubState']} x\n" for u in w.units) if user else "")
            if verbs[0] == "show":
                want = [x for x in a[a.index("show") + 1:] if not x.startswith("-") and x not in
                        ("Id", "ActiveState", "SubState", "NRestarts", "Result", "ExecStart")]
                return ok("\n\n".join("\n".join(f"{k}={v}" for k, v in u.items()) for u in w.units if u["Id"] in want))
            if verbs[0] == "list-timers":
                return ok("".join(f"Fri - - - {t} x.service\n" for t in w.timers) if user else "")
        if tool == "gh":
            rc, data = w.gh.get(a[1], (1, None))
            if rc == 404:
                return oc.Proc(1, "", "gh: Not Found (HTTP 404)")
            return oc.Proc(rc, json.dumps(data), "" if rc == 0 else "gh: error")
        if tool == "df":
            return ok(f"Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/x 100 50 50 {w.df_pct}% /\n")
        return oc.Proc(127, "", "not found")


def make_ctx(w: World, runner: FakeRunner | None = None, **cfg) -> oc.Ctx:
    conf = oc.Config(repo=w.repo, docker_dir=w.docker_dir, releases_dir=w.releases, data_root=w.data,
                     backup_dir=None, gh_repo=GH, use_gh=True, cron_globs=[str(w.tmp / "cron" / "*")])
    for k, v in cfg.items():
        setattr(conf, k, v)
    return oc.Ctx(conf, runner or FakeRunner(w), NOW)


def results(w: World, *ids: str, **cfg) -> dict[str, oc.R]:
    ctx = make_ctx(w, **cfg)
    return {r.check.id: r.r for r in oc.evaluate(ctx) if not ids or r.check.id in ids}


def cli(w: World, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    rc = oc.main(["--repo", str(w.repo), "--docker-dir", str(w.docker_dir), "--releases-dir", str(w.releases),
                  "--data-root", str(w.data), "--gh-repo", GH, "--cron-glob", str(w.tmp / "cron" / "*"), *args],
                 runner=FakeRunner(w), now=NOW, out=out)
    return rc, out.getvalue()


@pytest.fixture
def w(tmp_path: Path) -> World:
    return World(tmp_path)


# ------------------------------------------------------------------ thế giới tốt
HEALTHY = ["OPS-BLD-1", "OPS-BLD-3", "OPS-BLD-5", "OPS-REL-1", "OPS-REL-2", "OPS-REL-3", "OPS-REL-5", "OPS-VER-1",
           "OPS-VER-2", "OPS-VER-3", "OPS-VER-4", "OPS-VER-5", "OPS-VER-6", "OPS-VER-7", "OPS-CI-1", "OPS-CI-2",
           "OPS-CI-3", "OPS-CI-4", "OPS-CI-5", "OPS-BAK-1", "OPS-BAK-5", "OPS-MON-1", "OPS-MON-2", "OPS-MON-3",
           "OPS-MON-4", "OPS-MON-5", "OPS-MON-6", "OPS-SEC-1", "OPS-SEC-2", "OPS-SEC-3", "OPS-SEC-4", "OPS-SEC-5",
           "OPS-SEC-7", "OPS-SEC-8", "OPS-SEC-9", "OPS-SEC-10", "OPS-RES-1", "OPS-RES-2", "OPS-RES-3", "OPS-RES-4",
           "OPS-RES-5", "OPS-RES-6", "OPS-RES-7", "OPS-RES-8", "OPS-GIT-1", "OPS-GIT-3", "OPS-GIT-5"]


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


# ------------------------------------------------------------------ từng loại lỗi
def test_bare_image_id_fails_and_sets_exit_code(w: World) -> None:
    c = w.c("secai-report-backend")
    c["Config"]["Image"] = c["Image"]  # tạo từ sha256:... như prod 2026-11-27
    r = results(w, "OPS-VER-1")["OPS-VER-1"]
    assert r.status == oc.FAIL and "secai-report-backend" in r.evidence and "tag có sẵn: secai-agent-gateway:" in r.evidence
    rc, out = cli(w, "--group", "VER")
    assert rc == 1 and "FAIL  OPS-VER-1" in out


def test_trio_on_different_images_fails(w: World) -> None:
    w.c("secai-translation-worker")["Config"]["Image"] = w.prev
    w.c("secai-translation-worker")["Image"] = w.images[w.prev]["Id"]
    assert results(w, "OPS-VER-5")["OPS-VER-5"].status == oc.FAIL


def test_missing_revision_label_fails(w: World) -> None:
    w.images[w.tag]["Config"]["Labels"] = None
    r = results(w, "OPS-VER-4")["OPS-VER-4"]
    assert r.status == oc.FAIL and "secai-docker-gateway" in r.evidence


def test_prod_tag_must_match_latest_release(w: World) -> None:
    (w.releases / "LATEST-PROD").write_text(str(w.releases / "main-0ab1c2d3-20261120") + "\n")
    (w.releases / "main-0ab1c2d3-20261120" / "state.env").write_text(f"PREV_TAG=x\nNEW_TAG={w.prev}\n")
    r = results(w, "OPS-VER-6")["OPS-VER-6"]
    assert r.status == oc.FAIL and "NEW_TAG" in r.evidence


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


@pytest.mark.parametrize("pct,want", [(50, "PASS"), (85, "WARN"), (93, "FAIL")])
def test_disk_thresholds(w: World, pct: int, want: str) -> None:
    w.df_pct = pct
    assert results(w, "OPS-RES-1")["OPS-RES-1"].status == want


def test_restart_loop_and_failed_units_fail(w: World) -> None:
    w.units += [{"Id": "secai-5090-tunnel.service", "ActiveState": "activating", "SubState": "auto-restart",
                 "NRestarts": "240821", "Result": "exit-code"},
                {"Id": "secai-mlops-pipeline.service", "ActiveState": "failed", "SubState": "failed", "NRestarts": "0",
                 "Result": "exit-code"}]
    r = results(w, "OPS-MON-5")["OPS-MON-5"]
    assert r.status == oc.FAIL and "secai-5090-tunnel" in r.evidence and "secai-mlops-pipeline" in r.evidence


def test_publicly_bound_datastore_fails(w: World) -> None:
    w.c("secai-docker-redis")["HostConfig"]["PortBindings"] = {"6379/tcp": [{"HostIp": "", "HostPort": "6379"}]}
    w.c("secai-review-frontend")["HostConfig"]["PortBindings"] = {"5174/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5174"}]}
    rs = results(w, "OPS-SEC-1", "OPS-SEC-2")
    assert rs["OPS-SEC-1"].status == oc.FAIL and "secai-docker-redis" in rs["OPS-SEC-1"].evidence
    assert rs["OPS-SEC-2"].status == oc.FAIL and "secai-review-frontend" in rs["OPS-SEC-2"].evidence


def test_container_outside_compose_fails(w: World) -> None:
    w.c("secai-translation-converter")["Config"]["Labels"] = {}
    r = results(w, "OPS-REL-5")["OPS-REL-5"]
    assert r.status == oc.FAIL and "secai-translation-converter" in r.evidence


def test_db_in_container_layer_fails(w: World) -> None:
    w.c("secai-docker-gateway")["Mounts"] = []
    r = results(w, "OPS-BAK-5")["OPS-BAK-5"]
    assert r.status == oc.FAIL and "GATEWAY_DB_PATH" in r.evidence


def test_old_unmanaged_gateway_tags_fail(w: World) -> None:
    w.add_image("secai-agent-gateway:translate-opt-20261011", NOW - 40 * DAY)
    w.add_image("secai-agent-gateway:main-deadbeef-20261001", NOW - 40 * DAY)  # main-* thì gc lo
    r = results(w, "OPS-RES-3")["OPS-RES-3"]
    assert r.status == oc.FAIL and "translate-opt-20261011" in r.evidence and "main-deadbeef" not in r.evidence


def test_gc_timer_and_stale_rollback_containers(w: World) -> None:
    c = w.add("secai-docker-gateway-rollback-20261101", w.prev, status="exited")
    c["State"]["FinishedAt"] = iso(NOW - 10 * DAY)
    r = results(w, "OPS-RES-5")["OPS-RES-5"]
    assert r.status == oc.FAIL and "rollback-20261101" in r.evidence


def test_gh_unprotected_main_fails_and_gh_errors_skip(w: World) -> None:
    w.gh[f"repos/{GH}/branches/main/protection"] = (404, None)
    assert results(w, "OPS-CI-2")["OPS-CI-2"].status == oc.FAIL
    w.gh = {}
    rs = results(w, "OPS-CI-2", "OPS-CI-3", "OPS-SEC-10")
    assert {r.status for r in rs.values()} == {oc.SKIP}
    rs = results(w, "OPS-CI-3", use_gh=False)
    assert rs["OPS-CI-3"].status == oc.SKIP


def test_versions_ledger_is_skipped_when_absent(w: World) -> None:
    r = results(w, "OPS-VER-8")["OPS-VER-8"]
    assert r.status == oc.SKIP and "chưa có trên origin/main" in r.evidence


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
    assert results(w, "OPS-BAK-2")["OPS-BAK-2"].status == oc.FAIL  # không khai báo đích backup


# ------------------------------------------------------------------ secret
def plant_secrets(w: World) -> None:
    w.compose.write_text(f"services:\n  neo4j:\n    environment:\n      NEO4J_AUTH: {SECRET_VALUES[1]}\n"
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
    (leak / "profile.json").write_text(json.dumps({"environment": [f"OPENAI_API_KEY={SECRET_VALUES[0]}"]}))
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
    assert "NEO4J_AUTH" in rs["OPS-SEC-4"].evidence and "REVIEW_CREDENTIALS_FILE" not in rs["OPS-SEC-4"].evidence
    assert "NVD_API_KEY" in rs["OPS-SEC-5"].evidence and "mode 664" in rs["OPS-SEC-5"].evidence
    assert "secai-review-canary (api-key)" in rs["OPS-SEC-7"].evidence
    assert "SERVICE_TOKEN" in rs["OPS-SEC-9"].evidence and "GPG_KEY" not in rs["OPS-SEC-9"].evidence
    assert "profile.json" in rs["OPS-SEC-3"].evidence and ".env.bak-old" in rs["OPS-SEC-3"].evidence
    for r in rs.values():
        assert not any(s in r.evidence + r.fix for s in SECRET_VALUES), r.evidence


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


# ------------------------------------------------------------------ chỉ đọc
@pytest.mark.parametrize("argv", [
    ["docker", "run", "x"], ["docker", "rm", "x"], ["docker", "rmi", "x"], ["docker", "compose", "up"],
    ["docker", "image", "rm", "x"], ["docker", "system", "prune"], ["docker", "restart", "x"],
    ["gh", "api", "-X", "PATCH", "repos/a/b"], ["gh", "api", "repos/a/b", "-f", "x=1"], ["gh", "api", "--method=PUT", "x"],
    ["gh", "pr", "merge", "1"], ["gh", "api", "graphql"],
    ["systemctl", "--user", "restart", "x"], ["systemctl", "reset-failed"], ["systemctl", "--user", "daemon-reload"],
    ["git", "-C", "/r", "fetch"], ["git", "--no-optional-locks", "-C", "/r", "push"],
    ["git", "-C", "/r", "status", "--porcelain"],  # thiếu --no-optional-locks: status có thể ghi index
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
    ["docker", "ps", "-a"], ["docker", "inspect", "x"], ["docker", "image", "inspect", "x"], ["docker", "images"],
    ["docker", "system", "df"], ["gh", "api", "repos/a/b/branches/main/protection"], ["df", "-P", "/"],
    ["systemctl", "--user", "show", "x", "-p", "Id"], ["systemctl", "list-timers", "--all"],
    ["git", "--no-optional-locks", "-C", "/r", "status", "--porcelain"],
    ["git", "--no-optional-locks", "-C", "/r", "branch", "-r", "--merged", "origin/main"],
    ["git", "--no-optional-locks", "-C", "/r", "worktree", "list", "--porcelain"],
])
def test_read_only_guard_allows(argv: list[str]) -> None:
    oc.assert_read_only(argv)


def test_full_run_issues_only_read_only_commands(w: World) -> None:
    plant_secrets(w)
    runner = FakeRunner(w)
    out = io.StringIO()
    oc.main(["--repo", str(w.repo), "--docker-dir", str(w.docker_dir), "--releases-dir", str(w.releases),
             "--data-root", str(w.data), "--gh-repo", GH], runner=runner, now=NOW, out=out)
    tools = {c[0] for c in runner.calls}
    assert tools <= {"docker", "gh", "git", "systemctl", "df", "stat"}, tools
    for c in runner.calls:
        oc.assert_read_only(c)


def test_checker_bug_becomes_warn_not_crash(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(ctx):
        raise KeyError("x")
    ck = next(c for c in oc.CHECKS if c.id == "OPS-RES-8")
    monkeypatch.setattr(ck, "fn", boom)
    r = results(w, "OPS-RES-8")["OPS-RES-8"]
    assert r.status == oc.WARN and "lỗi checker" in r.evidence


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
    assert sec["status"] == "FAIL" and sec["level"] == "bắt buộc" and sec["fix"]


def test_exit_code_only_counts_must_failures() -> None:
    must = next(c for c in oc.CHECKS if c.level == oc.MUST)
    should = next(c for c in oc.CHECKS if c.level == oc.SHOULD)
    assert oc.exit_code([oc.Result(should, oc.R(oc.FAIL, "x")), oc.Result(must, oc.R(oc.WARN, "x"))]) == 0
    assert oc.exit_code([oc.Result(must, oc.R(oc.FAIL, "x"))]) == 1


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
    print(json.dumps(f)); sys.exit(0 if f else 1)
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
                        "--releases-dir", str(w.releases), "--data-root", str(w.data), "--cron-glob", str(tmp_path / "none")],
                       env=env, capture_output=True, text=True, timeout=120)
    doc = json.loads(p.stdout)
    st = {r["id"]: r["status"] for r in doc["results"]}
    assert st["OPS-VER-5"] == st["OPS-VER-1"] == st["OPS-RES-1"] == "PASS", doc["results"]
    assert st["OPS-CI-2"] == "SKIP" and st["OPS-MON-5"] == "SKIP"  # gh chưa login, systemctl rỗng
    assert p.returncode == doc["exit_code"]
    assert SECRET_VALUES[0] not in p.stdout


def test_real_tracked_tree_has_no_secret_outside_fixtures() -> None:
    """OPS-SEC-6 trên chính repo này: chuỗi giống khoá chỉ được nằm trong vùng fixture giả đã khai báo."""
    if not (ROOT / ".git").exists():
        pytest.skip("không có checkout git (gate release chạy trên git archive)")
    hits = oc.scan_tracked_secrets(ROOT)
    assert not hits, hits[:10]


def test_fixture_copy_is_independent() -> None:
    # Bảo đảm thế giới giả không bị test khác sửa chung (World dựng mới cho từng test).
    assert copy.deepcopy(oc.REQUIRED_RELEASE_TESTS) == oc.REQUIRED_RELEASE_TESTS
