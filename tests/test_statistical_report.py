from __future__ import annotations

import pytest
from secai_gateway.api.messages import merge_detail_table, statistical_detail_table
from secai_gateway.api.schemas import LegacyRequest


def test_statistical_detail_table_from_list_data() -> None:
    payload = {
        "from_date": "2026-09-10T07:00:00",
        "to_date": "2026-10-10T07:00:00",
        "summary": "Tổng số mục tiêu: 327",
        "detail": "CVE-2011-4130 – Xuất hiện 1 lần;",
        "scan_type": 0,
        "listData": [
            {
                "modified": "2026-09-08T23:33:57",
                "date": "2026-09-08T00:00:00",
                "cve": "",
                "description": "",
                "risk": 1,
                "risk_number": 114,
            },
            {
                "modified": "2026-09-07T18:55:24",
                "date": "2026-09-07T00:00:00",
                "cve": "CVE-2011-4130",
                "description": "Use-after-free vulnerability in the Response API in ProFTPD.",
                "risk": 4,
                "risk_number": 2,
            },
            {
                "modified": "2026-09-07T18:55:21",
                "date": "2026-09-07T00:00:00",
                "cve": "CVE-2020-9272",
                "description": "ProFTPD 1.3.7 has an out-of-bounds (OOB) read vulnerability.",
                "risk": 3,
                "risk_number": 2,
            },
        ],
    }
    req = LegacyRequest(**payload)
    table = statistical_detail_table(req)

    assert table is not None
    assert "| Ngày quét | Lỗ hổng | Mức độ | Mô tả |" in table
    assert "| Tên máy | IP |" not in table
    assert "CVE-2011-4130" in table
    assert "Nghiêm\u00a0trọng" in table
    assert "Use-after-free vulnerability" in table
    assert "CVE-2020-9272" in table
    assert "Cao" in table
    assert "2026-09-07" in table


def test_statistical_detail_table_preserves_nested_machines() -> None:
    payload = {
        "detail": [
            {
                "name": "srv-db",
                "ip": "10.0.0.1",
                "scan_date": "2026-10-01",
                "cves": [
                    {
                        "cve": "CVE-2024-1234",
                        "severity": "Critical",
                        "description": "RCE vulnerability",
                    }
                ],
            }
        ]
    }
    req = LegacyRequest(**payload)
    table = statistical_detail_table(req)

    assert table is not None
    assert "srv-db" in table
    assert "10.0.0.1" in table
    assert "CVE-2024-1234" in table
    assert "Nghiêm\u00a0trọng" in table
    assert "RCE vulnerability" in table


def test_merge_detail_table_preserves_recommendations_and_conclusion() -> None:
    model_detail = """### Phân tích chi tiết các lỗ hổng

#### CVE-2011-4130
- Chi tiết lỗ hổng ProFTPD

### Khuyến nghị
1. Cập nhật phần mềm.
2. Thiết lập tường lửa.

### Kết luận
Cần ưu tiên xử lý các lỗ hổng nghiêm trọng.
"""
    table = "| Ngày quét | Lỗ hổng | Mức độ | Mô tả |\n|---|---|---|---|\n| 2026-09-07 | CVE-2011-4130 | Cao | Desc |\n"

    merged = merge_detail_table(model_detail, table)

    assert "| Ngày quét | Lỗ hổng | Mức độ | Mô tả |" in merged
    assert "CVE-2011-4130" in merged
    assert "### Khuyến nghị" in merged
    assert "1. Cập nhật phần mềm." in merged
    assert "### Kết luận" in merged
    assert "Cần ưu tiên xử lý" in merged
    assert "#### CVE-2011-4130\n- Chi tiết" not in merged
