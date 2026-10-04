"""Prepare 18 grouped DEV DOCX jobs and map their real result blocks to samples.

No model, API, or reference text is used by this module. Selection is the first
30 aligned groups in the frozen DEV manifest, in manifest order.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from docx import Document

from secai_gateway.translation.documents import extract_docx
from secai_gateway.translation.settings import TranslationSettings


_OTHER = ("en", "zh-Hans", "ja", "ko", "fr", "de", "es", "ru", "ar")
DIRECTIONS = tuple([f"vi>{language}" for language in _OTHER] +
                   [f"{language}>vi" for language in _OTHER])
_DIRECTION_SET = frozenset(DIRECTIONS)
APPROVED_DEV_SHA256 = "ad1e0fd7f8068da49bee2255e64a8019ec5a2a840b0347e9403153c612a0f64e"


def _private_write(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _manifest_rows(path: Path) -> tuple[list[str], dict[tuple[str, str], dict[str, Any]]]:
    groups = []
    seen_groups = set()
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    sample_ids = set()
    with path.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            group, direction = row["aligned_item_id"], row["direction"]
            if (row["split"] != "DEV" or direction not in _DIRECTION_SET
                    or not isinstance(group, str) or not group
                    or not isinstance(row["source"], str) or not row["source"].strip()
                    or not isinstance(row["source_group"], str) or not row["source_group"]
                    or not isinstance(row["id"], str) or not row["id"]):
                raise ValueError("invalid frozen DEV row")
            if group not in seen_groups:
                groups.append(group)
                seen_groups.add(group)
            key = (group, direction)
            if key in rows or row["id"] in sample_ids:
                raise ValueError("duplicate DEV row or sample ID")
            rows[key] = row
            sample_ids.add(row["id"])
            if len(rows) > 60 * len(DIRECTIONS):
                raise ValueError("frozen DEV manifest exceeds 60 groups")
    if len(groups) != 60 or len(rows) != 60 * len(DIRECTIONS):
        raise ValueError("frozen DEV manifest must have 60 complete groups")
    for group in groups:
        if {direction for (item, direction) in rows if item == group} != _DIRECTION_SET:
            raise ValueError("incomplete aligned group")
    return groups, rows


def prepare_grouped(manifest: Path, output_dir: Path, *,
                    expected_manifest_sha256: str = APPROVED_DEV_SHA256) -> tuple[Path, Path]:
    """Create 18 DOCX inputs, a batch plan, and exact block/sample mapping."""
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if manifest_sha256 != expected_manifest_sha256:
        raise ValueError("approved frozen manifest digest does not match")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("output directory must be empty")
    groups, rows = _manifest_rows(manifest)
    selected = groups[:30]
    settings = TranslationSettings()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = []
    plan = []
    for direction in DIRECTIONS:
        selected_rows = [rows[(group, direction)] for group in selected]
        if len({row["source_group"] for row in selected_rows}) != 30:
            raise ValueError("selected rows lack 30 independent source groups")
        source_lang, target_lang = direction.split(">")
        source_lang = "zh" if source_lang == "zh-Hans" else source_lang
        target_lang = "zh" if target_lang == "zh-Hans" else target_lang
        path = output_dir / (direction.replace(">", "-to-") + ".docx")
        document = Document()
        for row in selected_rows:
            document.add_paragraph(row["source"])
        document.save(path)
        if path.stat().st_size > settings.max_upload_bytes:
            raise ValueError("grouped DOCX exceeds gateway upload limit")
        extracted = extract_docx(path, settings)
        if len(extracted.blocks) != 30:
            raise ValueError("grouped DOCX did not extract as 30 blocks")
        blocks = []
        for index, (row, block) in enumerate(zip(selected_rows, extracted.blocks)):
            if (block.kind != "paragraph" or block.order != index
                    or block.id != f"b{index + 1:06d}" or block.text != row["source"]):
                raise ValueError("grouped DOCX altered source paragraph")
            blocks.append({"index": index, "block_id": block.id, "sample_id": row["id"],
                           "aligned_item_id": row["aligned_item_id"],
                           "source_group": row["source_group"],
                           "source_sha256": hashlib.sha256(block.text.encode()).hexdigest()})
        files.append({"path": str(path.resolve()), "direction": direction,
                      "source_lang": source_lang, "target_lang": target_lang,
                      "blocks": blocks})
        plan.append({"path": str(path.resolve()), "source_lang": source_lang,
                     "target_lang": target_lang})
    plan_path = output_dir / "batch-plan.jsonl"
    mapping_path = output_dir / "sample-block-map.json"
    _private_write(plan_path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in plan))
    mapping = {"schema": "translate-v2-grouped-dev-v1",
               "manifest_sha256": manifest_sha256,
               "selected_groups": selected, "files": files}
    _private_write(mapping_path, json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")
    return plan_path, mapping_path


def map_results(mapping_path: Path, batch_manifest: Path, results_dir: Path,
                output: Path, *,
                expected_manifest_sha256: str = APPROVED_DEV_SHA256) -> dict[str, int]:
    """Verify every actual result block before exporting 540 candidate rows."""
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    if mapping.get("schema") != "translate-v2-grouped-dev-v1":
        raise ValueError("invalid grouped mapping")
    if mapping.get("manifest_sha256") != expected_manifest_sha256:
        raise ValueError("approved frozen manifest digest does not match")
    files = mapping.get("files")
    selected = mapping.get("selected_groups")
    if (not isinstance(files, list) or len(files) != 18
            or not isinstance(selected, list) or len(selected) != 30
            or len(set(selected)) != 30
            or {file["direction"] for file in files} != _DIRECTION_SET):
        raise ValueError("invalid grouped mapping inventory")
    mapped_paths = [file["path"] for file in files]
    if (len(set(mapped_paths)) != 18
            or any(str(Path(path).resolve()) != path for path in mapped_paths)):
        raise ValueError("invalid grouped mapping paths")
    for file in files:
        source_lang, target_lang = file["direction"].split(">")
        if (file["source_lang"] != ("zh" if source_lang == "zh-Hans" else source_lang)
                or file["target_lang"] != ("zh" if target_lang == "zh-Hans" else target_lang)):
            raise ValueError("invalid grouped language mapping")
        blocks = file.get("blocks")
        if (not isinstance(blocks, list) or len(blocks) != 30
                or [block["aligned_item_id"] for block in blocks] != selected
                or len({block["sample_id"] for block in blocks}) != 30
                or len({block["source_group"] for block in blocks}) != 30
                or any(block["index"] != index or block["block_id"] != f"b{index + 1:06d}"
                       for index, block in enumerate(blocks))):
            raise ValueError("invalid grouped block inventory")
    batch = json.loads(batch_manifest.read_text(encoding="utf-8"))
    entries = batch.get("entries")
    if not isinstance(entries, list) or len(entries) != 18:
        raise ValueError("expected exactly 18 completed file jobs")
    canonical_paths = [str(Path(row["path"]).resolve()) for row in entries]
    if (len(set(canonical_paths)) != 18 or set(canonical_paths) != set(mapped_paths)
            or any(row.get("status") != "succeeded" for row in entries)
            or len({row.get("job_id") for row in entries}) != 18
            or any(not isinstance(row.get("job_id"), str)
                   or len(row["job_id"]) != 32
                   or any(c not in "0123456789abcdef" for c in row["job_id"])
                   for row in entries)):
        raise ValueError("batch entries do not match exactly 18 succeeded jobs")
    by_path = dict(zip(canonical_paths, entries))
    candidates = []
    counts: collections.Counter[str] = collections.Counter()
    for file in files:
        job = by_path.get(file["path"])
        if job is None or not job.get("output_json"):
            raise ValueError("missing successful grouped file job")
        name = job["output_json"]
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("invalid result filename")
        result = json.loads((results_dir / name).read_text(encoding="utf-8"))
        if (result.get("job_id") != job["job_id"] or result.get("status") != "succeeded"
                or result.get("kind") != "file"
                or result.get("source_lang") != file["source_lang"]
                or result.get("target_lang") != file["target_lang"]):
            raise ValueError("result envelope mismatch")
        if len(result["blocks"]) != len(file["blocks"]) or len(result["blocks"]) != 30:
            raise ValueError("result must contain 30 blocks")
        if result.get("translated_text") != "\n".join(
                block["translated_text"] for block in result["blocks"]):
            raise ValueError("result text does not match blocks")
        for expected, actual in zip(file["blocks"], result["blocks"]):
            if (actual["id"] != expected["block_id"] or actual["order"] != expected["index"]
                    or actual.get("kind") != "paragraph"
                    or hashlib.sha256(actual["source_text"].encode()).hexdigest()
                    != expected["source_sha256"]):
                raise ValueError("source block mismatch")
            if not isinstance(actual["translated_text"], str) or not actual["translated_text"].strip():
                raise ValueError("empty translated block")
            candidates.append({"sample_id": expected["sample_id"],
                               "aligned_item_id": expected["aligned_item_id"],
                               "direction": file["direction"],
                               "source_sha256": expected["source_sha256"],
                               "translated_text": actual["translated_text"]})
            counts[file["direction"]] += 1
    if set(counts) != _DIRECTION_SET or set(counts.values()) != {30}:
        raise ValueError("candidate export lacks 30 rows in every direction")
    _private_write(output, "".join(json.dumps(row, ensure_ascii=False) + "\n"
                                   for row in candidates))
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    export = sub.add_parser("map-results")
    export.add_argument("--mapping", type=Path, required=True)
    export.add_argument("--batch-manifest", type=Path, required=True)
    export.add_argument("--results-dir", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        plan, mapping = prepare_grouped(args.manifest, args.output_dir)
        print(json.dumps({"plan": str(plan), "mapping": str(mapping),
                          "files": 18, "blocks_per_file": 30}, sort_keys=True))
    else:
        counts = map_results(args.mapping, args.batch_manifest, args.results_dir, args.output)
        print(json.dumps({"output": str(args.output), "counts": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
