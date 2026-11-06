# Import translation R2 repair — author evidence

Date: 2026-10-04. Base candidate: `bfab8ebc96e25b23325fd72a1d3bf034e9aea315`; frozen baseline: `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`. This is author-side evidence for the repair branch. Independent code review, TEST, Vanguard, OCR composition, staging performance, and release decisions remain separate.

## R2-01: terminal input lifecycle

The previous store made terminal status durable but only the worker's `reap()` removed input files. A web status read could fail an overdue queued job while leaving its file and storage charge indefinitely if the worker was absent. There was also a direct `cancelling` → `cancelled` update in `claim()` that skipped the shared terminal path.

All terminal transitions now commit the status and fencing first; an idempotent cleanup transaction then unlinks the job's own artifact slot and clears `input_path`/`input_bytes`. A failed unlink retains the storage charge and path for retry. A failed/aborted status transaction never removes an active job's input. Admission runs committed maintenance before quota evaluation. The web lifespan starts a one-second reaper task using `asyncio.to_thread`, so deadlines, cancellation, cleanup and expiry continue with no worker or requests while at least one web process is running. Shutdown cancels/awaits that task. If every web and worker process is stopped, no wall-clock cleanup is possible; the next startup or relevant store operation resumes maintenance. Running cancellation reaches terminal state at 13 seconds in the store, leaving margin for the one-second web tick against the 15-second criterion. Timing under process pressure still needs operational measurement.

| Transition | Fence/cleanup evidence |
|---|---|
| queued → cancelled/failed | Status read or idle web reaper terminalizes; input removed; quota reusable without manual worker reap. |
| running → succeeded/failed | `finish()` commits terminal state before deletion; later generation/lease cannot publish. |
| running → cancelling → cancelled | Worker finish, deadline, or 13-second timeout uses the same terminal path; stale success rejected. |
| terminal → expired/tombstone | Logical 410 applies at TTL; reaper retains terminal row while deletion fails, then moves metadata after cleanup. |
| cleanup interrupted | A committed terminal row with retained path is retried; missing file is treated as already removed, then byte charge clears. |
| rollback | Unknown lookup after overdue scan rolls back status and preserves active input. |

The cleanup path accepts only `artifact_dir / job_id` and never clears quota for an unlink failure. Shared-file/concurrent-process behavior follows SQLite `BEGIN IMMEDIATE`; repeated unlink is safe with `missing_ok=True`. SQLite/file deletion cannot be one atomic transaction, so cleanup is deliberately retried after the terminal commit.

## R2-02: technical literal boundaries

The previous URL/hash/version regexes used Unicode `\b`, which treats adjacent CJK, Arabic and Cyrillic letters as the same word. URLs also consumed adjacent non-ASCII prose when the boundary was relaxed. The repair applies ASCII identifier boundaries to technical patterns and reuses the legacy secret patterns with ASCII regex boundaries in this translation-only helper. URL scanning recognizes ASCII URI bodies beside other scripts, percent-encoded paths and punycode; an explicit separator starts a conservative Unicode host/path IRI span. Ordinary trailing sentence punctuation is excluded. Without a delimiter, the end of a Unicode IRI and adjacent same-script prose is ambiguous; that run remains protected rather than silently changing a possible URL. Source documents should separate such IRIs from prose when exact segmentation matters.

The tests cover two occurrences for CVE, CWE, IP, CIDR, URL, hash, software/version, Unix/Windows path, command/code and secret classes across Chinese, Arabic and Cyrillic neighbors; ASCII identifier substrings remain unmasked. They also cover IDN/Unicode path, IPv6 URL authority brackets, punycode, percent encoding, CJK/Arabic punctuation, placeholder restore, and a fake provider that tries to add a different URL. A real provider language/semantic outcome is not inferred from these deterministic tests.

## Verification and remaining gates

- Focused offline suite: **194 passed** in 10.91 seconds, exit 0. Command selected `tests/test_translate_v2_boundary.py`, `doc_converter.py`, `documents.py`, `job_access.py`, `jobs.py`, `language.py`, `ocr_tokens.py`, `service.py`, `worker.py`, `worker_process.py`, and `tests/test_translate_*.py`. External HTTP was blocked by loopback proxy variables; database, PoC and session paths were private under `/tmp/secai-repair`.
- Full offline suite: **1,071 collected; 1,051 passed, 19 failed, 1 skipped, 0 errors** in 163.14 seconds, exit **1**. Raw JUnit: `/tmp/secai-repair/reports/import-repair-final.full.junit.xml`, SHA-256 `c1a57e9a533ba0a214df18512b8abdd8807085e85d79b9d44d4cf5774a74138a`; stdout: `/tmp/secai-repair/reports/import-repair-final.full.stdout.log`, SHA-256 `21dc4c85e2cd71a25fea61eb7e601dcf93f84e9e9006da539ba40a89e12ca9f0`. These `/tmp` files must be copied before host cleanup if needed for later independent review.
- Parsed JUnit by exact `(classname, name)` identity against `/home/ict/secai-translate-baseline-wt/secai/secai-agent-gateway/docs/test-results/translate-baseline.full-timeout.junit.xml`: all **925** baseline IDs remain, **146** new IDs, **0** base status changes, **0** new failing/skipped IDs; the 19 failure IDs and one skip ID are identical to baseline. The full suite is **not PASS**. Its inherited failures include absent external Bughunt/PoC data and per-test timeouts; this comparison alone does not prove those failures harmless.
- The baseline command used the original project's `.venv` interpreter and a private pytest-timeout target. This run used `/home/ict/secai-translate-import-wt/.venv-import/bin/python` (Python 3.12) and explicit `/home/ict/.local/lib/python3.12/site-packages` in `PYTHONPATH` because its installed dependencies live in the user site under `env -i`. The initial `env -i` attempt without that site path stopped at collection with missing `httpx`; corrected invocation produced the counts above. Thus status comparison is exact by test ID, while dependency environment is not byte-identical.
- `git diff --check` passed before this report. No live LLM, OCR/GPU or production service was called by this repair.

Review-due evidence still requiring independent work includes complete legacy chat JSON/SSE and ownership replay (IMP-01); multipart/parser fixtures and resource measurements (IMP-02–07); actual provider payload/log canary capture and cross-process capability/privacy checks (IMP-09–14); and full multilingual/RTL/language-quality evidence (IMP-18–20). This author report resolves the observed R2 reproductions and documents targeted tests; it does not mark any overall criterion or release gate PASS.
