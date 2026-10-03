# Multilang assessment harness

This directory contains CPU validation/scoring and a serial runner for the existing Vast DuyTa translator. It does not ship corpus gold, a judge model, or a production route. Live model work is blocked while file/OCR owns the GPU slot.

Use a private virtual environment. Install the repository dev extra, pytest-timeout and the optional Redis extra needed by existing test fixtures; install the pinned, hash-locked metric dependency from requirements.lock. No dependency is added to the shared pyproject.

    uv venv .venv
    uv pip install --python .venv/bin/python -e '.[dev]' pytest-timeout redis==8.0.1
    uv pip install --python .venv/bin/python --require-hashes -r evals/multilang/requirements.lock
    .venv/bin/python -m pytest -q tests/multilang_assessment

Corpus JSONL rows require id, direction, source, reference, split, stratum, protected_spans, source_revision, license and source_group. A live row also requires data_classification to be public or anonymized. Protected spans are nonoverlapping source-codepoint offsets with exact text and kind. The validator rejects cross-split document groups, normalized duplicates and near duplicates. It requires all 18 directions by default:

    .venv/bin/python -m evals.multilang.validate_corpus corpus.jsonl --min-per-direction 30

References must come from an independently curated, licensed revision. The FLORES+ registry in source-registry.json is metadata only; access conditions have not been accepted here, so there is no data file to run. Keep all directions/reversals of a source document in one split. Keep devtest in tester custody as sealed TEST. Add independently reviewed security-domain references; no candidate translation becomes gold.

The runner has model-only, legacy-api and multilang-api modes. It schedules all 18 directions, en/vi controls, and the 16 new directions respectively. Without --live it prints a plan and makes no network call or result file:

    .venv/bin/python -m evals.multilang.run --manifest corpus.jsonl --mode model-only --split DEV --rounds 1 --output dev-results.jsonl

Live execution additionally requires a frozen inventory, an active shared lease released by OCR, bounded requests/rate/time/worst-case cost, a new output path, and a local file lock. Model-only also requires frozen prompt and decoding files, probes /v1/models, and accepts only the already verified DuyTa endpoint. API modes require an independent gateway-model attestation in their inventory. The API key, if required, comes only from MULTILANG_ASSESSMENT_API_KEY in process environment and is never written to results. Before every request the runner rereads the lease; TEST additionally requires exact sealed corpus and candidate config hashes. These checks implement a local admission guard and do not replace the resource owner's approval/scheduling.

Score with a separate JSONL of blind review annotations:

    .venv/bin/python -m evals.multilang.score --manifest corpus.jsonl --results dev-results.jsonl --annotations reviews.jsonl --mode model-only --phase DEV --rounds 3 --output dev-score.json

Each annotation uses sample_id, round, decision (pass/fail/uncertain), reviewer, blind=true, calibration_id, off_target, and a rubric object with boolean target_variant, meaning_preserved, no_hallucination, readable, register_appropriate and literals_preserved. Missing or uncertain reviews cannot pass the quality gate. The scorer matches result data hash, model ID/revision and prompt/config hashes, counts missing/error rows in every scheduled denominator, reports 30-sample per-direction/per-round gates, Wilson intervals based on unique source groups, exact protected occurrences, chrF++/BLEU signatures, usage/cost coverage, and latency/throughput only when evidence permits. The release gate remains BLOCKED until independent corpus/reviewer, en/vi non-inferiority margin, sealed TEST, security and integration gates are satisfied. Paired bootstrap uses source-document groups with 10,000 seeded resamples; its margin status remains PENDING_BASELINE_MARGIN.

The scorer never calls the live model. It does not claim that chrF++ or BLEU is a 70% quality pass rate, or that declared reviewer IDs prove qualified independent evaluation.
