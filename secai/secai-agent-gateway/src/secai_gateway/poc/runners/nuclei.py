"""nuclei runner: chạy 1 template vào target, parse JSONL → verdict có cấu trúc (§6)."""
from __future__ import annotations

import json
from typing import Any

from .docker_exec import ExecConfig, run_executor


async def verify(cfg: ExecConfig, *, template_host_path: str, target: str, run_id: str = "") -> dict[str, Any]:
    mounts = [(template_host_path, "/input/tpl.yaml")]
    res = await run_executor(cfg, ["nuclei", "--template", "/input/tpl.yaml", "--target", target],
                             mounts, run_id=run_id)
    matches: list[dict[str, Any]] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            matches.append(json.loads(line))
        except ValueError:
            continue
    # nuclei exit 0 dù match hay không; rc!=0 = lỗi template/chạy.
    if res.timed_out:
        verdict = "inconclusive"
    elif matches:
        verdict = "vulnerable"
    elif res.rc != 0:
        verdict = "error"
    else:
        verdict = "not-vulnerable"
    return {
        "engine": "nuclei",
        "verdict": verdict,
        "matched": len(matches),
        "evidence": [
            {"template": m.get("template-id"), "name": (m.get("info") or {}).get("name"),
             "severity": (m.get("info") or {}).get("severity"), "matched_at": m.get("matched-at")}
            for m in matches[:10]
        ],
        "rc": res.rc,
        "stderr_tail": res.stderr[-500:],
    }
