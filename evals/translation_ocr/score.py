"""Frozen OCR scorer: Unicode code-point CER and occurrence-aware token F1."""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path


def normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value).replace("\r\n", "\n")


def distance(reference: str, output: str) -> int:
    reference, output = normalize(reference), normalize(output)
    previous = list(range(len(output) + 1))
    for i, char in enumerate(reference, 1):
        current = [i]
        for j, other in enumerate(output, 1):
            current.append(min(previous[j] + 1, current[-1] + 1,
                               previous[j - 1] + (char != other)))
        previous = current
    return previous[-1]


def _tokens(text: str, kind: str) -> list[str]:
    # Count malformed OCR guesses as false positives rather than silently
    # dropping them or accepting a valid-looking prefix of an invalid literal.
    # ASCII identifier boundaries allow adjacent CJK/Arabic/Cyrillic prose.
    pattern = (r"(?<![A-Za-z0-9_.-])CVE[-A-Za-z0-9]+(?![A-Za-z0-9_-]|\.[A-Za-z0-9])"
               if kind == "cve" else
               r"(?<![A-Za-z0-9_:])(?<![A-Za-z0-9]\.)\d+(?:\.[A-Za-z0-9]+){3,}(?:/[A-Za-z0-9]+)?(?![A-Za-z0-9_/]|\.[A-Za-z0-9])")
    return [m.group() for m in re.finditer(pattern, normalize(text), flags=re.I)]


def score(manifest: dict, rows: list[dict], field: str) -> dict:
    predictions = {row["id"]: row.get(field, "") for row in rows}
    if len(predictions) != len(rows):
        raise ValueError("duplicate image ID")
    required = {item["id"] for item in manifest["images"]}
    if set(predictions) != required:
        raise ValueError("missing or extra image IDs")
    counts = {kind: {"tp": 0, "fp": 0, "fn": 0} for kind in ("cve", "ip")}
    per_image = []
    for item in manifest["images"]:
        ref, out = normalize(item["text"]), normalize(predictions[item["id"]])
        edits = distance(ref, out)
        per_image.append({"id": item["id"], "category": item["category"],
                          "language": item["language"], "reference_len": len(ref),
                          "edits": edits, "accuracy": 100 * max(0, 1 - edits / len(ref))})
        for kind in counts:
            expected = [token["text"] for token in item["tokens"] if token["kind"] == kind]
            actual = _tokens(out, kind)
            for token in expected:
                if token in actual:
                    actual.remove(token)
                    counts[kind]["tp"] += 1
                else:
                    counts[kind]["fn"] += 1
            counts[kind]["fp"] += len(actual)
    def aggregate(rows: list[dict]) -> dict:
        return {"count": len(rows), "micro_cer": sum(x["edits"] for x in rows) /
                sum(x["reference_len"] for x in rows),
                "micro_accuracy": 100 * max(0, 1 - sum(x["edits"] for x in rows) /
                                             sum(x["reference_len"] for x in rows)),
                "macro_accuracy": sum(x["accuracy"] for x in rows) / len(rows)}
    languages = sorted({item["language"] for item in manifest["images"]})
    subsets = {language: aggregate([x for x in per_image if x["language"] == language])
               for language in languages}
    subsets["technical"] = aggregate([x for x in per_image if x["category"] == "technical"])
    result = {"overall": aggregate(per_image), "per_image": per_image, "subsets": subsets}
    for kind, c in counts.items():
        denom = 2 * c["tp"] + c["fp"] + c["fn"]
        c["f1"] = None if denom == 0 else 2 * c["tp"] / denom
    result["tokens"] = counts
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("predictions", type=Path, help="JSONL with id/raw_text/corrected_text")
    parser.add_argument("--field", choices=("raw_text", "corrected_text"), default="raw_text")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    rows = [json.loads(line) for line in args.predictions.read_text().splitlines() if line]
    print(json.dumps(score(manifest, rows, args.field), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
