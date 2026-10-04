"""Grouped DEV DOCX preparation and result-to-sample mapping stay offline."""
import hashlib
import json
from pathlib import Path

import pytest

from secai_gateway.translation.documents import extract_docx
from secai_gateway.translation.settings import TranslationSettings
from evals.multilang.prepare_multifile_dev_grouped import (
    DIRECTIONS, prepare_grouped, map_results,
)


def _frozen_fixture(path):
    with path.open("w", encoding="utf-8") as output:
        for group in range(60):
            for direction in DIRECTIONS:
                source = f"Source sentence {group:02d} for {direction}."
                output.write(json.dumps({
                    "id": f"sample-{group:02d}-{direction.replace('>', '-to-')}",
                    "aligned_item_id": f"group-{group:02d}",
                    "source_group": f"source-group-{group:02d}",
                    "direction": direction, "source": source,
                    "reference": "SECRET GOLD REFERENCE",
                    "split": "DEV",
                }) + "\n")


def test_grouped_prep_makes_18_real_docx_with_exact_30_extracted_blocks(tmp_path):
    frozen = tmp_path / "frozen.jsonl"
    _frozen_fixture(frozen)
    plan_path, mapping_path = prepare_grouped(
        frozen, tmp_path / "prepared",
        expected_manifest_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest())
    plan = [json.loads(line) for line in plan_path.read_text().splitlines()]
    mapping = json.loads(mapping_path.read_text())
    assert len(plan) == len(mapping["files"]) == 18
    assert mapping["selected_groups"] == [f"group-{i:02d}" for i in range(30)]
    assert mapping["manifest_sha256"] == hashlib.sha256(frozen.read_bytes()).hexdigest()
    assert {row["direction"] for row in mapping["files"]} == set(DIRECTIONS)
    assert all(row["source_lang"] != "zh-Hans" and row["target_lang"] != "zh-Hans"
               for row in plan)
    for record in mapping["files"]:
        extracted = extract_docx(Path(record["path"]), TranslationSettings())
        assert len(extracted.blocks) == len(record["blocks"]) == 30
        for block, expected in zip(extracted.blocks, record["blocks"]):
            assert block.id == expected["block_id"]
            assert block.order == expected["index"]
            assert hashlib.sha256(block.text.encode()).hexdigest() == expected["source_sha256"]
        assert b"SECRET GOLD REFERENCE" not in Path(record["path"]).read_bytes()
    assert "SECRET GOLD REFERENCE" not in mapping_path.read_text()


def test_actual_result_mapping_keeps_each_direction_at_30_and_rejects_mismatch(tmp_path):
    frozen = tmp_path / "frozen.jsonl"
    _frozen_fixture(frozen)
    frozen_hash = hashlib.sha256(frozen.read_bytes()).hexdigest()
    _, mapping_path = prepare_grouped(
        frozen, tmp_path / "prepared", expected_manifest_sha256=frozen_hash)
    mapping = json.loads(mapping_path.read_text())
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    batch_rows = []
    for index, file in enumerate(mapping["files"]):
        extracted = extract_docx(Path(file["path"]), TranslationSettings())
        result_blocks = [
            {"id": block.id, "order": block.order, "kind": block.kind,
             "source_text": block.text, "translated_text": f"translated-{index}-{block.order}"}
            for block in extracted.blocks
        ]
        name = f"{index:04d}.json"
        job_id = f"{index + 1:032x}"
        (results_dir / name).write_text(json.dumps({
            "job_id": job_id, "kind": "file", "status": "succeeded",
            "source_lang": file["source_lang"], "target_lang": file["target_lang"],
            "translated_text": "\n".join(block["translated_text"] for block in result_blocks),
            "blocks": result_blocks,
        }))
        batch_rows.append({"path": str(file["path"]), "status": "succeeded",
                           "job_id": job_id, "output_json": name})
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps({"entries": batch_rows}))
    export = tmp_path / "candidate.jsonl"
    counts = map_results(mapping_path, batch, results_dir, export,
                         expected_manifest_sha256=frozen_hash)
    assert set(counts.values()) == {30} and len(counts) == 18
    candidates = [json.loads(line) for line in export.read_text().splitlines()]
    assert len(candidates) == 540
    assert all(row["translated_text"].startswith("translated-") for row in candidates)
    assert all("reference" not in row for row in candidates)

    bad = json.loads((results_dir / "0000.json").read_text())
    bad["blocks"][0]["source_text"] = "tampered"
    (results_dir / "0000.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="source block mismatch"):
        map_results(mapping_path, batch, results_dir, tmp_path / "bad.jsonl",
                    expected_manifest_sha256=frozen_hash)
    assert not (tmp_path / "bad.jsonl").exists()


@pytest.mark.parametrize("extra", ["duplicate", "unrelated"])
def test_mapper_rejects_extra_failed_batch_entry(tmp_path, extra):
    frozen = tmp_path / "frozen.jsonl"
    _frozen_fixture(frozen)
    frozen_hash = hashlib.sha256(frozen.read_bytes()).hexdigest()
    _, mapping_path = prepare_grouped(
        frozen, tmp_path / "prepared", expected_manifest_sha256=frozen_hash)
    mapping = json.loads(mapping_path.read_text())
    rows = [{"path": file["path"], "status": "succeeded", "job_id": f"{i + 1:032x}",
             "output_json": f"{i:04d}.json"} for i, file in enumerate(mapping["files"])]
    rows.append({"path": rows[0]["path"] if extra == "duplicate" else str(tmp_path / "other.docx"),
                 "status": "failed", "job_id": f"{99:032x}", "output_json": None})
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps({"entries": rows}))
    with pytest.raises(ValueError, match="exactly 18"):
        map_results(mapping_path, batch, tmp_path, tmp_path / "candidate.jsonl",
                    expected_manifest_sha256=frozen_hash)


@pytest.mark.parametrize("field,wrong", [
    ("job_id", "f" * 32), ("status", "failed"), ("kind", "image"),
    ("source_lang", "wrong"), ("target_lang", "wrong"),
])
def test_mapper_rejects_wrong_result_envelope(tmp_path, field, wrong):
    frozen = tmp_path / "frozen.jsonl"
    _frozen_fixture(frozen)
    frozen_hash = hashlib.sha256(frozen.read_bytes()).hexdigest()
    _, mapping_path = prepare_grouped(
        frozen, tmp_path / "prepared", expected_manifest_sha256=frozen_hash)
    mapping = json.loads(mapping_path.read_text())
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    rows = []
    for index, file in enumerate(mapping["files"]):
        extracted = extract_docx(Path(file["path"]), TranslationSettings())
        blocks = [{"id": block.id, "order": block.order, "kind": "paragraph", "source_text": block.text,
                   "translated_text": "candidate"} for block in extracted.blocks]
        job_id = f"{index + 1:032x}"
        result = {"job_id": job_id, "status": "succeeded", "kind": "file",
                  "source_lang": file["source_lang"], "target_lang": file["target_lang"],
                  "translated_text": "\n".join(block["translated_text"] for block in blocks),
                  "blocks": blocks}
        if index == 0:
            result[field] = wrong
        name = f"{index:04d}.json"
        (results_dir / name).write_text(json.dumps(result))
        rows.append({"path": file["path"], "status": "succeeded",
                     "job_id": job_id, "output_json": name})
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps({"entries": rows}))
    with pytest.raises(ValueError, match="result envelope mismatch"):
        map_results(mapping_path, batch, results_dir, tmp_path / "candidate.jsonl",
                    expected_manifest_sha256=frozen_hash)


def test_changed_frozen_corpus_or_mapping_digest_rejected(tmp_path):
    frozen = tmp_path / "frozen.jsonl"
    _frozen_fixture(frozen)
    expected = hashlib.sha256(frozen.read_bytes()).hexdigest()
    _, mapping_path = prepare_grouped(
        frozen, tmp_path / "prepared", expected_manifest_sha256=expected)
    changed = tmp_path / "changed.jsonl"
    changed.write_text(frozen.read_text().replace("Source sentence 00", "Modified sentence 00"))
    with pytest.raises(ValueError, match="approved frozen manifest digest"):
        prepare_grouped(changed, tmp_path / "changed-prep",
                        expected_manifest_sha256=expected)
    mapping = json.loads(mapping_path.read_text())
    mapping["manifest_sha256"] = "0" * 64
    mapping_path.write_text(json.dumps(mapping))
    with pytest.raises(ValueError, match="approved frozen manifest digest"):
        map_results(mapping_path, tmp_path / "missing-batch.json", tmp_path,
                    tmp_path / "candidate.jsonl", expected_manifest_sha256=expected)
