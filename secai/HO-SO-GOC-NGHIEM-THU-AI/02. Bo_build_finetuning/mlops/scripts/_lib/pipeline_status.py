#!/usr/bin/env python3
"""Ghi/cập nhật `runs/<run_id>/status.json` để trigger_api + trang nghiệm thu đọc tiến độ từng
giai đoạn THẬT (không giả lập). run_pipeline.sh gọi script này ở mỗi mốc giai đoạn.

status.json:
{
  "run_id": "...", "mode": "dry|execute|deploy", "started_at": "...", "ended_at": null,
  "overall": "running|done|failed|stopped",
  "stages": [
     {"id":"01","name":"...","state":"pending|running|done|skipped|failed",
      "started_at":..., "ended_at":..., "log":"logs/01.log", "exit_code":null, "note":""},
     ...
  ]
}

Ghi kiểu đọc-sửa-ghi nguyên file (atomic qua tmp + os.replace) — 1 tiến trình ghi (run_pipeline.sh
tuần tự), nhiều tiến trình đọc (trigger_api/web) nên không cần khoá.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def cmd_init(args: argparse.Namespace) -> None:
    stages = []
    for spec in args.stages.split("|"):
        spec = spec.strip()
        if not spec:
            continue
        sid, _, name = spec.partition(":")
        stages.append({
            "id": sid.strip(), "name": name.strip(), "state": "pending",
            "started_at": None, "ended_at": None,
            "log": f"logs/{sid.strip()}.log", "exit_code": None, "note": "",
        })
    data = {
        "run_id": args.run_id, "mode": args.mode, "started_at": _now(), "ended_at": None,
        "overall": "running", "stages": stages,
    }
    _save(Path(args.file), data)


def cmd_set(args: argparse.Namespace) -> None:
    path = Path(args.file)
    data = _load(path)
    for st in data["stages"]:
        if st["id"] == args.stage:
            if args.state:
                st["state"] = args.state
                if args.state == "running" and not st["started_at"]:
                    st["started_at"] = _now()
                if args.state in ("done", "failed", "skipped"):
                    st["ended_at"] = _now()
            if args.exit_code is not None:
                st["exit_code"] = args.exit_code
            if args.note is not None:
                st["note"] = args.note
            break
    _save(path, data)


def cmd_overall(args: argparse.Namespace) -> None:
    path = Path(args.file)
    data = _load(path)
    data["overall"] = args.state
    data["ended_at"] = _now()
    _save(path, data)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init")
    pi.add_argument("--file", required=True)
    pi.add_argument("--run-id", required=True)
    pi.add_argument("--mode", required=True)
    pi.add_argument("--stages", required=True, help="'01:Tên|02:Tên|...'")
    pi.set_defaults(func=cmd_init)

    ps = sub.add_parser("set")
    ps.add_argument("--file", required=True)
    ps.add_argument("--stage", required=True)
    ps.add_argument("--state", choices=["pending", "running", "done", "skipped", "failed"])
    ps.add_argument("--exit-code", type=int, dest="exit_code")
    ps.add_argument("--note")
    ps.set_defaults(func=cmd_set)

    po = sub.add_parser("overall")
    po.add_argument("--file", required=True)
    po.add_argument("--state", required=True, choices=["running", "done", "failed", "stopped"])
    po.set_defaults(func=cmd_overall)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
