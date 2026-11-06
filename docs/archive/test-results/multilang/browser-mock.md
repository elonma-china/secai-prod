# Browser mock evidence — FE0ea22cb

2026-10-04. Real Chromium via T3 collaborative preview, static build served from isolated FE worktree over private Tailscale address, API fetch stub installed in browser only. No live LLM calls and no production runtime mocks added. NOT quality evidence or real-backend smoke.

- At1280×800: vi→ar POST contains only text/source_lang/target_lang. Output matches fixture including Arabic joining characters, CVE/IP/URL and literal `<script>alert(1)</script>`.
- Arabic result `dir=rtl`; CVE/IP/URL each in `bdi dir=ltr`. No script DOM node created. Clipboard API stub receives exact original output string; this verifies application copy argument, not OS clipboard permission.
- At390×844 (iPhone12Pro CSS viewport): document scrollWidth390, panel width317/scrollWidth317. Inputs/buttons fit panel horizontally; vertical scrolling expected.
- vi→zh-Hans returns `检查 CVE-2024-1234 和 192.0.2.1。`, LTR and document width390.
- vi→ru returns `Проверьте CVE-2024-1234 и 192.0.2.1.`, LTR and document width390.
- Source text used: `Kiểm tra CVE-2024-1234 và 192.0.2.1.` All translations supplied by deterministic mock.
- Native snapshot/save API repeatedly returned PreviewAutomationExecutionError although navigate/evaluate/click/type/resize worked. No screenshot artifact or visual-font-quality claim. Screenshot evidence remains pending tooling recovery.

Component tests cover broader cancellation/stale/errors states; independent reviewer separately checks code. This report does not substitute live18-direction evaluation.

- MasterOFF capabilities404 after remount: new panel/toggle absent, disabled explanation visible; legacy en/vi/auto selector remains. No live inference attempted.

## Recheck after FE font fix a783280

Reloaded final static build: DOM has0 Google Fonts links and Performance resource entries have0 Google font CDN requests. Repeated mobile390px vi→ar mock: ArabicRTL output and CVE/IP intact; document scrollWidth390. Snapshot tool still fails, no image artifact.
