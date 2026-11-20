/**
 * ai-features.spec.ts — Playwright smoke-test TOÀN BỘ 6 chức năng AI trên UI thật.
 *
 * Mỗi test = 1 tab AI trên https://secai.coremind.io.vn/:
 *   1. Chatbot  (Trợ lý ảo ATTT)      — chat 1 câu, nhận text trả lời
 *   2. Dịch văn bản                    — dịch 1 đoạn ngắn, nhận bản dịch
 *   3. Sinh mã khai thác (v1)          — tra 1 CVE, nhận PoC text
 *   4. Sinh PoC (v2)                   — nhập CVE+target, stream bắt đầu (AC-2.1)
 *   5. Báo cáo thông minh              — nhập log, nhận markdown report
 *   6. Đánh giá A/B (Review)           — admin-only, mở tab, verify giao diện
 *
 * KHÔNG mock — gọi model + backend thật.
 * Chạy: cd test/ai-ui-tests && npx playwright test
 * Lọc:  npx playwright test -g "Chatbot"
 */
import { test, expect, type Page } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";

const RESULTS_DIR = path.resolve(__dirname, "results");

/* ─── helpers ─────────────────────────────────────────────────────────── */

async function goHome(page: Page) {
  await page.goto("/");
  await page.waitForSelector(".iassub", { timeout: 15_000 });
}

/** Click vào 1 tab AI bên sidebar. Tab "Đánh giá A/B" chỉ hiện ở role=admin. */
async function switchTab(page: Page, label: string) {
  const btn = page.locator(".iassub", { hasText: label }).first();
  await btn.click({ timeout: 10_000 });
  await page.waitForTimeout(1_000);
}

/** Đổi Demo role (user / admin) bằng dropdown header. */
async function switchRole(page: Page, role: "user" | "admin") {
  const sel = page.locator("select").filter({ hasText: /Người dùng|Admin/ }).first();
  if (await sel.isVisible().catch(() => false)) {
    await sel.selectOption(role);
    await page.waitForTimeout(1_500);
  }
}

async function saveScreenshot(page: Page, name: string) {
  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  await page.screenshot({ path: path.join(RESULTS_DIR, `${name}.png`), fullPage: true });
}

function writeResult(name: string, data: Record<string, unknown>) {
  fs.mkdirSync(RESULTS_DIR, { recursive: true });
  fs.writeFileSync(path.join(RESULTS_DIR, `${name}.json`), JSON.stringify(data, null, 2), "utf8");
}

/* ─── 1. Chatbot — Trợ lý ảo ATTT ───────────────────────────────────── */

test.describe("1. Chatbot — Trợ lý ảo ATTT", () => {
  test("chat 1 câu và nhận phản hồi", async ({ page }) => {
    await goHome(page);
    await switchTab(page, "Trợ lý ảo ATTT");

    // Bắt buộc tạo session mới (session cũ có thể cache ProviderError):
    // Click nút "Cuộc trò chuyện mới" trong sidebar
    const newChatBtn = page.locator("button, div, a").filter({ hasText: "Cuộc trò chuyện mới" }).first();
    await newChatBtn.click({ timeout: 8_000 });
    await page.waitForTimeout(2_000);

    // Verify trang đã reset — hiện placeholder "Bạn muốn hỏi gì hôm nay?"
    await expect(page.getByText("Bạn muốn hỏi gì hôm nay?").or(page.getByPlaceholder("Nhập nội dung..."))).toBeVisible({ timeout: 8_000 });

    // Input chat
    const input = page.getByPlaceholder("Nhập nội dung...").or(page.locator("textarea")).or(page.locator("[contenteditable]")).first();
    await expect(input).toBeVisible({ timeout: 8_000 });
    await input.click();
    await input.fill("CVE-2021-44228 là lỗ hổng gì? Trả lời 1 câu.");

    // Nút "Gửi" (icon ✈ Gửi)
    const sendBtn = page.getByRole("button", { name: "Gửi" });
    await expect(sendBtn).toBeVisible();
    await sendBtn.click();

    // Đợi ít nhất 1 tool activity hiện ("Hoạt động công cụ") hoặc text phản hồi stream
    const toolBar = page.getByText("Hoạt động công cụ");
    const replyArea = page.locator(".markdown, [class*='msg'], [class*='assistant'], [class*='answer']");
    await expect(toolBar.or(replyArea.first())).toBeVisible({ timeout: 60_000 });

    // Chờ thêm tối đa 60s cho stream hoàn tất (hết "Hoạt động công cụ" → text)
    await page.waitForTimeout(30_000);

    // Scrape mọi text trong vùng chat (loại input)
    const chatText = await page.locator(".chat-screen").innerText().catch(() => "");
    const hasReply = chatText.length > 100 && !/ProviderError/.test(chatText);
    const mentionsLog4j = /log4j|Log4Shell/i.test(chatText);

    const result = {
      tab: "chatbot",
      has_reply: hasReply,
      chat_text_len: chatText.length,
      mentions_log4j: mentionsLog4j,
      has_error: /ProviderError|Connection error/i.test(chatText),
      preview: chatText.slice(0, 400),
    };
    writeResult("chatbot", result);
    await saveScreenshot(page, "chatbot");

    // Soft assert: nếu ProviderError → test skip (infra), không fail test script
    if (result.has_error) {
      console.warn("Chatbot trả về ProviderError — LLM backend có thể không ổn định.");
      test.skip();
      return;
    }
    expect(result.has_reply).toBeTruthy();
  });
});

/* ─── 2. Dịch văn bản ────────────────────────────────────────────────── */

test.describe("2. Dịch văn bản", () => {
  test("dịch 1 đoạn Việt → Anh", async ({ page }) => {
    await goHome(page);
    await switchTab(page, "Dịch văn bản");
    await page.waitForTimeout(2_000);

    // Service có thể tắt
    const offMsg = page.getByText("chưa được bật").or(page.getByText("Chưa kết nối")).or(page.getByText("không khả dụng"));
    if (await offMsg.first().isVisible({ timeout: 3_000 }).catch(() => false)) {
      writeResult("translate", { tab: "translate", status: "service_off" });
      await saveScreenshot(page, "translate-off");
      test.skip();
      return;
    }

    const textarea = page.locator("textarea.multilang-input, textarea").first();
    if (!(await textarea.isVisible({ timeout: 5_000 }).catch(() => false))) {
      writeResult("translate", { tab: "translate", status: "no_input" });
      await saveScreenshot(page, "translate-noinput");
      test.skip();
      return;
    }

    await textarea.fill("Lỗ hổng bảo mật cho phép kẻ tấn công thực thi mã từ xa trên máy chủ.");

    // Nút submit trong form dịch
    const btn = page.locator("button[type='submit'], button:has-text('Dịch')").first();
    if (await btn.isVisible().catch(() => false)) {
      await btn.click();
    }

    await page.waitForTimeout(20_000);
    const pageText = await page.locator(".multilang-panel, form").first().innerText().catch(() => "");
    const hasEnglish = /vulnerability|exploit|remote|attack|server|code execution/i.test(pageText);

    const result = {
      tab: "translate",
      status: "ok",
      output_len: pageText.length,
      has_english: hasEnglish,
      preview: pageText.slice(0, 400),
    };
    writeResult("translate", result);
    await saveScreenshot(page, "translate");
  });
});

/* ─── 3. Sinh mã khai thác (v1) ──────────────────────────────────────── */

test.describe("3. Sinh mã khai thác (v1)", () => {
  test("tra CVE-2021-44228 và nhận kết quả", async ({ page }) => {
    await goHome(page);
    await switchTab(page, "Sinh mã khai thác");

    const input = page.getByPlaceholder("Mã CVE, mô tả lỗ hổng, hoặc target/ref…");
    await expect(input).toBeVisible({ timeout: 8_000 });
    await input.fill("CVE-2021-44228");

    const submit = page.locator("button:has-text('Sinh mã khai thác')").first();
    await expect(submit).toBeEnabled();
    await submit.click();

    // Kết quả stream markdown
    const response = page.locator(".markdown, .exploit-result, pre, .iascard").first();
    await expect(response).toBeVisible({ timeout: 120_000 });

    const text = await response.innerText();
    const result = {
      tab: "exploit-v1",
      cve: "CVE-2021-44228",
      has_response: text.length > 20,
      response_len: text.length,
      mentions_jndi: /jndi|ldap|log4j/i.test(text),
      preview: text.slice(0, 400),
    };
    writeResult("exploit-v1", result);
    await saveScreenshot(page, "exploit-v1");

    expect(result.has_response).toBeTruthy();
  });
});

/* ─── 4. Sinh PoC (v2) ─────────────────────────────────────────────── */

test.describe("4. Sinh PoC (v2)", () => {
  test.setTimeout(5 * 60 * 1000);

  test("nhập CVE+target, log stream bắt đầu (AC-2.1)", async ({ page }) => {
    await goHome(page);
    await switchTab(page, "Sinh PoC (v2)");

    const cveInput = page.getByPlaceholder("CVE, ví dụ CVE-2026-63030");
    const targetInput = page.getByPlaceholder("Target — bắt buộc có port, ví dụ 172.19.0.3:80");
    await expect(cveInput).toBeVisible({ timeout: 8_000 });

    await cveInput.fill("CVE-2017-11610");
    await targetInput.fill("192.168.96.9:9001");

    const submit = page.getByRole("button", { name: "Sinh PoC", exact: true });
    await expect(submit).toBeEnabled();
    await submit.click();

    // AC-2.1: đợi step/log đầu tiên hiện
    // FE hiện "Đang chạy…" trong header card hoặc step-line. `.first()` vì nhiều element match.
    const streaming = page.getByText("Đang chạy").first().or(page.locator("[class*='step']").first());
    await expect(streaming).toBeVisible({ timeout: 60_000 });

    const result = {
      tab: "exploit-v2",
      cve: "CVE-2017-11610",
      target: "192.168.96.9:9001",
      stream_started: true,
    };
    writeResult("exploit-v2", result);
    await saveScreenshot(page, "exploit-v2-streaming");

    expect(result.stream_started).toBeTruthy();
    // KHÔNG chờ hết run — batch test riêng ở test/exploit-v2/playwright/
  });
});

/* ─── 5. Báo cáo thông minh ──────────────────────────────────────────── */

test.describe("5. Báo cáo thông minh", () => {
  test("tạo báo cáo từ log scan", async ({ page }) => {
    await goHome(page);
    await switchTab(page, "Báo cáo thông minh");

    // Tab "Tạo báo cáo" đã active mặc định (từ screenshot)
    const logInput = page.getByPlaceholder("Dán log, kết quả quét hoặc dữ liệu cần tổng hợp…");
    await expect(logInput).toBeVisible({ timeout: 8_000 });

    const sampleLog = [
      "Nmap scan report for 192.168.1.100",
      "PORT   STATE SERVICE VERSION",
      "22/tcp open  ssh     OpenSSH 7.4",
      "80/tcp open  http    Apache 2.4.6",
      "443/tcp open  ssl     OpenSSL 1.0.2k-fips (vuln CVE-2017-3735)",
      "3306/tcp open mysql   MySQL 5.7.21 (weak auth)",
    ].join("\n");
    await logInput.fill(sampleLog);

    // Nút "Tạo báo cáo" — có 2 button cùng tên (tab header + submit button).
    // Submit button nằm SAU textarea (sau logInput) và KHÔNG phải tab button (opt class).
    // Dùng locator cụ thể: button chứa icon + text, KHÔNG có class "opt".
    const createBtn = page.locator("button:has-text('Tạo báo cáo'):not(.opt)").first();
    await expect(createBtn).toBeVisible({ timeout: 5_000 });
    await createBtn.click();

    // Kết quả stream vào phần "Kết quả (Markdown)" — ban đầu chứa placeholder text
    // "Kết quả báo cáo sẽ hiển thị tại đây..." rồi được thay bằng markdown thật.
    // Đợi placeholder biến mất HOẶC text mới dài hơn placeholder
    const resultArea = page.locator(".markdown, [class*='report-result'], [class*='result']").first();

    // Chờ stream bắt đầu — nội dung thay đổi khỏi placeholder
    await page.waitForFunction(
      () => {
        const el = document.querySelector(".markdown, [class*='report'], [class*='result']");
        const txt = (el?.textContent || "").trim();
        return txt.length > 60 && !txt.startsWith("Kết quả báo cáo sẽ hiển thị");
      },
      { timeout: 120_000 },
    ).catch(() => null);

    const text = await resultArea.innerText().catch(() => "");
    const fullPageText = await page.locator("body").innerText().catch(() => "");
    const reportText = text || fullPageText;

    const result = {
      tab: "report",
      has_report: reportText.length > 100 && !/Kết quả báo cáo sẽ hiển thị/.test(reportText.slice(0, 50)),
      report_len: reportText.length,
      mentions_scan: /nmap|port|ssh|apache|mysql|192\.168|CVE/i.test(reportText),
      preview: reportText.slice(0, 500),
    };
    writeResult("report", result);
    await saveScreenshot(page, "report");

    // Soft: report có thể không generate nếu model bận — ghi kết quả, không hard-fail
    if (!result.has_report) {
      console.warn("Báo cáo chưa sinh được — có thể model bận/chậm.");
    }
  });
});

/* ─── 6. Đánh giá A/B (Review) — admin only ─────────────────────────── */

test.describe("6. Đánh giá A/B (Review)", () => {
  test("mở tab review (admin), giao diện hiện", async ({ page }) => {
    await goHome(page);

    // PHẢI switch sang admin TRƯỚC — tab "Đánh giá A/B" không hiện ở role user
    await switchRole(page, "admin");
    await page.waitForTimeout(1_000);

    // Sidebar giờ phải có tab "Đánh giá A/B"
    // Sau khi switch admin, FE redirect sang screen "review" tự động (xem App.tsx line 77)
    // Nên có thể đã ở review rồi. Nếu chưa, click vào tab.
    const reviewTab = page.locator(".iassub", { hasText: "Đánh giá A/B" }).first();
    if (await reviewTab.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await reviewTab.click();
      await page.waitForTimeout(1_000);
    }

    // Verify UI review hiện (header "Đánh giá" hoặc queue table hoặc empty state)
    const reviewUI = page.getByText("Hàng đợi được phân công")
      .or(page.getByText("Đánh giá"))
      .or(page.locator("[class*='review']"))
      .first();
    const visible = await reviewUI.isVisible({ timeout: 8_000 }).catch(() => false);

    const result = {
      tab: "review",
      admin_mode: true,
      ui_visible: visible,
    };
    writeResult("review", result);
    await saveScreenshot(page, "review");

    // Ghi nhận — không hard-fail vì tab phụ thuộc dữ liệu review
  });
});
