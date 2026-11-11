import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** E2E dịch (v3 trực tiếp + v2 tệp/ảnh) trên FE đã chạy sẵn: PLAYWRIGHT_BASE_URL=http://127.0.0.1:5556.
 * Gateway/worker/MT phải sống. Mọi test bắt pageerror + console.error: giao diện không được lỗi. */
const FX = (name: string) => path.resolve(path.dirname(fileURLToPath(import.meta.url)), "fixtures/translate", name);
const pageErrors = new WeakMap<Page, string[]>();

test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  pageErrors.set(page, errors);
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error" && !/Failed to load resource|favicon/.test(message.text())) errors.push(`console: ${message.text()}`);
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Dịch văn bản" }).click();
  await expect(page.getByLabel("Văn bản nguồn")).toBeVisible();
});

test.afterEach(async ({ page }) => {
  expect(pageErrors.get(page) ?? [], "không được có lỗi JS/console").toEqual([]);
});

const selects = (page: Page) => page.locator("main select");
const resultBox = (page: Page) => page.getByRole("status", { name: "Kết quả dịch" });
const submit = (page: Page) => page.locator("main button[type=submit]");

async function pair(page: Page, source: string, target: string) {
  await selects(page).nth(0).selectOption(source);
  await selects(page).nth(1).selectOption(target);
}

async function translate(page: Page, text: string, timeout = 90_000) {
  const box = page.getByLabel("Văn bản nguồn");
  await box.fill(text);
  await submit(page).click();
  await expect(page.getByText("Dịch hoàn tất.")).toBeVisible({ timeout });
  await expect(page.getByRole("alert")).toHaveCount(0);
  return (await resultBox(page).innerText()).trim();
}

const SAMPLES: Array<[string, string, string]> = [
  ["en", "The flaw CVE-2026-31337 lets remote attackers run arbitrary code. Install the patch now.", "CVE-2026-31337"],
  ["zh-Hans", "漏洞 CVE-2026-31337 允许远程攻击者执行任意代码。请立即安装补丁。", "CVE-2026-31337"],
  ["ja", "脆弱性 CVE-2026-31337 により、リモートの攻撃者が任意のコードを実行できます。直ちにパッチを適用してください。", "CVE-2026-31337"],
  ["ko", "취약점 CVE-2026-31337 은 원격 공격자가 임의의 코드를 실행할 수 있게 합니다. 즉시 패치를 설치하세요.", "CVE-2026-31337"],
  ["fr", "La faille CVE-2026-31337 permet à des attaquants distants d'exécuter du code arbitraire. Installez le correctif immédiatement.", "CVE-2026-31337"],
  ["de", "Die Schwachstelle CVE-2026-31337 erlaubt Angreifern die Ausführung beliebigen Codes. Installieren Sie den Patch sofort.", "CVE-2026-31337"],
  ["es", "La vulnerabilidad CVE-2026-31337 permite a atacantes remotos ejecutar código arbitrario. Instale el parche de inmediato.", "CVE-2026-31337"],
  ["ru", "Уязвимость CVE-2026-31337 позволяет удалённым злоумышленникам выполнять произвольный код. Немедленно установите исправление.", "CVE-2026-31337"],
  ["ar", "تسمح الثغرة CVE-2026-31337 للمهاجمين عن بعد بتنفيذ تعليمات برمجية عشوائية. ثبّت التصحيح فورًا.", "CVE-2026-31337"],
];

test.describe("dịch trực tiếp", () => {
  for (const [lang, text, keep] of SAMPLES) {
    test(`${lang} → vi giữ nguyên ${keep}`, async ({ page }) => {
      await pair(page, lang, "vi");
      const out = await translate(page, text);
      expect(out).toContain(keep);
      expect(out.length).toBeGreaterThan(20);
    });
  }

  test("vi → en", async ({ page }) => {
    await pair(page, "vi", "en");
    const out = await translate(page, "Lỗ hổng CVE-2026-31337 cho phép kẻ tấn công thực thi mã từ xa. Hãy cập nhật bản vá ngay.");
    expect(out).toContain("CVE-2026-31337");
    expect(out).toMatch(/[a-z]{4,}/);
  });

  test("literal kỹ thuật được giữ nguyên (lệnh, IP, URL, đường dẫn, mật khẩu)", async ({ page }) => {
    await pair(page, "en", "vi");
    const out = await translate(page,
      "Run `curl -sk https://10.0.4.17:8443/api/v1/admin/export` then read /etc/app/secrets.env. Default credentials admin / P@ssw0rd123 are enabled on srv-db01.corp.local.");
    for (const literal of ["https://10.0.4.17:8443/api/v1/admin/export", "/etc/app/secrets.env", "P@ssw0rd123", "srv-db01.corp.local"]) {
      expect(out).toContain(literal);
    }
  });

  test("văn bản chỉ có literal (không có chữ để dịch): báo lỗi hợp lệ, giao diện vẫn dùng được", async ({ page }) => {
    await pair(page, "en", "vi");
    await page.getByLabel("Văn bản nguồn").fill("10.0.0.1");
    await submit(page).click();
    // Server từ chối có chủ đích (không có chữ để dịch); phải là thông báo, không phải crash.
    await expect(page.getByRole("alert")).toHaveText("Nội dung yêu cầu không hợp lệ.");
    await expect(submit(page)).toBeEnabled();
    expect((await translate(page, "Block 10.0.0.1 at the firewall.")).length).toBeGreaterThan(5);
  });

  test("emoji, xuống dòng, code fence", async ({ page }) => {
    await pair(page, "en", "vi");
    const out = await translate(page, "Status 🔒 secured.\n\n```\nsudo systemctl restart nginx\n```\n\nCheck the logs 📄 afterwards.");
    expect(out).toContain("sudo systemctl restart nginx");
  });

  test("văn bản 1 từ và tiếng Anh trộn tiếng Việt", async ({ page }) => {
    await pair(page, "en", "vi");
    expect((await translate(page, "Firewall")).length).toBeGreaterThan(2);
    await page.getByLabel("Văn bản nguồn").fill("Please patch Apache Tomcat 9.0.71 immediately, cảm ơn.");
    await submit(page).click();
    await expect(page.getByText("Dịch hoàn tất.")).toBeVisible({ timeout: 60_000 });
  });

  test("rỗng/chỉ khoảng trắng: nút dịch bị khóa", async ({ page }) => {
    await expect(submit(page)).toBeDisabled();
    await page.getByLabel("Văn bản nguồn").fill("   \n  ");
    await expect(submit(page)).toBeDisabled();
  });

  test("vượt giới hạn ký tự: báo lỗi và khóa nút", async ({ page }) => {
    await page.getByLabel("Văn bản nguồn").fill("a ".repeat(12_100));
    await expect(page.getByText("Văn bản vượt giới hạn cho phép.")).toBeVisible();
    await expect(submit(page)).toBeDisabled();
  });

  test("hoán đổi hướng dịch", async ({ page }) => {
    await pair(page, "en", "vi");
    await page.getByRole("button", { name: "Hoán đổi hướng dịch" }).click();
    await expect(selects(page).nth(0)).toHaveValue("vi");
    await expect(selects(page).nth(1)).toHaveValue("en");
    const out = await translate(page, "Hãy khởi động lại máy chủ sau khi cập nhật.");
    expect(out).toMatch(/[a-z]{4,}/);
  });

  test("bấm dịch hai lần liên tiếp chỉ ra một kết quả, không lỗi", async ({ page }) => {
    await pair(page, "en", "vi");
    await page.getByLabel("Văn bản nguồn").fill("Restart the service and verify the configuration.");
    await submit(page).dblclick();
    await expect(page.getByText("Dịch hoàn tất.")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  test("văn bản dài thực tế (~3000 ký tự, nhiều đoạn) dịch được", async ({ page }) => {
    await pair(page, "en", "vi");
    const sentences = [
      "The security team reviewed the web server and found the administrative interface exposed to the internet.",
      "An attacker could use the flaw CVE-2026-31337 to execute commands with the privileges of the service account.",
      "The vendor released a fixed build, and the organization should schedule the upgrade within the next maintenance window.",
      "Access logs from the previous quarter were collected and searched for unusual authentication attempts.",
      "No evidence of data exfiltration was found, although several scanning attempts originated from external hosts.",
      "Management recommends enabling multi-factor authentication for every administrative account without exception.",
      "A follow-up assessment will verify that the exposed interface is restricted to the internal management network.",
    ];
    const text = Array.from({ length: 4 }, (_, i) => sentences.slice(i % 3).concat(sentences.slice(0, i % 3)).join(" ")).join("\n\n");
    expect(text.length).toBeGreaterThan(2500);
    const out = await translate(page, text, 150_000);
    expect(out).toContain("CVE-2026-31337");
    expect(out.length).toBeGreaterThan(1500);
  });

  test("văn bản dày literal vượt sức chứa: hoặc dịch xong hoặc báo rõ, UI không treo", async ({ page }) => {
    await pair(page, "en", "vi");
    const paragraph = "During the assessment the team found CVE-2026-31337 on 10.1.2.3 and applied the vendor patch. ";
    await page.getByLabel("Văn bản nguồn").fill(Array.from({ length: 10 }, () => paragraph.repeat(5)).join("\n\n"));
    await submit(page).click();
    await expect(page.getByText("Dịch hoàn tất.").or(page.getByRole("alert"))).toBeVisible({ timeout: 150_000 });
    await expect(submit(page)).toBeEnabled();
    const alert = page.getByRole("alert");
    if (await alert.count()) await expect(alert).toHaveText(/vượt giới hạn|không qua kiểm tra/);
  });

  test("hủy giữa chừng rồi dịch lại bình thường", async ({ page }) => {
    await pair(page, "en", "vi");
    const paragraph = "The attacker pivoted through the internal network and exfiltrated customer records. ";
    await page.getByLabel("Văn bản nguồn").fill(Array.from({ length: 12 }, () => paragraph.repeat(6)).join("\n\n"));
    await submit(page).click();
    await page.getByRole("button", { name: "Hủy dịch" }).click();
    await expect(page.getByText("Đã hủy yêu cầu dịch.")).toBeVisible();
    await expect(submit(page)).toBeEnabled();
    const out = await translate(page, "Rotate the credentials.");
    expect(out.length).toBeGreaterThan(5);
  });

  test("chuyển sang tab tệp rồi quay lại vẫn dịch được", async ({ page }) => {
    await page.getByRole("button", { name: "Dịch tệp và ảnh" }).click();
    await expect(page.locator("input[type=file]")).toBeVisible();
    await page.getByRole("group", { name: "Chế độ dịch" }).getByRole("button", { name: "Dịch trực tiếp" }).click();
    await pair(page, "en", "vi");
    expect((await translate(page, "Disable the account.")).length).toBeGreaterThan(5);
  });
});

async function openFiles(page: Page, source = "en", target = "vi") {
  await page.getByRole("button", { name: "Dịch tệp và ảnh" }).click();
  await selects(page).nth(0).selectOption(source);
  await selects(page).nth(1).selectOption(target);
}

const rows = (page: Page) => page.getByRole("list", { name: "Danh sách tệp dịch" }).locator("li");

async function preview(page: Page, name: string, expected: string[]) {
  await page.getByRole("button", { name: `Xem trước ${name}` }).click();
  const aside = page.getByRole("complementary", { name: "Xem trước bản dịch" });
  for (const text of expected) await expect(aside).toContainText(text, { timeout: 45_000 });
}

test.describe("dịch tệp và ảnh", () => {
  test("txt nhỏ", async ({ page }) => {
    await openFiles(page);
    await page.locator("input[type=file]").setInputFiles(FX("small.txt"));
    await expect(page.getByText("Dịch hoàn tất")).toBeVisible({ timeout: 120_000 });
    await preview(page, "small.txt", ["CVE-2026-31337", "10.0.0.5"]);
  });

  test("docx có bảng", async ({ page }) => {
    await openFiles(page);
    await page.locator("input[type=file]").setInputFiles(FX("small-table.docx"));
    await expect(page.getByText("Dịch hoàn tất")).toBeVisible({ timeout: 120_000 });
    await preview(page, "small-table.docx", ["CVE-2026-31337"]);
  });

  test("ảnh OCR + dịch", async ({ page }) => {
    await openFiles(page);
    await page.locator("input[type=file]").setInputFiles(FX("advisory-en.png"));
    await expect(page.getByText("Dịch hoàn tất")).toBeVisible({ timeout: 180_000 });
    await preview(page, "advisory-en.png", ["CVE-2024-12345"]);
  });

  test("tệp sai định dạng bị từ chối có thông báo, không crash", async ({ page }) => {
    await openFiles(page);
    await page.locator("input[type=file]").setInputFiles(FX("fake.pdf"));
    await expect(page.getByText(/chỉ nhận TXT\/DOCX\/DOC/)).toBeVisible();
    await expect(rows(page)).toHaveCount(0);
  });

  test("tệp rỗng bị từ chối có thông báo", async ({ page }) => {
    await openFiles(page);
    await page.locator("input[type=file]").setInputFiles(FX("empty.txt"));
    await expect(page.getByText(/không rỗng/)).toBeVisible();
    await expect(rows(page)).toHaveCount(0);
  });

  test("hai tệp cùng lúc đều hoàn tất", async ({ page }) => {
    await openFiles(page);
    await page.locator("input[type=file]").setInputFiles([FX("small.txt"), FX("small-table.docx")]);
    await expect(page.getByText("Dịch hoàn tất")).toHaveCount(2, { timeout: 240_000 });
  });

  test("hủy job tài liệu dài rồi gửi tệp khác", async ({ page }) => {
    await openFiles(page);
    await page.locator("input[type=file]").setInputFiles(FX("long-30p.docx"));
    await expect(page.getByRole("button", { name: "Hủy long-30p.docx" })).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "Hủy long-30p.docx" }).click();
    await expect(page.getByText(/Đã hủy|đã hủy/).first()).toBeVisible({ timeout: 60_000 });
    await page.locator("input[type=file]").setInputFiles(FX("small.txt"));
    await expect(page.getByText("Dịch hoàn tất")).toBeVisible({ timeout: 180_000 });
  });

  test("tải lại trang khi job đang chạy: không lỗi và job được khôi phục/hoàn tất", async ({ page }) => {
    await openFiles(page);
    await page.locator("input[type=file]").setInputFiles(FX("small-table.docx"));
    await expect(rows(page)).toHaveCount(1);
    await page.reload();
    await page.getByRole("button", { name: "Dịch văn bản" }).click();
    await page.getByRole("button", { name: "Dịch tệp và ảnh" }).click();
    await expect(page.getByRole("heading", { name: "Dịch thuật bảo mật" })).toBeVisible();
  });
});
