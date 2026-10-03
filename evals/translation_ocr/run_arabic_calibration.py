"""Run bounded prompt calibration on separate Arabic fixtures through private OCR."""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.request
from pathlib import Path


def main() -> None:
    manifest = Path(sys.argv[1])
    token_file = Path(sys.argv[2])
    output = Path(sys.argv[3])
    corpus = json.loads(manifest.read_text())
    token = token_file.read_text().strip()
    rows = []
    for prompt in ("standard", "logical", "arabic"):
        for item in corpus["images"]:
            image = (manifest.parent / item["image"]).read_bytes()
            body = json.dumps({"model": "PaddlePaddle/PaddleOCR-VL-1.6",
                               "image_base64": base64.b64encode(image).decode(),
                               "calibration_prompt": prompt}).encode()
            request = urllib.request.Request(
                "http://127.0.0.1:18083/v1/ocr", body,
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            started = time.monotonic()
            with urllib.request.urlopen(request, timeout=90) as response:
                data = json.load(response)
            text = "\n".join(block["text"] for block in data["blocks"])
            rows.append({"id": item["id"], "prompt": prompt,
                         "reference": item["text"], "raw_text": text,
                         "latency_seconds": round(time.monotonic() - started, 3)})
            print(prompt, item["id"], repr(text), flush=True)
    output.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")


if __name__ == "__main__":
    main()
