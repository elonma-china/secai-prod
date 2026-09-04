# Ư3 — Leave-one-hard-negative-family-out robustness check (2026-09-01)

`metrics.json`'s in-distribution numbers can't answer whether the MOF hard-negative fix (round
1–2, `docs/input-classifier-improvement-plan.md`) actually **generalizes** across phrasing
families, or whether the model just memorized each family's specific templates. This runs a
genuine Leave-One-(family)-Out check: exclude an entire hard-negative family from training —
not just some skeletons within it — retrain, and measure the false-positive rate on that
family's corresponding held-out `overdefense.jsonl` category.

Method: `scripts/eval_lodo_family_holdout.py` (offline, `--embed st`). Nothing touches the
shipped assets — every retrain runs in a temp dir; the baseline row scores the currently
shipped head.

## Result

| family | eval category | baseline FP (shipped) | FP with family excluded | verdict |
|---|---|---:|---:|---|
| `hnreport` | report-numbered | 0/32 | 0/32 | **generalizes** from other families |
| `hnaudit` | audit-imperative | 0/36 | **1/36** | **REGRESSES** — family was necessary |
| `hncve` | cve-detail | 0/40 | 0/40 | **generalizes** from other families |
| `hnpentest` | pentest-writeup | 0/40 | 0/40 | **generalizes** from other families |

Side observation: excluding `hnreport` also dropped injection-attack recall by one point
(35/36 vs the shipped 36/36) in that specific controlled run — removing benign counter-examples
can slightly shift the decision boundary. Not a concern for the shipped model (which keeps all
families, 36/36), but worth noting as an artifact of the ablation itself.

## Interpretation

**3 of 4 families genuinely generalize** — the MOF technique teaches something closer to
"trigger word ≠ injection" as a transferable signal, not just per-family template matching.
This is real evidence (not just an assumption from the literature) that the over-defense fix is
robust in the way it was intended to be.

**`hnaudit` is the exception**, and it lines up exactly with what round 1→round 2 already found
empirically: `audit-imperative` was the hardest category (all 6 residual FPs after round 1 sat
in this one category, on the "liệt kê... bypass/khai thác" imperative pattern) and needed its
own dedicated round-2 templates. This LODO result confirms that fix genuinely depends on
`hnaudit`'s specific templates — the other families don't substitute for it. If this category
regresses again in the future, re-adding/expanding `hnaudit`-style hard-negatives (not other
families) is the fix.

## Caveats

- **Excluding a family reshuffles everything else too** (not just removing that family):
  `build_rows()` threads one shared `random.Random(SEED)` through a sequential build, and the
  train/test-split + CATCAP-cap loop iterates a dict keyed by category in first-occurrence
  insertion order. Removing a family's dict key shifts every `rng.shuffle()` call for
  categories that come after it — a butterfly effect that changes which OTHER rows land in
  train vs. test and which VI rows get a no-diacritics/teencode variant (confirmed empirically:
  e.g. excluding `hnreport` changes `hnaudit`'s row count from 136 to 138). This is still fully
  deterministic for a given exclusion (verified in `tests/test_build_sanitize_dataset_exclude_groups.py`)
  and doesn't affect this experiment's validity — training excludes the family either way, and
  scoring happens against a separate, unaffected held-out file — but it means the "excluded"
  dataset isn't simply "the baseline dataset minus one family."
- `hnimp` has no clean 1:1 eval category (its "list payloads for detection rules" framing
  doesn't map onto one `overdefense.jsonl` `cat`) — not tested here.
- Small sample sizes per category (32–40 rows) — a single-row FP swing (0→1) is the whole
  "regression" signal for `hnaudit`; meaningful, but not a large-N statistical result.
- This tests EXCLUSION robustness, not real production distribution shift — still a synthetic,
  template-generated corpus on both the training and eval sides (see `docs/input-classifier-
  improvement-plan.md` §3 for the fuller Ư3 roadmap: real traffic logs + external benchmarks
  like NotInject/LLMail-Inject are the next, unstarted step).
