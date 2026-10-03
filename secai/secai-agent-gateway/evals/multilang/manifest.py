"""Validate source provenance and leakage boundaries before translation runs."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


DIRECTIONS = (
    "vi>zh-Hans", "zh-Hans>vi", "vi>ja", "ja>vi", "vi>ko", "ko>vi",
    "vi>fr", "fr>vi", "vi>de", "de>vi", "vi>es", "es>vi",
    "vi>ru", "ru>vi", "vi>ar", "ar>vi", "vi>en", "en>vi",
)
SPLITS = frozenset({"TRAIN", "DEV", "TEST"})
REQUIRED = frozenset({
    "id", "direction", "source", "reference", "split", "stratum",
    "protected_spans", "source_revision", "license", "source_group",
})


class ManifestError(ValueError):
    """The corpus cannot be used without correcting its source evidence."""


@dataclass(frozen=True)
class ManifestSummary:
    directions: tuple[str, ...]
    samples: int
    unique_sources: int
    data_hash: str


def normalized_source(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _ngrams(value: str, size: int = 5) -> set[str]:
    return {value[index:index + size] for index in range(max(0, len(value) - size + 1))}


def canonical_hash(records: Iterable[dict[str, Any]]) -> str:
    payload = json.dumps(list(records), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _nonempty(row: dict[str, Any], key: str) -> str:
    value = row[key]
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key} must be a nonempty string")
    return value


def validate_manifest(
    rows: Iterable[dict[str, Any]], *, require_directions: bool = True,
    min_per_direction: int = 0,
) -> ManifestSummary:
    records = list(rows)
    ids: set[str] = set()
    groups: dict[str, str] = {}
    sources: dict[str, str] = {}
    seen_sources: list[tuple[str, str]] = []
    directions: set[str] = set()
    counts: dict[str, int] = {}
    for row in records:
        if not isinstance(row, dict) or not REQUIRED.issubset(row):
            missing = sorted(REQUIRED - set(row)) if isinstance(row, dict) else ["row is not an object"]
            raise ManifestError(f"missing manifest fields: {missing}")
        sample_id = _nonempty(row, "id")
        if sample_id in ids:
            raise ManifestError(f"duplicate sample id: {sample_id}")
        ids.add(sample_id)
        direction = _nonempty(row, "direction")
        if direction not in DIRECTIONS:
            raise ManifestError(f"unsupported direction: {direction}")
        directions.add(direction)
        counts[direction] = counts.get(direction, 0) + 1
        split = _nonempty(row, "split")
        if split not in SPLITS:
            raise ManifestError(f"invalid split: {split}")
        group = _nonempty(row, "source_group")
        if group in groups and groups[group] != split:
            raise ManifestError(f"source_group appears in multiple splits: {group}")
        groups[group] = split
        source = _nonempty(row, "source")
        _nonempty(row, "reference")
        norm = normalized_source(source)
        if norm in sources and sources[norm] != split:
            raise ManifestError(f"duplicate source appears in multiple splits: {sample_id}")
        if len(norm) >= 20:
            grams = _ngrams(norm)
            for prior, prior_split in seen_sources:
                if prior_split == split or len(prior) < 20:
                    continue
                prior_grams = _ngrams(prior)
                if len(grams & prior_grams) / len(grams | prior_grams) >= 0.80:
                    raise ManifestError(f"near duplicate source appears in multiple splits: {sample_id}")
        sources[norm] = split
        seen_sources.append((norm, split))
        revision = _nonempty(row, "source_revision")
        if revision.casefold() in {"latest", "main", "head", "unknown", "none", "unversioned"} or not re.search(r"[0-9a-f]{7,}$", revision):
            raise ManifestError(f"source_revision is not pinned: {sample_id}")
        license_name = _nonempty(row, "license")
        if license_name.casefold() in {"unknown", "none", "unlicensed", "tbd"}:
            raise ManifestError(f"license is not established: {sample_id}")
        strata = row["stratum"]
        if not isinstance(strata, list) or not strata or any(not isinstance(x, str) or not x.strip() for x in strata):
            raise ManifestError(f"stratum must be a nonempty list: {sample_id}")
        spans = row["protected_spans"]
        if not isinstance(spans, list):
            raise ManifestError(f"protected_spans must be a list: {sample_id}")
        previous_end = 0
        for span in spans:
            if not isinstance(span, dict):
                raise ManifestError(f"protected_span must be an object: {sample_id}")
            start, end, text = span.get("start"), span.get("end"), span.get("text")
            if (not isinstance(start, int) or isinstance(start, bool) or
                    not isinstance(end, int) or isinstance(end, bool) or
                    start < previous_end or end <= start or end > len(source) or
                    not isinstance(text, str) or source[start:end] != text or
                    not isinstance(span.get("kind"), str)):
                raise ManifestError(f"protected_span offsets/text invalid: {sample_id}")
            previous_end = end
    if require_directions and directions != set(DIRECTIONS):
        raise ManifestError(f"manifest must include all 18 directions; missing {sorted(set(DIRECTIONS) - directions)}")
    if min_per_direction and any(counts.get(direction, 0) < min_per_direction for direction in (DIRECTIONS if require_directions else directions)):
        raise ManifestError(f"fewer than {min_per_direction} samples in a direction")
    return ManifestSummary(
        directions=tuple(direction for direction in DIRECTIONS if direction in directions),
        samples=len(records),
        unique_sources=len(groups),
        data_hash=canonical_hash(records),
    )
