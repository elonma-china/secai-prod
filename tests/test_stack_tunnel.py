"""stack/images/tunnel/entrypoint.sh (Task 8d): ssh/socat giả qua PATH, không mạng thật."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ENTRY = Path(__file__).resolve().parents[1] / "stack/images/tunnel/entrypoint.sh"

FAKE_SSH = '''#!/bin/sh
printf '%s\\n' "$*" >> "$LOG"
n=$(wc -l < "$LOG")
[ "$n" -lt 2 ] || kill -TERM "$PPID"   # lần nối lại thứ hai: dừng entrypoint như `docker stop`
exit 255
'''
FAKE_SOCAT = '''#!/bin/sh
printf '%s\\n' "$*" >> "$LOG"
exec sleep 30
'''


def _env(tmp_path, **kv):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    for name, text in (("ssh", FAKE_SSH), ("socat", FAKE_SOCAT)):
        (b / name).write_text(text)
        (b / name).chmod(0o755)
    d = tmp_path / "run"
    d.mkdir(exist_ok=True)
    (d / "key").write_text("k")
    (d / "key").chmod(0o600)
    (d / "known_hosts").write_text("[10.0.0.9]:2222 ssh-ed25519 AAAA\n")
    return {"PATH": f"{b}:{os.environ['PATH']}", "LOG": str(tmp_path / "log"), "TUNNEL_DIR": str(d),
            "TUNNEL_RETRY_SECONDS": "0", **kv}


SSH = {"TUNNEL_MODE": "ssh", "TUNNEL_SSH_HOST": "10.0.0.9", "TUNNEL_SSH_PORT": "2222", "TUNNEL_SSH_USER": "tunnel",
       "TUNNEL_REMOTE_SPECIALIST_PORT": "28868", "TUNNEL_REMOTE_OCR_PORT": "28083"}


def test_ssh_mode_hardened_options_and_reconnects(tmp_path):
    r = subprocess.run(["sh", str(ENTRY)], env=_env(tmp_path, **SSH), capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr                      # TERM -> thoát sạch
    calls = (tmp_path / "log").read_text().splitlines()
    assert len(calls) == 2 and "nối lại" in r.stderr        # ssh chết thì tự nối lại
    a = calls[0]
    for opt in ("-N", "-F /dev/null", "-p 2222", "-l tunnel", "BatchMode=yes", "StrictHostKeyChecking=yes",
                f"UserKnownHostsFile={tmp_path}/run/known_hosts", "GlobalKnownHostsFile=/dev/null",
                "PasswordAuthentication=no", "KbdInteractiveAuthentication=no", "IdentitiesOnly=yes",
                "ExitOnForwardFailure=yes", "ServerAliveInterval=", f"-i {tmp_path}/run/key",
                "-L 127.0.0.1:18868:127.0.0.1:28868", "-L 127.0.0.1:18083:127.0.0.1:28083"):
        assert opt in a, opt
    assert a.endswith("-- 10.0.0.9")


def test_ssh_mode_refuses_missing_settings(tmp_path):
    for k in ("TUNNEL_SSH_HOST", "TUNNEL_SSH_USER"):
        r = subprocess.run(["sh", str(ENTRY)], env=_env(tmp_path, **{**SSH, k: ""}), capture_output=True, text=True, timeout=10)
        assert r.returncode != 0 and k in r.stderr
    e = _env(tmp_path, **SSH)
    (Path(e["TUNNEL_DIR"]) / "known_hosts").write_text("")
    r = subprocess.run(["sh", str(ENTRY)], env=e, capture_output=True, text=True, timeout=10)
    assert r.returncode != 0 and "known_hosts" in r.stderr
    assert not (tmp_path / "log").exists()                  # không bao giờ gọi ssh khi thiếu cấu hình


def _start(tmp_path, **kv):
    p = subprocess.Popen(["sh", str(ENTRY)], env=_env(tmp_path, **kv), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    return p


def test_direct_mode_socat_to_stub_and_stops_on_term(tmp_path):
    p = _start(tmp_path, TUNNEL_MODE="direct", TUNNEL_DIRECT_SPECIALIST="stub-specialist:18868", TUNNEL_DIRECT_OCR="stub-ocr:18083")
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=10)
    calls = sorted((tmp_path / "log").read_text().splitlines())
    assert calls == ["TCP-LISTEN:18083,bind=127.0.0.1,reuseaddr,fork TCP:stub-ocr:18083",
                     "TCP-LISTEN:18868,bind=127.0.0.1,reuseaddr,fork TCP:stub-specialist:18868"]
    assert "CHỈ ĐỂ TEST" in err and p.returncode == 0


def test_off_mode_idles_and_bad_mode_fails(tmp_path):
    p = _start(tmp_path, TUNNEL_MODE="off")
    assert p.poll() is None
    p.send_signal(signal.SIGTERM)
    p.communicate(timeout=10)
    assert p.returncode == 0 and not (tmp_path / "log").exists()
    r = subprocess.run(["sh", str(ENTRY)], env=_env(tmp_path, TUNNEL_MODE="vpn"), capture_output=True, text=True, timeout=10)
    assert r.returncode != 0 and "TUNNEL_MODE" in r.stderr


def test_exits_when_translation_gateway_netns_is_gone(tmp_path):
    # Docker khởi động lại translation-gateway -> netns cũ chỉ còn lo; sidecar phải thoát (!= 0) để restart policy
    # cho nó nhập netns mới (thấy ở smoke 8d: không thoát thì tunnel chết lặng lẽ).
    net = tmp_path / "net"
    (net / "lo").mkdir(parents=True)
    for kv in ({"TUNNEL_MODE": "direct", "TUNNEL_DIRECT_SPECIALIST": "s:1"}, SSH):
        r = subprocess.run(["sh", str(ENTRY)], env=_env(tmp_path, TUNNEL_NET_DIR=str(net), **kv), capture_output=True, text=True, timeout=20)
        assert r.returncode != 0 and "netns" in r.stderr, kv


WATCH = Path(__file__).resolve().parents[1] / "stack/lib/netns_watch.py"


def _watch(tmp_path, ifaces, *cmd, **kw):
    net = tmp_path / "net"
    for i in ifaces:
        (net / i).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "NETNS_WATCH_DIR": str(net), "NETNS_WATCH_INTERVAL": "0.2"}
    return subprocess.Popen([sys.executable, "-I", str(WATCH), *cmd], env=env, stderr=subprocess.PIPE, text=True, **kw)


def test_worker_watch_exits_when_netns_lost(tmp_path):
    p = _watch(tmp_path, ["lo"], "sleep", "30")
    assert p.wait(10) == 1 and "netns" in p.stderr.read()


def test_worker_watch_passes_exit_code_and_forwards_term(tmp_path):
    assert _watch(tmp_path, ["lo", "eth0"], "sh", "-c", "exit 3").wait(10) == 3
    p = _watch(tmp_path, ["lo", "eth0"], "sh", "-c", "trap 'exit 7' TERM; while :; do sleep 0.1; done")
    time.sleep(1)
    p.send_signal(signal.SIGTERM)
    assert p.wait(10) == 7
