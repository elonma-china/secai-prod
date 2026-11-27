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
PRODROW = {"id": 62088958, "label": "secai-prod-vast", "dph_total": 3.14, "actual_status": "running"}
SECRET = "SECRET_MARK_9f3a"
VASTKEY = "VASTKEY_MARK_77c1"

FAKE_VASTAI = r'''#!/usr/bin/env python3
# Mo hinh vastai 1.3.0: destroy can -y (khong -y: "Aborted." rc 0, no-op); show instances-v1 phan trang (next_token);
# show instance --raw tra {"instances": null} khi khong co; loi van rc 0; thong bao cap nhat o stderr.
import json, os, sys
d = os.environ["FAKE_DIR"]
open(d + "/vastai.log", "a").write(" ".join(sys.argv[1:]) + "\n")
sys.stderr.write("update available: vastai 9.9.9\n")
def rd(n, default=""):
    try: return open(f"{d}/{n}").read().strip()
    except OSError: return default
def inst(): return json.load(open(d + "/instances.json"))
def save(x): json.dump(x, open(d + "/instances.json", "w"))
def bad(mode):
    if mode == "deprecated": print("DEPRECATED: use `show instances-v2`"); return True
    if mode == "garbage": print("{not json"); return True
    if mode == "fail": return True
    return False
a = sys.argv[1:]
if a[:2] == ["search", "offers"]:
    print(open(d + "/offers.json").read()); sys.exit(0)
if a[:2] == ["create", "instance"]:
    mode = rd("create_mode", "ok")
    if mode == "fail":
        print("Error: boom"); sys.exit(1)
    if mode == "prod":
        print(json.dumps({"success": True, "new_contract": 62088958})); sys.exit(0)
    offer = a[2]; label = a[a.index("--label") + 1]
    price = float(rd("create_price") or next((o["dph_total"] for o in json.load(open(d + "/offers.json")) if str(o["id"]) == offer), 0.25))
    x = inst(); x.append({"id": 777, "label": label, "dph_total": price, "actual_status": "running",
                          "ssh_host": "1.2.3.4", "ssh_port": 2222, "public_ipaddr": "1.2.3.4"}); save(x)
    if mode == "ambiguous":
        print("Error: gateway timeout"); sys.exit(1)
    print(json.dumps({"success": True, "new_contract": 777})); sys.exit(0)
if a[:2] == ["show", "user"]:
    if bad(rd("user_mode", "ok")): sys.exit(0)
    print(json.dumps({"id": int(rd("account_id", "4242")), "email": "x@example.invalid"})); sys.exit(0)
if a[:2] == ["show", "instances-v1"]:
    m = rd("list_mode", "ok")
    if bad(m): sys.exit(0)
    rows = [] if m == "empty" else inst()
    if m == "stringids": rows = [dict(r, id=str(r["id"])) for r in rows]
    if m == "floatids": rows = [dict(r, id=float(r["id"])) for r in rows]
    tok = a[a.index("--next-token") + 1] if "--next-token" in a else None
    off = int(tok[1:]) if tok else 0
    size = int(rd("page_size", "25"))
    page = rows[off:off + size]
    print(json.dumps({"instances": page, "next_token": ("p%d" % (off + size)) if off + size < len(rows) else None,
                      "total_instances": len(rows)})); sys.exit(0)
if a[:2] == ["show", "instance"]:
    if bad(rd("show_mode_after_destroy") if int(rd("destroy_count", "0")) > 0 and rd("show_mode_after_destroy") else rd("show_mode", "ok")):
        sys.exit(0)
    row = next((r for r in inst() if str(r["id"]) == a[2]), None)
    print(json.dumps(row if row else {"instances": None})); sys.exit(0)
if a[:2] == ["destroy", "instance"]:
    ids = [x for x in a[2:] if not x.startswith("-")]
    if "62088958" in a: open(d + "/PROD_DESTROYED", "w").write("x")
    if "-y" not in a and "--yes" not in a:
        print("\nAborted."); sys.exit(0)
    n = int(rd("destroy_count", "0")) + 1; open(d + "/destroy_count", "w").write(str(n))
    mode = rd("destroy_mode", "ok")
    if mode == "fail": print(json.dumps({"success": False, "msg": "nope"})); sys.exit(0)
    if mode == "stuck" and n < 3: print(json.dumps({"success": True})); sys.exit(0)
    save([i for i in inst() if str(i["id"]) not in ids]); print(json.dumps({"success": True})); sys.exit(0)
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
    (binp / "loginctl").write_text('#!/bin/sh\necho "$*" >> "$FAKE_DIR/loginctl.log"\ncat "$FAKE_DIR/linger" 2>/dev/null || echo Linger=yes\n')
    (binp / "loginctl").chmod(0o755)
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
          "max_hours": 3, "timers": list(timers), "account_id": "4242", **kw}
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


D777 = "destroy instance 777 -y --raw"


def destroys(b) -> list[str]:
    return lines_with(b, "vastai", "destroy")


# ------------------------------------------------------------------ destroy-eval.sh

def test_destroy_exact_id_single_command_then_verifies_and_cancels_timers(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode == 0, r.stderr
    assert destroys(box) == [D777]
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
    assert r.returncode == 0 and destroys(box) == [D777]
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
    assert "console" in r.stderr and "777" in r.stderr  # bao operator huy tay
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
    assert destroys(box) == [D777] * 3
    assert_prod_alive(box)


def test_destroy_gives_up_nonzero_and_keeps_timers(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    set_mode(box, "destroy_mode", "fail")
    r = sh(DESTROY, "--yes", b=box, extra={"STACK_EVAL_DESTROY_RETRIES": "3"})
    assert r.returncode != 0
    assert len(destroys(box)) == 3
    assert log(box, "systemctl") == []  # timer van chay de thu lai
    assert "console" in r.stderr and "777" in r.stderr  # to tieng yeu cau huy tay
    assert "777" in ids_listed(box)


def test_destroy_idempotent_when_already_gone(box):
    write_state(box)  # 777 khong co trong danh sach
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode == 0 and destroys(box) == []
    assert any("tB.timer" in s for s in log(box, "systemctl"))
    assert "destroyed_at" in json.loads((box.sdir / "state.json").read_text())
    assert sh(DESTROY, "--force-deadline", b=box).returncode == 0
    assert_prod_alive(box)


@pytest.mark.parametrize("mode", ["deprecated", "garbage", "fail"])
def test_destroy_fails_safe_when_vastai_output_unparseable(box, mode):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    set_mode(box, "show_mode", mode)
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode != 0 and destroys(box) == []  # khong doan: khong destroy, cung khong bao "da huy"
    assert log(box, "systemctl") == []


def test_destroy_passes_yes_flag_since_vastai_prompts_otherwise(box):
    # fake mo hinh vastai that: khong -y thi "Aborted." rc 0 va khong huy gi. Thieu -y => instance con => script phai that bai.
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode == 0, r.stderr
    assert D777 in destroys(box) and "-y" in destroys(box)[0].split()
    assert "777" not in ids_listed(box)


def test_destroy_failed_success_field_is_not_success(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    set_mode(box, "destroy_mode", "fail")  # rc 0 nhung {"success": false}
    r = sh(DESTROY, "--yes", b=box, extra={"STACK_EVAL_DESTROY_RETRIES": "2"})
    assert r.returncode != 0 and "success" in r.stderr


@pytest.mark.parametrize("mode", ["empty", "stringids", "floatids", "garbage"])
def test_destroy_never_decides_from_the_list(box, mode):
    # danh sach rong/id kieu khac/loi KHONG duoc anh huong: tra theo ID (show instance)
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    set_mode(box, "list_mode", mode)
    r = sh(DESTROY, "--yes", b=box)
    assert r.returncode == 0 and destroys(box) == [D777]
    assert not lines_with(box, "vastai", "instances-v1")


def test_destroy_gone_not_proven_by_unreadable_lookup_keeps_timers_and_state(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    set_mode(box, "show_mode_after_destroy", "garbage")  # that ra da huy, nhung khong chung minh duoc
    r = sh(DESTROY, "--yes", b=box, extra={"STACK_EVAL_DESTROY_RETRIES": "2"})
    assert r.returncode != 0
    assert log(box, "systemctl") == []
    assert "destroyed_at" not in json.loads((box.sdir / "state.json").read_text())


@pytest.mark.parametrize("mode", ["ok", "stringids", "floatids"])
def test_destroy_pending_state_finds_instance_by_label_across_pages(box, mode):
    lab = "secai-stack-eval-20261123-ab12"
    write_state(box, id_="", label=lab, pending=True, deadline=int(time.time()) + 3600)
    add_instance(box, 701, "decoy", 1.0)
    add_instance(box, 702, "decoy", 1.0)
    add_instance(box, 777, lab, 0.25)  # trang 2 (page_size 2: prod+701 | 702+777)
    set_mode(box, "page_size", "2")
    set_mode(box, "list_mode", mode)
    r = sh(DESTROY, "--force-deadline", b=box)
    assert r.returncode == 0, r.stderr
    assert destroys(box) == [D777]
    assert len([x for x in log(box, "vastai") if "instances-v1" in x]) >= 2  # da di het trang
    assert json.loads((box.sdir / "state.json").read_text())["id"] == "777"
    assert_prod_alive(box)


def test_destroy_pending_not_found_never_stops_timers_even_past_deadline(box):
    lab = "secai-stack-eval-20261123-ab12"
    write_state(box, id_="", label=lab, pending=True, deadline=int(time.time()) + 3600)
    r = sh(DESTROY, "--force-deadline", b=box)
    assert r.returncode == 4 and destroys(box) == [] and log(box, "systemctl") == []
    write_state(box, id_="", label=lab, pending=True, deadline=int(time.time()) - 5)
    r = sh(DESTROY, "--force-deadline", b=box)
    assert r.returncode == 6 and destroys(box) == [] and log(box, "systemctl") == []  # danh sach khong phai bang chung
    assert "console" in r.stderr.lower() and lab in r.stderr and "--abandon-pending" in r.stderr
    assert lab in (box.sdir / "ALERT.txt").read_text()


def _pending(box, **kw):
    write_state(box, id_="", label="secai-stack-eval-20261123-ab12", pending=True, deadline=int(time.time()) - 5, **kw)


def test_abandon_needs_explicit_console_confirmation_and_prints_instruction_first(box):
    _pending(box)
    r = sh(DESTROY, "--abandon-pending", b=box)
    assert r.returncode == 2 and log(box, "systemctl") == []
    assert "console" in r.stderr.lower() and "secai-stack-eval-20261123-ab12" in r.stderr and "--confirm-console-checked" in r.stderr
    assert "trễ" in r.stderr  # danh sach co the tre ngay sau create
    assert "abandoned_at" not in json.loads((box.sdir / "state.json").read_text())


def test_abandon_refused_with_other_account_even_if_list_is_empty(box):
    _pending(box)
    set_mode(box, "account_id", "9999")  # khoa khac tai khoan: danh sach rong hop le nhung vo nghia
    r = sh(DESTROY, "--abandon-pending", "--confirm-console-checked", b=box)
    assert r.returncode == 5 and log(box, "systemctl") == []
    assert "abandoned_at" not in json.loads((box.sdir / "state.json").read_text())
    write_state(box, id_="", label="secai-stack-eval-20261123-ab12", pending=True, deadline=1, account_id="")
    assert sh(DESTROY, "--abandon-pending", "--confirm-console-checked", b=box).returncode == 3  # khong co danh tinh


def test_abandon_refused_while_up_holds_lock(box):  # (A) up con dang cho create
    _pending(box)
    holder = subprocess.Popen(["flock", "-n", str(box.sdir / ".lock"), "sleep", "20"])
    try:
        time.sleep(0.5)
        r = sh(DESTROY, "--abandon-pending", "--confirm-console-checked", b=box)
        assert r.returncode == 3 and log(box, "systemctl") == []
        assert "abandoned_at" not in json.loads((box.sdir / "state.json").read_text())
    finally:
        holder.kill()


def test_abandon_refused_when_instance_id_already_recorded(box):  # (B) ID da ghi nhung state chua cap nhat / danh sach tre
    _pending(box)
    (box.sdir / "instance.id").write_text("777\n")
    r = sh(DESTROY, "--abandon-pending", "--confirm-console-checked", b=box)
    assert r.returncode == 3 and log(box, "systemctl") == []
    assert "abandoned_at" not in json.loads((box.sdir / "state.json").read_text())


def test_abandon_refused_when_label_found_or_list_unreadable(box):
    _pending(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    assert sh(DESTROY, "--abandon-pending", "--confirm-console-checked", b=box).returncode == 3
    box.fake.joinpath("instances.json").write_text(json.dumps([PRODROW]))
    set_mode(box, "list_mode", "garbage")
    assert sh(DESTROY, "--abandon-pending", "--confirm-console-checked", b=box).returncode == 3
    assert log(box, "systemctl") == [] and destroys(box) == []


def test_up_does_not_accept_abandoned_state_that_holds_an_id(box):
    write_state(box, abandoned_at=1)  # co ID 777
    assert sh(EVAL, "up", b=box).returncode != 0 and not lines_with(box, "vastai", "create")
    _pending(box, abandoned_at=1)
    (box.sdir / "instance.id").write_text("777\n")
    assert sh(EVAL, "up", b=box).returncode != 0 and not lines_with(box, "vastai", "create")


def test_destroy_pending_uses_instance_id_when_listing_lags(box):
    lab = "secai-stack-eval-20261123-ab12"
    write_state(box, id_="", label=lab, pending=True, deadline=int(time.time()) + 3600)
    add_instance(box, 777, lab, 0.25)
    (box.sdir / "instance.id").write_text("777\n")
    set_mode(box, "list_mode", "empty")  # danh sach chua thay
    r = sh(DESTROY, "--force-deadline", b=box)
    assert r.returncode == 0, r.stderr
    assert destroys(box) == [D777]


@pytest.mark.parametrize("via", ["prod", "protected"])
def test_destroy_pending_validates_label_found_id_before_saving_it(box, via):
    lab = "secai-stack-eval-20261123-ab12"
    write_state(box, id_="", label=lab, pending=True, deadline=1)
    extra = {}
    if via == "prod":
        f = box.fake / "instances.json"
        x = json.loads(f.read_text()); x[0]["label"] = lab; x[0]["dph_total"] = 0.2; f.write_text(json.dumps(x))
    else:
        add_instance(box, 777, lab, 0.25)
        (box.tmp / "prot").write_text("777\n")
        extra["STACK_EVAL_PROTECTED"] = str(box.tmp / "prot")
    r = sh(DESTROY, "--force-deadline", b=box, extra=extra)
    assert r.returncode == 3 and destroys(box) == []
    st = json.loads((box.sdir / "state.json").read_text())
    assert st["id"] == "" and st["pending"] is True  # khong luu ID chua hop le vao state


def test_destroy_fallback_without_state_honors_expected_label(box):
    box.sdir.mkdir(parents=True)
    (box.sdir / "instance.id").write_text("777\n")
    add_instance(box, 777, "secai-stack-eval-x", 0.25)
    r = sh(DESTROY, "--force-deadline", b=box, extra={"EVAL_EXPECT_LABEL": "secai-stack-eval-y", "STACK_EVAL_DESTROY_RETRIES": "1"})
    assert r.returncode == 3 and destroys(box) == []
    r = sh(DESTROY, "--force-deadline", b=box, extra={"EVAL_EXPECT_LABEL": "secai-stack-eval-x", "STACK_EVAL_DESTROY_RETRIES": "1"})
    assert destroys(box) == [D777]


def test_destroy_abandon_pending_requires_flag_stops_timers_and_unblocks_next_up(box):
    lab = "secai-stack-eval-20261123-ab12"
    write_state(box, id_="", label=lab, pending=True, deadline=int(time.time()) - 5)
    r = sh(DESTROY, "--abandon-pending", "--confirm-console-checked", b=box)
    assert r.returncode == 0 and destroys(box) == []
    assert any("tB.timer" in x for x in log(box, "systemctl"))
    assert "abandoned_at" in json.loads((box.sdir / "state.json").read_text())
    assert sh(EVAL, "up", b=box).returncode == 0  # state da bo => cho phep up moi
    # khong dung cho state da co ID
    write_state(box)
    assert sh(DESTROY, "--abandon-pending", "--confirm-console-checked", b=box).returncode == 3


@pytest.mark.parametrize("what", ["other_account", "user_fails", "no_account_in_state"])
def test_destroy_identity_mismatch_or_unknown_is_never_a_gone_proof(box, what):
    # instance KHONG nam trong danh sach cua tai khoan hien tai (tai khoan khac thi show instance tra null = "gone" gia)
    if what == "no_account_in_state":
        write_state(box, account_id="")
    else:
        write_state(box)
    if what == "other_account":
        set_mode(box, "account_id", "9999")
    if what == "user_fails":
        set_mode(box, "user_mode", "garbage")
    r = sh(DESTROY, "--force-deadline", b=box)
    assert r.returncode == 5 and destroys(box) == []
    assert log(box, "systemctl") == []  # timer van chay
    assert "destroyed_at" not in json.loads((box.sdir / "state.json").read_text())
    assert (box.sdir / "ALERT.txt").exists()


def test_destroy_identity_mismatch_also_blocks_destroy_of_existing_instance(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    set_mode(box, "account_id", "9999")
    assert sh(DESTROY, "--yes", b=box).returncode == 5 and destroys(box) == []


def test_destroy_expected_label_from_timer_must_match_state(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    r = sh(DESTROY, "--force-deadline", b=box, extra={"EVAL_EXPECT_LABEL": "secai-stack-eval-20260101-zzzz"})
    assert r.returncode == 3 and destroys(box) == []
    r = sh(DESTROY, "--force-deadline", b=box, extra={"EVAL_EXPECT_LABEL": "secai-stack-eval-20261123-ab12"})
    assert r.returncode == 0 and destroys(box) == [D777]


def test_destroy_second_instance_waits_on_lock_then_gives_up(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    holder = subprocess.Popen(["flock", "-n", str(box.sdir / ".destroy.lock"), "sleep", "20"])
    try:
        time.sleep(0.5)
        r = sh(DESTROY, "--yes", b=box, extra={"STACK_EVAL_LOCK_WAIT": "1"})
        assert r.returncode == 7 and destroys(box) == []
    finally:
        holder.kill()


def test_destroy_refusal_writes_alert_file(box):
    write_state(box, id_=PROD)
    assert sh(DESTROY, "--yes", b=box).returncode == 3
    assert "REFUSED" in (box.sdir / "ALERT.txt").read_text()


def test_destroy_pending_with_unreadable_list_refuses(box):
    write_state(box, id_="", label="secai-stack-eval-20261123-ab12", pending=True, deadline=1)
    set_mode(box, "list_mode", "garbage")
    r = sh(DESTROY, "--force-deadline", b=box)
    assert r.returncode == 3 and destroys(box) == [] and log(box, "systemctl") == []


def test_destroy_pending_label_resolving_to_prod_is_refused(box):
    lab = "secai-stack-eval-20261123-ab12"
    write_state(box, id_="", label=lab, pending=True, deadline=1)
    f = box.fake / "instances.json"
    x = json.loads(f.read_text()); x[0]["label"] = lab; x[0]["dph_total"] = 0.2; f.write_text(json.dumps(x))
    r = sh(DESTROY, "--force-deadline", b=box)
    assert r.returncode == 3 and destroys(box) == []
    assert_prod_alive(box)


def test_destroy_empty_id_without_pending_flag_still_refused_even_if_label_matches(box):
    write_state(box, id_="", label="secai-stack-eval-20261123-ab12")
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    assert sh(DESTROY, "--yes", b=box).returncode == 3 and destroys(box) == []


def test_destroy_without_state_uses_instance_id_but_still_checks_label_and_price(box):
    box.sdir.mkdir(parents=True)
    (box.sdir / "instance.id").write_text("777\n")
    add_instance(box, 777, "random-name", 0.25)
    assert sh(DESTROY, "--yes", b=box).returncode == 3 and destroys(box) == []
    box.fake.joinpath("instances.json").write_text(json.dumps([PRODROW, {"id": 777, "label": "secai-stack-eval-x", "dph_total": 0.9}]))
    assert sh(DESTROY, "--yes", b=box).returncode == 3 and destroys(box) == []
    box.fake.joinpath("instances.json").write_text(json.dumps([PRODROW, {"id": 777, "label": "secai-stack-eval-x", "dph_total": 0.25}]))
    r = sh(DESTROY, "--yes", b=box, extra={"STACK_EVAL_DESTROY_RETRIES": "1"})
    assert destroys(box) == [D777] and r.returncode == 5  # huy duoc, nhung khong co account_id nen khong tuyen bo "da bien mat"
    (box.sdir / "instance.id").write_text(PROD + "\n")
    assert sh(DESTROY, "--yes", b=box).returncode == 3
    assert_prod_alive(box)



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


def test_up_dry_run_skips_excluded_offers(box):
    # offer da thue ma khong len duoc ssh phai bi loai de lan sau khong chon lai chinh no
    (box.fake / "offers.json").write_text(json.dumps([
        {"id": 2, "dph_total": 0.20, "cpu_ram": 65536, "disk_space": 200, "inet_down": 900, "reliability2": 0.99},
        {"id": 5, "dph_total": 0.28, "cpu_ram": 65536, "disk_space": 200, "inet_down": 900, "reliability2": 0.99}]))
    r = sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_EXCLUDE_OFFERS": "2"})
    assert r.returncode == 0, r.stderr
    assert "create instance 5 " in r.stdout and "create instance 2 " not in r.stdout
    r = sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_EXCLUDE_OFFERS": "2 5"})
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")


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
    assert all("--on-unit-active=600" in x for x in runs)  # lap lai moi 10 phut cho toi khi chung minh da huy
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


def test_up_provision_builds_tunnel_image_and_binds_gateway_to_loopback(box):
    r = sh(EVAL, "up", b=box)
    assert r.returncode == 0, r.stderr
    script = (box.fake / "ssh.stdin").read_text()
    # profile translation can image sidecar (ke ca TUNNEL_MODE=off); init chay bang root dat TUNNEL_UID=0 -> preflight tu choi
    assert "stack/images/tunnel" in script and "TUNNEL_IMAGE=secai-specialist-tunnel:eval-" in script
    assert "TUNNEL_UID=$TU" in script and "--build-arg TUNNEL_UID=" in script
    assert "GATEWAY_BIND=127.0.0.1" in script  # may thue co IP cong khai: gateway khong xac thuc, khong mo ra ngoai


def test_up_layer_a_only_with_instance_key_and_via_stdin(box):
    r = sh(EVAL, "up", b=box, extra={"EVAL_CONTAINER_API_KEY": "INSTKEY_MARK_5"})
    assert r.returncode == 0, r.stderr
    assert "INSTKEY_MARK_5" not in "\n".join(log(box, "ssh") + log(box, "systemd-run") + log(box, "scp") + log(box, "rsync"))
    assert "INSTKEY_MARK_5" in (box.fake / "ssh.stdin").read_text()
    assert "INSTKEY_MARK_5" not in r.stdout + r.stderr
    layer_a = [x for x in log(box, "ssh") if ".vast-instance" in x][0]
    assert "-H @" in layer_a and "INSTKEY_MARK_5" not in layer_a  # khoa khong len argv cua curl tren instance


def test_up_refuses_when_env_key_differs_from_key_file_or_file_missing(box):
    r = sh(EVAL, "up", b=box, extra={"VAST_API_KEY": "OTHERACCT_MARK_2"})
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")
    assert "OTHERACCT_MARK_2" not in r.stdout + r.stderr and VASTKEY not in r.stdout + r.stderr
    (box.tmp / "home/.config/vastai/vast_api_key").unlink()
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")
    assert sh(EVAL, "up", b=box, extra={"VAST_API_KEY": VASTKEY}).returncode != 0  # van thieu tep khoa cho timer


@pytest.mark.parametrize("fname", ["../../.vast_api_key"])
def test_up_refuses_env_file_with_legacy_key_and_container_key_equal_to_it(box, fname):
    kf = box.tmp / "home/.config/vastai" / fname
    kf.write_text("OTHERKEY_MARK_88")
    Path(box.env["EVAL_ENV_FILE"]).write_text("X=OTHERKEY_MARK_88\n")
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0 and not lines_with(box, "vastai", "create") and "OTHERKEY_MARK_88" not in r.stdout + r.stderr
    Path(box.env["EVAL_ENV_FILE"]).write_text(f"QWEN_API_KEY={SECRET}\n")
    r = sh(EVAL, "up", b=box, extra={"EVAL_CONTAINER_API_KEY": "OTHERKEY_MARK_88"})
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")


def test_up_refuses_tfa_key_differing_from_key_file_or_env(box):
    tfa = box.tmp / "home/.config/vastai/vast_tfa_key"
    tfa.write_text("TFAOTHER_MARK_3")  # CLI co the uu tien tfa => up va timer co the thanh hai tai khoan khac nhau
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0 and not lines_with(box, "vastai", "create") and "TFAOTHER_MARK_3" not in r.stdout + r.stderr
    r = sh(EVAL, "up", b=box, extra={"VAST_API_KEY": VASTKEY})  # env khop tep khoa nhung tfa khac
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")
    tfa.write_text(VASTKEY)  # tfa trung tep khoa => moi cach phan giai ra cung mot khoa
    assert sh(EVAL, "up", b=box, extra={"VAST_API_KEY": VASTKEY}).returncode == 0


def test_up_requires_linger_and_sets_runtime_max_on_timers(box):
    set_mode(box, "linger", "Linger=no")
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0 and not lines_with(box, "vastai", "create") and "linger" in r.stderr.lower()
    (box.fake / "linger").unlink()
    assert sh(EVAL, "up", b=box).returncode == 0
    assert all("-p RuntimeMaxSec=300" in x for x in log(box, "systemd-run"))


def test_up_accepts_env_key_equal_to_key_file(box):
    assert sh(EVAL, "up", b=box, extra={"VAST_API_KEY": VASTKEY}).returncode == 0
    assert VASTKEY not in "\n".join(log(box, "systemd-run") + log(box, "ssh") + log(box, "rsync") + log(box, "scp"))


def test_up_refuses_env_file_containing_account_key_without_printing_it(box):
    Path(box.env["EVAL_ENV_FILE"]).write_text(f"QWEN_API_KEY={SECRET}\nVAST={VASTKEY}\n")
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0 and not lines_with(box, "vastai", "create") and log(box, "ssh") == []
    assert VASTKEY not in r.stdout + r.stderr + "\n".join(log(box, "vastai"))


def test_up_records_account_identity_not_key_and_binds_timers_to_label(box):
    r = sh(EVAL, "up", b=box)
    assert r.returncode == 0, r.stderr
    st = json.loads((box.sdir / "state.json").read_text())
    assert st["account_id"] == "4242" and VASTKEY not in json.dumps(st)
    assert all(f"--setenv=EVAL_EXPECT_LABEL={st['label']}" in x for x in log(box, "systemd-run"))


def test_up_aborts_when_account_identity_unreadable(box):
    set_mode(box, "user_mode", "garbage")
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")


def test_up_failed_timer_setup_does_not_block_next_up(box):
    set_mode(box, "systemd-run_rc", "1")
    assert sh(EVAL, "up", b=box).returncode != 0
    assert not (box.sdir / "state.json").exists()  # khong thue gi => khong de state pending chan 3 gio
    (box.fake / "systemd-run_rc").unlink()
    assert sh(EVAL, "up", b=box).returncode == 0


@pytest.mark.parametrize("price,destroyed", [("0.40", True), ("0.90", False)])
def test_up_aborts_if_instance_price_exceeds_cap_after_create(box, price, destroyed):
    set_mode(box, "create_price", price)
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0 and "giá" in r.stderr
    assert destroys(box) == ([D777] if destroyed else [])  # > 1.5 x cap: destroy-eval tu choi, bao huy tay
    if not destroyed:
        assert "console" in r.stderr.lower()
    assert_prod_alive(box)


def test_status_prints_alert_file(box):
    now = int(time.time())
    write_state(box, created_at=now, deadline=now + 3600)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    (box.sdir / "ALERT.txt").write_text("2026-11-23 destroy-eval: REFUSED: something\n")
    r = sh(EVAL, "status", b=box)
    assert "REFUSED: something" in r.stdout and "ALERT" in r.stdout


def test_up_refuses_container_key_equal_to_account_key(box):
    r = sh(EVAL, "up", b=box, extra={"EVAL_CONTAINER_API_KEY": VASTKEY})  # = noi dung ~/.config/vastai/vast_api_key
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")
    assert VASTKEY not in r.stdout + r.stderr
    r = sh(EVAL, "up", b=box, extra={"EVAL_CONTAINER_API_KEY": "ENVKEY_MARK_1", "VAST_API_KEY": "ENVKEY_MARK_1"})
    assert r.returncode != 0 and not lines_with(box, "vastai", "create")


def test_up_query_uses_gb_vms_and_storage(box):
    assert sh(EVAL, "up", "--dry-run", b=box).returncode == 0
    q = lines_with(box, "vastai", "search")[0]
    assert "cpu_ram>=32 " in q and "vms_enabled=true" in q and "dph_total<=0.30" in q and "disk_space>=100" in q
    assert "--storage 100" in q
    assert sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_EXPLOIT": "1"}).returncode == 0
    assert "cpu_ram>=48 " in lines_with(box, "vastai", "search")[1]


def test_up_trap_and_pending_state_set_before_create_with_label_fallback(box):
    set_mode(box, "create_mode", "ambiguous")  # instance duoc tao nhung vastai bao loi
    set_mode(box, "list_mode", "garbage")      # va khong tra cuu duoc bang label
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0 and destroys(box) == []
    st = json.loads((box.sdir / "state.json").read_text())
    assert st["pending"] is True and st["id"] == "" and st["label"].startswith("secai-stack-eval-") and len(st["timers"]) == 2
    assert len(log(box, "systemd-run")) == 2  # timers da dat TRUOC create
    set_mode(box, "list_mode", "ok")  # den deadline: timer tra theo label va huy
    r = sh(DESTROY, "--force-deadline", b=box)
    assert r.returncode == 0, r.stderr
    assert destroys(box) == [D777]
    assert_prod_alive(box)


def test_up_create_returning_prod_id_is_refused_without_destroy(box):
    set_mode(box, "create_mode", "prod")
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0
    assert destroys(box) == [] and not (box.sdir / "instance.id").exists()
    assert_prod_alive(box)


def test_up_second_concurrent_up_is_refused_by_lock(box):
    box.sdir.mkdir(parents=True)
    holder = subprocess.Popen(["flock", "-n", str(box.sdir / ".lock"), "sleep", "20"])
    try:
        time.sleep(0.5)
        r = sh(EVAL, "up", b=box)
        assert r.returncode != 0 and not lines_with(box, "vastai", "create")
    finally:
        holder.kill()


def test_up_records_id_immediately_even_if_later_step_fails_then_destroys_via_exit_trap(box):
    set_mode(box, "ssh_rc", "255")  # moi ssh that bai sau khi da create
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0
    assert (box.sdir / "instance.id").read_text().strip() == "777"
    assert json.loads((box.sdir / "state.json").read_text())["id"] == "777"
    assert destroys(box) == [D777]  # EXIT trap huy qua destroy-eval.sh
    assert_prod_alive(box)


def test_up_timer_failure_aborts_before_any_rental(box):
    set_mode(box, "systemd-run_rc", "1")
    r = sh(EVAL, "up", b=box)
    assert r.returncode != 0
    assert not lines_with(box, "vastai", "create")  # khong co deadline thi khong thue
    assert not (box.sdir / "instance.id").exists() and destroys(box) == []


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
    set_mode(box, "show_mode", "deprecated")
    r = sh(EVAL, "status", b=box)
    assert r.returncode != 0


def test_down_delegates_to_destroy_eval(box):
    write_state(box)
    add_instance(box, 777, "secai-stack-eval-20261123-ab12", 0.25)
    r = sh(EVAL, "down", b=box)
    assert r.returncode == 0, r.stderr
    assert destroys(box) == [D777]
    assert_prod_alive(box)


def test_run_executes_remote_sequence_via_ssh_stdin(box):
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222)
    r = sh(EVAL, "run", b=box)
    assert r.returncode == 0, r.stderr
    script = (box.fake / "ssh.stdin").read_text()
    for needle in ("bootstrap.sh init", "bootstrap.sh preflight", "check.sh all", "acceptance/run-api.sh"):
        assert needle in script
    assert SECRET not in "\n".join(log(box, "ssh"))


def test_run_passes_eval_skip_defaults_and_writes_summary(box):
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222)
    r = sh(EVAL, "run", b=box)
    assert r.returncode == 0, r.stderr
    argv = "\n".join(log(box, "ssh"))
    # mac dinh eval tren may thue: khong co lab/exploit, khong co Hy-MT2/OCR
    assert "SKIP_CASES='UI-POC-1 UI-POC-2 UI-POC-3 UI-TR-2 UI-TR-3'" in argv and "STACK_HAS_OCR=0" in argv
    script = (box.fake / "ssh.stdin").read_text()
    assert "SKIP_REASONS" in script and "TUNNEL_MODE" in script and "summary.json" in script


def test_run_custom_skip_cases_and_ocr(box):
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222)
    r = sh(EVAL, "run", b=box, extra={"EVAL_SKIP_CASES": "UI-TR-3", "STACK_HAS_OCR": "1"})
    assert r.returncode == 0, r.stderr
    argv = "\n".join(log(box, "ssh"))
    assert "SKIP_CASES='UI-TR-3'" in argv and "STACK_HAS_OCR=1" in argv


@pytest.mark.parametrize("extra", [{"EVAL_SKIP_CASES": "UI-TR-3;reboot"}, {"EVAL_SKIP_CASES": "x'y"}, {"STACK_HAS_OCR": "yes"}])
def test_run_rejects_unsafe_skip_values_before_ssh(box, extra):
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222)
    r = sh(EVAL, "run", b=box, extra=extra)
    assert r.returncode != 0 and not log(box, "ssh")


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


def test_collect_rejects_symlinks_and_uses_no_links(box):
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222)
    (box.tmp / "bin" / "rsync").write_text(FAKE_RSYNC_PULL + "")
    (box.tmp / "bin" / "rsync").write_text(FAKE_RSYNC_PULL.replace("exit 0", 'ln -s /etc/passwd "$dest/evil"\nexit 0'))
    r = sh(EVAL, "collect", b=box)
    assert any("--no-links" in x for x in log(box, "rsync"))
    assert r.returncode != 0 and "symlink" in r.stderr.lower()


def test_rsync_e_quotes_ssh_key_path_with_spaces(box):
    key = box.tmp / "my key"
    key.write_text("k")
    r = sh(EVAL, "up", b=box, extra={"EVAL_SSH_KEY": str(key)})
    assert r.returncode == 0, r.stderr
    assert any(f'-i "{key}"' in x for x in log(box, "rsync"))


# ------------------------------------------------------------------ EVAL_MODE=ghcr (may trang nhu nguoi van hanh) + ghcrtest

GHCR_VM_TEST = STACK / "acceptance" / "ghcr-vm-test.sh"
G_REF = "0123456789abcdef0123456789abcdef01234567"
G_OK = {"GHCR_TEST_REF": G_REF, "GHCR_TEST_V1": "2026.11.27-0f980c19", "GHCR_TEST_V2": "2026.11.28-1a2b3c4d",
        "GHCR_TEST_D1": "2026.11.27", "GHCR_TEST_D2": "2026.11.27.2"}


def no_env_no_pkg(b) -> dict[str, str]:
    return {k: v for k, v in b.env.items() if k not in ("EVAL_ENV_FILE", "EVAL_DATA_PKG")}


def test_up_ghcr_mode_provisions_docker_only_no_source_no_env(box):
    r = subprocess.run(["bash", str(EVAL), "up"], env={**no_env_no_pkg(box), "EVAL_MODE": "ghcr"},
                       text=True, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr + r.stdout
    st = json.loads((box.sdir / "state.json").read_text())
    assert st["mode"] == "ghcr" and st["disk_gb"] == 150 and st["id"] == "777"
    q = lines_with(box, "vastai", "search")[0]
    assert "cpu_ram>=48 " in q and "disk_space>=150" in q and "--storage 150" in q  # bo phat hanh co profile exploit
    assert any("--disk 150 " in x for x in lines_with(box, "vastai", "create"))
    assert log(box, "scp") == [] and log(box, "rsync") == []  # khong git archive, khong goi du lieu
    assert ".env" not in "\n".join(log(box, "ssh"))
    script = (box.fake / "ssh.stdin").read_text()
    for needle in ("get.docker.com", "zstd", "curl", "python3", "iproute2", "mkdir -p /opt/secai-stack/results"):
        assert needle in script
    # khong build, khong dang nhap registry, khong dat sysctl: secai install phai tu lam (day la dieu can thu)
    for bad in ("docker build", "max_map_count", "docker login", "secai-src.tgz", "QWEN"):
        assert bad not in script
    assert len(log(box, "systemd-run")) == 2 and destroys(box) == []
    assert "ghcrtest" in r.stdout and "cảnh báo" not in r.stdout.lower()
    assert_prod_alive(box)


def test_up_ghcr_respects_explicit_ram_and_disk(box):
    r = sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_MODE": "ghcr", "EVAL_MIN_RAM_GB": "60", "EVAL_DISK_GB": "200"})
    assert r.returncode == 0, r.stderr
    q = lines_with(box, "vastai", "search")[0]
    assert "cpu_ram>=60 " in q and "disk_space>=200" in q and "--storage 200" in q and "--disk 200 " in r.stdout
    # mode khac van giu mac dinh cu
    assert sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_MODE": "packtest"}).returncode == 0
    q = lines_with(box, "vastai", "search")[1]
    assert "cpu_ram>=32 " in q and "--storage 100" in q


def test_up_ghcr_provision_failure_destroys_instance(box):
    env = {**no_env_no_pkg(box), "EVAL_MODE": "ghcr"}
    (box.tmp / "bin" / "ssh").write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_DIR/ssh.log\"\n[ \"$*\" != \"${*%bash -s}\" ] && exit 1\nexit 0\n")
    r = subprocess.run(["bash", str(EVAL), "up"], env=env, text=True, capture_output=True, timeout=60)
    assert r.returncode != 0 and "provision ghcr" in r.stderr
    assert destroys(box) == [D777]
    assert_prod_alive(box)


def test_provision_ghcr_fragment_installs_only_missing_tools(tmp_path):
    root = tmp_path / "opt"
    root.mkdir()
    binp = tmp_path / "bin"
    for c in ("docker", "curl", "python3"):
        stub(binp / c, f'echo "$*" >> "{tmp_path}/{c}.log"')
    stub(binp / "apt-get", f'echo "$*" >> "{tmp_path}/apt.log"')
    (binp / "mkdir").symlink_to("/bin/mkdir")
    r = subprocess.run(["/bin/bash", str(remote_script("REMOTE_PROVISION_GHCR", root))], text=True, capture_output=True,
                       timeout=60, env={"PATH": str(binp)})   # khong zstd, khong ip; khong sysctl (goi la loi)
    assert r.returncode == 0, r.stderr
    apt = (tmp_path / "apt.log").read_text().splitlines()
    assert apt[0] == "update -qq" and apt[1].split()[-2:] == ["zstd", "iproute2"]
    assert (root / "results").is_dir()
    assert not (tmp_path / "curl.log").exists()  # da co docker: khong chay get.docker.com


def ghcr_state(b, **kw) -> None:
    now = int(time.time())
    write_state(b, ssh_host="1.2.3.4", ssh_port=2222, **{"mode": "ghcr", "created_at": now, "deadline": now + 3 * 3600, **kw})


def test_ghcrtest_runs_vm_script_with_validated_env_on_ssh(box):
    ghcr_state(box)
    r = sh(EVAL, "ghcrtest", b=box, extra=G_OK)
    assert r.returncode == 0, r.stderr
    calls = log(box, "ssh")
    assert len(calls) == 1
    want = (f"REF={G_REF} V1=2026.11.27-0f980c19 V2=2026.11.28-1a2b3c4d D1=2026.11.27 D2=2026.11.27.2 "
            "REPO=elonma-china/secai-prod FAILTEST=1 DATA_DIR= bash -s")
    assert calls[0].endswith("root@1.2.3.4 " + want) and "-p 2222" in calls[0]
    assert (box.fake / "ssh.stdin").read_text() == GHCR_VM_TEST.read_text()
    assert "collect" in r.stdout


def test_ghcrtest_custom_repo_and_failtest_and_propagates_rc(box):
    ghcr_state(box)
    set_mode(box, "ssh_rc", "5")
    r = sh(EVAL, "ghcrtest", b=box, extra={**G_OK, "GHCR_TEST_REPO": "me/fork.x", "GHCR_TEST_FAILTEST": "0"})
    assert r.returncode == 5
    assert "REPO=me/fork.x FAILTEST=0 DATA_DIR= bash -s" in log(box, "ssh")[0]


@pytest.mark.parametrize("bad", [
    {"GHCR_TEST_REF": ""}, {"GHCR_TEST_REF": "0123abc"}, {"GHCR_TEST_REF": G_REF.upper()}, {"GHCR_TEST_REF": G_REF + "0"},
    {"GHCR_TEST_REF": G_REF[:-1] + ";"}, {"GHCR_TEST_REF": "main"},
    {"GHCR_TEST_V1": ""}, {"GHCR_TEST_V1": "2026.11.27"}, {"GHCR_TEST_V1": "2026.11.27-0F980C19"},
    {"GHCR_TEST_V1": "2026.11.27-0f980c19;reboot"}, {"GHCR_TEST_V2": "stable"}, {"GHCR_TEST_V2": "2026.11.27-0f980c19"},
    {"GHCR_TEST_D1": ""}, {"GHCR_TEST_D1": "2026-11-27"}, {"GHCR_TEST_D1": "2026.11.27.123"}, {"GHCR_TEST_D2": "latest"},
    {"GHCR_TEST_D2": "2026.11.27"}, {"GHCR_TEST_D2": "2026.11.27 x"},
    {"GHCR_TEST_REPO": "noslash"}, {"GHCR_TEST_REPO": "a/b/c"}, {"GHCR_TEST_REPO": "a/b;x"}, {"GHCR_TEST_REPO": "a/b c"},
    {"GHCR_TEST_FAILTEST": "2"}, {"GHCR_TEST_FAILTEST": "yes"},
])
def test_ghcrtest_rejects_bad_inputs_before_ssh(box, bad):
    ghcr_state(box)
    r = sh(EVAL, "ghcrtest", b=box, extra={**G_OK, **bad})
    assert r.returncode != 0 and log(box, "ssh") == []
    assert "GHCR_TEST_" in r.stderr


@pytest.mark.parametrize("mode", [None, "packtest", "full"])
def test_ghcrtest_requires_ghcr_state(box, mode):
    now = int(time.time())
    kw = {} if mode is None else {"mode": mode}
    write_state(box, ssh_host="1.2.3.4", ssh_port=2222, created_at=now, deadline=now + 3600, **kw)
    r = sh(EVAL, "ghcrtest", b=box, extra=G_OK)
    assert r.returncode != 0 and log(box, "ssh") == [] and "ghcr" in r.stderr


def test_ghcrtest_refuses_near_deadline(box):
    ghcr_state(box, deadline=int(time.time()) + 300)
    r = sh(EVAL, "ghcrtest", b=box, extra=G_OK)
    assert r.returncode != 0 and "deadline" in r.stderr and log(box, "ssh") == []


def test_ghcrtest_is_bounded_by_deadline(box):
    # ssh gia ngu lau: timeout (deadline - 10 phut) phai cat no
    ghcr_state(box, deadline=int(time.time()) + 602)
    (box.tmp / "bin" / "ssh").write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_DIR/ssh.log\"\nexec sleep 30\n")
    t0 = time.time()
    r = sh(EVAL, "ghcrtest", b=box, extra=G_OK)
    assert r.returncode != 0 and time.time() - t0 < 20 and len(log(box, "ssh")) == 1


def test_usage_lists_ghcrtest_and_mode_message(box):
    r = sh(EVAL, b=box)
    assert r.returncode == 2 and "ghcrtest" in r.stderr
    r = sh(EVAL, "up", "--dry-run", b=box, extra={"EVAL_MODE": "ghcr;x"})
    assert r.returncode != 0 and "full|packtest|ghcr" in r.stderr and not log(box, "vastai")


def test_scripts_pass_bash_syntax_check():
    for s in (EVAL, DESTROY):
        assert subprocess.run(["bash", "-n", str(s)], capture_output=True).returncode == 0


RUN_API = STACK / "acceptance" / "run-api.sh"


def test_run_api_records_skipped_cases_with_reason_and_summary(tmp_path):
    ids = [c["id"] for c in json.loads((STACK / "acceptance" / "cases.json").read_text())["cases"]]
    out = tmp_path / "acc"
    reasons = {"UI-TR-2": "không chạy: không có Hy-MT2/OCR"}
    # port 9 (discard): bo qua het nen khong co request nao; ca bi bo qua phai co ban ghi pass=null + ly do, khong phai PASS
    r = subprocess.run(["bash", str(RUN_API), "http://127.0.0.1:9"], text=True, capture_output=True, timeout=60,
                       env={**os.environ, "ACCEPT_OUT": str(out), "SKIP_CASES": " ".join(ids), "SKIP_REASONS": json.dumps(reasons)})
    assert r.returncode == 0, r.stderr
    tr2 = json.loads((out / "UI-TR-2.json").read_text())
    assert tr2["pass"] is None and tr2["reason"] == reasons["UI-TR-2"]
    other = json.loads((out / "UI-CHAT-1.json").read_text())
    assert other["pass"] is None and "SKIP_CASES" in other["reason"]
    s = json.loads((out / "summary.json").read_text())
    assert s["pass"] == [] and s["fail"] == [] and sorted(s["skipped"]) == sorted(ids)
    assert s["skipped"]["UI-TR-2"] == reasons["UI-TR-2"]
    assert all(line.startswith("SKIP") for line in r.stdout.splitlines() if line.strip())


# ------------------------------------------------------------------ chay THAT cac heredoc tu xa (thay /opt, /srv bang thu muc tam)

def remote_script(marker: str, root: Path) -> Path:
    lines = EVAL.read_text().splitlines()
    start = next(i for i, x in enumerate(lines) if f"<<'{marker}'" in x)
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == marker)
    body = "\n".join(lines[start + 1:end]).replace("/opt/secai-stack", str(root)).replace("/srv/secai-data", str(root / "srv"))
    p = root / f"{marker}.sh"
    p.write_text(body + "\n")
    return p


def stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


@pytest.mark.parametrize("line,want", [("", "10002"), ("TUNNEL_UID=\n", "10002"), ("TUNNEL_UID=0\n", "10002"),
                                       ("TUNNEL_UID=1234\n", "1234")])
def test_provision_fragment_runs_with_or_without_tunnel_uid(tmp_path, line, want):
    root = tmp_path / "opt"
    (root / "stack").mkdir(parents=True)
    (root / "stack/.env").write_text("COMPOSE_PROFILES=translation\n" + line)
    binp = tmp_path / "bin"
    for c in ("docker", "sysctl", "zstd"):
        stub(binp / c, f'echo "$*" >> "{tmp_path}/{c}.log"')
    r = subprocess.run(["bash", str(remote_script("REMOTE_PROVISION", root))], text=True, capture_output=True, timeout=60,
                       env={"PATH": f"{binp}:/usr/bin:/bin", "SHA": "abcd1234", "EXPLOIT": "0"})
    assert r.returncode == 0, r.stderr
    env = (root / "stack/.env").read_text().splitlines()
    assert [x for x in env if x.startswith("TUNNEL_UID=")][-1] == f"TUNNEL_UID={want}"
    assert env[-3:] == ["TUNNEL_UID=" + want, "GATEWAY_BIND=127.0.0.1", "COMPOSE_PROFILES=translation,report,review"]
    assert f"--build-arg TUNNEL_UID={want} -t secai-specialist-tunnel:eval-abcd1234" in (tmp_path / "docker.log").read_text()


TOKEN = "deadbeef" * 8


def run_remote(tmp_path, acc=None, fail_step="", skip="UI-TR-2 UI-POC-1", env_lines="TUNNEL_MODE=off\nCOMPOSE_PROFILES=translation,report,review\n"):
    """Chay REMOTE_RUN voi stub; acc = summary.json ma run-api.sh gia ghi (None: run-api.sh gia loi truoc khi ghi)."""
    root = tmp_path / "opt"
    st = root / "stack"
    st.mkdir(parents=True, exist_ok=True)
    (st / ".env").write_text("DATA_ROOT=/x\n" + env_lines)
    fails = f'[ "$1" = "{fail_step}" ] && exit 1' if fail_step else ":"
    stub(st / "bootstrap.sh", f'echo "REVIEW_ADMIN_TOKEN={TOKEN}"\n{fails}\nexit 0')
    stub(st / "restore-data.sh", "exit 0")
    stub(st / "check.sh", f'[ "{fail_step}" = check ] && exit 1\nexit 0')
    accw = ("" if acc is None else f"mkdir -p \"$ACCEPT_OUT\"; printf '%s' '{json.dumps(acc)}' > \"$ACCEPT_OUT/summary.json\"\n"
            "echo \"$SKIP_REASONS\" > \"$ACCEPT_OUT/reasons.json\"")
    stub(st / "acceptance/run-api.sh", accw + f'\n[ "{fail_step}" = acceptance ] && exit 1\nexit 0')
    stub(tmp_path / "bin/docker", 'case "$1" in ps) ;; *) echo cid ;; esac')   # `docker ps -q --filter health=...` rong = khong con container starting/unhealthy
    return subprocess.run(["bash", str(remote_script("REMOTE_RUN", root))], text=True, capture_output=True, timeout=60,
                          env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "SKIP_CASES": skip, "STACK_HAS_OCR": "0"}), root / "results"


OK_ACC = {"pass": ["UI-CHAT-1"], "fail": [], "skipped": {"UI-TR-2": "x"}}


@pytest.mark.parametrize("acc,fail_step,verdict", [
    (OK_ACC, "", "pass"),
    ({"pass": ["UI-CHAT-1"], "fail": ["UI-RPT-1"], "skipped": {}}, "acceptance", "fail"),
    (OK_ACC, "check", "fail"),                                   # ca dat nhung buoc check loi
    (OK_ACC, "preflight", "fail"),                               # abort: bo ca khong chay
    ({"pass": [], "fail": [], "skipped": {"UI-CHAT-1": "x"}}, "", "incomplete"),   # bo qua het khong phai dat
])
def test_remote_run_verdict(tmp_path, acc, fail_step, verdict):
    r, R = run_remote(tmp_path, acc, fail_step)
    s = json.loads((R / "summary.json").read_text())
    assert s["verdict"] == verdict, (s, r.stderr)
    assert (r.returncode == 0) == (verdict != "fail")
    if fail_step == "preflight":
        assert s["acceptance"] is None and s["steps"]["acceptance"] == "skipped"


def test_remote_run_rerun_moves_old_results_and_does_not_reuse_old_acceptance(tmp_path):
    run_remote(tmp_path, OK_ACC)
    r, R = run_remote(tmp_path, OK_ACC, fail_step="init")   # lan 2 abort som: khong duoc thay ca dat cua lan 1
    s = json.loads((R / "summary.json").read_text())
    assert s["acceptance"] is None and s["verdict"] == "fail"
    assert not (R / "acceptance").exists() and (R / "steps.txt").read_text().splitlines()[0] == "init rc=1"
    prev = list(R.parent.glob("results.prev-*"))
    assert len(prev) == 1 and json.loads((prev[0] / "summary.json").read_text())["verdict"] == "pass"


def test_remote_run_scrubs_tokens_before_sha256(tmp_path):
    r, R = run_remote(tmp_path, OK_ACC)
    for f in R.rglob("*"):
        if f.is_file():
            assert TOKEN not in f.read_text(), f
    assert "REVIEW_ADMIN_TOKEN=<redacted>" in (R / "init.log").read_text()
    assert subprocess.run(["sha256sum", "-c", "--strict", "--quiet", "SHA256SUMS"], cwd=R).returncode == 0


@pytest.mark.parametrize("env_lines,tr,poc", [
    ("TUNNEL_MODE=off\nCOMPOSE_PROFILES=translation,report,review\n", "không có Hy-MT2/OCR (TUNNEL_MODE=off)", "profile exploit tắt"),
    ("TUNNEL_MODE=ssh\nCOMPOSE_PROFILES=exploit,translation\n", "EVAL_SKIP_CASES (TUNNEL_MODE=ssh", "EVAL_SKIP_CASES"),
])
def test_remote_run_skip_reasons_follow_real_config(tmp_path, env_lines, tr, poc):
    r, R = run_remote(tmp_path, OK_ACC, env_lines=env_lines)
    reasons = json.loads((R / "acceptance/reasons.json").read_text())
    assert tr in reasons["UI-TR-2"] and poc in reasons["UI-POC-1"]
    if "ssh" in env_lines:
        assert "Hy-MT2" not in reasons["UI-TR-2"] and "tắt" not in reasons["UI-POC-1"]



def test_ghcrtest_rsyncs_data_pkg_and_passes_data_dir(box, tmp_path):
    ghcr_state(box)
    pkg = tmp_path / "datapkg"; pkg.mkdir()
    (pkg / "SHA256SUMS").write_text("x  y\n")
    r = sh(EVAL, "ghcrtest", b=box, extra={**G_OK, "GHCR_TEST_DATA_PKG": str(pkg)})
    assert r.returncode == 0, r.stderr
    rs = log(box, "rsync")
    assert len(rs) == 1 and f"{pkg}/ root@1.2.3.4:/opt/secai-stack/pkg/" in rs[0] and "--no-links" in rs[0]
    assert "DATA_DIR=/opt/secai-stack/pkg bash -s" in log(box, "ssh")[0]


def test_ghcrtest_rejects_bad_data_pkg_before_ssh(box, tmp_path):
    ghcr_state(box)
    r = sh(EVAL, "ghcrtest", b=box, extra={**G_OK, "GHCR_TEST_DATA_PKG": str(tmp_path / "missing")})
    assert r.returncode != 0 and log(box, "ssh") == [] and log(box, "rsync") == []
