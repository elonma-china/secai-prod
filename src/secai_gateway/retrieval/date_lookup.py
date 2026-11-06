"""Bounded, deterministic retrieval for CVEs updated on a calendar day."""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from . import qdrant
from .filters import ENTITY_ID_RES, upper_list

_VN = ZoneInfo("Asia/Ho_Chi_Minh")
_DATE = re.compile(r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4})\b")


def _updated_day(prompt: str, now: datetime | None = None) -> date | None:
    if not re.search(r"\bCVEs?\b|lỗ\s+hổng", prompt, re.I):
        return None
    if not re.search(r"cập\s*nhật|updated?", prompt, re.I):
        return None
    matches = _DATE.findall(prompt)
    if len(matches) == 1:
        parts = re.split(r"[/.-]", matches[0])
        y, m, d = parts if len(parts[0]) == 4 else parts[::-1]
        return date(int(y), int(m), int(d))
    if matches:
        return None  # A date range needs a separate query; never silently select one end.
    today = (now or datetime.now(_VN)).astimezone(_VN).date()
    short = re.search(r"ngày\s+(\d{1,2})/(\d{1,2})(?![/\d])", prompt, re.I)
    if short:
        return date(today.year, int(short[2]), int(short[1]))
    if re.search(r"hôm\s*qua|yesterday", prompt, re.I):
        return today - timedelta(days=1)
    if re.search(r"hôm\s*nay|today", prompt, re.I):
        return today
    return None


async def _date_grounding(prompt: str) -> dict[str, Any] | None:
    started = time.monotonic()
    try:
        day = _updated_day(prompt)
    except ValueError:
        return {"query": prompt, "result": "Ngày cập nhật không hợp lệ. Yêu cầu người dùng kiểm tra ngày/tháng/năm.",
                "summary": "qdrant_search [updated-date: invalid date]", "duration_ms": 0}
    if day is None:
        return None
    start = datetime.combine(day, datetime.min.time(), _VN).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    qfilter: dict[str, Any] = {"must": [{"key": "updated_at", "range": {
        "gte": start.isoformat().replace("+00:00", "Z"),
        "lt": end.isoformat().replace("+00:00", "Z"),
    }}]}
    ids = upper_list(ENTITY_ID_RES["cve_id"].findall(prompt))
    if ids:
        qfilter["must"].append({"key": "cve_id", "match": {"any": ids}})
    requested = re.search(r"(?:liệt\s*kê|list|cho|lấy)\s+(\d+)\s+CVEs?", prompt, re.I)
    limit = min(max(int(requested[1]), 1), 20) if requested else 5
    rows: dict[str, dict[str, Any]] = {}
    offset = None
    exhausted = False
    async with httpx.AsyncClient(timeout=20) as client:
        # Bound work and memory even when duplicate chunks dominate the date interval.
        for _ in range(20):
            body: dict[str, Any] = {"filter": qfilter, "limit": 100,
                                    "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            response = await client.post(f"{qdrant.QDRANT_URL}/collections/{qdrant.COLLECTION}/points/scroll", json=body)
            response.raise_for_status()
            result = response.json()["result"]
            for point in result["points"]:
                payload = point.get("payload") or {}
                cve = payload.get("cve_id")
                if cve and cve not in rows:
                    rows[cve] = payload
                if len(rows) >= limit:
                    break
            offset = result.get("next_page_offset")
            exhausted = offset is None
            if len(rows) >= limit or exhausted:
                break
    heading = (f"CVE có updated_at trong ngày {day:%d/%m/%Y}, múi giờ Asia/Ho_Chi_Minh (UTC+7). "
               "Đây là ngày cập nhật bản ghi, không phải ngày công bố hay thời điểm đồng bộ. "
               "Tất cả bản ghi bên dưới khớp ngày theo UTC+7; trình bày thời gian cập nhật đã chuyển sang UTC+7, "
               "không so sánh phần ngày của timestamp UTC với ngày địa phương. "
               "Danh sách mẫu, không phải tổng số hoặc xếp hạng mới nhất. Chỉ trả lời từ các bản ghi bên dưới.")
    hits = []
    for i, payload in enumerate(rows.values()):
        updated = datetime.fromisoformat(str(payload['updated_at']).replace('Z', '+00:00'))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        hits.append(qdrant._format_hit(i, 1.0, payload, None)
                    + f"\n**Thời gian cập nhật (UTC+7):** {updated.astimezone(_VN).isoformat()}"
                    + f"\n**updated_at (UTC):** {payload.get('updated_at')}"
                    + f"\n**Source:** {payload.get('source') or 'KB nội bộ'}")
    if not hits:
        hits = ["Không tìm thấy CVE khớp ngày cập nhật trong KB nội bộ." if exhausted
                else "Đã đạt giới hạn tra cứu; chưa tìm thấy CVE trong phần dữ liệu đã đọc. Không suy ra KB không có dữ liệu."]
    return {"query": prompt, "result": heading + "\n\n" + "\n\n---\n\n".join(hits),
            "summary": f"qdrant_search({day.isoformat()}, UTC+7) {len(rows)} CVEs [updated-date]",
            "duration_ms": round((time.monotonic() - started) * 1000)}
