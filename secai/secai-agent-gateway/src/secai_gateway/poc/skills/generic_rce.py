"""Skill fallback: RCE tổng quát (kể cả Spring Boot DevTools). vuln_class = rce / unknown."""
from __future__ import annotations

from .base import Skill

SKILL = Skill(
    vuln_class="rce",
    context=(
        "RCE tổng quát khi chưa có playbook riêng. Quy trình pentester: (1) recon banner/version qua "
        "http_request GET /; (2) enum endpoint/tính năng lộ (actuator, admin, upload); "
        "(3) chọn vector RCE phù hợp rồi chạy `id`, đọc `uid=`. Vector hay gặp:\n"
        "- Actuator exec: GET /actuator xem listing → nếu có 'exec' link → GET /actuator/exec → uid=.\n"
        "- Actuator env+SpEL: /actuator/env → chỉnh property → /actuator/refresh → RCE.\n"
        "- Deserialization/upload: gửi object/tệp độc → thực thi.\n"
        "LƯU Ý: Luôn đọc /actuator trước để thấy danh sách endpoint thực tế. "
        "Nếu listing có 'exec', truy cập ngay /actuator/exec. "
        "Khi cần chạy công cụ tuỳ ý (msfvenom, python exploit script) dùng run_cmd (đã gated)."
    ),
    suggested_sequence=[
        "http_request GET / để lấy banner/version",
        "http_request GET /actuator — đọc KỸ listing, nếu có link 'exec' → truy cập ngay /actuator/exec",
        "http_request GET /actuator/exec (không có param — endpoint tự chạy id)",
        "searchsploit <tên sản phẩm> để tra PoC public nếu không tìm được endpoint trực tiếp",
    ],
    payload_templates=[
        "# Actuator exec (không param, tự chạy id): GET /actuator/exec",
        "# SpEL: POST /actuator/env body=property+SpEL, sau đó /actuator/refresh",
    ],
    oracle_hint="response/stdout chứa NONCE tự-inject hoặc `uid=<n>(...)` từ lệnh id.",
)
