# Report MVP browser smoke — 2026-10-04

Frontend checkpoint: `864c1b8`; backend checkpoint: `b2213e1`. T3 native browser, test-only Vite port18732 and real report router port18731 backed by temporary SQLite. LLM generation is replaced by a deterministic mock marked `[MOCK MODEL — UI SMOKE ONLY]`; this is interface/integration evidence, not model-quality evidence. No customer data or remote inference.

Verified through UI:

- Report page retains old create/statistics tabs; feature-on adds “Theo mẫu”.
- Enter dedicated test report key in password field; key is absent from localStorage.
- Create template “Báo cáo kiểm thử giao diện” with sections Tổng quan/Khuyến nghị; saved version1 appears in DB-backed selector.
- Supply synthetic CVE/IP data, generate report; report ID, pinned template version and revision1 displayed.
- Submit edit instruction; revision2 persisted and displayed. Switch back to revision1 and original content remains visible.
- Upload synthetic Markdown via actual file input; extracted draft populates editor, review notice displayed, template is not auto-saved.

Limitations/findings:

- Initial T3 host was outside Vite allowedHosts; temporary dev-server override added exactly the preview host, no product config changes.
- Isolated report-only backend does not implement old /health, /projects or /sessions; their404s are harness limitations and not production regression evidence.
- Mock edit removed critical facts; independent reviewer separately reproduced real validator acceptance of that omission. Backend fix requested before final PASS. Recheck validation after fix.
- Follow-up at backend5ffb785: mock server reloaded fixed validator and fact-preserving edit response; fresh browser generate/edit produced revision2 retaining CVE-2021-44228 and10.0.0.5. Later R3–R6 changes independently passed focused tests. Temporary smoke servers stopped after verification.
- DOCX import is covered by backend tests, not this browser file-upload smoke.
- Live LLM content score70% remains unmeasured, not inferred from UI success.

Screenshot saved by T3 under browser-artifacts (local, not committed). Temporary data and test key only.
