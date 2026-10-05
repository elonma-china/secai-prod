# Qwen3 reward pipeline implementation plan

> Execute inline using superpowers:executing-plans; user authorized implementation on 2026-10-06.

**Goal:** Prepare approved preference data, train Qwen3-1.7B scalar scorer offline, evaluate expert agreement, and expose opt-in scoring without changing chat sampling.
**Spec:** ../specs/2026-10-05-qwen3-reward-design.md
**Architecture:** Verified review exports feed a deterministic grouped train/validation/test dataset. Offline Transformers/PyTorch training fits a scalar head (head-only or LoRA). Evaluation and explicit release gates produce a checksum-pinned artifact served separately from the CPU gateway. No model downloads or training on real customer data during technical tests.

## Constraints and review focus

- Keep sampling 0.1, cooldown 600 and Bearer reviewer/admin auth.
- QA/demo artifacts cannot serve production; checksum is integrity, not authenticity.
- Preserve all source-conversation links when deduplicating; never truncate answers silently.
- Pin base revision and dependencies; score head must be saved with adapters.
- No accepted test dataset or release thresholds: no production eligibility.
- Gateway must reject absent service, upstream errors, nonfinite scores and artifact identity mismatches.

## Tasks

- [x] Dataset: tests first for deduplication leakage, three partitions, approval rejection and tampering; implement `prepare_dataset` and immutable dataset checksums in reward/pipeline.py using reward/dataset.py. Production additionally requires explicit trusted expert actor IDs.
- [x] Training: tests first for pairwise gradient and real tiny Qwen3 save/load; implement reward/transformer.py and offline CLI with head/LoRA training, validation metrics and separate sealed-test evaluation. Optional training dependencies stay outside gateway runtime.
- [x] Serving: tests first for release gates, integrity, bearer auth, shape and limits; implement separate reward/service.py and gateway review_reward.py relay (disabled by default).
- [x] Validation: run reward/review tests, tiny architecture smoke, full backend suite; report unrelated failures accurately. Document operator commands and missing production data. No production activation without eligible model.

Validation evidence: ../../test-results/reward-qwen-pipeline-20261006.md. Native implementation with one independent final reviewer; four Important findings fixed with regression tests. No trained production model or runtime activation.
