#!/usr/bin/env python3
"""CHỈ ĐỂ TEST. Stub dịch vụ Hy-MT2 (POST /v1/translate) cho nghiệm thu UI-TR-2/TR-3 khi không có model thật.

Chứng minh ĐƯỜNG ỐNG (tunnel, convert, job, kết quả, tiếng Việt trong đầu ra), KHÔNG chứng minh chất lượng dịch:
EN->VI chỉ thay một bảng từ cố định và thêm tiền tố "[stub] "; chiều khác trả nguyên văn kèm tiền tố.
Hợp đồng y như translation/specialist.py và multilang/provider.py: Bearer, body {text, source_lang, target_lang},
trả đúng {"translated_text", "model"}. Chỉ stdlib.
  STUB_API_KEYS  khoá Bearer chấp nhận, phân cách dấu phẩy (bắt buộc; rỗng thì không chạy)
  STUB_MODEL     tên model trả về (phải bằng TRANSLATE_V2_SPECIALIST_MODEL và MULTILANG_SPECIALIST_MODEL)
  STUB_PORT      cổng (mặc định 18868)
"""
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORDS = [  # dài trước ngắn; không đụng token kỹ thuật (Apache Log4j2, CVE-..., phiên bản, ⟦T…⟧)
    ("security advisory", "khuyến cáo bảo mật"), ("remote code execution", "thực thi mã từ xa"),
    ("affected versions", "các phiên bản bị ảnh hưởng"), ("arbitrary code", "mã tùy ý"),
    ("vulnerability", "lỗ hổng"), ("unauthenticated", "chưa xác thực"), ("attacker", "kẻ tấn công"),
    ("server", "máy chủ"), ("allows", "cho phép"), ("upgrade", "nâng cấp"), ("mitigation", "biện pháp giảm thiểu"),
    ("detection", "phát hiện"), ("or later", "hoặc mới hơn"), ("in", "trong"), ("to", "để"),
]
_RX = re.compile(r"\b(" + "|".join(re.escape(e) for e, _ in WORDS) + r")\b", re.I)
_MAP = {e: v for e, v in WORDS}


def translate(text, source, target):
    if source == "en" and target == "vi":
        text = _RX.sub(lambda m: _MAP[m.group(0).lower()], text)
    return "[stub] " + text


def keys():
    ks = {k.strip() for k in os.environ.get("STUB_API_KEYS", "").split(",") if k.strip()}
    if not ks:
        sys.exit("stub: STUB_API_KEYS rỗng; không chạy stub không xác thực")
    return ks


def serve(port, model, handle):
    """handle(body: dict) -> dict | None (None = 400). Bearer bắt buộc; GET /health không cần khoá."""
    allowed = keys()

    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            raw = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self._send(200, {"status": "ok", "stub": True, "model": model}) if self.path == "/health" else self._send(404, {})

        def do_POST(self):
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth[7:] not in allowed:
                return self._send(401, {"error": "unauthorized"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                out = handle(self.path, body) if isinstance(body, dict) else None
            except ValueError:
                out = None
            self._send(200, out) if out is not None else self._send(400, {"error": "bad request"})

        def log_message(self, fmt, *a):
            sys.stderr.write("stub %s\n" % (fmt % a))

    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


def main():
    model = os.environ.get("STUB_MODEL") or sys.exit("stub: STUB_MODEL rỗng")

    def handle(path, b):
        if path != "/v1/translate" or set(b) != {"text", "source_lang", "target_lang"} or \
                not isinstance(b["text"], str) or not b["text"]:
            return None
        return {"translated_text": translate(b["text"], b["source_lang"], b["target_lang"]), "model": model}

    serve(int(os.environ.get("STUB_PORT", "18868")), model, handle)


if __name__ == "__main__":
    main()
