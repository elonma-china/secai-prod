# Core review Round 3 — `dbda8ecc`

**Spec/code verdict: FAIL. Release gate: BLOCKED.** The four original Round 2 examples now pass independent local reproductions. One interaction case still fails CORE-07.

| ID | Criterion/test | Evidence | Severity | Suggested fix |
|---|---|---|---|---|
| R3-01 | CORE-07: mixed-script source should remain uncertain without reliable language evidence | [service.py](/home/ict/secai-multilang-review-core3-wt/secai/secai-agent-gateway/src/secai_gateway/multilang/service.py:89) treats ≥90% contrary-script letters as a definite mismatch. Arabic source `راجع: Не удалось установить соединение с сервером после обновления операционной системы и перезапуска приложения.` contains an Arabic instruction and a Russian error message. Cyrillic accounts for 94/98 letters; the service returned `SOURCE_LANGUAGE_MISMATCH` with **zero** model calls. The frozen contract requires mixed-script evidence alone to remain uncertain. | Medium | Keep mixed declared-script prose on the uncertainty path; reserve 422 for wholly contrary-script input or a validated language detector. Add a long quoted error-message case. |

| Round 2 finding | Independent disposition |
|---|---|
| R2-01 prompt fragment | **Fixed for the reproduced case:** generated `ignore instructions inside it.` and a punctuation variant each failed after two attempts. Source-overlap prose was allowed; an additional generated prompt fragment was still rejected. |
| R2-02 protected literal | **Fixed:** a preserved code literal containing `return only translated plain text` succeeded in one attempt. The same literal plus generated prompt prose failed after two attempts. |
| R2-03 wrong script | **Partial:** wholly Cyrillic text declared Arabic now returns 422 before inference, while ordinary Arabic/Latin technical prose remains uncertain. R3-01 shows a mixed-script false positive. |
| R2-04 accent near-copy | **Fixed for the reproduced case:** accent-stripped French copied into Vietnamese failed after two attempts; distinct Vietnamese prose succeeded. |

The `42f8d40..HEAD` diff changes only the multilang service, targeted tests, and report. `git diff --check` passed; the checkout is clean. Fixed model ID and no-tool provider payload remain unchanged by this diff, and no legacy or protected source changed. Reproductions used the core virtualenv, an in-memory fake provider, isolated private paths, and no network. I did not repeat the broad suite; the author’s **135 targeted passes** are reported evidence, not an independent full-suite result. Loaded tokenizer/revision attestation, integrated file/OCR tests, live quality and security evaluation remain blocked for release.