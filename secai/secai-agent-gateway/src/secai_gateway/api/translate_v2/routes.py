from __future__ import annotations

import asyncio
import json
import re
import secrets
from datetime import datetime, timezone
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from pathlib import PurePath

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
from starlette.requests import Request as StarletteRequest

from ...translation.jobs import JobStore
from ...translation.settings import TranslationSettings
from ...translation.types import TranslationError

router = APIRouter(prefix="/ai/v2/translate", include_in_schema=True)
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_LANGUAGES = frozenset({"vi", "en", "zh", "ja", "ko", "fr", "de", "es", "ru", "ar"})
# v3's capability list and the IAS UI built against it use the canonical
# "zh-Hans" code; v2 has only ever accepted "zh". Without this alias, a
# client that reuses the v3 code for a v2 upload gets INVALID_LANGUAGE/400
# before a job is even created (production case ids FILE-vi-zh-Hans /
# FILE-zh-Hans-vi, 2026-10-09).
_LANGUAGE_ALIASES = {"zh-hans": "zh"}


def _normalize_lang(code: str) -> str:
    return _LANGUAGE_ALIASES.get(code.strip().lower(), code)
_STATUS = {
    "INVALID_REQUEST": 400, "INVALID_LANGUAGE": 400,
    "UNSUPPORTED_LANGUAGE_PAIR": 400, "EMPTY_FILE": 400,
    "JOB_NOT_FOUND": 404, "JOB_EXPIRED": 410, "RESULT_NOT_READY": 409,
    "IDEMPOTENCY_CONFLICT": 409, "JOB_ALREADY_FINISHED": 409,
    "JOB_FAILED": 409, "JOB_CANCELLED": 409,
    "FILE_TOO_LARGE": 413, "REQUEST_TOO_LARGE": 413,
    "TEXT_TOO_LONG": 413, "IMAGE_TOO_LARGE": 413,
    "ARCHIVE_LIMIT_EXCEEDED": 413, "UNSUPPORTED_FILE_TYPE": 415,
    "FILE_TYPE_MISMATCH": 415, "CORRUPT_DOCUMENT": 422,
    "CORRUPT_IMAGE": 422, "UNSUPPORTED_ENCODING": 422,
    "UNSAFE_DOCUMENT": 422, "NO_TEXT_DETECTED": 422,
    "RATE_LIMITED": 429, "QUEUE_FULL": 429,
    "DEPENDENCY_UNAVAILABLE": 503, "STORAGE_UNAVAILABLE": 503,
}


def _error(code: str, message: str | None = None) -> JSONResponse:
    status = _STATUS.get(code, 500)
    headers = {"Cache-Control": "no-store"}
    if status == 429:
        headers["Retry-After"] = "60"
    return JSONResponse({"error": {"code": code, "message": message or code.replace("_", " ").capitalize() + ".",
                                   "request_id": secrets.token_hex(12)}},
                        status_code=status, headers=headers)


def _stamp(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _job(row: dict) -> dict:
    job_id = row["job_id"]
    return {
        "job_id": job_id, "kind": row["kind"], "status": row["status"], "stage": row["stage"],
        "progress": {"completed": row["completed"], "total": row["total"]},
        "source_lang": row["source_lang"], "target_lang": row["target_lang"],
        "warnings": json.loads(row["warnings_json"]),
        "error": ({"code": row["error_code"], "message": "Processing failed.",
                   "request_id": secrets.token_hex(12)} if row["error_code"] else None),
        "created_at": _stamp(row["created"]), "updated_at": _stamp(row["updated"]),
        "expires_at": _stamp(row["expires"]),
        "status_url": f"/ai/v2/translate/jobs/{job_id}",
        "result_url": f"/ai/v2/translate/jobs/{job_id}/result",
    }


def _store(request: Request) -> tuple[JobStore, TranslationSettings]:
    settings: TranslationSettings = request.app.state.translation_v2_settings
    if not settings.enabled:
        raise TranslationError("DEPENDENCY_UNAVAILABLE", "Translation service is unavailable.")
    if request.app.state.translation_v2_store is None:
        raise TranslationError("STORAGE_UNAVAILABLE", "Translation storage is unavailable.")
    return request.app.state.translation_v2_store, settings


async def _admit(request: Request, kind: str) -> JSONResponse:
    form = None
    try:
        store, settings = _store(request)
        peer = request.client.host if request.client else "unknown"
        await asyncio.to_thread(store.rate, peer, "upload", settings.upload_rate_per_minute)
        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("multipart/form-data;"):
            raise TranslationError("INVALID_REQUEST", "Multipart form is required.")
        size = 0
        chunks: list[bytes] = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > settings.max_request_bytes:
                raise TranslationError("REQUEST_TOO_LARGE", "Request is too large.")
            chunks.append(chunk)
        body = b"".join(chunks)
        delivered = False

        async def receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        parsed = StarletteRequest(request.scope, receive=receive)
        try:
            form = await parsed.form(max_files=1, max_fields=settings.max_multipart_parts - 1,
                                     max_part_size=settings.max_text_field_bytes)
        except Exception as exc:
            raise TranslationError("INVALID_REQUEST", "Multipart form is invalid.") from exc
        items = form.multi_items()
        if len(items) > settings.max_multipart_parts or len({key for key, _ in items}) != len(items):
            raise TranslationError("INVALID_REQUEST", "Multipart fields are invalid.")
        fields = dict(items)
        if set(fields) - {"file", "source_lang", "target_lang"}:
            raise TranslationError("INVALID_REQUEST", "Multipart fields are invalid.")
        if "file" not in fields or "target_lang" not in fields:
            raise TranslationError("INVALID_REQUEST", "Required multipart fields are missing.")
        if not isinstance(fields["file"], UploadFile):
            raise TranslationError("INVALID_REQUEST", "A file is required.")
        if any(not isinstance(fields[name], str) or len(fields[name].encode()) > settings.max_text_field_bytes
               for name in fields if name != "file"):
            raise TranslationError("INVALID_REQUEST", "Multipart fields are invalid.")
        source = _normalize_lang(fields.get("source_lang", "auto"))
        target = _normalize_lang(fields["target_lang"])
        if source not in _LANGUAGES | {"auto"} or target not in _LANGUAGES:
            raise TranslationError("INVALID_LANGUAGE", "Language is invalid.")
        if source != "auto" and source != target and source != "vi" and target != "vi":
            raise TranslationError("UNSUPPORTED_LANGUAGE_PAIR", "Language pair is unsupported.")
        key = request.headers.get("Idempotency-Key")
        if key is not None and not _HEX.fullmatch(key):
            raise TranslationError("INVALID_REQUEST", "Idempotency key is invalid.")
        upload: UploadFile = fields["file"]
        filename = upload.filename or ""
        # .NET encodes non-ASCII filenames as MIME encoded-words.
        if filename.startswith("=?") and filename.endswith("?="):
            try:
                filename = str(make_header(decode_header(filename)))
            except (HeaderParseError, LookupError, UnicodeError, ValueError) as exc:
                raise TranslationError("INVALID_REQUEST", "Filename encoding is invalid.") from exc
        if not filename or len(filename) > 255 or any(c in filename for c in "/\\\x00"):
            raise TranslationError("INVALID_REQUEST", "Filename is invalid.")
        extension = PurePath(filename).suffix.lower()
        allowed = {".txt", ".docx", ".doc"} if kind == "file" else {".png", ".jpg", ".jpeg"}
        if extension not in allowed:
            raise TranslationError("UNSUPPORTED_FILE_TYPE", "File type is unsupported.")
        payload = await upload.read(settings.max_upload_bytes + 1)
        if len(payload) > settings.max_upload_bytes:
            raise TranslationError("FILE_TOO_LARGE", "File is too large.")
        if not payload:
            raise TranslationError("EMPTY_FILE", "File is empty.")
        if extension == ".docx" and not payload.startswith(b"PK\x03\x04"):
            raise TranslationError("FILE_TYPE_MISMATCH", "File type does not match extension.")
        if extension == ".doc" and not payload.startswith(_OLE):
            raise TranslationError("FILE_TYPE_MISMATCH", "File type does not match extension.")
        if extension == ".txt" and payload.startswith((b"PK\x03\x04", _OLE, b"%PDF", b"\x89PNG", b"\xff\xd8")):
            raise TranslationError("FILE_TYPE_MISMATCH", "File type does not match extension.")
        if kind == "image":
            if not settings.ocr_enabled or not settings.ocr_base_url:
                raise TranslationError("DEPENDENCY_UNAVAILABLE", "OCR service is unavailable.")
            if extension == ".png" and not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                raise TranslationError("FILE_TYPE_MISMATCH", "File type does not match extension.")
            if extension in {".jpg", ".jpeg"} and not payload.startswith(b"\xff\xd8\xff"):
                raise TranslationError("FILE_TYPE_MISMATCH", "File type does not match extension.")
        if extension == ".doc":
            from ...translation.documents import converter_available

            if not await asyncio.to_thread(converter_available, settings):
                raise TranslationError("DEPENDENCY_UNAVAILABLE", "DOC converter is unavailable.")
        row = await asyncio.to_thread(store.admit, kind, payload, extension, source, target, peer, key)
        return JSONResponse(_job(row), status_code=202, headers={"Cache-Control": "no-store"})
    except TranslationError as exc:
        return _error(exc.code, exc.message)
    except Exception:
        return _error("INTERNAL_ERROR", "Internal processing error.")
    finally:
        if form is not None:
            await form.close()


@router.post("/files", status_code=202)
async def upload_file(request: Request):
    return await _admit(request, "file")


@router.post("/images", status_code=202)
async def upload_image(request: Request):
    return await _admit(request, "image")


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    try:
        store, settings = _store(request)
        peer = request.client.host if request.client else "unknown"
        await asyncio.to_thread(store.rate, peer, "lookup", settings.lookup_rate_per_minute)
        row = await asyncio.to_thread(store.get, job_id)
        return JSONResponse(_job(row), headers={"Cache-Control": "no-store"})
    except TranslationError as exc:
        return _error(exc.code, exc.message)
    except Exception:
        return _error("INTERNAL_ERROR", "Internal processing error.")


@router.get("/jobs/{job_id}/result")
async def get_result(job_id: str, request: Request):
    try:
        store, settings = _store(request)
        peer = request.client.host if request.client else "unknown"
        await asyncio.to_thread(store.rate, peer, "lookup", settings.lookup_rate_per_minute)
        row = await asyncio.to_thread(store.get, job_id)
        if row["status"] == "failed":
            raise TranslationError("JOB_FAILED", "Job failed.")
        if row["status"] == "cancelled":
            raise TranslationError("JOB_CANCELLED", "Job was cancelled.")
        if row["status"] != "succeeded":
            raise TranslationError("RESULT_NOT_READY", "Result is not ready.")
        return JSONResponse(json.loads(row["result_json"]), headers={"Cache-Control": "no-store"})
    except TranslationError as exc:
        return _error(exc.code, exc.message)
    except Exception:
        return _error("INTERNAL_ERROR", "Internal processing error.")


@router.delete("/jobs/{job_id}", status_code=202)
async def delete_job(job_id: str, request: Request):
    try:
        store, settings = _store(request)
        peer = request.client.host if request.client else "unknown"
        await asyncio.to_thread(store.rate, peer, "lookup", settings.lookup_rate_per_minute)
        row = await asyncio.to_thread(store.cancel, job_id)
        return JSONResponse(_job(row), status_code=202, headers={"Cache-Control": "no-store"})
    except TranslationError as exc:
        return _error(exc.code, exc.message)
    except Exception:
        return _error("INTERNAL_ERROR", "Internal processing error.")
