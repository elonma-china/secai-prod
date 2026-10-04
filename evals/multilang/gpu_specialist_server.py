"""Isolated, fail-closed GPU diagnostic owner for pinned Hy-MT2-7B Q4_K_M.

This is a benchmark launcher, not a production OCR/MT broker. OCR does not yet
participate in its advisory lock; an operator must keep the OCR slot stopped.
No model is loaded by the default --check-only mode.
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

BASE = Path("/workspace/secai-gpu-specialists")
MODEL = BASE / "model/Hy-MT2-7B-Q4_K_M.gguf"
MODEL_SHA256 = "9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b"
MODEL_REVISION = "ab8472660ac61fac25f1af43fac2599d52a8a775"
SERVER = BASE / "runtime/llama.cpp/build-cuda/bin/llama-server"
SERVER_SHA256 = "968c20dc975560d48587321068931e6d5b1ab2e0430491ff37b38d6eca8a8239"
RUNTIME_SO_HASHES = {
    "libggml-base.so.0.25.3": "1688cc140ee8a09536d3ed4af94466c27b682643c33240d05dcb43842bb44778",
    "libggml.so.0.25.3": "4c0f9721bc64192d73ce2070fd751fa47a34eb11329738379683850f18eb71cd",
    "libllama-common.so.0.5.0": "69832c6e3bb1ff751d520b94f4529804c44e6d5689e46f4c7dd36f09564c2662",
    "libmtmd.so.0.5.0": "876974aff7a301fe13e2b805369b6d08f32c2c442c64de8dfb67ace77accc5aa",
    "libllama-server-impl.so": "93440d752df9414b674ce546ca136be6705e0749238080015bfd217d518d6d42",
    "libggml-cuda.so.0.25.3": "a57d429e95e71c58ea27d02afbda1d6953920c6246ae310a883ab41db6144cb6",
    "libllama.so.0.5.0": "c331fdcc85ddc0a2f730054b92e7603176aa617482b1450351c57794bae08404",
    "libggml-cpu.so.0.25.3": "e86506ede6aa20a03ff019005f24d1001e0a5fa50b733e92cdad082558d63de0",
}
LLAMA_COMMIT = "11fe02151f79c41d0d4af7da708755d73b9c0da6"
COMPAT_LIBRARY = Path("/usr/local/cuda-13.0/compat/libcuda.so.580.95.05")
COMPAT_SHA256 = "f27223c58d4c0d2ead3c2d747eb30a530c449f89c42e16b23e9bef04f3e6dc2e"
GPU_UUID = "GPU-d2b574d4-95fd-702a-37cc-d57253daa7bc"
GPU_NAME = "NVIDIA A100-SXM4-80GB"
DRIVER = "535.230.02"
EXPECTED_INCUMBENTS = frozenset({3203145, 3257875, 3276685})
OWNED_CAP_MIB = 6144
FREE_RESERVE_MIB = 2048
PRELOAD_FREE_MIB = OWNED_CAP_MIB + FREE_RESERVE_MIB
MIN_RAM_BYTES = 16 * 1024**3
MIN_DISK_BYTES = 15 * 1024**3
PORT = 18767
MAX_SERVE_SECONDS = 6 * 60 * 60
MODEL_ALIAS = f"tencent/Hy-MT2-7B-GGUF@{MODEL_REVISION}:Q4_K_M:gpu"
EOG_OVERRIDE = ("tokenizer.ggml.eos_token_id=int:127960,"
                "tokenizer.ggml.eot_token_id=int:127967")
LOCK = BASE / "admission.lock"
POISONED = BASE / "poisoned-cleanup.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _lines(arguments: list[str]) -> list[str]:
    result = subprocess.run(arguments, check=True, capture_output=True, text=True,
                            timeout=10)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def gpu_snapshot() -> dict:
    devices = _lines(["nvidia-smi", "--query-gpu=uuid,name,driver_version,memory.total,"
                      "memory.free,utilization.gpu", "--format=csv,noheader,nounits"])
    if len(devices) != 1:
        raise RuntimeError("expected_exactly_one_gpu")
    fields = [piece.strip() for piece in devices[0].split(",")]
    if len(fields) != 6:
        raise RuntimeError("invalid_gpu_snapshot")
    uuid, name, driver = fields[:3]
    total, free, utilization = map(int, fields[3:])
    apps = _lines(["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory",
                   "--format=csv,noheader,nounits"])
    processes: dict[int, int] = {}
    for line in apps:
        parts = [piece.strip() for piece in line.split(",")]
        if len(parts) != 2:
            raise RuntimeError("invalid_gpu_process_snapshot")
        pid, used = map(int, parts)
        if pid in processes:
            raise RuntimeError("duplicate_gpu_pid")
        processes[pid] = used
    return {"uuid": uuid, "name": name, "driver": driver, "total_mib": total,
            "free_mib": free, "utilization_percent": utilization,
            "processes_mib": processes}


def validate_snapshot(snapshot: dict, *, owner_host_pid: int | None = None,
                      before_load: bool = False) -> tuple[int, int | None]:
    if (snapshot["uuid"] != GPU_UUID or snapshot["name"] != GPU_NAME or
            snapshot["driver"] != DRIVER or snapshot["total_mib"] != 81920):
        raise RuntimeError("gpu_identity_changed")
    actual = set(snapshot["processes_mib"])
    added = actual - EXPECTED_INCUMBENTS
    missing = EXPECTED_INCUMBENTS - actual
    if missing or len(added) > 1 or (before_load and added):
        raise RuntimeError(f"gpu_process_set_changed:added={sorted(added)},missing={sorted(missing)}")
    if owner_host_pid is not None and added != {owner_host_pid}:
        raise RuntimeError("owned_gpu_host_pid_disappeared_or_changed")
    # nvidia-smi reports host PIDs, whereas Popen.pid is in the container PID
    # namespace. Only the unique new host PID can be attributed to this
    # diagnostic slot; OCR must remain stopped because it does not share LOCK.
    discovered = owner_host_pid if owner_host_pid is not None else next(iter(added), None)
    owned = snapshot["processes_mib"].get(discovered, 0) if discovered else 0
    if owned > OWNED_CAP_MIB:
        raise RuntimeError(f"owned_vram_cap_exceeded:{owned}")
    required_free = FREE_RESERVE_MIB + max(0, OWNED_CAP_MIB - owned)
    if snapshot["free_mib"] < required_free:
        raise RuntimeError(f"gpu_free_reserve_breached:{snapshot['free_mib']}<{required_free}")
    if before_load and snapshot["utilization_percent"] != 0:
        raise RuntimeError("gpu_busy_before_load")
    return owned, discovered


def host_resources() -> dict:
    available = 0
    with Path("/proc/meminfo").open() as stream:
        for line in stream:
            if line.startswith("MemAvailable:"):
                available = int(line.split()[1]) * 1024
                break
    disk = shutil.disk_usage(BASE).free
    if available < MIN_RAM_BYTES or disk < MIN_DISK_BYTES:
        raise RuntimeError("host_resource_reserve_breached")
    return {"available_ram_bytes": available, "available_disk_bytes": disk}


def verify_artifacts() -> dict:
    expected = {MODEL: MODEL_SHA256, SERVER: SERVER_SHA256,
                COMPAT_LIBRARY: COMPAT_SHA256}
    expected.update({SERVER.parent / name: digest for name, digest in RUNTIME_SO_HASHES.items()})
    for path, digest in expected.items():
        if len(digest) != 64 or sha256_file(path) != digest:
            raise RuntimeError(f"artifact_checksum_mismatch:{path.name}")
    commit = _lines(["git", "-C", str(BASE / "runtime/llama.cpp"), "rev-parse", "HEAD"])
    if commit != [LLAMA_COMMIT]:
        raise RuntimeError("llama_commit_mismatch")
    return {"model_sha256": MODEL_SHA256, "server_sha256": SERVER_SHA256,
            "runtime_so_sha256": RUNTIME_SO_HASHES,
            "compat_sha256": COMPAT_SHA256, "llama_commit": LLAMA_COMMIT}


def server_command() -> list[str]:
    return [str(SERVER), "--model", str(MODEL), "--host", "127.0.0.1",
            "--port", str(PORT), "--threads", "4", "--threads-batch", "4",
            "--parallel", "1", "--ctx-size", "2048", "--batch-size", "512",
            "--ubatch-size", "256", "--n-gpu-layers", "99", "--fit", "off",
            "--override-kv", EOG_OVERRIDE, "--no-webui", "--alias", MODEL_ALIAS]


def listener_pid() -> int | None:
    """Container PID of the exact loopback listener, independent of GPU host PID."""
    rows = _lines(["ss", "-H", "-ltnp", "sport", "=", f":{PORT}"])
    if not rows:
        return None
    if len(rows) != 1 or f"127.0.0.1:{PORT}" not in rows[0]:
        raise RuntimeError("unexpected_server_listener")
    pids = {int(match) for match in re.findall(r"pid=(\d+)", rows[0])}
    if len(pids) != 1:
        raise RuntimeError("server_listener_owner_unavailable")
    return next(iter(pids))


def check_only() -> dict:
    if POISONED.exists():
        raise RuntimeError("prior_gpu_cleanup_not_proven")
    artifacts = verify_artifacts()
    resources = host_resources()
    snapshot = gpu_snapshot()
    validate_snapshot(snapshot, before_load=True)
    if listener_pid() is not None:
        raise RuntimeError("server_port_already_bound")
    return {"status": "READY_FOR_ROOT_OWNED_GPU_LOAD", "artifacts": artifacts,
            "host": resources, "gpu": snapshot, "server_command": server_command(),
            "warning": "OCR does not yet share the advisory lock; root must hold its slot stopped"}


def _healthy() -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def _full_offload(log_path: Path) -> bool:
    return bool(re.search(r"offloaded 33/33 layers to GPU", log_path.read_text(errors="replace")))


def _stop_child(process: subprocess.Popen) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def _arm_parent_death(parent_pid: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent_pid:
        os._exit(127)


def serve() -> None:
    os.umask(0o077)
    LOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK.open("a+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        baseline = check_only()
        run_dir = BASE / "evidence" / time.strftime("gpu-run-%Y%m%dT%H%M%SZ", time.gmtime())
        run_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        (run_dir / "preflight.json").write_text(json.dumps(baseline, sort_keys=True) + "\n")
        environment = os.environ.copy()
        environment["CUDA_VISIBLE_DEVICES"] = "0"
        environment["OMP_NUM_THREADS"] = "4"
        environment["LD_LIBRARY_PATH"] = ("/usr/local/cuda-13.0/compat:"
                                          "/usr/local/cuda-13.0/lib64")
        log_path = run_dir / "server.log"
        stopped = False

        def request_stop(_signum, _frame):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        parent_pid = os.getpid()
        with log_path.open("x") as log_file:
            process = subprocess.Popen(server_command(), stdout=log_file,
                                       stderr=subprocess.STDOUT, env=environment,
                                       start_new_session=True,
                                       pass_fds=(lock_file.fileno(),),
                                       preexec_fn=lambda: _arm_parent_death(parent_pid))
            peak = 0
            owner_host_pid = None
            ready = False
            reason = "operator_stop"
            started = time.monotonic()
            try:
                with (run_dir / "gpu-samples.jsonl").open("x") as samples:
                    while not stopped and process.poll() is None:
                        snapshot = gpu_snapshot()
                        # Before the owner appears in nvidia-smi, retain the full preload reserve.
                        owned, owner_host_pid = validate_snapshot(
                            snapshot, owner_host_pid=owner_host_pid)
                        resources = host_resources()
                        peak = max(peak, owned)
                        samples.write(json.dumps({"elapsed_s": round(time.monotonic()-started, 2),
                                                  "owned_mib": owned, "snapshot": snapshot,
                                                  "host": resources},
                                                 sort_keys=True) + "\n")
                        samples.flush(); os.fsync(samples.fileno())
                        if not ready and _healthy():
                            # Three independent observations must line up:
                            # this Popen child owns the live listener; its own
                            # log proves full GPU offload; one new host GPU PID
                            # has a positive allocation. The container PID is
                            # never compared to nvidia-smi's host PID.
                            if (listener_pid() != process.pid or
                                    owner_host_pid is None or owned <= 0 or
                                    not _full_offload(log_path)):
                                raise RuntimeError("full_gpu_offload_not_attested")
                            ready = True
                            print(json.dumps({"event": "ready", "container_pid": process.pid,
                                              "gpu_host_pid": owner_host_pid,
                                              "run_dir": str(run_dir), "owned_mib": owned,
                                              "free_mib": snapshot["free_mib"]}), flush=True)
                        if not ready and time.monotonic() - started > 180:
                            raise RuntimeError("gpu_model_startup_timeout")
                        if time.monotonic() - started > MAX_SERVE_SECONDS:
                            reason = "server_whole_deadline"
                            break
                        time.sleep(1)
                if process.poll() is not None and not stopped:
                    reason = f"server_exited_{process.returncode}"
            except Exception as error:
                reason = f"safety_stop:{type(error).__name__}:{error}"
                raise
            finally:
                _stop_child(process)
                clean = False
                for _ in range(30):
                    snapshot = gpu_snapshot()
                    if (set(snapshot["processes_mib"]) == EXPECTED_INCUMBENTS and
                            snapshot["free_mib"] >= PRELOAD_FREE_MIB):
                        clean = True
                        break
                    time.sleep(1)
                summary = {"reason": reason, "peak_owned_mib": peak,
                           "clean_exit_attested": clean,
                           "owner_container_pid": process.pid,
                           "owner_gpu_host_pid": owner_host_pid,
                           "final_gpu": snapshot}
                (run_dir / "summary.json").write_text(json.dumps(summary, sort_keys=True)+"\n")
                if not clean:
                    POISONED.write_text(json.dumps(summary, sort_keys=True)+"\n")
                print(json.dumps({"event": "complete", **summary}), flush=True)
                if not clean:
                    raise RuntimeError("gpu_cleanup_not_proven; future admission poisoned")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true",
                        help="load the GPU model under the ownership and resource guard")
    args = parser.parse_args()
    if args.serve:
        serve()
    else:
        print(json.dumps(check_only(), sort_keys=True))
