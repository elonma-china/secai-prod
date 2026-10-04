"""Single-request GPU OCR process; supervised and disposable."""
from __future__ import annotations

import json
import os
import sys
import threading
import time

from fastapi import HTTPException

from qwen_server_real_pages_bounded import MODEL_ID, Request, _infer


def main() -> None:
    try:
        request = Request.model_validate_json(sys.stdin.buffer.read(15_100_000))
        if request.model != MODEL_ID or request.calibration_prompt != "conditional":
            raise HTTPException(422, "Unsupported OCR request")
        result = _infer(request, os.environ.get("OCR_REQUEST_ID", "supervised"),
                        time.monotonic(), threading.Event())
        response = {"status": 200, "body": result}
    except HTTPException as exc:
        response = {"status": exc.status_code, "detail": str(exc.detail)}
    except Exception:
        response = {"status": 503, "detail": "OCR worker unavailable"}
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
