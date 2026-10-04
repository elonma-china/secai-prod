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
    assert result["tokens"]["cve"]["tp"] == 40
    assert result["tokens"]["ip"]["tp"] == 40
    assert len([key for key in result["subsets"] if key != "technical"]) == 10


def test_guessed_five_octet_ip_counts_false_positive_and_false_negative():
    manifest = {"images": [{"id": "arabic", "category": "technical", "language": "ar",
                            "text": "CVE-2024-3018 تخص 192.0.2.18.",
                            "tokens": [{"kind": "cve", "text": "CVE-2024-3018"},
                                       {"kind": "ip", "text": "192.0.2.18"}]}]}
    result = score(manifest, [{"id": "arabic", "raw_text":
                               "CVE-2024-3018 تخص 18.192.0.2.1"}], "raw_text")
    assert result["tokens"]["cve"] == {"tp": 1, "fp": 0, "fn": 0, "f1": 1.0}
    assert result["tokens"]["ip"] == {"tp": 0, "fp": 1, "fn": 1, "f1": 0.0}


def test_malformed_cve_and_ip_next_to_cjk_are_not_laundered():
    manifest = {"images": [{"id": "cjk", "category": "technical", "language": "zh",
                            "text": "中CVE-2024-1234中 192.0.2.1",
                            "tokens": [{"kind": "cve", "text": "CVE-2024-1234"},
                                       {"kind": "ip", "text": "192.0.2.1"}]}]}
    result = score(manifest, [{"id": "cjk", "raw_text":
                               "中CVE-2024-1234X中 192.O.2.1"}], "raw_text")
    for kind in ("cve", "ip"):
        assert result["tokens"][kind] == {"tp": 0, "fp": 1, "fn": 1, "f1": 0.0}


def test_sentence_period_before_ip_does_not_hide_exact_token():
    manifest = {"images": [{"id": "period", "category": "technical", "language": "ar",
                            "text": "192.0.2.18", "tokens": [{"kind": "ip", "text": "192.0.2.18"}]}]}
    result = score(manifest, [{"id": "period", "raw_text": ".192.0.2.18"}], "raw_text")
    assert result["tokens"]["ip"] == {"tp": 1, "fp": 0, "fn": 0, "f1": 1.0}
