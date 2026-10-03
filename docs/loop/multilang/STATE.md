# Multilang — STATE

- Phase: EXECUTION/T0-T1; user authorized implementation. Criteria + preflight + FE preparation active; live benchmark/merge/deploy NOT RUN.
- Plan: `docs/plans/multilang-translate.md`.
- Source: `docs/plans/multilang-source-requirements.txt` (exact user attachment).
- Scope: zh/ja/ko/fr/de/es/ru/ar ↔ vi (16 new); en↔vi regression. Latest user clarification: deployed DuyTa is the runtime orchestrator and translator. Confirmed: maximum3 auxiliary models total (OCR/language-ID/evaluation), tried sequentially; DuyTa excluded from auxiliary count and remains unchanged.
- Confirmed defaults: main, zh-Hans, Arabic MSA.
- Base observed/fetched: ce9cd47ee1273018fec1c5cbab944a86d68d6ed5 (main=origin/main).
- Protected import branch snapshot: feature/import-file-translate @4505c570167ebd0e8ddfba752308b1811f88383b.
- Protected OCR branch snapshot: feature/image-ocr-translate @404f93a90958027fe0d2b386c45477b724adffc6.
- Both merge-bases with main: ce9cd47. Agents remain active elsewhere; refresh refs + WIP ownership before every cycle.
- Worktree created: `/home/ict/secai-multilang-wt`, branch `feature/multilang-translate`, base `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`. This is the authoritative planning workspace. Worker worktrees remain planned; adapter branch conditional Gate C only.
- Existing file/OCR `docs/loop/STATE.md` and `LOG.md` are READ-ONLY for this task. This namespace prevents concurrent writes.
- Canonical FE nested inside gateway is untracked; old sibling tracked deletion belongs to user. T5 snapshot only in dedicated FE worktree with explicit file manifest.
- Baseline evidence: 63 targeted translation tests passed during prior read-only review, 17 deselected; not quality evaluation/full-suite success. Other-task baseline documents905pass19fail1skip; not rerun here.
- Local installed Linch1.1.0 differs from pyproject>=1.2.1,<2; isolate environment before code/test acceptance.
- Model local declaration: vllm / DuyTa/Cyber-F1-AWQ / configured context65536. Live revision/quant/engine/max output/LoRA capability unverified.
- COMPUTE_PROVISIONING: EXISTING_RENTED_INSTANCE, confirmed by user. File/OCR state references Vast instance53846446 / A10080GB; revalidate at execution. DEV_ENDPOINT/reservation: TO_VERIFY. ADDITIONAL_RENTAL: NOT_AUTHORIZED. NATIVE_REVIEW: UNCONFIRMED. No live benchmark or Vast mutation by this task.
- Criteria: astra A draft received at `docs/criteria/multilang-planning-proposal.md`; quality target approximately70% confirmed; operationalized as per-direction sample pass rate≥70%. Rubric freeze and regression/performance margins remain to finalize.
- Review/test/adapter/vanguard/integration cycles: all0; quality A/B/C/D NOT RUN.
- Decision: additive multilang namespace + new v3 JSON text API + ASGI composition, legacy/file/OCR untouched. Identity policy and detailed guards frozen at T1 before API CODE.
- Next: execution T0/T1 precedes code/benchmark. Implementation authorized; CPU code/tests may proceed after contract freeze with flagsOFF while live assessment waits resource slot. Use existing rented compute after endpoint/workload preflight and shared scheduling; no new-rental budget prerequisite for baseline. Adapter quota/time reviewed separately at Gate C.

| Task | Status | Fix/review | Vanguard returns |
|---|---|---:|---:|
| Assessment | PLANNED | 0/5 | 0/3 |
| Core/API | PLANNED | 0/5 | 0/3 |
| FE | PLANNED | 0/5 | 0/3 |
| Adapter | CONDITIONAL, NOT CREATED | 0/5 | 0/3 |
| Integration/docs | PLANNED | 0/5 | 0/3 |

No language marked beta/stable/qualified; all eight new-language rollout flags must start OFF.

- HF reference saved at `docs/plans/multilang-hf-model-inventory.json`: DuyTa only; Qwen alternatives removed following user correction. Metadata only; weights not downloaded, live serving unverified.
- HF latest Cyber-F1-AWQ identifies qwen3_5_moe/compressed-tensors, differs from local comment; must match actual served revision before testing.
- GPU policy: one candidate at a time, shared reservation, capacity check, monitored run, own-process shutdown and VRAM release verification before next candidate. Never unload other agents’ models.

- Auxiliary model shortlist: TO_SELECT in T2 from HF metadata; trial count0/3. OCR remains owned by file/OCR agent; reuse existing contract/evidence, coordinate before any OCR trial. QE/judge offline only.

- Active workers: Astra criteria (planning worktree, docs-only); Sol preflight (assessment worktree); Sol FE (FE worktree, preparation before contract). Core worktree created, worker waits slot.
- SharedGPU: other task STATE explicitly grants OCR exclusive benchmark reservation; no live inference/training/model loads authorized until reservation release. Read-only preflight observed ~7586MiB free, ~41%util; not a capacity guarantee.

- Pre-existing import/OCR cross-merge conflict at365e9e0/99cd0a2 in profiles.yaml and translation/settings.py (merge-tree exit1); main vs each branch clean. Not caused by multilang. Refresh at integration; no protected branch modified.
