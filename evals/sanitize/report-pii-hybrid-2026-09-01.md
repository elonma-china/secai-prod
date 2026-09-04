# Ư5 — PII hybrid exploration: GLiNER vs regex gate (2026-09-01)

Offline, no vast needed — GLiNER is an independent small NER model downloaded from HuggingFace
(`urchade/gliner_multi_pii-v1`), unrelated to the vast-hosted Cyber-F1/sec-embedding serving.
`scripts/eval_pii_hybrid.py` compares it against the current hard gate
(`security.require_public_input`, a regex/pattern check) on the SAME ground truth
`dataset.jsonl` uses (`pii=1`: rows containing a synthetic secret shape from `_fake_secret` —
openai/aws/pk/password/bearer/card/cccd/phone/email/jwt/ghp; `pii=0`: 200 benign rows).

## Headline numbers (threshold=0.5, the library default)

| method | recall | false-trip (on benign) | notes |
|---|---:|---:|---|
| regex gate (`require_public_input`) | 32.1% (61/190) | **0.0%** (0/200) | current production hard-block |
| GLiNER zero-shot | **75.8%** (144/190) | **15.0%** (30/200) | 9 generic PII-ish labels, threshold 0.5 |
| hybrid (regex OR GLiNER) | **82.6%** (157/190) | 15.0% (30/200) | union — FP unchanged since regex has 0 FP to add |

Latency: GLiNER mean 76.1ms / p95 90.4ms per call (small model, CPU) — acceptable for a
synchronous pre-agent gate, same order of magnitude as the embedding hop the injection
classifier already adds.

**This confirms the literature's general claim** (hybrid regex+neural beats either alone on
recall) — but with a REAL, non-trivial precision cost that the literature review alone didn't
quantify for THIS project's specific traffic shape.

## Root cause of the false-trips (same failure class as the injection over-defense fix)

Inspecting the 30 false-trips shows one dominant, very legible pattern:

```
text: How do I do hashing passwords with bcrypt?         -> matched "passwords" as password (0.53)
text: How do I do verifying a JWT correctly?               -> matched "JWT" as jwt token (0.78)
text: Cách regex kiểm tra định dạng email an toàn?          -> matched "email" as email address (0.53)
text: Cách salt cho mật khẩu đúng cách?                     -> matched "mật khẩu" as password (0.82)
```

GLiNER's zero-shot labels are bare concept words (`"password"`, `"jwt token"`, `"email
address"`). On a CONCEPT question ("how do I hash passwords") there is no actual secret VALUE in
the text — but the model matches the label word itself as if it were an instance of the thing.
This is **exactly the same failure class** as the injection classifier's over-defense (round
1–2 of this session): "concept mention ≠ actual instance." Same root cause, different
subsystem.

## Threshold sweep — no single threshold buys both recall and precision

| threshold | recall | false-trip |
|---:|---:|---:|
| 0.30 | 177/190 (93.2%) | 51/200 (25.5%) |
| 0.50 | 144/190 (75.8%) | 30/200 (15.0%) |
| 0.60 | 129/190 (67.9%) | 20/200 (10.0%) |
| 0.70 | 99/190 (52.1%) | 14/200 (7.0%) |
| 0.80 | 78/190 (41.1%) | 5/200 (2.5%) |
| **0.85** | 65/190 (34.2%) | **1/200 (0.5%)** |
| 0.90 | 51/190 (26.8%) | 1/200 (0.5%) |

At 0.85, false-trip drops to near-zero — but recall (34.2%) is now **no better than the regex
gate alone (32.1%)**. The tradeoff curve offers no threshold where GLiNER, with this label set,
is a clear win on both axes simultaneously. The bottleneck is the LABEL DESIGN (bare concept
words), not just threshold calibration — the same lesson as MOF hard-negatives for the injection
classifier: fixing the label/prompt design (few-shot phrasing biased toward "a concrete value
of X is present", not just the word "X") is the next step, not something this exploratory pass
had time to fully solve.

## Recommendation

- **Do NOT swap GLiNER in as a hard-block replacement for `require_public_input` as-is.** At any
  threshold that recovers meaningful recall gain (≤0.7), it reintroduces real over-defense —
  the same class of problem this session spent two rounds fixing for a different subsystem.
- **If pursued further**: (a) redesign the GLiNER label prompts (few-shot examples or
  value-oriented phrasing instead of bare concept words) and re-run this same harness before
  touching production; (b) consider per-label thresholds, not one global cutoff (e.g. "credit
  card number"/"api key" plausibly separate more cleanly than "password"/"jwt token" did here —
  not measured per-label in this pass, worth doing before any integration decision).
- **Lower-risk near-term option**: use GLiNER as a SOFT signal only (like the classifier's own
  `pii` flag — logged/audited, never blocking), at a moderate threshold (~0.6–0.7), rather than
  a hard 422. This captures some of the recall upside with none of the hard-block over-defense
  risk, and doesn't require solving the label-design problem first.
- **Architecture question stays open regardless**: GLiNER needs `torch` at inference time,
  conflicting with the numpy-only air-gap serving runtime constraint (see docs/input-classifier-
  improvement-plan.md §8 "Ghi chú kiến trúc"). Nothing here resolves that trade-off — it's a
  separate decision from whether GLiNER's accuracy is good enough.

## Reproduce

```
uv pip install gliner   # not in pyproject — dev-only exploration, not shipped to runtime
python scripts/eval_pii_hybrid.py --model urchade/gliner_multi_pii-v1 --n-negative 200
```
