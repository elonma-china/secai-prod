import { defineConfig } from "@playwright/test";

/** E2E cho exploit-v2 — KHÔNG tự start FE/gateway (khác pattern app/e2e của pentestcode):
 * endpoint pentestcode/@ias/server + lab target đều cần chạy thủ công trước (xem
 * e2e/README.md), nên test chỉ trỏ vào URL FE đã chạy sẵn qua PLAYWRIGHT_BASE_URL. */
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000, // live-exploit tests override this too (see exploit-v2.spec.ts) — kept in sync
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:5555",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
