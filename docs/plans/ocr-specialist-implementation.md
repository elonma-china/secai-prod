# OCR Specialist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development task-by-task. SOL implements scoped changes; fresh independent SOL reviews each task. Root integrates after dependency gates. Do not edit another agent's worktree.

**Goal:** DuyTa can request OCR through scoped tools, receive real OCR results or honest pending/error states, and leave translation as a separately evaluated step.

**Architecture:** Add an internal OCR-only kind to the existing durable document queue and expose it through a separate v3 document-agent ingress. A dedicated DuyTa runner receives server-owned per-run dependencies through Linch `RunOptions(deps=...)`; opaque handles bind tool calls to a document context. Reuse the OCR pipeline and lifecycle implementation after their owner's review, preserving public v2 and legacy chat contracts.

**Tech Stack:** Existing Python/FastAPI/Pydantic v2, SQLite, Linch>=1.2.1,<2, existing DuyTa HTTP service and OCR backend; no new model or framework dependency.

**Spec:** [Approved design](ocr-specialist-orchestration.md), approved by user “oke” on2026-10-04. This plan freezes the ingress details left open by the design. Status: approved; CPU implementation in progress.

## Global Constraints

- DuyTa remains orchestrator. OCR and translation quality are separate; alternative translators may be compared only if DuyTa fails, with no automatic production replacement.
- Wait for the current OCR benchmark to complete before model calls/new model loads. An unloaded helper between retries is not completion. Existing three auxiliary trials remain exhausted; no new rental.
- All new features default OFF; no main merge, push, deployment or alteration of running services in this plan.
- Reuse the reviewed file/OCR lifecycle. Do not modify owner worktrees, datasets, raw outputs, review counters or thresholds.
- Public v2 remains anonymous capability API with unchanged upload fields. Internal job IDs never enter model context, public tool results, SSE, history or logs.
- OCR pipeline returns actual raw/corrected text, edits, warnings and needs_review. No invented confidence, coordinates, accuracy or translation.
- Initial document-agent upload support: PNG/JPEG only. Existing text-file extraction remains on the file API; PDF scan remains unsupported. Plain text prompts must not trigger OCR without an image handle.
- Inherit existing image limits:10MiB upload,11MiB request,20,000,000pixels,10,000edge; queue20,active/IP2,storage5GiB,deadline30min,result24h,tombstone24h. New ingress shares admission/rate/storage accounting rather than adding an unlimited upload store.
- Tool preview limit4,000codepoints, prompt limit10,000codepoints, max3Linch turns,1,024output tokens/generation,120s total orchestration turn, at most one image and one OCR submission per turn, no automatic LLM polling. Verify real tokenizer/context budget before provider calls; no fake token counter. Preserve full result in its authorized artifact endpoint.
- Context credential:32CSPRNGbytes rendered64lowercasehex; store only its SHA256. Handle:16CSPRNGbytes rendered32lowercasehex, useless outside its bound context. Context lives at most24h from creation; no refresh through polling.

## Dependency and ownership map

Observed integration base82a442b; import repair147bd666 is **not yet accepted**, OCRff311c1 is an implementation checkpoint, and other quality/runtime repairs are active. Record fresh exact SHAs and owner verdicts before composing them. Existing source/test evidence cannot be relabeled as execution on a newly composed tree.

Create `feature/document-ocr-agent` in a new worktree from integration82a442b for additive scope/tool work. Task2 and real integration wait for accepted file/OCR commits; do not cherry-pick uncommitted WIP. A provisional composition may be used only for explicitly labeled CPU compatibility tests, never acceptance. Stage exact files.

- New `src/secai_gateway/document_agent/{scope,backend,tools,runner,settings}.py`: context/handle binding; narrow JobStore adapter; tool contracts; DuyTa runner; validated OFF-by-default settings.
- New `src/secai_gateway/api/document_agent/{routes,schemas}.py` and `src/secai_gateway/document_app.py`: additive HTTP/ASGI entrypoint, scoped JSON/SSE presentation.
- Shared edits after owner gate: `translation/jobs.py`, `translation/worker.py`, `api/translate_v2/routes.py`, plus a shared image-admission helper only if necessary to reuse validation without copying it.
- New `prompts/document_ocr.md`; additive `profiles.yaml` entry. Reuse the agent factory and untrusted-content wrapper. Existing translation/correction profiles keep tools empty.
- Tests under `tests/document_agent/`; regression targets include original v2/import/OCR, v3translation and both legacy chat variants.

## Review Focus

1. A hallucinated/other-context handle cannot read or cancel another task; test in Task1.
2. Worker restart/cancel during OCR cannot publish a stale success or run translation; test in Task2.
3. Repeated tool calls/retried HTTP turns cannot create duplicate OCR jobs; test in Tasks1–3.
4. A long or hostile OCR result cannot expose capability secrets through JSON/SSE/history or issue new tool actions; test in Tasks3–4.
5. New flags OFF, expired context, and missing OCR dependency have deterministic behavior without breaking legacy startup; test in Tasks4–5.

## Frozen new ingress

Prefix `/ai/v3/document-agent`, dispatched only by the new composition; disabled returns404 without importing OCR optional dependencies.

- `POST /contexts`:201 `{context_token, expires_at}`. No login introduced. Creation is rate-limited by trusted peer IP with the existing5/min upload policy; maximum20unexpired contexts/IP. Responses and errors are `Cache-Control:no-store`; never log credentials.
- Remaining routes require `X-Document-Context` matching a live context. Missing/invalid credential returns401; expired credential410. It is a document resource capability, not proof of user identity; never trust caller-supplied user_id for ownership.
- `POST /turns`: multipart `prompt`, optional `file` (PNG/JPEG only), optional `task_handle`, optional `stream` true/false. Unknown/duplicate fields400. Required `Idempotency-Key`32hex binds exact prompt/file bytes/task handle/stream mode within the context; changed payload409. Image bytes exist only in bounded request memory until tool admission, which uses the durable shared queue. Closing the request before admission frees them; after admission job lifecycle owns input cleanup.
- JSON response200 contains `{answer, tasks:[{task_handle,status,needs_review}], request_id}`. A queued/running task is an honest pending answer, not OCR success. A retried completed turn returns its stored safe response; an already-running duplicate returns409 `TURN_IN_PROGRESS` without a second model/OCR call.
- Stream mode emits safe `token`, `tool_start`, `tool_end`, `final`, `error` events. Tool events contain names and scoped handles/status only, no generic serialized Linch object, raw job row, context credential or provider request. Final answer equals persisted safe response. Disconnect cancels the current DuyTa request, not an already admitted durable OCR job.
- `GET /tasks/{task_handle}` returns scoped status; `GET /tasks/{task_handle}/result` returns complete OCR result only after success; `DELETE /tasks/{task_handle}` delegates cancellation. Out-of-scope/unknown404, expired410, result-not-ready409, already-terminal cancellation follows existing409/202 semantics. A subsequent explicit `/turns` with task_handle can have DuyTa summarize the completed result; no hidden background conversation or endless polling.

HTTP path handles are scoped identifiers, not the v2 bearer capability. Headers/body access logging for this new prefix must be disabled/redacted before staging.

### Task 1: Scoped context and idempotent handle binding (independent CPU work)

**Files:** new document_agent/scope.py, settings.py, tests/document_agent/test_scope.py.

**Interfaces:** `DocumentScopeStore(path: Path)`; `create(peer: str, now: float) -> ContextGrant`; `resolve(token: str, now: float) -> DocumentScope`; `bind_task(scope: DocumentScope, job_id: str) -> str`; `resolve_task(scope: DocumentScope, task_handle: str) -> str`. Internal immutable `DocumentScope` carries database context ID and expiry, never raw token. `ContextGrant` exposes token only to HTTP issuance. Persist mappings and turn-idempotency state in private SQLite tables, with fixed expiry and cleanup; no raw file/text in scope metadata.

- [ ] Write failing tests: cross-context lookup/cancel404;64hex token creation with only hash stored; exact TTL boundary; concurrent bind returns one handle; equal/different idempotency payload replay/409; admission failure releases turn state; restart retains mapping without logging secrets.
- [ ] Run `/tmp/secai-multilang-final-env/bin/python -m pytest -q tests/document_agent/test_scope.py`; confirm failures exercise missing behavior.
- [ ] Implement the interfaces and finite settings. Thread blocking SQLite operations off the request event loop. Bound turn-response storage and expire it with context.
- [ ] Run targeted tests and `git diff --check`; obtain fresh independent review, then commit only these files.

### Task 2: OCR-only internal job, preserving the accepted lifecycle

**Dependency:** accepted file/OCR snapshot; root resolves composition conflicts without changing either contract. Single shared queue/worker, no second OCR queue service.

**Files:** translation/jobs.py, translation/worker.py, api/translate_v2/routes.py, document_agent/backend.py; tests/document_agent/test_ocr_jobs.py and existing lifecycle tests.

**Interfaces:** `OcrJobBackend.submit(scope, attachment: ImageAttachment, peer: str) -> ScopedTask`; `status(scope, task_handle) -> ScopedTask`, `result(scope, task_handle) -> CorrectedOcr`, `cancel(scope, task_handle) -> ScopedTask`. `ImageAttachment` is an immutable, validated in-memory PNG/JPEG input with opaque attachment handle and bytes; paths/URLs are never model input. Use internal `kind='ocr_image'`, `source_lang='auto'`, `target_lang=''` sentinel meaning **not applicable**, never a translation target. Existing `kind TEXT` needs no column migration; new scope table migrations are explicit/idempotent. Add tombstone kind metadata with legacy default `public` because old tombstones contain only public jobs; new tombstones preserve actual kind. Without it, public lookup could reveal expired internal tasks after the jobs row is reaped.

- [ ] Write failing tests proving `ocr_image` calls pipeline once and translate_blocks zero times; exact raw/corrected/warnings/edits retained; ordinary image/file jobs still translate.
- [ ] Add `JobStore.get_public` and `cancel_public` with kind filter inside the transaction; route public v2 lookup/result/delete through them. Test internal IDs404 even if known, existing public IDs unchanged, internal tombstones still404, legacy public tombstones410, and atomic cancel races.
- [ ] Reuse admit/claim/heartbeat/progress/finish/cancel/reap. Derive stable admission key from bound context + validated image hash + OCR configuration, then resolve/create task mapping idempotently; crashes between admission and binding recover the same job, not a duplicate. Keep shared limits/storage/lease fencing unchanged; an internal job deadline is the earlier of the existing30min deadline and context expiry, never later. Ordinary jobs retain their existing deadline behavior.
- [ ] Branch the worker after CorrectedOcr: internal result schema `{schema_version:1,result_type:'ocr',ocr:...}`; skip translation. Unknown job kinds fail explicitly. A deployment check must ensure all workers support this kind before enabling new ingress.
- [ ] Test timeout, cancellation, restart/reclaim, stale generations, expiry/tombstone, cleanup failure, queue fairness and no capability leakage; run accepted lifecycle regression tests. Preserve inherited failures rather than suppressing them. Review independently and commit.

### Task 3: DuyTa tool adapter and bounded document runner

**Files:** document_agent/tools.py, runner.py; prompts/document_ocr.md and additive profiles.yaml entry; tests/document_agent/test_tools.py, test_runner.py.

**Interfaces:** `DocumentRunDeps(scope, attachments, backend, peer)` passed by `RunOptions(deps=...)`; `build_document_tools() -> dict[str, Tool]` exposing only `ocr_extract(attachment_id)` and `ocr_result(task_handle)`; `DocumentRunner.complete(prompt, deps)` and `.stream(prompt, deps)` share one event-producing execution path. Dedicated in-memory Linch sessions/run state; no web chat session-store reuse.

- [ ] Write failing contract tests: missing/wrong deps rejected, fabricated attachment/task handle denied, duplicate extract reuses task, pending is not success, result previews bounded4,000codepoints with explicit truncation flag, raw/full result retained for authorized endpoint.
- [ ] Reuse configured DuyTa provider/factory, verify expected model identity, apply separate document_ocr profile. Bind complete/stream deps identically; registry fixed before session runs. No OCR tool in translate/correction/chat profiles.
- [ ] Wrap OCR text as untrusted content using the existing helper (including newly named tools in hook coverage if needed). Keep plain Unicode and protected literals intact. One submit/turn; no automatic result polling, bounded provider timeout/turn cap. No raw context/job IDs in ToolResult metadata, summaries or errors.
- [ ] Test source-text instruction injection, model selecting an unregistered tool, cancellation and side-effect retry. Test shared stage scheduling without holding an OCR reservation while requesting DuyTa correction; respect the existing OCR worker's ownership of model resources. Do not claim local semaphores coordinate remote/other processes; cross-worker live release remains an integration prerequisite.
- [ ] Run targeted tests, fresh independent review, commit.

### Task 4: Additive HTTP composition and safe JSON/SSE

**Files:** api/document_agent/routes.py, schemas.py; document_app.py; tests/document_agent/test_http.py, test_stream.py.

**Interfaces:** implement the frozen ingress above; `create_document_app(base_app, scope_store, backend, runner, settings)` composes with the accepted existing app. Reuse base lifespan once; root `document_app:app` delegates all other paths unchanged. No production entrypoint switch in this task.

- [ ] Write failing real-ASGI tests for all request/response/error cases above, finite multipart admission and content validation; include duplicate fields, spoofed content type, oversized body/pixels, unsupported PDF/URL/path, no-image text prompt, expired/out-of-scope handles.
- [ ] Implement bounded ingress using existing admission logic, context resolution before model calls and safe presentation DTOs. Propagate disconnect/cancel as specified. Reconcile streaming idempotency by storing the safe final/error response and replaying that result without regenerating tokens/tool calls.
- [ ] Test with actual Linch scripted-provider events: secret canaries absent from JSON, every SSE event, session/run stores and logs; raw private job IDs never reach model payload. Check result size limits before serialization and that all sync disk/SQLite work is off-loop.
- [ ] Test flagsOFF/missingOCR startup and legacy `/ai/chat`, `/sessions/{sid}/chat`, JSON/SSE, v2files/images and v3translation contracts. Run full backend suite against the same dependency baseline and compare case IDs/failures; independently review and commit.

### Task 5: Integration, live routing and quality evidence

**Files:** docs/api/document-agent.md; docs/test-results/document-agent/; exact shared-code changes only after accepted owner integration.

- [ ] Compose accepted commits in a new integration worktree; inspect merge conflicts and retain both owners' profile/settings changes. Run scoped tests then required full suite; report inherited failures with names. No main merge.
- [ ] Stage with actual tokenizer/provider and configured existing OCR model, isolated paths/port and feature flag. Wait for confirmed current OCR completion/release; do not use a3-PID lull as permission. Preserve actual model revisions/hashes where known and label inferred identity honestly.
- [ ] Exercise real DuyTa choosing OCR on image request, receiving pending/result, subsequent result request, no OCR on unrelated text, cancel/timeout and JSON/SSE consistency. Record tool traces without credentials; model refusal/misrouting is a failure even if unit tests pass.
- [ ] Measure OCR separately from translation and OCR→translation. Continue the existing18-direction benchmark objective; this agent wrapper does not prove70% translation quality. If DuyTa fails, compare approved specialist translator candidates sequentially with the same frozen dataset and honest scoring.
- [ ] Verify GPU ownership/resources before/after and stop only processes created by this task. Publish exact-SHA report and fresh integration review. Keep featureOFF if quality/dependency/security evidence is missing; no silent production fallback or deployment.

## Plan self-review / handoff

All approved design sections map to Tasks1–5. New ingress, token/handle distinction, pending continuation, internal kind sentinel and public-kind containment are explicit. The five review-focus cases have owning tests. No fourth auxiliary model, new training, PDF rasterization, UI redesign or production rollout is included. Self-review caught internal tombstone leakage and context/job expiry mismatch; Task2 now explicitly covers both before implementation. The previously selected execution method remains isolated SOL workers plus independent reviews; it is not necessary to choose that method again. Implementation has not started and dependency acceptance is still pending.

## MVP delivery priority — 2026-10-04

Latest user instruction prioritizes the fastest usable feature, with iterative optimization afterward. Deliver the smallest complete flow: DuyTa calls OCR for an image, receives an honest pending state, and retrieves the scoped result on a later request. Preserve context isolation, bounded inputs, durable idempotency, cancellation, existing API compatibility and focused independent review. No additional abstraction, framework or model is required.

CPU tools/runner and ingress may be implemented against the frozen backend interface with a scripted backend while the shared file/OCR branch is under review. Such tests establish adapter behavior only; actual backend integration and live routing remain required before claiming a usable integrated feature. Do not wait for broad model-quality certification to finish CPU functionality.

Wide corpus expansion, repeated quality rounds, performance tuning and optional refinements move to follow-up. Prior failed results remain failed; deferred evaluation is not PASS and does not certify the 70% target. The original translation/model-selection objective remains open. Shared GPU remains owned by the other runtime agent, which is now preparing a translation specialist after checkpointing OCR; do not interpret an unloaded OCR helper as permission to launch competing inference.
