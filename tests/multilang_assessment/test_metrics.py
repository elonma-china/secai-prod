from evals.multilang.metrics import diagnostic_metrics


def test_exact_reference_scores_100_with_explicit_signature():
    report = diagnostic_metrics(
        ["The security patch is available today."],
        ["The security patch is available today."],
        target_lang="en",
    )
    assert report["chrfpp"] == 100.0
    assert report["bleu"] == 100.0
    assert report["bleu_tokenizer"] == "intl"
    assert "version:2.5.1" in report["bleu_signature"]
    assert report["coverage"] == {"scored": 1, "scheduled": 1}


def test_japanese_bleu_is_labeled_character_tokenization():
    report = diagnostic_metrics(
        ["セキュリティ更新を確認してください。"],
        ["セキュリティ更新を確認してください。"],
        target_lang="ja",
    )
    assert report["bleu_tokenizer"] == "char"
    assert report["bleu"] == 100.0


def test_failed_prediction_is_missing_not_zero_metric_or_quality_pass():
    report = diagnostic_metrics(
        ["Bonjour", "Bonsoir"],
        ["Bonjour", None],
        target_lang="fr",
    )
    assert report["coverage"] == {"scored": 1, "scheduled": 2}
    assert report["chrfpp"] == 100.0
    assert report["quality_gate"] == "NOT_ASSESSED"
