"""Private, bounded Qwen 2B OCR candidates on the existing Vast GPU."""
from __future__ import annotations

import base64
import asyncio
import json
import io
import math
import os
from pathlib import Path
import threading
import time
from typing import Literal

import torch
from fastapi import FastAPI, Header, HTTPException, Request as FastAPIRequest
from PIL import Image
from pydantic import BaseModel, Field
from transformers import AutoModelForMultimodalLM, AutoProcessor, StoppingCriteria, StoppingCriteriaList

from server import _free_mib, _owned_mib
import server as _gpu_accounting

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
_trace_lock = threading.Lock()
_model = None
_processor = None
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
DEADLINE_SECONDS = 100
MAX_OUTPUT_TOKENS = 4096
MAX_OWNED_MIB = int(os.environ.get("OCR_MAX_PEAK_MIB", "6144"))
if not 1 <= MAX_OWNED_MIB <= 7168:
    raise RuntimeError("invalid OCR GPU budget")
RESERVE_MIB = 2048


def _resource_sample() -> tuple[int, int]:
    try:
        free, owned = _free_mib(), _owned_mib()
        if not isinstance(free, int) or not isinstance(owned, int):
            raise ValueError("missing GPU measurement")
        if owned < 0 or owned > MAX_OWNED_MIB or free < RESERVE_MIB + max(0, MAX_OWNED_MIB-owned):
            raise ValueError("GPU reserve breached")
        return free, owned
    except Exception as exc:
        raise HTTPException(503, "OCR GPU reserve unavailable") from exc


def _trace(request_id: str, event: str, **fields) -> None:
    path = os.environ.get("OCR_TRACE_PATH")
    if not path:
        return
    row = {"time_unix": time.time(), "request_id": request_id,
           "event": event, **fields}
    try:
        with _trace_lock, open(path, "a", encoding="utf-8") as sink:
            sink.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


class BoundedStop(StoppingCriteria):
    def __init__(self, until: float, cancel: threading.Event, prompt_tokens: int,
                 request_id: str = ""):
        self.until = until
        self.cancel = cancel
        self.prompt_tokens = prompt_tokens
        self.request_id = request_id
        self.deadline = False
        self.cancelled = False
        self.repetition = False
        self.resource = False
        self.steps = 0
        self.last_full_sample = time.monotonic()
        self.last_owned = 0

    def __call__(self, input_ids, scores, **kwargs):
        self.deadline = time.monotonic() >= self.until
        self.cancelled = self.cancel.is_set()
        self.steps += 1
        if self.steps == 1 and self.request_id:
            _trace(self.request_id, "generation_progress",
                   generated_tokens=int(input_ids.shape[-1]-self.prompt_tokens))
        try:
            # Free memory is cheap to sample every step. The process listing is
            # sampled every eight steps or 0.5s; stale owned memory makes the
            # reserve test stricter when the OCR process allocates more memory.
            if self.steps == 1 or self.steps % 8 == 0 or time.monotonic() - self.last_full_sample >= .5:
                _, self.last_owned = _resource_sample()
                self.last_full_sample = time.monotonic()
            free = _free_mib()
            if not isinstance(free, int) or free < RESERVE_MIB + max(0, MAX_OWNED_MIB-self.last_owned):
                raise ValueError("GPU free reserve breached")
        except HTTPException:
            self.resource = True
        except Exception:
            self.resource = True
        generated = input_ids[0, self.prompt_tokens:]
        if generated.numel() >= 160 and generated.numel() % 8 == 0:
            tail = generated[-512:].tolist()
            self.repetition = any(
                tail[-period:] == tail[-2*period:-period]
                == tail[-3*period:-2*period] == tail[-4*period:-3*period]
                for period in range(1, min(128, len(tail)//4) + 1)
            )
        stopped = self.deadline or self.cancelled or self.repetition or self.resource
        return torch.full((input_ids.shape[0],), stopped,
                          dtype=torch.bool, device=input_ids.device)


class Request(BaseModel):
    model: str
    image_base64: str = Field(max_length=15_000_000)
    calibration_prompt: Literal["natural", "arabic", "verbatim", "universal", "conditional"] = "conditional"


def _load() -> None:
    global _model, _processor
    if _model is not None:
        return
    _resource_sample()
    owner_file = os.environ.get("OCR_OWNED_PID_FILE")
    if owner_file:
        host_pid = getattr(_gpu_accounting, "_host_pid", None)
        if not isinstance(host_pid, int):
            raise HTTPException(503, "OCR GPU owner unavailable")
        Path(owner_file).write_text(str(host_pid), encoding="ascii")
    path = os.environ["OCR_MODEL_PATH"]
    _processor = AutoProcessor.from_pretrained(path, local_files_only=True, trust_remote_code=False)
    _model = AutoModelForMultimodalLM.from_pretrained(
        path, local_files_only=True, trust_remote_code=False, torch_dtype=torch.bfloat16,
    ).to("cuda").eval()
    try:
        _resource_sample()
    except HTTPException:
        _model = None
        torch.cuda.empty_cache()
        raise


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


@app.get("/v1/eval-status")
def eval_status(authorization: str = Header(default="")):
    token = os.environ.get("OCR_SERVICE_TOKEN", "")
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(401, "Unauthorized")
    return {"busy": _lock.locked(), "ready": _model is not None}


def _infer(request: Request, request_id: str, started: float,
           cancel: threading.Event):
    try:
        _resource_sample()
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
        _resource_sample()
        deadline = BoundedStop(started + DEADLINE_SECONDS, cancel,
                               int(encoded["input_ids"].shape[-1]), request_id)
        _trace(request_id, "generation_start", owned_mib=_owned_mib(), free_mib=_free_mib())
        with torch.inference_mode():
            output = _model.generate(**encoded, max_new_tokens=MAX_OUTPUT_TOKENS,
                                     do_sample=False, stopping_criteria=StoppingCriteriaList([deadline]))
        generated_tokens = int(output[0].shape[-1] - encoded["input_ids"].shape[-1])
        _trace(request_id, "generation_end", elapsed_seconds=round(time.monotonic()-started, 3),
               generated_tokens=generated_tokens, deadline_triggered=deadline.deadline,
               cancelled=deadline.cancelled, repetition=deadline.repetition,
               resource_stop=deadline.resource,
               hit_output_cap=generated_tokens >= MAX_OUTPUT_TOKENS,
               owned_mib=_owned_mib(), free_mib=_free_mib())
        if deadline.deadline or time.monotonic() >= started + DEADLINE_SECONDS:
            raise HTTPException(503, "OCR inference deadline exceeded")
        if deadline.cancelled or cancel.is_set():
            raise HTTPException(503, "OCR request disconnected")
        if deadline.repetition:
            raise HTTPException(503, "OCR repeated output")
        if deadline.resource:
            raise HTTPException(503, "OCR GPU reserve unavailable")
        if generated_tokens >= MAX_OUTPUT_TOKENS:
            raise HTTPException(503, "OCR output cap reached")
        _resource_sample()
        text = _processor.decode(output[0][encoded["input_ids"].shape[-1]:],
                                 skip_special_tokens=True).strip()
        blocks = [] if not text else [{"text": text, "bbox": None, "confidence": None}]
        _trace(request_id, "response", status=200, elapsed_seconds=round(time.monotonic()-started, 3))
        return {"model": MODEL_ID, "blocks": blocks}
    except HTTPException as exc:
        _trace(request_id, "response", status=exc.status_code, reason=str(exc.detail),
               elapsed_seconds=round(time.monotonic()-started, 3))
        raise
    except Exception as exc:
        _trace(request_id, "response", status=500, reason=type(exc).__name__,
               elapsed_seconds=round(time.monotonic()-started, 3))
        raise


@app.post("/v1/ocr")
async def ocr(request: Request, connection: FastAPIRequest,
              authorization: str = Header(default=""),
              x_eval_request_id: str = Header(default="")):
    token = os.environ.get("OCR_SERVICE_TOKEN", "")
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(401, "Unauthorized")
    if request.model != MODEL_ID:
        raise HTTPException(422, "Unsupported model")
    request_id = x_eval_request_id if x_eval_request_id.isascii() and len(x_eval_request_id) <= 80 else "invalid-id"
    _trace(request_id, "arrived")
    if not _lock.acquire(blocking=False):
        _trace(request_id, "rejected", status=503, reason="busy")
        raise HTTPException(503, "OCR busy")
    started = time.monotonic()
    cancel = threading.Event()
    _trace(request_id, "acquired")
    try:
        worker = asyncio.create_task(asyncio.to_thread(_infer, request, request_id,
                                                         started, cancel))
    except Exception:
        _lock.release()
        raise
    def release(done):
        _lock.release()
        _trace(request_id, "released", elapsed_seconds=round(time.monotonic()-started, 3))
    worker.add_done_callback(release)
    try:
        while not worker.done():
            if await connection.is_disconnected():
                cancel.set()
                break
            await asyncio.wait({worker}, timeout=0.1)
        result = await asyncio.shield(worker)
        if cancel.is_set():
            raise HTTPException(503, "OCR request disconnected")
        return result
    except asyncio.CancelledError:
        cancel.set()
        # The lock belongs to the worker completion callback, not this coroutine.
        raise
