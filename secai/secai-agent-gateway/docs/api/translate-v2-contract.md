# Translation v2 — frozen contract v1 (2026-10-03)

Status: design frozen for CODE; implementation/acceptance NOT yet PASS. Base: `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`. This document resolves proposals in `docs/plans/translate-import-ocr.md`; changes require root-coordinated written decision and affected revalidation. No frontend work or legacy API/schema/SSE behavior changes.

## Public HTTP contract

No login, bearer token, cookie, API key or `user_id` required. No owner/tenant check. Possession of a job ID permits reading and cancellation. IDs are `secrets.token_hex(16)` or equivalent 16 CSPRNG bytes, lowercase 32 hex; UUID4 is insufficient for 128 random bits. No list/search route. Never log IDs, Idempotency-Key, upload text, filenames, raw/corrected text or credentials, including reverse-proxy/access/error traces. All v2 responses, including errors, have `Cache-Control: no-store` and JSON Content-Type except empty OPTIONS. No HTML rendering. Request ID is independent random diagnostic ID; it must not encode job ID.

| Method/path | Request | Response |
|---|---|---|
| POST `/ai/v2/translate/files` | multipart `file` exactly once, `target_lang` required `en|vi`, `source_lang` optional `auto|en|vi` default `auto` | 202 Job; TXT/DOCX/DOC only |
| POST `/ai/v2/translate/images` | same fields | 202 Job; PNG/JPEG only |
| GET `/ai/v2/translate/jobs/{job_id}` | none | 200 Job including failed/cancelled |
| GET `/ai/v2/translate/jobs/{job_id}/result` | none | 200 Result only when succeeded |
| DELETE `/ai/v2/translate/jobs/{job_id}` | none | 202 Job when active/cancelling/cancelled; 409 on succeeded/failed |

Unknown multipart fields (including `user_id`), duplicate fields, missing fields, non-multipart input or malformed multipart return 400 INVALID_REQUEST; invalid language 400 INVALID_LANGUAGE. Language values are exact lowercase; same source/target permitted and yields identity transform with preserved structure. Filename extension case-insensitive; actual byte/structure validation is mandatory, MIME header advisory. Unknown/malformed job ID returns 404 without differing timing-sensitive lookup paths. Missing/expired job returns 404/410 (tombstone period only). No new 401/403 flows.

Optional `Idempotency-Key`: exactly 32 lowercase hex client-generated random characters (entropy cannot be proven server-side). Invalid format 400 INVALID_REQUEST. Scope `(route kind, key)`, not IP/user. SHA-256 fingerprint over original file bytes, normalized source/target language and lowercase extension, excluding filename stem/MIME/header order. Atomic key registration with durable job creation; simultaneous equal requests resolve to one job. Same payload returns current same Job with HTTP 202, including terminal job; different payload 409 IDEMPOTENCY_CONFLICT. Expired key during tombstone returns 410 JOB_EXPIRED, never new execution. Key mapping retained until tombstone expires; then key can be reused. Keys are capability secrets as well. POST without key always creates independent jobs. Failed sync admission does not reserve key. Validate/size-limit streamed bytes before fingerprint/admission; cleanup duplicates.

### Schemas

All listed properties present unless explicitly optional. Unknown JSON output fields may be added only in a reviewed future version. Error strings human-readable/safe, codes stable. UTC timestamps RFC3339.

```json
{
  "job_id":"0123456789abcdef0123456789abcdef", "kind":"file",
  "status":"queued", "stage":"queued",
  "progress":{"completed":0,"total":null},
  "source_lang":"auto", "target_lang":"vi", "warnings":[], "error":null,
  "created_at":"2026-10-03T00:00:00Z", "updated_at":"2026-10-03T00:00:00Z",
  "expires_at":null,
  "status_url":"/ai/v2/translate/jobs/0123456789abcdef0123456789abcdef",
  "result_url":"/ai/v2/translate/jobs/0123456789abcdef0123456789abcdef/result"
}
```

`status`: queued/running/cancelling/cancelled/succeeded/failed. `stage`: queued/extracting/preprocessing/ocr/correcting/translating/done. Terminal stage always done. Progress counts fully translated source blocks, not chunks; starts 0/null, total fixed after extraction/correction, completed monotonic and <=total; no fake OCR percent. Partial blocks/results are not public. `source_lang=auto` remains auto (no invented detected language). `error`: null or `{code,message,request_id}`; warning `{code,message,block_id}` with block_id nullable. Safe messages contain no input/exception/endpoint secrets.

```json
{
  "job_id":"0123456789abcdef0123456789abcdef", "kind":"file", "status":"succeeded",
  "source_lang":"en", "target_lang":"vi", "translated_text":"Nội dung",
  "blocks":[{"id":"b000001","kind":"paragraph","order":0,"source_text":"Content","translated_text":"Nội dung","metadata":{}}],
  "warnings":[], "ocr":null
}
```

Block kinds: paragraph/table_cell/header/footer/footnote/literal/ocr_text. Contiguous order starts 0; stable IDs `b000001` onwards assigned after extraction. `translated_text` is exact `"\n".join(block.translated_text for block in blocks)`; no implicit stripping. Metadata JSON contains only extractor-owned keys: section (zero-based integer), table/row/column (zero-based integers), footnote_id (string), source_part (safe logical part name), bbox (four nonnegative pixel coordinates), confidence (0..1 or null). Not every key applies. Empty structural cells preserved; blank-only overall document fails NO_TEXT_DETECTED. Original raw XML or local paths must not appear. Nested table IDs are unique globally in the document and cells follow depth-first row-major order at their body position; metadata table/row/column makes membership unambiguous. Merged cells additionally permit integer row_span/column_span metadata >=1.

For image result `ocr` is `{raw_text, corrected_text, edits, needs_review}`. Both text fields are exact newline joins of their blocks. Edit is `{block_id,start,end,before,after,reason}`; offsets zero-based Unicode code points in raw block, half-open, sorted and non-overlapping; `before == raw[start:end]`. `reason` is short safe enum `spelling|spacing|punctuation`. `needs_review` boolean true when correction rejected or ambiguous technical material remains. No fabricated confidence. Source_text for translated image blocks is corrected text; raw_text always retained until result TTL.

Error envelope: `{"error":{"code":"INVALID_REQUEST","message":"Invalid request.","request_id":"independent-diagnostic-id"}}`.

| HTTP | Codes | Timing |
|---|---|---|
| 400 | INVALID_REQUEST, INVALID_LANGUAGE, EMPTY_FILE | admission |
| 404 / 410 | JOB_NOT_FOUND / JOB_EXPIRED | lookup |
| 409 | RESULT_NOT_READY, IDEMPOTENCY_CONFLICT, JOB_ALREADY_FINISHED, JOB_FAILED, JOB_CANCELLED | lookup/admission |
| 413 | FILE_TOO_LARGE, REQUEST_TOO_LARGE, TEXT_TOO_LONG, IMAGE_TOO_LARGE, ARCHIVE_LIMIT_EXCEEDED | admission if known, otherwise job failure |
| 415 | UNSUPPORTED_FILE_TYPE, FILE_TYPE_MISMATCH | admission (extension/magic), deeper mismatch may fail job |
| 422 | CORRUPT_DOCUMENT, CORRUPT_IMAGE, UNSUPPORTED_ENCODING, UNSAFE_DOCUMENT, NO_TEXT_DETECTED | deeper parsing generally job failure |
| 429 | RATE_LIMITED, QUEUE_FULL | admission/lookup; integer Retry-After seconds >=1 |
| 503 | DEPENDENCY_UNAVAILABLE, STORAGE_UNAVAILABLE | before durable acceptance only |

After 202 no retroactive upload HTTP error: GET status remains 200 with failed state and error. Additional asynchronous codes: EMPTY_TRANSLATION, TOKEN_INTEGRITY_FAILED, PROCESSING_TIMEOUT, OCR_UNAVAILABLE, TRANSLATION_UNAVAILABLE, WORKER_LOST, INTERNAL_ERROR. Result on failed/cancelled returns 409 JOB_FAILED/JOB_CANCELLED; details available via status. Unexpected v2 exceptions map safe INTERNAL_ERROR HTTP500. An invalid correction proposal is discarded, raw retained, warning CORRECTION_REJECTED; correction transport outage/exhausted timeout fails the job (no hidden skipped stage). Other warning codes: ENCODING_ASSUMED, UNSUPPORTED_PART, AMBIGUOUS_TECHNICAL_TOKEN. Absence of warnings does not imply perfect OCR.

## Resources and lifecycle

Defaults below are enforced limits, configurable via dedicated `TranslationSettings` env prefix `TRANSLATE_V2_`; benchmark records exact values. Do not loosen for passing tests without contract revision.

| Limit | Default |
|---|---|
| Uploaded file / complete request | 10 MiB / 11 MiB; actual streamed bytes, not trusted Content-Length |
| multipart | <=8 parts, <=4 KiB each text field, exactly one file; unknown/duplicate parts rejected |
| Image | <=20,000,000 pixels; each edge <=10,000; limits before expensive decode and after preprocess |
| Extracted text / blocks / model chunks | <=200,000 Unicode code points / 10,000 / 300; no silent truncation |
| DOCX | <=2,000 entries, <=50 MiB aggregate inflated bytes, <=100:1 aggregate ratio; enforce streamed decompression |
| Chunk | <=6,000 code points AND actual configured provider token budget with output reserve; indivisible oversized literal bypasses LLM unchanged |
| Jobs / LLM / OCR | <=2 running jobs, <=4 in-flight LLM calls (correction included), <=1 OCR call; single worker process on same host, SQLite store shared with web processes |
| Queue / IP | <=20 queued globally, <=2 active (queued+running+cancelling) per client IP |
| Rate | upload 5/60s/IP, combined GET/DELETE 60/60s/IP; cross-process transaction store; exact v2 prefix excluded from legacy limiter |
| Timeout | converter 60s, OCR 120s/call, LLM 120s/call; deadline 30min from admission (queue time included) |
| Retry | max 1 retry per transient OCR/LLM call or translation integrity rejection; no retry auth/invalid input; capped by job deadline |
| Storage | separate private job DB/artifact directory; total 5 GiB quota with atomic reservation; input/intermediate removed within 5min of terminal |
| Retention | terminal result 24h; tombstone extra 24h; reaper interval <=5min, logical expiry checked on read before physical deletion |
| Lease | 60s lease, heartbeat <=15s; monotonic fencing generation on claim; one reclaim maximum then WORKER_LOST; deadline never reset |

SQLite is the only initial queue backend. Durable acceptance means input atomically persisted and job/idempotency transaction committed before 202. No session DB reused. Run one supervised standalone job worker process with `SESSION_STORE_BACKEND=memory`, Redis session backend disabled, and separate private worker gateway DB/storage paths; its LinchRunner is independent of web GatewayState and legacy settings. LLM transforms use this isolated in-memory session runner; session_id=None alone does not prove non-persistence, because Linch may assign an anonymous ID and persist wrapped prompts. Do not let internal v2 prompts or raw content enter existing session/ledger storage. Sol-1 owns this dependency construction and verifies actual configured stores. Do not run transformations using the web lifespan runner. Durable queue/lease recovers independently of web request tasks; standalone worker reads the shared job SQLite/input directory on the same host. Worker auxiliary ledger/DB must not retain prompts/content; verify this with canary scan and purge private transient state at restart/terminal as applicable. No new distributed queue is needed. Restart recovery redoes work from original input without exposing partial output; stale generation cannot publish/progress. Atomic CAS terminal transition resolves cancel/success race: cancellation wins if cancelling committed first, otherwise DELETE sees already finished. DELETE active returns 202 after recording cancelling (queued may immediately become cancelled); repeated DELETE cancelled still 202. Cancel stops scheduling work immediately, aborts in-flight HTTP/converter where possible; cannot publish success after cancelling. Cancellation terminal within 15s under test, except uninterruptible cleanup bounded by converter timeout (60s); evidence records actual. TTL set once terminal commits. At result expiry both status/result/delete return 410 until tombstone expiry then 404; tombstones hold only ID, terminal status, expiry and hashed idempotency mapping, never text/IP/file metadata. Rate records also expire; IP active count releases terminal transactionally.

Trust forwarding headers only from explicit trusted proxies; default peer IP. v2 CORS scoped separately, same configured allowed origins, GET/POST/DELETE/OPTIONS and Content-Type/Idempotency-Key only; no credentials. Legacy CORS/header/rate behavior unchanged. Missing OCR disables image admission with 503 only; file/legacy startup remains usable. Missing DOC converter blocks DOC only. No external OCR URL supplied by caller. AIRGAP policy applies to configured service connection; no runtime Internet/model fetch.

## Extraction decisions

TXT: BOM UTF-8/UTF-16 LE/BE strict; without BOM strict UTF-8 first. If UTF-8 fails, candidate CP1258 uses Python `bytes.decode("cp1258", errors="strict")`, exact encode round-trip and no prohibited decoded controls (C0 except TAB/LF/CR, DEL and C1). Require at least one Vietnamese-specific precomposed character in NFC(candidate) from `ăâđêôơưĂÂĐÊÔƠƯ` or vowel+Vietnamese tone combinations (enumerated Vietnamese alphabet), and reject file-signature/binary anomalies. Accept this explicitly heuristic CP1258 branch with ENCODING_ASSUMED; NFC used for classification only, decoded output retained (apart from newline normalization). This cannot distinguish all other 8-bit encodings, so warnings must state assumption; do not claim confident detection. Candidate without Vietnamese signal or failing strict checks => UNSUPPORTED_ENCODING. UTF-16 without BOM is unsupported in v1; documented fixture uses BOM. Never errors=ignore/replace. Reject NUL in decoded text; NUL bytes inside valid BOM UTF-16 are not themselves a binary rejection. CP1258 positive fixtures must contain Vietnamese-specific characters and combining tones and exercise this branch. Invalid CP1258 bytes and ambiguous 8-bit text are rejection fixtures.

DOCX: validate Word package/content types/parts, reject DTD/entities, macros, OLE/embedded active objects, external relationships (including external hyperlinks under this deliberately strict initial policy), encrypted/password-protected input. DOC: require CFB/OLE signature plus WordDocument stream, scan active content before isolated conversion; macro-disabled/no-network converter with temp profile and subprocess limits. Full paragraph/table/header/footer/footnote coverage required; antiword only acceptable if goldens prove all coverage, otherwise LibreOffice conversion to DOCX. ZIP traversal, links and decompression attacks rejected before unsafe writes. CPU/decode/conversion work off event loop.

Logical order per section: effective header once (resolve link-to-previous) → body paragraphs/tables interleaved → footnotes immediately after referencing block (once per reference, note ID retained) → effective footer once. Table cells row-major with row/column/table metadata; merged cells emit one origin cell with row_span/column_span metadata. Floating drawings/textboxes/endnotes/comments not part of guaranteed coverage: emit UNSUPPORTED_PART identifying logical part, never silently drop visible text. No pagination/font/layout/docx-download promise.

## Python boundary (sol-1 owns definitions)

`translation/types.py` defines the following exact public names; dataclasses frozen, keyword construction, tuples for collections. Types may add private helpers only. All offsets/limits use Unicode code points. Neither side imports the other's router/DB internals.

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

SourceLang = Literal['auto', 'en', 'vi']
TargetLang = Literal['en', 'vi']
BlockKind = Literal['paragraph', 'table_cell', 'header', 'footer', 'footnote', 'literal', 'ocr_text']

@dataclass(frozen=True)
class WarningItem:
    code: str
    message: str
    block_id: str | None = None

@dataclass(frozen=True)
class DocumentBlock:
    id: str
    kind: BlockKind
    order: int
    text: str
    metadata: dict[str, Any]

@dataclass(frozen=True)
class ExtractedDocument:
    blocks: tuple[DocumentBlock, ...]
    warnings: tuple[WarningItem, ...] = ()

@dataclass(frozen=True)
class OcrEdit:
    block_id: str
    start: int
    end: int
    before: str
    after: str
    reason: Literal['spelling', 'spacing', 'punctuation']

@dataclass(frozen=True)
class OcrResult:
    blocks: tuple[DocumentBlock, ...]
    raw_text: str
    warnings: tuple[WarningItem, ...] = ()

@dataclass(frozen=True)
class CorrectedOcr:
    blocks: tuple[DocumentBlock, ...]
    raw_text: str
    corrected_text: str
    edits: tuple[OcrEdit, ...] = ()
    warnings: tuple[WarningItem, ...] = ()
    needs_review: bool = False

@dataclass(frozen=True)
class PreparedImage:
    path: Path
    media_type: Literal['image/png', 'image/jpeg']
    width: int
    height: int

@dataclass(frozen=True)
class TranslatedBlock:
    source: DocumentBlock
    translated_text: str

class TranslationError(Exception):
    # constructor: (code: str, message: str, *, retryable: bool = False)
    code: str
    message: str
    retryable: bool

class ProcessingContext(Protocol):
    async def checkpoint(self, stage: str, completed: int = 0,
                         total: int | None = None) -> None: ...
    # Checks cancellation, fence and absolute job deadline; raises
    # asyncio.CancelledError for cancellation/stale fence, TranslationError
    # PROCESSING_TIMEOUT for deadline. Does not reset progress on stage update.
    async def complete(self, profile: str, prompt: str) -> tuple[str, dict[str, Any]]: ...
    # Central LLM semaphore/timeouts via runner.complete(profile,prompt,None),
    # no grounding; only translate/ocr_correction profiles allowed here.

class OcrPipeline(Protocol):
    async def process(self, input_path: Path, *, work_dir: Path,
                      context: ProcessingContext) -> CorrectedOcr: ...
```

Sol-1 `translation/service.py`: `async translate_blocks(blocks: tuple[DocumentBlock,...], source_lang: SourceLang, target_lang: TargetLang, *, context: ProcessingContext) -> tuple[TranslatedBlock,...]`. Own strict technical-preservation translation adapter until sol-2 helper available; import sol-2 helper lazily or dependency injection, never duplicate incompatible shared dataclasses. Sol-2 `translation/ocr_client.py`: `class ImageOcrPipeline` implements protocol, constructor `(*, settings: TranslationSettings)`; settings defined below by sol-1. Sol-1 lazy factory loads it only when OCR enabled, then calls process and translate_blocks; pipeline never translates, writes jobs, or publishes result. Sol-2 `ocr_preprocess.py`: `async preprocess_image(input_path: Path, *, work_dir: Path) -> PreparedImage`; `ocr_client.py`: `async recognize_image(image: PreparedImage, *, settings: TranslationSettings) -> OcrResult`; `ocr_correction.py`: `async correct_ocr(result: OcrResult, *, context: ProcessingContext) -> CorrectedOcr`. Pipeline checkpoints preprocessing/ocr/correcting; caller translating. Caller owns work_dir cleanup; original remains immutable. API is not permitted to accept remote image URLs.

Sol-2 `technical_tokens.py`: `protect_literals(text: str) -> ProtectedText`, `restore_literals(output: str, protected: ProtectedText) -> str`; `ProtectedText` defined by sol-2 has `.text: str`; restore rejects unknown/missing/duplicate/reordered placeholders via TranslationError TOKEN_INTEGRITY_FAILED. Mask longest disjoint spans before chunking; preserve occurrence and position within source block, input placeholders escaped collision-free. Sol-1 may consume `.text` and restore only, not private token structures. Sol-2 checkpoints this small helper early; import final acceptance composes both branches. Until then sol-1 may inject a test double, not ship an unverified weaker fallback. Recognize CVE/CWE, valid IP/CIDR, URLs, hashes, software/version phrases, paths, commands/code and secrets (reuse legacy secret patterns read-only). Oversized code literal retained as literal block or bypass chunk with identity, never split technical span. No nested masking through legacy build_translate_prompt: use a v2 prompt over already protected text with existing spotlight helper, or verify helper does not double-mask. Old literal_mask.py and routes.py remain untouched.

Shared TranslationSettings minimum fields: `enabled: bool=False`, `ocr_enabled: bool=False`, `ocr_base_url: str=''`, `ocr_api_key: str=''` (redacted repr), `ocr_model: str=''`, `ocr_timeout_seconds: float=120`, `ocr_max_retries: int=1`; all other resource limits above configurable by sol-1 with documented names. No credentials in examples. Local default disabled prevents missing optional dependency breaking old APIs. Tests enable explicitly. OCR server wire API owned/documented by sol-2, pinned with adapter contract tests; client validates bounded responses before constructing OcrResult.

Correction uses new registry entry `ocr_correction`, tools `[]`, base false, max_turns 2; no chat/session persistence. Sol-1 owns minimal `agent/runner.py` exemption adding this profile to date-context skip; retain every existing profile behavior. Evidence must inspect actual provider payload/tool registry, not only prompt text. Invalid edits: discard whole proposal, preserve raw, warn. No insertions/deletions of whole passages; cap changed code points to 10% of raw block (minimum 1), each edit <=32 code points, no newline changes or edits intersecting protected literals. Correction proposes prose edits only; ambiguous technical tokens remain raw and flagged, no autonomous technical-token repair in v1. Sol-2 may use validated supplementary OCR evidence solely as warning evidence, not relax preservation.

## Hardware/performance decisions and evidence

Root preflight reports existing Vast instance 53846446 A100 SXM4 80GB, 71,040 MiB used and 10,010 MiB free, /workspace 74GB free. Snapshot is not reserved quota. OCR total model parameters including vision encoder <=4B. OCR-owned peak GPU allocation <=7 GiB (7168 MiB), with >=2 GiB (2048 MiB) free safety reserve. Before initial load when owned OCR allocation is zero, require free >=9216 MiB. Before inference with a persistent loaded model, require `free_MiB >= 2048 + max(0, 7168 - owned_ocr_current_MiB)`, where owned allocation is measured for the OCR process(es), not inferred from configured model size. Sample allocations and free memory throughout the run; enforce owned OCR peak <=7168 MiB and free >=2048 MiB, abort safely if insufficient. Serialize OCR loads/inference. This accounts for already-loaded OCR memory without counting it twice; it does not relax the allocation cap or safety reserve. No restart/reconfigure incumbent LLM/embedding/reranker; one benchmark model loaded at a time. Use isolated venv/process if Docker absent, loopback plus protected tunnel or private TLS/service auth. Benchmark pin weights revision/checksums/runtime dependencies; no local GPU.

Mandatory quality gates are in criteria documents. Provisional design performance budgets: post-upload admission p95 <=2s, status p95 <=300ms at 10 clients within rate limits, warm OCR p95 <=30s for <=2MP inputs, gateway+worker RSS <=2GiB excluding isolated converter/model process. These are NOT claims or hard quality thresholds before measurement. Report pass/fail against budgets; if missed, root must record sizing/optimization decision before production readiness, never falsify or reduce OCR quality gates. Peak memory/resource isolation, no OOM, deadlines and limits are hard gates. Record incumbent service before/after health plus real embedding/reranker request and representative latency; no regression claim without comparative samples.

## Staging and review protocol

Sol-1 checkpoints types/settings first; sol-2 tests against contract doubles only until checkpoint exists, never commits duplicate types. HTTP image acceptance uses separate temporary staging worktree composed of exact sol-1 and sol-2 SHAs on BASE_SHA; record commands, composition SHA, environment and evidence. No feature owner edits other branch files; conflict returned through root. Branch review compares owned diff to BASE_SHA; cross-feature evidence names both SHAs. Changing either invalidates affected evidence and requires replay. This is pre-integration staging, not approved integration/main. Independent reviewer, tester, vanguard must run their own checks; only after their PASS may independent astra B perform integration. Missing live Vast/model evidence is BLOCKED, not mock-based PASS.
