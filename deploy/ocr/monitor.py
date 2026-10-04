"""Sample OCR GPU allocation from before model load until a run marker appears."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def query() -> tuple[int, dict[int, tuple[str, int]]]:
    free_text = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
        text=True, timeout=3,
    ).splitlines()
    if len(free_text) != 1:
        raise RuntimeError("expected one GPU")
    apps_text = subprocess.check_output(
        ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,used_gpu_memory",
         "--format=csv,noheader,nounits"], text=True, timeout=3,
    )
    apps = {}
    for line in apps_text.splitlines():
        uuid, pid, used = (part.strip() for part in line.split(","))
        apps[int(pid)] = (uuid, int(used))
    return int(free_text[0].strip()), apps


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("done", type=Path)
    args = parser.parse_args()
    baseline_free, baseline_apps = query()
    if baseline_free < 9216:
        raise SystemExit("free GPU memory below pre-load reserve")
    incumbent_pids = set(baseline_apps)
    device_uuids = {uuid for uuid, _ in baseline_apps.values()}
    if len(device_uuids) != 1:
        raise SystemExit("incumbent GPU UUID ambiguous")
    device_uuid = next(iter(device_uuids))
    samples = []
    anomalies = []
    owned_pid = None
    while not args.done.exists():
        try:
            free, apps = query()
            if not incumbent_pids <= set(apps):
                raise RuntimeError("incumbent GPU PID changed")
            new_pids = set(apps) - incumbent_pids
            if len(new_pids) > 1:
                raise RuntimeError("multiple new GPU PIDs")
            if new_pids:
                observed = next(iter(new_pids))
                if owned_pid is not None and observed != owned_pid:
                    raise RuntimeError("owned GPU PID changed")
                owned_pid = observed
            if owned_pid is not None and owned_pid not in apps:
                raise RuntimeError("owned GPU PID disappeared")
            owned = 0 if owned_pid is None else apps[owned_pid][1]
            if owned_pid is not None and apps[owned_pid][0] != device_uuid:
                raise RuntimeError("owned GPU UUID differs")
            if owned > 7168 or free < max(2048, 9216 - owned):
                raise RuntimeError(f"quota violated: free={free} own={owned}")
            samples.append({"t": time.time(), "free_mib": free,
                            "owned_mib": owned, "host_pid": owned_pid})
        except Exception as exc:
            anomalies.append({"t": time.time(), "error": str(exc)})
            break
        time.sleep(.25)
    args.output.write_text(json.dumps({"baseline_free_mib": baseline_free,
                                      "incumbent_pids": sorted(incumbent_pids),
                                      "samples": samples, "anomalies": anomalies}) + "\n")
    if anomalies or not samples:
        raise SystemExit("GPU monitor failed; inspect output")
    print(f"samples={len(samples)} min_free={min(x['free_mib'] for x in samples)} "
          f"max_owned={max(x['owned_mib'] for x in samples)}")


if __name__ == "__main__":
    main()
