"""Deploy/rollback only the isolated translation Docker project."""
from __future__ import annotations
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import time
import urllib.request

PROJECT = "secai-translation-release"
NAMES = ("secai-translation-gateway", "secai-translation-worker", "secai-translation-frontend")


def verify_profiles(root):
    expected = dict(zip(("gateway", "worker", "frontend"), NAMES))
    allowed_mounts = {"translation-data:/app/data", str(root / "tokenizer") + ":/app/translation-tokenizer:ro"}
    for profile in ("release", "rollback"):
        data = json.loads((root / (profile + ".json")).read_text())
        assert set(data["services"]) == set(expected), "unexpected services"
        assert data["volumes"] == {"translation-data": {"name": "secai-translation-data"}}, "unexpected volumes"
        assert data["networks"] == {"secai-net": {"external": True, "name": "secai-docker_secai-net"}}, "unexpected networks"
        for service, name in expected.items():
            cfg = data["services"][service]
            assert cfg["container_name"] == name, "unexpected container name"
            assert not cfg.get("privileged") and not cfg.get("devices") and not cfg.get("volumes_from"), "unexpected host access"
            if service == "frontend":
                assert cfg.get("network_mode") == "host" and cfg["environment"]["FE_PORT"] == "5179"
                assert not cfg.get("volumes") and not cfg.get("ports")
            else:
                assert set(cfg.get("volumes", [])) == allowed_mounts, "unexpected mounts"
                assert cfg.get("network_mode") == "host" and not cfg.get("networks") and not cfg.get("ports")
                command = cfg["command"]
                if service == "gateway":
                    assert command == ["uvicorn", "secai_gateway.app:app" if profile == "rollback" else "secai_gateway.multilang_app:app", "--host", "0.0.0.0", "--port", "18185"]
                else:
                    assert command == ["python", "-m", "secai_gateway.translation.worker"]


def compose(root, profile, *args):
    return subprocess.run(["docker", "compose", "-p", PROJECT, "-f",
                           str(root / (profile + ".json")), *args],
                          check=True, capture_output=True, text=True).stdout


def verify_ownership():
    for name in NAMES:
        p = subprocess.run(["docker", "inspect", name], capture_output=True, text=True)
        if p.returncode == 0:
            labels = json.loads(p.stdout)[0]["Config"]["Labels"] or {}
            if labels.get("com.docker.compose.project") != PROJECT:
                raise RuntimeError("Container name belongs to another deployment: " + name)


def ready(*, multilang):
    stable_worker_samples = 0
    for _ in range(45):
        try:
            for port in (18185, 5179):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                    assert r.status == 200
                if multilang:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/ai/v3/translate/languages", timeout=5) as r:
                        data = json.load(r)
                        assert len(data["pairs"]) == 16 and all(x["enabled"] for x in data["pairs"])
            if multilang:
                p = subprocess.run(["docker", "inspect", "secai-translation-worker"],
                                   check=True, capture_output=True, text=True)
                worker = json.loads(p.stdout)[0]
                assert worker["State"]["Running"] and worker["RestartCount"] == 0, "worker not stable"
                stable_worker_samples += 1
                if stable_worker_samples < 3:
                    time.sleep(1)
                    continue
            return
        except Exception:
            stable_worker_samples = 0
            time.sleep(1)
    raise RuntimeError("Translation deployment readiness failed")


def activate(root, profile):
    # Both profiles have only our three uniquely named services and own volume.
    verify_profiles(root)
    verify_ownership()
    if profile == "release":
        image = json.loads((root / "release.json").read_text())["services"]["gateway"]["image"]
        subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
                        "-e", "POC_CATALOG_DB=/tmp/release-poc.db", "-e", "GATEWAY_DB_PATH=/tmp/release-gw.db",
                        image, "-c", "import secai_gateway.translation.worker"],
                       check=True, capture_output=True, text=True)
    compose(root, "release", "stop", "worker")
    compose(root, profile, "up", "-d", "--no-build", "gateway", "frontend")
    if profile == "release":
        compose(root, profile, "up", "-d", "--no-build", "--force-recreate", "worker")
    ready(multilang=profile == "release")
    state = root / "active.json"
    state.write_text(json.dumps({"active": profile}))
    state.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("deploy", "rollback", "check", "status"))
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.state_dir.resolve()
    # A cooperative lock prevents two operators switching this project together.
    with (root / "deployment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_profiles(root)
        verify_ownership()
        if args.action == "check":
            for profile in ("release", "rollback"):
                compose(root, profile, "config", "--quiet")
        elif args.action == "rollback":
            activate(root, "rollback")
        elif args.action == "deploy":
            try:
                activate(root, "release")
            except Exception:
                activate(root, "rollback")
                raise
        print((root / "active.json").read_text() if (root / "active.json").exists() else '{"active":"prepared"}')


if __name__ == "__main__":
    main()
