"""scripts/versions.py và lời gọi ghi sổ trong scripts/release-prod.sh, chạy trên docker/git giả.

`docker` giả trả `docker inspect` đầy đủ (kể cả env chứa secret) từ một file JSON mà test sửa được;
`git` giả trả số commit `behind-main`. SECAI_RELEASES_DIR luôn là thư mục tạm, không bao giờ là sổ thật.
"""
from __future__ import annotations

import fcntl
import http.client
import importlib.util
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "versions.py"
RELEASE = ROOT / "scripts" / "release-prod.sh"
SECRET = "sk-FAKE-SECRET-7f3c9e1d55aa"
GW_TAG = "secai-agent-gateway:main-2dcff27c-20261127"

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, re, sys
STATE = __STATE__
args = sys.argv[1:]
with open(STATE + "/calls.jsonl", "a") as f:
    f.write(json.dumps(args) + "\n")
w = json.load(open(STATE + "/docker.json"))
HEX = re.compile(r"(?:sha256:)?([0-9a-f]{12,64})")
def norm(r):
    name, at, dig = r.partition("@")
    if ":" not in name.rsplit("/", 1)[-1] and not at:
        name += ":latest"
    for p in ("docker.io/library/", "docker.io/", "library/"):
        if name.startswith(p):
            name = name[len(p):]
            break
    return name + ("@" + dig if at else "")
def find_img(ref):
    m = HEX.fullmatch(ref)
    for i in w["images"]:
        if m and i["Id"].split(":")[-1].startswith(m.group(1)):
            return i
        if not m and (norm(ref) in map(norm, i["RepoTags"]) or norm(ref) in map(norm, i["RepoDigests"])):
            return i
def emit(found, missing):
    print(json.dumps(found))
    for x in missing:
        print("Error: No such object: " + x, file=sys.stderr)
    sys.exit(1 if missing else 0)
if args[:1] == ["ps"]:
    print("\n".join(c["Name"].lstrip("/") for c in w["containers"]))
    sys.exit(0)
if args[:1] == ["inspect"]:
    names = [a for i, a in enumerate(args[1:], 1) if not a.startswith("-") and args[i - 1] != "--type"]
    by = {c["Name"].lstrip("/"): c for c in w["containers"]}
    emit([by[n] for n in names if n in by], [n for n in names if n not in by])
if args[:2] == ["image", "inspect"]:
    found = [(r, find_img(r)) for r in args[2:]]
    emit([i for _, i in found if i], [r for r, i in found if not i])
sys.exit(0)
'''

FAKE_GIT = r'''#!/usr/bin/env python3
import json, sys
STATE = __STATE__
args = sys.argv[1:]
counts = json.load(open(STATE + "/git.json"))
if "rev-list" in args:
    rev = args[-1].split("..")[0]
    if rev in counts:
        print(counts[rev])
        sys.exit(0)
    print("fatal: bad revision", file=sys.stderr)
    sys.exit(128)
sys.exit(0)
'''


def hid(c: str) -> str:
    return "sha256:" + c * 64


def container(name: str, ref: str, image: str, *, env=(), labels=None, running=True, health="healthy", project="secai-docker"):
    return {
        "Id": (name.encode().hex() * 8)[:64], "Name": "/" + name, "Image": image,
        "Config": {"Image": ref, "Env": [f"OPENAI_API_KEY={SECRET}", "PATH=/usr/local/bin:/usr/bin", f"DB_URL=postgres://u:{SECRET}@db/x", *env],
                   "Labels": {"com.docker.compose.project": project, "com.example.token": SECRET, **(labels or {})},
                   "Cmd": ["--api-key", SECRET]},
        "State": {"Status": "running" if running else "exited", "Running": running,
                  "StartedAt": "2026-11-27T07:32:32.30914107Z", **({"Health": {"Status": health}} if health else {})},
        "HostConfig": {"Binds": [f"/srv/{SECRET}:/x"]}, "Args": [SECRET],
    }


def image(iid: str, tags, *, created="2026-11-27T06:40:01.979808192Z", labels=None, env=()):
    return {"Id": iid, "RepoTags": list(tags), "RepoDigests": [t.rsplit(":", 1)[0] + "@" + iid for t in tags[:1]],
            "Created": created, "Config": {"Labels": labels, "Env": [f"HF_TOKEN={SECRET}", *env]}}


def world() -> dict:
    """Mô phỏng máy prod ngày 2026-11-27 (rút gọn pool pentestcode còn 3 container)."""
    rev_fe, rev_rep, rev_pc = "4c590be8" + "a" * 32, "c4c3c802" + "b" * 32, "0d9b296a" + "c" * 32
    gw_env = ("GIT_SHA=2dcff27c", "GIT_BRANCH=main")
    images = [
        image(hid("1"), [GW_TAG], env=gw_env),
        image(hid("2"), ["secai-agent-gateway:ab-ui-restyle-20261007"], labels={"org.opencontainers.image.revision": rev_fe}),
        image(hid("3"), ["secai-agent-gateway:report-export-20261005"], labels={"org.opencontainers.image.revision": rev_rep}),
        image(hid("4"), ["secai-agent-gateway:review-mvp-20261004"]),
        image(hid("5"), ["secai-agent-gateway:review-mvp-20261027b"]),
        image(hid("6"), ["secai-doc-convert-svc:1"]),
        image(hid("7"), ["secai-pentestcode:1.3.14-tools"], labels={"org.opencontainers.image.revision": rev_pc}),
        image(hid("8"), ["qdrant/qdrant:v1.10.1"]),
        image(hid("9"), ["neo4j:5.20-community"]),
        image(hid("a"), ["redis:7-alpine"]),
        image(hid("b"), ["docker.elastic.co/elasticsearch/elasticsearch:8.15.3"]),
        image(hid("c"), ["secai-agent-gateway:dev-foo"]),
        image(hid("d"), ["poclab/thing:latest"]),
    ]
    conts = [
        container("secai-docker-gateway", GW_TAG, hid("1"), env=gw_env),
        container("secai-translation-gateway", GW_TAG, hid("1"), env=gw_env, project="secai-translation-release"),
        container("secai-translation-worker", GW_TAG, hid("1"), env=gw_env, project="secai-translation-release"),
        container("secai-translation-frontend", hid("2"), hid("2"), labels={"org.opencontainers.image.revision": rev_fe}),
        container("secai-report-backend", hid("3"), hid("3"), labels={"org.opencontainers.image.revision": rev_rep}),
        container("secai-review-frontend", "secai-agent-gateway:review-mvp-20261004", hid("4")),
        container("secai-review-canary", "secai-agent-gateway:review-mvp-20261027b", hid("5")),
        container("secai-translation-converter", "secai-doc-convert-svc:1", hid("6"), health=None, project=None),
        *(container(n, "secai-pentestcode:1.3.14-tools", hid("7"), labels={"org.opencontainers.image.revision": rev_pc})
          for n in ("secai-docker-pentestcode", "secai-docker-pentestcode-1", "secai-docker-pentestcode-2")),
        container("secai-docker-qdrant", "qdrant/qdrant:v1.10.1", hid("8")),
        container("secai-docker-neo4j", "neo4j:5.20-community", hid("9")),
        container("secai-docker-redis", "redis:7-alpine", hid("a")),
        container("secai-docker-es", "docker.elastic.co/elasticsearch/elasticsearch:8.15.3", hid("b")),
        container("secai-gw-dev-foo-devgw-1", "secai-agent-gateway:dev-foo", hid("c"), project="secai-gw-dev-foo"),
        container("poclab-unrelated-target", "poclab/thing:latest", hid("d")),
    ]
    return {"containers": conts, "images": images}


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.bin = root / "bin"
        self.releases = root / "releases"
        self.bin.mkdir()
        self.releases.mkdir()
        for name, src in (("docker", FAKE_DOCKER), ("git", FAKE_GIT)):
            p = self.bin / name
            p.write_text(src.replace("__STATE__", repr(str(root))))
            p.chmod(0o755)
        self.world = world()
        self.save()
        self.git_counts({"2dcff27c": 0})
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "SECAI_RELEASES_DIR": str(self.releases)}
        self.env.pop("SECAI_REPO", None)
        self.outputs: list[str] = []

    @property
    def ledger(self) -> Path:
        return self.releases / "versions.jsonl"

    def save(self) -> None:
        (self.root / "docker.json").write_text(json.dumps(self.world))

    def git_counts(self, counts: dict) -> None:
        (self.root / "git.json").write_text(json.dumps(counts))

    def cont(self, name: str) -> dict:
        return next(c for c in self.world["containers"] if c["Name"] == "/" + name)

    def run(self, *args: str, code: int = 0) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], env=self.env, text=True, capture_output=True, timeout=60)
        self.outputs += [r.stdout, r.stderr]
        assert r.returncode == code, f"rc={r.returncode}\n{r.stdout}\n{r.stderr}"
        return r

    def status(self, *args: str, code: int = 0) -> dict:
        data = json.loads(self.run("status", "--json", *args, code=code).stdout)
        return {s["id"]: s for s in data["services"]}

    def entries(self) -> list[dict]:
        return [json.loads(line) for line in self.ledger.read_text().splitlines()] if self.ledger.exists() else []

    def docker_calls(self) -> list[list[str]]:
        p = self.root / "calls.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


@pytest.fixture()
def sb(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path)


# ---------------------------------------------------------------- catalog


def test_catalog_covers_services_without_secrets_or_absolute_paths() -> None:
    raw = (ROOT / "deploy" / "services.json").read_text()
    data = json.loads(raw)
    assert "/home/" not in raw and not re.search(r'"/[a-z]', raw)
    ids = [s["id"] for s in data["services"]]
    assert len(ids) == len(set(ids))
    names = {c for s in data["services"] for c in s.get("containers", [])}
    for n in ("secai-docker-gateway", "secai-translation-gateway", "secai-translation-worker", "secai-translation-frontend",
              "secai-report-backend", "secai-review-frontend", "secai-review-canary", "secai-translation-converter",
              "secai-docker-qdrant", "secai-docker-neo4j", "secai-docker-redis", "secai-docker-es"):
        assert n in names, n
    roles = {s["id"]: s["role"] for s in data["services"]}
    assert roles["gateway"] == "prod" and roles["review-canary"] == "canary" and roles["dev-stacks"] == "dev"
    gw = next(s for s in data["services"] if s["id"] == "gateway")
    assert "release-prod.sh run --yes" in gw["upgrade"] and "release-prod.sh rollback --yes" in gw["rollback"]
    pool = next(s for s in data["services"] if "container_pattern" in s and s["role"] == "dependency")
    assert re.fullmatch(pool["container_pattern"], "secai-docker-pentestcode-24")
    assert re.fullmatch(pool["container_pattern"], "secai-docker-pentestcode")
    assert not re.fullmatch(pool["container_pattern"], "secai-docker-pentestcode-smoke")


# ---------------------------------------------------------------- status và cờ


def test_status_flags_match_the_real_machine(sb: Sandbox) -> None:
    s = sb.status()
    assert s["gateway"]["flags"] == [] and s["gateway"]["version"] == GW_TAG
    assert s["gateway"]["running"] == 3 and s["gateway"]["health"] == "healthy 3/3"
    assert s["translation-frontend"]["flags"] == ["bare-id"]
    assert s["translation-frontend"]["version"] == "2" * 12  # tag ab-ui-restyle-… không bất biến → ID ngắn
    assert s["report-backend"]["flags"] == ["bare-id"]
    assert s["review-frontend"]["flags"] == ["floating-tag", "no-revision"]
    assert s["review-canary"]["flags"] == ["floating-tag", "no-revision"]
    assert s["translation-converter"]["flags"] == ["floating-tag"]  # không dựng từ repo: không đòi revision
    assert s["pentestcode-pool"]["flags"] == ["floating-tag:minor"] and s["pentestcode-pool"]["running"] == 3
    assert s["qdrant"]["flags"] == [] and s["elasticsearch"]["flags"] == []
    assert s["neo4j"]["flags"] == ["floating-tag"] and s["redis"]["flags"] == ["floating-tag"]
    assert "dev-stacks" not in s  # dev chỉ hiện khi hỏi


def test_revision_from_label_versus_env(sb: Sandbox) -> None:
    s = sb.status()
    gw = s["gateway"]["containers"][0]
    assert (gw["revision"], gw["revision_source"], gw["branch"]) == ("2dcff27c", "env", "main")
    fe = s["translation-frontend"]["containers"][0]
    assert fe["revision"].startswith("4c590be8") and fe["revision_source"] == "label"
    # label thắng env khi có cả hai
    c = sb.cont("secai-docker-gateway")
    c["Config"]["Labels"]["org.opencontainers.image.revision"] = "abcdef1234"
    sb.save()
    gw = sb.status()["gateway"]["containers"][0]
    assert (gw["revision"], gw["revision_source"]) == ("abcdef1234", "label")


def test_tag_moved(sb: Sandbox) -> None:
    """Tag review-mvp-20261027b bị gắn sang image khác: container vẫn chạy image cũ."""
    new = image(hid("e"), ["secai-agent-gateway:review-mvp-20261027b"])
    old = next(i for i in sb.world["images"] if i["Id"] == hid("5"))
    old["RepoTags"] = ["secai-agent-gateway:review-55aa55a-20261027"]
    sb.world["images"].append(new)
    sb.save()
    canary = sb.status()["review-canary"]
    assert "tag-moved" in canary["flags"]
    assert canary["version"] == "secai-agent-gateway:review-55aa55a-20261027"  # tag bất biến còn lại của image
    old["RepoTags"] = []
    sb.save()
    canary = sb.status()["review-canary"]
    assert "tag-moved" in canary["flags"] and canary["version"] == "5" * 12


def test_group_drift(sb: Sandbox) -> None:
    sb.world["images"].append(image(hid("e"), ["secai-agent-gateway:main-033b2aa3-20261123"]))
    w = sb.cont("secai-translation-worker")
    w["Config"]["Image"], w["Image"] = "secai-agent-gateway:main-033b2aa3-20261123", hid("e")
    sb.save()
    gw = sb.status()["gateway"]
    assert "group-drift" in gw["flags"] and gw["version"] == "2 bản khác nhau"
    text = sb.run("status").stdout
    assert "gateway ×2" in text and "gateway  " in text and "group-drift" in text


def test_missing_container_and_stopped_container(sb: Sandbox) -> None:
    sb.world["containers"] = [c for c in sb.world["containers"] if c["Name"] != "/secai-translation-converter"]
    rep = sb.cont("secai-report-backend")
    rep["State"].update(Status="exited", Running=False)
    sb.save()
    s = sb.status()
    assert s["translation-converter"]["flags"] == ["missing"]
    assert "missing" in s["report-backend"]["flags"]
    assert s["report-backend"]["running"] == 0


def test_restarting_or_paused_container_is_missing(sb: Sandbox) -> None:
    """Docker để Running=true cho container đang crash-loop (restarting) hay bị pause."""
    sb.cont("secai-translation-converter")["State"].update(Status="restarting", Running=True, Restarting=True, Pid=0)
    sb.cont("secai-report-backend")["State"].update(Status="paused", Running=True, Paused=True)
    sb.save()
    s = sb.status()
    assert s["translation-converter"]["flags"] == ["floating-tag", "missing"]
    assert "missing" in s["report-backend"]["flags"]


def test_health_counts_only_containers_with_a_healthcheck() -> None:
    h = load_versions().health
    assert h([{"health": "healthy"}, {"health": None}]) == "healthy 1/1"
    assert h([{"health": "healthy"}, {"health": "healthy"}, {"health": "starting"}]) == "starting 2/3"
    assert h([{"health": "unhealthy"}, {"health": "starting"}]) == "unhealthy 0/2"


def test_digest_pinned_ref_is_clean(sb: Sandbox) -> None:
    """Ghim `repo@sha256:<Id>` (containerd store: RepoDigests = repo@<Id>) là bất biến, không phải tag-moved."""
    sb.cont("secai-docker-redis")["Config"]["Image"] = "redis@" + hid("a")
    sb.save()
    assert sb.status()["redis"]["flags"] == []
    sb.run("record", "--service", "redis", "--action", "release", "--from", "redis:7-alpine", "--to", "redis@" + hid("a"))
    assert sb.entries()[-1]["to"]["image_id"] == hid("a")


def test_behind_main_only_with_git(sb: Sandbox) -> None:
    sb.git_counts({"2dcff27c": 5})
    assert sb.status()["gateway"]["flags"] == ["behind-main:5"]
    sb.git_counts({})  # commit không có trong repo / không có git: không gắn cờ
    assert sb.status()["gateway"]["flags"] == []


def clean_prod(sb: Sandbox) -> None:
    """Đưa mọi service prod về tag bất biến có revision: `status --strict` phải thoát 0."""
    for name, ref in (("secai-translation-frontend", "secai-agent-gateway:fe-4c590be8-20261007"),
                      ("secai-report-backend", "secai-agent-gateway:report-c4c3c802-20261005"),
                      ("secai-review-frontend", "secai-agent-gateway:review-1234abcd-20261004"),
                      ("secai-translation-converter", "secai-doc-convert-svc:1.0.0")):
        c = sb.cont(name)
        c["Config"]["Image"] = ref
        c["Config"]["Labels"]["org.opencontainers.image.revision"] = "1234abcd"
        next(i for i in sb.world["images"] if i["Id"] == c["Image"])["RepoTags"].append(ref)
    sb.save()


def test_strict_exit_code(sb: Sandbox) -> None:
    sb.run("status", "--strict", code=1)
    sb.run("status", "--strict", "--role", "dependency")  # không có service prod nào được xét
    clean_prod(sb)
    sb.run("status", "--strict")
    sb.git_counts({"2dcff27c": 7})  # behind-main chỉ để biết, không làm strict thất bại
    assert sb.status("--strict")["gateway"]["flags"] == ["behind-main:7"]


def _bare(sb):
    c = sb.cont("secai-review-frontend")
    c["Config"]["Image"] = c["Image"]


def _floating(sb):
    c = sb.cont("secai-translation-converter")
    c["Config"]["Image"] = "secai-doc-convert-svc:1"


def _norev(sb):
    c = sb.cont("secai-report-backend")
    del c["Config"]["Labels"]["org.opencontainers.image.revision"]
    next(i for i in sb.world["images"] if i["Id"] == c["Image"])["Config"]["Labels"] = None


def _moved(sb):
    next(i for i in sb.world["images"] if i["Id"] == hid("4"))["RepoTags"] = ["secai-agent-gateway:review-1234abcd-20261004"]
    sb.cont("secai-review-frontend")["Config"]["Image"] = "secai-agent-gateway:review-99999999-20261004"


def _drift(sb):
    sb.world["images"].append(image(hid("e"), ["secai-agent-gateway:main-033b2aa3-20261123"]))
    w = sb.cont("secai-translation-worker")
    w["Config"]["Image"], w["Image"] = "secai-agent-gateway:main-033b2aa3-20261123", hid("e")


def _missing(sb):
    sb.cont("secai-docker-gateway")["State"].update(Status="exited", Running=False)


@pytest.mark.parametrize("break_it,flag", [(_bare, "bare-id"), (_floating, "floating-tag"), (_norev, "no-revision"),
                                           (_moved, "tag-moved"), (_drift, "group-drift"), (_missing, "missing")])
def test_each_strict_flag_fails_strict(sb: Sandbox, break_it, flag: str) -> None:
    clean_prod(sb)
    break_it(sb)
    sb.save()
    flags = {f for s in sb.status("--strict", code=1).values() if s["role"] == "prod" for f in s["flags"]}
    assert flag in flags


def test_short_image_id_prefix_is_bare_id(sb: Sandbox) -> None:
    """`docker run 2222222` lưu Config.Image là tiền tố ID ngắn: phải là bare-id, không phải tag trôi/di chuyển."""
    sb.cont("secai-translation-frontend")["Config"]["Image"] = "2222222"
    sb.save()
    assert sb.status()["translation-frontend"]["flags"] == ["bare-id"]


def test_pattern_service_with_no_container_is_missing(sb: Sandbox) -> None:
    sb.world["containers"] = [c for c in sb.world["containers"] if "pentestcode" not in c["Name"]]
    sb.save()
    pool = sb.status()["pentestcode-pool"]
    assert pool["flags"] == ["missing"] and pool["total"] == 0


def load_versions():
    spec = importlib.util.spec_from_file_location("versions_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dev_role_only_on_request(sb: Sandbox) -> None:
    assert "secai-gw-dev" not in sb.run("status").stdout
    s = sb.status("--role", "dev")
    assert list(s) == ["dev-stacks"] and s["dev-stacks"]["containers"][0]["name"] == "secai-gw-dev-foo-devgw-1"
    assert "dev-stacks" in sb.status("--role", "all")
    sb.run("status", "--role", "bogus", code=2)


def test_only_catalog_containers_and_only_read_only_docker_calls(sb: Sandbox) -> None:
    out = sb.run("status", "--json", "--role", "all").stdout
    assert "poclab-unrelated-target" not in out
    for call in sb.docker_calls():
        assert call[:2] == ["ps", "-a"] or call[:1] == ["inspect"] or call[:2] == ["image", "inspect"], call


def test_text_status_is_vietnamese_table(sb: Sandbox) -> None:
    out = sb.run("status").stdout
    assert "SERVICE" in out and "VAI TRÒ" in out and "gateway ×3" in out
    assert "bare-id: container tạo từ ID image trần" in out
    assert "Service prod có cờ cảnh báo: translation-frontend, report-backend" in out


# ---------------------------------------------------------------- sổ: record, snapshot, history


def test_ledger_is_0600_and_append_only(sb: Sandbox) -> None:
    sb.ledger.write_text('{"ts": "2026-01-01T00:00:00Z", "service": "gateway", "action": "baseline", "containers": [], "to": {"ref": "x"}}\n')
    sb.ledger.chmod(0o644)
    sb.run("record", "--service", "gateway", "--action", "release", "--from", "secai-agent-gateway:main-033b2aa3-20261123",
           "--to", GW_TAG, "--release-dir", str(sb.releases / "main-2dcff27c-20261127"), "--note", "thử")
    sb.run("record", "--service", "gateway", "--action", "rollback", "--from", GW_TAG, "--to", "secai-agent-gateway:main-033b2aa3-20261123")
    assert stat.S_IMODE(sb.ledger.stat().st_mode) == 0o600
    lines = sb.ledger.read_text().splitlines()
    assert len(lines) == 3 and lines[0].startswith('{"ts": "2026-01-01')
    rel, rb = json.loads(lines[1]), json.loads(lines[2])
    assert set(rel) == {"ts", "service", "containers", "action", "from", "to", "release_dir", "by", "note"}
    assert rel["action"] == "release" and rel["to"] == {"ref": GW_TAG, "image_id": hid("1"), "revision": "2dcff27c"}
    assert rel["from"]["image_id"] is None  # image cũ đã bị xoá: chỉ còn ref
    assert rel["containers"] == ["secai-docker-gateway", "secai-translation-gateway", "secai-translation-worker"]
    assert rb["action"] == "rollback" and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", rb["ts"])


def test_ledger_symlink_is_refused(sb: Sandbox, tmp_path: Path) -> None:
    other = tmp_path / "other.txt"
    other.write_text("keep\n")
    other.chmod(0o644)
    sb.ledger.symlink_to(other)
    sb.run("record", "--service", "gateway", "--action", "release", "--from", "a:1", "--to", GW_TAG, code=1)
    assert other.read_text() == "keep\n" and stat.S_IMODE(other.stat().st_mode) == 0o644


def test_record_without_docker_still_writes_the_ref(sb: Sandbox) -> None:
    (sb.root / "empty").mkdir()
    sb.env["PATH"] = str(sb.root / "empty")
    sb.run("record", "--service", "gateway", "--action", "release", "--from", "a:1", "--to", GW_TAG)
    (e,) = sb.entries()
    assert e["to"] == {"ref": GW_TAG, "image_id": None, "revision": None} and len(e["containers"]) == 3


def test_record_with_hanging_docker_falls_back_within_its_timeout(sb: Sandbox, monkeypatch) -> None:
    (sb.bin / "docker").write_text("#!/bin/sh\nexec sleep 30\n")
    monkeypatch.setenv("PATH", sb.env["PATH"])
    monkeypatch.setenv("SECAI_RELEASES_DIR", str(sb.releases))
    v = load_versions()
    v.RECORD_DOCKER_TIMEOUT = 1
    t = time.monotonic()
    assert v.main(["record", "--service", "gateway", "--action", "release", "--from", "a:1", "--to", GW_TAG]) == 0
    assert time.monotonic() - t < 10
    (e,) = sb.entries()
    assert e["to"]["image_id"] is None and len(e["containers"]) == 3


def test_record_rejects_unknown_service_and_action(sb: Sandbox) -> None:
    sb.run("record", "--service", "nope", "--action", "release", "--from", "a", "--to", "b", code=2)
    sb.run("record", "--service", "gateway", "--action", "observed", "--from", "a", "--to", "b", code=2)
    assert not sb.ledger.exists()


def test_snapshot_baseline_then_silent_then_observed(sb: Sandbox) -> None:
    sb.run("snapshot", "--dry-run")
    assert not sb.ledger.exists()
    sb.run("snapshot")
    first = sb.entries()
    assert {e["action"] for e in first} == {"baseline"}
    assert {e["service"] for e in first} == {"gateway", "translation-frontend", "report-backend", "review-frontend", "review-canary",
                                            "translation-converter", "pentestcode-pool", "qdrant", "neo4j", "redis", "elasticsearch"}
    pool = next(e for e in first if e["service"] == "pentestcode-pool")
    assert len(pool["containers"]) == 3
    sb.run("snapshot")
    assert len(sb.entries()) == len(first)  # không đổi thì không ghi
    sb.world["images"].append(image(hid("e"), ["redis:7.4.1"]))
    r = sb.cont("secai-docker-redis")
    r["Config"]["Image"], r["Image"] = "redis:7.4.1", hid("e")
    sb.save()
    sb.run("snapshot")
    new = sb.entries()[len(first):]
    assert len(new) == 1 and new[0]["action"] == "observed" and new[0]["service"] == "redis"
    assert new[0]["from"]["ref"] == "redis:7-alpine" and new[0]["to"] == {"ref": "redis:7.4.1", "image_id": hid("e"), "revision": None}


def test_snapshot_pool_partial_rebuild_and_scale_up(sb: Sandbox) -> None:
    sb.run("snapshot")
    n = len(sb.entries())
    ref = "secai-pentestcode:1.3.14-tools"
    next(i for i in sb.world["images"] if i["Id"] == hid("7"))["RepoTags"] = []
    sb.world["images"].append(image(hid("e"), [ref]))  # tag bị dựng lại
    sb.cont("secai-docker-pentestcode-2")["Image"] = hid("e")  # chỉ -2 được tạo lại từ cùng ref
    sb.world["containers"].append(container("secai-docker-pentestcode-3", ref, hid("7")))  # scale lên, image cũ
    sb.save()
    sb.run("snapshot")
    new = {tuple(e["containers"]): e for e in sb.entries()[n:]}
    assert set(new) == {("secai-docker-pentestcode-2",), ("secai-docker-pentestcode-3",)}
    rebuilt = new[("secai-docker-pentestcode-2",)]
    assert rebuilt["action"] == "observed" and rebuilt["to"]["image_id"] == hid("e") and rebuilt["from"]["image_id"] == hid("7")
    assert new[("secai-docker-pentestcode-3",)]["from"] is None
    # hai container từ hai image khác nhau cùng đổi sang một image: không có `from` chung
    n = len(sb.entries())
    sb.world["images"].append(image(hid("0"), ["secai-pentestcode:1.3.15-tools"]))
    for name in ("secai-docker-pentestcode-2", "secai-docker-pentestcode-3"):
        c = sb.cont(name)
        c["Config"]["Image"], c["Image"] = "secai-pentestcode:1.3.15-tools", hid("0")
    sb.save()
    sb.run("snapshot")
    (e,) = sb.entries()[n:]
    assert e["containers"] == ["secai-docker-pentestcode-2", "secai-docker-pentestcode-3"]
    assert e["from"] is None and e["to"]["image_id"] == hid("0")


def test_snapshot_after_record_sees_no_change(sb: Sandbox) -> None:
    sb.run("snapshot")
    n = len(sb.entries())
    sb.world["images"].append(image(hid("e"), ["secai-agent-gateway:main-9999aaaa-20261128"], env=("GIT_SHA=9999aaaa",)))
    for name in ("secai-docker-gateway", "secai-translation-gateway", "secai-translation-worker"):
        c = sb.cont(name)
        c["Config"]["Image"], c["Image"] = "secai-agent-gateway:main-9999aaaa-20261128", hid("e")
    sb.save()
    sb.run("record", "--service", "gateway", "--action", "release", "--from", GW_TAG, "--to", "secai-agent-gateway:main-9999aaaa-20261128")
    sb.run("snapshot")
    assert len(sb.entries()) == n + 1 and sb.entries()[-1]["action"] == "release"


def test_bad_ledger_lines_are_skipped_not_fatal(sb: Sandbox) -> None:
    sb.run("snapshot")
    n = len(sb.entries())
    with sb.ledger.open("ab") as f:  # dòng bị cắt giữa ký tự nhiều byte, rồi một dòng sai kiểu
        f.write('{"ts": "2026-11-27T13:00:00Z", "service": "gateway", "note": "đầ'.encode()[:-1] + b"\n")
        f.write(b'{"ts": 5, "service": "gateway", "action": [], "from": {"ref": 1}, "to": {"ref": 2}, "containers": [3]}\n')
    r = sb.run("history", "gateway")
    assert "bỏ qua dòng" in r.stderr
    sb.run("history", "--json")
    sb.run("snapshot")
    with serving(sb) as port:
        assert get(port, "/")[0] == 200 and get(port, "/api/history")[0] == 200
    assert len([line for line in sb.ledger.read_bytes().splitlines()]) == n + 2


def test_append_after_torn_last_line_keeps_the_new_entry(sb: Sandbox) -> None:
    sb.run("snapshot")
    with sb.ledger.open("ab") as f:
        f.write(b'{"ts": "2026-11-27T13:00:00Z", "service": "gate')  # ghi dở, không có \n
    sb.run("record", "--service", "gateway", "--action", "release", "--from", "a:1", "--to", GW_TAG)
    hist = json.loads(sb.run("history", "gateway", "--json").stdout)
    assert hist[0]["action"] == "release"


def test_ledger_writes_wait_for_the_flock(sb: Sandbox) -> None:
    sb.ledger.touch()
    with sb.ledger.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        p = subprocess.Popen([sys.executable, "-I", str(SCRIPT), "record", "--service", "gateway", "--action", "release",
                              "--from", "a:1", "--to", GW_TAG], env=sb.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        assert p.poll() is None and sb.ledger.read_text() == ""
    assert p.wait(timeout=30) == 0 and len(sb.entries()) == 1


def test_snapshot_pins_image_id_after_floating_tag_backfill(sb: Sandbox) -> None:
    """Backfill để image_id = null cho tag trôi; snapshot phải ghim image_id đang chạy để thấy lần dựng lại sau."""
    ref = "secai-agent-gateway:review-mvp-20261004"
    sb.ledger.write_text(json.dumps({"ts": "2026-10-04T11:03:54Z", "service": "review-frontend", "action": "backfill",
                                     "containers": ["secai-review-frontend"], "from": None,
                                     "to": {"ref": ref, "image_id": None, "revision": None}}) + "\n")
    sb.run("snapshot")
    pin = [e for e in sb.entries() if e["service"] == "review-frontend"][-1]
    assert pin["action"] == "baseline" and pin["to"]["image_id"] == hid("4") and "ghim" in pin["note"]
    n = len(sb.entries())
    sb.run("snapshot")
    assert len(sb.entries()) == n
    # tag review-mvp-20261004 dựng lại, container tạo lại từ cùng ref
    old = next(i for i in sb.world["images"] if i["Id"] == hid("4"))
    old["RepoTags"] = []
    sb.world["images"].append(image(hid("e"), [ref]))
    sb.cont("secai-review-frontend")["Image"] = hid("e")
    sb.save()
    sb.run("snapshot")
    new = sb.entries()[n:]
    assert len(new) == 1 and new[0]["action"] == "observed" and new[0]["to"]["image_id"] == hid("e")


def test_snapshot_skips_while_release_holds_the_deploy_lock(sb: Sandbox) -> None:
    lock = sb.releases / "gateway-deploy.lock"
    with lock.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # như `exec 8>…; flock -n 8` của release-prod.sh
        r = sb.run("snapshot")
        assert "đang giữ lock" in r.stderr and not sb.ledger.exists()
    sb.run("snapshot")
    assert sb.entries()


def wrap_docker_ps(sb: Sandbox, snippet: str) -> None:
    """Chạy `snippet` (sh) trước mỗi `docker ps` của docker giả."""
    real = sb.bin / "docker-real"
    (sb.bin / "docker").rename(real)
    (sb.bin / "docker").write_text(f'#!/bin/sh\nif [ "$1" = ps ]; then\n{snippet}\nfi\nexec "{real}" "$@"\n')
    (sb.bin / "docker").chmod(0o755)


HOLD_LOCK = """import fcntl, sys, time
f = open(sys.argv[1], "w")
fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
open(sys.argv[2], "w").close()
time.sleep(60)
"""


def test_snapshot_skips_when_release_takes_the_lock_while_reading_docker(sb: Sandbox) -> None:
    """Release lấy lock deploy sau lần kiểm đầu, lúc snapshot đang đọc docker: không được ghi `observed`."""
    sb.run("snapshot")
    n = len(sb.entries())
    sb.world["images"].append(image(hid("e"), ["secai-agent-gateway:main-22222222-20261128"]))
    c = sb.cont("secai-docker-gateway")
    c["Config"]["Image"], c["Image"] = "secai-agent-gateway:main-22222222-20261128", hid("e")
    sb.save()
    holder, ready, pid = sb.root / "hold.py", sb.root / "locked", sb.root / "holder.pid"
    holder.write_text(HOLD_LOCK)
    wrap_docker_ps(sb, f'if [ ! -e "{ready}" ]; then\n"{sys.executable}" "{holder}" "{sb.releases}/gateway-deploy.lock" "{ready}" '
                       f'</dev/null >/dev/null 2>&1 &\necho $! > "{pid}"\nwhile [ ! -e "{ready}" ]; do sleep 0.05; done\nfi')
    try:
        r = sb.run("snapshot")
        assert len(sb.entries()) == n and "bỏ qua lượt này" in r.stderr
    finally:
        if pid.exists():
            os.kill(int(pid.read_text()), signal.SIGTERM)


def test_record_does_not_wait_while_snapshot_reads_docker(sb: Sandbox) -> None:
    """snapshot đọc docker ngoài flock của sổ; sổ đổi trong lúc đó thì snapshot bỏ lượt thay vì ghi theo sổ cũ."""
    sb.run("snapshot")
    n = len(sb.entries())
    started = sb.root / "ps-started"
    wrap_docker_ps(sb, f'touch "{started}"; sleep 4')
    p = subprocess.Popen([sys.executable, "-I", str(SCRIPT), "snapshot"], env=sb.env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        t = time.monotonic()
        sb.run("record", "--service", "gateway", "--action", "release", "--from", "a:1", "--to", GW_TAG)
        assert time.monotonic() - t < 3 and p.poll() is None
    finally:
        _, err = p.communicate(timeout=30)
    assert p.returncode == 0 and "bỏ qua lượt này" in err
    assert [e["action"] for e in sb.entries()[n:]] == ["release"]


def test_history_filters_and_limits(sb: Sandbox) -> None:
    sb.run("history")  # sổ trống vẫn chạy
    sb.run("snapshot")
    sb.run("record", "--service", "gateway", "--action", "release", "--from", "secai-agent-gateway:main-033b2aa3-20261123", "--to", GW_TAG)
    hist = json.loads(sb.run("history", "gateway", "--json").stdout)
    assert [e["action"] for e in hist] == ["release", "baseline"]  # mới nhất trước
    assert len(json.loads(sb.run("history", "--json", "-n", "3").stdout)) == 3
    text = sb.run("history", "gateway").stdout
    assert "HÀNH ĐỘNG" in text and "release" in text
    sb.run("history", "khong-co", code=2)


# ---------------------------------------------------------------- backfill


FE_A, FE_B = "sha256:" + "e1" * 32, "sha256:" + "e2" * 32  # image cũ đã bị xoá khỏi máy


def release_dirs(sb: Sandbox) -> None:
    def profile(path: Path, services: dict, mtime: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": "x", "services": {
            k: {"image": img, "container_name": c, "environment": {"API_KEY": SECRET, "LLM_TOKEN": SECRET}, "command": ["--key", SECRET]}
            for k, (c, img) in services.items()}}))
        os.utime(path, (mtime, mtime))
    base = 1_790_000_000.0
    d = sb.releases
    profile(d / "review-mvp-20261004" / "compose.json", {"frontend": ("secai-review-frontend", "secai-agent-gateway:review-mvp-20261004")}, base)
    profile(d / "review-reward-fix-20261005" / "frontend-release.json", {"frontend": ("secai-translation-frontend", FE_B)}, base + 100)
    profile(d / "review-reward-fix-20261005" / "frontend-rollback.json", {"frontend": ("secai-translation-frontend", FE_A)}, base + 200)
    profile(d / "ab-ui-restyle-20261007" / "frontend-release.json", {"frontend": ("secai-translation-frontend", hid("2"))}, base + 300)
    profile(d / "ab-ui-restyle-20261007" / "frontend-canary.json", {"frontend": ("secai-ab-fe-canary", hid("2"))}, base + 300)
    profile(d / "ra-soat-20261120" / "translation-main.json", {"gateway": ("secai-translation-gateway", "secai-agent-gateway:main-033b2aa3-20261123"),
                                                               "worker": ("secai-translation-worker", "secai-agent-gateway:main-033b2aa3-20261123")}, base + 400)
    profile(d / "ra-soat-20261120" / "credentials.json", {"x": ("secai-docker-gateway", "should-not-be-read:1")}, base + 400)
    rel = d / "main-2dcff27c-20261127"
    rel.mkdir()
    (rel / "state.env").write_text(f"PREV_TAG=secai-agent-gateway:main-033b2aa3-20261123\nNEW_TAG={GW_TAG}\n"
                                   f"COMPOSE_BAK=/x/bak\nTRANS_PREV_PROFILE=/x/p.json\nSECRET_THING={SECRET}\n")
    (rel / "RELEASE-NOTES.md").write_text("# Release main-2dcff27c (2026-11-27T14:32:39+07:00)\n")
    profile(rel / "translation-main.json", {"gateway": ("secai-translation-gateway", GW_TAG), "worker": ("secai-translation-worker", GW_TAG)},
            dt_epoch("2026-11-27T07:32:30Z"))
    gates_only = d / "main-17a3f886-20261123"  # gates không release: không có RELEASE-NOTES → bỏ qua state.env
    gates_only.mkdir()
    (gates_only / "state.env").write_text("PREV_TAG=a:1\nNEW_TAG=secai-agent-gateway:main-17a3f886-20261123\n")
    profile(d / "main-2dcff27c-20261127" / "tree" / "compose.json", {"g": ("secai-docker-gateway", "tree-should-be-skipped:1")}, base)


def dt_epoch(iso: str) -> float:
    import datetime as dt
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def test_backfill_dry_run_writes_nothing_and_second_run_adds_nothing(sb: Sandbox) -> None:
    release_dirs(sb)
    plan = sb.run("backfill").stdout
    assert not sb.ledger.exists() and "(kế hoạch)" in plan
    sb.run("backfill", "--yes")
    entries = sb.entries()
    assert entries and {e["action"] for e in entries} == {"backfill"}
    by = {}
    for e in entries:
        by.setdefault(e["service"], []).append(e)
    fe = [(e["from"] and e["from"]["ref"], e["to"]["ref"]) for e in sorted(by["translation-frontend"], key=lambda e: e["ts"])]
    # rollback.json là trạng thái trước (xếp trước bản mới của cùng thư mục), canary không thuộc catalog
    assert fe == [(None, FE_A), (FE_A, FE_B), (FE_B, hid("2"))]
    gw = [e for e in by["gateway"] if e["to"]["ref"] == GW_TAG]
    assert len(gw) == 1 and gw[0]["containers"] == ["secai-docker-gateway", "secai-translation-gateway", "secai-translation-worker"]
    assert gw[0]["from"]["ref"] == "secai-agent-gateway:main-033b2aa3-20261123"
    assert gw[0]["to"]["image_id"] == hid("1")  # tag bất biến còn trên máy: lấy được image_id
    review = by["review-frontend"][0]
    assert review["to"]["image_id"] is None  # tag trôi: không gán image_id hiện tại cho quá khứ
    refs = json.dumps(entries)
    assert "main-17a3f886" not in refs and "should-not-be-read" not in refs and "tree-should-be-skipped" not in refs
    sb.run("backfill", "--yes")
    assert len(sb.entries()) == len(entries)
    # Replay theo ts phải ra đúng image đang chạy: snapshot sau backfill không thấy gateway đổi.
    sb.run("snapshot")
    assert not [e for e in sb.entries()[len(entries):] if e["service"] in ("gateway", "translation-frontend")]


def test_backfill_does_not_merge_past_a_later_change(tmp_path: Path) -> None:
    """gw X→Y (t0), gw Y→Z (t0+50), worker X→Y (t0+100) trong cùng thư mục: replay phải ra gw=Z, worker=Y."""
    v = load_versions()
    X, Y, Z = ("sha256:" + c * 64 for c in "abc")
    t0 = 1_790_000_000.0
    def put(path: Path, pairs, t):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"services": {n: {"image": img, "container_name": n} for n, img in pairs}}))
        os.utime(path, (t, t))
    put(tmp_path / "r1" / "release.json", [("secai-translation-gateway", X), ("secai-translation-worker", X)], t0 - 1000)
    put(tmp_path / "r2" / "a.json", [("secai-translation-gateway", Y)], t0)
    put(tmp_path / "r2" / "b.json", [("secai-translation-gateway", Z)], t0 + 50)
    put(tmp_path / "r2" / "c.json", [("secai-translation-worker", Y)], t0 + 100)
    services = v.load_catalog()
    v.Images = lambda refs, timeout=None: None  # không gọi docker
    entries = sorted(v.backfill_entries(services, [tmp_path], []), key=lambda e: e["ts"])
    state = v.last_state(entries, "gateway")
    assert state["secai-translation-gateway"]["ref"] == Z and state["secai-translation-worker"]["ref"] == Y


def test_backfill_ignores_floating_tag_in_before_profiles(sb: Sandbox) -> None:
    """`multilang-…` trong profile "before" có thể chính là image đang chạy theo ID: không sinh lần đổi giả."""
    d = sb.releases / "vast-cutover-20261009"
    d.mkdir()
    (d / "translation-release-before.json").write_text(json.dumps({"services": {"g": {
        "image": "secai-agent-gateway:multilang-20261004", "container_name": "secai-translation-gateway"}}}))
    (d / "profile-new.json").write_text(json.dumps({"services": {"g": {
        "image": hid("1"), "container_name": "secai-translation-gateway"}}}))
    out = sb.run("backfill").stdout
    assert "multilang-20261004" not in out and out.count("backfill ") == 1


def test_backfill_skips_release_already_recorded(sb: Sandbox) -> None:
    release_dirs(sb)
    sb.run("record", "--service", "gateway", "--action", "release", "--from", "secai-agent-gateway:main-033b2aa3-20261123",
           "--to", GW_TAG, "--release-dir", "/home/ict/secai-releases/main-2dcff27c-20261127")
    sb.run("backfill", "--yes")
    gw_to = [e["to"]["ref"] for e in sb.entries() if e["service"] == "gateway"]
    assert gw_to.count(GW_TAG) == 1


@pytest.mark.parametrize("state_env", [True, False])
def test_backfill_skips_release_dir_whose_switch_did_not_finish(sb: Sandbox, state_env: bool) -> None:
    """release-prod.sh đã ghi translation-main.json (có thể cả state.env) rồi die/revert trước RELEASE-NOTES."""
    release_dirs(sb)
    d = sb.releases / "main-bbbbbbb2-20261128"
    d.mkdir()
    (d / "pytest.log").write_text("3 passed\n")
    if state_env:
        (d / "state.env").write_text(f"PREV_TAG={GW_TAG}\nNEW_TAG=secai-agent-gateway:main-bbbbbbb2-20261128\n")
    (d / "translation-main.json").write_text(json.dumps({"services": {"gateway": {
        "image": "secai-agent-gateway:main-bbbbbbb2-20261128", "container_name": "secai-translation-gateway"}}}))
    assert "main-bbbbbbb2" not in sb.run("backfill").stdout


def test_backfill_does_not_repeat_a_recorded_rollback(sb: Sandbox) -> None:
    """release A→X, rollback X→A (đã ghi, release_dir X), release A→Y: backfill không thêm X→A lần nữa."""
    a, x, y = ("secai-agent-gateway:main-aaaaaaa1-20261119", "secai-agent-gateway:main-cccccccc-20261120",
               "secai-agent-gateway:main-dddddddd-20261122")
    def rel(name: str, prev: str, new: str, iso: str) -> str:
        d = sb.releases / name
        d.mkdir()
        (d / "pytest.log").write_text("3 passed\n")
        (d / "state.env").write_text(f"PREV_TAG={prev}\nNEW_TAG={new}\n")
        (d / "RELEASE-NOTES.md").write_text(f"# Release {name} ({iso})\n")
        return str(d)
    dx, dy = rel("main-cccccccc-20261120", a, x, "2026-11-20T10:00:00Z"), rel("main-dddddddd-20261122", a, y, "2026-11-22T10:00:00Z")
    gw = ["secai-docker-gateway", "secai-translation-gateway", "secai-translation-worker"]
    sb.ledger.write_text("".join(json.dumps({"ts": ts, "service": "gateway", "containers": gw, "action": act, "from": {"ref": f},
                                             "to": {"ref": t}, "release_dir": d}) + "\n"
                                 for ts, act, f, t, d in (("2026-11-20T10:00:05Z", "release", a, x, dx),
                                                          ("2026-11-21T08:00:00Z", "rollback", x, a, dx),
                                                          ("2026-11-22T10:00:05Z", "release", a, y, dy))))
    sb.run("backfill", "--yes")
    assert not [e for e in sb.entries() if e["action"] == "backfill" and (e["from"] or {}).get("ref") == x]


def test_ledger_state_is_newest_ts_when_backfill_runs_last(sb: Sandbox) -> None:
    """Thứ tự hay gặp sau merge: snapshot → record release → backfill --yes (dòng backfill mang ts quá khứ)."""
    release_dirs(sb)
    sb.run("snapshot")
    sb.run("record", "--service", "gateway", "--action", "release", "--from", "secai-agent-gateway:main-033b2aa3-20261123",
           "--to", GW_TAG, "--release-dir", str(sb.releases / "main-2dcff27c-20261127"))
    sb.run("backfill", "--yes")
    assert json.loads(sb.run("history", "gateway", "--json").stdout)[0]["action"] == "release"
    assert json.loads(sb.run("history", "review-frontend", "--json").stdout)[0]["action"] == "baseline"
    n = len(sb.entries())
    sb.run("snapshot")
    assert len(sb.entries()) == n


def test_backfill_extra_dir(sb: Sandbox, tmp_path: Path) -> None:
    extra = tmp_path / "extra" / "old-20261001"
    extra.mkdir(parents=True)
    (extra / "release.json").write_text(json.dumps({"services": {"r": {"image": hid("3"), "container_name": "secai-report-backend",
                                                                        "environment": {"K": SECRET}}}}))
    out = sb.run("backfill", "--extra-dir", str(tmp_path / "extra")).stdout
    assert "report-backend" in out and "old-20261001" in out


# ---------------------------------------------------------------- secret không bao giờ lộ


def test_secret_never_appears_in_any_output(sb: Sandbox) -> None:
    release_dirs(sb)
    sb.run("status")
    sb.run("status", "--json", "--role", "all")
    sb.run("snapshot", "--dry-run")
    sb.run("snapshot")
    sb.run("record", "--service", "gateway", "--action", "release", "--from", "a:1", "--to", GW_TAG)
    sb.run("backfill")
    sb.run("backfill", "--yes")
    sb.run("history")
    sb.run("history", "--json", "-n", "500")
    with serving(sb) as port:
        for path in ("/", "/?role=all", "/api/status", "/api/status?role=all", "/api/history", "/api/history?service=gateway"):
            code, body = get(port, path)
            assert code == 200
            sb.outputs.append(body)
    sb.outputs.append(sb.ledger.read_text())
    assert sb.outputs and all(SECRET not in o for o in sb.outputs)
    assert all("OPENAI_API_KEY" not in o and "HF_TOKEN" not in o and "API_KEY" not in o for o in sb.outputs)


# ---------------------------------------------------------------- serve


class serving:
    def __init__(self, sb: Sandbox) -> None:
        self.sb = sb

    def __enter__(self) -> int:
        self.p = subprocess.Popen([sys.executable, "-I", str(SCRIPT), "serve", "--port", "0"], env=self.sb.env,
                                  stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True)
        line = self.p.stderr.readline()
        m = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
        assert m, line
        return int(m.group(1))

    def __exit__(self, *exc) -> None:
        self.p.terminate()
        self.p.wait(timeout=10)
        self.p.stderr.close()


def get(port: int, path: str, method: str = "GET", headers: dict | None = None) -> tuple[int, str]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        c.request(method, path, body=b"x" if method == "POST" else None, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read().decode()
    finally:
        c.close()


def test_serve_get_only_loopback_only(sb: Sandbox) -> None:
    sb.run("snapshot")
    with serving(sb) as port:
        n = len(sb.docker_calls())
        assert get(port, "/api/status?role=bogus")[0] == 400 and len(sb.docker_calls()) == n  # lỗi client, chưa gọi docker
        code, body = get(port, "/")
        assert code == 200 and "<!doctype html>" in body and 'http-equiv="refresh" content="60"' in body
        assert "prefers-color-scheme:dark" in body and "width=device-width" in body
        assert "scripts/release-prod.sh rollback --yes" in body and "bare-id" in body
        assert "<script" not in body and "http://" not in body.split("<body>")[0] and "<form" not in body and "<button" not in body
        code, body = get(port, "/api/status")
        assert code == 200 and {s["id"] for s in json.loads(body)["services"]} >= {"gateway", "report-backend"}
        code, body = get(port, "/api/history?service=gateway")
        assert code == 200 and json.loads(body)["entries"][0]["action"] == "baseline"
        assert get(port, "/api/history?service=khong-co")[0] == 404
        assert get(port, "/khong-co")[0] == 404
        for method in ("POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            assert get(port, "/", method)[0] == 405, method
        assert get(port, "/", headers={"Host": "evil.example"})[0] == 403
        assert get(port, "/api/status", headers={"Host": "10.0.0.5:18190"})[0] == 403
        assert get(port, "/", headers={"Host": f"localhost:{port}"})[0] == 200


def test_serve_has_no_flag_to_bind_elsewhere() -> None:
    r = subprocess.run([sys.executable, "-I", str(SCRIPT), "serve", "--help"], text=True, capture_output=True)
    assert "--port" in r.stdout and "--host" not in r.stdout and "--bind" not in r.stdout
    src = SCRIPT.read_text()
    assert src.count('ThreadingHTTPServer(("127.0.0.1", a.port)') == 1


def test_serve_caches_docker_for_a_few_seconds(sb: Sandbox) -> None:
    with serving(sb) as port:
        get(port, "/api/status")
        n = len(sb.docker_calls())
        get(port, "/api/status")
        get(port, "/")
        assert len(sb.docker_calls()) == n


# ---------------------------------------------------------------- release-prod.sh: ghi sổ không đổi mã thoát

RP_DOCKER = r'''#!/bin/sh
S=__S__
echo "$*" >> "$S/docker_calls.txt"
case "$1" in
inspect)
  case "$*" in
    *config_files*) echo "$S/trans-prev.json" ;;
    *Config.Image*) cat "$S/current_tag" ;;
    *) echo '[]' ;;
  esac ;;
compose)
  f=docker-compose.yml; prev=""
  for a in "$@"; do [ "$prev" = "-f" ] && f=$a; prev=$a; done
  grep -o 'secai-agent-gateway:main-[0-9a-f]*-[0-9]*' "$f" | head -1 > "$S/current_tag.new" && mv "$S/current_tag.new" "$S/current_tag" ;;
create) echo cid123 ;;
cp) cp -r "$S/src" "$3" ;;
image) echo '[]'; exit 1 ;;
esac
exit 0
'''
RP_GIT = r'''#!/bin/sh
[ "$1" = "-C" ] && shift 2
case "$1" in
rev-parse) echo 2222222222222222222222222222222222222222 ;;
log) echo "2222222 thay đổi" ;;
diff) case "$*" in *--stat*) echo " src/x.py | 1 +" ;; esac ;;
archive) tar -C __S__ -cf - src ;;
esac
exit 0
'''
RP_CURL = r'''#!/bin/sh
for a in "$@"; do url=$a; done
case "$url" in *health*) printf '{"status":"ok","git_sha":"22222222"}\n' ;; esac
exit 0
'''
OLD = "secai-agent-gateway:main-11111111-20261101"


class ReleaseSandbox:
    def __init__(self, root: Path) -> None:
        self.root, self.bin, self.releases = root, root / "bin", root / "releases"
        self.docker_dir, self.repo = root / "secai-docker", root / "repo"
        for d in (self.bin, self.releases, self.docker_dir, self.repo / ".venv" / "bin", root / "src" / "secai_gateway"):
            d.mkdir(parents=True)
        (root / "src" / "secai_gateway" / "x.py").write_text("x = 1\n")
        for name, src in (("docker", RP_DOCKER), ("git", RP_GIT), ("curl", RP_CURL)):
            (self.bin / name).write_text(src.replace("__S__", str(root)))
            (self.bin / name).chmod(0o755)
        py = self.repo / ".venv" / "bin" / "python"
        py.write_text("#!/bin/sh\necho '3 passed in 0.01s'\n")
        py.chmod(0o755)
        (self.docker_dir / "docker-compose.yml").write_text(f"services:\n  gateway:\n    image: {OLD}\n")
        (root / "trans-prev.json").write_text(json.dumps({"services": {
            "gateway": {"image": OLD, "container_name": "secai-translation-gateway", "environment": {"K": SECRET}},
            "worker": {"image": OLD, "container_name": "secai-translation-worker"}}}))
        (root / "current_tag").write_text(OLD + "\n")
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "SECAI_RELEASES_DIR": str(self.releases),
                    "SECAI_DOCKER_DIR": str(self.docker_dir), "SECAI_REPO": str(self.repo), "SECAI_DATA_ROOT": str(root / "data")}

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(RELEASE), *args], env=self.env, text=True, capture_output=True, timeout=120)

    def ledger(self) -> list[dict]:
        p = self.releases / "versions.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []


@pytest.mark.parametrize("ledger_ok", [True, False])
def test_release_and_rollback_record_without_changing_exit_code(tmp_path: Path, ledger_ok: bool) -> None:
    rs = ReleaseSandbox(tmp_path)
    if not ledger_ok:
        (rs.releases / "versions.jsonl").mkdir()  # ghi sổ chắc chắn lỗi
    r = rs.run("run", "--yes")
    assert r.returncode == 0, r.stderr[-2000:]
    assert "RELEASE OK" in r.stderr
    assert (rs.root / "current_tag").read_text().strip().startswith("secai-agent-gateway:main-22222222-")
    r2 = rs.run("rollback", "--yes")
    assert r2.returncode == 0, r2.stderr[-2000:]
    assert "rollback xong" in r2.stdout and (rs.root / "current_tag").read_text().strip() == OLD
    if ledger_ok:
        rel, rb = rs.ledger()
        assert rel["action"] == "release" and rel["service"] == "gateway" and rel["from"]["ref"] == OLD
        assert rel["to"]["ref"].startswith("secai-agent-gateway:main-22222222-") and rel["release_dir"].startswith(str(rs.releases))
        assert rb["action"] == "rollback" and rb["to"]["ref"] == OLD and rb["from"]["ref"] == rel["to"]["ref"]
        assert "cảnh báo" not in r.stderr + r2.stderr
    else:
        assert "cảnh báo" in r.stderr and "cảnh báo" in r2.stderr
    assert SECRET not in r.stdout + r.stderr + r2.stdout + r2.stderr


def test_release_script_ledger_calls_are_guarded() -> None:
    src = RELEASE.read_text()
    calls = [line for line in src.splitlines() if 'versions.py" record' in line]
    assert len(calls) == 2
    for line in calls:
        assert 'SECAI_RELEASES_DIR="$RELEASES" timeout 60 python3 -I "$(dirname "$0")/versions.py"' in line
    outer = {int(t) for t in re.findall(r'timeout (\d+) python3 -I "\$\(dirname "\$0"\)/versions\.py"', src)}
    assert outer == {60} and 2 * load_versions().RECORD_DOCKER_TIMEOUT < 60  # nhánh dự phòng còn kịp ghi ref
    blocks = re.findall(r'versions\.py" record[^\n]*\\\n[^\n]*', src)
    assert len(blocks) == 2 and all('|| log "cảnh báo' in b for b in blocks)
    assert src.index("versions.py\" record --service gateway --action release") > src.index('cat > "$REL/RELEASE-NOTES.md"')


# ---------------------------------------------------------------- install-versions-ui.sh (HOME tạm, systemctl giả)


def test_installer_writes_loopback_units_and_removes_them(tmp_path: Path) -> None:
    home, bin_, calls = tmp_path / "home", tmp_path / "bin", tmp_path / "calls.txt"
    home.mkdir()
    bin_.mkdir()
    (bin_ / "systemctl").write_text(f"#!/bin/sh\necho systemctl \"$@\" >> '{calls}'\n")
    (bin_ / "git").write_text(f"#!/bin/sh\necho git \"$@\" >> '{calls}'\ncase \"$*\" in\n"
                              f"  *'worktree list'*) echo 'worktree {tmp_path}/main' ;;\n"
                              "  *cat-file*) [ -z \"$NO_VERSIONS\" ] || exit 1 ;;\nesac\nexit 0\n")
    for f in ("systemctl", "git"):
        (bin_ / f).chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_}:{os.environ['PATH']}"}
    script = str(ROOT / "scripts" / "install-versions-ui.sh")
    r = subprocess.run(["bash", script], env={**env, "NO_VERSIONS": "1"}, cwd=tmp_path, text=True, capture_output=True)
    assert r.returncode == 2 and "chưa có scripts/versions.py" in r.stderr
    r = subprocess.run(["bash", script], env=env, cwd=tmp_path, text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    units = home / ".config" / "systemd" / "user"
    ui = (units / "secai-versions-ui.service").read_text()
    assert "serve --port 18190" in ui and "--host" not in ui and "0.0.0.0" not in ui
    assert "OnUnitActiveSec=1h" in (units / "secai-versions-snapshot.timer").read_text()
    assert "snapshot" in (units / "secai-versions-snapshot.service").read_text()
    runner = home / ".local" / "bin" / "secai-versions-run"
    text = runner.read_text()
    assert "git archive origin/main scripts/versions.py deploy/services.json" in text
    assert f'SECAI_REPO="{tmp_path}/main"' in text and 'exec python3 -I "$dest/scripts/versions.py"' in text
    assert subprocess.run(["bash", "-n", str(runner)]).returncode == 0
    log = calls.read_text()
    assert "systemctl --user enable --now secai-versions-ui.service secai-versions-snapshot.timer" in log
    r = subprocess.run(["bash", script, "--remove"], env=env, cwd=tmp_path, text=True, capture_output=True)
    assert r.returncode == 0 and not runner.exists() and not list(units.glob("secai-versions-*"))
