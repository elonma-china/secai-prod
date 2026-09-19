# Nghiệm thu / hand-over docs — index

This folder gathers the acceptance-test ("nghiệm thu") and hand-over paperwork that used to
be scattered loose across `/home/ict/AIEngines/` and `/home/ict/AIEngines/secai/`. Files here
are **copies** consolidated for discoverability; see "Originals" below for what the orchestrator
can remove after review. This is paperwork/evidence, not code — it does not affect the running
gateway. For the *live* showcase API that serves acceptance evidence to the FE at runtime, see
`docs/nghiem-thu-showcase/` instead (that one documents `/showcase-api/*`, still separate on
purpose since it's an API reference, not a document deliverable).

## What's here

| File | What it is | Status |
|---|---|---|
| `Nghiem-thu-AI.xlsx` | Official acceptance workbook ("Nghiệm thu AI"), rewritten 2026-09-06 with evidence steps in a new column next to the original col G (col G itself untouched). Canonical/current version of the "Nghiem-thu-AI" line. | **Canonical** |
| `Nghiem-thu-AI-tomtat.md` | Human-readable summary of `Nghiem-thu-AI.xlsx` — what the workbook is, the evidence-color legend, how to navigate it. | **Canonical**, companion to the xlsx above |
| `MINH-CHUNG-AI-SUMMARY.md` | Earlier (2026-09-05) summary of the same "fill the evidence column" work, describing `Tùng - TeamAI - Tài liệu nghiệm thu - FILLED.xlsx`. Superseded by `Nghiem-thu-AI-tomtat.md` (2026-09-06 rewrite) but kept for history/traceability since the FILLED workbook it describes is also kept. | Stale (superseded), kept for traceability |
| `Tùng - TeamAI - Tài liệu nghiệm thu.xlsx` | Original acceptance workbook as delivered by the customer-side contact ("Tùng"), unmodified. | **Canonical** (source-of-truth original) |
| `Tùng - TeamAI - Tài liệu nghiệm thu - FILLED.xlsx` | The above, filled in with the evidence column per `MINH-CHUNG-AI-SUMMARY.md`'s convention (2026-09-05 pass, earlier than `Nghiem-thu-AI.xlsx`'s 09-06 rewrite). | Superseded by `Nghiem-thu-AI.xlsx` for current evidence, kept because it's a distinct deliverable already handed over |
| `AutoScan_24_8.docx.md` | Large (3.1 MB) Markdown export of an AutoScan-related Word doc (2026-08-24 content). Reference/data document, not acceptance evidence per se — kept here because it was grouped with the other loose hand-over docs in the source audit. | Reference — not re-verified in this pass |
| `BAO-CAO-RA-SOAT.md` | Review/reconciliation report (2026-09-16) for `ChuyenDe9.docx` / `ChuyenDe10.docx` — explains the correction principle used (keep all simulated numbers in tables; resolve contradictions by editing wording/labels only). Read this before reading ChuyenDe9/10. | **Canonical**, companion to the two docx below |
| `ChuyenDe9.docx` | Deliverable doc, post-review (see `BAO-CAO-RA-SOAT.md`). | **Canonical** |
| `ChuyenDe10.docx` | Deliverable doc, post-review (see `BAO-CAO-RA-SOAT.md`). | **Canonical** |
| `RA-SOAT-TON-DONG-NGOAI-GATEWAY-2026-09-19.md` | Read-only audit (2026-09-19) of everything *outside* the gateway's tracked scope: secrets-at-rest, untracked/at-risk data dirs, uncommitted loose ends inside the gateway repo, governance flags (KPI numbers, hardcoded pass/fail), and how `HO-SO-GOC-NGHIEM-THU-AI/` + `KPI-VA-GEN-TEST/` are runtime-mounted into the gateway container. This is the audit that this doc-consolidation task (and the `deploy/` move) was scoped from. | **Canonical**, most recent/most complete map of the mess |

Not copied here (explicitly excluded per the consolidation task):

- `ChuyenDe9.docx.bak`, `ChuyenDe10.docx.bak` — pre-review backups superseded by the reviewed
  `.docx` above. **Disposable** once the reviewed versions are confirmed good.

## Not moved (stays where it is, out of scope for this consolidation)

- `secai/TAI-LIEU-DU-LIEU-PHAN-HE-AI.md` — already git-tracked; describes the CVE knowledge-base
  data stores (not acceptance/hand-over paperwork). Left in place.
- `secai/cves.postman_collection.json`, `secai/stream-demo.sh` — dev/demo tooling, not documents.
- `secai/ui-history-server-runs.png`, `secai/ui-live-run-starting.png`, `secai/ui-service-status.png`
  — ad-hoc screenshots (already flagged as disposable scratch by the 2026-09-19 audit above).

## Recommended follow-up (for the orchestrator, not done here)

Once these copies are reviewed and confirmed accurate:
- Remove the loose originals at `/home/ict/AIEngines/secai/…` and `/home/ict/AIEngines/…` (see
  the "Originals to remove" list in this task's final report) — they're now duplicated here.
- Delete the two `.docx.bak` files — not copied anywhere, purely disposable.
- Decide whether `MINH-CHUNG-AI-SUMMARY.md` and the older FILLED workbook are worth keeping for
  traceability or can be dropped once `Nghiem-thu-AI.xlsx` is confirmed as the sole official copy.
