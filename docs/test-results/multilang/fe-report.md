# FE multilang — baseline preparation

Date: 2026-10-03. Branch: `feature/fe-devspace-multilang` at base `ce9cd47`.

The canonical FE source was the untracked directory `/home/ict/AIEngines/secai/secai-agent-gateway/secai-fe-devspace` selected by the user. Copied 29 allowlisted files (`src/**`, `.gitignore`, `README.md`, `index.html`, `package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts`) into this isolated worktree. The source file hashes are in [fe-source-manifest.sha256](fe-source-manifest.sha256); `sha256sum -c` passed for all 29 copied files. No source `.env`, log, archive, `node_modules`, `dist`, or generated TypeScript build info was copied. The tracked sibling `secai/secai-fe-devspace` was not changed.

Baseline verification in `secai-fe-devspace`:

- `npm ci`: exit 0; npm reported 3 dependency audit findings (1 low, 1 moderate, 1 high). No dependency upgrades were applied during baseline preparation.
- `npm run build`: exit 0; TypeScript and Vite build passed. Vite reported one bundle-size warning for the existing ~598 kB JavaScript chunk.
- `git diff --check`: exit 0.

At this baseline checkpoint, feature implementation had not started. Build output and installed packages are ignored local artifacts. Baseline commit: `8b41f62`.

## Dependency audit at baseline

`npm audit --json` returned exit 1 with 3 package findings (1 low, 1 moderate, 1 high). The installed versions below come from `npm ls dompurify esbuild vite --all --json`.

| Package | Installed | Audit severity | Advisory / affected range | `fixAvailable` |
|---|---:|---|---|---|
| `dompurify` | 3.4.15 | Low | [GHSA-p98j-92pf-mc4p](https://github.com/advisories/GHSA-p98j-92pf-mc4p), 3.4.13–3.4.15 | `true` |
| `esbuild` | 0.21.5 | Moderate | [GHSA-67mh-4wv8-2f99](https://github.com/advisories/GHSA-67mh-4wv8-2f99), ≤0.24.2 | Vite 8.3.2 (major) |
| `vite` | 5.4.21 | High (aggregate package severity) | [GHSA-4w7w-66w2-5vf9](https://github.com/advisories/GHSA-4w7w-66w2-5vf9), ≤6.4.1; [GHSA-v6wh-96g9-6wx3](https://github.com/advisories/GHSA-v6wh-96g9-6wx3), ≤6.4.2; [GHSA-fx2h-pf6j-xcff](https://github.com/advisories/GHSA-fx2h-pf6j-xcff), ≤6.4.2; also depends on affected `esbuild` | Vite 8.3.2 (major) |

Reachability assessment: the High Vite finding affects the **development server**, and `vite.config.ts` binds it to `0.0.0.0` with Cloudflare tunnel hosts allowed. This configuration can expose it beyond localhost, so the development and preview workflow must treat the finding as live. The `.map` traversal advisory additionally requires a predictable, sensitive source map file outside the project. The two other Vite advisories target Windows path/UNC handling, which is outside this Linux worktree but relevant to Windows dev use. The static `dist` build does not run the Vite server; this does not clear the exposed dev-server risk. `esbuild` is used via Vite's development tooling. `dompurify` is imported in `src/components/FileCode.tsx`; the reported exploit requires an `afterSanitize` hook and `IN_PLACE`, neither of which is configured at that call site. This is a code-path assessment, not a general clearance of that dependency. No audit fixes or dependency changes were applied.

## Feature implementation and verification

The new UI consumes only `GET /ai/v3/translate/languages` and JSON `POST /ai/v3/translate`. The legacy en↔vi screen remains the default and keeps its existing stream API. A capabilities 404 hides the new mode and explains why; enabled, disabled and beta pair states come from the server response. The new panel sends only `text`, `source_lang` and `target_lang`; it displays server warnings and metadata, maps `error.code` to safe user guidance, and never retries inference automatically. Edits, pair changes, cancel and unmount abort in-flight work and discard stale responses.

The result uses React text nodes with LTR `bdi` spans for Latin technical strings. Arabic output uses `dir=rtl`; clipboard copy uses the untouched `translated_text` string. CSS uses local font fallbacks and a single-column layout on narrow screens. No model selector, new SSE consumer, upload/job control or file/OCR route was added.

Verification after the feature and dependency update:

- `npm ci`: exit 0, 253 packages installed from the feature lockfile, 0 audit findings.
- `npm test`: exit 0, 21/21 API and component tests. Fixtures cover capabilities 404, enabled/disabled/beta/stable, directions and swap, exact JSON payload, code-point limit, warnings/metadata, HTML/RTL/technical spans and exact copy, stale response, cancel, HTTP 400/403/413/422/429/502/503/504 and network failure.
- `npm run build`: exit 0, TypeScript and Vite 6.4.3 passed; the existing bundle-size warning remains (~612 kB JavaScript chunk).
- `npm audit --audit-level=low`: exit 0, 0 findings.
- Dev server on `http://127.0.0.1:5177/`: HTTP 200 and `ss` confirms bind `127.0.0.1:5177` only. Browser route: open the app and select **Dịch văn bản** in the sidebar. Intercept `GET /ai/v3/translate/languages` with a 9-language/16-pair capabilities fixture before selecting **Mở dịch đa ngôn ngữ**; intercept `POST /ai/v3/translate` with a schema-complete JSON fixture. These are browser mocks, not live translations.

Dependency remediation used the smallest compatible patched line verified locally: Vite `6.4.3` (and esbuild `0.25.12`), Vitest `4.1.11`, DOMPurify `3.4.16`. `@vitejs/plugin-react` remains `4.7.0`, whose Vite 6 peer range is compatible. Both dev and preview servers now default to loopback. The baseline audit table above records inherited findings; the current lockfile has 0 npm audit findings. This does not certify unrelated application behavior or backend security.

Browser desktop/mobile screenshots and real-backend smoke are pending the integration environment and quality/resource gates. No live model inference was run for this FE report.
