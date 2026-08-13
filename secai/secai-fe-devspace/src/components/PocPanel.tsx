import { useRef, useState } from "react";
import { exploit, stream, ApiError } from "../api";
import type { ExploitMeta, ToolCall } from "../types";
import Markdown from "./Markdown";
import ToolActivity from "./ToolActivity";
import FileAwareMarkdown, { PocFilePanel, parsePocArtifacts } from "./FileCode";

/** True khi Mục 1 là mã do model tự tổng hợp, chưa fetch/xác thực từ nguồn thật. */
function isSynthesized(meta: ExploitMeta | null): boolean {
  if (!meta) return false;
  return meta.synthesized === true || meta.candidate_status === "not_generated";
}

/** True khi Mục 1 là PoC thật đã fetch + có nguồn gốc kiểm chứng được. */
function isVerified(meta: ExploitMeta | null): boolean {
  if (!meta) return false;
  if (meta.reference_code_injected === true) return true;
  return /found|verified/i.test(meta.candidate_status || "");
}

function Section1Status({ meta }: { meta: ExploitMeta | null }) {
  if (!meta) return null;
  if (isSynthesized(meta)) {
    return (
      <div className="rewrite-banner">
        ⚠️ Mục 1 do AI tự tổng hợp từ mô tả lỗ hổng — <b>chưa fetch/xác thực PoC thật</b>
        {meta.reason ? ` (lý do: ${meta.reason})` : ""}. Cần kiểm chứng lại trước khi dùng.
      </div>
    );
  }
  if (isVerified(meta)) {
    return (
      <div className="rewrite-banner ok">
        ✅ Mục 1 là mã tham chiếu đã fetch từ nguồn công khai (không phải do model bịa ra).
      </div>
    );
  }
  return null;
}

/** PoC / exploit-research panel (SSE + optional non-stream). */
export default function PocPanel({ streaming, userId }: { streaming: boolean; userId: string }) {
  const [input, setInput] = useState("");
  const [out, setOut] = useState("");
  const [section1, setSection1] = useState("");
  const [section1Preview, setSection1Preview] = useState(false);
  const [meta, setMeta] = useState<ExploitMeta | null>(null);
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
    setSection1Preview(false);
    setMeta(null);
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
            onSection1: (body, preview) => {
              setSection1(body);
              setSection1Preview(preview);
            },
            onError: (e) => setError(e),
            onFinal: (p) => {
              const content =
                (p.content as string) || (p.answer as string) || (p.message as string) || "";
              if (content) setOut(content);
              setSection1Preview(false);
              setMeta((p.metadata as ExploitMeta) || null);
            },
          },
          ac.signal,
        );
      } else {
        const r = await exploit(body, ac.signal);
        setOut((r.content as string) || (r.answer as string) || (r.message as string) || "");
        setMeta((r.metadata as ExploitMeta) || null);
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

  const artifacts = section1 ? parsePocArtifacts(section1) : null;
  const evCount = meta?.evidence_ids?.length ?? 0;
  const edbCount = meta?.edb_ids?.length ?? 0;

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
        {!busy && <Section1Status meta={meta} />}
        {section1 && (
          <div style={{ marginTop: 12 }}>
            {artifacts ? (
              <PocFilePanel artifacts={artifacts} preview={section1Preview} />
            ) : (
              <div>
                <div className="role">Mục 1 (PoC)</div>
                <FileAwareMarkdown>{section1}</FileAwareMarkdown>
              </div>
            )}
          </div>
        )}
        {meta && !busy && (
          <div className="meta">
            {meta.profile && <span className="badge">{meta.profile}</span>}
            {meta.candidate_status && (
              <span className={"badge" + (isVerified(meta) ? " ok" : isSynthesized(meta) ? " warn" : "")}>
                {meta.candidate_status}
              </span>
            )}
            {meta.section1_tier && <span className="badge">{meta.section1_tier}</span>}
            {evCount > 0 && <span className="badge">{evCount} evidence</span>}
            {edbCount > 0 && <span className="badge">{edbCount} EDB</span>}
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
