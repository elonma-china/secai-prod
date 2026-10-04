# Report templates MVP API

The existing IAS report endpoints remain unchanged. This is an opt-in internal API at `/ai/v2/reports`. Set `REPORT_FEATURE_ENABLED=true`, a non-empty `REPORT_API_KEY`, and optionally `REPORT_DB_PATH` (default `./data/reports.db`). Every request requires `X-API-Key: <REPORT_API_KEY>`. The key grants access to **all** templates and reports in this workspace; this API does not enforce individual user ownership. Keep it behind the trusted internal frontend/server boundary and do not expose the key in browser code. Disabled or unconfigured routes return 404; invalid or missing key returns 401 before parsing request bodies.

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| GET | `/templates` | — | Array of templates |
| POST | `/templates` | `TemplateFields` | Created template |
| POST | `/templates/import` | Multipart `file` (.docx, .md, .txt) | Editable `TemplateFields` draft; caller must POST `/templates` to save |
| PATCH | `/templates/{id}` | `TemplateFields` plus `expected_version` | Updated template (409 if stale) |
| POST | `/generate` | `{"template_id":"...","data":"..."}` | Generated report, revision 1 |
| GET | `/{report_id}` | — | Current report |
| POST | `/{report_id}/edit` | `{"instruction":"...","expected_revision":1}` | Updated report (409 if stale) |
| GET | `/{report_id}/revisions` | — | Array of `{revision,content,created_at,instruction}` (`instruction` is empty for revision 1) |

`TemplateFields` is `{name, description?, sections: string[], instructions?, example?}`. Saved templates also have `id`, `version`, `created_at`, and `updated_at`. Reports are `{id, template_id, template_version, revision, content, created_at}`. `content` is Markdown. Inputs are capped at 2 MiB per import, 40,000 characters per template example, 20,000 characters per report data field, and 4,000 characters per edit instruction. DOCX with macro/binary parts, excessive extracted data, or XML DTD is rejected. Sensitive identifiers and secrets detected by the gateway's public-input guard are rejected.

The report store retains the original `data` and the selected template section snapshot for later edit validation; these internal fields are not returned by the API. Generated and edited drafts must contain every selected section in order and must not introduce new CVE IDs, IP addresses, URLs, hashes, or numeric literals absent from the original data. Edits must also retain all prior CVE IDs, IP addresses, URLs, and hashes. An invalid draft is retried once with validation feedback. If both attempts fail, the API returns 422 without creating a report or revision. These checks are deliberately narrow and do not verify the meaning of ordinary prose.

Report creation and editing use the configured `LLM_PROVIDER` chat-completions endpoint (Qwen/vLLM by default). The service returns 502 if the model is unavailable and stores a report only after successful generation. Prompts ask the model to use only supplied facts; this is a drafting aid, not a factual guarantee. Human review remains necessary before publication. No serving, LoRA, live quality claim, catalog or publishing workflow is included in this MVP.

## Quality fixes (2026-10-04)

URL checks preserve path/query case and punctuation inside explicit URL delimiters; bare sentence-ending punctuation is excluded from comparison. New peer-level sections after required template sections are rejected; nested subheadings remain allowed.

Edits receive the original source as untrusted data as well as the current report. When an instruction names exactly one editable template section, the service copies other section bodies from the previous revision before validation. It recognizes the supported explicit keep phrases and overlapping section names; free-form or multi-section instructions still depend on the model following the prompt. Source facts and revision history remain stored in SQLite.

Prompts distinguish missing evidence from a failed control, require separate entity-to-system/IP/evidence associations, and retain conflicting values rather than inventing a resolution. A few narrow Vietnamese phrase checks catch known omissions; these are not general semantic verification. Synthetic live quality results and full saved reports are indexed at `/home/ict/secai-report-evidence/INDEX.md`.

### Source coverage and bounded factual checks

Generation and edit requests now send source data in a JSON object to the model; the system distinguishes source facts from embedded instructions. A validation retry appends trusted feedback outside that object. This is an internal model prompt, not a public JSON response contract.

Conclusive Vietnamese compliance outcomes are rejected when source text has no matching conclusive outcome context; uncertain/imperative clauses do not count as evidence. Additional bounded checks reject CT-number/IP pairings absent from source records (with shared source scope allowed), repeated explicitly duplicate-marked timeline actions, and a few contradictory unknown-handling statements. These checks cover specific forms; they are not a general semantic proof or a guarantee for arbitrary control-code formats. Distinct dates/actions and recaps in different sections remain allowed. At most one retry is used; invalid drafts never replace saved revisions.

### Template formatting and edit scope

Section matching normalizes optional numeric prefixes (such as `1. Tóm tắt`) consistently for validation and scoped edits. Markdown ordered-list markers are excluded from numeric fact checks in both source and report; numbers inside list items remain checked. Source facts inside fenced code blocks retain their control/IP associations. Explicit Vietnamese whole-report edit requests (such as `Rút ngắn toàn bộ báo cáo, kể cả phần Tóm tắt`) are not narrowed to the named example section; negated global requests do not enable that override. These remain bounded text checks, not a general instruction parser.
