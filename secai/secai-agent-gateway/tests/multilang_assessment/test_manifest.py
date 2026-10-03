import pytest

from evals.multilang.manifest import ManifestError, validate_manifest


DIRECTIONS = (
    "vi>zh-Hans", "zh-Hans>vi", "vi>ja", "ja>vi", "vi>ko", "ko>vi",
    "vi>fr", "fr>vi", "vi>de", "de>vi", "vi>es", "es>vi",
    "vi>ru", "ru>vi", "vi>ar", "ar>vi", "vi>en", "en>vi",
)


def row(sample_id="s1", direction="vi>ja", split="DEV", source="Xin chào bạn.", group="doc-1"):
    return {
        "id": sample_id,
        "direction": direction,
        "source": source,
        "reference": "こんにちは。",
        "split": split,
        "stratum": ["general", "short"],
        "protected_spans": [],
        "source_revision": "public-corpus@1234567",
        "license": "CC-BY-4.0",
        "source_group": group,
    }


def test_split_leakage_rejected_for_same_document_group():
    with pytest.raises(ManifestError, match="source_group"):
        validate_manifest([
            row(split="DEV"),
            row("s2", "vi>fr", "TEST", "Vui lòng dịch câu khác.", "doc-1"),
        ], require_directions=False)


def test_split_leakage_rejected_for_normalized_source_across_groups():
    with pytest.raises(ManifestError, match="duplicate source"):
        validate_manifest([
            row(source="  Xin   chào bạn.  "),
            row("s2", "vi>fr", "TEST", "xin chào bạn.", "doc-2"),
        ], require_directions=False)


def test_split_leakage_rejected_for_near_duplicate_punctuation():
    with pytest.raises(ManifestError, match="near duplicate"):
        validate_manifest([
            row(source="The package is vulnerable to CVE-2024-1234."),
            row("s2", "vi>fr", "TEST", "The package is vulnerable to CVE-2024-1234!", "doc-2"),
        ], require_directions=False)


def test_all_18_directions_required():
    with pytest.raises(ManifestError, match="18 directions"):
        validate_manifest([row()])

    complete = [row(f"s{i}", direction, group=f"doc-{i}", source=f"Câu số {i}.")
                for i, direction in enumerate(DIRECTIONS)]
    assert validate_manifest(complete).directions == DIRECTIONS


def test_reference_provenance_requires_pinned_revision_and_license():
    bad = row()
    bad["source_revision"] = "latest"
    with pytest.raises(ManifestError, match="source_revision"):
        validate_manifest([bad], require_directions=False)

    bad = row()
    bad["license"] = "unknown"
    with pytest.raises(ManifestError, match="license"):
        validate_manifest([bad], require_directions=False)


def test_protected_span_must_match_source_offsets():
    bad = row(source="Kiểm tra CVE-2024-1234.")
    bad["protected_spans"] = [{"start": 8, "end": 21, "text": "CVE-2024-1234", "kind": "cve"}]
    with pytest.raises(ManifestError, match="protected_span"):
        validate_manifest([bad], require_directions=False)
