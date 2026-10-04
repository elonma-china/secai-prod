"""Bounded, resumable client for the existing translation v2 file-job API.

The manifest contains capability IDs and idempotency keys. Keep it private.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Sequence

import httpx


LANGUAGES = frozenset({"vi", "en", "zh", "ja", "ko", "fr", "de", "es", "ru", "ar"})
EXTENSIONS = frozenset({".txt", ".docx", ".doc"})
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
MAX_FILES = 2000
MAX_FILE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class BatchEntry:
    path: Path
    source_lang: str
    target_lang: str


class BatchError(Exception):
    pass


@contextmanager
def _manifest_lock(path: Path):
    """Only one run/resume/cancel writer may operate on a manifest at a time."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(path.name + ".lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BatchError("manifest is in use; stop the running batch before cancel/resume") from None
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _binding(row: dict[str, Any]) -> str:
    value = [row["sha256"], row["source_lang"], row["target_lang"],
             Path(row["path"]).suffix.lower()]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_entry(entry: BatchEntry) -> None:
    if entry.source_lang not in LANGUAGES | {"auto"} or entry.target_lang not in LANGUAGES:
        raise BatchError("invalid language code")
    if (entry.source_lang not in {"auto", "vi", entry.target_lang}
            and entry.target_lang != "vi"):
        raise BatchError("unsupported language pair")
    if entry.path.suffix.lower() not in EXTENSIONS:
        raise BatchError("unsupported file extension")
    if not entry.path.is_file() or entry.path.stat().st_size > MAX_FILE_BYTES:
        raise BatchError("missing or oversized file")
    if not entry.path.name or len(entry.path.name) > 255 or any(c in entry.path.name for c in "\\\x00"):
        raise BatchError("invalid filename")


def create_manifest(entries: Sequence[BatchEntry], path: Path) -> dict[str, Any]:
    """Persist random keys and fingerprints before any network request."""
    if not entries or len(entries) > MAX_FILES or path.exists():
        raise BatchError("manifest exists or batch size is invalid")
    rows = []
    for entry in entries:
        entry = BatchEntry(entry.path.resolve(), entry.source_lang, entry.target_lang)
        _validate_entry(entry)
        row = {"path": str(entry.path), "source_lang": entry.source_lang,
                     "target_lang": entry.target_lang, "sha256": _digest_file(entry.path),
                     "key": secrets.token_hex(16), "job_id": None, "status": "pending",
                     "error_code": None, "output_txt": None, "output_json": None}
        row["binding"] = _binding(row)
        rows.append(row)
    data = {"schema": "translate-v2-batch-v1", "entries": rows}
    _atomic_json(path, data)
    return data


def _load_manifest(path: Path, *, check_inputs: bool = True) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "translate-v2-batch-v1" or not isinstance(data.get("entries"), list):
        raise BatchError("unsupported manifest")
    if not 1 <= len(data["entries"]) <= MAX_FILES:
        raise BatchError("invalid manifest size")
    for row in data["entries"]:
        if row.get("binding") != _binding(row):
            raise BatchError("input or language binding changed")
        entry = BatchEntry(Path(row["path"]), row["source_lang"], row["target_lang"])
        if check_inputs and row["status"] not in TERMINAL:
            _validate_entry(entry)
            if _digest_file(entry.path) != row["sha256"]:
                raise BatchError("input changed since manifest creation")
        if (not isinstance(row["key"], str) or len(row["key"]) != 32
                or any(c not in "0123456789abcdef" for c in row["key"])):
            raise BatchError("invalid manifest key")
    return data


async def _request(client: httpx.AsyncClient, method: str, url: str, deadline: float,
                   *, attempts: int = 4, **kwargs: Any) -> httpx.Response:
    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BatchError("request deadline exceeded")
        try:
            # A transport failure may happen after the server accepted the POST.
            # Replay the complete bytes with the same persisted idempotency key.
            for part in kwargs.get("files", {}).values():
                if isinstance(part, tuple) and hasattr(part[1], "seek"):
                    part[1].seek(0)
            response = await asyncio.wait_for(
                client.request(method, url, timeout=min(30, remaining), **kwargs),
                timeout=remaining)
        except (asyncio.TimeoutError, httpx.TimeoutException, httpx.TransportError):
            if attempt == attempts - 1:
                raise BatchError("transport unavailable") from None
            await asyncio.sleep(min(2 ** attempt, max(0, deadline - time.monotonic())))
            continue
        if response.status_code != 429:
            return response
        if attempt == attempts - 1:
            raise BatchError("rate limit retry budget exhausted")
        retry = response.headers.get("Retry-After", "60")
        try:
            delay = max(1, int(retry))
        except ValueError:
            delay = 60
        await asyncio.sleep(min(delay, max(0, deadline - time.monotonic())))
    raise BatchError("request retry budget exhausted")


async def _run_batch_locked(client: httpx.AsyncClient, entries: Sequence[BatchEntry] | None,
                            manifest_path: Path, output_dir: Path, *, max_inflight: int,
                            upload_interval: float, poll_interval: float,
                            job_timeout: float) -> dict[str, int]:
    """Run or resume one bounded batch; each failure is recorded per file."""
    if not 1 <= max_inflight <= 2 or upload_interval < 0 or poll_interval <= 0 or job_timeout <= 0:
        raise BatchError("invalid batch limits")
    data = create_manifest(entries, manifest_path) if entries is not None else _load_manifest(manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = asyncio.Lock()
    upload_lock = asyncio.Lock()
    next_upload = 0.0
    queue = asyncio.Queue()
    for index, row in enumerate(data["entries"]):
        if row["status"] not in TERMINAL:
            queue.put_nowait(index)

    async def save():
        async with lock:
            _atomic_json(manifest_path, data)

    async def process(index: int):
        nonlocal next_upload
        row = data["entries"][index]
        deadline = time.monotonic() + job_timeout
        try:
            if row["job_id"] is None:
                path = Path(row["path"])
                _validate_entry(BatchEntry(path, row["source_lang"], row["target_lang"]))
                if _digest_file(path) != row["sha256"]:
                    raise BatchError("input changed before upload")
                row["status"] = "uploading"
                await save()
                # At most one upload per interval across all workers; the opened
                # file streams one request rather than materializing the batch.
                async with upload_lock:
                    await asyncio.sleep(max(0, next_upload - time.monotonic()))
                    next_upload = time.monotonic() + upload_interval
                    with path.open("rb") as source:
                        response = await _request(client, "POST", "/ai/v2/translate/files", deadline,
                            files={"file": (path.name, source)},
                            data={"source_lang": row["source_lang"], "target_lang": row["target_lang"]},
                            headers={"Idempotency-Key": row["key"]})
                if response.status_code != 202:
                    row["status"] = "failed"
                    row["error_code"] = response.json().get("error", {}).get("code", "HTTP_ERROR")
                    await save()
                    return
                row["job_id"] = response.json()["job_id"]
                row["status"] = "queued"
                await save()
            job_id = row["job_id"]
            while True:
                response = await _request(client, "GET", f"/ai/v2/translate/jobs/{job_id}", deadline)
                if response.status_code != 200:
                    raise BatchError(f"status HTTP {response.status_code}")
                status = response.json()["status"]
                row["status"] = status
                if status in TERMINAL:
                    break
                await asyncio.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
            if status == "succeeded":
                response = await _request(client, "GET", f"/ai/v2/translate/jobs/{job_id}/result", deadline)
                if response.status_code != 200:
                    raise BatchError(f"result HTTP {response.status_code}")
                result = response.json()
                stem = f"{index + 1:04d}"
                _atomic_json(output_dir / f"{stem}.json", result)
                _atomic_text(output_dir / f"{stem}.txt", result["translated_text"])
                row["output_json"], row["output_txt"] = f"{stem}.json", f"{stem}.txt"
            else:
                row["error_code"] = response.json().get("error", {}).get("code") if status == "failed" else "JOB_CANCELLED"
            await save()
        except (BatchError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            # A POST may have committed even if its response was lost. The
            # persisted key makes this state safe to replay on resume.
            row["status"] = "interrupted"
            row["error_code"] = type(exc).__name__.upper()
            await save()

    async def worker():
        while not queue.empty():
            try:
                index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await process(index)
            queue.task_done()

    await asyncio.gather(*(worker() for _ in range(max_inflight)))
    return {status: sum(row["status"] == status for row in data["entries"])
            for status in ("succeeded", "failed", "cancelled", "interrupted", "pending")}


async def run_batch(client: httpx.AsyncClient, entries: Sequence[BatchEntry] | None,
                    manifest_path: Path, output_dir: Path, *, max_inflight: int = 2,
                    upload_interval: float = 12, poll_interval: float = 4,
                    job_timeout: float = 35 * 60) -> dict[str, int]:
    with _manifest_lock(manifest_path):
        return await _run_batch_locked(client, entries, manifest_path, output_dir,
                                       max_inflight=max_inflight,
                                       upload_interval=upload_interval,
                                       poll_interval=poll_interval,
                                       job_timeout=job_timeout)


async def _cancel_batch_locked(client: httpx.AsyncClient, manifest_path: Path) -> dict[str, int]:
    data = _load_manifest(manifest_path, check_inputs=False)
    cancelled = 0
    skipped = 0
    unresolved = 0
    for row in data["entries"]:
        if row["status"] in TERMINAL:
            continue
        if row["job_id"] is None and row["status"] == "pending":
            row["status"] = "cancelled"
            skipped += 1
            _atomic_json(manifest_path, data)
            continue
        try:
            deadline = time.monotonic() + 90
            if row["job_id"] is None:
                # A stopped POST may have committed. Replay its saved key to
                # recover the job ID, then cancel it under the same manifest lock.
                path = Path(row["path"])
                _validate_entry(BatchEntry(path, row["source_lang"], row["target_lang"]))
                if _digest_file(path) != row["sha256"]:
                    raise BatchError("input changed before cancel recovery")
                with path.open("rb") as source:
                    admitted = await _request(client, "POST", "/ai/v2/translate/files", deadline,
                        files={"file": (path.name, source)},
                        data={"source_lang": row["source_lang"], "target_lang": row["target_lang"]},
                        headers={"Idempotency-Key": row["key"]})
                if admitted.status_code != 202:
                    raise BatchError("unable to recover accepted job")
                row["job_id"] = admitted.json()["job_id"]
                _atomic_json(manifest_path, data)
            response = await _request(client, "DELETE", f"/ai/v2/translate/jobs/{row['job_id']}",
                                      deadline)
            if response.status_code == 202:
                row["status"] = response.json()["status"]
                cancelled += 1
            elif response.status_code == 409:
                status = await _request(client, "GET", f"/ai/v2/translate/jobs/{row['job_id']}", deadline)
                if status.status_code != 200 or status.json()["status"] not in TERMINAL:
                    raise BatchError("unable to resolve terminal job")
                row["status"] = status.json()["status"]
            else:
                raise BatchError("unable to cancel job")
            _atomic_json(manifest_path, data)
        except (BatchError, OSError, ValueError, KeyError, json.JSONDecodeError):
            unresolved += 1
    return {"cancel_requested": cancelled, "not_submitted": skipped, "unresolved": unresolved}


async def cancel_batch(client: httpx.AsyncClient, manifest_path: Path) -> dict[str, int]:
    with _manifest_lock(manifest_path):
        return await _cancel_batch_locked(client, manifest_path)


def _expand_paths(values: Sequence[str]) -> list[Path]:
    def walk_files(directory: Path):
        for root, dirs, files in os.walk(directory, followlinks=False):
            dirs.sort()
            for name in sorted(files):
                yield Path(root) / name

    paths = []
    scanned = 0
    for value in values:
        path = Path(value)
        candidates = walk_files(path) if path.is_dir() else (path,)
        for candidate in candidates:
            scanned += 1
            if scanned > 10_000:
                raise BatchError("directory scan exceeds maximum file count")
            if candidate.suffix.lower() in EXTENSIONS and candidate.is_file():
                paths.append(candidate)
                if len(paths) > MAX_FILES:
                    raise BatchError("batch exceeds maximum file count")
    return paths


def _plan_entries(path: Path) -> list[BatchEntry]:
    entries = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            row = json.loads(line)
            entries.append(BatchEntry(Path(row["path"]), row["source_lang"], row["target_lang"]))
            if len(entries) > MAX_FILES:
                raise BatchError("batch exceeds maximum file count")
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "resume", "cancel"))
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--plan", type=Path, help="JSONL rows: path, source_lang, target_lang")
    parser.add_argument("--source-lang")
    parser.add_argument("--target-lang")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.action == "run":
        if args.plan:
            entries = _plan_entries(args.plan)
        else:
            if not args.source_lang or not args.target_lang:
                parser.error("run requires --plan or both language flags")
            entries = [BatchEntry(path, args.source_lang, args.target_lang) for path in _expand_paths(args.paths)]
    else:
        entries = None
    if args.action != "cancel" and args.output_dir is None:
        parser.error("run/resume requires --output-dir")

    async def execute():
        async with httpx.AsyncClient(base_url=args.base_url) as client:
            if args.action == "cancel":
                return await cancel_batch(client, args.manifest)
            return await run_batch(client, entries, args.manifest, args.output_dir)

    result = asyncio.run(execute())
    print(json.dumps(result, sort_keys=True))
    if args.action == "cancel" and result["unresolved"]:
        raise SystemExit(1)
    if args.action != "cancel" and any(result[key] for key in ("failed", "interrupted", "pending")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
