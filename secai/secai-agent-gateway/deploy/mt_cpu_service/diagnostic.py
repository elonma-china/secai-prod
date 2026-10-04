"""Private grouped-DEV file diagnostic; network actions require --allow-live.

The existing grouped mapper owns source-to-block verification. This wrapper only
fixes the batch limits and ensures that private API traffic stays on loopback.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from evals.multilang.multifile_batch import (
    BatchEntry,
    BatchError,
    _atomic_json,
    _digest_file,
    _load_manifest,
    _validate_row,
    cancel_batch,
    create_manifest,
    run_batch,
)
from evals.multilang.prepare_multifile_dev_grouped import (
    APPROVED_DEV_SHA256,
    DIRECTIONS,
    map_results,
    prepare_grouped,
)
from secai_gateway.translation.documents import extract_docx
from secai_gateway.translation.settings import TranslationSettings


def _entries(work_dir: Path) -> list[BatchEntry]:
    mapping = json.loads((work_dir / "inputs/sample-block-map.json").read_text())
    plan = [json.loads(line) for line in
            (work_dir / "inputs/batch-plan.jsonl").read_text().splitlines() if line]
    files = mapping.get("files")
    if (mapping.get("schema") != "translate-v2-grouped-dev-v1"
            or mapping.get("manifest_sha256") != APPROVED_DEV_SHA256
            or not isinstance(files, list) or len(files) != 18 or len(plan) != 18
            or {item.get("direction") for item in files} != set(DIRECTIONS)):
        raise ValueError("expected the frozen 18-direction grouped DEV input")
    expected = {(item["path"], item["source_lang"], item["target_lang"])
                for item in files}
    actual = {(item["path"], item["source_lang"], item["target_lang"])
              for item in plan}
    if actual != expected or len(actual) != 18:
        raise ValueError("batch plan differs from grouped DEV mapping")
    input_dir = (work_dir / "inputs").resolve()
    entries = []
    settings = TranslationSettings(_env_file=None)
    mapped = {item["path"]: item for item in files}
    for item in plan:
        path = Path(item["path"]).resolve()
        if path.parent != input_dir or path.suffix != ".docx":
            raise ValueError("batch plan contains a file outside the private input directory")
        expected_blocks = mapped[str(path)].get("blocks")
        if not isinstance(expected_blocks, list) or len(expected_blocks) != 30:
            raise ValueError("prepared source block mapping is incomplete")
        try:
            blocks = extract_docx(path, settings).blocks
        except Exception:
            raise ValueError("prepared source block extraction failed") from None
        if len(blocks) != 30 or any(
                block.id != expected["block_id"] or block.order != index
                or block.kind != "paragraph"
                or hashlib.sha256(block.text.encode()).hexdigest() != expected["source_sha256"]
                for index, (block, expected) in enumerate(zip(blocks, expected_blocks))):
            raise ValueError("prepared source block differs from frozen mapping")
        entries.append(BatchEntry(path, item["source_lang"], item["target_lang"]))
    return entries


def _sealed_entries(work_dir: Path) -> list[BatchEntry]:
    """Check all inputs against the offline-sealed batch before any API request."""
    entries = _entries(work_dir)
    try:
        data = _load_manifest(work_dir / "private-batch-manifest.json")
        rows = data["entries"]
        if len(rows) != 18:
            raise ValueError("incomplete offline batch seal")
        for entry, row in zip(entries, rows):
            _validate_row(row)
            if (row["path"] != str(entry.path) or row["source_lang"] != entry.source_lang
                    or row["target_lang"] != entry.target_lang):
                raise ValueError("offline batch seal differs from grouped plan")
            if row["sha256"] != _digest_file(entry.path):
                raise ValueError("prepared DOCX digest changed after offline seal")
    except (BatchError, OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("offline batch seal is missing or invalid") from None
    return entries


def _create_seal(work_dir: Path) -> int:
    entries = _entries(work_dir)
    manifest = work_dir / "private-batch-manifest.json"
    create_manifest(entries, manifest)
    try:
        _sealed_entries(work_dir)
    except Exception:
        manifest.unlink(missing_ok=True)
        raise
    return len(entries)


def _verified_batch(work_dir: Path) -> None:
    """Require the existing mapper's 18-job, 540-block success proof."""
    temporary = work_dir / f".v3-batch-check-{secrets.token_hex(8)}.jsonl"
    try:
        map_results(work_dir / "inputs/sample-block-map.json",
                    work_dir / "private-batch-manifest.json",
                    work_dir / "results", temporary)
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("file batch is not verified: 18 successful jobs and 540 blocks required") from None
    finally:
        temporary.unlink(missing_ok=True)


def _loopback_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
            or not parsed.port or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("diagnostic gateway URL must be plain loopback HTTP")
    return value.rstrip("/")


async def _live(action: str, work_dir: Path, base_url: str) -> dict[str, int]:
    base_url = _loopback_url(base_url)
    manifest = work_dir / "private-batch-manifest.json"
    results = work_dir / "results"
    if action != "cancel":
        _sealed_entries(work_dir)
    async with httpx.AsyncClient(base_url=base_url, trust_env=False,
                                 follow_redirects=False) as client:
        if action == "cancel":
            return await cancel_batch(client, manifest)
        return await run_batch(client, None, manifest, results, max_inflight=2,
                               upload_interval=12, poll_interval=4, job_timeout=7500)


async def _v3_smoke(work_dir: Path, base_url: str, manifest: Path) -> dict[str, int]:
    """Four public-DEV probes and one synthetic literal probe, sequentially."""
    _verified_batch(work_dir)
    snapshot = manifest.read_bytes()
    if hashlib.sha256(snapshot).hexdigest() != APPROVED_DEV_SHA256:
        raise ValueError("approved frozen DEV manifest digest does not match")
    directions = ("vi>ja", "ja>vi", "vi>zh-Hans", "vi>ar")
    selected = {}
    for line in snapshot.splitlines():
        row = json.loads(line)
        if row["direction"] in directions and row["direction"] not in selected:
            selected[row["direction"]] = row
    if set(selected) != set(directions):
        raise ValueError("frozen DEV manifest is missing smoke directions")
    probes = [(selected[direction]["id"], direction, selected[direction]["source"], ())
              for direction in directions]
    probes.append(("synthetic-literals-v1", "vi>ja",
                   "Kiểm tra CVE-2024-12345 tại 192.0.2.1.",
                   ("CVE-2024-12345", "192.0.2.1")))
    rows = []
    async with httpx.AsyncClient(base_url=_loopback_url(base_url), trust_env=False,
                                 follow_redirects=False, timeout=125) as client:
        for sample_id, direction, source_text, literals in probes:
            source, target = direction.split(">")
            result = {"sample_id": sample_id, "direction": direction}
            try:
                response = await client.post("/ai/v3/translate", json={
                    "text": source_text, "source_lang": source, "target_lang": target})
            except httpx.HTTPError:
                result["error_code"] = "TRANSPORT_ERROR"
                rows.append(result)
                continue
            result["status_code"] = response.status_code
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if (response.status_code == 200 and isinstance(payload, dict)
                    and isinstance(payload.get("translated_text"), str)
                    and payload["translated_text"].strip()):
                result["response"] = payload
                if literals:
                    result["literal_ok"] = all(literal in payload["translated_text"]
                                               for literal in literals)
            elif response.status_code == 200:
                result["error_code"] = "INVALID_RESPONSE"
            else:
                error = payload.get("error") if isinstance(payload, dict) else None
                code = error.get("code") if isinstance(error, dict) else None
                result["error_code"] = code if isinstance(code, str) and len(code) <= 64 else "HTTP_ERROR"
            rows.append(result)
    _atomic_json(work_dir / "v3-smoke.json", {"schema": "v3-public-dev-smoke-v1",
                                               "manifest_sha256": APPROVED_DEV_SHA256,
                                               "results": rows})
    return {"ok": sum("response" in row and row.get("literal_ok", True) for row in rows),
            "total": len(probes)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "seal", "run", "resume", "cancel",
                                           "map-results", "v3-smoke"))
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--dev-manifest", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if args.work_dir.is_symlink():
        parser.error("private work directory must not be a symlink")
    work_dir = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if work_dir.stat().st_mode & 0o077:
        parser.error("private work directory must have mode 0700")
    if args.action == "prepare":
        if args.dev_manifest is None:
            parser.error("prepare requires --dev-manifest")
        plan, mapping = prepare_grouped(args.dev_manifest, work_dir / "inputs")
        _create_seal(work_dir)
        result = {"plan": str(plan), "mapping": str(mapping), "files": 18,
                  "blocks_per_file": 30}
    elif args.action == "seal":
        result = {"sealed_files": _create_seal(work_dir)}
    elif args.action == "map-results":
        result = map_results(work_dir / "inputs/sample-block-map.json",
                             work_dir / "private-batch-manifest.json",
                             work_dir / "results", work_dir / "candidate-blocks.jsonl")
    elif args.action == "v3-smoke":
        if not args.allow_live or not args.base_url or args.dev_manifest is None:
            parser.error("v3-smoke requires --allow-live, --base-url and --dev-manifest")
        result = asyncio.run(_v3_smoke(work_dir, args.base_url, args.dev_manifest))
        if result["ok"] != result["total"]:
            print(json.dumps(result, sort_keys=True))
            raise SystemExit(1)
    else:
        if not args.allow_live or not args.base_url:
            parser.error("network action requires --allow-live and --base-url")
        result = asyncio.run(_live(args.action, work_dir, args.base_url))
        if result.get("failed") or result.get("unfinished") or result.get("unresolved"):
            print(json.dumps(result, sort_keys=True))
            raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
