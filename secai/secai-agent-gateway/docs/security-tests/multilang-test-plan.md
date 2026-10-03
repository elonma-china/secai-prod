# Multilang independent security test plan — round 1

Prepared before execution, 2026-10-04. Target: `test/multilang-security` at `42f8d40aff6768ee437dc4a7776826b86d0d2e88`; FE evidence at `788da975cf9eed0e0bf72089fd137e00fd8ad2b2`. Frozen contract and SEC-01..09: `/home/ict/secai-multilang-wt/secai/secai-agent-gateway/docs/api/multilang-contract.md` and `docs/criteria/multilang-integration-security.md`. Assessment `f31a86c` is under fix; its review is outside this round.

Method: independent pytest cases in `tests/multilang_security/`, injected fake LLM or `httpx.MockTransport`, ASGI in process, isolated environment and paths, no external network/model. Read author tests only to identify injection points. Failures are findings, not product patches. CPU evidence is a checkpoint; live quality, combined file/OCR, and full release integration remain pending.

| Case ID | Gate | Method | Expected result | Severity if breached |
|---|---|---|---|---|
| SV-01A | SEC-01 | Mixed-script multilingual instruction payload, fake transport trace, canary-bearing fake output | Provider request fixed to incumbent model, no tools/function calls; no network action; prompt/glossary canary cannot appear in successful output | High |
| SV-02A | SEC-02 | Bidi/isolate/NUL/surrogate input and output; NFC/NFD, Arabic joiners and protected literals | Dangerous controls fail 422/502 envelope; valid joiners and literal bytes survive | High |
| SV-03A | SEC-03 | Extra JSON fields, fake user/model/flags, authorization and forwarded identity | Extras fail 400 and cannot enable flags; v3 token/role/IDOR N/A because no object/identity; legacy session/project ownership regression checked | High |
| SV-04A | SEC-04 | Inert HTML/SQL/shell/template/URL data, fake provider destination; CORS preflight/headers/errors | No fetch/execute, no request-controlled destination; exact CORS allowlist and safe JSON envelopes | High |
| SV-05A | SEC-05 | Streaming byte cap, Unicode chars, token/context budget, concurrent admission, fixed-window IP, deadline/disconnect, transport retries | Hard caps before inference; 429/504 correct; spoofed forwarding ignored; slots released and no hidden retry | High |
| SV-06A | SEC-06 | Canary source/secret/system/glossary in fake output and captured logs, session/ledger writes, request and provider traces | No new persistence, log/input echo, prompt leak, arbitrary egress, or secret exposure | High |
| SV-07A | SEC-07 | Inspect exact dependency/model/config pins and FE lock diff; review existing npm audit report; inspect scanner availability | Pin and no-tool destination evidence; report known advisories and scan status honestly, unavailable scanner = NOT RUN | Medium/High by reachability |
| SV-08A | SEC-08 | Concurrent distinct-language fake calls, nth-chunk failure, retry/order/placeholder tamper, cancellation and flag snapshot flip | No cross-request data; atomic error; bounded attempts; no stale flag change midrequest; canceled work stops | High |
| SV-09A | SEC-09 | Master/per-language OFF, missing optional dependency, legacy routes and ownership, app composition | New routes 404/403 as specified; legacy behavior retained; rollback and startup clean | High |

Limits recorded up front: CPU fakes cannot prove live multilingual injection resistance or translation quality, serving checkpoint identity, cluster-wide quotas, browser display/copy, full main+file+OCR composition, or production rollout. Any unresolved Medium needs a fix or written user acceptance; Critical/High unresolved must be zero before release.
