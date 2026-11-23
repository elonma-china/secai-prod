import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "ui.spec.ts",
  timeout: 35 * 60 * 1000, // trần; mỗi ca tự đặt hạn riêng từ cases.json
  retries: 0,
  workers: 1, // tuần tự: các ca PoC dùng chung LLM, không chạy song song
  reporter: [["list"]],
  use: { baseURL: process.env.PLAYWRIGHT_BASE_URL || "https://ias.id.vn", headless: true, actionTimeout: 15_000 },
});
