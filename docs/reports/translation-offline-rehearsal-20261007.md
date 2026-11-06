# Diễn tập harness dịch hoàn toàn offline — 2026-10-07

Đây là recipe tái lập kiểm tra validator, dry plan và scorer bằng hai mẫu **synthetic**, không phải baseline chất lượng ngôn ngữ. Source/reference cố ý không phải cặp gold; output và metadata model/config được dựng bằng code. Không có inference, network, GPU, người chấm hay corpus thật. Chỉ dùng harness đã có, không cần thêm dependency hoặc sửa product code.

Chạy từ project root với `.venv/bin/python` đã có dependency dự án. `sacrebleu` là diagnostic tùy chọn: thiếu thư viện thì scorer báo `MISSING_DEPENDENCY`; không cài/download trong recipe. Không dùng recipe này cho quyết định nghiệm thu; tiêu chí thật vẫn là [multilang assessment](../criteria/multilang-assessment.md), evidence còn thiếu xem [baseline readiness](translation-baseline-readiness-20261007.md).

Lưu block sau thành shell script hoặc chạy nguyên block. Có thể chọn thư mục scratch mới bằng `OFFLINE_REHEARSAL_DIR=/tmp/my-new-rehearsal sh /tmp/rehearsal.sh`. Thư mục phải chưa tồn tại; mọi artifact chỉ nằm trong đó. `mkdir` không dùng `-p`, vì vậy chạy lại cùng đường dẫn dừng trước khi ghi đè. Để replay, chọn thư mục mới hoặc bỏ biến để mỗi lần dùng `mktemp -d`. Không xoá evidence lượt trước.

```sh
(
set -eu
umask 077
if [ -n "${OFFLINE_REHEARSAL_DIR:-}" ]; then
    mkdir "$OFFLINE_REHEARSAL_DIR"
else
    OFFLINE_REHEARSAL_DIR=$(mktemp -d /tmp/secai-translation-offline-20261007.XXXXXX)
fi
export OFFLINE_REHEARSAL_DIR
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

.venv/bin/python - <<'PY'
import hashlib
import json
import os
from pathlib import Path
import subprocess

from evals.multilang.manifest import canonical_hash

scratch = Path(os.environ["OFFLINE_REHEARSAL_DIR"])
manifest = [
    {
        "id": "synthetic-1", "direction": "vi>ja", "source": "Câu tổng hợp một.",
        "reference": "こんにちは。", "split": "DEV", "stratum": ["general", "short"],
        "protected_spans": [], "source_revision": "synthetic-fixture@1234567",
        "license": "synthetic-fixture-only", "source_group": "synthetic-doc-1",
        "aligned_item_id": "synthetic-doc-1",
    },
    {
        "id": "synthetic-2", "direction": "vi>ja", "source": "Câu tổng hợp hai.",
        "reference": "こんにちは。", "split": "DEV", "stratum": ["general", "short"],
        "protected_spans": [], "source_revision": "synthetic-fixture@1234567",
        "license": "synthetic-fixture-only", "source_group": "synthetic-doc-2",
        "aligned_item_id": "synthetic-doc-2",
    },
]
# Revision/license above are schema placeholders, not corpus provenance/permission.
data_hash = canonical_hash(manifest)
common = {
    "round": 1, "phase": "DEV", "mode": "model-only",
    # Scorer requires this model ID. These rows did not come from that model.
    "model_id": "DuyTa/Cyber-F1-AWQ", "model_revision": "0" * 40,
    "prompt_hash": hashlib.sha256(b"synthetic-prompt").hexdigest(),
    "config_hash": hashlib.sha256(b"synthetic-config").hexdigest(),
    "data_hash": data_hash, "latency_ms": 0.0,
    "input_tokens": None, "output_tokens": None, "cost_usd": None,
}
results = [
    dict(common, sample_id="synthetic-1", raw_output="こんにちは。",
         validated_output="こんにちは。", error=None),
    dict(common, sample_id="synthetic-2", raw_output=None,
         validated_output=None, error="timeout"),
]
for name, rows in (("synthetic-manifest.jsonl", manifest),
                   ("synthetic-results.jsonl", results), ("empty-annotations.jsonl", [])):
    with (scratch / name).open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
metadata = {
    "evidence_kind": "synthetic-offline-harness-rehearsal",
    "live": False, "semantic_quality_assessed": False,
    "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "harness_diff_sha256": hashlib.sha256(subprocess.check_output(
        ["git", "diff", "HEAD", "--", "evals/multilang"])).hexdigest(),
    "data_hash": data_hash,
    "input_file_sha256": {
        name: hashlib.sha256((scratch / name).read_bytes()).hexdigest()
        for name in ("synthetic-manifest.jsonl", "synthetic-results.jsonl", "empty-annotations.jsonl")
    },
}
with (scratch / "rehearsal-metadata.json").open("x", encoding="utf-8") as stream:
    json.dump(metadata, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
PY

.venv/bin/python -m evals.multilang.validate_corpus \
    "$OFFLINE_REHEARSAL_DIR/synthetic-manifest.jsonl" --allow-subset \
    > "$OFFLINE_REHEARSAL_DIR/validator.json"
.venv/bin/python -m evals.multilang.run \
    --manifest "$OFFLINE_REHEARSAL_DIR/synthetic-manifest.jsonl" \
    --mode model-only --split DEV --rounds 3 \
    --output "$OFFLINE_REHEARSAL_DIR/should-not-exist-inference.jsonl" \
    > "$OFFLINE_REHEARSAL_DIR/dry-plan.json"
.venv/bin/python -m evals.multilang.score \
    --manifest "$OFFLINE_REHEARSAL_DIR/synthetic-manifest.jsonl" \
    --results "$OFFLINE_REHEARSAL_DIR/synthetic-results.jsonl" \
    --annotations "$OFFLINE_REHEARSAL_DIR/empty-annotations.jsonl" \
    --mode model-only --phase DEV --rounds 3 \
    --output "$OFFLINE_REHEARSAL_DIR/synthetic-score.json"

.venv/bin/python - <<'PY'
import json
import os
from pathlib import Path

scratch = Path(os.environ["OFFLINE_REHEARSAL_DIR"])
validator = json.loads((scratch / "validator.json").read_text())
plan = json.loads((scratch / "dry-plan.json").read_text())
report = json.loads((scratch / "synthetic-score.json").read_text())
metadata = json.loads((scratch / "rehearsal-metadata.json").read_text())
assert validator["samples"] == validator["unique_sources"] == 2
assert validator["directions"] == ["vi>ja"]
assert validator["acceptance_ready"] is False
assert plan["live"] is False and plan["rounds"] == 3
assert plan["scheduled_requests"] == 6 and plan["unique_sources"] == 2
assert not (scratch / "should-not-exist-inference.jsonl").exists()
assert validator["data_hash"] == plan["data_hash"] == report["data_hash"] == metadata["data_hash"]
direction = report["directions"]["vi>ja"]
rounds = direction["rounds"]
assert direction["scheduled_requests"] == 6 and direction["unique_sources"] == 2
assert set(rounds) == {"1", "2", "3"}
assert all(row["scheduled"] == 2 for row in rounds.values())
assert rounds["1"]["errors"] == 1 and rounds["1"]["missing_results"] == 0
assert rounds["1"]["unreviewed_outputs"] == 1
assert all(rounds[turn]["missing_results"] == 2 for turn in ("2", "3"))
assert sum(row["missing_results"] for row in rounds.values()) == 4
assert all(row["quality_passes"] == 0 and row["quality_gate"] == "BLOCKED"
           and row["off_target_gate"] == "BLOCKED" for row in rounds.values())
assert direction["quality_gate"] == report["release_gate"] == "BLOCKED"
assert rounds["1"]["diagnostics"]["coverage"] == {"scored": 1, "scheduled": 2}
print(json.dumps({
    "evidence_kind": metadata["evidence_kind"], "scratch": str(scratch),
    "live": False, "scheduled": 6, "errors": 1, "missing": 4,
    "unreviewed_outputs": 1, "quality_gate": direction["quality_gate"],
    "release_gate": report["release_gate"],
    "diagnostic_status_round1": rounds["1"]["diagnostics"]["metric_status"],
}, ensure_ascii=False, sort_keys=True))
PY
)
```

Kỳ vọng kiểm tra: validator có `acceptance_ready=false`; dry plan có `live=false`, 6 request theo lịch và không tạo file inference. Scorer cũng dùng 3 lượt: mỗi lượt mẫu số 2, tổng lịch 6; round 1 có một output dựng sẵn chưa chấm và một timeout, round 2–3 thiếu cả bốn output. Timeout và output thiếu được giữ trong mẫu số. Quality và release phải `BLOCKED`; không truyền cờ giảm `--min-samples-per-direction` (mặc định vẫn 30).

`validator.json`, `dry-plan.json`, `synthetic-score.json`, các input và `rehearsal-metadata.json` là artifact riêng cho lượt diễn tập. Metadata ghi Git SHA, hash diff tracked của harness và hash input; nó không attestation runtime/model. Nếu có file harness chưa tracked, cần ghi riêng tên/hash trước khi so sánh lượt chạy. Điểm diagnostic từ reference/output dựng sẵn không phải độ chính xác dịch và không được đưa vào báo cáo chất lượng.

Runner không có `--live` trả về trước phần inventory/reservation/network. Scorer chỉ đọc file local. Scorer mở output bằng chế độ exclusive `x`: gọi lại score trên cùng `synthetic-score.json` phải bị từ chối ghi đè. Recipe dùng thư mục mới để tránh cả việc shell redirect ghi đè report validator/dry plan. Replay có fixture data hash ổn định khi recipe không đổi; code SHA/diff hash có thể thay đổi khi sửa harness, và đường dẫn scratch luôn có thể khác.

Tài liệu này là hướng dẫn; kết quả chạy thực tế do Luna ghi trong báo cáo kiểm chứng. Không coi sự tồn tại của runbook là lệnh đã pass.
