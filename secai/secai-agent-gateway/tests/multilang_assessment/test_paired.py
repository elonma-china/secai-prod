import pytest

from evals.multilang.score import ScoreError, paired_bootstrap_delta


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
