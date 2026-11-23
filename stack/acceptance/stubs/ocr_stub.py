#!/usr/bin/env python3
"""CHỈ ĐỂ TEST. Stub OCR (POST /v1/ocr) theo hợp đồng translation/ocr_client.py: body {model, image_base64},
trả {"model", "blocks": [{"text", "bbox", "confidence"}]}. Luôn trả CỐ ĐỊNH chữ của fixtures/sample-en.png
(acceptance/make_fixtures.py), không đọc ảnh: chứng minh đường ống, KHÔNG chứng minh chất lượng OCR.
  STUB_API_KEYS, STUB_MODEL (= TRANSLATE_V2_OCR_MODEL), STUB_PORT (mặc định 18083)
"""
import base64
import binascii
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # chạy được cả với python -I (không tự thêm thư mục script)
from specialist_stub import serve  # noqa: E402

LINES = [  # đúng make_fixtures.py; bbox theo bố cục ảnh 1250x330
    "Security Advisory",
    "A remote code execution vulnerability in Apache Log4j2",
    "allows an unauthenticated attacker to run arbitrary code",
    "on the server. Upgrade to version 2.17.1 or later.",
]


def main():
    model = os.environ.get("STUB_MODEL") or sys.exit("stub: STUB_MODEL rỗng")

    def handle(path, b):
        if path != "/v1/ocr" or set(b) != {"model", "image_base64"} or b["model"] != model:
            return None
        try:
            if not base64.b64decode(b["image_base64"], validate=True):
                return None
        except (binascii.Error, TypeError):
            return None
        return {"model": model, "blocks": [
            {"text": t, "bbox": [30, 25 + i * 75, 1220, 85 + i * 75], "confidence": 0.99} for i, t in enumerate(LINES)]}

    serve(int(os.environ.get("STUB_PORT", "18083")), model, handle)


if __name__ == "__main__":
    main()
