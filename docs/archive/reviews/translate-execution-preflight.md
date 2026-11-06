# Translation execution preflight — 2026-10-03

Status: Git/SSH/provider connectivity verified; baseline and criteria pending. No feature code or OCR deployment performed.

- Git fetch origin main succeeded; local main = origin/main = ce9cd47ee1273018fec1c5cbab944a86d68d6ed5. Dirty work checkout preserved.
- Criteria worktree: /home/ict/secai-translate-criteria-wt; branch chore/translate-criteria.
- Baseline worktree: /home/ict/secai-translate-baseline-wt, detached locked SHA.
- Feature worktrees prepared: /home/ict/secai-translate-import-wt and /home/ict/secai-translate-ocr-wt; no implementation yet.
- Vast API reports running instance 53846446; SSH succeeds. First contact registered previously unknown host key using accept-new; existing key validation remains enabled. Instance in old deploy/status.sh is stale.
- GPU A100-SXM4-80GB: total81920MiB, used71040MiB, free10010MiB; three existing compute processes. No process stopped or reconfigured. Disk /workspace 74GB free.
- Runtime observed: Python3.12.13; torch2.11.0+cu130; transformers5.13.1; vllm0.25.1; driver535.230.02. No docker CLI found remotely. OCR must use isolated runtime/process; compatibility of OCR packages not yet validated.
- OCR operating budget: peak7168MiB plus free2048MiB headroom; recheck before every load/run; serial benchmarks. This is a cap, not a measured OCR memory claim. If no candidate fits, stop for resources rather than reclaim production VRAM.
- Existing .env endpoints point at an older host. Current mapped ports were verified by /v1/models: remote8000=embedding, remote8001=LLM, remote8002=reranker. Staging config must use current discovery/tunnel, not stale .env URLs.
- Each /models endpoint returned200 with the existing configured credential (value never stored in report). Synthetic LLM chat completion200 valid choices ~0.64s; embedding request200 valid data ~0.79s. Reranker only model discovery verified, not ranking quality.
- Existing services not restarted; no local config/secret modified. Remote OCR port still must be reserved/bind-checked by implementer before deployment. No new Vast rental/paid capacity change authorized.
- Baseline initial run exposed missing external claude-bug-bounty tree; tester continues diagnosis/full run, no baseline PASS claim.

Next gate: root reads final baseline + frozen astra A contract/criteria. Missing OCR-runtime compatibility or baseline dependency must be recorded honestly, never replaced by a fake PASS.
