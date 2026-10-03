# Multilang overlap preflight

Base main: `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`. Feature code not written at this checkpoint.

## feature/import-file-translate
- Snapshot `365e9e097aa49f57963efe9d994d3982a36fa6b2`; merge-base `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`; main merge-tree exit `0`.
- Merge output: `79e81d6c9d000a815aa7009f4649f160fe8ba90b`
- Protected changed files:

```text
secai/secai-agent-gateway/deploy/translate-v2/Dockerfile.converter
secai/secai-agent-gateway/deploy/translate-v2/convert.sh
secai/secai-agent-gateway/deploy/translate-v2/worker.py
secai/secai-agent-gateway/docs/api/translate-v2-contract.md
secai/secai-agent-gateway/docs/criteria/image-ocr-translate.md
secai/secai-agent-gateway/docs/criteria/import-file-translate.md
secai/secai-agent-gateway/docs/plans/translate-import-ocr.md
secai/secai-agent-gateway/docs/reviews/translate-plan-review.md
secai/secai-agent-gateway/pyproject.toml
secai/secai-agent-gateway/src/secai_gateway/agent/runner.py
secai/secai-agent-gateway/src/secai_gateway/api/middleware.py
secai/secai-agent-gateway/src/secai_gateway/api/translate_v2/__init__.py
secai/secai-agent-gateway/src/secai_gateway/api/translate_v2/routes.py
secai/secai-agent-gateway/src/secai_gateway/app.py
secai/secai-agent-gateway/src/secai_gateway/prompts/profiles.yaml
secai/secai-agent-gateway/src/secai_gateway/prompts/translate_v2.md
secai/secai-agent-gateway/src/secai_gateway/translation/__init__.py
secai/secai-agent-gateway/src/secai_gateway/translation/documents.py
secai/secai-agent-gateway/src/secai_gateway/translation/jobs.py
secai/secai-agent-gateway/src/secai_gateway/translation/service.py
secai/secai-agent-gateway/src/secai_gateway/translation/settings.py
secai/secai-agent-gateway/src/secai_gateway/translation/technical_tokens.py
secai/secai-agent-gateway/src/secai_gateway/translation/types.py
secai/secai-agent-gateway/src/secai_gateway/translation/worker.py
secai/secai-agent-gateway/tests/fixtures/translate_v2/basic.doc
secai/secai-agent-gateway/tests/test_translate_v2_boundary.py
secai/secai-agent-gateway/tests/test_translate_v2_doc_converter.py
secai/secai-agent-gateway/tests/test_translate_v2_documents.py
secai/secai-agent-gateway/tests/test_translate_v2_job_access.py
secai/secai-agent-gateway/tests/test_translate_v2_jobs.py
secai/secai-agent-gateway/tests/test_translate_v2_language.py
secai/secai-agent-gateway/tests/test_translate_v2_ocr_tokens.py
secai/secai-agent-gateway/tests/test_translate_v2_service.py
secai/secai-agent-gateway/tests/test_translate_v2_worker.py
secai/secai-agent-gateway/uv.lock
```

## feature/image-ocr-translate
- Snapshot `99cd0a2a02e0e687ddb84013ab3e755a773d64b4`; merge-base `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`; main merge-tree exit `0`.
- Merge output: `3fa3b5dcc375ccd26ef8b63febec96a871e67639`
- Protected changed files:

```text
secai/secai-agent-gateway/deploy/ocr/README.md
secai/secai-agent-gateway/deploy/ocr/server.py
secai/secai-agent-gateway/docs/api/translate-v2-contract.md
secai/secai-agent-gateway/docs/benchmarks/ocr-model-selection.md
secai/secai-agent-gateway/docs/criteria/image-ocr-translate.md
secai/secai-agent-gateway/docs/criteria/import-file-translate.md
secai/secai-agent-gateway/docs/plans/translate-import-ocr.md
secai/secai-agent-gateway/docs/reviews/translate-plan-review.md
secai/secai-agent-gateway/evals/translation_ocr/append_languages.py
secai/secai-agent-gateway/evals/translation_ocr/build_fixtures.py
secai/secai-agent-gateway/evals/translation_ocr/images/01.png
secai/secai-agent-gateway/evals/translation_ocr/images/02.png
secai/secai-agent-gateway/evals/translation_ocr/images/03.png
secai/secai-agent-gateway/evals/translation_ocr/images/04.png
secai/secai-agent-gateway/evals/translation_ocr/images/05.png
secai/secai-agent-gateway/evals/translation_ocr/images/06.png
secai/secai-agent-gateway/evals/translation_ocr/images/07.png
secai/secai-agent-gateway/evals/translation_ocr/images/08.png
secai/secai-agent-gateway/evals/translation_ocr/images/09.png
secai/secai-agent-gateway/evals/translation_ocr/images/10.png
secai/secai-agent-gateway/evals/translation_ocr/images/11.png
secai/secai-agent-gateway/evals/translation_ocr/images/12.png
secai/secai-agent-gateway/evals/translation_ocr/images/13.png
secai/secai-agent-gateway/evals/translation_ocr/images/14.png
secai/secai-agent-gateway/evals/translation_ocr/images/15.png
secai/secai-agent-gateway/evals/translation_ocr/images/16.png
secai/secai-agent-gateway/evals/translation_ocr/images/17.png
secai/secai-agent-gateway/evals/translation_ocr/images/18.png
secai/secai-agent-gateway/evals/translation_ocr/images/19.png
secai/secai-agent-gateway/evals/translation_ocr/images/20.png
secai/secai-agent-gateway/evals/translation_ocr/images/21.png
secai/secai-agent-gateway/evals/translation_ocr/images/22.png
secai/secai-agent-gateway/evals/translation_ocr/images/23.png
secai/secai-agent-gateway/evals/translation_ocr/images/24.png
secai/secai-agent-gateway/evals/translation_ocr/images/25.png
secai/secai-agent-gateway/evals/translation_ocr/images/26.png
secai/secai-agent-gateway/evals/translation_ocr/images/27.png
secai/secai-agent-gateway/evals/translation_ocr/images/28.png
secai/secai-agent-gateway/evals/translation_ocr/images/29.png
secai/secai-agent-gateway/evals/translation_ocr/images/30.png
secai/secai-agent-gateway/evals/translation_ocr/images/31.png
secai/secai-agent-gateway/evals/translation_ocr/images/32.png
secai/secai-agent-gateway/evals/translation_ocr/images/33.png
secai/secai-agent-gateway/evals/translation_ocr/images/34.png
secai/secai-agent-gateway/evals/translation_ocr/images/35.png
secai/secai-agent-gateway/evals/translation_ocr/images/36.png
secai/secai-agent-gateway/evals/translation_ocr/images/37.png
secai/secai-agent-gateway/evals/translation_ocr/images/38.png
secai/secai-agent-gateway/evals/translation_ocr/images/39.png
secai/secai-agent-gateway/evals/translation_ocr/images/40.png
secai/secai-agent-gateway/evals/translation_ocr/images/41.png
secai/secai-agent-gateway/evals/translation_ocr/images/42.png
secai/secai-agent-gateway/evals/translation_ocr/images/43.png
secai/secai-agent-gateway/evals/translation_ocr/images/44.png
secai/secai-agent-gateway/evals/translation_ocr/images/45.png
secai/secai-agent-gateway/evals/translation_ocr/images/46.png
secai/secai-agent-gateway/evals/translation_ocr/images/47.png
secai/secai-agent-gateway/evals/translation_ocr/images/48.png
secai/secai-agent-gateway/evals/translation_ocr/images/49.png
secai/secai-agent-gateway/evals/translation_ocr/images/50.png
secai/secai-agent-gateway/evals/translation_ocr/images/51.png
secai/secai-agent-gateway/evals/translation_ocr/images/52.png
secai/secai-agent-gateway/evals/translation_ocr/images/53.png
secai/secai-agent-gateway/evals/translation_ocr/images/54.png
secai/secai-agent-gateway/evals/translation_ocr/images/55.png
secai/secai-agent-gateway/evals/translation_ocr/images/56.png
secai/secai-agent-gateway/evals/translation_ocr/images/57.png
secai/secai-agent-gateway/evals/translation_ocr/images/58.png
secai/secai-agent-gateway/evals/translation_ocr/images/59.png
secai/secai-agent-gateway/evals/translation_ocr/images/60.png
secai/secai-agent-gateway/evals/translation_ocr/images/61.png
secai/secai-agent-gateway/evals/translation_ocr/images/62.png
secai/secai-agent-gateway/evals/translation_ocr/images/blank.png
secai/secai-agent-gateway/evals/translation_ocr/manifest.json
secai/secai-agent-gateway/evals/translation_ocr/prepare_corpus.py
secai/secai-agent-gateway/evals/translation_ocr/results/paddle-62/round-0.jsonl
secai/secai-agent-gateway/evals/translation_ocr/results/paddle-62/round-1.jsonl
secai/secai-agent-gateway/evals/translation_ocr/results/paddle-62/round-2.jsonl
secai/secai-agent-gateway/evals/translation_ocr/results/paddle/round-0.jsonl
secai/secai-agent-gateway/evals/translation_ocr/results/paddle/round-1.jsonl
secai/secai-agent-gateway/evals/translation_ocr/results/paddle/round-2.jsonl
secai/secai-agent-gateway/evals/translation_ocr/run_correction.py
secai/secai-agent-gateway/evals/translation_ocr/run_local.py
secai/secai-agent-gateway/evals/translation_ocr/score.py
secai/secai-agent-gateway/src/secai_gateway/agent/runner.py
secai/secai-agent-gateway/src/secai_gateway/prompts/ocr_correction.md
secai/secai-agent-gateway/src/secai_gateway/prompts/profiles.yaml
secai/secai-agent-gateway/src/secai_gateway/prompts/translate_v2.md
secai/secai-agent-gateway/src/secai_gateway/translation/__init__.py
secai/secai-agent-gateway/src/secai_gateway/translation/ocr_client.py
secai/secai-agent-gateway/src/secai_gateway/translation/ocr_correction.py
secai/secai-agent-gateway/src/secai_gateway/translation/ocr_preprocess.py
secai/secai-agent-gateway/src/secai_gateway/translation/settings.py
secai/secai-agent-gateway/src/secai_gateway/translation/technical_tokens.py
secai/secai-agent-gateway/src/secai_gateway/translation/types.py
secai/secai-agent-gateway/tests/test_translate_v2_boundary.py
secai/secai-agent-gateway/tests/test_translate_v2_ocr_client.py
secai/secai-agent-gateway/tests/test_translate_v2_ocr_correction.py
secai/secai-agent-gateway/tests/test_translate_v2_ocr_preprocess.py
secai/secai-agent-gateway/tests/test_translate_v2_ocr_score.py
secai/secai-agent-gateway/tests/test_translate_v2_ocr_tokens.py
```

## Ownership decision

New code exclusively `src/secai_gateway/multilang/`, `api/multilang/`, `multilang_app.py`, `tests/multilang*`, `evals/multilang/`, `deploy/multilang/`, dedicated multilang docs and isolated nested FE. Shared legacy paths unchanged. File/OCR branch snapshots refresh before every integration cycle. Textual merge success is not runtime compatibility evidence.

## Existing cross-branch conflicts before multilang code

`git merge-tree --write-tree feature/import-file-translate feature/image-ocr-translate` returned1 at import365e9e0/OCR99cd0a2. Conflicts: `prompts/profiles.yaml` (content), `translation/settings.py` (add/add). These predate this feature; no protected branch modified. Each protected branch can still be tested independently with multilang; full combined runtime evidence requires owner-resolved snapshots or a reviewed disposable-only reconciliation, never silent resolution on their branches.
