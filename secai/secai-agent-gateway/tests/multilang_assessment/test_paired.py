import json

import pytest

from evals.multilang.manifest import canonical_hash
from evals.multilang.score import ScoreError, main as score_main, paired_bootstrap_delta, paired_quality_report

from .test_manifest import row
from .test_score import annotation, result


def test_paired_bootstrap_resamples_document_groups_not_repeated_requests():
    report = paired_bootstrap_delta(
        baseline=[0.0, 0.0, 0.0],
        candidate=[1.0, 1.0, 1.0],
        source_groups=["doc-1", "doc-1", "doc-1"],
        replicates=10000,
        seed=42,
    )
    assert report["unique_groups"] == 1
    assert report["delta"] == 1.0
    assert report["ci95"] == [1.0, 1.0]
    assert report["replicates"] == 10000


def test_paired_bootstrap_is_deterministic_and_margin_remains_unset():
    args = dict(
        baseline=[0.0, 1.0, 1.0],
        candidate=[1.0, 1.0, 0.0],
        source_groups=["a", "b", "c"],
        replicates=10000, seed=81,
    )
    assert paired_bootstrap_delta(**args) == paired_bootstrap_delta(**args)
    assert paired_bootstrap_delta(**args)["noninferiority_gate"] == "PENDING_BASELINE_MARGIN"


def test_paired_bootstrap_rejects_unpaired_rows():
    with pytest.raises(ScoreError, match="paired"):
        paired_bootstrap_delta([0.0], [1.0, 0.0], ["a"])


def test_paired_quality_report_joins_same_en_vi_sources_but_blocks_margin():
    manifest = [row("vi", "vi>en", group="vi", source="Câu nguồn vi."),
                row("en", "en>vi", group="en", source="English source.")]
    digest = canonical_hash(manifest)
    baseline = [result("vi"), result("en")]
    candidate = [result("vi"), result("en")]
    for item in baseline:
        item["mode"] = "legacy-api"
        item["data_hash"] = digest
    for item in candidate:
        item["data_hash"] = digest
    reviews = [annotation("vi"), annotation("en")]
    report = paired_quality_report(manifest, baseline, candidate, reviews, reviews,
                                   phase="DEV", rounds=1)
    assert set(report["directions"]) == {"vi>en", "en>vi"}
    assert report["directions"]["vi>en"]["delta"] == 0
    assert report["directions"]["vi>en"]["noninferiority_gate"] == "PENDING_BASELINE_MARGIN"
    assert report["release_gate"] == "BLOCKED"


def test_score_cli_embeds_paired_report_when_control_files_are_supplied(tmp_path):
    manifest = [row("vi", "vi>en", group="vi", source="Câu nguồn vi."),
                row("en", "en>vi", group="en", source="English source.")]
    digest = canonical_hash(manifest)
    baseline = [result("vi"), result("en")]
    candidate = [result("vi"), result("en")]
    for item in baseline:
        item["mode"] = "legacy-api"
        item["data_hash"] = digest
    for item in candidate:
        item["data_hash"] = digest
    reviews = [annotation("vi"), annotation("en")]

    def write(name, rows):
        path = tmp_path / name
        path.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in rows))
        return path

    paths = [write("manifest.jsonl", manifest), write("candidate.jsonl", candidate),
             write("reviews.jsonl", reviews), write("baseline.jsonl", baseline),
             write("baseline-reviews.jsonl", reviews)]
    output = tmp_path / "report.json"
    assert score_main([
        "--manifest", str(paths[0]), "--results", str(paths[1]),
        "--annotations", str(paths[2]), "--baseline-results", str(paths[3]),
        "--baseline-annotations", str(paths[4]), "--mode", "model-only",
        "--phase", "DEV", "--rounds", "1", "--min-samples-per-direction", "1",
        "--output", str(output),
    ]) == 0
    report = json.loads(output.read_text())
    assert report["en_vi_paired"]["directions"]["vi>en"]["paired_requests"] == 1
    assert report["en_vi_paired"]["release_gate"] == "BLOCKED"
