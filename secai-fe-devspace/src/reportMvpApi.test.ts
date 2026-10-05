// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { reportMvpApi, ReportApiError } from "./reportMvpApi";

afterEach(() => vi.unstubAllGlobals());

describe("saved report exports", () => {
  it("downloads the selected saved revision with workspace authentication", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("%PDF-content", { headers: { "Content-Type": "application/pdf" } }));
    vi.stubGlobal("fetch", fetch);
    const result = await reportMvpApi.exportReport("", "saved-id", "pdf", 2);
    expect(fetch.mock.calls[0][0]).toBe("/ai/v2/reports/saved-id/export?format=pdf&revision=2");
    const headers = fetch.mock.calls[0][1].headers;
    expect(headers.get("X-Report-Workspace")).toBe("shared");
    expect(headers.has("X-API-Key")).toBe(false);
    expect(result.blob.size).toBeGreaterThan(0);
    expect(result.filename).toBe("report-saved-id-v2.pdf");
  });

  it("preserves server errors and rejects JSON or empty success responses", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Revision not found" }), { status: 404 }))
      .mockResolvedValueOnce(new Response("{}", { headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response("", { headers: { "Content-Type": "text/plain" } })));
    await expect(reportMvpApi.exportReport("", "id", "txt", 1)).rejects.toMatchObject({ status: 404, message: "Revision not found" });
    await expect(reportMvpApi.exportReport("", "id", "pdf", 1)).rejects.toBeInstanceOf(ReportApiError);
    await expect(reportMvpApi.exportReport("", "id", "txt", 1)).rejects.toBeInstanceOf(ReportApiError);
  });
});
