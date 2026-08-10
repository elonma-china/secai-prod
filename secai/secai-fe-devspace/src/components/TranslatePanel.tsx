import { useRef, useState } from "react";
import { translate, stream, ApiError } from "../api";
import Markdown from "./Markdown";

const LANGS = [
  { v: "", label: "Tự nhận diện" },
  { v: "Tiếng Việt", label: "Tiếng Việt" },
  { v: "English", label: "English" },
  { v: "中文", label: "中文" },
  { v: "日本語", label: "日本語" },
];

export default function TranslatePanel({ streaming }: { streaming: boolean }) {
  const [text, setText] = useState("");
  const [src, setSrc] = useState("");
  const [tgt, setTgt] = useState("English");
  const [out, setOut] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  async function run() {
    if (!text.trim() || busy) return;
    setError("");
    setOut("");
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;
    const body: Record<string, unknown> = { text: text.trim(), target_lang: tgt };
    if (src) body.source_lang = src;
    try {
      if (streaming) {
        await stream(
          "/ai/translate/stream",
          body,
          {
            onToken: (d) => setOut((p) => p + d),
            onRegenerate: () => setOut(""),
            onError: (e) => setError(e),
            onFinal: (p) =>
              setOut((p.translated_text as string) || (p.translation as string) || (p.content as string) || ""),
          },
          ac.signal
        );
      } else {
        const r = await translate(body, ac.signal);
        setOut((r.translated_text as string) || (r.translation as string) || "");
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError")
        setError(e instanceof ApiError ? `Lỗi ${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  function swap() {
    if (!src) return; // không đổi khi nguồn là auto
    setSrc(tgt);
    setTgt(src);
    setText(out);
    setOut("");
  }

  return (
    <div className="panel">
      <div className="card">
        <div className="row" style={{ marginBottom: 12 }}>
          <label className="lbl" style={{ flex: 1 }}>
            Nguồn
            <select value={src} onChange={(e) => setSrc(e.target.value)}>
              {LANGS.map((l) => <option key={l.v} value={l.v}>{l.label}</option>)}
            </select>
          </label>
          <button className="btn ghost sm" style={{ alignSelf: "flex-end" }} onClick={swap} title="Đảo chiều">⇄</button>
          <label className="lbl" style={{ flex: 1 }}>
            Đích
            <select value={tgt} onChange={(e) => setTgt(e.target.value)}>
              {LANGS.filter((l) => l.v).map((l) => <option key={l.v} value={l.v}>{l.label}</option>)}
            </select>
          </label>
        </div>
        <div className="grid2">
          <label className="lbl">
            Văn bản gốc
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={10} placeholder="Nhập văn bản cần dịch…" />
          </label>
          <label className="lbl">
            Kết quả
            <div className="output" style={{ minHeight: 200, padding: "10px 14px" }}>
              {out ? <Markdown>{out}</Markdown> : busy ? <span className="spin" /> : <span className="hint">Bản dịch hiển thị ở đây.</span>}
            </div>
          </label>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          {busy ? (
            <button className="btn danger" onClick={() => abortRef.current?.abort()}>Dừng</button>
          ) : (
            <button className="btn" onClick={run} disabled={!text.trim()}>Dịch</button>
          )}
          {out && <button className="btn ghost" onClick={() => navigator.clipboard.writeText(out)}>Copy</button>}
        </div>
      </div>
      {error && <div className="error-box">{error}</div>}
    </div>
  );
}
