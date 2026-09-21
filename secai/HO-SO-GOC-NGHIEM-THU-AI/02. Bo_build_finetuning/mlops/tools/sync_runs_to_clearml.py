#!/usr/bin/env python3
"""Đồng bộ các run đã có trong `mlops/runs/` lên ClearML (self-host) làm nhật ký quan sát
(observability) — công cụ ĐỘC LẬP, bổ sung thêm (additive), giống tinh thần
`download_and_smoke_test.sh` cùng thư mục: không phụ thuộc vào và không sửa 10 script pipeline
hay `trigger_api/app.py`.

CHỈ ĐỌC thư mục `runs/` — KHÔNG chạy `run_pipeline.sh`, KHÔNG gọi bất kỳ endpoint nào của
`trigger_api` (không rent GPU, không train, không push HF, không promote). An toàn để chạy bất
cứ lúc nào, kể cả lặp lại nhiều lần trên cùng một run (idempotent theo `task_name=<run_id>`).

Với mỗi thư mục con của `runs/` (mỗi thư mục = 1 run_id):
  - tạo/lấy lại 1 ClearML Task project_name="secai-mlops-pipeline", task_name=<run_id>
  - upload `trigger_api.log` (nếu có) làm artifact
  - nếu có `report-production/finetune-decision.json` và/hoặc
    `report-candidate/finetune-decision.json`: upload nguyên văn JSON làm artifact + báo cáo
    bảng (report_table) các "checks" bên trong (metric/current/status/lý do) + 1 scalar
    tóm tắt decision (FINETUNE_REQUIRED=1 / còn lại=0).

Dùng:
  export CLEARML_CONFIG_FILE=/home/ict/.config/secai-mlops/clearml.conf   # xem trigger_api/README.md
  python3 sync_runs_to_clearml.py                     # quét toàn bộ mlops/runs/*
  python3 sync_runs_to_clearml.py --run-id <run_id>    # chỉ 1 run
  python3 sync_runs_to_clearml.py --dry-run            # chỉ in ra sẽ làm gì, KHÔNG gọi ClearML,
                                                        # KHÔNG cần cài package `clearml`
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
MLOPS_ROOT = TOOLS_DIR.parent
DEFAULT_RUNS_DIR = MLOPS_ROOT / "runs"
DEFAULT_PROJECT_NAME = "secai-mlops-pipeline"

# (khoá kết quả, đường dẫn tương đối trong run_dir) — khớp với trigger_api/app.py:status()
DECISION_FILES = (
    ("production_drift", "report-production/finetune-decision.json"),
    ("candidate_requalify", "report-candidate/finetune-decision.json"),
)

DECISION_TABLE_COLUMNS = (
    "id", "target", "metric", "current", "baseline", "status", "platform_kpi", "reason",
)


def iter_run_dirs(runs_dir: Path):
    if not runs_dir.is_dir():
        return
    for child in sorted(runs_dir.iterdir()):
        if child.is_dir():
            yield child


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  [warn] không đọc được {path}: {exc}", file=sys.stderr)
        return None


def decision_table_rows(decision: dict) -> list[list[Any]]:
    """Table dạng list-of-rows mà `Logger.report_table` chấp nhận (không cần pandas):
    hàng đầu là header."""
    rows: list[list[Any]] = [list(DECISION_TABLE_COLUMNS)]
    for check in decision.get("checks", []) or []:
        rows.append([
            check.get("id"),
            check.get("target"),
            check.get("metric"),
            check.get("current"),
            check.get("baseline"),
            check.get("status"),
            check.get("platform_kpi"),
            "; ".join(check.get("reason") or []),
        ])
    return rows


def find_existing_task(task_cls: Any, project_name: str, task_name: str) -> Any | None:
    # Task.get_tasks() khớp tên theo kiểu "partial match" — tự lọc lại để chỉ nhận đúng tên
    # (tránh 2 run_id 1 là tiền tố của run_id kia bị gộp nhầm).
    for existing in task_cls.get_tasks(project_name=project_name, task_name=task_name) or []:
        if getattr(existing, "name", None) == task_name:
            return existing
    return None


def sync_run(task_cls: Any | None, run_dir: Path, project_name: str, dry_run: bool) -> None:
    run_id = run_dir.name
    print(f"[{run_id}] {run_dir}")

    log_path = run_dir / "trigger_api.log"
    decisions: dict[str, dict] = {}
    for key, rel in DECISION_FILES:
        decision = load_json(run_dir / rel)
        if decision is not None:
            decisions[key] = decision

    if dry_run:
        print(f"  [dry-run] sẽ tạo/cập nhật ClearML task project={project_name!r} task_name={run_id!r}")
        print(f"  [dry-run] trigger_api.log: {'có' if log_path.is_file() else 'không có'}")
        if not decisions:
            print("  [dry-run] chưa có finetune-decision.json nào (production hoặc candidate)")
        for key, decision in decisions.items():
            print(f"  [dry-run] {key}: decision={decision.get('decision')} "
                  f"targets={decision.get('finetune_targets')}")
        return

    assert task_cls is not None
    existing = find_existing_task(task_cls, project_name, run_id)
    if existing is None:
        task = task_cls.create(
            project_name=project_name,
            task_name=run_id,
            task_type=task_cls.TaskTypes.data_processing,
            add_task_init_call=False,
        )
        print(f"  tạo mới ClearML task {task.id}")
    else:
        task = existing
        print(f"  dùng lại ClearML task có sẵn {task.id}")

    try:
        task.mark_started(force=True)
    except Exception as exc:  # noqa: BLE001 — best-effort chuyển trạng thái, không chặn sync dữ liệu
        print(f"  [warn] mark_started: {exc}", file=sys.stderr)

    task.set_comment(
        f"Đồng bộ tự động bởi tools/sync_runs_to_clearml.py từ {run_dir} — "
        "chỉ đọc runs/, không chạy pipeline, không gọi trigger_api."
    )

    if log_path.is_file():
        task.upload_artifact(name="trigger_api_log", artifact_object=str(log_path))
        print("  đã upload trigger_api.log")
    else:
        print("  (chưa có trigger_api.log — bỏ qua)")

    for key, decision in decisions.items():
        task.upload_artifact(name=f"{key}__finetune_decision_json", artifact_object=decision)
        rows = decision_table_rows(decision)
        if len(rows) > 1:
            task.get_logger().report_table(
                title=f"finetune-decision ({key})", series="checks", iteration=0, table_plot=rows,
            )
        is_required = 1.0 if decision.get("decision") == "FINETUNE_REQUIRED" else 0.0
        task.get_logger().report_single_value(f"{key}_finetune_required", is_required)
        print(f"  đã upload decision {key}: {decision.get('decision')}")

    try:
        task.mark_completed(force=True, status_message="synced by sync_runs_to_clearml.py")
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] mark_completed: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR),
                         help=f"Mặc định: {DEFAULT_RUNS_DIR}")
    parser.add_argument("--project-name", default=DEFAULT_PROJECT_NAME,
                         help=f"Mặc định: {DEFAULT_PROJECT_NAME}")
    parser.add_argument("--run-id", default=None,
                         help="Chỉ đồng bộ 1 run_id cụ thể thay vì quét toàn bộ runs/.")
    parser.add_argument("--dry-run", action="store_true",
                         help="Chỉ in ra sẽ đồng bộ gì, KHÔNG gọi ClearML (không cần cài package clearml).")
    args = parser.parse_args(argv)

    runs_dir = Path(args.runs_dir).resolve()

    if args.run_id:
        run_dir = runs_dir / args.run_id
        if not run_dir.is_dir():
            print(f"không thấy run_id={args.run_id!r} tại {run_dir}", file=sys.stderr)
            return 2
        run_dirs = [run_dir]
    else:
        run_dirs = list(iter_run_dirs(runs_dir))

    if not run_dirs:
        print(f"không có run nào trong {runs_dir}")
        return 0

    task_cls = None
    if not args.dry_run:
        try:
            from clearml import Task  # type: ignore
        except ImportError:
            print(
                "thiếu package 'clearml' trong môi trường python hiện tại — "
                "`pip install clearml`, hoặc chạy lại với --dry-run để xem trước không cần cài.",
                file=sys.stderr,
            )
            return 1
        task_cls = Task

    for run_dir in run_dirs:
        sync_run(task_cls, run_dir, args.project_name, args.dry_run)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
