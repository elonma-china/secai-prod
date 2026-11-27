"""stack/secai: CLI người vận hành. Docker/curl giả (registry JSON: ref -> digest + cây tệp), bundle dựng từ stack/ thật
với check.sh/restore-data.sh giả có ghi lại lời gọi. Không cần git checkout, không chạy Docker thật."""
import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1] / "stack"
SECAI = STACK / "secai"
REG = "ghcr.io/elonma-china"
V1, V2 = "2026.11.27-aaaaaaaa", "2026.11.28-bbbbbbbb"
D1, D2 = "2026.11.27", "2026.11.28"
KEY = "sk-FAKE-SECRET-secai-7731"   # khoá LLM giả: không được xuất hiện ở argv/stdout/stderr
GTOKEN = "dckr_pat_FAKEreadOnlyToken000000000xx"   # token Docker Hub chỉ đọc giả: chỉ được đi qua stdin của docker login
DATA = "docker.io/testns/secai-data"
BASES = {"QDRANT_IMAGE": "qdrant/qdrant:v1.10.1", "NEO4J_IMAGE": "neo4j:5.20-community",
         "REDIS_IMAGE": "redis:7-alpine", "ES_IMAGE": "docker.elastic.co/elasticsearch/elasticsearch:8.15.3"}


def dg(s):
    return "sha256:" + hashlib.sha256(s.encode()).hexdigest()


FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, shutil, sys
F = "@FAKE@"
argv = sys.argv[1:]
with open(os.path.join(F, "calls.log"), "a") as f:
    f.write(json.dumps(argv) + "\n")
with open(os.path.join(F, "env.log"), "a") as f:
    f.write(json.dumps(sorted(k for k in os.environ)) + "\n")
reg = json.load(open(os.path.join(F, "registry.json")))
lp = os.path.join(F, "local.json")
local = json.load(open(lp)) if os.path.exists(lp) else []
def save(): json.dump(local, open(lp, "w"))
def repo_of(r):
    return r.rsplit(":", 1)[0] if ":" in r.rsplit("/", 1)[-1] else r
def resolve(ref):
    if "@" in ref:
        name, d = ref.split("@", 1)
        for k, v in reg.items():
            if v["digest"] == d and repo_of(k) == repo_of(name):
                return k, v
        return None, None
    return (ref, reg[ref]) if ref in reg else (None, None)
def fail(msg, rc=1):
    sys.stderr.write("fake docker: " + msg + "\n"); sys.exit(rc)
cmd = argv[0] if argv else ""
if cmd == "info": sys.exit(0)
if cmd == "compose":
    a = argv[1:]
    if a[:1] == ["version"]: print("Docker Compose version v2.40.3"); sys.exit(0)
    pd = a[a.index("--project-directory") + 1]
    rest = a[a.index("-f") + 2:]
    envf = os.path.join(pd, ".env")
    env = {}
    for l in open(envf):
        if "=" in l and not l.startswith("#"):
            k, v = l.rstrip("\n").split("=", 1); env[k] = v
    if rest[:1] == ["up"]:
        if os.path.exists(os.path.join(F, "up_rc")): sys.exit(int(open(os.path.join(F, "up_rc")).read()))
        snap = {k: env.get(k, "") for k in ("GATEWAY_IMAGE", "VM_IMAGE", "QDRANT_IMAGE", "NEO4J_IMAGE", "REDIS_IMAGE", "ES_IMAGE", "DATA_ROOT",
                                       "SECAI_VERSION", "SECAI_DATA_VERSION")}
        with open(os.path.join(F, "up.jsonl"), "a") as f: f.write(json.dumps(snap) + "\n")
        json.dump(snap, open(os.path.join(F, "running.json"), "w")); sys.exit(0)
    if rest[:1] == ["ps"]:
        run = json.load(open(os.path.join(F, "running.json"))) if os.path.exists(os.path.join(F, "running.json")) else None
        svcs = ["gateway", "qdrant", "neo4j", "redis", "es", "vm-a", "vm-b"]
        if "-aq" in rest or "-q" in rest:
            if run: print("\n".join("c-" + s for s in svcs))
            sys.exit(0)
        if run:
            health = open(os.path.join(F, "health")).read().strip() if os.path.exists(os.path.join(F, "health")) else "healthy"
            for s in svcs: print(json.dumps({"Service": s, "State": "running", "Health": health}))
        sys.exit(0)
    if rest[:1] in (["down"], ["logs"]): sys.exit(0)
    fail("compose " + " ".join(rest), 9)
if cmd == "login":
    tok = sys.stdin.read()
    open(os.path.join(F, "login.stdin"), "a").write(tok)
    cfg = os.environ.get("DOCKER_CONFIG", "")
    open(os.path.join(F, "login.cfg"), "a").write(cfg + "\n")
    if not cfg or tok != open(os.path.join(F, "valid_token")).read(): fail("unauthorized")
    json.dump({"auths": {"ghcr.io": {"auth": "x"}}}, open(os.path.join(cfg, "config.json"), "w")); sys.exit(0)
if cmd == "logout":
    cfg = os.environ.get("DOCKER_CONFIG", "")
    if cfg and os.path.exists(os.path.join(cfg, "config.json")): os.remove(os.path.join(cfg, "config.json"))
    sys.exit(0)
if cmd == "pull":
    ref = argv[-1]
    open(os.path.join(F, "pull.log"), "a").write(json.dumps([ref, os.environ.get("DOCKER_CONFIG", "")]) + "\n")
    if "secai-data" in ref:   # package private: cần đăng nhập trong DOCKER_CONFIG tạm
        cfg = os.environ.get("DOCKER_CONFIG", "")
        if not (cfg and os.path.exists(os.path.join(cfg, "config.json"))): fail("unauthorized: authentication required")
    if os.path.exists(os.path.join(F, "pull_fail")) and open(os.path.join(F, "pull_fail")).read().strip() in ref:
        fail("pull " + ref)
    k, v = resolve(ref)
    if not k: fail("not found " + ref)
    local.extend([ref, k + "@" + v["digest"]]); save(); print(ref); sys.exit(0)
if cmd == "image" and argv[1] == "inspect":
    ref = argv[-1]
    if ref not in local: sys.exit(1)
    if "--format" in argv:
        k, v = resolve(ref)
        print(repo_of(k) + "@" + v["digest"])
    sys.exit(0)
if cmd == "image" and argv[1] == "rm": sys.exit(0)
if cmd == "create":
    ref = argv[-2]
    k, v = resolve(ref)
    if not k: fail("create: no image " + ref)
    cid = "cid-" + str(len(open(os.path.join(F, "calls.log")).readlines()))
    cs = os.path.join(F, "containers.json")
    c = json.load(open(cs)) if os.path.exists(cs) else {}
    c[cid] = v["fs"]; json.dump(c, open(cs, "w")); print(cid); sys.exit(0)
if cmd == "cp":
    src, dst = argv[1], argv[2]
    cid, path = src.split(":", 1)
    fs = json.load(open(os.path.join(F, "containers.json")))[cid]
    shutil.copytree(os.path.join(fs, path.strip("/").rstrip("/.")), dst, dirs_exist_ok=True); sys.exit(0)
if cmd in ("rm", "stop"): sys.exit(0)
if cmd == "run":
    if "-d" in argv: print("tmpqdrant"); sys.exit(0)
    sys.exit(0)
if cmd == "port": print("127.0.0.1:16333"); sys.exit(0)
if cmd == "inspect":
    run = json.load(open(os.path.join(F, "running.json")))
    m = {"gateway": "GATEWAY_IMAGE", "vm-a": "VM_IMAGE", "vm-b": "VM_IMAGE", "qdrant": "QDRANT_IMAGE",
         "neo4j": "NEO4J_IMAGE", "redis": "REDIS_IMAGE", "es": "ES_IMAGE"}
    for cid in argv[3:]:
        s = cid[2:]
        img = run.get(m.get(s, ""), "") or ("other/" + s + ":1@" + "sha256:" + "0" * 64)
        if os.path.exists(os.path.join(F, "drift")) and s == "gateway": img = "drift:1"
        print("\t".join([s, img, "sha256:" + "f" * 64, "running", "healthy"]))
    sys.exit(0)
fail("unknown: " + " ".join(argv), 9)
'''

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
F = "@FAKE@"
url = [a for a in sys.argv[1:] if a.startswith("http")][-1]
with open(os.path.join(F, "curl.log"), "a") as f: f.write(json.dumps(sys.argv[1:]) + "\n")
with open(os.path.join(F, "curl.env"), "a") as f: f.write(json.dumps(sorted(os.environ)) + "\n")
if url.endswith("/collections"): sys.exit(0)
if "/collections/" in url:
    print(json.dumps({"result": {"status": "green", "points_count": int(os.environ.get("FAKE_POINTS", "5"))}})); sys.exit(0)
if url.endswith("/health"):
    run = json.load(open(os.path.join(F, "running.json")))
    print(json.dumps({"status": "ok", "git_sha": run["SECAI_VERSION"][-8:]})); sys.exit(0)
sys.exit(7)
'''

FAKE_CHECK = r'''#!/usr/bin/env bash
# check.sh giả: trượt khi SECAI_VERSION / SECAI_DATA_VERSION hiện tại nằm trong danh sách trượt của test.
d="$(cd "$(dirname "$0")" && pwd)"
v=$(grep -E '^SECAI_VERSION=' "$d/.env" | tail -1 | cut -d= -f2-)
dv=$(grep -E '^SECAI_DATA_VERSION=' "$d/.env" | tail -1 | cut -d= -f2-)
echo "check $* $v $dv" >> "@FAKE@/check.log"
for x in $(cat "@FAKE@/check_fail" 2>/dev/null); do [ "$x" = "$v" ] || [ "$x" = "$dv" ] && { echo "FAIL: fake" >&2; exit 1; }; done
exit 0
'''

FAKE_RESTORE = r'''#!/usr/bin/env bash
# restore-data.sh giả: tạo cây DATA_ROOT như bản thật, ghi lại tham số (không giá trị bí mật).
echo "restore DATA_ROOT=$DATA_ROOT NEO4J_IMAGE=$NEO4J_IMAGE args=$*" >> "@FAKE@/restore.log"
mkdir -p "$DATA_ROOT"/{exploitdb,poc/store,poc-sources,exploitdb-mcp-server,qdrant,_state/qdrant_storage,_state/neo4j_data}
chmod 0777 "$DATA_ROOT/poc" "$DATA_ROOT/poc/store"
[ ! -f "@FAKE@/restore_fail" ] || exit 1
exit 0
'''


def write_exec(p, text):
    p.write_text(text)
    p.chmod(0o755)


class Box:
    def __init__(self, tmp):
        self.tmp = tmp
        self.fake = tmp / "fake"; self.fake.mkdir()
        self.bin = tmp / "bin"; self.bin.mkdir()
        self.home = tmp / "home" / "secai"
        write_exec(self.bin / "docker", FAKE_DOCKER.replace("@FAKE@", str(self.fake)))
        write_exec(self.bin / "curl", FAKE_CURL.replace("@FAKE@", str(self.fake)))
        (tmp / "mmc").write_text("262144\n")
        (self.fake / "valid_token").write_text(GTOKEN)
        self.tokfile = tmp / "ghcr-token"
        self.tokfile.write_text(GTOKEN + "\n"); self.tokfile.chmod(0o600)
        self.reg = {}
        for k, ref in BASES.items():
            self.reg[ref] = {"digest": dg(ref), "fs": str(tmp)}
        for v in (V1, V2):
            self.add_bundle(v)
        for d in (D1, D2):
            self.add_data(d)
        self.save()

    def pins(self, v):
        imgs = {"GATEWAY_IMAGE": f"{REG}/secai-agent-gateway:{v}", "VM_IMAGE": f"{REG}/secai-pentestcode-vm:{v}", **BASES}
        return {k: {"ref": r, "digest": dg(r)} for k, r in imgs.items()}

    def add_bundle(self, v, manifest_version=None, tag=None):
        fs = self.tmp / f"bundle-{tag or v}"
        st = fs / "stack"
        shutil.copytree(STACK, st, ignore=shutil.ignore_patterns(".env", "secrets", "tokenizer", "node_modules", "__pycache__",
                                                                 "test-results", "playwright-report"))
        write_exec(st / "check.sh", FAKE_CHECK.replace("@FAKE@", str(self.fake)))
        write_exec(st / "restore-data.sh", FAKE_RESTORE.replace("@FAKE@", str(self.fake)))
        man = {"schema": 1, "version": manifest_version or v, "git_sha": v[-8:] * 5, "images": self.pins(v),
               "data": {"default_version": D1, "image": DATA}}
        (st / "secai-stack.json").write_text(json.dumps(man))
        self.reg[f"{REG}/secai-stack:{tag or v}"] = {"digest": dg("bundle" + (tag or v)), "fs": str(fs)}
        for k, e in self.pins(v).items():
            if k in ("GATEWAY_IMAGE", "VM_IMAGE"):
                self.reg[e["ref"]] = {"digest": e["digest"], "fs": str(fs)}

    def add_data(self, d):
        fs = self.tmp / f"data-{d}"
        pkg = fs / "secai-data"
        (pkg / "qdrant").mkdir(parents=True)
        (pkg / "qdrant" / "cve_kb_hybrid.snapshot").write_bytes(b"snap" + d.encode())
        (pkg / "MANIFEST.md").write_text("# SecAI data package\n\n- created: 2026-11-26T10:00:00Z\n- qdrant collection: cve_kb_hybrid (5 points)\n")
        sums = "".join(f"{hashlib.sha256((pkg / f).read_bytes()).hexdigest()}  {f}\n" for f in ("qdrant/cve_kb_hybrid.snapshot", "MANIFEST.md"))
        (pkg / "SHA256SUMS").write_text(sums)
        self.reg[f"{DATA}:{d}"] = {"digest": dg("data" + d), "fs": str(fs)}

    def save(self):
        (self.fake / "registry.json").write_text(json.dumps(self.reg))

    def env(self, **kw):
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp / "home"), "SECAI_HOME": str(self.home),
             "FAKE_DOCKER_DIR": str(self.fake), "MAX_MAP_COUNT_FILE": str(self.tmp / "mmc"), "SECAI_MIN_FREE_GB": "0",
             "SECAI_UP_TIMEOUT": "5", "LANG": "C.UTF-8", "SECAI_DOCKERHUB_TOKEN_FILE": str(self.tokfile)}
        e.update(kw)
        return e

    def install_env(self, **kw):
        base = dict(SECAI_YES="1", LLM_BASE_URL="http://10.9.0.1:18000/v1", LLM_MODEL="m-llm", LLM_API_KEY=KEY,
                    EMBEDDING_API_URL="http://10.9.0.1:18000", EMBEDDING_MODEL="m-emb", EMBEDDING_API_KEY="emb-key-123",
                    RERANKER_API_URL="http://10.9.0.1:18000", RERANKER_MODEL="m-rr", RERANKER_API_KEY="rr-key-123",
                    VERIFY_ALLOWED_TARGETS="10.250.0.0/24", GATEWAY_BIND="127.0.0.1")
        base.update(kw)
        return self.env(**base)

    def run(self, *args, env=None):
        return subprocess.run(["bash", str(SECAI), *args], text=True, capture_output=True, env=env or self.env(),
                              stdin=subprocess.DEVNULL, timeout=180)

    def data_pkg(self, d):
        return self.tmp / f"data-{d}" / "secai-data"

    def calls(self):
        p = self.fake / "calls.log"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def ups(self):
        p = self.fake / "up.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def state(self):
        return json.loads((self.home / "state.json").read_text())

    def envfile(self):
        return dict(l.split("=", 1) for l in (self.home / "stack" / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))

    def set(self, name, text):
        (self.fake / name).write_text(text)


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def installed(box, version=V1):
    r = box.run("install", version, env=box.install_env())
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def test_bash_syntax():
    assert subprocess.run(["bash", "-n", str(SECAI)]).returncode == 0
    assert os.access(SECAI, os.X_OK)


def test_install_happy_path(box):
    r = installed(box)
    st = box.state()
    assert st["installed"] == "true" and st["version"] == V1 and st["data_version"] == D1
    assert st["bundle_digest"] == dg("bundle" + V1)
    env = box.envfile()
    pins = box.pins(V1)
    for k, e in pins.items():
        assert env[k] == f"{e['ref']}@{e['digest']}", k
    assert env["SECAI_VERSION"] == V1 and env["SECAI_DATA_VERSION"] == D1
    assert env["DATA_ROOT"] == str(box.home / "data" / D1)
    assert env["COMPOSE_PROFILES"] == "exploit" and env["GATEWAY_BIND"] == "127.0.0.1"
    assert env["NEO4J_PASSWORD"] and env["IAS_SERVER_POOL"].startswith("http://vm-a:5101")
    mode = stat.S_IMODE((box.home / "stack" / ".env").stat().st_mode)
    assert mode == 0o600
    calls = box.calls()
    # mọi image kéo theo digest, compose up đúng pin của V1
    pulled = [c[-1] for c in calls if c[0] == "pull"]
    for e in pins.values():
        assert f"{e['ref']}@{e['digest']}" in pulled
    assert box.ups()[-1]["GATEWAY_IMAGE"] == f"{pins['GATEWAY_IMAGE']['ref']}@{pins['GATEWAY_IMAGE']['digest']}"
    # Qdrant tạm chỉ trên 127.0.0.1, restore dùng NEO4J_IMAGE ghim
    runs = [c for c in calls if c[0] == "run"]
    assert any("-d" in c and "127.0.0.1::6333" in c for c in runs)
    assert any("--entrypoint" in c and "sh" in c for c in runs)   # sửa quyền poc bằng image gateway
    rl = (box.fake / "restore.log").read_text()
    assert f"DATA_ROOT={box.home}/data/{D1}" in rl and f"NEO4J_IMAGE={BASES['NEO4J_IMAGE']}@" in rl
    assert "--qdrant-url http://127.0.0.1:16333" in rl
    assert (box.home / "data" / D1 / ".secai-data-complete").exists()
    assert not (box.home / "downloads" / f"data-{D1}").exists()
    assert "check all" in (box.fake / "check.log").read_text()
    # không bao giờ down -v
    assert not any(c[0] == "compose" and "-v" in c and "down" in c for c in calls)


def test_install_secret_never_in_argv_or_output(box):
    r = installed(box)
    allcalls = (box.fake / "calls.log").read_text() + (box.fake / "curl.log").read_text()
    assert KEY not in allcalls and KEY not in r.stdout + r.stderr
    assert KEY in (box.home / "stack" / ".env").read_text()
    for p in box.home.rglob("*"):
        if p.is_file() and p.name != ".env" and "env-backups" not in str(p):
            assert KEY not in p.read_text(errors="replace"), p


def test_children_get_clean_environment(box):
    # biến shell (vd. GATEWAY_IMAGE export sẵn) không được lọt vào compose/check (compose ưu tiên môi trường hơn .env)
    installed(box)
    envs = [json.loads(l) for l in (box.fake / "env.log").read_text().splitlines()]
    for e in envs:
        assert "LLM_API_KEY" not in e and "GATEWAY_IMAGE" not in e and "SECAI_YES" not in e


def test_install_missing_required_non_interactive(box):
    e = box.install_env()
    del e["LLM_API_KEY"]; del e["VERIFY_ALLOWED_TARGETS"]
    r = box.run("install", V1, env=e)
    assert r.returncode == 1
    assert "LLM_API_KEY" in r.stderr and "VERIFY_ALLOWED_TARGETS" in r.stderr
    assert not box.ups()
    assert not (box.home / "state.json").exists()


def test_install_rejects_bad_url(box):
    r = box.run("install", V1, env=box.install_env(LLM_BASE_URL="ftp://x"))
    assert r.returncode == 1 and "LLM_BASE_URL" in r.stderr and not box.ups()


def test_install_refuses_tag_manifest_mismatch(box):
    box.add_bundle(V2, manifest_version=V1, tag="2026.11.29-cccccccc")
    box.save()
    r = box.run("install", "2026.11.29-cccccccc", env=box.install_env())
    assert r.returncode == 1 and "từ chối" in r.stderr
    assert not box.ups()


def test_install_stable_resolves_version(box):
    box.add_bundle(V2, tag="stable")
    box.save()
    r = box.run("install", env=box.install_env())
    assert r.returncode == 0, r.stderr
    assert box.state()["version"] == V2


def test_install_bad_version_string(box):
    r = box.run("install", "latest", env=box.install_env())
    assert r.returncode == 1 and "không hợp lệ" in r.stderr
    assert not any(c[0] == "pull" for c in box.calls())


def test_install_twice_refused(box):
    installed(box)
    r = box.run("install", V1, env=box.install_env())
    assert r.returncode == 1 and "upgrade" in r.stderr


def test_install_point_count_mismatch_fails(box):
    r = box.run("install", V1, env=box.install_env(FAKE_POINTS="4"))
    assert r.returncode == 1 and "MANIFEST ghi 5" in r.stderr
    assert not box.ups()


def test_install_check_fail_exit1_but_installed(box):
    box.set("check_fail", V1)
    r = box.run("install", V1, env=box.install_env())
    assert r.returncode == 1 and "secai check" in r.stderr
    assert box.state()["installed"] == "true"


def test_status_json(box):
    installed(box)
    r = box.run("status", "--json")
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)
    assert s["version"] == V1 and s["data_version"] == D1 and s["images_match"] is True
    assert s["health_git_sha"] == V1[-8:] == s["manifest_git_sha8"]
    assert {x["service"] for x in s["services"]} >= {"gateway", "vm-a", "vm-b", "qdrant"}


def test_status_detects_drift(box):
    installed(box)
    box.set("drift", "1")
    s = json.loads(box.run("status", "--json").stdout)
    assert s["images_match"] is False and "gateway" in s["image_mismatch"]


def test_upgrade_ok(box):
    installed(box)
    r = box.run("upgrade", V2)
    assert r.returncode == 0, r.stderr
    st = box.state()
    assert st["version"] == V2 and st["previous_version"] == V1
    assert box.envfile()["SECAI_VERSION"] == V2
    assert box.ups()[-1]["SECAI_VERSION"] == V2
    assert json.loads((box.home / "stack" / "secai-stack.json").read_text())["version"] == V2
    assert (box.home / "releases" / V1).is_dir()   # giữ bản trước để rollback
    assert list((box.home / "env-backups").iterdir())


def test_upgrade_same_version_noop(box):
    installed(box)
    n = len(box.ups())
    r = box.run("upgrade", V1)
    assert r.returncode == 0 and len(box.ups()) == n


def test_upgrade_check_fail_auto_rollback(box):
    installed(box)
    box.set("check_fail", V2)
    r = box.run("upgrade", V2)
    assert r.returncode == 3, r.stderr
    st = box.state()
    assert st["version"] == V1 and st["last_failed_upgrade"] == V2
    assert [u["SECAI_VERSION"] for u in box.ups()[-2:]] == [V2, V1]
    env = box.envfile()
    assert env["SECAI_VERSION"] == V1
    assert env["GATEWAY_IMAGE"].startswith(f"{REG}/secai-agent-gateway:{V1}@")
    assert json.loads((box.home / "stack" / "secai-stack.json").read_text())["version"] == V1


def test_upgrade_check_fail_both_exit4(box):
    installed(box)
    box.set("check_fail", f"{V1} {V2}")
    r = box.run("upgrade", V2)
    assert r.returncode == 4
    assert box.state()["version"] == V1


def test_upgrade_pull_fail_changes_nothing(box):
    installed(box)
    n = len(box.ups())
    box.set("pull_fail", f"secai-agent-gateway:{V2}")
    r = box.run("upgrade", V2)
    assert r.returncode == 1
    assert len(box.ups()) == n and box.state()["version"] == V1
    assert box.envfile()["SECAI_VERSION"] == V1


def test_rollback(box):
    installed(box)
    assert box.run("upgrade", V2).returncode == 0
    r = box.run("rollback")
    assert r.returncode == 0, r.stderr
    st = box.state()
    assert st["version"] == V1 and st["previous_version"] == V2
    assert box.ups()[-1]["SECAI_VERSION"] == V1


def test_rollback_without_previous(box):
    installed(box)
    r = box.run("rollback")
    assert r.returncode == 1 and "không có bản trước" in r.stderr


def test_data_update_ok_keeps_old(box):
    installed(box)
    r = box.run("data", "update", D2)
    assert r.returncode == 0, r.stderr
    st = box.state()
    assert st["data_version"] == D2 and st["previous_data_version"] == D1
    assert box.envfile()["DATA_ROOT"] == str(box.home / "data" / D2)
    assert (box.home / "data" / D1 / ".secai-data-complete").exists()
    assert (box.home / "data" / D2 / ".secai-data-complete").exists()
    assert box.ups()[-1]["DATA_ROOT"].endswith(D2)


def test_data_update_fail_reverts(box):
    installed(box)
    box.set("check_fail", D2)
    r = box.run("data", "update", D2)
    assert r.returncode == 3, r.stderr
    assert box.state()["data_version"] == D1
    assert box.envfile()["DATA_ROOT"] == str(box.home / "data" / D1)
    assert box.ups()[-1]["DATA_ROOT"].endswith(D1)
    assert (box.home / "data" / D2 / ".secai-data-complete").exists()   # giữ để thử lại, không xoá


def test_data_rollback(box):
    installed(box)
    assert box.run("data", "update", D2).returncode == 0
    r = box.run("data", "rollback")
    assert r.returncode == 0, r.stderr
    assert box.state()["data_version"] == D1 and box.envfile()["SECAI_DATA_VERSION"] == D1


def test_data_update_bad_version(box):
    installed(box)
    r = box.run("data", "update", "2026-11-28")
    assert r.returncode == 1 and "không hợp lệ" in r.stderr


def test_lock_blocks_concurrent(box):
    installed(box)
    with open(box.home / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = box.run("upgrade", V2)
    assert r.returncode == 1 and "đang chạy" in r.stderr


def test_not_installed_commands_refuse(box):
    for args in (["status"], ["upgrade", V2], ["rollback"], ["data", "update", D2], ["check"]):
        r = box.run(*args)
        assert r.returncode == 1 and "chưa cài" in r.stderr, args


def test_down_never_removes_volumes(box):
    installed(box)
    assert box.run("down").returncode == 0
    downs = [c for c in box.calls() if c[0] == "compose" and "down" in c]
    assert downs and all("-v" not in c and "--volumes" not in c for c in downs)


def test_disk_guard(box):
    r = box.run("install", V1, env=box.install_env(SECAI_MIN_FREE_GB="999999"))
    assert r.returncode == 1 and "GB trống" in r.stderr
    assert not any(c[0] == "pull" for c in box.calls())



# ───────────── dữ liệu private: token chỉ đọc, DOCKER_CONFIG tạm, gói có sẵn ─────────────
def test_data_pull_uses_temp_docker_config_and_cleans_up(box):
    r = installed(box)
    logins = (box.fake / "login.cfg").read_text().split()
    assert logins and all(not Path(c).exists() for c in logins)        # DOCKER_CONFIG tạm đã xoá
    assert not (box.tmp / "home" / ".docker").exists()                  # không để credential trong ~/.docker
    assert (box.fake / "login.stdin").read_text() == GTOKEN            # token chỉ đi qua stdin
    allcalls = (box.fake / "calls.log").read_text() + (box.fake / "curl.log").read_text()
    assert GTOKEN not in allcalls and GTOKEN not in r.stdout + r.stderr
    for p in box.home.rglob("*"):
        if p.is_file():
            assert GTOKEN not in p.read_text(errors="replace"), p
    login = [c for c in box.calls() if c[0] == "login"][0]
    assert login == ["login", "-u", "testns", "--password-stdin"]   # Docker Hub (không ghi server), user = namespace


def test_secret_env_not_inherited_by_direct_children(box):
    e = box.install_env(SECAI_DOCKERHUB_TOKEN=GTOKEN)
    del e["SECAI_DOCKERHUB_TOKEN_FILE"]
    r = box.run("install", V1, env=e)
    assert r.returncode == 0, r.stderr
    for line in (box.fake / "curl.env").read_text().splitlines():
        keys = json.loads(line)
        assert "SECAI_DOCKERHUB_TOKEN" not in keys and "LLM_API_KEY" not in keys and "EMBEDDING_API_KEY" not in keys


def test_token_file_must_be_private(box):
    box.tokfile.chmod(0o644)
    r = box.run("install", V1, env=box.install_env())
    assert r.returncode == 1 and "mode 600/400" in r.stderr
    assert not box.ups()


def test_wrong_token_fails_and_cleans(box):
    box.tokfile.write_text("ghp_wrong\n")
    r = box.run("install", V1, env=box.install_env())
    assert r.returncode == 1 and "token" in r.stderr
    assert all(not Path(c).exists() for c in (box.fake / "login.cfg").read_text().split())
    assert "ghp_wrong" not in r.stdout + r.stderr


def test_no_token_non_interactive_points_to_data_dir(box):
    e = box.install_env(); del e["SECAI_DOCKERHUB_TOKEN_FILE"]
    r = box.run("install", V1, env=e)
    assert r.returncode == 1 and "--data-dir" in r.stderr and not box.ups()


def test_install_with_data_dir_needs_no_token(box):
    e = box.install_env(); del e["SECAI_DOCKERHUB_TOKEN_FILE"]
    pkg = box.data_pkg(D1)
    r = box.run("install", V1, "--data-dir", str(pkg), env=e)
    assert r.returncode == 0, r.stderr
    assert box.state()["data_version"] == "2026.11.26"      # lấy từ "created" trong MANIFEST.md
    assert not any(c[0] == "login" for c in box.calls())
    assert not any(c[0] == "pull" and "secai-data" in c[-1] for c in box.calls())
    assert pkg.exists() and (pkg / "SHA256SUMS").exists()   # gói của người vận hành không bị xoá
    marker = json.loads((box.home / "data" / "2026.11.26" / ".secai-data-complete").read_text())
    assert marker["digest"].startswith("dir:")


def test_data_load_from_dir_and_version_arg(box):
    installed(box)
    r = box.run("data", "load", str(box.data_pkg(D2)), "2026.11.28.2")
    assert r.returncode == 0, r.stderr
    st = box.state()
    assert st["data_version"] == "2026.11.28.2" and st["previous_data_version"] == D1
    assert box.envfile()["DATA_ROOT"].endswith("/data/2026.11.28.2")
    r = box.run("data", "load", str(box.tmp / "nope"))
    assert r.returncode == 1 and "SHA256SUMS" in r.stderr


# ───────────── sửa sau review: không để trạng thái lẫn lộn, dữ liệu trùng nhãn, ký tự nguy hiểm ─────────────
def stack_version(box):
    return json.loads((box.home / "stack" / "secai-stack.json").read_text())["version"]


def test_rollback_pull_failure_changes_nothing(box):
    installed(box)
    assert box.run("upgrade", V2).returncode == 0
    # image của V1 không còn cục bộ và kéo lỗi
    local = json.loads((box.fake / "local.json").read_text())
    (box.fake / "local.json").write_text(json.dumps([x for x in local if V1 not in x]))
    box.set("pull_fail", f"secai-agent-gateway:{V1}")
    n = len(box.ups())
    r = box.run("rollback")
    assert r.returncode == 1 and "KHÔNG đổi gì" in r.stderr
    assert box.state()["version"] == V2 and stack_version(box) == V2 and box.envfile()["SECAI_VERSION"] == V2
    assert len(box.ups()) == n


def test_upgrade_preflight_failure_restores_files(box):
    installed(box)
    # bản V2 hỏng preflight: bootstrap.sh của V2 trả lỗi ở preflight
    bs = box.tmp / f"bundle-{V2}" / "stack" / "bootstrap.sh"
    bs.write_text(bs.read_text().replace('    preflight) preflight ;;', '    preflight) echo "FAIL: giả" >&2; exit 1 ;;'))
    n = len(box.ups())
    r = box.run("upgrade", V2)
    assert r.returncode == 1, r.stderr
    assert stack_version(box) == V1 and box.envfile()["SECAI_VERSION"] == V1 and box.state()["version"] == V1
    assert len(box.ups()) == n


def test_upgrade_same_version_repairs_inconsistent_files(box):
    installed(box)
    shutil.copy(box.tmp / f"bundle-{V2}" / "stack" / "secai-stack.json", box.home / "stack" / "secai-stack.json")
    r = box.run("upgrade", V1)
    assert r.returncode == 0, r.stderr
    assert "khôi phục" in r.stderr and stack_version(box) == V1 and box.ups()[-1]["SECAI_VERSION"] == V1


def test_data_load_same_label_different_content_refused(box):
    e = box.install_env(); del e["SECAI_DOCKERHUB_TOKEN_FILE"]
    assert box.run("install", V1, "--data-dir", str(box.data_pkg(D1)), env=e).returncode == 0
    # gói khác (D2) nhưng cùng nhãn ngày MANIFEST -> phải từ chối, không im lặng dùng bản cũ
    other = box.tmp / "other"
    shutil.copytree(box.data_pkg(D2), other)
    r = box.run("data", "load", str(other))
    assert r.returncode == 1 and "nội dung khác" in r.stderr
    r = box.run("data", "load", str(other), "2026.11.26.2")
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("val", ["ab$Cd1234567890", "ab'cd1234567890", 'ab"cd1234567890', "ab`cd1234567890", "ab\\cd1234567890"])
def test_settings_with_shell_special_chars_rejected(box, val):
    r = box.run("install", V1, env=box.install_env(LLM_API_KEY=val))
    assert r.returncode == 1 and "LLM_API_KEY" in r.stderr and not box.ups()
    assert val not in r.stdout + r.stderr


def test_install_does_not_print_review_token(box):
    r = installed(box)
    assert "REVIEW_ADMIN_TOKEN" not in r.stdout + r.stderr


def test_optional_project_name(box):
    r = box.run("install", V1, env=box.install_env(COMPOSE_PROJECT_NAME="secai-next", POC_LABS_EXTERNAL="true"))
    assert r.returncode == 0, r.stderr
    env = box.envfile()
    assert env["COMPOSE_PROJECT_NAME"] == "secai-next" and env["POC_LABS_EXTERNAL"] == "true"
    r = box.run("install", V1, env=box.install_env(COMPOSE_PROJECT_NAME="Bad Name"))
    assert r.returncode == 1



# ───────────── dữ liệu trên Docker Hub private; image nền Docker Hub kéo bằng phiên đăng nhập nếu có token ─────────────
def pulls(box):
    return [json.loads(l) for l in (box.fake / "pull.log").read_text().splitlines()]


def test_base_images_pulled_with_hub_session_when_token(box):
    installed(box)
    p = dict((ref, cfg) for ref, cfg in pulls(box))
    for k in ("QDRANT_IMAGE", "NEO4J_IMAGE", "REDIS_IMAGE"):
        e = box.pins(V1)[k]
        assert p[f"{e['ref']}@{e['digest']}"], k              # kéo trong DOCKER_CONFIG tạm đã đăng nhập
    assert p[f"{DATA}:{D1}"]
    assert all(not Path(c).exists() for c in p.values() if c)   # mọi DOCKER_CONFIG tạm đã xoá


def test_no_token_with_data_dir_warns_and_pulls_anonymously(box):
    e = box.install_env(); del e["SECAI_DOCKERHUB_TOKEN_FILE"]
    r = box.run("install", V1, "--data-dir", str(box.data_pkg(D1)), env=e)
    assert r.returncode == 0, r.stderr
    assert "ẩn danh" in r.stderr and not any(c[0] == "login" for c in box.calls())
    assert all(cfg == "" for _, cfg in pulls(box))


def test_data_repo_override(box):
    other = "docker.io/otherns/secai-data"
    box.reg[f"{other}:{D1}"] = dict(box.reg[f"{DATA}:{D1}"]); box.save()
    r = box.run("install", V1, env=box.install_env(SECAI_DATA_REPO=other))
    assert r.returncode == 0, r.stderr
    assert any(ref == f"{other}:{D1}" for ref, _ in pulls(box))
    assert [c for c in box.calls() if c[0] == "login"][0][2] == "otherns"
