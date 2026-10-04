# Grouped real-file DEV preparation (provisional)

`prepare_multifile_dev_grouped.py` packages the first 30 aligned groups in the
frozen FLORES DEV manifest as **18 DOCX files**, one direction per file and one
paragraph per sample. This reduces the v2 upload count from 1,080 to 18 while
keeping the required denominator of 30 separate samples in each direction.
Every file is re-extracted with the existing DOCX extractor before the plan is
published; the tool requires exactly 30 paragraph blocks with text identical
to the manifest source. Each file is about 40 KiB in this DEV preparation, well
below the 10 MiB upload limit. The gateway still translates the 30 blocks in
its normal job lifecycle, with its existing per-job deadline and queue bounds.
The tool does not alter a source sentence or embed references in the DOCX.

```sh
POC_CATALOG_DB=/tmp/secai-grouped-prep-poc.db SESSION_STORE_BACKEND=memory REDIS_URL='' \
PYTHONPATH=src:. python -m evals.multilang.prepare_multifile_dev_grouped prepare \
  --manifest /home/ict/secai-multilang-benchmark-corpus/dev/flores101-dev-sample60-manifest.jsonl \
  --output-dir /private/grouped-dev

POC_CATALOG_DB=/tmp/secai-grouped-prep-poc.db SESSION_STORE_BACKEND=memory REDIS_URL='' \
PYTHONPATH=src:. python -m evals.multilang.multifile_batch run \
  --base-url http://127.0.0.1:8080 \
  --plan /private/grouped-dev/batch-plan.jsonl \
  --manifest /private/grouped-batch.json \
  --output-dir /private/grouped-results

POC_CATALOG_DB=/tmp/secai-grouped-prep-poc.db SESSION_STORE_BACKEND=memory REDIS_URL='' \
PYTHONPATH=src:. python -m evals.multilang.prepare_multifile_dev_grouped map-results \
  --mapping /private/grouped-dev/sample-block-map.json \
  --batch-manifest /private/grouped-batch.json \
  --results-dir /private/grouped-results \
  --output /private/grouped-candidates.jsonl
```

The `sample-block-map.json` records the frozen manifest SHA-256, selected group
order, direction, sample ID, source-group ID, expected block index/ID, and
SHA-256 of each exact source paragraph. `map-results` accepts only 18 succeeded
file jobs, verifies every result block ID/order and source hash, and exports 540
candidate rows keyed by sample ID and direction. The mapper requires exactly
18 unique canonical batch paths matching the plan and exactly 18 succeeded
jobs. It binds each result's job ID, succeeded status, file kind, source and
target languages, complete result text and 30 paragraph blocks to that job's
mapping. It omits references; an
independent reviewer can join the candidate rows to trusted DEV references and
judge each direction separately. A missing/changed block fails the export
without producing a partial candidate file. This export is not a quality score.

The approved frozen manifest is pinned in the command with SHA-256
`ad1e0fd7f8068da49bee2255e64a8019ec5a2a840b0347e9403153c612a0f64e`.
Both preparation and result mapping reject a different digest. Only the Python
test fixture supplies its own expected digest; the CLI has no override.
It yielded 18 DOCX files with 30 verified extracted blocks apiece at
`/tmp/secai-multifile-grouped-dev-r2`; no model or API call was made.

Checks: `PYTHONPATH=src:. /tmp/secai-multilang-final-env/bin/python -m pytest -q tests/test_multifile_grouped_preparation.py`
with `POC_CATALOG_DB=/tmp/secai-multifile-test-poc.db SESSION_STORE_BACKEND=memory REDIS_URL=''`:
**10 passed** after the independent review repair. The local CLI preparation
command above also completed. The environment variables in the example make
the standalone command reproducible without a writable `/srv/exploitdb`.
The
real-file quality gate remains at least 22 acceptable translations out of 30
in **each** of 18 directions under independent review. Completion of file jobs
alone does not pass that gate. The separate review of batch client commit
`4edf83f` found recovery/bounding defects. Batch repair `dafa3fc` and the
grouped repair still require independent re-review before live use. The mapping
is local evidence binding, not cryptographic proof of model origin; collection
of real API result files must remain trusted.
