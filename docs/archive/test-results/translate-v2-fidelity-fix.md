# Translate v2 semantic fidelity candidate

Candidate code commit: `9702d3e5a2f9575309348aead7deebbb35fce800` (base `bfab8ebc96e25b23325fd72a1d3bf034e9aea315`). This is an implementation and CPU regression result, not a semantic quality PASS. No live LLM or GPU call was made from this worktree.

## Diagnosis and change

The independent FLORES-101 review found 56 semantic FAIL and 6 BLOCKED among 216 actual calls. Short source sentences reached `translate_blocks` intact; its output path only restores and checks protected technical literals. The model generated fluent translations with changes to source propositions. The `translate_v2` system profile gave only a broad meaning-preservation instruction, while the shared provider sent sampling temperature 0.85. Linch accepts a per-run temperature, but the provider's `extra_body.temperature` is merged into the HTTP body afterward, so a per-run setting alone cannot override it.

The candidate adds a lazy, dedicated Linch Agent/provider for `translate_v2` with temperature 0.0 and universal instructions to retain entity and type, count, negation and quantifier scope, conditions and time, and degree of certainty or evidence. It forbids unsupported inference and strengthened claims. The existing Agent/provider and `translate` profile retain their sampling configuration. The dedicated Agent uses the same session/run stores and hook factory, with a separate anonymous session and the profile's empty tool override. This adds one Agent/provider on first v2 use, but no new model or dependency. A lower temperature and stronger prompt are hypotheses for improvement; only fresh live output reviewed against frozen references can establish whether they help.

## CPU verification

A `httpx.MockTransport` test inspected the **actual outgoing OpenAI-compatible JSON**: the legacy provider sent `temperature: 0.85`; the v2 provider sent `temperature: 0.0`, both with model `DuyTa/Cyber-F1-AWQ`. Routing tests checked that only `translate_v2` selects the dedicated Agent, anonymous session IDs differ, shared store identity remains, and v2 exposes zero tools. Focused tests (`test_translate_v2_sampling.py`, `test_translate_v2_service.py`, `test_prompts.py`) completed **31 passed**. The first two new tests were observed failing before implementation. A private `/tmp/secai-translate-quality-deps` target supplied pinned `lingua-language-detector==2.0.2` for the focused run; no shared venv was modified.

The full suite used the historical baseline policy in [translate-baseline-command.md](/home/ict/AIEngines/secai/secai-agent-gateway/docs/test-results/translate-baseline-command.md): `env -i`, offline HF, disabled external proxies, private DB/cache paths, `pytest-timeout==2.4.0`, and `--timeout=10 --timeout-method=signal`. It used the existing read-only `/home/ict/secai-translate-import-wt/.venv-import/bin/python`, this checkout's `src` on `PYTHONPATH`, and no live model calls. Result: **1,017 collected; 997 passed, 19 failed, 1 skipped, 0 errors** in 164.45 seconds; exit code 1. The original baseline had 925 collected, 905 passed, 19 failed, 1 skipped. Comparison by exact JUnit `(classname, name)` found all **925 baseline IDs retained with identical PASS/FAIL/SKIP status**; 92 additional IDs all passed. The failures are the same inherited Bughunt external-tree, PoC source/catalog, and exploit endpoint timeout tests. Thus the full suite is not green, but this branch added no failing test ID under the same harness policy.

Evidence files:

| File | SHA-256 |
|---|---|
| `translate-v2-fidelity-full.junit.xml` | `f63bfd05856f3986eb10337927e9af954498311c3e5e25e3eea4cfbf2dfba881` |
| `translate-v2-fidelity-full.stdout.log` | `97dae305cb38bbd0e029a654a5e73a549bfb7e59935c7851dc482f9b80d8a525` |

`git diff --check` passed. The known 6 Korean-source BLOCKED judgments remain unresolved; no annotation, reference, rubric or historic output was changed. Fresh frozen FLORES and held-out public-reference live runs, followed by independent proposition-level review, are still required before an IMP-18/20 quality decision.
