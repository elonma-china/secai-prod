"""metasploit runner: chạy `check` (verify, KHÔNG exploit) cho 1 module → verdict từ CheckCode."""
from __future__ import annotations

from typing import Any

from .docker_exec import ExecConfig, run_executor

# Ánh xạ chuỗi CheckCode của msf → verdict chuẩn.
_VULN = ("appears to be vulnerable", "is vulnerable", "target is vulnerable")
_SAFE = ("not exploitable", "not appear to be vulnerable", "target is not vulnerable", "is not vulnerable")
_UNSURE = ("cannot reliably check", "cannot be validated", "could not be validated",
           "no check", "does not support check", "check failed", "unable to")


async def verify(cfg: ExecConfig, *, module: str, target: str, rport: int | None = None,
                 run_id: str = "") -> dict[str, Any]:
    args = ["msf-check", "--module", module, "--target", target]
    if rport:
        args += ["--rport", str(rport)]
    res = await run_executor(cfg, args, run_id=run_id)
    low = res.stdout.lower()
    if any(s in low for s in _VULN):
        verdict = "vulnerable"
    elif any(s in low for s in _SAFE):
        verdict = "not-vulnerable"
    elif res.timed_out:
        verdict = "inconclusive"
    elif any(s in low for s in _UNSURE):
        verdict = "inconclusive"
    else:
        verdict = "inconclusive"
    return {
        "engine": "metasploit",
        "verdict": verdict,
        "module": module,
        "rc": res.rc,
        "output_tail": res.stdout[-1500:],
        "stderr_tail": res.stderr[-500:],
    }
