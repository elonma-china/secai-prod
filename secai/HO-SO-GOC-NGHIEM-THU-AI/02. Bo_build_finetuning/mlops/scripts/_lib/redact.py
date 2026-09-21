#!/usr/bin/env python3
"""Lọc stdin -> stdout, che secret trước khi log giai đoạn được ghi xuống đĩa (runs/<id>/logs/
<NN>.log) và stream ra trigger_api.log. Lý do: bước 06b/07 gọi serving-up-cyber-f1.sh in ra dòng
"API KEY : <key serving>" (và có thể Bearer/hf_/sk- token) — không nên tồn tại plaintext trên đĩa
dù endpoint đọc log đã gán key. Che ở tầng ghi log, KHÔNG đụng giá trị thật mà pipeline dùng
(08_rent parse key từ mktemp riêng, không phải từ file log này).

Chạy: <cmd> 2>&1 | redact.py | tee logs/NN.log
"""
from __future__ import annotations

import re
import sys

_PATTERNS = [
    # "API KEY : xxx" / "api_key=xxx" / '"api_key": "xxx"'
    (re.compile(r'(?i)(api[_ ]?key\s*["\']?\s*[:=]\s*["\']?)([^\s"\',}]+)'), r"\1***REDACTED***"),
    (re.compile(r'(?i)(bearer\s+)(\S+)'), r"\1***REDACTED***"),
    (re.compile(r'\bsk-[A-Za-z0-9_\-]{6,}'), "sk-***REDACTED***"),
    (re.compile(r'\bhf_[A-Za-z0-9]{16,}'), "hf_***REDACTED***"),
]


def redact(line: str) -> str:
    for pat, repl in _PATTERNS:
        line = pat.sub(repl, line)
    return line


def main() -> None:
    for line in sys.stdin:
        sys.stdout.write(redact(line))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
