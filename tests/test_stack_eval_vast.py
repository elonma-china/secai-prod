"""stack/eval-vast.sh + stack/destroy-eval.sh voi vastai/ssh/scp/rsync/systemd-run GIA qua PATH.

Khong bao gio goi vastai that: PATH chi gom thu muc fake + /usr/bin:/bin. Instance prod 62088958 luon co trong danh sach
gia; tep PROD_DESTROYED xuat hien neu bat ky lenh destroy nao nham vao no (moi test deu khang dinh no khong ton tai).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1] / "stack"
EVAL = STACK / "eval-vast.sh"
DESTROY = STACK / "destroy-eval.sh"
PROD = "62088958"
SECRET = "SECRET_MARK_9f3a"
VASTKEY = "VASTKEY_MARK_77c1"

FAKE_VASTAI = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_DIR"]
open(d + "/vastai.log", "a").write(" ".join(sys.argv[1:]) + "\n")
def rd(n, default=""):
    try: return open(f"{d}/{n}").read().strip()
    except OSError: return default
def inst(): return json.load(open(d + "/instances.json"))
def save(x): json.dump(x, open(d + "/instances.json", "w"))
a = sys.argv[1:]
if a[:2] == ["search", "offers"]:
    print(open(d + "/offers.json").read()); sys.exit(0)
if a[:2] == ["create", "instance"]:
    mode = rd("create_mode", "ok")
    if mode == "fail":
        print("Error: boom"); sys.exit(1)
    offer = a[2]; label = a[a.index("--label") + 1]
    price = next((o["dph_total"] for o in json.load(open(d + "/offers.json")) if str(o["id"]) == offer), 0.25)
    x = inst(); x.append({"id": 777, "label": label, "dph_total": price, "actual_status": "running",
                          "ssh_host": "1.2.3.4", "ssh_port": 2222, "public_ipaddr": "1.2.3.4"}); save(x)
    if mode == "ambiguous":
        print("Error: gateway timeout"); sys.exit(1)
    print(json.dumps({"success": True, "new_contract": 777})); sys.exit(0)
if a[:2] == ["show", "instances-v1"] or a[:2] == ["show", "instances"]:
    m = rd("list_mode", "ok")
    if m == "deprecated": print("DEPRECATED: use `show instances-v2`"); sys.exit(0)
    if m == "garbage": print("{not json"); sys.exit(0)
    if m == "fail": sys.exit(1)
    print(json.dumps(inst())); sys.exit(0)
if a[:2] == ["destroy", "instance"]:
    ids = a[2:]
    if ids and ids[0] == "62088958": open(d + "/PROD_DESTROYED", "w").write("x")
    n = int(rd("destroy_count", "0")) + 1; open(d + "/destroy_count", "w").write(str(n))
    mode = rd("destroy_mode", "ok")
    if mode == "fail": print("Error"); sys.exit(1)
    if mode == "stuck" and n < 3: print("queued"); sys.exit(0)
    save([i for i in inst() if str(i["id"]) not in ids]); print("destroyed"); sys.exit(0)
print("unknown fake vastai command", a); sys.exit(9)
'''
FAKE_LOGGER = '''#!/bin/sh
# ghi argv vao {name}.log; ghi stdin (neu co) vao {name}.stdin; rc tu {name}_rc (mac dinh 0)
printf '%s\\n' "$*" >> "$FAKE_DIR/{name}.log"
if [ ! -t 0 ]; then cat >> "$FAKE_DIR/{name}.stdin"; fi
{extra}
rc=0; [ -f "$FAKE_DIR/{name}_rc" ] && rc=$(cat "$FAKE_DIR/{name}_rc")
exit "$rc"
'''


@pytest.fixture
def box(tmp_path: Path):
    fake = tmp_path / "fake"
    fake.mkdir()
    binp = tmp_path / "bin"
    binp.mkdir()
    (binp / "vastai").write_text(FAKE_VASTAI)
    (binp / "vastai").chmod(0o755)
    for name, extra in (("ssh", ""), ("scp", ""), ("rsync", ""), ("systemctl", ""),
                        ("systemd-run", 'echo "Running timer as unit: run-fake.timer" >&2')):
        (binp / name).write_text(FAKE_LOGGER.format(name=name, extra=extra))
        (binp / name).chmod(0o755)
    (fake / "instances.json").write_text(json.dumps([
        {"id": int(PROD), "label": "secai-prod-vast", "dph_total": 3.14, "actual_status": "running"}]))
    (fake / "offers.json").write_text(json.dumps([
        {"id": 1, "dph_total": 0.45, "cpu_ram": 65536, "disk_space": 200, "inet_down": 900, "reliability2": 0.99},
        {"id": 2, "dph_total": 0.25, "cpu_ram": 65536, "disk_space": 200, "inet_down": 900, "reliability2": 0.99},
        {"id": 3, "dph_total": 0.20, "cpu_ram": 8192, "disk_space": 200, "inet_down": 900, "reliability2": 0.99}]))
    home = tmp_path / "home"
    (home / ".config/vastai").mkdir(parents=True)
    (home / ".config/vastai/vast_api_key").write_text(VASTKEY)
    repo = tmp_path / "repo"
    (repo / "stack").mkdir(parents=True)
    (repo / "stack/x.txt").write_text("x")
    for c in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(repo), *c], check=True, capture_output=True)
    envf = tmp_path / "eval.env"
    envf.write_text(f"QWEN_API_KEY={SECRET}\n")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "SHA256SUMS").write_text("")
    key = tmp_path / "id"
    key.write_text("k")
    sdir = tmp_path / "eval-state"
    env = {"PATH": f"{binp}:/usr/bin:/bin", "HOME": str(home), "FAKE_DIR": str(fake), "STACK_EVAL_DIR": str(sdir),
           "EVAL_POLL_SLEEP": "0", "STACK_EVAL_DESTROY_WAIT": "0", "EVAL_REPO_ROOT": str(repo),
           "EVAL_ENV_FILE": str(envf), "EVAL_DATA_PKG": str(pkg), "EVAL_SSH_KEY": str(key)}

    class Box:
        pass
    b = Box()
    b.env, b.fake, b.sdir, b.tmp = env, fake, sdir, tmp_path
    return b


def sh(script: Path, *args: str, b, extra: dict[str, str] | None = None, stdin: str | None = None):
    return subprocess.run(["bash", str(script), *args], env={**b.env, **(extra or {})}, text=True, capture_output=True,
                          input=stdin, timeout=60)


def log(b, name: str) -> list[str]:
    p = b.fake / f"{name}.log"
    return p.read_text().splitlines() if p.exists() else []


def lines_with(b, name: str, word: str) -> list[str]:
    return [line for line in log(b, name) if word in line.split()]


def set_mode(b, name: str, value: str) -> None:
    (b.fake / name).write_text(value)


def write_state(b, id_="777", label="secai-stack-eval-20261123-ab12", max_dph=0.30, timers=("tB", "tD"), **kw) -> None:
    b.sdir.mkdir(parents=True, exist_ok=True)
    st = {"id": id_, "label": label, "created_at": 0, "deadline": 0, "price": 0.25, "max_dph": max_dph,
          "max_hours": 3, "timers": list(timers), **kw}
    (b.sdir / "state.json").write_text(json.dumps(st))


def add_instance(b, id_: int, label: str, price: float) -> None:
    f = b.fake / "instances.json"
    x = json.loads(f.read_text())
    x.append({"id": id_, "label": label, "dph_total": price, "actual_status": "running"})
    f.write_text(json.dumps(x))


def ids_listed(b) -> set[str]:
    return {str(i["id"]) for i in json.loads((b.fake / "instances.json").read_text())}


def assert_prod_alive(b) -> None:
    assert not (b.fake / "PROD_DESTROYED").exists()
    assert PROD in ids_listed(b)
    assert not any(PROD in line.split() for line in lines_with(b, "vastai", "destroy"))


def destroys(b) -> list[str]:
    return lines_with(b, "vastai", "destroy")


# ------------------------------------------------------------------ destroy-eval.sh

def test_destroy_exact_id_single_command_then_verifies_and_cancels_timers(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode == 0, r.stderr
    assert destroys(box) == ["destroy instance 777"]
    assert "777" not in ids_listed(box)
    stops = log(box, "systemctl")
    assert any("tB.timer" in s for s in stops) and any("tD.timer" in s for s in stops)
    assert_prod_alive(box)


def test_destroy_requires_confirmation_flag(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    r = sh(DESTROY, b=box)
    assert r.returncode != 0 and destroys(box) == []


def test_destroy_force_deadline_still_goes_through_checks(box):
    write_state(box, label="secai-stack-eval-20261123-ab12")
    add_instance(box, 777, "someone-elses-box", 0.25)  # label khong khop state
    r = sh(DESTROY, "--force-deadline", b=box)
    assert r.returncode != 0 and destroys(box) == []
    add_instance(box, 778, "x", 0.1)  # nhieu: chi 777 la ID trong state
    box.fake.joinpath("instances.json").write_text(json.dumps(
        [{"id": 777, "label": "secai-stack-eval-20261123-ab12", "dph_total": 0.25}, {"id": int(PROD), "label": "p", "dph_total": 3.14}]))
    r = sh(DESTROY, "--force-deadline", b=box)
    assert r.returncode == 0 and destroys(box) == ["destroy instance 777"]
    assert_prod_alive(box)


@pytest.mark.parametrize("protected_file", ["missing", "empty", "listed"])
def test_destroy_never_touches_prod_even_if_state_edited_and_label_matches(box, protected_file):
    # state bi sua thanh ID prod, label khop, protected file thieu/rong/co -> denylist cung van chan
    write_state(box, id_=PROD, label="secai-stack-eval-20261123-ab12")
    f = box.fake / "instances.json"
    x = json.loads(f.read_text())
    x[0]["label"] = "secai-stack-eval-20261123-ab12"  # prod co label giong
    x[0]["dph_total"] = 0.25
    f.write_text(json.dumps(x))
    extra = {}
    if protected_file != "missing":
        pf = box.tmp / "protected"
        pf.write_text("" if protected_file == "empty" else f"{PROD}\n")
        extra["STACK_EVAL_PROTECTED"] = str(pf)
    else:
        extra["STACK_EVAL_PROTECTED"] = str(box.tmp / "no-such-file")
    for flag in ("--yes", "--force-deadline"):
        r = sh(DESTROY, flag, b=box, extra=extra)
        assert r.returncode != 0
        assert destroys(box) == []
    assert_prod_alive(box)


def test_destroy_protected_file_adds_ids(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    pf = box.tmp / "protected"
    pf.write_text("111\n777\n")
    r = sh(DESTROY, "--yes", b=box, extra={"STACK_EVAL_PROTECTED": str(pf)})
    assert r.returncode != 0 and destroys(box) == []


@pytest.mark.parametrize("bad", ["", "  ", "*", "77*", "777 778", "777,778", "0777", "-1", "abc", "62088958 777", "777;62088958"])
def test_destroy_rejects_malformed_ids(box, bad):
    write_state(box, id_=bad)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode != 0 and destroys(box) == []
    assert_prod_alive(box)


def test_destroy_rejects_non_scalar_id_in_state(box):
    box.sdir.mkdir(parents=True)
    (box.sdir / "state.json").write_text(json.dumps({"id": [777, int(PROD)], "label": "secai-stack-eval-x", "max_dph": 0.3}))
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode != 0 and destroys(box) == []


def test_destroy_rejects_bad_label_prefix_and_state_mismatch(box):
    write_state(box, label="other-label")
    add_instance(box, 777, "other-label", 0.25)
    assert sh(DESTROY, "--yes", b=box).returncode != 0
    write_state(box, label="secai-stack-eval-20261123-ab12")
    assert sh(DESTROY, "--yes", b=box).returncode != 0  # instance that co label khac state
    assert destroys(box) == []


def test_destroy_rejects_price_above_one_and_half_cap(box):
    write_state(box, max_dph=0.30)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.46)
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode != 0 and destroys(box) == []
    box.fake.joinpath("instances.json").write_text(json.dumps(
        [{"id": 777, "label": "secai-stack-eval-20261123-ab12", "dph_total": 0.45}]))
    assert sh(DESTROY, "--yes", b=box).returncode == 0  # == 1.5 x cap van duoc


def test_destroy_instance_id_file_mismatch_refused(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    (box.sdir / "instance.id").write_text(PROD + "\n")
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode != 0 and destroys(box) == []
    assert_prod_alive(box)


def test_destroy_retries_until_gone(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    set_mode(box, "destroy_mode", "stuck")  # chi that su bien mat tu lan goi thu 3
    r = sh(DESTROY, "--yes", b=box, extra={"STACK_EVAL_DESTROY_RETRIES": "5"})
    assert r.returncode == 0, r.stderr
    assert destroys(box) == ["destroy instance 777"] * 3
    assert_prod_alive(box)


def test_destroy_gives_up_nonzero_and_keeps_timers(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    set_mode(box, "destroy_mode", "fail")
    r = sh(DESTROY, "--yes", b=box, extra={"STACK_EVAL_DESTROY_RETRIES": "3"})
    assert r.returncode != 0
    assert len(destroys(box)) == 3
    assert log(box, "systemctl") == []  # timer van chay de thu lai
    assert "777" in ids_listed(box)


def test_destroy_idempotent_when_already_gone(box):
    write_state(box)  # 777 khong co trong danh sach
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode == 0 and destroys(box) == []
    assert any("tB.timer" in s for s in log(box, "systemctl"))
    assert sh(DESTROY, "--force-deadline", b=box).returncode == 0
    assert_prod_alive(box)


@pytest.mark.parametrize("mode", ["deprecated", "garbage", "fail"])
def test_destroy_fails_safe_when_vastai_output_unparseable(box, mode):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    set_mode(box, "list_mode", mode)
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode != 0 and destroys(box) == []  # khong doan: khong destroy, cung khong bao "da huy"
    assert log(box, "systemctl") == []


def test_destroy_without_state_refuses(box):
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode != 0 and destroys(box) == []


def test_destroy_rejects_extra_args(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    assert sh(DESTROY, "--yes", PROD, b=box).returncode != 0
    assert sh(DESTROY, "--yes", "777", b=box).returncode != 0
    assert destroys(box) == []


# ------------------------------------------------------------------ eval-vast.sh up

def test_up_dry_run_searches_but_never_creates_or_writes(box):
    r = sh(EVAL, "up", "--dry-run", b=box)
    assert r.returncode == 0, r.stderr
    assert lines_with(box, "vastai", "search") and not lines_with(box, "vastai", "create")
    assert not box.sdir.exists() or not (box.sdir / "state.json").exists()
    assert log(box, "systemd-run") == [] and log(box, "ssh") == []
    assert "create instance 2 " in r.stdout  # offer re nhat hop le (0.25), khong phai 0.45 hay offer thieu RAM (id 3)


def test_up_search_blocks_price_over_cap(box):
    (box.fake / "offers.json").write_text(json.dumps(
        [{"id": 1, "dph_total": 0.45, "cpu_ram": 65536, "disk_space": 200, "inet_down": 900, "reliability2": 0.99}]))
    r = sh(EVAL, "up", "--dry-run", b=box)
    assert r.returncode != 0
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")


def test_up_refuses_cap_above_030_without_explicit_flag(box):
    r = sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_MAX_DPH": "0.50"})
    assert r.returncode != 0 and not log(box, "vastai")
    r = sh(EVAL, "up", "--dry-run", "--i-accept-higher", b=box, extra={"EVAL_MAX_DPH": "0.50"})
    assert r.returncode == 0
    r = sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_MAX_HOURS": "4"})
    assert r.returncode != 0


def test_up_full_flow_records_id_state_timers_and_is_secret_safe(box):
    r = sh(EVAL, "up", b=box)
    assert r.returncode == 0, r.stderr + r.stdout
    assert (box.sdir / "instance.id").read_text().strip() == "777"
    assert oct((box.sdir / "instance.id").stat().st_mode & 0o777) == "0o600"
    st = json.loads((box.sdir / "state.json").read_text())
    assert st["id"] == "777" and st["label"].startswith("secai-stack-eval-") and st["max_hours"] <= 3 and st["price"] <= 0.30
    assert st["deadline"] - st["created_at"] <= 3 * 3600
    runs = log(box, "systemd-run")
    assert len(runs) == 2 and all("destroy-eval.sh --force-deadline" in x for x in runs)
    assert "--on-active=10800" in runs[0] and "--on-active=11400" in runs[1]  # B = 3h, D = B + 10 phut
    assert len(st["timers"]) == 2
    assert "ABORT" not in r.stdout and "layer a" in r.stdout.lower()  # lop A bi bo vi khong co khoa instance, noi ro
    # secret chi di qua stdin cua ssh, khong bao gio qua argv; khoa tai khoan Vast khong len may thue
    argv_all = "\n".join(log(box, "ssh") + log(box, "scp") + log(box, "rsync") + log(box, "systemd-run") + log(box, "vastai"))
    assert SECRET not in argv_all and VASTKEY not in argv_all
    stdin_all = "".join((box.fake / f"{n}.stdin").read_text() for n in ("ssh", "scp", "rsync") if (box.fake / f"{n}.stdin").exists())
    assert SECRET in stdin_all and VASTKEY not in stdin_all
    assert SECRET not in r.stdout + r.stderr and VASTKEY not in r.stdout + r.stderr
    assert "cảnh báo" in r.stdout.lower() or "warning" in r.stdout.lower()  # canh bao chuyen key sang host ngoai
    assert destroys(box) == [] and (box.fake / "PROD_DESTROYED").exists() is False


def test_up_layer_a_only_with_instance_key_and_via_stdin(box):
    r = sh(EVAL, "up", b=box, extra={"EVAL_CONTAINER_API_KEY": "INSTKEY_MARK_5"})
    assert r.returncode == 0, r.stderr
    assert "INSTKEY_MARK_5" not in "\n".join(log(box, "ssh") + log(box, "systemd-run") + log(box, "scp") + log(box, "rsync"))
    assert "INSTKEY_MARK_5" in (box.fake / "ssh.stdin").read_text()
    assert "INSTKEY_MARK_5" not in r.stdout + r.stderr


def test_up_records_id_immediately_even_if_later_step_fails_then_destroys_via_exit_trap(box):
    set_mode(box, "ssh_rc", "255")  # moi ssh that bai sau khi da create
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0
    assert (box.sdir / "instance.id").read_text().strip() == "777"
    assert json.loads((box.sdir / "state.json").read_text())["id"] == "777"
    assert destroys(box) == ["destroy instance 777"]  # EXIT trap huy qua destroy-eval.sh
    assert_prod_alive(box)


def test_up_id_recorded_before_timer_step_failure(box):
    set_mode(box, "systemd-run_rc", "1")
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0
    assert (box.sdir / "instance.id").read_text().strip() == "777"
    assert destroys(box) == ["destroy instance 777"]  # khong co deadline thi khong duoc de may chay


def test_up_create_error_finds_orphan_by_label(box):
    set_mode(box, "create_mode", "ambiguous")  # vastai bao loi nhung instance da duoc tao
    r = sh(EVAL, "up", b=box)
    assert (box.sdir / "instance.id").read_text().strip() == "777"
    assert lines_with(box, "vastai", "instances-v1")
    assert r.returncode == 0, r.stderr


def test_up_create_error_no_orphan_fails_without_destroy(box):
    set_mode(box, "create_mode", "fail")
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0 and destroys(box) == []
    assert not (box.sdir / "instance.id").exists()


def test_up_refuses_when_previous_instance_still_recorded(box):
    write_state(box)
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")


def test_up_requires_env_file_and_data_pkg(box):
    for var in ("EVAL_ENV_FILE", "EVAL_DATA_PKG"):
        env = dict(box.env)
        env.pop(var)
        r = subprocess.run(["bash", str(EVAL), "up"], env=env, text=True, capture_output=True)
        assert r.returncode != 0 and not lines_with(box, "vastai", "create")


# ------------------------------------------------------------------ status / down / run / collect

def test_status_cross_checks_and_fails_safe_on_deprecated(box):
    now = int(time.time())
    write_state(box, created_at=now, deadline=now + 3600)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    r = sh(EVAL, "status", b=box)
    assert r.returncode == 0, r.stderr
    assert "777" in r.stdout and "secai-stack-eval-20261123-ab12" in r.stdout and "0.25" in r.stdout
    set_mode(box, "list_mode", "deprecated")
    r = sh(EVAL, "status", b=box)
    assert r.returncode != 0


def test_down_delegates_to_destroy_eval(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    r = sh(EVAL, "down", b=box)
    assert r.returncode == 0, r.stderr
    assert destroys(box) == ["destroy instance 777"]
    assert_prod_alive(box)


def test_run_executes_remote_sequence_via_ssh_stdin(box):
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222)
    r = sh(EVAL, "run", b=box)
    assert r.returncode == 0, r.stderr
    script = (box.fake / "ssh.stdin").read_text()
    for needle in ("bootstrap.sh init", "bootstrap.sh preflight", "check.sh all", "acceptance/run-api.sh"):
        assert needle in script
    assert SECRET not in "\n".join(log(box, "ssh"))


def test_run_propagates_failure_but_still_leaves_results(box):
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222)
    set_mode(box, "ssh_rc", "3")
    assert sh(EVAL, "run", b=box).returncode != 0


FAKE_RSYNC_PULL = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_DIR/rsync.log"
dest=$(eval echo \\${$#})
mkdir -p "$dest"
echo '{"ok": true}' > "$dest/check.log"
(cd "$dest" && sha256sum check.log > SHA256SUMS)
[ -f "$FAKE_DIR/tamper" ] && echo changed > "$dest/check.log"
exit 0
"""


def test_collect_pulls_results_only_and_verifies_sha256(box):
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222)
    (box.tmp / "bin" / "rsync").write_text(FAKE_RSYNC_PULL)
    r = sh(EVAL, "collect", b=box)
    assert r.returncode == 0, r.stderr
    rs = "\n".join(log(box, "rsync"))
    assert "/opt/secai-stack/results/" in rs
    assert "/opt/secai-stack/stack/.env" not in rs and SECRET not in rs and VASTKEY not in rs
    assert any(p.name == "check.log" for p in box.sdir.glob("results-*/*"))
    (box.fake / "tamper").write_text("1")
    r = sh(EVAL, "collect", b=box)
    assert r.returncode != 0 and "sha256" in r.stderr.lower()


def test_scripts_pass_bash_syntax_check():
    for s in (EVAL, DESTROY):
        assert subprocess.run(["bash", "-n", str(s)], capture_output=True).returncode == 0
