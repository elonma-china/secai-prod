"""Private, single-process OCR service for a pinned local PaddleOCR-VL snapshot."""
from __future__ import annotations

import base64
import io
import os
import threading

import torch
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoProcessor

MODEL_ID = "PaddlePaddle/PaddleOCR-VL-1.6"
MODEL_REVISION = "c5630abae1d940eafe0697512a0325494b02ab42"
MIN_FREE_MIB = 9216
MAX_PEAK_MIB = 7168
_lock = threading.Lock()
_model = None
_processor = None
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class Request(BaseModel):
    model: str
    image_base64: str = Field(max_length=15_000_000)


def _free_mib() -> int:
    return torch.cuda.mem_get_info()[0] // (1024 * 1024)


def _guard() -> None:
    if not torch.cuda.is_available() or _free_mib() < MIN_FREE_MIB:
        raise HTTPException(503, "GPU reserve unavailable")


def _load() -> None:
    global _model, _processor
    if _model is not None:
        return
    _guard()
    path = os.environ["OCR_MODEL_PATH"]
    _processor = AutoProcessor.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    _model = AutoModelForImageTextToText.from_pretrained(
        path, local_files_only=True, trust_remote_code=False, torch_dtype=torch.bfloat16,
    ).to("cuda").eval()
    if torch.cuda.max_memory_reserved() // (1024 * 1024) > MAX_PEAK_MIB:
        _model = None
        torch.cuda.empty_cache()
        raise HTTPException(503, "OCR GPU quota exceeded")


@app.get("/health")
def health():
    return {"ready": _model is not None, "model": MODEL_ID, "revision": MODEL_REVISION}


@app.post("/v1/ocr")
def ocr(request: Request, authorization: str = Header(default="")):
    token = os.environ.get("OCR_SERVICE_TOKEN", "")
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(401, "Unauthorized")
    if request.model != MODEL_ID:
        raise HTTPException(422, "Unsupported model")
    if not _lock.acquire(blocking=False):
        raise HTTPException(503, "OCR busy")
    try:
        _guard()
        _load()
        try:
            raw = base64.b64decode(request.image_base64, validate=True)
            if len(raw) > 10 * 1024 * 1024:
                raise ValueError("image limit")
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            if image.width * image.height > 20_000_000 or max(image.size) > 10_000:
                raise ValueError("pixel limit")
        except Exception as exc:
            raise HTTPException(422, "Invalid image") from exc
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image}, {"type": "text", "text": "OCR:"}]}]
        encoded = _processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True, return_dict=True,
            return_tensors="pt", images_kwargs={"size": {
                "shortest_edge": _processor.image_processor.min_pixels,
                "longest_edge": 1280 * 28 * 28}},
        ).to(_model.device)
        torch.cuda.reset_peak_memory_stats()
        with torch.inference_mode():
            output = _model.generate(**encoded, max_new_tokens=1024, do_sample=False)
        peak = torch.cuda.max_memory_reserved() // (1024 * 1024)
        if peak > MAX_PEAK_MIB:
            raise HTTPException(503, "OCR GPU quota exceeded")
        text = _processor.decode(output[0][encoded["input_ids"].shape[-1]:-1],
                                 skip_special_tokens=True)
        # The model does not provide reliable region boxes or confidence for this task.
        blocks = [] if not text.strip() else [{"text": text, "bbox": None, "confidence": None}]
        return {"model": MODEL_ID, "blocks": blocks}
    finally:
        _lock.release()
