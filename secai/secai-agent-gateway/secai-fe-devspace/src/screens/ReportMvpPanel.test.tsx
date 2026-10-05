// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ReportMvpPanel from "./ReportMvpPanel";

const template = { id: "sample", name: "Báo cáo kiểm tra", description: "", sections: ["Kết quả"], instructions: "", example: "", version: 1, created_at: "2026-10-05", updated_at: "2026-10-05" };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("workspace reports", () => {
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
