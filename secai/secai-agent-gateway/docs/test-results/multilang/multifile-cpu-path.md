# Provisional multi-file CPU translation path

This branch adds a usable batch client for the existing v2 file-job API. It is a
provisional test path, not approved main or production integration. The gateway
already accepts one TXT/DOCX/DOC per request, gives each file its own durable job,
and exposes status, result and cancellation. The client submits at most two jobs
at once, defaults to one upload every 12 seconds for the existing five uploads
per minute peer limit, polls each job no faster than every four seconds, and
honors `Retry-After` on 429. Each HTTP operation has at most four attempts and
each file has a 35-minute client deadline. Server queue, active-peer and rate
limits are unchanged.

## Use

Run from the gateway directory with the project environment installed and the
v2 gateway plus standalone worker configured. The CLI talks only to the gateway;
the worker uses its configured private translation specialist. Keep the manifest
and output directory private: they contain capability IDs, idempotency keys and
translated content.

```sh
PYTHONPATH=src:. python -m evals.multilang.multifile_batch run \
  --base-url http://127.0.0.1:8080 \
  --manifest /private/batch-manifest.json \
  --output-dir /private/translated \
  --source-lang vi --target-lang zh \
  /private/input-dir

PYTHONPATH=src:. python -m evals.multilang.multifile_batch resume \
  --base-url http://127.0.0.1:8080 \
  --manifest /private/batch-manifest.json \
  --output-dir /private/translated

PYTHONPATH=src:. python -m evals.multilang.multifile_batch cancel \
  --base-url http://127.0.0.1:8080 \
  --manifest /private/batch-manifest.json
```

`run --plan /private/batch-plan.jsonl` replaces the homogeneous language flags
for mixed batches. Each JSONL row needs `path`, `source_lang` and `target_lang`.
Languages use the v2 wire codes, including `zh`, and may use `auto` as source.
One manifest entry records the original path, source/target, file digest,
idempotency key, job ID, status, error code and output names. The client saves
keys before upload, so a restart can replay an accepted POST without creating
a duplicate job. It checks file bytes and language binding before replay. A
failed file is recorded and the rest continue. `cancel` sends DELETE for every
admitted nonterminal job. Stop a running `run`/`resume` process before invoking
`cancel`; an OS lock rejects concurrent writers. If the process stopped while
an upload response was unknown, `cancel` replays its saved key to recover the
job ID before DELETE. Files still marked pending were never submitted and are
cancelled locally. An unresolved cancellation exits nonzero and can be retried.
`run`/`resume` exit nonzero when any file failed or remains interrupted/pending.
The manifest and each output file are written with
mode 0600; the output directory is created with mode 0700. If a directory is
given, the client enumerates supported files recursively, stops at 2,000 inputs
or 10,000 scanned files, and streams one upload at a time. This client does not
reconstruct a DOCX document: it writes the v2 result blocks to `0001.json` and
the translated text to `0001.txt`, with the input-name mapping in the manifest.

## Frozen DEV file preparation

The separate preparation command reads only the frozen FLORES DEV manifest and
does not contact a model. It requires exactly 60 rows in each of 18 directions,
maps `zh-Hans` to the v2 `zh` wire code, and writes one source file per row.
Every fifth file in a direction is DOCX; the rest are TXT. It writes a mixed
batch plan without references or scores.

```sh
PYTHONPATH=src:. python -m evals.multilang.prepare_multifile_dev \
  --manifest /home/ict/secai-multilang-benchmark-corpus/dev/flores101-dev-sample60-manifest.jsonl \
  --output-dir /private/flores-dev-files

PYTHONPATH=src:. python -m evals.multilang.multifile_batch run \
  --base-url http://127.0.0.1:8080 \
  --plan /private/flores-dev-files/batch-plan.jsonl \
  --manifest /private/flores-dev-batch.json \
  --output-dir /private/flores-dev-results
```

The second command must wait for a coordinated, running CPU specialist and
gateway worker. No live model call was made by this change. Functional ASGI
tests use JobStore, Worker and a mocked specialist; they do not measure
translation quality. The user quality gate is at least 22/30 acceptable
translations **in each of 18 directions**, evaluated on real translated output
with an appropriate independent review method. A batch that completes 1,080
files or passes mock tests does not establish that gate. The dedicated CPU
service contract under preparation is POST `/v1/translate` with `text`,
`source_lang`, `target_lang`, returning `translated_text` and the actual model
name. The selected model and pinned revision belong in the live run record;
this client does not choose them. The initial MADLAD CPU candidate was rejected
by the separate quality review, so its output cannot qualify this path.

## Checks recorded on this branch

- `PYTHONPATH=src:. /tmp/secai-multilang-final-env/bin/python -m pytest -q tests/test_multifile_batch.py tests/test_translate_v2_job_access.py tests/test_translate_v2_jobs.py tests/test_translate_v2_worker.py tests/test_translate_v2_documents.py`
  with `POC_CATALOG_DB=/tmp/secai-multifile-test-poc.db SESSION_STORE_BACKEND=memory REDIS_URL=''`:
  58 passed after the second review patch. The batch tests process mixed TXT/DOCX through actual ASGI routes and
  JobStore/Worker for all 18 directions, observes at most two active jobs, checks
  output files, replay after an accepted POST loses every response, one-file
  failure, cancellation of ambiguous and unsubmitted files, lock contention,
  changed input/target rejection, whole-response deadlines, nonzero CLI exit,
  and a complete upload after 429 retry.
- `PYTHONPATH=src:. /tmp/secai-multilang-final-env/bin/python -m evals.multilang.prepare_multifile_dev --manifest /home/ict/secai-multilang-benchmark-corpus/dev/flores101-dev-sample60-manifest.jsonl --output-dir /tmp/secai-multifile-dev-prepared-r3`:
  completed; 60 files in each of 18 directions. This only prepared inputs.
- `git diff --check`: passed.

No quality percentage, live latency, CPU model availability, or production
readiness is claimed here.

## Independent review recovery patch

The independent review of `4edf83f` found three batch recovery defects. The
follow-up patch moves input hashing into the individual row immediately before
a POST. A known job can be polled and exported even if its original file has
since been removed; a changed pending file fails only its own row. On resume,
known jobs are reconciled before uncertain POSTs, then fresh uploads. If an
accepted job's status cannot be established, or a POST may have been accepted
without a response, the client records `interrupted` and stops admitting more
files until a later resume/cancel resolves it. This keeps unresolved remote
work inside the two-job batch budget. Transient POST 5xx responses remain
`interrupted` with the same persisted idempotency key. Validation responses
remain terminal per-file failures. The batch command exits nonzero while any
work is pending, interrupted or failed. These changes address the reported
locations around manifest preflight, scheduling, and POST response handling;
independent re-review is still required.

Round 2 also found that an HTML/empty 503 body could throw during error-code
parsing after the HTTP status had already been classified, changing an
interrupted upload to a failed one and allowing further admissions. The client
now classifies 5xx before optional bounded body parsing; invalid bodies get
`HTTP_503` and the admission halt remains in force. The returned summary now
counts queued/running/cancelling/other entries, reports `total` and
`unfinished`, and exits nonzero if unfinished work remains. Regressions cover
both non-JSON 503 bodies and a halted queue of saved job IDs. Independent
re-review of this second patch remains required.
