# Report templates and editable reports — design draft

Status: local design, not implementation or release approval. Governing scope is `report-feature-user-spec.txt`, plus user confirmations: database storage; UI template creation by BOTH form and import; prompt-based editing of generated reports.

## User workflows

1. Author a template in a form: name, ordered sections, required fields, input schema, writing rules, missing-data behavior, optional reference report. Save a draft.
2. Alternatively import DOCX/Markdown/TXT: upload with resource limits, extract text without executing content, retain original privately, produce an editable draft. Ambiguous extraction is surfaced for review; never silently claim faithful Word page layout reconstruction. Import must not call a remote model without data approval.
3. Preview a draft with sample data. Publication requires schema checks, relevant regression checks and authorized action. Revisions are immutable; publish/disable/rollback changes the active pointer transactionally.
4. Select a published report type/preset and fill its generated form. The API pins the exact template/prompt versions at request acceptance; concurrent publication cannot change an in-flight generation.
5. View validated report and request natural-language edits, such as shorter overview or recommendations as a table. Each accepted edit creates a revision of the report, never overwrites the shared template. Required sections and factual provenance remain enforced; unsupported edits return a clear error rather than a fabricated result.
6. Browse prior report revisions and restore one as a new revision. Preserve history and ownership checks for every read/write/export of originals and revisions.

## Storage

Proposed initial deployment: dedicated SQLite report database with configurable path, separate from existing session tables. Use transactions, foreign keys, bounded lock wait and optimistic concurrency for publication/edit operations; run blocking SQLite/file operations outside the request event loop. This assumes a single-host deployment; distributed writers require revisiting storage before release.

Logical entities: templates and immutable template versions; presets and immutable prompt versions; reports and immutable report revisions; private imported assets; audit events. Bind all scoped data to trusted tenant/owner identity once the identity contract is selected. Report revisions pin template, preset, model and adapter identity plus parent revision and validation outcome.

Application audit API is append-only. This does not make a local SQLite file tamper-proof against a host/database administrator. If the customer's threat model requires independent tamper resistance, select an external append-only audit destination before claiming that guarantee.

## New API boundary (provisional)

Prefix `/ai/v2/reports`; no changes to legacy `/ai/generate-report` or `/ai/generate-statistical-report` contracts or behavior.

| Resource | Proposed operations |
|---|---|
| `/templates` | List permitted templates; create form-authored draft |
| `/templates/import` | Upload DOCX/MD/TXT into draft |
| `/templates/{id}/versions` | Inspect versions; create revised draft |
| `/templates/{id}/preview` | Validate/preview draft with approved sample inputs |
| `/templates/{id}/publish` | Run publication checks, atomically activate version |
| `/templates/{id}/disable` | Disable future selection/generation |
| `/templates/{id}/rollback` | Activate a previously valid version with audit |
| `/prompts` and version operations | Read permitted catalog; manage presets with authorized role |
| `/` | Generate report using preset/version and structured input |
| `/{report_id}` | Read an owned, validated report |
| `/{report_id}/revisions` | List history; create revision using edit instruction and expected parent |
| `/{report_id}/restore` | Restore an owned prior revision as a new revision |

Exact schemas, limits, progress/stream protocol, idempotency, errors and role mapping must be frozen in the contract before independent coders start. No raw model stream is presented as an accepted report before validation. Errors distinguish invalid input, unauthorized action, stale revision, disabled template, excessive size/rate, upstream failure and validation failure.

## Generation and editing

Use only the configured incumbent report LLM through a new compatibility wrapper; no changes to existing provider/runner contracts. Runtime model identity must be verified, not inferred from a default config or a documentation name. No inference or external template upload is currently authorized.

Treat user input, imported content and edit instructions as bounded data, separated from platform invariants. Avoid executable template engines or arbitrary expression evaluation. Derive tables and aggregates deterministically from supplied structured data and preserve critical literals. Validate required structure, parseability, factual entities/important numbers, missing-data behavior and absence of leaked internal instructions before returning accepted output. Limit retries and reject unresolved failures. Measure rejection/completion rates separately from correctness of accepted reports.

For long input, design bounded chunking with provenance and literal accounting; free-form summarization alone cannot prove fact preservation. No unrestricted retrieval may introduce facts absent from approved input. Existing report profiles permit retrieval and therefore cannot be inherited unchanged without reviewing this boundary.

Edit instruction permission is distinct from permission to administer templates or alter data sources. All report edits run the same validators. Formatting changes may be accepted; instructions to invent facts or delete required sections are rejected with an explanation.

## Minimal integration and file ownership

- New backend directory `src/secai_gateway/report_v2/`, focused `tests/report_v2/`, assessment artifacts isolated from release code.
- One router registration hook in `app.py`; global flag defaults off. A configuration field may require a minimal `config.py` addition. Both are documented overlap exceptions, not permission to refactor shared code.
- Per-template enable state supplements global flag. With global flag off, legacy behavior remains and the new API is unavailable.
- SOL registry owns template schemas/storage/import/publish/audit; SOL core owns presets/generation/revisions/validators/API. Shared types and identity interface must be agreed before either writes them.
- Dedicated SOL FE owns report screens/components/new API client functions. Source snapshot provenance must be recorded because nested FE is untracked on base and overlaps multilingual FE changes. No WIP branch is merged without user authorization.
- ASTRA A owns criteria; fresh SOL reviewer/tester and Vanguard own independent evidence; ASTRA B owns final integration after gates. Root only coordinates/documents.

## Ordered gates and current blockers

1. Criteria before code/benchmark; then isolated legacy test baseline with external services mocked and network access controlled.
2. ASSESS incumbent configuration and approved templates; freeze DEV/TEST/unseen-template partitions. No invented customer template corpus.
3. Gate A baseline; user confirms soft thresholds once. Preserve all hard thresholds. Gate B prompt/pipeline optimizations; Gate C adapter only when needed and explicitly permitted by prerequisites; Gate D honest per-template conclusion.
4. Parallel feature implementation after assessment decisions and contract; independent review; >=30 samples/template per round, >=3 rounds; update/rollback/race/edit tests; independent security gate; integration; verified API handoff.

Blocking customer/environment facts: actual initial templates/reference reports or authorization to design seed templates; trusted identity/tenant source and role mapping; permitted dev endpoint/budget/sanitized data for external inference. Form/file support is confirmed and does not resolve these facts. Local documentation/criteria and a properly isolated legacy baseline can proceed independently.

No production deployment, no main merge, no new GPU rental and no external sensitive-data transfer are implied by this draft.
