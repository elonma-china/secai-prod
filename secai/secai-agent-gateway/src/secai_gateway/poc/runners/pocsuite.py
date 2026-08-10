"""pocsuite3 runner: nạp 1 PoC (POCBase) --verify vào target. Output pocsuite dạng bảng nên
verdict là heuristic (kèm output thô để đánh giá lại)."""
from __future__ import annotations

from typing import Any

from .docker_exec import ExecConfig, run_executor


async def verify(cfg: ExecConfig, *, poc_host_path: str, target: str, run_id: str = "") -> dict[str, Any]:
    mounts = [(poc_host_path, "/input/poc.py")]
    res = await run_executor(cfg, ["pocsuite", "--poc", "/input/poc.py", "--target", target],
                             mounts, run_id=run_id)
    low = res.stdout.lower()
    if res.timed_out:
        verdict = "inconclusive"
    elif ("succeed" in low or "vulnerable" in low) and "failed" not in low:
        verdict = "vulnerable"
    elif "failed" in low or "not vulnerable" in low:
        verdict = "not-vulnerable"
    else:
        verdict = "inconclusive"
    return {
        "engine": "pocsuite3",
        "verdict": verdict,
        "rc": res.rc,
        "output_tail": res.stdout[-1500:],
        "stderr_tail": res.stderr[-500:],
    }
