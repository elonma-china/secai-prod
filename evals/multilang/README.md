# Multilang assessment harness

This directory contains CPU validation/scoring and a serial runner for the existing Vast DuyTa translator. It does not ship corpus gold, a judge model, or a production route. Live model work remains blocked until a coordinated GPU reservation, corpus and attested configuration exist; the OCR task has stopped its own helper, and all three auxiliary model trials are used.

Use a private virtual environment. Install the repository dev extra, pytest-timeout and the optional Redis extra needed by existing test fixtures; install the pinned, hash-locked metric dependency from requirements.lock. No dependency is added to the shared pyproject.

    uv venv .venv
    uv pip install --python .venv/bin/python -e '.[dev]' pytest-timeout redis==8.0.1
    uv pip install --python .venv/bin/python --require-hashes -r evals/multilang/requirements.lock
    .venv/bin/python -m pytest -q tests/multilang_assessment

Corpus JSONL rows require id, direction, source, reference, split, stratum, protected_spans, source_revision, license, source_group and **aligned_item_id** from the trusted source dataset. A live row also requires data_classification to be public or anonymized. Protected spans are nonoverlapping source-codepoint offsets with exact text and kind. The validator rejects cross-split document groups, cross-language/reversed rows bearing the same aligned item ID in different splits, normalized duplicates under different IDs, and near duplicates. The minimum quota counts distinct source groups per direction; 30 rows from one group are insufficient. The ID is still supplied data, so independent tester custody and source provenance remain necessary. The validator requires all 18 directions by default:

    .venv/bin/python -m evals.multilang.validate_corpus corpus.jsonl --min-per-direction 30

References must come from an independently curated, licensed revision. The FLORES+ registry in source-registry.json is metadata only; access conditions have not been accepted here, so there is no data file to run. Keep all directions/reversals of a source document in one split. Keep devtest in tester custody as sealed TEST. Add independently reviewed security-domain references; no candidate translation becomes gold.

The runner has model-only, legacy-api and multilang-api modes. It schedules all 18 directions, en/vi controls, and the 16 new directions respectively. Without --live it prints a plan and makes no network call or result file:

    .venv/bin/python -m evals.multilang.run --manifest corpus.jsonl --mode model-only --split DEV --rounds 1 --output dev-results.jsonl

Live execution additionally requires a frozen inventory, an active shared lease released by OCR, bounded requests/rate/time/worst-case cost, a new output path, and a local file lock. Model-only requires frozen prompt and decoding files, probes /v1/models, and accepts only the verified incumbent DuyTa endpoint. Decoding accepts only finite temperature/top_p, bounded max_tokens and optional seed; it cannot replace model, messages, stream or add tools. API modes require an independent gateway-model attestation in their inventory. The API key, if required, comes only from MULTILANG_ASSESSMENT_API_KEY in process environment and is never written to results. Before every request the runner rereads the lease after rate-limit sleep; the async HTTP probe and POST are bounded by an absolute wall deadline no later than the remaining lease time. **TEST live execution is deliberately blocked** until an independent tester-custody counter and holdout-retirement record exist; seal hashes alone cannot prevent reuse. These are local admission checks and do not replace the resource owner's approval/scheduling. No assessment live call has been made.

Score with a separate JSONL of blind review annotations:

    .venv/bin/python -m evals.multilang.score --manifest corpus.jsonl --results dev-results.jsonl --annotations reviews.jsonl --mode model-only --phase DEV --rounds 3 --output dev-score.json

Each annotation uses sample_id, round, **output_sha256 of the exact validated output**, decision (pass/fail/uncertain), reviewer, blind=true, calibration_id, off_target, and a rubric object with boolean target_variant, meaning_preserved, no_hallucination, readable, register_appropriate and literals_preserved. Stale/missing/uncertain reviews cannot pass; a self-declared reviewer or calibration ID is not verified provenance, so even a threshold PASS remains `quality_gate=BLOCKED`. The scorer matches result data hash, model ID/revision and prompt/config hashes, counts missing/error rows in every scheduled denominator, reports 30 distinct source groups per direction/round, Wilson intervals based on unique source groups, protected literal mismatches per sample (hashes rather than raw values), chrF++/BLEU signatures, heuristic script/copy/repetition/preamble/truncation rates and review-derived hallucination rate with coverage, usage/cost coverage, and latency/throughput. Unsupported protected kinds are `entity_gate=BLOCKED`. The release gate remains BLOCKED until independent corpus/reviewer, en/vi non-inferiority margin, sealed TEST, security and integration gates are satisfied.

For en↔vi paired descriptive evidence, supply the legacy-api control results and output-bound reviews alongside the model-only candidate:

    .venv/bin/python -m evals.multilang.score --manifest corpus.jsonl --results candidate.jsonl --annotations candidate-reviews.jsonl --baseline-results legacy.jsonl --baseline-annotations legacy-reviews.jsonl --mode model-only --phase DEV --rounds 3 --output paired-report.json

The paired bootstrap resamples source-document groups with 10,000 fixed-seed replicates. It requires both en↔vi directions and complete result/review pairs; its non-inferiority gate remains `PENDING_BASELINE_MARGIN`. The scorer does not infer a margin or production PASS from a descriptive interval.

The scorer never calls the live model. It does not claim that chrF++ or BLEU is a 70% quality pass rate, or that declared reviewer IDs prove qualified independent evaluation.
