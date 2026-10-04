"""Prepare bounded TXT/DOCX file inputs from the frozen FLORES DEV manifest.

This command does not contact a model or inspect DEVTEST/holdout material.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

from docx import Document

from .multifile_batch import MAX_FILES, _atomic_text


EXPECTED = frozenset(
    [f"vi>{language}" for language in ("en", "zh-Hans", "ja", "ko", "fr", "de", "es", "ru", "ar")]
    + [f"{language}>vi" for language in ("en", "zh-Hans", "ja", "ko", "fr", "de", "es", "ru", "ar")]
)
SAFE_ID = re.compile(r"[A-Za-z0-9_-]+\Z")


def prepare(manifest: Path, output_dir: Path) -> tuple[Path, dict[str, int]]:
    """Write one source file per row and a batch plan; every fifth file is DOCX."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("output directory must be empty")
    rows = []
    counts: collections.Counter[str] = collections.Counter()
    identifiers: set[str] = set()
    with manifest.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            direction = row["direction"]
            if row["split"] != "DEV" or direction not in EXPECTED or not SAFE_ID.fullmatch(row["id"]):
                raise ValueError("unexpected DEV row")
            if not isinstance(row["source"], str) or not row["source"].strip():
                raise ValueError("missing source text")
            if row["id"] in identifiers:
                raise ValueError("duplicate source ID")
            identifiers.add(row["id"])
            counts[direction] += 1
            if counts[direction] > 60 or len(rows) >= MAX_FILES:
                raise ValueError("unexpected row count")
            rows.append(row)
    if set(counts) != EXPECTED or set(counts.values()) != {60}:
        raise ValueError("frozen sample must contain 60 rows in all 18 directions")

    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    plan = []
    ordinals: collections.Counter[str] = collections.Counter()
    for row in rows:
        source_lang, target_lang = row["direction"].split(">")
        source_lang = "zh" if source_lang == "zh-Hans" else source_lang
        target_lang = "zh" if target_lang == "zh-Hans" else target_lang
        direction_dir = output_dir / row["direction"].replace(">", "-to-")
        direction_dir.mkdir(mode=0o700, exist_ok=True)
        # The ordinal in the frozen manifest decides format consistently.
        ordinal = ordinals[row["direction"]]
        ordinals[row["direction"]] += 1
        suffix = ".docx" if ordinal % 5 == 0 else ".txt"
        path = direction_dir / f"{row['id']}{suffix}"
        if suffix == ".docx":
            doc = Document()
            doc.add_paragraph(row["source"])
            doc.save(path)
        else:
            _atomic_text(path, row["source"])
        plan.append({"path": str(path.resolve()), "source_lang": source_lang,
                     "target_lang": target_lang, "id": row["id"],
                     "direction": row["direction"]})
    plan_path = output_dir / "batch-plan.jsonl"
    _atomic_text(plan_path, "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in plan))
    return plan_path, dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    plan, counts = prepare(args.manifest, args.output_dir)
    print(json.dumps({"plan": str(plan), "directions": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
