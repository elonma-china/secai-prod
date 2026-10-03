"""Private, bounded Qwen 2B OCR candidates on the existing Vast GPU."""
from __future__ import annotations

import base64
import io
import math
import os
import threading
from typing import Literal

import torch
from fastapi import FastAPI, Header, HTTPException
from PIL import Image
from pydantic import BaseModel, Field
from transformers import AutoModelForMultimodalLM, AutoProcessor

from server import _free_mib, _guard, _owned_mib, MAX_PEAK_MIB

REVISIONS = {
    "Qwen/Qwen3.5-2B": "15852e8c16360a2fea060d615a32b45270f8a8fc",
    "Qwen/Qwen3-VL-2B-Instruct": "89644892e4d85e24eaac8bacfd4f463576704203",
}
MODEL_ID = os.environ.get("OCR_MODEL_ID", "Qwen/Qwen3.5-2B")
if MODEL_ID not in REVISIONS:
    raise RuntimeError("unsupported OCR model ID")
MODEL_REVISION = REVISIONS[MODEL_ID]
PROMPTS = {
    "natural": "Transcribe all visible text in natural reading order. Return only the text; preserve CVE identifiers and IP addresses exactly.",
    "arabic": "Read the Arabic text from right to left. Output the original Unicode text in logical order. Keep each CVE and IP literal exactly as printed. Output only the transcription.",
    "verbatim": "Copy the text in this image verbatim, without translation or paraphrase. Use normal Arabic reading order. Preserve all numbers, dots and hyphens exactly.",
    "universal": "Transcribe every visible line exactly in its script's logical reading order: Arabic reads right to left, while Latin, CJK and Cyrillic read left to right. Preserve the exact order and spelling of every CVE identifier and IP address. Do not translate or paraphrase. Return only the transcription.",
    "conditional": "Transcribe the visible text in its original language. If it contains Arabic, read the Arabic text from right to left and output the original Unicode text in logical order, keeping every embedded CVE and IP literal exactly as printed. Otherwise, read the text in its natural order. Do not translate or paraphrase. Output only the transcription.",
}
_lock = threading.Lock()
_model = None
_processor = None
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class Request(BaseModel):
    model: str
    image_base64: str = Field(max_length=15_000_000)
    calibration_prompt: Literal["natural", "arabic", "verbatim", "universal", "conditional"] = "conditional"


def _load() -> None:
    global _model, _processor
    if _model is not None:
        return
    _guard()
    path = os.environ["OCR_MODEL_PATH"]
    _processor = AutoProcessor.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    _model = AutoModelForMultimodalLM.from_pretrained(
        path, local_files_only=True, trust_remote_code=False, torch_dtype=torch.bfloat16,
    ).to("cuda").eval()
    if _owned_mib() > MAX_PEAK_MIB or _free_mib() < 2048:
        _model = None
        torch.cuda.empty_cache()
        raise HTTPException(503, "OCR GPU quota exceeded")


def _bounded_image(raw: bytes) -> Image.Image:
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError("image limit")
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    if image.width * image.height > 20_000_000 or max(image.size) > 10_000:
        raise ValueError("pixel limit")
    ratio = min(1.0, 1536 / max(image.size), math.sqrt(1_000_000 / (image.width * image.height)))
    if ratio < 1:
        image = image.resize((max(1, int(image.width * ratio)),
                              max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)
    return image


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
            image = _bounded_image(base64.b64decode(request.image_base64, validate=True))
        except Exception as exc:
            raise HTTPException(422, "Invalid image") from exc
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": PROMPTS[request.calibration_prompt]}]}]
        encoded = _processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True, return_dict=True,
            return_tensors="pt", processor_kwargs={"images_kwargs": {"size": {
                "shortest_edge": 65_536, "longest_edge": 1_000_000}}},
        ).to(_model.device)
        with torch.inference_mode():
            output = _model.generate(**encoded, max_new_tokens=256, do_sample=False)
        if _owned_mib() > MAX_PEAK_MIB or _free_mib() < 2048:
            raise HTTPException(503, "OCR GPU quota exceeded")
        text = _processor.decode(output[0][encoded["input_ids"].shape[-1]:],
                                 skip_special_tokens=True).strip()
        blocks = [] if not text else [{"text": text, "bbox": None, "confidence": None}]
        return {"model": MODEL_ID, "blocks": blocks}
    finally:
        _lock.release()
