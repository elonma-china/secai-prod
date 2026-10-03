"""Fail-closed JSONL corpus validation before any model call."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .manifest import validate_manifest


class CorpusError(ValueError):
    pass


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CorpusError(f"invalid JSONL line {line_number}") from exc
            if not isinstance(item, dict):
                raise CorpusError(f"JSONL line {line_number} is not an object")
            records.append(item)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--allow-subset", action="store_true", help="local fixture only; not acceptance")
    parser.add_argument("--min-per-direction", type=int, default=0)
    args = parser.parse_args(argv)
    rows = load_jsonl(args.manifest)
    summary = validate_manifest(
        rows, require_directions=not args.allow_subset,
        min_per_direction=args.min_per_direction,
    )
    print(json.dumps({
        "samples": summary.samples,
        "directions": list(summary.directions),
        "unique_sources": summary.unique_sources,
        "data_hash": summary.data_hash,
        "splits": dict(Counter(row["split"] for row in rows)),
        "acceptance_ready": not args.allow_subset and args.min_per_direction >= 30,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
