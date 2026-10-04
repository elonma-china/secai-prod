import os
import signal
import subprocess
import sys
import time
from pathlib import Path


LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "run_translate_v2_local.py"
BOOTSTRAP = r'''
import importlib.util, subprocess, sys
spec=importlib.util.spec_from_file_location("launcher", sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original=subprocess.Popen
count=0
case=sys.argv[3]
def fake_popen(cmd, **kwargs):
    global count
    count+=1
    if case=="second_fails" and count==2:
        raise OSError("worker startup failed")
    process=original([sys.executable,"-c","import time; time.sleep(30)"],**kwargs)
    print("CHILD_PID="+str(process.pid),flush=True)
    return process
module.subprocess.Popen=fake_popen
sys.argv=[sys.argv[1],"--store-dir",sys.argv[2]]
raise SystemExit(module.main())
'''


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _run_launcher_case(tmp_path, case):
    env = dict(os.environ, QWEN_BASE_URL="http://127.0.0.1:18080/v1", QWEN_MODEL="test")
    parent = subprocess.Popen([sys.executable, "-u", "-c", BOOTSTRAP,
                               str(LAUNCHER), str(tmp_path), case],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    pids = []
    try:
        expected = 2 if case == "sigterm" else 1
        while len(pids) < expected:
            line = parent.stdout.readline().strip()
            assert line, parent.stderr.read()
            if line.startswith("CHILD_PID="):
                pids.append(int(line.split("=", 1)[1]))
        if case == "sigterm":
            os.kill(parent.pid, signal.SIGTERM)
        parent.wait(timeout=5)
        for _ in range(20):
            if all(not _alive(pid) for pid in pids):
                break
            time.sleep(0.1)
        assert all(not _alive(pid) for pid in pids), "launcher left a child running"
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.wait(timeout=5)
        for pid in pids:
            if _alive(pid):
                os.kill(pid, signal.SIGTERM)


def test_launcher_sigterm_stops_its_api_and_worker(tmp_path):
    _run_launcher_case(tmp_path, "sigterm")


def test_launcher_worker_startup_failure_stops_api(tmp_path):
    _run_launcher_case(tmp_path, "second_fails")
