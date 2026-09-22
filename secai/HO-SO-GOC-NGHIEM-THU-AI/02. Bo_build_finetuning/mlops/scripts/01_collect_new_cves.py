#!/usr/bin/env python3
"""Giai đoạn 1 — diff watermark: liệt kê CVE đã vào KB (Qdrant) kể từ lần finetune trước.

Không thu thập CVE (việc đó do phân hệ Thu thập dữ liệu — sync_autoscan_to_kb.py — làm hàng
ngày, xem mlops/README.md). Script này chỉ ĐỌC Qdrant, scroll payload `updated_at` (field có
thật trên mọi điểm, ghi bởi sync_autoscan_to_kb.py) lớn hơn watermark đã lưu ở lần finetune
gần nhất (state/last_finetune.json), rồi xuất CSV 1 cột `cve_id` — input trực tiếp cho
`gen_testset.py --source customer` (xem 02_build_eval_set.sh).

Watermark theo dõi riêng cho từng component (embedding/reranker/llm) vì mỗi component có thể
được finetune/duyệt baseline vào thời điểm khác nhau; script này lấy watermark CŨ NHẤT trong 3
component (thận trọng: nếu 1 component đã lâu chưa duyệt, vẫn coi CVE từ mốc đó là "mới" để
không bỏ sót — bù lại có thể trùng lặp với đợt trước của component khác, việc `gen_testset.py
--exclude-pool` ở bước sau sẽ tự loại các CVE đã dùng ở benchmark cũ).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import common

PAGE_SIZE = 256
MAX_PAGES = 200  # trần an toàn — 200*256=51.200 CVE/lần chạy là quá đủ cho 1 chu kỳ

# THỰC TẾ ĐÃ KIỂM TRA (2026-09-18, live cve_kb_hybrid, 744k điểm): field `updated_at` CHỈ tồn
# tại trên điểm có `source="autoscan-api-sync"` (~14.7k/744k) — toàn bộ dữ liệu nạp trước đó
# (bulk `hf:DuyTa/cve-kgrag-db` 654k, `nvd-cron-sync` 44k, `opencve-backfill` 30k) KHÔNG có
# field này. Vì vậy watermark rỗng KHÔNG được coi là "toàn bộ KB là mới" (744k điểm không phải
# một mẫu có ý nghĩa) — mặc định thành "bây giờ", chỉ tính CVE autoscan nạp SAU thời điểm cài
# đặt pipeline này trở đi, đúng tinh thần "CVE mới cập nhật vào KB theo tần suất hàng ngày".
#
# Cũng đã kiểm tra: Qdrant range-filter TRÊN CHÍNH SERVER hoạt động đúng với chuỗi RFC3339
# (`{"range": {"gt": "2026-09-01T00:00:00Z"}}` trả đúng tập con) — nên lọc thẳng ở filter, KHÔNG
# scroll-rồi-lọc-bằng-Python: 744k điểm với match rải ngẫu nhiên theo ID (không theo thời gian)
# khiến scroll tuần tự không bao giờ quét hết trong một số trang hợp lý.
AUTOSCAN_SOURCE = "autoscan-api-sync"


def oldest_watermark(state: dict) -> str:
    values = [c.get("watermark") or "" for c in state["components"].values()]
    non_empty = [v for v in values if v]
    if non_empty:
        return min(non_empty)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    print(f"[01_collect_new_cves] chưa có watermark nào trong state — coi 'bây giờ' ({now}) là mốc "
          "khởi động, KHÔNG lùi về toàn bộ lịch sử KB (xem ghi chú ở đầu file)")
    return now


def scroll_new_cves(config: dict, watermark: str, log=print) -> list[str]:
    """Scroll CHỈ tập con điểm autoscan có `updated_at > watermark`, lọc ở Qdrant filter (server
    side) — không quét toàn collection rồi lọc bằng Python (744k điểm, match rải ngẫu nhiên theo
    ID chứ không theo thời gian, nên client-side filter không quét hết trong số trang hợp lý)."""
    qdrant_filter = {"must": [
        {"key": "source", "match": {"value": AUTOSCAN_SOURCE}},
        {"key": "updated_at", "range": {"gt": watermark}},
    ]}
    seen: dict[str, str] = {}  # cve_id -> max updated_at đã thấy (dedup 2 chunk/CVE)
    offset = None
    for page in range(MAX_PAGES):
        body = {"limit": PAGE_SIZE, "filter": qdrant_filter,
                "with_payload": ["cve_id", "updated_at"], "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        result = common.qdrant_post(config, "/points/scroll", body)
        points = result.get("result", {}).get("points", [])
        if not points:
            break
        for point in points:
            payload = point.get("payload") or {}
            cve_id = payload.get("cve_id")
            updated_at = str(payload.get("updated_at") or "")
            if not cve_id:
                continue
            seen[cve_id] = max(seen.get(cve_id, ""), updated_at)
        offset = result.get("result", {}).get("next_page_offset")
        if offset is None:
            break
        if page and page % 20 == 0:
            log(f"[01_collect_new_cves] scanned {page * PAGE_SIZE} matched points, {len(seen)} CVE so far...")
    return sorted(seen)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True, help="CSV output (1 cột cve_id)")
    parser.add_argument("--watermark", help="Ghi đè watermark thay vì đọc state/last_finetune.json (test/debug)")
    args = parser.parse_args()

    config = common.load_config()
    state = common.load_state()
    watermark = args.watermark if args.watermark is not None else oldest_watermark(state)
    print(f"[01_collect_new_cves] watermark = {watermark or '(rỗng — CHỈ dùng khi test tay bằng --watermark \"\")'}")

    cve_ids = scroll_new_cves(config, watermark)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["cve_id"])
        for cve_id in cve_ids:
            writer.writerow([cve_id])

    print(f"[01_collect_new_cves] {len(cve_ids)} CVE mới kể từ watermark -> {args.out}")
    # exit code 2 = "không đủ CVE mới" — run_pipeline.sh dùng để dừng sớm, không phải lỗi
    threshold = config.get("min_new_cves_to_run", 1)
    if len(cve_ids) < threshold:
        print(f"[01_collect_new_cves] {len(cve_ids)} < min_new_cves_to_run={threshold} — bỏ qua chu kỳ này")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
