# Assessment review round 4 — CPU response

Date: 2026-10-04. Review source: `.superpowers/sdd/multilang-translate/assessment-review-round-4.md` in the coordination worktree. This checkpoint follows immutable `8e53a00a8adf710dd630196de8341a2585e5d97a`. It addresses the sole residual R01 protocol marker finding; the four other round-3 findings were closed by the independent reviewer and are unchanged.

Model-only response validation now checks an explicit table of `tool_calls`, `function_call`, `tool_call_id`, `tool_choice`, `tools` and `functions` at all three response levels: top-level body, choice and assistant message. Any non-null marker rejects the response, including empty objects/lists and strings. This covers the reproduced choice-level `tool_call_id: "call_42"` bypass. A null marker is permitted, so ordinary assistant responses with optional null protocol fields remain usable. The existing checks for exactly one choice, assistant role, no delta and `finish_reason: stop` remain in force; there is no general unknown-field rejection.

The negative matrix independently enumerates all six marker names, three locations and three non-null shapes (**54 cases**), alongside role/shape tests and a positive null-marker case. `.venv/bin/python -m pytest -q -p no:cacheprovider tests/multilang_assessment` returned **152 passed**. All HTTP responses were mocked; no network, GPU, live model or auxiliary model was used. Compilation, diff and staged secret checks are performed before commit.

Actual corpus, qualified human review, tester-issued reservation, model quality and release evidence remain **BLOCKED / NOT RUN**. The protocol fix is a CPU code guard, not a quality pass.
