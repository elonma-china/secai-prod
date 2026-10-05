// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ReportMvpPanel from "./ReportMvpPanel";

const template = { id: "sample", name: "Báo cáo kiểm tra", description: "", sections: ["Kết quả"], instructions: "", example: "", version: 1, created_at: "2026-10-05", updated_at: "2026-10-05" };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("workspace reports", () => {
  it("exports the revision being previewed as a download and disables exports while waiting", async () => {
    const report = { id: "saved-id", revision: 2, template_id: "sample", template_version: 1, content: "New text", created_at: "2026-10-05" };
    let resolveExport!: (response: Response) => void;
    const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify([template])))
      .mockResolvedValueOnce(new Response(JSON.stringify(report)))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ revision: 1, content: "Old text", created_at: "2026-10-04" }, { revision: 2, content: "New text", created_at: "2026-10-05" }])))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveExport = resolve; }));
    vi.stubGlobal("fetch", fetch);
    const createUrl = vi.fn().mockReturnValue("blob:test");
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = createUrl;
      static revokeObjectURL = vi.fn();
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    render(<ReportMvpPanel />);
    expect(screen.queryByRole("button", { name: "Tải PDF" })).toBeNull();
    await screen.findByRole("option", { name: "Báo cáo kiểm tra · v1" });
    fireEvent.change(screen.getByPlaceholderText("Dán ID để mở lại báo cáo"), { target: { value: "saved-id" } });
    fireEvent.click(screen.getByRole("button", { name: "Mở báo cáo" }));
    const historical = await screen.findByRole("button", { name: /^Bản 1 ·/ });
    await waitFor(() => expect(screen.getByRole("button", { name: "Tải PDF" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(historical);
    fireEvent.click(screen.getByRole("button", { name: "Tải PDF" }));
    expect(fetch.mock.calls[3][0]).toBe("/ai/v2/reports/saved-id/export?format=pdf&revision=1");
    expect(screen.getByRole("button", { name: "Tải TXT" }).hasAttribute("disabled")).toBe(true);
    resolveExport(new Response("%PDF-content", { headers: { "Content-Type": "application/pdf" } }));
    await screen.findByText("Đã tải báo cáo bản 1 (PDF).");
    expect(createUrl).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(print).not.toHaveBeenCalled();
  });

  it("shows export failure without triggering a download", async () => {
    const report = { id: "saved-id", revision: 1, template_id: "sample", template_version: 1, content: "Text", created_at: "2026-10-05" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify([template])))
      .mockResolvedValueOnce(new Response(JSON.stringify(report)))
      .mockResolvedValueOnce(new Response("[]"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Export unavailable" }), { status: 422 })));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<ReportMvpPanel />);
    await screen.findByRole("option", { name: "Báo cáo kiểm tra · v1" });
    fireEvent.change(screen.getByPlaceholderText("Dán ID để mở lại báo cáo"), { target: { value: "saved-id" } });
    fireEvent.click(screen.getByRole("button", { name: "Mở báo cáo" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Tải Word" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Tải Word" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Export unavailable");
    expect(click).not.toHaveBeenCalled();
  });

  it("loads templates immediately without a password field or client API key", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify([template])));
    vi.stubGlobal("fetch", fetch);
    const { container } = render(<ReportMvpPanel />);
    expect(await screen.findByRole("option", { name: "Báo cáo kiểm tra · v1" })).toBeTruthy();
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(screen.queryByText("Khóa API báo cáo")).toBeNull();
    const [path, options] = fetch.mock.calls[0];
    expect(path).toBe("/ai/v2/reports/templates");
    expect(options.headers.get("X-Report-Workspace")).toBe("shared");
    expect(options.headers.has("X-API-Key")).toBe(false);
  });

  it("can retry a server workspace configuration failure without entering a key", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response("{}", { status: 503 })).mockResolvedValueOnce(new Response(JSON.stringify([template])));
    vi.stubGlobal("fetch", fetch);
    render(<ReportMvpPanel />);
    expect((await screen.findByRole("alert")).textContent).toContain("chưa được cấu hình trên máy chủ");
    await waitFor(() => expect(screen.getByRole("button", { name: "Tải lại danh sách mẫu" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "Tải lại danh sách mẫu" }));
    expect(await screen.findByRole("option", { name: "Báo cáo kiểm tra · v1" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
