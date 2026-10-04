# Translation specialist — functional MVP addendum

2026-10-04; implementation base `147bd66`, branch `feature/translation-specialist-agent`. This is acceptance design, not evidence of PASS.

## Scope and precedence

Latest user scope: deliver a functional backend multi-agent MVP now; optimize quality later. DuyTa orchestrates and a dedicated translation model with **fewer than 4,000,000,000 total parameters** translates. Tencent Hy-MT2-1.8B is a candidate pending runtime verification, not accepted solely by model name. OCR remains a separate service on existing Vast. No frontend, new rental or incumbent restart/reconfiguration.

This addendum overrides the [v2 contract](../api/translate-v2-contract.md) only for specialist routing instead of DuyTa translation, the explicitly unsupported OCR-edit fallback below, and evidence breadth for **FUNCTIONAL_MVP_PASS**. Public APIs/schemas/SSE, legacy profiles, security, extraction and lifecycle behavior otherwise remain unchanged. Public job APIs require no login/bearer/user ID; internal service authentication does not change this.

Expanded corpora, three-round certification, qualified semantic review of every direction, per-language OCR accuracy/F1 certification and broad quality/latency optimization are **DEFERRED — NOT PASS**. Preserve all historical results in [import criteria](import-file-translate.md) and [OCR criteria](image-ocr-translate.md); never relabel a past FAIL or delete difficult samples. Unresolved known Critical/High findings, including dangerous semantic defects, remain blockers. Functional MVP is not full original acceptance.

Temporary feature-branch composition of import `147bd66` and OCR `ff311c1`, preserving newer literal helpers and lifecycle fixes, is within implementation scope. It is not an official integration/main merge. Record the actual composition SHA in evidence.

## Internal HTTP contract

One trusted configured URL over loopback/tunnel or private TLS. `Authorization: Bearer <service-token>` is mandatory; no caller-supplied URL, model, prompts, tools or decoding settings. Keep AIRGAP/allowlists; no runtime Internet/model fetch.

`POST /v1/translate`, JSON:

```json
{"text":"The update fixes CVE-2024-1234.","source_lang":"en","target_lang":"vi"}
```

HTTP 200 JSON:

```json
{"translated_text":"Bản cập nhật khắc phục CVE-2024-1234.","model":"actual-configured-model-identifier"}
```

- Required request fields exactly as above; reject unknown fields. Nonempty `text`, at most 6,000 Unicode code points. Exact language codes: `vi,en,zh,ja,ko,fr,de,es,ru,ar`; support vi↔each other language and identity. No internal `auto`: gateway resolves it through its existing bounded local detector; unresolved detection fails as before. Public requested source remains `auto`. Explicit source bypasses detection.
- Wire request cap 128 KiB; decoded response-body cap 256 KiB, enforced incrementally before full buffering/parsing. Keep existing per-chunk provider context/output-reserve limits, using the selected model tokenizer/config. Identity/literal-only text may bypass inference unchanged.
- Validate nonempty string `translated_text` for prose and nonempty string `model` identifying the actual service model. No raw backend payload, analysis, prompts, traces or credentials. Reject malformed response types, never coerce them into success.
- Safe errors: `{"error":{"code":"...","message":"..."}}`. Invalid JSON/fields: 400 `INVALID_REQUEST`; language/pair: 400 `INVALID_LANGUAGE` / `UNSUPPORTED_LANGUAGE_PAIR`; request size: 413 `REQUEST_TOO_LARGE`; missing/wrong bearer: 401 `UNAUTHORIZED`; saturation: 429 `BUSY`; backend unavailable: 503 `TRANSLATION_UNAVAILABLE`; deadline: 504 `PROCESSING_TIMEOUT`; invalid/empty/truncated generation: 502 `INVALID_TRANSLATION`.
- Adapter checks backend finish reason/completion evidence. Token cutoff, context overflow, malformed generation or absent required completion evidence fails closed, never a truncated HTTP 200. Error messages contain no source, URL, credentials or exception details. No implicit DuyTa translation fallback.
- Gateway maps empty prose to `EMPTY_TRANSLATION`, deadline to `PROCESSING_TIMEOUT`, token mutation to `TOKEN_INTEGRITY_FAILED`, other auth/transport/schema errors to `TRANSLATION_UNAVAILABLE`. Existing post-202 job failure semantics remain; no partial public results. At most one retry for transient failure or integrity rejection, within the original deadline; no authentication/input retry.

Use a dedicated async structured client accepting `text/source_lang/target_lang`; never parse legacy prompt prose for routing. DuyTa's registered callable translation specialist uses the same client and token protections: DuyTa delegates and returns validated specialist output, without performing translation itself. Preserve legacy translate behavior. No session/user history in transformation calls.

Gateway retains chunking, protected-span masking/restoration and exact occurrence/position validation. Missing/duplicated/altered placeholders or literals fail closed after bounded retry, with no silent repair/drop. Preserve CJK, combining marks and Arabic logical order. Record model/tokenizer revision and actual context/output reserve.

## Truthful unsupported OCR-edit fallback

A translation-only specialist need not support structured correction. When editing is explicitly configured as unsupported: skip correction inference, retain original OCR blocks/raw text unchanged, set `corrected_text == raw_text`, `edits=[]`, `needs_review=true`, and emit existing implementation warning **`OCR_CORRECTION_REJECTED`** explaining that automatic correction is unavailable. Translate unchanged OCR text through the specialist. This is not corrected-quality evidence or OCR-09 PASS.

If correction is configured as supported, preserve strict edit validation and existing raw fallback for invalid proposals. A supported correction-stage outage/timeout still fails under the existing contract; do not disguise it as unsupported mode. No hidden DuyTa fallback, fabricated edits or confidence.

## Finite acceptance

Evidence row: `ID | command/sample | expected | actual | evidence | SHA | PASS/FAIL/BLOCKED | severity`. Missing execution is BLOCKED; observed violation is FAIL. **FUNCTIONAL_MVP_PASS** requires all rows PASS and zero unresolved known Critical/High findings. Preserve full original quality verdict separately.

| ID | Required check |
|---|---|
| MVP-01 | Verify served model ID/revision, total parameter count <4B, code/weights license, runtime/tokenizer configuration and completion detection. One real authorized translation succeeds; missing/wrong internal bearer fails safely. Health alone is insufficient. |
| MVP-02 | Unit tests demonstrate structured specialist calls from file/image jobs without DuyTa transformation. One real registered-tool invocation through DuyTa reaches the specialist and returns its validated translation; no prompt parsing or history persistence. |
| MVP-03 | Live client smoke: one frozen nonempty synthetic prose fixture per each of 18 vi↔en/zh/ja/ko/fr/de/es/ru/ar directions, each containing an annotated protected literal. Record outputs/errors/model/revision. Every row has complete nonempty output, exact literal occurrences and no evident wrong target script, whole-source nontranslation or prompt leakage. Semantic adequacy remains NOT CERTIFIED when unreviewed. Add one identity, one auto→supported direction, one unresolved-auto failure and one rejected non-vi cross-pair. |
| MVP-04 | Real anonymous multipart HTTP upload→poll→result for one TXT, one DOCX and one PNG/JPEG, each with two ordered blocks/lines and a technical literal. Use real specialist and real image OCR. Verify order/join/provenance, literal occurrences, and validated correction or explicit raw fallback. Existing tests retain DOC and second image-format coverage. This is functional E2E, not broad format/quality certification. |
| MVP-05 | Focused tests: timeout, cancellation queued/in-flight, 401/429/503, malformed/empty/oversized responses, truncation and retry exhaustion. Verify retry cap, no auth/input retry, no partial success, cancellation/fencing race and no success after committed cancelling. |
| MVP-06 | Preserve 120s call limit, 30min admission deadline, <=2 jobs/4 model calls/1 OCR call, chunk/body limits and existing cancellation bounds. Async HTTP stays nonblocking. Live run records translator/shared GPU allocation and incumbent LLM/embedding/reranker functional probes; no OOM/disruption. Retain OCR allocation/reserve guards and explicitly measure added translator cost; missing capacity blocks deployment. |
| MVP-07 | Token regression covers repeated/adjacent literals, placeholder collision/mutation, truncation, CJK, combining characters, Arabic with LTR literals. Missing/extra/altered protected occurrences fail closed. Live smoke/E2E also preserves annotated occurrences exactly; distinguish OCR recognition errors from downstream preservation. |
| MVP-08 | Synthetic canary scan of gateway/worker/service/backend/proxy logs finds no source/output text, filename, job IDs, idempotency keys, bearer or internal prompts. Errors sanitized, no raw model logging. Existing no-store, TTL/cleanup, capability and injection tests pass. Specialist cannot execute tools or fetch caller URLs. |
| MVP-09 | Run relevant translation/job/OCR/tool tests then full backend suite to completion; compare baseline/new failures by exact node ID and explain dependency skips without calling failed suite PASS. Check legacy OpenAPI plus translate/translate-stream and both chat JSON/SSE regressions; no legacy API/schema/auth/profile changes. `git diff --check` passes. |
| MVP-10 | Independent final-SHA review maps evidence and unresolved findings, reports functional MVP verdict separately from original quality verdict, and lists deferred criteria/historical FAILs. No extra expanded corpus or redundant stage process is required by this addendum. |

Reuse existing fixtures/harnesses. Pin smoke fixture hashes before execution; record exact replay commands and all failed attempts. A new-SHA rerun can prove a fix without erasing the original failure. Commit only requested safe synthetic evidence, never runtime credentials or user content. Authoring this document does not claim deployment, merge or acceptance success.
