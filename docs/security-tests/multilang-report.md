# Multilang Sol Vanguard — independent round 1 CPU checkpoint

2026-10-04. **CPU checkpoint: PASS for the cases run. Security release: BLOCKED.** No new actionable product defect was reproduced in these cases. This is not approval to enable flags, merge main, or deploy. Core independent round 2 remains in progress; assessment `f31a86c` is under fix and awaits final review. This report does not repeat that assessment review.

## Provenance and isolation

| Item | Exact revision / evidence |
|---|---|
| Base/main | `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5` (`git merge-base` of this branch) |
| Core under test | `42f8d40aff6768ee437dc4a7776826b86d0d2e88`; branch `test/multilang-security` |
| FE reviewed read-only | `788da975cf9eed0e0bf72089fd137e00fd8ad2b2` |
| Assessment context | `f31a86c`, **UNDER FIX; final review PENDING** |
| File/OCR WIP context | file `bfab8eb`, OCR `ff311c1`; no combined tree tested |
| Python dependency declaration | `pyproject.toml` SHA-256 `a026cffc0f3ff82cc93ff52b07f845b77aa74ab71597b02c11d309be854384ba` |
| Core security files | `multilang/config.py` `490b7b3ad97e5847f862af29968d88b76395da45441af346735a719672fc0211`; `provider.py` `8528ead7d2881bb4c8b831c495b863f3cfb5d19ec61c247f94c603675c4ba298`; `service.py` `09a81ef15a12c85f57552d1c50c3dd276780303047313f83e0e44124383378ca` |

Plan was written first at [multilang-test-plan.md](multilang-test-plan.md). The cases in [test_vanguard.py](../../tests/multilang_security/test_vanguard.py) use injected `httpx.MockTransport`, injected LLMs, local ASGI scopes, and a synthetic character counter for API budget branches. A separate SHA-pinned local tokenizer fixture verifies truncation and malformed files fail closed. The run used the existing core `.venv` without modifying it, `PYTHONPATH` pointing at this checkout's `src`, `env -i`, private mode-0700 temporary HOME/cache/data/database paths, bytecode and plugin autoload disabled, offline Hugging Face settings, loopback-deny proxies, in-memory sessions, and no `.env`. No live model, Vast, socket transport, package install, or external connection was used.

Final command selected `tests/multilang_security` plus four legacy non-owner cases (`UC17a` through `UC17d`) and both legacy en↔vi prompt cases, under a 10-second per-test timeout. **19 passed in 3.57 seconds** (13 vanguard, 6 legacy). There were two existing legacy OpenAPI duplicate-operation-ID warnings from showcase proxies; no test failed. The first collection attempt was stopped by the legacy default `/srv/exploitdb` path; all subsequent runs redirected it to private test data. Early test-only fixture errors (wrong CORS env key, an overbroad fake-output predicate, and Starlette's expected no-response path after disconnect) were corrected before the final run. No product source changed.

## Results by frozen security criterion

| Gate | CPU disposition | Evidence and limit |
|---|---|---|
| SEC-01 | **PARTIAL PASS** | `SV-01A`: mixed zh/ja/ko/ru/ar/vi/English instruction, SSRF and shell/SQL/HTML-like source reached only the fixed incumbent mock URL with system/user messages, `stream=false`, and no tool fields. A fake that echoed the per-request prompt canary failed after two bounded attempts. Live multilingual injection resistance and no real tool action remain **BLOCKED**. |
| SEC-02 | **PARTIAL PASS** | `SV-02A`: input bidi embed/override/isolate, NUL and surrogate failed before inference; malicious generated bidi/surrogate failed after retry. Arabic joiners and decomposed protected literal survived `protect`/`restore`. FE source uses React text nodes, `<bdi dir="ltr">` for technical spans, `dir=rtl`, and clipboard `writeText(result)`. Browser exact code-point copy/display is **NOT RUN**. |
| SEC-03 | **PARTIAL PASS / v3 auth N/A** | `SV-03A`: model, user ID, enabled flag and adapter extra fields each returned 400; an Authorization header did not enable a disabled pair. V3 invalid/expired token, role escalation and object IDOR are **N/A**: frozen v3 is anonymous/stateless and has no owned object ID. Existing legacy `UC17a`–`UC17d` passed non-owner 404/403 checks. File/OCR capability convention in combined tree is **BLOCKED**. |
| SEC-04 | **PARTIAL PASS** | `SV-04A`: inert URL/HTML/template/SQL/shell payload was data to one fixed mock destination; malformed provider JSON types mapped to `MODEL_UNAVAILABLE`. Trusted CORS origin received allow-origin, untrusted origin did not; no-store/nosniff/request-ID error headers held. FE static render showed no `dangerouslySetInnerHTML` in the panel. Real browser CSP/network trace and combined stack are **NOT RUN**. |
| SEC-05 | **PARTIAL PASS** | `SV-05A`: body bytes, code points, synthetic source/context tokens, admission, 429 Retry-After, peer-IP quota despite changing `X-Forwarded-For`, deadline 504, task cancel and explicit ASGI disconnect were probed. A SHA-pinned tokenizer with serialized truncation and a hash-matched malformed tokenizer both returned no counter. Real serving tokenizer/template/context attestation and multiworker ingress quota are **BLOCKED**; process semaphore is not cluster-wide. |
| SEC-06 | **PARTIAL PASS** | `SV-06A`: test-only secret was masked before the fake LLM, restored in permitted output, and absent from captured Python logs; prompt canary was absent from log and success output. Static read found no session/ledger/logger write in new v3 modules. Production access/error/tool traces, provider egress inventory, and data retention are **NOT RUN**. |
| SEC-07 | **PARTIAL / SCANNER NOT RUN** | `SV-07A`: FE lock at `788da975cf` pins DOMPurify 3.4.16, Vite 6.4.3, esbuild 0.25.12, Vitest 4.1.11 with integrity fields and declared licenses. Existing FE report records post-update `npm audit --audit-level=low`: 0 findings, while baseline had 1 low, 1 moderate, 1 high; this tester did **not** rerun npm audit. `semgrep` and `codeql` binaries were unavailable: scans **NOT RUN**, never “clean.” Runtime DuyTa checkpoint revision/license, tokenizer-serving identity, adapter checksum/license/safe-format remain **BLOCKED/N/A until Gate C**. |
| SEC-08 | **PARTIAL PASS** | `SV-08A`: concurrent zh/ar requests kept distinct outputs and snapshot settings, a later disabled request made no call, nth-chunk invalid output failed atomically, canary retry stayed within two attempts, and disconnect canceled in-flight work. No real provider concurrency or retry trace was available. |
| SEC-09 | **PARTIAL PASS** | `SV-09A`: master OFF returned the legacy app with new GET/POST 404, no v3 OpenAPI route, and `/health` 200. Legacy en↔vi prompt cases and owner checks passed. Missing optional-model-dependency startup, complete legacy JSON/SSE, file/OCR jobs, rollback/restart, and full suite in composed tree are **BLOCKED/NOT RUN**. |

## Findings and release gaps

Structured format: `ID | criterion/test | evidence | severity | suggested fix`.

| ID | Criterion/test | Evidence | Severity | Suggested fix |
|---|---|---|---|---|
| SV-R1-01 | SEC-07 scanner and model provenance | Local scanner binaries absent; npm audit 0 is author evidence from FE report, not a vanguard rerun; actual serving checkpoint/revision and token template were not attested. | **Unrated evidence gap; release BLOCKED** | Run approved scanners against the integrated lock/code and attest exact serving model, tokenizer, template and license before SEC-07 clearance. |
| SV-R1-02 | SEC-01/02/06/08 live and browser behavior | Fake transport and static FE read cannot establish live injection resistance, output quality, browser byte-preserving copy, production trace privacy, or real egress. | **Unrated evidence gap; release BLOCKED** | Perform sealed live multilingual adversarial cases and browser/network evidence after core final SHA and serving inventory are fixed. |
| SV-R1-03 | SEC-03/09 combined legacy/file/OCR | This branch does not contain the integrated main+file+OCR tree; six selected legacy cases passed, but full ownership/job/SSE and rollback composition was not exercised. | **Unrated evidence gap; release BLOCKED** | Run the integrated full suite, imported file/OCR cases and rollback tests in one disposable composed tree. |

**New actionable Critical/High/Medium findings in this CPU round: 0.** This says only that the cases run found none. Prior core round-1 findings are recorded in `docs/reviews/multilang/core-round-1*.md`; fixes at the exact core SHA still await independent round 2. Assessment findings at `f31a86c` are under fix and pending final adjudication. No unresolved Medium is accepted here; release requires fixes or explicit written user acceptance for any remaining Medium, and zero unresolved Critical/High. Live 16-direction quality, combined file/OCR, full integration, real-backend FE smoke and production release decision remain **BLOCKED/PENDING**.
