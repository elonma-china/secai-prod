# Multilang — STATE

- Phase: PLAN; feature implementation/benchmark live/merge/deploy NOT RUN.
- Plan: `docs/plans/multilang-translate.md`.
- Source: `docs/plans/multilang-source-requirements.txt` (exact user attachment).
- Scope: zh/ja/ko/fr/de/es/ru/ar ↔ vi (16 new); en↔vi regression. Same generating LLM only.
- Confirmed defaults: main, zh-Hans, Arabic MSA.
- Base observed/fetched: ce9cd47ee1273018fec1c5cbab944a86d68d6ed5 (main=origin/main).
- Protected import branch snapshot: feature/import-file-translate @4505c570167ebd0e8ddfba752308b1811f88383b.
- Protected OCR branch snapshot: feature/image-ocr-translate @404f93a90958027fe0d2b386c45477b724adffc6.
- Both merge-bases with main: ce9cd47. Agents remain active elsewhere; refresh refs + WIP ownership before every cycle.
- Worktrees for this feature: PLANNED, not created. Adapter branch conditional Gate C only.
- Existing file/OCR `docs/loop/STATE.md` and `LOG.md` are READ-ONLY for this task. This namespace prevents concurrent writes.
- Canonical FE nested inside gateway is untracked; old sibling tracked deletion belongs to user. T5 snapshot only in dedicated FE worktree with explicit file manifest.
- Baseline evidence: 63 targeted translation tests passed during prior read-only review, 17 deselected; not quality evaluation/full-suite success. Other-task baseline documents905pass19fail1skip; not rerun here.
- Local installed Linch1.1.0 differs from pyproject>=1.2.1,<2; isolate environment before code/test acceptance.
- Model local declaration: vllm / DuyTa/Cyber-F1-AWQ / configured context65536. Live revision/quant/engine/max output/LoRA capability unverified.
- GPU_BUDGET_USD: UNSET. DEV_ENDPOINT/reservation: UNSET. NATIVE_REVIEW: UNCONFIRMED. No live spend or Vast mutation by this task.
- Criteria: astra A planning proposal requested; implementation freeze and quality threshold approval PENDING.
- Review/test/adapter/vanguard/integration cycles: all0; quality A/B/C/D NOT RUN.
- Decision: additive multilang namespace + new v3 JSON text API + ASGI composition, legacy/file/OCR untouched. Identity policy and detailed guards frozen at T1 before API CODE.
- Next: finish plan self-review and criteria proposal; user review plan; execution T0/T1 precedes code/benchmark. Resource-dependent tasks stay blocked until numeric budget+endpoint reservation exist.

| Task | Status | Fix/review | Vanguard returns |
|---|---|---:|---:|
| Assessment | PLANNED | 0/5 | 0/3 |
| Core/API | PLANNED | 0/5 | 0/3 |
| FE | PLANNED | 0/5 | 0/3 |
| Adapter | CONDITIONAL, NOT CREATED | 0/5 | 0/3 |
| Integration/docs | PLANNED | 0/5 | 0/3 |

No language marked beta/stable/qualified; all eight new-language rollout flags must start OFF.
