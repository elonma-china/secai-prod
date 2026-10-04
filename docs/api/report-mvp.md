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
