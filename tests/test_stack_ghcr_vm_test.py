"""stack/acceptance/ghcr-vm-test.sh chay THAT voi curl/ip/docker/secai/model stub GIA.

Khong mang, khong docker that, khong /opt, /root: RESULTS_DIR/SECAI_BIN/STUB_PY/SECAI_HOME tro vao thu muc tam. secai gia giu trang
thai phien ban trong tep JSON va mo phong upgrade hong khi stub tat (rc 3/4 = da tu rollback). Khoa stub va moc TOKMARK chung minh
khoa chi di qua env da export (khong len argv cua bat ky tien trinh gia nao) va log duoc che truoc SHA256SUMS.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1] / "stack"
VMTEST = STACK / "acceptance" / "ghcr-vm-test.sh"
REF = "0123456789abcdef0123456789abcdef01234567"
V1, V2, D1, D2 = "2026.11.27-0f980c19", "2026.11.28-1a2b3c4d", "2026.11.27", "2026.11.27.2"
STUBKEY = "stub-test-key-not-secret"
LEAK = "TOKMARK_51e7"
ALL_STEPS = ["facts", "fetch", "stub", "install", "status1", "check1", "acceptance", "upgrade", "status2", "rollback", "status3",
             "dataupdate", "status4", "check2", "failtest", "status5", "stub_restart", "check3"]
ENV_KEYS = ("SECAI_HOME", "SECAI_YES", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "EMBEDDING_API_URL", "EMBEDDING_MODEL",
            "EMBEDDING_API_KEY", "RERANKER_API_URL", "RERANKER_MODEL", "RERANKER_API_KEY", "VERIFY_ALLOWED_TARGETS", "GATEWAY_BIND",
            "SECAI_DATA_VERSION")

FAKE_SECAI = r'''#!/usr/bin/env python3
# secai gia: argv -> secai.log, env (cac khoa quan tam) -> secai-env.jsonl; trang thai phien ban trong secai-state.json.
import json, os, sys
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]
def rd(n, default=""):
    try: return open(f"{d}/{n}").read().strip()
    except OSError: return default
open(d + "/secai.log", "a").write(" ".join(a) + "\n")
if os.path.exists(d + "/read_stdin"):   # lenh doc stdin khong duoc nuot phan con lai cua script (ssh ... bash -s < script)
    open(d + "/stdin-bytes", "a").write(str(len(sys.stdin.read())) + "\n")
with open(d + "/secai-env.jsonl", "a") as f:
    f.write(json.dumps({"argv": a, "env": {k: os.environ.get(k) for k in KEYS}}) + "\n")
sp = d + "/secai-state.json"
st = json.load(open(sp)) if os.path.exists(sp) else {}
def save(): json.dump(st, open(sp, "w"))
def stub_up():
    try:
        os.kill(int(open(os.environ["FAKE_PIDF"]).read()), 0); return True
    except Exception:
        return False
if a[0] == "install":
    st.update(version=a[1], data_version=os.environ.get("SECAI_DATA_VERSION"), previous_version=None, previous_data_version=None)
    save()
    print("LLM_API_KEY=" + os.environ.get("LLM_API_KEY", ""))   # log ro ri: phai bi che truoc SHA256SUMS
    print("GHCR_TOKEN=" + LEAK)
    sys.exit(int(rd("install_rc", "0")))
if a[0] == "status":
    s = dict(st, images_match=rd("images_match", "true") == "true", services=[{"service": "gateway", "state": "running", "health": "healthy"}])
    if rd("status_version"):
        s["version"] = rd("status_version")
    print(json.dumps(s)); sys.exit(0)
if a[0] == "check":
    sys.exit(int(rd("check_rc", "0")))
if a[0] == "upgrade":
    r = 0 if stub_up() else int(rd("upgrade_down_rc", "3"))   # stub tat -> check hong -> tu rollback (3) / rollback ma check van hong (4)
    if r == 0:
        st.update(previous_version=st["version"], version=a[1]); save()
    sys.exit(r)
if a[0] == "rollback":
    st["version"], st["previous_version"] = st["previous_version"], st["version"]; save(); sys.exit(0)
if a[:2] == ["data", "update"]:
    st.update(previous_data_version=st["data_version"], data_version=a[2]); save(); sys.exit(0)
if a[:2] == ["data", "load"]:   # data load DIR VERSION
    st.update(previous_data_version=st["data_version"], data_version=a[3]); save(); sys.exit(0)
sys.exit(9)
'''.replace("KEYS", repr(ENV_KEYS)).replace("LEAK", repr(LEAK))

FAKE_STUB = r'''import os, sys, time
open(os.environ["FAKE_DIR"] + "/stub.argv", "a").write(" ".join(sys.argv[1:]) + "\n")
open(os.environ["FAKE_DIR"] + "/stub.env", "a").write(repr(sorted(k for k in os.environ if "KEY" in k)) + "\n")
time.sleep(120)
'''

FAKE_CURL = r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_DIR/curl.log"
out=""; url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out=$2; shift 2 ;;
        --retry|--max-time) shift 2 ;;
        -*) shift ;;
        *) url=$1; shift ;;
    esac
done
case "$url" in
    */health) p=$(cat "$FAKE_PIDF" 2>/dev/null) && kill -0 "$p" 2>/dev/null && { echo '{"ok":true}'; exit 0; }; exit 7 ;;
esac
[ -f "$FAKE_DIR/fetch_fail" ] && exit 22
case "$url" in
    https://raw.githubusercontent.com/*/stack/secai) cp "$FAKE_DIR/src/secai" "$out" ;;
    https://raw.githubusercontent.com/*/stack/acceptance/stubs/model_stub.py) cp "$FAKE_DIR/src/model_stub.py" "$out" ;;
    *) exit 22 ;;
esac
'''

FAKE_RUN_API = r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_DIR/run-api.log"
pwd > "$FAKE_DIR/run-api.cwd"
printf '%s' "$SKIP_CASES" > "$FAKE_DIR/skip_cases"
printf '%s' "$SKIP_REASONS" > "$FAKE_DIR/skip_reasons"
mkdir -p "$ACCEPT_OUT"
cp "$FAKE_DIR/acc.json" "$ACCEPT_OUT/summary.json"
exit "$(cat "$FAKE_DIR/acc_rc" 2>/dev/null || echo 0)"
'''

OK_ACC = {"pass": ["UI-CHAT-1"], "fail": [], "skipped": {"UI-TR-2": "x"}}


def exe(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    p.chmod(0o755)


class VM:
    def __init__(self, tmp: Path):
        self.tmp, self.fake, self.bin = tmp, tmp / "fake", tmp / "bin"
        self.R, self.home, self.root = tmp / "results", tmp / "secai-home", tmp / "root"
        self.root.mkdir()
        exe(self.fake / "src/secai", FAKE_SECAI)
        (self.fake / "src/model_stub.py").write_text(FAKE_STUB)
        (self.fake / "acc.json").write_text(json.dumps(OK_ACC))
        exe(self.bin / "curl", FAKE_CURL)
        exe(self.bin / "ip", '#!/bin/sh\necho "$*" >> "$FAKE_DIR/ip.log"\n'
            "echo '4: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever'\n")
        exe(self.bin / "docker", '#!/bin/sh\necho "$*" >> "$FAKE_DIR/docker.log"\necho "Docker version 29.1.3 (fake)"\n')
        exe(self.home / "stack/acceptance/run-api.sh", FAKE_RUN_API)
        self.stub_py = self.root / "model_stub.py"
        self.secai = self.root / "secai"
        self.env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp), "FAKE_DIR": str(self.fake),
                    "FAKE_PIDF": str(self.root / "model_stub.pid"), "RESULTS_DIR": str(self.R), "SECAI_BIN": str(self.secai),
                    "STUB_PY": str(self.stub_py), "SECAI_HOME": str(self.home),
                    "REF": REF, "V1": V1, "V2": V2, "D1": D1, "D2": D2, "REPO": "elonma-china/secai-prod", "FAILTEST": "1"}

    def set(self, name: str, value: str) -> None:
        (self.fake / name).write_text(value)

    def run(self, **extra):
        env = {**self.env, **{k: v for k, v in extra.items() if v is not None}}
        for k, v in extra.items():
            if v is None:
                env.pop(k, None)
        return subprocess.run(["bash", str(VMTEST)], env=env, text=True, capture_output=True, timeout=120)

    def log(self, name: str) -> list[str]:
        p = self.fake / name
        return p.read_text().splitlines() if p.exists() else []

    def steps(self) -> list[tuple[str, str]]:
        return [tuple(x.split(" ", 1)) for x in (self.R / "steps.txt").read_text().splitlines()]

    def summary(self) -> dict:
        return json.loads((self.R / "summary.json").read_text())

    def secai_env(self) -> list[dict]:
        return [json.loads(x) for x in self.log("secai-env.jsonl")]


@pytest.fixture
def vm(tmp_path):
    return VM(tmp_path)


def all_result_text(R: Path) -> str:
    return "\n".join(f.read_text(errors="replace") for f in R.rglob("*") if f.is_file())


def sha_ok(R: Path) -> bool:
    return subprocess.run(["sha256sum", "-c", "--strict", "--quiet", "SHA256SUMS"], cwd=R, capture_output=True).returncode == 0


def test_bash_syntax():
    assert subprocess.run(["bash", "-n", str(VMTEST)], capture_output=True).returncode == 0


def test_full_run_passes_in_operator_order(vm):
    r = vm.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert vm.steps() == [(s, "rc=0") for s in ALL_STEPS]
    assert vm.log("secai.log") == [f"install {V1}", "status --json", "check", f"upgrade {V2}", "status --json", "rollback",
                                   "status --json", f"data update {D2}", "status --json", "check", f"upgrade {V2}", "status --json",
                                   "check"]
    s = vm.summary()
    assert s["verdict"] == "pass" and s["failtest"] is True and s["failtest_upgrade_rc"] == 3
    assert s["versions"] == {"ref": REF, "repo": "elonma-china/secai-prod", "v1": V1, "v2": V2, "d1": D1, "d2": D2, "data_dir": ""}
    assert s["acceptance"] == OK_ACC and set(s["timings_s"]) == set(ALL_STEPS)
    # tai dung hai tep tu raw GitHub o dung commit, ghi sha256
    urls = [x for x in vm.log("curl.log") if "raw.githubusercontent.com" in x]
    assert any(f"https://raw.githubusercontent.com/elonma-china/secai-prod/{REF}/stack/secai " in x + " " for x in urls)
    assert any(f"/{REF}/stack/acceptance/stubs/model_stub.py" in x for x in urls)
    fetch = (vm.R / "fetch.log").read_text()
    assert str(vm.secai) in fetch and str(vm.stub_py) in fetch and len([x for x in fetch.split() if len(x) == 64]) == 2
    # stub tren IP docker0, cong 18000; khoi dong lai sau failtest
    assert vm.log("stub.argv") == ["--host 172.17.0.1 --port 18000"] * 2
    assert vm.log("ip.log") and not (vm.root / "model_stub.pid").exists()   # stub da dung o cuoi
    for n in (1, 2, 3, 4, 5):
        assert (vm.R / f"status-{n}.json").is_file()
    # acceptance: chay trong $SECAI_HOME/stack, bo dung 5 ca kem ly do
    assert vm.log("run-api.log") == ["http://127.0.0.1:8131"]
    assert vm.log("run-api.cwd") == [str(vm.home / "stack")]
    assert (vm.fake / "skip_cases").read_text().split() == ["UI-POC-1", "UI-POC-2", "UI-POC-3", "UI-TR-2", "UI-TR-3"]
    reasons = json.loads((vm.fake / "skip_reasons").read_text())
    assert set(reasons) == {"UI-POC-1", "UI-POC-2", "UI-POC-3", "UI-TR-2", "UI-TR-3"}
    assert all("LLM thật" in reasons[f"UI-POC-{i}"] for i in (1, 2, 3))
    assert "translation" in reasons["UI-TR-2"] and "translation" in reasons["UI-TR-3"]
    assert sha_ok(vm.R)


def test_keys_only_in_exported_env_never_on_argv(vm):
    assert vm.run().returncode == 0
    argv = "\n".join(vm.log("curl.log") + vm.log("secai.log") + vm.log("stub.argv") + vm.log("ip.log") + vm.log("docker.log")
                     + vm.log("run-api.log"))
    assert STUBKEY not in argv and "KEY" not in argv
    calls = vm.secai_env()
    inst = calls[0]
    assert inst["argv"] == ["install", V1]
    want = {"SECAI_HOME": str(vm.home), "SECAI_YES": "1", "LLM_BASE_URL": "http://172.17.0.1:18000/v1",
            "LLM_MODEL": "DuyTa/Cyber-F1-AWQ", "LLM_API_KEY": STUBKEY, "EMBEDDING_API_URL": "http://172.17.0.1:18000",
            "EMBEDDING_MODEL": "DuyTa/sec-embedding", "EMBEDDING_API_KEY": STUBKEY, "RERANKER_API_URL": "http://172.17.0.1:18000",
            "RERANKER_MODEL": "DuyTa/sec-rerank", "RERANKER_API_KEY": STUBKEY, "VERIFY_ALLOWED_TARGETS": "10.250.0.0/24",
            "GATEWAY_BIND": "127.0.0.1", "SECAI_DATA_VERSION": D1}
    assert inst["env"] == want
    # cau hinh install chi cho install; cac lenh sau chi co SECAI_HOME + SECAI_YES (khong SECAI_DATA_VERSION cu de lan data update)
    for c in calls[1:]:
        assert c["env"]["SECAI_HOME"] == str(vm.home) and c["env"]["SECAI_YES"] == "1"
        assert all(c["env"][k] is None for k in ENV_KEYS if k not in ("SECAI_HOME", "SECAI_YES")), c
    assert vm.log("stub.env") == ["[]", "[]"]   # stub khong nhan khoa nao


def test_logs_redacted_before_sha256sums(vm):
    assert vm.run().returncode == 0
    text = all_result_text(vm.R)
    assert LEAK not in text and STUBKEY not in text
    inst = (vm.R / "install.log").read_text()
    assert "LLM_API_KEY=<redacted>" in inst and "GHCR_TOKEN=<redacted>" in inst
    assert (vm.R / "SHA256SUMS").stat().st_size > 0 and sha_ok(vm.R)
    listed = {x.split("  ", 1)[1] for x in (vm.R / "SHA256SUMS").read_text().splitlines()}
    assert {"./summary.json", "./steps.txt", "./install.log", "./status-1.json", "./acceptance/summary.json"} <= listed


def test_docker0_detection_skipped_with_stub_host(vm):
    r = vm.run(STUB_HOST="127.0.0.1")
    assert r.returncode == 0, r.stderr
    assert vm.log("ip.log") == [] and vm.log("stub.argv")[0] == "--host 127.0.0.1 --port 18000"
    assert vm.secai_env()[0]["env"]["LLM_BASE_URL"] == "http://127.0.0.1:18000/v1"


@pytest.mark.parametrize("bad", [
    {"REF": "0123abc"}, {"REF": REF.upper()}, {"REF": None}, {"REF": REF + ";id"},
    {"V1": "2026.11.27"}, {"V1": "stable"}, {"V2": V1}, {"V2": None},
    {"D1": "2026-11-27"}, {"D1": "2026.11.27.123"}, {"D2": D1}, {"D2": "latest"},
    {"REPO": "noslash"}, {"REPO": "a/b/c"}, {"REPO": "a/b;x"},
    {"FAILTEST": "2"}, {"STUB_HOST": "1.2.3.4;x"},
])
def test_invalid_inputs_exit_before_doing_anything(vm, bad):
    r = vm.run(**bad)
    assert r.returncode != 0
    assert not vm.R.exists() and not list(vm.fake.glob("*.log")) and not vm.secai.exists()


def test_rerun_moves_previous_results(vm):
    assert vm.run().returncode == 0
    (vm.fake / "secai-state.json").unlink()
    assert vm.run().returncode == 0
    prev = list(vm.tmp.glob("results.prev-*"))
    assert len(prev) == 1 and json.loads((prev[0] / "summary.json").read_text())["verdict"] == "pass"
    assert vm.summary()["verdict"] == "pass"


def test_empty_results_dir_from_up_is_reused(vm):
    vm.R.mkdir()
    assert vm.run().returncode == 0
    assert not list(vm.tmp.glob("results.prev-*"))


@pytest.mark.parametrize("flag,value,step", [("status_version", "2026.01.01-deadbeef", "status1"),
                                              ("images_match", "false", "status1")])
def test_status_mismatch_fails_step_and_verdict(vm, flag, value, step):
    vm.set(flag, value)
    r = vm.run()
    assert r.returncode != 0
    steps = dict(vm.steps())
    assert steps[step] == "rc=1" and steps["install"] == "rc=0"
    log = (vm.R / f"{step}.log").read_text()
    assert "SAI" in log
    assert vm.summary()["verdict"] == "fail" and sha_ok(vm.R)


def test_status_assertions_per_step(vm):
    assert vm.run().returncode == 0
    st = {n: json.loads((vm.R / f"status-{n}.json").read_text()) for n in (1, 2, 3, 4, 5)}
    assert (st[1]["version"], st[1]["data_version"]) == (V1, D1)
    assert (st[2]["version"], st[2]["previous_version"]) == (V2, V1)
    assert st[3]["version"] == V1
    assert (st[4]["data_version"], st[4]["previous_data_version"]) == (D2, D1)
    assert st[5]["version"] == V1


@pytest.mark.parametrize("down_rc,ok", [("3", True), ("4", True), ("0", False), ("1", False)])
def test_failtest_accepts_only_auto_rollback(vm, down_rc, ok):
    vm.set("upgrade_down_rc", down_rc)
    r = vm.run()
    steps = dict(vm.steps())
    s = vm.summary()
    assert s["failtest_upgrade_rc"] == int(down_rc)
    assert (steps["failtest"] == "rc=0") is ok
    assert (r.returncode == 0) is ok and (s["verdict"] == "pass") is ok
    if down_rc == "0":
        assert steps["status5"] == "rc=1"   # upgrade lot qua: dang o V2, khong phai V1
    assert steps["stub_restart"] == "rc=0" and steps["check3"] == "rc=0"
    assert "rc=" + down_rc in (vm.R / "failtest.log").read_text()


def test_failtest_disabled(vm):
    r = vm.run(FAILTEST="0")
    assert r.returncode == 0, r.stderr
    assert [n for n, _ in vm.steps()] == ALL_STEPS[:ALL_STEPS.index("failtest")]
    assert vm.log("secai.log").count(f"upgrade {V2}") == 1
    s = vm.summary()
    assert s["failtest"] is False and s["failtest_upgrade_rc"] is None and s["verdict"] == "pass"


@pytest.mark.parametrize("flag,step", [("install_rc", "install"), ("fetch_fail", "fetch")])
def test_early_failure_aborts_rest_but_still_writes_results(vm, flag, step):
    vm.set(flag, "1")
    r = vm.run()
    assert r.returncode != 0
    steps = vm.steps()
    i = [n for n, _ in steps].index(step)
    assert steps[i] == (step, "rc=1") and all(v == "skipped" for _, v in steps[i + 1:])
    assert [n for n, _ in steps] == ALL_STEPS
    assert vm.summary()["verdict"] == "fail" and sha_ok(vm.R)
    assert not (vm.root / "model_stub.pid").exists()
    assert "upgrade" not in "\n".join(vm.log("secai.log"))


@pytest.mark.parametrize("acc,acc_rc,verdict", [
    ({"pass": ["UI-CHAT-1"], "fail": ["UI-RPT-1"], "skipped": {}}, "1", "fail"),
    ({"pass": [], "fail": [], "skipped": {"UI-CHAT-1": "x"}}, "0", "incomplete"),
])
def test_acceptance_result_drives_verdict(vm, acc, acc_rc, verdict):
    vm.set("acc.json", json.dumps(acc))
    vm.set("acc_rc", acc_rc)
    r = vm.run()
    assert r.returncode != 0 and vm.summary()["verdict"] == verdict
    assert dict(vm.steps())["upgrade"] == "rc=0"   # nghiem thu truot khong chan phan nang cap/rollback


def test_check_failure_fails_verdict_but_continues(vm):
    vm.set("check_rc", "1")
    r = vm.run()
    steps = dict(vm.steps())
    assert r.returncode != 0 and steps["check1"] == "rc=1" and steps["upgrade"] == "rc=0"
    assert vm.summary()["verdict"] == "fail"


def test_runs_over_stdin_like_ssh_and_steps_never_read_the_script(vm):
    vm.set("read_stdin", "1")
    r = subprocess.run(["bash", "-s"], input=VMTEST.read_text(), env=vm.env, text=True, capture_output=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert [n for n, _ in vm.steps()] == ALL_STEPS and vm.summary()["verdict"] == "pass"
    reads = vm.log("stdin-bytes")
    assert len(reads) == 13 and set(reads) == {"0"}
    assert "verdict=pass" in r.stdout


def test_lost_ssh_output_keeps_running_to_the_end(vm):
    # ssh dut: stdout/stderr dong. Script khong chet vi SIGPIPE, chay het, ghi console.log + SHA256SUMS de collect.
    env = dict(vm.env)
    p = subprocess.Popen(["bash", str(VMTEST)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p.stdout.close()
    p.stderr.close()
    assert p.wait(timeout=120) == 0
    assert vm.summary()["verdict"] == "pass" and sha_ok(vm.R)
    assert "check3 rc=0" in (vm.R / "console.log").read_text()



def test_data_dir_mode_uses_install_data_dir_and_data_load(vm):
    # secai-data private, VM không giữ token gh: gói dữ liệu được rsync lên, dùng --data-dir và data load
    r = vm.run(DATA_DIR="/opt/secai-stack/pkg")
    assert r.returncode == 0, r.stdout + r.stderr
    log = vm.log("secai.log")
    assert log[0] == f"install {V1} --data-dir /opt/secai-stack/pkg"
    assert f"data load /opt/secai-stack/pkg {D2}" in log and not any(x.startswith("data update") for x in log)
    assert vm.summary()["versions"]["data_dir"] == "/opt/secai-stack/pkg"


@pytest.mark.parametrize("bad", ["relative/pkg", "/opt/x;reboot", "/opt/a b"])
def test_data_dir_validated(vm, bad):
    r = vm.run(DATA_DIR=bad)
    assert r.returncode == 2 and not vm.log("secai.log")
