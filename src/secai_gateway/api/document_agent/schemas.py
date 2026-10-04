"""Finite multipart and image admission for one document turn."""
from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default

from fastapi import HTTPException, Request

MAX_REQUEST_BYTES = 11 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_EDGE = 10_000
_HANDLE = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True, slots=True)
class TurnInput:
    prompt: str
    file_bytes: bytes | None
    media_type: str | None
    task_handle: str | None
    stream: bool

    def digest(self) -> str:
        parts = (self.prompt.encode("utf-8"), self.file_bytes or b"", (self.media_type or "").encode(), (self.task_handle or "").encode(), b"1" if self.stream else b"0")
        return hashlib.sha256(b"".join(len(p).to_bytes(8, "big") + p for p in parts)).hexdigest()


def _bad(code: str, status: int = 400) -> HTTPException:
    return HTTPException(status, detail={"code": code})


def _validate_image(data: bytes, media_type: str) -> None:
    # PIL is imported only for enabled image admissions, never at default-OFF startup.
    from PIL import Image, UnidentifiedImageError

    if not data or len(data) > MAX_IMAGE_BYTES:
        raise _bad("IMAGE_SIZE_INVALID", 413)
    try:
        with Image.open(io.BytesIO(data)) as image:
            expected = "PNG" if media_type == "image/png" else "JPEG"
            width, height = image.size
            if image.format != expected or width < 1 or height < 1 or width > MAX_EDGE or height > MAX_EDGE or width * height > MAX_PIXELS:
                raise _bad("IMAGE_INVALID")
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise _bad("IMAGE_INVALID")


async def parse_turn(request: Request) -> TurnInput:
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data;"):
        raise _bad("MULTIPART_REQUIRED")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_REQUEST_BYTES:
            raise _bad("REQUEST_TOO_LARGE", 413)
    # The stdlib parser reads this already-bounded in-memory body. No UploadFile spool.
    message = BytesParser(policy=default).parsebytes(b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode("ascii", "ignore") + b"\r\n\r\n" + raw)
    if not message.is_multipart() or message.defects:
        raise _bad("MULTIPART_INVALID")
    fields: dict[str, bytes] = {}
    media_type: str | None = None
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data" or part.defects:
            raise _bad("MULTIPART_INVALID")
        name = part.get_param("name", header="content-disposition")
        if name not in {"prompt", "file", "task_handle", "stream"} or name in fields:
            raise _bad("FIELD_INVALID")
        data = part.get_payload(decode=True)
        if data is None:
            raise _bad("MULTIPART_INVALID")
        if name == "file":
            media_type = part.get_content_type().lower()
            if media_type not in {"image/png", "image/jpeg"}:
                raise _bad("IMAGE_TYPE_UNSUPPORTED", 415)
            if not part.get_filename():
                raise _bad("FILE_NAME_REQUIRED")
        elif part.get_filename() is not None or len(data) > 40_000:
            raise _bad("FIELD_INVALID")
        fields[name] = data
    if "prompt" not in fields:
        raise _bad("PROMPT_REQUIRED")
    try:
        prompt = fields["prompt"].decode("utf-8")
        handle = fields.get("task_handle", b"").decode("ascii") or None
        stream_text = fields.get("stream", b"false").decode("ascii")
    except UnicodeError:
        raise _bad("FIELD_ENCODING_INVALID")
    if len(prompt) > 10_000 or not prompt.strip():
        raise _bad("PROMPT_INVALID")
    if handle is not None and not _HANDLE.fullmatch(handle):
        raise _bad("TASK_HANDLE_INVALID")
    if stream_text not in {"true", "false"}:
        raise _bad("STREAM_INVALID")
    image_bytes = fields.get("file")
    if image_bytes is not None:
        import asyncio
        await asyncio.to_thread(_validate_image, image_bytes, media_type)
    return TurnInput(prompt, image_bytes, media_type, handle, stream_text == "true")
