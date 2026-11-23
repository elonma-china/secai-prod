"""supervisor: HOME slot nhóm = gid gateway + 2770, engine chạy umask 007 (gateway dọn được engagements, slot khác không đọc)."""
import importlib.util
import os
import pathlib
import stat

import pytest

_SRC = pathlib.Path(__file__).resolve().parent.parent / "stack/images/pentestcode-vm/supervisor.py"


def _load(monkeypatch, gid=None):
    if gid is None:
        monkeypatch.delenv("VM_SHARED_GID", raising=False)
    else:
        monkeypatch.setenv("VM_SHARED_GID", str(gid))
    spec = importlib.util.spec_from_file_location("vm_supervisor_perms", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ensure(monkeypatch, tmp_path, gid=None):
    sup = _load(monkeypatch, gid)
    sup.ROOT = str(tmp_path)
    calls = {"chown": [], "chmod": []}
    monkeypatch.setattr(sup.pwd, "getpwnam", lambda n: object())          # user đã có -> không useradd
    monkeypatch.setattr(sup.os, "chown", lambda p, u, g: calls["chown"].append((str(p), u, g)))
    monkeypatch.setattr(sup.os, "chmod", lambda p, m: calls["chmod"].append((str(p), m)))
    return sup, calls


def test_home_group_is_shared_gid_mode_2770(monkeypatch, tmp_path):
    sup, calls = _ensure(monkeypatch, tmp_path)
    uid, home = sup.ensure_user(3)
    assert (uid, home) == (20003, f"{tmp_path}/slot3")
    assert calls["chown"] == [(home, 20003, 10100)]
    assert calls["chmod"] == [(home, 0o2770)]


def test_shared_gid_from_env(monkeypatch, tmp_path):
    sup, calls = _ensure(monkeypatch, tmp_path, gid=12345)
    _, home = sup.ensure_user(1)
    assert calls["chown"][0] == (home, 20001, 12345)


def test_engine_started_with_umask_007_and_clear_groups(monkeypatch, tmp_path):
    sup, _ = _ensure(monkeypatch, tmp_path)
    seen = {}

    class P:
        pid, returncode = 1, 0
        def poll(self): return 0
        def wait(self): return 0

    def popen(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return P()

    monkeypatch.setattr(sup.subprocess, "Popen", popen)
    monkeypatch.setattr(sup.time, "sleep", lambda s: sup.stop.set())
    sup.run_slot(1)
    assert seen["kw"]["umask"] == 0o007
    assert "--clear-groups" in seen["cmd"] and "--regid=20001" in seen["cmd"]


# ---- sweep() trên filesystem thật (không cần root: gid = nhóm của chính user test) ----
UID, GID = os.getuid(), os.getgid()


def _engine_tree(tmp_path):
    """Giống engine: dir 0700, file 0600."""
    home = tmp_path / "slot1"
    eng = home / ".ias/engagements/e1"
    eng.mkdir(parents=True, mode=0o700)
    for d in (home / ".ias", home / ".ias/engagements", eng):
        d.chmod(0o700)
    f = eng / "state.json"
    f.write_text("{}")
    f.chmod(0o600)
    return home, eng, f


def _mode(p):
    return stat.S_IMODE(os.lstat(p).st_mode)


def test_sweep_makes_dirs_2770_and_files_group_rw(monkeypatch, tmp_path):
    sup = _load(monkeypatch)
    home, eng, f = _engine_tree(tmp_path)
    sup.sweep(str(home), UID, GID)
    for d in (home / ".ias", home / ".ias/engagements", eng):
        assert _mode(d) == 0o2770 and os.lstat(d).st_gid == GID
    assert _mode(f) == 0o660 and os.lstat(f).st_gid == GID


def test_sweep_symlinked_ias_not_followed(monkeypatch, tmp_path):
    sup = _load(monkeypatch)
    victim = tmp_path / "victim"
    (victim / "sub").mkdir(parents=True, mode=0o700)
    secret = victim / "sub/secret"
    secret.write_text("x")
    secret.chmod(0o600)
    victim.chmod(0o700)
    home = tmp_path / "slot1"
    home.mkdir()
    (home / ".ias").symlink_to(victim)
    sup.sweep(str(home), UID, GID)  # không raise
    assert _mode(victim) == 0o700 and _mode(victim / "sub") == 0o700 and _mode(secret) == 0o600


def test_sweep_skips_symlink_child_and_keeps_going(monkeypatch, tmp_path):
    sup = _load(monkeypatch)
    home, eng, f = _engine_tree(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("x")
    victim.chmod(0o600)
    (eng / "link").symlink_to(victim)
    vdir = tmp_path / "vdir"
    vdir.mkdir(mode=0o700)
    (eng / "dlink").symlink_to(vdir)
    sup.sweep(str(home), UID, GID)
    assert _mode(victim) == 0o600 and _mode(vdir) == 0o700
    assert _mode(f) == 0o660  # entry khác vẫn được xử lý


def test_sweep_tolerates_vanishing_entry(monkeypatch, tmp_path):
    sup = _load(monkeypatch)
    home, eng, f = _engine_tree(tmp_path)
    real, hit = os.listdir, []

    def flaky(fd):
        names = real(fd)
        if not hit and "state.json" in names:
            hit.append(1)
            os.unlink(eng / "state.json")  # biến mất giữa listdir và stat
        return names

    monkeypatch.setattr(sup.os, "listdir", flaky)
    sup.sweep(str(home), UID, GID)
    assert hit and _mode(eng) == 0o2770

    def boom(fd):
        raise FileNotFoundError
    monkeypatch.setattr(sup.os, "listdir", boom)
    sup.sweep(str(home), UID, GID)  # listdir lỗi cũng không raise


def test_sweep_continues_after_oserror_on_one_entry(monkeypatch, tmp_path):
    sup = _load(monkeypatch)
    home, eng, _ = _engine_tree(tmp_path)
    for n in ("a", "b", "c"):
        (eng / n).write_text("x")
        (eng / n).chmod(0o600)
    real = os.fchmod
    state = {"n": 0}

    def flaky(fd, mode):
        state["n"] += 1
        if state["n"] == 4:
            raise PermissionError(1, "denied")
        return real(fd, mode)

    monkeypatch.setattr(sup.os, "fchmod", flaky)
    sup.sweep(str(home), UID, GID)
    done = [n for n in ("a", "b", "c", "state.json") if _mode(eng / n) == 0o660]
    assert len(done) == 3  # đúng 1 entry lỗi, các entry còn lại vẫn xong


def test_sweep_ignores_foreign_owner_and_missing_ias(monkeypatch, tmp_path):
    sup = _load(monkeypatch)
    home, eng, f = _engine_tree(tmp_path)
    sup.sweep(str(home), UID + 1, GID)  # .ias không thuộc slot uid này -> bỏ qua
    assert _mode(f) == 0o600
    sup.sweep(str(tmp_path / "nonexistent"), UID, GID)  # không có .ias


def test_sweep_entry_budget(monkeypatch, tmp_path):
    sup = _load(monkeypatch)
    monkeypatch.setattr(sup, "SWEEP_MAX_ENTRIES", 2)
    home, eng, _ = _engine_tree(tmp_path)
    for n in range(5):
        (eng / f"f{n}").write_text("x")
    sup.sweep(str(home), UID, GID)  # dừng sớm, không raise


def test_sweeper_loop_sweeps_every_slot(monkeypatch):
    sup = _load(monkeypatch)
    sup.N, sup.ROOT = 3, "/r"
    seen = []
    monkeypatch.setattr(sup, "sweep", lambda h, u, g: seen.append((h, u, g)))
    waits = iter([False, True])
    monkeypatch.setattr(sup.stop, "wait", lambda t: next(waits))
    sup.sweeper()
    assert seen == [(f"/r/slot{i}", 20000 + i, 10100) for i in (1, 2, 3)]
