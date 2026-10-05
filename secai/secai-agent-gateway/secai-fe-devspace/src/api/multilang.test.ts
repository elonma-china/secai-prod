import { afterEach, describe, expect, it, vi } from "vitest";
import { getMultilangCapabilities, MultilangApiError, translateMultilang, streamMultilang } from "./multilang";

afterEach(() => { vi.unstubAllGlobals(); });

describe("multilang JSON client", () => {
  it("streams English chunks and requires a complete final", async () => {
    const answer = { translated_text: "Check logs.", source_lang: "vi", target_lang: "en", warnings: [], metadata: { request_id: "req-1", chunks: 1, attempts: 1 } };
    const chunk = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(`event: chunk\ndata: {"delta":"Check logs."}\n\nevent: final\ndata: ${JSON.stringify(answer)}\n\ndata: [DONE]\n\n`, { headers: { "Content-Type": "text/event-stream" } })));
    await expect(streamMultilang({ text: "Kiểm tra nhật ký.", source_lang: "vi", target_lang: "en" }, chunk)).resolves.toEqual(answer);
    expect(chunk).toHaveBeenCalledWith("Check logs.");
    expect(fetch).toHaveBeenCalledWith("/ai/v3/translate/stream", expect.anything());
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('event: chunk\ndata: {"delta":"Partial"}\n\ndata: [DONE]\n\n')));
    await expect(streamMultilang({ text: "Dịch", source_lang: "vi", target_lang: "en" }, chunk)).rejects.toMatchObject({ code: "STREAM_INCOMPLETE" });
  });

  it("propagates a structured error after a chunk", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('event: chunk\ndata: {"delta":"Partial"}\n\nevent: error\ndata: {"error":{"code":"OUTPUT_VALIDATION_FAILED","request_id":"req-2"}}\n\ndata: [DONE]\n\n')));
    await expect(streamMultilang({ text: "Dịch", source_lang: "vi", target_lang: "en" }, vi.fn())).rejects.toMatchObject({ code: "OUTPUT_VALIDATION_FAILED" });
  });
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
