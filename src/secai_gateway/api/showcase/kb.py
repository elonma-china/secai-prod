"""Nhóm KB — `GET /showcase-api/kb/sync-log`: log cron cập nhật tri thức KB (CVE/0-day) hằng ngày.

Chứng minh tiêu chí "quy trình cập nhật tri thức được thiết lập, chạy đều đặn" (sheet dòng
537/564/70) — vốn chỉ chứng minh được bằng code/cron. Đọc log THẬT của `run_sync.sh`
(settings.showcase_kb_sync_log), parse từng lần chạy. Chỉ đọc, không cho sửa lịch qua UI.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Query

from ...config import get_settings

router = APIRouter(prefix="/showcase-api/kb", tags=["showcase-kb"])

_START = re.compile(r"Starting sync at (.+)")
_END = re.compile(r"(?:sync exit=(\d+)|Sync completed successfully) at (.+)")


@router.get("/sync-log")
def sync_log(limit: int = Query(14, ge=1, le=100)) -> dict:
    s = get_settings()
    path = Path(s.showcase_kb_sync_log)
    schedule = {"cron": s.showcase_kb_cron, "frequency": "Hằng ngày lúc 03:00",
                "editable_via_ui": False,
                "note": "Lịch cố định trong cron; sửa bằng file cấu hình khi khởi động, không qua giao diện."}
    if not path.is_file():
        return {"schedule": schedule, "runs": [], "log_tail": [], "source": s.showcase_kb_sync_log}

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    runs = []
    cur = None
    for ln in lines:
        ms = _START.search(ln)
        if ms:
            cur = {"started": ms.group(1).strip(), "ended": None, "exit": None}
            continue
        me = _END.search(ln)
        if me and cur is not None:
            cur["exit"] = int(me.group(1)) if me.group(1) is not None else 0
            cur["ended"] = me.group(2).strip()
            cur["status"] = "ok" if cur["exit"] == 0 else "fail"
            runs.append(cur)
            cur = None
    runs = runs[::-1][:limit]  # mới nhất trước
    return {"schedule": schedule, "runs": runs, "log_tail": lines[-40:],
            "source": s.showcase_kb_sync_log}
