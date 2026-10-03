// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { MultilangCapabilities, MultilangLanguageCode } from "../api/multilang";
import MultilangTranslatePanel from "./MultilangTranslatePanel";
import TranslateScreen from "./TranslateScreen";

const codes: MultilangLanguageCode[] = ["vi", "zh-Hans", "ja", "ko", "fr", "de", "es", "ru", "ar"];
const names = ["Tiếng Việt", "Trung giản thể", "Nhật", "Hàn", "Pháp", "Đức", "Tây Ban Nha", "Nga", "Ả Rập"];
const natives = ["Tiếng Việt", "简体中文", "日本語", "한국어", "Français", "Deutsch", "Español", "Русский", "العربية"];
const scripts = ["Latn", "Hans", "Jpan", "Kore", "Latn", "Latn", "Latn", "Cyrl", "Arab"] as const;

function capabilities(enabled: MultilangLanguageCode[] = ["zh-Hans", "ar"]): MultilangCapabilities {
  return {
    languages: codes.map((code, index) => ({ code, name: names[index], native_name: natives[index], aliases: code === "zh-Hans" ? ["zh"] : [], script: scripts[index], direction: code === "ar" ? "rtl" : "ltr" })),
    pairs: codes.slice(1).flatMap((code) => {
      const active = enabled.includes(code);
      const status = active ? (code === "ar" ? "beta" : "stable") : "disabled";
      return [
        { source_lang: "vi" as const, target_lang: code, enabled: active, status },
        { source_lang: code, target_lang: "vi" as const, enabled: active, status },
      ];
    }),
    limits: { max_text_chars: 24000, max_source_tokens: 8192, max_chunks: 16, request_timeout_seconds: 120 },
  };
}

function answer(translated_text: string, source_lang: MultilangLanguageCode = "vi", target_lang: MultilangLanguageCode = "ar") {
  return { translated_text, source_lang, target_lang, warnings: [], metadata: { request_id: "req-1", chunks: 1, attempts: 1 } };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("multilang translation panel", () => {
  it("lists the eight new languages and blocks a disabled direction before POST", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    render(<MultilangTranslatePanel capabilities={capabilities()} />);

    const target = screen.getByRole("combobox", { name: "Ngôn ngữ đích" });
    expect(screen.getByRole("combobox", { name: "Ngôn ngữ nguồn" }).querySelectorAll("option")).toHaveLength(9);
    expect(target.querySelectorAll("option")).toHaveLength(8);
    fireEvent.change(target, { target: { value: "ar" } });
    expect(screen.getByText("Beta")).toBeTruthy();
    fireEvent.change(target, { target: { value: "zh-Hans" } });
    expect(screen.getByText("Sẵn sàng")).toBeTruthy();
    fireEvent.change(target, { target: { value: "fr" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Văn bản nguồn" }), { target: { value: "Bonjour" } });
    expect(screen.getByText("Chưa bật")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Dịch đa ngôn ngữ" }).hasAttribute("disabled")).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("renders Arabic and Latin technical spans as plain text and copies exact Unicode", async () => {
    const output = "افحص CVE-2024-1234 و 192.0.2.1 https://example.org/a?x=1&y=2 و ب‍ي ال‌عربية <script>alert(1)</script>";
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(answer(output)), { status: 200, headers: { "Content-Type": "application/json" } }));
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("fetch", fetch);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const { container } = render(<MultilangTranslatePanel capabilities={capabilities()} />);

    fireEvent.change(screen.getByRole("combobox", { name: "Ngôn ngữ đích" }), { target: { value: "ar" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Văn bản nguồn" }), { target: { value: "Kiểm tra CVE-2024-1234 và 192.0.2.1" } });
    fireEvent.click(screen.getByRole("button", { name: "Dịch đa ngôn ngữ" }));
    const result = screen.getByRole("status", { name: "Kết quả dịch" });
    await waitFor(() => expect(result.textContent).toBe(output));
    expect(result.getAttribute("dir")).toBe("rtl");
    const technicalSpans = Array.from(result.querySelectorAll('bdi[dir="ltr"]')).map((node) => node.textContent);
    expect(technicalSpans).toContain("CVE-2024-1234");
    expect(technicalSpans).toContain("192.0.2.1");
    expect(technicalSpans).toContain("https://example.org/a?x=1&y=2");
    expect(container.querySelector("script")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Sao chép bản dịch" }));
    expect(writeText).toHaveBeenCalledWith(output);
    const [, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ text: "Kiểm tra CVE-2024-1234 và 192.0.2.1", source_lang: "vi", target_lang: "ar" });
  });

  it("discards a late response after the source changes", async () => {
    let resolveRequest!: (value: Response) => void;
    const fetch = vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { resolveRequest = resolve; }));
    vi.stubGlobal("fetch", fetch);
    render(<MultilangTranslatePanel capabilities={capabilities()} />);

    const source = screen.getByRole("textbox", { name: "Văn bản nguồn" });
    fireEvent.change(source, { target: { value: "Bản cũ" } });
    fireEvent.click(screen.getByRole("button", { name: "Dịch đa ngôn ngữ" }));
    const signal = (fetch.mock.calls[0][1] as RequestInit).signal as AbortSignal;
    fireEvent.change(source, { target: { value: "Bản mới" } });
    expect(signal.aborted).toBe(true);
    await act(async () => resolveRequest(new Response(JSON.stringify(answer("OLD RESULT")), { status: 200, headers: { "Content-Type": "application/json" } })));
    expect(screen.queryByText("OLD RESULT")).toBeNull();
    expect(screen.getByRole("button", { name: "Dịch đa ngôn ngữ" }).hasAttribute("disabled")).toBe(false);
  });

  it("keeps code points intact, shows quality warnings, then swaps into the reverse pair", async () => {
    const first = { ...answer("日本語 CVE-2024-1234", "vi", "ja"), warnings: ["SOURCE_LANGUAGE_UNCERTAIN", "TARGET_LANGUAGE_UNCERTAIN"] };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(first), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    render(<MultilangTranslatePanel capabilities={capabilities(["ja"])} />);

    fireEvent.change(screen.getByRole("textbox", { name: "Văn bản nguồn" }), { target: { value: "😀 CVE-2024-1234" } });
    fireEvent.click(screen.getByRole("button", { name: "Dịch đa ngôn ngữ" }));
    expect(await screen.findByText(/Hệ thống chưa xác định chắc ngôn ngữ nguồn/)).toBeTruthy();
    expect(screen.getByText(/Hệ thống chưa xác định chắc ngôn ngữ đích/)).toBeTruthy();
    expect(screen.getByText("Mã yêu cầu: req-1 · 1 đoạn · 1 lượt tạo")).toBeTruthy();
    expect(JSON.parse(String((fetch.mock.calls[0][1] as RequestInit).body)).text).toBe("😀 CVE-2024-1234");

    fireEvent.click(screen.getByRole("button", { name: "Hoán đổi hướng dịch" }));
    expect((screen.getByRole("combobox", { name: "Ngôn ngữ nguồn" }) as HTMLSelectElement).value).toBe("ja");
    expect((screen.getByRole("combobox", { name: "Ngôn ngữ đích" }) as HTMLSelectElement).value).toBe("vi");
    expect((screen.getByRole("textbox", { name: "Văn bản nguồn" }) as HTMLTextAreaElement).value).toBe("日本語 CVE-2024-1234");
    expect(screen.queryByText(/Hệ thống chưa xác định chắc ngôn ngữ nguồn/)).toBeNull();
  });

  it("counts Unicode code points against the server limit", () => {
    const limited = capabilities();
    limited.limits.max_text_chars = 4;
    render(<MultilangTranslatePanel capabilities={limited} />);
    const source = screen.getByRole("textbox", { name: "Văn bản nguồn" });
    fireEvent.change(source, { target: { value: "ab😀c" } });
    expect(screen.getByText("4 / 4 ký tự")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Dịch đa ngôn ngữ" }).hasAttribute("disabled")).toBe(false);
    fireEvent.change(source, { target: { value: "ab😀cd" } });
    expect(screen.getByRole("button", { name: "Dịch đa ngôn ngữ" }).hasAttribute("disabled")).toBe(true);
  });

  it("shows an explicit cancel state and does not retry a failed request", async () => {
    const fetch = vi.fn().mockImplementation((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))));
    vi.stubGlobal("fetch", fetch);
    render(<MultilangTranslatePanel capabilities={capabilities()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Văn bản nguồn" }), { target: { value: "Xin chào" } });
    fireEvent.click(screen.getByRole("button", { name: "Dịch đa ngôn ngữ" }));
    fireEvent.click(screen.getByRole("button", { name: "Hủy dịch" }));
    expect(await screen.findByText("Đã hủy yêu cầu dịch.")).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([
    [400, "INVALID_REQUEST", "Nội dung yêu cầu không hợp lệ."],
    [403, "LANGUAGE_DISABLED", "Ngôn ngữ này chưa được bật."],
    [413, "TEXT_TOO_LONG", "Văn bản vượt giới hạn cho phép."],
    [422, "SOURCE_LANGUAGE_MISMATCH", "Ngôn ngữ nguồn có thể không đúng với văn bản."],
    [429, "RATE_LIMITED", "Hệ thống đang giới hạn yêu cầu. Thử lại sau 7 giây."],
    [502, "OUTPUT_VALIDATION_FAILED", "Bản dịch chưa đạt kiểm tra an toàn. Vui lòng thử lại sau."],
    [503, "MODEL_UNAVAILABLE", "Dịch vụ dịch tạm thời không khả dụng."],
    [504, "TRANSLATION_TIMEOUT", "Yêu cầu dịch đã hết thời gian."],
  ])("shows safe guidance for HTTP %i / %s", async (status, code, message) => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code, message: "raw backend detail", request_id: "req-2" } }), { status, headers: { "Content-Type": "application/json", "Retry-After": "7" } }));
    vi.stubGlobal("fetch", fetch);
    render(<MultilangTranslatePanel capabilities={capabilities()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Văn bản nguồn" }), { target: { value: "Xin chào" } });
    fireEvent.click(screen.getByRole("button", { name: "Dịch đa ngôn ngữ" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", message);
    expect(screen.queryByText("raw backend detail")).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("shows a connection error without retrying inference", async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError("network failed"));
    vi.stubGlobal("fetch", fetch);
    render(<MultilangTranslatePanel capabilities={capabilities()} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Văn bản nguồn" }), { target: { value: "Xin chào" } });
    fireEvent.click(screen.getByRole("button", { name: "Dịch đa ngôn ngữ" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Không kết nối được dịch vụ dịch. Kiểm tra kết nối rồi thử lại.");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe("legacy screen capability gate", () => {
  it("keeps legacy translation visible and hides the new mode when capabilities return 404", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("Not Found", { status: 404 }));
    vi.stubGlobal("fetch", fetch);
    render(<TranslateScreen userId="devspace" />);
    expect(await screen.findByText("Dịch đa ngôn ngữ chưa được bật trên máy chủ.")).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Dịch bởi qwen2.5 · security-tuned")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Dịch đa ngôn ngữ" })).toBeNull();
  });

  it("opens the v3 mode only after server capabilities load", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(capabilities(["ar"])), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    render(<TranslateScreen userId="devspace" />);
    expect(screen.getByText("Dịch bởi qwen2.5 · security-tuned")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Mở dịch đa ngôn ngữ" }));
    expect(screen.getByRole("button", { name: "Mở dịch đa ngôn ngữ" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("combobox", { name: "Ngôn ngữ nguồn" })).toBeTruthy();
    expect(screen.queryByText("Dịch bởi qwen2.5 · security-tuned")).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
