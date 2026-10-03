# Browser → HTTP staging smoke

2026-10-04. FE build38c99f4 (reports through d7c7eab), backend core bfe4e6b. Repeat on final integration SHA required. **Transport/UI PASS on this snapshot; live model quality NOT RUN.**

T3 preview initially worked for mocked-response checks but later returned explicit unavailable-host error and instructed headless fallback. Playwright CLI Chromium used session multilang-root. Browser fetch was bridged to disposable real HTTP ASGI backend; response bodies were not mocked in the browser. Backend injects fake LLM, tokenizer counter and legacy runner, with outbound socket.connect denied. Thus this checks UI/HTTP/service composition, not provider or genuine tokenizer correctness. Health degraded and existing chat/project UI requests fail because this staging only serves the translation smoke; screenshot status correctly remains interrupted.

- vi→ar, vi→zh-Hans, ja→vi: HTTP200 through browser with correct normalized languages, CVE-2024-1234 and192.0.2.1 preserved. One chunk/one attempt; uncertainty warnings rendered.
- Arabic output dir=rtl; CVE/IP bdi dir=ltr. DOM text exactly matches returned text. Copy API received exact unchanged text (clipboard API stubbed; operating-system permission/paste not tested).
- Desktop1280×900 and mobile390×844 screenshots inspected. At mobile document scrollWidth390 matches viewport390; translation controls remain usable and result reachable by scrolling. Existing outer navigation/header is clipped at mobile; not introduced by the multilang panel.
- Runtime Google-font links0. Prior browser-mock report covers malicious HTML as literal text, Russian and master404 hiding with mocked capabilities.

Artifacts outside tracked source: output/playwright/multilang-http-ar-desktop.png, multilang-http-ar-mobile.png, multilang-http-ar-mobile-result.png in orchestration worktree. Screenshot of result captured after scrolling the existing main container. No production endpoints/keys used. HTTP staging details/lifecycle are ephemeral, not deployment instructions.

## Replay at core dbda8ec

Restarted only the owned local fake-LLM staging on frozen core `dbda8ecc1967d6aa2d33e887560379fd0bb7cddb`; same FE build and isolation. HTTP client passed capabilities/CORS, three v3 pairs, disabled403/extra-field400, and both legacy directions JSON plus SSE token/final. Playwright repeated all three directions with HTTP200 and awaited actual returned text in the UI. Screenshots `output/playwright/core-dbda8ec-ar-mobile.png` and `core-dbda8ec-ar-desktop.png` were captured and visually inspected. Literal text remains exact; Arabic RTL renders, mobile result reachable without page horizontal overflow. This replay uses fake LLM/token counter and remains non-quality evidence. Final combined integration replay remains pending.
