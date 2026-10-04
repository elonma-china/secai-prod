// @vitest-environment jsdom
import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import TranslateScreen from "./TranslateScreen";
import TranslateFileBatchPanel from "./TranslateFileBatchPanel";

const ids = ["0".repeat(32), "1".repeat(32), "2".repeat(32)];
const job = (n: number, status = "queued", kind = "file") => ({
  job_id: ids[n], kind, status, stage: status === "queued" ? "queued" : "done",
  progress: { completed: 0, total: null }, source_lang: "vi", target_lang: "zh",
  warnings: [], error: null,
});
const answer = (n: number, kind = "file") => ({
  job_id: ids[n], kind, status: "succeeded", source_lang: "vi", target_lang: "zh",
  translated_text: "مرحبا <script>alert(1)</script>", blocks: [], warnings: [{ code: "AMBIGUOUS_TECHNICAL_TOKEN", message: "Review", block_id: null }],
  ocr: kind === "image" ? { raw_text: "RAW", corrected_text: "CORRECTED", edits: [], needs_review: true } : null,
});
const ok = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const files = () => [new File(["docx"], "a.docx"), new File(["png"], "b.png"), new File(["text"], "c.txt")];

beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal("crypto", webcrypto);
  vi.stubEnv("VITE_TRANSLATE_V2_FILES_ENABLED", "true");
  vi.stubEnv("VITE_TRANSLATE_V2_IMAGES_ENABLED", "true");
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); sessionStorage.clear(); });

describe("user-facing v2 file batch", () => {
  it("offers the file tab when v3 capabilities are unavailable, without changing legacy translation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not found", { status: 404 })));
    render(<TranslateScreen userId="user" />);
    expect(await screen.findByRole("button", { name: "Dịch tệp và ảnh" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dịch tệp và ảnh" }));
    expect(screen.getByLabelText("Chọn nhiều tệp cần dịch")).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Ngôn ngữ nguồn của tệp" })).toBeTruthy();
  });

  it("keeps the file tab hidden when the frontend flag is off", async () => {
    vi.stubEnv("VITE_TRANSLATE_V2_FILES_ENABLED", "false");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not found", { status: 404 })));
    render(<TranslateScreen userId="user" />);
    await screen.findByText("Dịch đa ngôn ngữ chưa được bật trên máy chủ.");
    expect(screen.queryByRole("button", { name: "Dịch tệp và ảnh" })).toBeNull();
  });

  it("keeps three mixed files in order while admitting at most two remote jobs", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    const posts: Array<[string, RequestInit]> = [];
    const fetch = vi.fn().mockImplementation(async (url: string, init: RequestInit) => {
      if (init.method === "POST") { posts.push([url, init]); return ok(job(posts.length - 1, "queued", url.endsWith("images") ? "image" : "file"), 202); }
      return ok(job(url.includes(ids[0]) ? 0 : 1));
    });
    vi.stubGlobal("fetch", fetch);
    const { container } = render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByRole("combobox", { name: "Ngôn ngữ nguồn của tệp" }), { target: { value: "vi" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Ngôn ngữ đích của tệp" }), { target: { value: "zh" } });
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: files() } });
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0][0]).toBe("/ai/v2/translate/files");
    expect(container.textContent).toContain("c.txt");
    await act(async () => { await vi.advanceTimersByTimeAsync(14_000); });
    expect(posts).toHaveLength(2);
    expect(posts[1][0]).toBe("/ai/v2/translate/images");
    await act(async () => { await vi.advanceTimersByTimeAsync(50_000); });
    expect(posts).toHaveLength(2);
    const first = posts[0][1].body as FormData;
    expect(first.get("source_lang")).toBe("vi");
    expect(first.get("target_lang")).toBe("zh");
    expect((posts[0][1].headers as Record<string, string>)["Idempotency-Key"]).toMatch(/^[0-9a-f]{32}$/);
  });

  it("honors Retry-After before retrying a 429 with the same key", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    const posts: RequestInit[] = [];
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (_url: string, init: RequestInit) => {
      if (init.method === "POST") {
        posts.push(init);
        return posts.length === 1 ? new Response("", { status: 429, headers: { "Retry-After": "20" } })
          : ok({ ...job(0), target_lang: "en" }, 202);
      }
      return ok({ ...job(0), target_lang: "en" });
    }));
    render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["one"], "a.txt")] } });
    await waitFor(() => expect(posts).toHaveLength(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(19_000); });
    expect(posts).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(posts).toHaveLength(2);
    expect((posts[1].headers as Record<string, string>)["Idempotency-Key"]).toBe((posts[0].headers as Record<string, string>)["Idempotency-Key"]);
  });

  it("continues with another file after a permanent admission rejection", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (_url: string, init: RequestInit) => {
      if (init.method === "POST") {
        posts += 1;
        return posts === 1 ? new Response(JSON.stringify({ error: { code: "UNSUPPORTED_FILE_TYPE" } }), { status: 415 })
          : ok({ ...job(1), target_lang: "en" }, 202);
      }
      return ok({ ...job(1), target_lang: "en" });
    }));
    render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["one"], "a.txt"), new File(["two"], "b.txt")] } });
    await waitFor(() => expect(screen.getByText("UNSUPPORTED_FILE_TYPE")).toBeTruthy());
    await act(async () => { await vi.advanceTimersByTimeAsync(13_000); });
    expect(posts).toBe(2);
    expect(screen.getByText(/queued · queued/)).toBeTruthy();
  });

  it("holds admission after uncertain POST, requires the same file for replay, and resumes after refresh", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    let attempts = 0;
    const posts: RequestInit[] = [];
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (_url: string, init: RequestInit) => {
      if (init.method === "POST") { posts.push(init); attempts += 1; if (attempts === 1) throw new TypeError("disconnect"); return ok({ ...job(0), target_lang: "en" }, 202); }
      return ok({ ...job(0), target_lang: "en" });
    }));
    const { unmount } = render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["one"], "a.txt"), new File(["two"], "b.txt")] } });
    await waitFor(() => expect(screen.getByText(/Chưa rõ máy chủ đã nhận/)).toBeTruthy());
    expect(posts).toHaveLength(1);
    unmount();
    render(<TranslateFileBatchPanel imagesEnabled />);
    expect(screen.getByText(/Chưa rõ máy chủ đã nhận/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Chọn lại tệp a.txt"), { target: { files: [new File(["changed"], "a.txt")] } });
    expect(await screen.findByText(/Tệp đã thay đổi/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Chọn lại tệp a.txt"), { target: { files: [new File(["one"], "a.txt")] } });
    await act(async () => { await vi.advanceTimersByTimeAsync(13_000); });
    await waitFor(() => expect(posts).toHaveLength(2));
    expect((posts[1].headers as Record<string, string>)["Idempotency-Key"]).toBe((posts[0].headers as Record<string, string>)["Idempotency-Key"]);
  });

  it("selects and reselects the same file on insecure LAN HTTP without crypto.subtle", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    vi.stubGlobal("crypto", { getRandomValues: webcrypto.getRandomValues.bind(webcrypto), subtle: undefined });
    vi.stubGlobal("isSecureContext", false);
    const posts: RequestInit[] = [];
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (_url: string, init: RequestInit) => {
      if (init.method === "POST") {
        posts.push(init);
        if (posts.length === 1) throw new TypeError("disconnect");
        return ok({ ...job(0), target_lang: "en" }, 202);
      }
      return ok({ ...job(0), target_lang: "en" });
    }));
    render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["Tiếng Việt 😀"], "a.txt")] } });
    await waitFor(() => expect(screen.getByText(/Chưa rõ máy chủ đã nhận/)).toBeTruthy());
    const row = JSON.parse(sessionStorage.getItem("secai.translate.v2.file.batch.v1") ?? "null").rows[0];
    expect(row.hash).toBe("bd7b4426e12470860f7738247d3c13ffd9c85ae403acd5c2252b9ac26a3c4dd5");
    fireEvent.change(screen.getByLabelText("Chọn lại tệp a.txt"), { target: { files: [new File(["Tiếng Việt 😀"], "a.txt")] } });
    await act(async () => { await vi.advanceTimersByTimeAsync(13_000); });
    expect(posts).toHaveLength(2);
    expect((posts[1].headers as Record<string, string>)["Idempotency-Key"]).toBe((posts[0].headers as Record<string, string>)["Idempotency-Key"]);
  });

  it("does not admit another file while replay of an uncertain POST is still unresolved", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    let resolveReplay!: (response: Response) => void;
    let calls = 0;
    const fetch = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      if (init.method !== "POST") return Promise.resolve(ok(job(0)));
      calls += 1;
      if (calls === 1) return Promise.reject(new TypeError("disconnect"));
      if (calls === 2) return new Promise<Response>((resolve) => { resolveReplay = resolve; });
      return Promise.resolve(ok(job(1), 202));
    });
    vi.stubGlobal("fetch", fetch);
    render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["one"], "a.txt"), new File(["two"], "b.txt")] } });
    await waitFor(() => expect(screen.getByText(/Chưa rõ máy chủ đã nhận/)).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Chọn lại tệp a.txt"), { target: { files: [new File(["one"], "a.txt")] } });
    await act(async () => { await vi.advanceTimersByTimeAsync(13_000); });
    await waitFor(() => expect(calls).toBe(2));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(calls).toBe(2);
    await act(async () => resolveReplay(ok({ ...job(0), target_lang: "en" }, 202)));
  });

  it("keeps the admission halt if an uncertain replay receives a non-202 response", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (_url: string, init: RequestInit) => {
      if (init.method !== "POST") return ok({ ...job(0), target_lang: "en" });
      posts += 1;
      if (posts === 1) throw new TypeError("disconnect");
      return new Response(JSON.stringify({ error: { code: "INVALID_REQUEST" } }), { status: 400 });
    }));
    render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["one"], "a.txt"), new File(["two"], "b.txt")] } });
    await waitFor(() => expect(screen.getByText(/Chưa rõ máy chủ đã nhận/)).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Chọn lại tệp a.txt"), { target: { files: [new File(["one"], "a.txt")] } });
    await act(async () => { await vi.advanceTimersByTimeAsync(50_000); });
    expect(posts).toBe(2);
    expect(screen.getByText(/Chưa rõ máy chủ đã nhận/)).toBeTruthy();
  });

  it("replays the same unknown POST key after 429, reload and matching-file reselection", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    const posts: RequestInit[] = [];
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (_url: string, init: RequestInit) => {
      if (init.method === "POST") {
        posts.push(init);
        if (posts.length === 1) throw new TypeError("disconnect");
        if (posts.length === 2) return new Response("", { status: 429, headers: { "Retry-After": "20" } });
        return ok({ ...job(0), target_lang: "en" }, 202);
      }
      return ok({ ...job(0), target_lang: "en" });
    }));
    const { unmount } = render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["same"], "a.txt"), new File(["later"], "b.txt")] } });
    await waitFor(() => expect(screen.getByText(/Chưa rõ máy chủ đã nhận/)).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Chọn lại tệp a.txt"), { target: { files: [new File(["same"], "a.txt")] } });
    await act(async () => { await vi.advanceTimersByTimeAsync(13_000); });
    expect(posts).toHaveLength(2);
    await waitFor(() => expect(screen.getByText("HTTP 429")).toBeTruthy());
    unmount();
    render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn lại tệp a.txt"), { target: { files: [new File(["same"], "a.txt")] } });
    await act(async () => { await vi.advanceTimersByTimeAsync(21_000); });
    expect(posts).toHaveLength(3);
    expect((posts[2].headers as Record<string, string>)["Idempotency-Key"]).toBe((posts[0].headers as Record<string, string>)["Idempotency-Key"]);
    expect(screen.queryByText(/Chưa rõ máy chủ đã nhận/)).toBeNull();
  });

  it("releases a persisted active job when GET confirms JOB_EXPIRED and admits the next file", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string, init: RequestInit) => {
      if (init.method === "POST") { posts += 1; return ok({ ...job(posts - 1), target_lang: "en" }, 202); }
      if (url.includes(ids[0])) return new Response(JSON.stringify({ error: { code: "JOB_EXPIRED" } }), { status: 410 });
      return ok({ ...job(1), target_lang: "en" });
    }));
    const first = render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["one"], "a.txt")] } });
    await waitFor(() => expect(first.container.textContent).toContain("queued · queued"));
    first.unmount();
    render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["two"], "b.txt")] } });
    await act(async () => { await vi.advanceTimersByTimeAsync(18_000); });
    expect(posts).toBe(2);
    expect(screen.getByText("JOB_EXPIRED")).toBeTruthy();
  });

  it("releases a persisted succeeded job when its result has expired", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    sessionStorage.setItem("secai.translate.v2.file.batch.v1", JSON.stringify({ rows: [{
      id: "a".repeat(32), key: "b".repeat(32), hash: "c".repeat(64), name: "old.txt", size: 3, extension: ".txt",
      kind: "file", source: "vi", target: "en", phase: "succeeded", jobId: ids[0], job: { ...job(0, "succeeded"), target_lang: "en" },
    }], nextUploadAt: 0 }));
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string, init: RequestInit) => {
      if (init.method === "POST") { posts += 1; return ok({ ...job(1), target_lang: "en" }, 202); }
      if (url.includes(ids[0])) return new Response(JSON.stringify({ error: { code: "JOB_EXPIRED" } }), { status: 410 });
      return ok({ ...job(1), target_lang: "en" });
    }));
    render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["new"], "new.txt")] } });
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(posts).toBe(1);
    expect(screen.getByText("JOB_EXPIRED")).toBeTruthy();
  });

  it("releases a persisted cancelling job when DELETE confirms JOB_EXPIRED", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    sessionStorage.setItem("secai.translate.v2.file.batch.v1", JSON.stringify({ rows: [{
      id: "a".repeat(32), key: "b".repeat(32), hash: "c".repeat(64), name: "old.txt", size: 3, extension: ".txt",
      kind: "file", source: "vi", target: "en", phase: "active", jobId: ids[0], cancelRequested: true,
      job: { ...job(0, "cancelling"), target_lang: "en" },
    }], nextUploadAt: 0 }));
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: string, init: RequestInit) => {
      if (init.method === "POST") { posts += 1; return ok({ ...job(1), target_lang: "en" }, 202); }
      if (url.includes(ids[0])) return new Response(JSON.stringify({ error: { code: "JOB_EXPIRED" } }), { status: 410 });
      return ok({ ...job(1), target_lang: "en" });
    }));
    render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["new"], "new.txt")] } });
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(posts).toBe(1);
    expect(screen.getByText("JOB_EXPIRED")).toBeTruthy();
  });

  it("shows translated image text, OCR evidence and warnings without rendering markup", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    const fetch = vi.fn().mockImplementation(async (url: string, init: RequestInit) => {
      if (init.method === "POST") return ok({ ...job(0, "queued", "image"), target_lang: "ar" }, 202);
      if (url.endsWith("/result")) return ok({ ...answer(0, "image"), target_lang: "ar" });
      return ok({ ...job(0, "succeeded", "image"), target_lang: "ar" });
    });
    vi.stubGlobal("fetch", fetch);
    const { container } = render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByRole("combobox", { name: "Ngôn ngữ đích của tệp" }), { target: { value: "ar" } });
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["png"], "a.png")] } });
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init.method === "POST")).toBe(true));
    await act(async () => { await vi.advanceTimersByTimeAsync(9_000); });
    expect(screen.getByText("مرحبا <script>alert(1)</script>")).toBeTruthy();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector(".file-batch-result")?.getAttribute("dir")).toBe("rtl");
    expect(screen.getByText("RAW")).toBeTruthy();
    expect(screen.getByText("CORRECTED")).toBeTruthy();
    expect(screen.getByText(/cần kiểm tra/)).toBeTruthy();
    expect(screen.getByText(/AMBIGUOUS_TECHNICAL_TOKEN/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Tải TXT" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Tải JSON" })).toBeTruthy();
  });

  it("keeps a cancelling job's slot until terminal status is verified", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    let status = "queued";
    let posts = 0;
    const fetch = vi.fn().mockImplementation(async (url: string, init: RequestInit) => {
      if (init.method === "POST") {
        const n = posts++;
        return ok({ ...job(n, "queued", url.endsWith("images") ? "image" : "file"), target_lang: "ar" }, 202);
      }
      if (init.method === "DELETE") { status = "cancelling"; return ok({ ...job(0, "cancelling", "image"), target_lang: "ar" }, 202); }
      const n = url.includes(ids[0]) ? 0 : 1;
      return ok({ ...job(n, n === 0 ? status : "queued", n === 0 ? "image" : "file"), target_lang: "ar" });
    });
    vi.stubGlobal("fetch", fetch);
    const { container } = render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByRole("combobox", { name: "Ngôn ngữ nguồn của tệp" }), { target: { value: "vi" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Ngôn ngữ đích của tệp" }), { target: { value: "ar" } });
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["png"], "a.png"), new File(["two"], "b.txt"), new File(["three"], "c.txt")] } });
    await waitFor(() => expect(container.textContent).toContain("queued · queued"));
    fireEvent.click(screen.getByRole("button", { name: "Hủy a.png" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init.method === "DELETE")).toBe(true));
    expect(container.textContent).toContain("cancelling");
    await act(async () => { await vi.advanceTimersByTimeAsync(50_000); });
    expect(posts).toBe(2);
    expect(container.textContent).toContain("c.txt");
  });

  it("reconciles a cancel/success race with GET instead of repeating DELETE", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "Date"] });
    let deletes = 0;
    let gets = 0;
    const fetch = vi.fn().mockImplementation(async (url: string, init: RequestInit) => {
      if (init.method === "POST") return ok({ ...job(0), target_lang: "en" }, 202);
      if (init.method === "DELETE") {
        deletes += 1;
        return new Response(JSON.stringify({ error: { code: "JOB_ALREADY_FINISHED" } }), { status: 409 });
      }
      if (url.endsWith("/result")) return ok({ ...answer(0), target_lang: "en" });
      gets += 1;
      return ok({ ...job(0, "succeeded"), target_lang: "en" });
    });
    vi.stubGlobal("fetch", fetch);
    const { container } = render(<TranslateFileBatchPanel imagesEnabled />);
    fireEvent.change(screen.getByLabelText("Chọn nhiều tệp cần dịch"), { target: { files: [new File(["one"], "a.txt")] } });
    await waitFor(() => expect(container.textContent).toContain("queued · queued"));
    fireEvent.click(screen.getByRole("button", { name: "Hủy a.txt" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(9_000); });
    expect(deletes).toBe(1);
    expect(gets).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Tải JSON" })).toBeTruthy();
  });
});
