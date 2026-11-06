# Specialist gateway adapter checkpoint

Branch `feature/multilang-specialist-provider` adds an explicitly selected private adapter to v3 only. The default incumbent DuyTa path remains selected. No live model or network request was made in this worktree. The CPU runtime team supplied the candidate identity, tokenizer hash, 512-token encoder and 256-token decoder bounds, target prefix, and terminal-EOS contract; the adapter requires pinned configuration and validates the returned model, limits, and output.

Verification from `secai/secai-agent-gateway` with `PYTHONPATH=src`, `/tmp/secai-multilang-final-env/bin/python`, `SESSION_STORE_BACKEND=memory`, and private `/tmp/secai-specialist-*` database paths:

| Command | Result |
| --- | --- |
| `python -m pytest -q tests/multilang/test_specialist_provider.py` | 5 passed at the first adapter checkpoint. |
| `python -m pytest -q tests/multilang tests/multilang_security` | 69 passed after final changes; two inherited duplicate FastAPI operation-ID warnings. |

The new tests cover exact structured wire pairs, Simplified Chinese alias mapping, mixed concurrent directions, preserved literals, missing tokenizer evidence, model mismatch, unavailable service, oversized response, public/malformed endpoint rejection, decoder output bound, and encoder prefix bound. The current tests use a deterministic token counter or an HTTP mock; genuine tokenizer/model equivalence, terminal-EOS enforcement by the server, CPU latency, and quality remain outside this adapter checkpoint. The source documentation is [multilang-specialist-adapter.md](../../api/multilang-specialist-adapter.md).
