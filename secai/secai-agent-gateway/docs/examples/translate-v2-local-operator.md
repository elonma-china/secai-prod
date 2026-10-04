# Local translation v2 operator handoff

This launcher starts one loopback API process and one standalone job worker sharing a private SQLite/artifact store. It uses the existing Linch `translate_v2` and `ocr_correction` profiles with the configured DuyTa translation endpoint. See [the public contract](../api/translate-v2-contract.md) for complete request, result, error, retention and limit definitions. This is a local evaluation setup; current functional and quality evidence is in [the backend checkpoint](../test-results/file-ocr-stability-backend.md).

Copy [`translate-v2-local.env.example`](translate-v2-local.env.example) to a private path outside the repository and fill the existing `QWEN_BASE_URL`, `QWEN_MODEL` and `QWEN_API_KEY`. Image admission additionally needs the private `TRANSLATE_V2_OCR_BASE_URL`, `TRANSLATE_V2_OCR_MODEL` and `TRANSLATE_V2_OCR_API_KEY` of a running OCR service. Keep the file readable only by the local operator. The launcher overrides only its child process environment; it does not edit the original `.env` or restart other services.

From the gateway directory, with its dependencies installed in the active Python environment:

```sh
python scripts/run_translate_v2_local.py \
  --env-file /private/path/translate-v2.env \
  --store-dir /private/path/translate-v2-store \
  --port 18089
```

The launcher binds `127.0.0.1:18089`, creates the store with mode 0700, and sets in-memory sessions and no Redis for its children. Stop its PID with SIGTERM or press Ctrl-C; it terminates both child processes. Use an unused port and private store path for each isolated run. Do not put provider credentials on the command line or in a tracked file.

If another process holds the SQLite database locked during first startup, WAL initialization retries a finite 20 times. With the configured 30-second SQLite waits, persistent contention can delay startup for roughly 10 minutes before an error; check that no other owner is holding the private store rather than repeatedly launching more workers.

The tested public path is multipart admission followed by job polling and result lookup. For example, in a separate shell, use a file you are authorized to send to the configured provider:

```sh
base_url=http://127.0.0.1:18089
job_id=$(curl -fsS -F 'file=@/private/path/sample.txt' \
  -F 'source_lang=en' -F 'target_lang=vi' \
  "$base_url/ai/v2/translate/files" | python -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
curl -fsS "$base_url/ai/v2/translate/jobs/$job_id"
curl -fsS "$base_url/ai/v2/translate/jobs/$job_id/result"
```

Poll the job URL until `status` is `succeeded`, `failed` or `cancelled` before fetching the result. A failed job's code appears in its status; the result endpoint returns 409. For images, send PNG or JPEG to `/ai/v2/translate/images` with the same fields, only when the OCR service is configured and ready. DELETE `/ai/v2/translate/jobs/{job_id}` requests cancellation of an active job. Job IDs are bearer capabilities: keep them out of shared logs and reports.

Local checks have exercised TXT, DOCX, DOC, PNG and JPEG through admission, the worker and result retrieval. The PNG/JPEG checks were one-line synthetic images on an interim OCR service. One DOC run echoed a source footer rather than translating it; broader OCR and translation quality gates remain open. The frozen backend full suite had seven inherited external-fixture failures, so neither the suite nor feature acceptance is marked passing.
