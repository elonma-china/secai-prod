import json
from pathlib import Path

from evals.translation_ocr.score import distance, score


def test_unicode_cer_preserves_diacritics_case_spaces():
    assert distance("á A\n", "a a\n") == 2
    assert distance("A  B", "A B") == 1
    assert distance("a\r\nb", "a\nb") == 0


def test_complete_manifest_identity_scores_100():
    manifest = json.loads((Path(__file__).resolve().parents[1] /
                           "evals/translation_ocr/manifest.json").read_text())
    rows = [{"id": item["id"], "raw_text": item["text"]} for item in manifest["images"]]
    result = score(manifest, rows, "raw_text")
    assert result["overall"]["micro_accuracy"] == 100
    assert result["overall"]["macro_accuracy"] == 100
    assert result["tokens"]["cve"]["f1"] == 1
    assert result["tokens"]["ip"]["f1"] == 1
    assert result["tokens"]["cve"]["tp"] == 32
    assert result["tokens"]["ip"]["tp"] == 32
