// Nghiệm thu UI: đọc cases.json, đăng nhập bằng IAS_UI_USER/IAS_UI_PASS (chỉ từ env, không ghi ra đâu cả).
//   PLAYWRIGHT_BASE_URL (mặc định https://ias.id.vn)   ACCEPT_OUT (thư mục kết quả)
//   STACK_HAS_OCR=0 để bỏ UI-TR-3   chọn ca: npx playwright test --grep UI-CHAT-1
import { test, Page } from "@playwright/test";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as path from "path";

const cases: any[] = JSON.parse(fs.readFileSync(path.join(__dirname, "cases.json"), "utf8")).cases;
const BASE = process.env.PLAYWRIGHT_BASE_URL || "https://ias.id.vn";
const ymd = new Date().toISOString().slice(0, 10).replace(/-/g, "");
const OUT = process.env.ACCEPT_OUT || path.resolve(__dirname, "../../local/stack-acceptance", `${ymd}-ui`);
fs.mkdirSync(OUT, { recursive: true });

const clean = (s: string) => s.replace(/\/sess:[0-9a-f]+/g, "/{sid}").replace(/\/[0-9a-f]{32}/g, "/{job}");

// "a|b" = một trong các lựa chọn, "re:<regex>" = regex; không phân biệt hoa thường.
function has(text: string, pat: string): boolean {
  if (pat.startsWith("re:")) return new RegExp(pat.slice(3), "i").test(text);
  return pat.split("|").some((p) => text.toLowerCase().includes(p.toLowerCase()));
}

function judge(c: any, text: string, extra: string[] = []): string[] {
  const why: string[] = [];
  for (const p of [...c.expect_all, ...extra]) if (!has(text, p)) why.push(`thiếu: ${p}`);
  for (const p of c.expect_none || []) if (has(text, p)) why.push(`không được có: ${p}`);
  if (c.min_chars && text.length < c.min_chars) why.push(`ngắn ${text.length} < ${c.min_chars} ký tự`);
  return why;
}

async function login(page: Page) {
  for (let attempt = 0; attempt < 2; attempt++) {
    await page.goto(`${BASE}/login`);
    const user = page.getByPlaceholder("Nhập tên đăng nhập");
    await user.waitFor();
    const close = page.getByRole("button", { name: "Đóng" }); // modal "Phiên làm việc đã kết thúc"
    if (await close.first().isVisible().catch(() => false)) await close.first().click();
    await user.fill(process.env.IAS_UI_USER || "");
    await page.getByPlaceholder("Nhập mật khẩu của bạn").fill(process.env.IAS_UI_PASS || "");
    await page.getByRole("button", { name: "Đăng nhập" }).click();
    if (await page.waitForURL((u) => !u.pathname.includes("login"), { timeout: 15000 }).then(() => true, () => false)) return;
  }
  throw new Error("đăng nhập thất bại (kiểm tra IAS_UI_USER/IAS_UI_PASS)");
}

type Run = { text: string; notes?: Record<string, unknown>; extra?: string[]; fails?: string[] };

async function chat(page: Page, c: any): Promise<Run> {
  const created = page.waitForResponse((r) => r.request().method() === "POST" && /\/api\/ais\/sessions$/.test(new URL(r.url()).pathname));
  await page.locator(".new-chat-btn").click(); // phiên mới: không nhiễm ngữ cảnh ca trước
  await created;
  await page.waitForTimeout(1500); // FE nạp lại khung chat sau khi tạo phiên; gõ sớm hơn sẽ bị xoá
  const [ph, q] = Object.entries<string>(c.fields)[0];
  await page.getByPlaceholder(ph).fill(q);
  const resp = page.waitForResponse((r) => r.url().includes("/chat/stream"), { timeout: c.timeout_s * 1000 });
  await page.getByRole("button", { name: c.click }).click();
  await (await resp).finished();
  await page.waitForTimeout(1500);
  const text = await page.locator(".assistant-content").last().innerText();
  return { text };
}

async function translateText(page: Page, c: any): Promise<Run> {
  const [ph, src] = Object.entries<string>(c.fields)[0];
  await page.getByPlaceholder(ph).fill(src);
  const resp = page.waitForResponse((r) => r.url().includes("/translate"), { timeout: c.timeout_s * 1000 });
  await page.getByRole("button", { name: c.click, exact: true }).click();
  await (await resp).finished();
  const out = page.getByPlaceholder(c.output_field);
  await page.waitForFunction((el) => !!(el as HTMLTextAreaElement).value.trim(), await out.elementHandle(), { timeout: 15000 }).catch(() => {});
  return { text: await out.inputValue() };
}

async function translateFile(page: Page, c: any, id: string): Promise<Run> {
  await page.getByText(c.tab).first().click();
  const sel = page.locator("select");
  await sel.nth(0).selectOption(c.source_value); // nguồn en => đích tự khoá vi
  const tgt = await sel.nth(1).inputValue();
  await page.locator("input[type=file]").setInputFiles(path.join(__dirname, c.input));
  const deadline = Date.now() + c.timeout_s * 1000;
  let status = "";
  while (Date.now() < deadline) {
    const body = await page.locator("body").innerText();
    if (body.includes("Dịch hoàn tất")) { status = "completed"; break; }
    if (body.includes("Dịch thất bại")) { status = "failed"; break; }
    await page.waitForTimeout(3000);
  }
  const notes: Record<string, unknown> = { status: status || "timeout", target_select: tgt };
  if (status !== "completed") { // lỗi hiện ở khung kết quả bên phải thẻ tệp; không dùng toàn trang (có menu tiếng Việt)
    const err = await page.locator(".translated-loading, .translated-error, .translated-preview, .translated-result").first().innerText().catch(() => "");
    const code = /"code":\s*"([A-Z_]+)"/.exec(await page.locator("body").innerText())?.[1] || "";
    return { text: `${code} ${err}`.slice(0, 600), notes, fails: [`job ${status || "quá hạn"}`] };
  }
  await page.locator(".preview-file-button").first().click();
  await page.waitForTimeout(1500);
  const preview = await page.locator("body").innerText();
  const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 20000 }).catch(() => null), page.getByRole("button", { name: "Tải TXT" }).click().catch(() => {})]);
  let txt = "";
  if (dl) { const f = path.join(OUT, `${id}.download.txt`); await dl.saveAs(f); txt = fs.readFileSync(f, "utf8"); notes.download = dl.suggestedFilename(); }
  else notes.download = "không có tệp tải về";
  return { text: `${txt}\n${preview}`, notes, fails: dl ? [] : ["không tải được tệp TXT"] };
}

async function report(page: Page, c: any, id: string): Promise<Run> {
  for (const k of ["Đối tượng quét", "Định dạng file tải"]) await page.getByText(c.fields[k], { exact: true }).click();
  const tpl = page.locator("textarea").first();
  await tpl.fill(c.fields["Mẫu template báo cáo"]);
  const dates = await page.locator("input[placeholder='dd/mm/yyyy']").evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value));
  const resp = page.waitForResponse((r) => r.url().includes("/report-ai"), { timeout: c.timeout_s * 1000 });
  await page.getByRole("button", { name: c.click }).click();
  await (await resp).finished();
  await page.waitForTimeout(1500);
  const text = await page.evaluate(() => {
    const l = [...document.querySelectorAll("div,label,span")].find((e) => (e as HTMLElement).innerText?.trim() === "Kết quả phân tích");
    return (l?.parentElement as HTMLElement | undefined)?.innerText || "";
  });
  const notes: Record<string, unknown> = { dates };
  const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 60000 }).catch(() => null), page.getByRole("button", { name: c.download }).click()]);
  const extra = [`từ ${dates[0]} đến ${dates[1]}`], fails: string[] = [];
  if (dl) {
    const f = path.join(OUT, `${id}.docx`);
    await dl.saveAs(f);
    notes.download = dl.suggestedFilename();
    try { // .docx hợp lệ = zip có word/document.xml
      execFileSync("python3", ["-c", "import sys,zipfile;zipfile.ZipFile(sys.argv[1]).getinfo('word/document.xml')", f]);
      notes.docx_valid = true;
    } catch { notes.docx_valid = false; fails.push("docx không mở được"); }
  } else { notes.download = "không có tệp tải về"; fails.push("không tải được tệp .docx"); }
  return { text, notes, extra, fails };
}

async function exploit(page: Page, c: any, id: string): Promise<Run> {
  for (const [ph, v] of Object.entries<string>(c.fields)) await page.locator(`input[placeholder^="${ph}"]`).fill(v);
  let sse = "";
  page.on("response", (r) => { if (new URL(r.url()).pathname.endsWith("/api/ais/stream")) r.text().then((t) => (sse = t)).catch(() => {}); });
  const btn = page.locator("button.poc-button");
  const t0 = Date.now();
  await btn.click();
  let sawDisabled = false, last = "", lastChange = Date.now();
  while (Date.now() - t0 < c.timeout_s * 1000) {
    await page.waitForTimeout(2000);
    const body = await page.locator("body").innerText();
    const enabled = await btn.isEnabled();
    if (!enabled) sawDisabled = true;
    if (body !== last) { last = body; lastChange = Date.now(); }
    if (body.includes("Kết quả phiên chạy") && enabled) break;
    if (enabled && Date.now() - lastChange > 8000 && Date.now() - t0 > 8000) break; // dừng sớm (vd. check_target thất bại)
  }
  const text = await page.locator("body").innerText();
  fs.writeFileSync(path.join(OUT, `${id}.page.txt`), text);
  fs.writeFileSync(path.join(OUT, `${id}.sse.txt`), sse.slice(0, 300_000));
  const status = text.includes("Khai thác THÀNH CÔNG") ? "SUCCESS" : /Kết quả phiên chạy\s+(\S+)/.exec(text)?.[1] || null;
  return { text, notes: { status, saw_disabled: sawDisabled, seconds: Math.round((Date.now() - t0) / 1000), sse_bytes: sse.length } };
}

for (const c of cases) {
  test(c.id, async ({ page }) => {
    test.setTimeout(c.timeout_s * 1000 + 120_000);
    const skip = c.requires === "ocr" && process.env.STACK_HAS_OCR === "0";
    const rec: any = { id: c.id, base: BASE, started: new Date().toISOString() };
    const endpoints = new Set<string>();
    page.on("request", (r) => {
      const u = new URL(r.url());
      if (["fetch", "xhr"].includes(r.resourceType()) && u.pathname.startsWith("/api/ais/")) endpoints.add(`${r.method()} ${clean(u.pathname)}`);
    });
    const t0 = Date.now();
    try {
      if (skip) { rec.pass = null; rec.reason = c.skip_message; return; }
      await login(page);
      await page.goto(`${BASE}${c.page}`);
      await page.waitForTimeout(2500);
      const run = c.kind === "chat" ? await chat(page, c)
        : c.kind === "translate_text" ? await translateText(page, c)
        : c.kind === "translate_file" ? await translateFile(page, c, c.id)
        : c.kind === "report" ? await report(page, c, c.id)
        : await exploit(page, c, c.id);
      const why = judge(c, run.text, run.extra);
      why.push(...(run.fails || []));
      Object.assign(rec, { pass: why.length === 0, reason: why.join("; ") || "đạt", output_head: run.text.slice(0, 600), notes: run.notes });
    } catch (e: any) {
      Object.assign(rec, { pass: false, reason: `lỗi chạy: ${String(e.message).split("\n")[0].slice(0, 300)}` });
    } finally {
      rec.duration_s = Math.round((Date.now() - t0) / 1000);
      rec.endpoints = [...endpoints].sort();
      await page.screenshot({ path: path.join(OUT, `${c.id}.png`), fullPage: true }).catch(() => {});
      fs.writeFileSync(path.join(OUT, `${c.id}.json`), JSON.stringify(rec, null, 1));
    }
    if (rec.pass === false) throw new Error(`${c.id}: ${rec.reason}`);
  });
}
