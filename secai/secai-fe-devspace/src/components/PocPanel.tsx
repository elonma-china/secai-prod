import { useRef, useState } from "react";
import { exploit, stream, ApiError } from "../api";
import type { ToolCall } from "../types";
import Markdown from "./Markdown";
import ToolActivity from "./ToolActivity";
import FileAwareMarkdown from "./FileCode";

/** PoC / exploit-research panel (SSE + optional non-stream). */
export default function PocPanel({ streaming, userId }: { streaming: boolean; userId: string }) {
  const [input, setInput] = useState("");
  const [out, setOut] = useState("");
  const [section1, setSection1] = useState("");
  const [tools, setTools] = useState<ToolCall[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  async function run() {
    const text = input.trim();
    if (!text || busy) return;
    setError("");
    setOut("");
    setSection1("");
    setTools([]);
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;
    const body = { message: text, user_id: userId || "anonymous" };
    try {
      if (streaming) {
        await stream(
          "/ai/exploit/stream",
          body,
          {
            onToken: (d) => setOut((p) => p + d),
            onRegenerate: () => setOut(""),
            onToolCall: (t) => setTools((p) => [...p, t]),
            onToolResult: (t) => setTools((p) => [...p, t]),
            onSection1: (body, _preview) => setSection1(body),
            onError: (e) => setError(e),
            onFinal: (p) => {
              const content =
                (p.content as string) || (p.answer as string) || (p.message as string) || "";
              if (content) setOut(content);
            },
          },
          ac.signal,
        );
      } else {
        const r = await exploit(body, ac.signal);
        setOut((r.content as string) || (r.answer as string) || (r.message as string) || "");
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError(e instanceof ApiError ? `Lỗi ${e.status}: ${e.message}` : String(e));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
    setBusy(false);
  }

  return (
    <div className="panel">
      <div className="card">
        <p className="muted">Exploit research — tìm / fetch PoC (không bịa mã). Streaming khuyến nghị.</p>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="VD: Phân tích CVE-2021-44228 và PoC công khai…"
          disabled={busy}
          rows={4}
        />
        <div className="row" style={{ marginTop: 10 }}>
          {busy ? (
            <button className="btn danger" onClick={stop}>Dừng</button>
          ) : (
            <button className="btn" onClick={run} disabled={!input.trim()}>Chạy</button>
          )}
        </div>
        {error && <div className="error-box">{error}</div>}
        <ToolActivity tools={tools} />
        {section1 && (
          <div style={{ marginTop: 12 }}>
            <div className="role">Mục 1 (PoC)</div>
            <FileAwareMarkdown>{section1}</FileAwareMarkdown>
          </div>
        )}
        {out && (
          <div style={{ marginTop: 12 }}>
            <Markdown>{out}</Markdown>
          </div>
        )}
      </div>
    </div>
  );
}
