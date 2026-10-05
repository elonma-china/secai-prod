# CPU Reward Pipeline Implementation Plan

> **Superseded product model decision (2026-10-05):** The user selected Qwen/Qwen3-1.7B with a scalar score head and approved expert chosen/rejected training, followed by held-out expert agreement evaluation before product release. See `../specs/2026-10-05-qwen3-reward-design.md`. The lexical CPU scorer below is only a technical smoke-test baseline; its serving/promotion tasks are deferred. This document is not a Qwen3 implementation plan.

> Execute inline with focused tests; no delegated workers.

**Goal:** Build a CPU preference reward training/scoring pipeline before expert training data exists.

**Architecture:** A small learned linear scorer over deterministic hashed answer/context features, fitted with pairwise logistic loss. Offline CLI consumes matching admin raw/preference exports and verifies their manifests and adjudications. Grouped holdout separates prompts and source conversations. Serving is explicitly enabled, pins a validated JSON artifact by SHA-256, and rejects demo artifacts. No automatic training, model promotion, chatbot selection or policy fine-tuning.

**Tech Stack:** Python, existing NumPy, FastAPI, pytest; no new runtime dependency, GPU, external model or dataset download.

- [ ] Implement/export validation, group split and lexical CPU scorer with tests for real learning, prompt overlap, reversal, ties and artifact validation.
- [ ] Add offline CLI and evaluation metadata; demonstrate using clearly synthetic fixtures only. No customer data quality claims.
- [ ] Add authenticated reward model status/scoring routes and configuration; fail closed without a pinned production artifact.
- [ ] Check related review regressions, full backend suite according to project instructions, CLI smoke and artifact integrity; document commands and limits.

Production has 7 campaigns, 29 items and 12 adjudications at inspection; no adjudications in non-QA/demo campaigns. Do not train or activate a production model from these QA records. Previous in-progress chat A/B layout changes are outside this pipeline's edits.
