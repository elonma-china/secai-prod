"""supervisor: HOME slot nhóm = gid gateway + 2770, engine chạy umask 007 (gateway dọn được engagements, slot khác không đọc)."""
import importlib.util
import pathlib

_SRC = pathlib.Path(__file__).resolve().parent.parent / "stack/images/pentestcode-vm/supervisor.py"


def _load(monkeypatch, gid=None):
    if gid is None:
        monkeypatch.delenv("VM_GATEWAY_GID", raising=False)
    else:
        monkeypatch.setenv("VM_GATEWAY_GID", str(gid))
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


def test_home_group_is_gateway_gid_mode_2770(monkeypatch, tmp_path):
    sup, calls = _ensure(monkeypatch, tmp_path)
    uid, home = sup.ensure_user(3)
    assert (uid, home) == (20003, f"{tmp_path}/slot3")
    assert calls["chown"] == [(home, 20003, 10001)]
    assert calls["chmod"] == [(home, 0o2770)]


def test_gateway_gid_from_env(monkeypatch, tmp_path):
    sup, calls = _ensure(monkeypatch, tmp_path, gid=12345)
    _, home = sup.ensure_user(1)
    assert calls["chown"][0] == (home, 20001, 12345)


def test_existing_old_ias_tree_is_regrouped(monkeypatch, tmp_path):
    sup, calls = _ensure(monkeypatch, tmp_path)
    eng = tmp_path / "slot2/.ias/engagements/e1"
    eng.mkdir(parents=True)
    (eng / "state.json").write_text("{}")
    sup.ensure_user(2)
    chowned = {p for p, _, g in calls["chown"] if g == 10001}
    assert {str(eng), str(eng / "state.json")} <= chowned
    modes = dict(calls["chmod"])
    assert modes[str(eng)] & 0o2070 == 0o2070 and modes[str(eng / "state.json")] & 0o070 == 0o070


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
