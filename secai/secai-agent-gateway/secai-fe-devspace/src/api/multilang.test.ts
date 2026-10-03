import { afterEach, describe, expect, it, vi } from "vitest";
import { getMultilangCapabilities, MultilangApiError, translateMultilang } from "./multilang";

afterEach(() => { vi.unstubAllGlobals(); });

describe("multilang JSON client", () => {
  it("fetches capabilities from the exact v3 route", async () => {
    const capabilities = { languages: [], pairs: [], limits: { max_text_chars: 24000, max_source_tokens: 8192, max_chunks: 16, request_timeout_seconds: 120 } };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(capabilities), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);

    await expect(getMultilangCapabilities()).resolves.toEqual(capabilities);
    expect(fetch).toHaveBeenCalledWith("/ai/v3/translate/languages", expect.objectContaining({ method: "GET" }));
  });

  it("sends only the frozen text and language fields, with abort support", async () => {
    const answer = { translated_text: "检查 CVE-2024-1234。", source_lang: "vi", target_lang: "zh-Hans", warnings: [], metadata: { request_id: "req-1", chunks: 1, attempts: 1 } };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(answer), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    const controller = new AbortController();

    await expect(translateMultilang({ text: "Kiểm tra CVE-2024-1234.", source_lang: "vi", target_lang: "zh-Hans" }, controller.signal)).resolves.toEqual(answer);
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/ai/v3/translate");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(init.signal).toBe(controller.signal);
    expect(JSON.parse(String(init.body))).toEqual({ text: "Kiểm tra CVE-2024-1234.", source_lang: "vi", target_lang: "zh-Hans" });
  });

  it("exposes the machine error code and Retry-After without echoing raw response content", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "RATE_LIMITED", message: "Quá nhiều yêu cầu", request_id: "req-2" } }), { status: 429, headers: { "Content-Type": "application/json", "Retry-After": "7" } }));
    vi.stubGlobal("fetch", fetch);

    try {
      await translateMultilang({ text: "xin chào", source_lang: "vi", target_lang: "ar" });
      throw new Error("expected API error");
    } catch (error) {
      expect(error).toBeInstanceOf(MultilangApiError);
      expect(error).toMatchObject({ status: 429, code: "RATE_LIMITED", requestId: "req-2", retryAfter: 7 });
    }
  });

  it("keeps 404 capability responses distinct from network failures", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Not Found", { status: 404 })));
    await expect(getMultilangCapabilities()).rejects.toMatchObject({ status: 404 });
  });
});
