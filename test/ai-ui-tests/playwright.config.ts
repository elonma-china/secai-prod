import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  timeout: 3 * 60 * 1000,            // 3 phút mỗi test (chat/translate nhanh; exploit lâu hơn)
  retries: 0,
  workers: 1,                         // tuần tự — tránh nghẽn engine
  reporter: [["list"], ["json", { outputFile: "results/report.json" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "https://ias.id.vn/",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    actionTimeout: 15_000,
  },
});
