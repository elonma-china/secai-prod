import { useRef, useState } from "react";
import { chat, stream, ApiError } from "../api";
import type { ToolCall } from "../types";
import Markdown from "./Markdown";
import ToolActivity from "./ToolActivity";

interface Msg {
  role: "user" | "assistant";
  content: string;
  tools?: ToolCall[];
}

export default function ChatPanel({ streaming, userId }: { streaming: boolean; userId: string }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const scroll = () =>
    requestAnimationFrame(() => scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight));

  function patchLast(fn: (m: Msg) => Msg) {
    setMsgs((prev) => {
      const next = [...prev];
      next[next.length - 1] = fn(next[next.length - 1]);
      return next;
    });
    scroll();
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setError("");
    setInput("");
    setMsgs((p) => [...p, { role: "user", content: text }, { role: "assistant", content: "", tools: [] }]);
    setBusy(true);
    scroll();
    const ac = new AbortController();
    abortRef.current = ac;
    const body = { message: text, user_id: userId || "anonymous" };
    try {
      if (streaming) {
        await stream(
          "/ai/chat/stream",
          body,
          {
            onToken: (d) => patchLast((m) => ({ ...m, content: m.content + d })),
            onRegenerate: () => patchLast((m) => ({ ...m, content: "" })), // huỷ nháp, chờ bản sửa
            onToolCall: (t) => patchLast((m) => ({ ...m, tools: [...(m.tools || []), t] })),
            onToolResult: (t) => patchLast((m) => ({ ...m, tools: [...(m.tools || []), t] })),
            onError: (e) => setError(e),
            onFinal: (p) =>
              patchLast((m) => ({ ...m, content: (p.content as string) || (p.answer as string) || m.content })),
          },
          ac.signal
        );
      } else {
        const r = await chat(body, ac.signal);
        patchLast((m) => ({ ...m, content: (r.response as string) || (r.answer as string) || "" }));
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        setError(e instanceof ApiError ? `Lỗi ${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
    setBusy(false);
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="chat panel">
      <div className="messages" ref={scrollRef}>
        {msgs.length === 0 && <div className="empty">Hỏi trợ lý bảo mật SecAI. Enter để gửi, Shift+Enter xuống dòng.</div>}
        {msgs.map((m, i) => (
          <div key={i} className={"msg " + m.role}>
            <div className="avatar">{m.role === "user" ? "U" : "AI"}</div>
            <div className="bubble">
              <div className="role">{m.role === "user" ? "Bạn" : "SecAI"}</div>
              {m.tools && <ToolActivity tools={m.tools} />}
              {m.role === "assistant" && !m.content && busy && i === msgs.length - 1 ? (
                <span className="spin" />
              ) : (
                <Markdown>{m.content}</Markdown>
              )}
            </div>
          </div>
        ))}
      </div>
      {error && <div className="error-box">{error}</div>}
      <div className="composer">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder="Nhập câu hỏi…"
          disabled={busy}
        />
        {busy ? (
          <button className="btn danger" onClick={stop}>Dừng</button>
        ) : (
          <button className="btn" onClick={send} disabled={!input.trim()}>Gửi</button>
        )}
      </div>
    </div>
  );
}
