import { afterEach, describe, expect, it, vi } from "vitest";
import { cancelTranslationJob, getTranslationJob, getTranslationResult, submitTranslationFile, TranslateV2Error } from "./translateV2";

const id = "0123456789abcdef0123456789abcdef";
const key = "fedcba9876543210fedcba9876543210";
const job = { job_id: id, kind: "image", status: "queued", stage: "queued", progress: { completed: 0, total: null }, source_lang: "vi", target_lang: "zh", warnings: [], error: null };

afterEach(() => { vi.unstubAllGlobals(); });

describe("translation v2 browser client", () => {
  it("posts one image with exact v2 multipart fields and a stable key", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(job), { status: 202 }));
    vi.stubGlobal("fetch", fetch);
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "source.png", { type: "image/png" });
    await expect(submitTranslationFile(file, "vi", "zh", key)).resolves.toMatchObject(job);
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/ai/v2/translate/images");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Idempotency-Key": key });
    expect(Array.from((init.body as FormData).entries())).toEqual([["file", file], ["source_lang", "vi"], ["target_lang", "zh"]]);
  });

  it("routes a DOCX to files and preserves job, result and cancel URLs", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ ...job, kind: "file" }), { status: 202 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(job), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...job, status: "succeeded", translated_text: "مرحبا", blocks: [], ocr: { raw_text: "raw", corrected_text: "corrected", edits: [], needs_review: true } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...job, status: "cancelled" }), { status: 202 }));
    vi.stubGlobal("fetch", fetch);
    await submitTranslationFile(new File(["text"], "source.docx"), "ar", "vi", key);
    await getTranslationJob(id);
    const result = await getTranslationResult(id);
    await cancelTranslationJob(id);
    expect(result.ocr?.raw_text).toBe("raw");
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      "/ai/v2/translate/files", `/ai/v2/translate/jobs/${id}`, `/ai/v2/translate/jobs/${id}/result`, `/ai/v2/translate/jobs/${id}`,
    ]);
    expect((fetch.mock.calls[3][1] as RequestInit).method).toBe("DELETE");
  });

  it("classifies HTML 503 and 429 before reading optional JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response("<html>bad gateway</html>", { status: 503 }))
      .mockResolvedValueOnce(new Response("", { status: 429, headers: { "Retry-After": "7" } })));
    const file = new File(["hi"], "a.txt");
    await expect(submitTranslationFile(file, "vi", "en", key)).rejects.toMatchObject({ status: 503, retryAfter: null });
    await expect(submitTranslationFile(file, "vi", "en", key)).rejects.toMatchObject({ status: 429, retryAfter: 7 });
  });

  it("does not treat a non-202 upload response as an accepted durable job", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(job), { status: 200 })));
    await expect(submitTranslationFile(new File(["hi"], "a.txt"), "vi", "en", key))
      .rejects.toMatchObject({ status: 0, code: "INVALID_RESPONSE" });
  });

  it("bounds a stalled POST and reports uncertain transport without inventing a job", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn().mockImplementation((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));
    const request = submitTranslationFile(new File(["hi"], "a.txt"), "vi", "en", key);
    const assertion = expect(request).rejects.toBeInstanceOf(TranslateV2Error);
    await vi.advanceTimersByTimeAsync(21_000);
    await assertion;
    vi.useRealTimers();
  });
});
