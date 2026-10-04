import { useState } from "react";
import { ArrowLeftRight, Copy, Languages, ShieldCheck, ShieldX } from "lucide-react";
import { ApiError, translateStream } from "../api";
import Markdown from "../components/Markdown";

/** "Dịch văn bản" — gateway contract (see `api/messages.py::translate_langs`): the single
 * `language` field IS THE TARGET language ("dịch sang <language>"), not the source — the
 * backend auto-detects source. Omitting `language` entirely (our "auto" option) hits the
 * bidirectional auto-detect branch (VI↔EN). Literal masking (accounts/passwords/tokens/URLs)
 * always runs server-side for every translate call (`build_translate_prompt` → `mask_literals`)
 * — there's no request field to toggle it off, so the "Giữ verbatim…" indicator below is
 * shown as an always-on guarantee rather than a real switch. */

const MAX_CHARS = 4000;

type Lang = "en" | "vi" | "auto";

const LANG_OPTS: { id: Lang; label: string }[] = [
  { id: "en", label: "English" },
  { id: "vi", label: "Tiếng Việt" },
  { id: "auto", label: "Tự động nhận diện" },
];

export default function TranslateScreen({ userId }: { userId: string }) {
  const [text, setText] = useState("");
  const [language, setLanguage] = useState<Lang>("vi");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);

  async function submit() {
    const value = text.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    setBlocked(null);
    setResult("");
    const ac = new AbortController();
    const body: Record<string, unknown> & { user_id: string } = { text: value, user_id: userId };
    if (language !== "auto") body.language = language;
    try {
      await translateStream(
        body,
        {
          onToken: (delta) => setResult((r) => r + delta),
          onFinal: (payload) =>
            setResult((prev) => String(payload.content ?? payload.translation ?? payload.answer ?? prev)),
          onError: (msg) => setError(msg),
        },
        ac.signal,
      );
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        /* aborted — no-op */
      } else if (e instanceof ApiError && e.status === 422) {
        setBlocked(e.message);
      } else if (e instanceof ApiError) {
        setError(`Lỗi ${e.status}: ${e.message}`);
      } else {
        setError(String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  function swap() {
    if (!result.trim() || busy) return;
    setText(result);
    setResult("");
    setLanguage((l) => (l === "en" ? "vi" : l === "vi" ? "en" : "vi"));
  }

  async function copyResult() {
    try {
      await navigator.clipboard.writeText(result);
    } catch {
      /* clipboard unavailable — silently ignore */
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ padding: "16px 26px", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
        <h3 style={{ fontSize: 21 }}>Dịch thuật bảo mật</h3>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "24px 26px" }}>
        {blocked && (
          <div className="banner-row" style={{ marginBottom: 16 }}>
            <div className="banner-422">
              <ShieldX size={18} strokeWidth={1.5} color="#f0897c" className="icon" />
              <div style={{ fontSize: 13, lineHeight: 1.5 }}>
                <strong>Đầu vào bị chặn (422).</strong>{" "}
                <span style={{ color: "var(--text)" }}>
                  Nội dung có thể chứa bí mật/PII nên không được gửi tới mô hình. Chi tiết: {blocked}
                </span>
              </div>
            </div>
          </div>
        )}
        {error && !blocked && (
          <div className="error-box" style={{ marginBottom: 16 }}>
            {error}
          </div>
        )}

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 64px 1fr",
            gap: 0,
            alignItems: "stretch",
            minHeight: 460,
          }}
        >
          {/* ── Nguồn ── */}
          <div className="iascard" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div className="iascard-head">
              <span style={{ fontSize: 11, color: "var(--muted2)", letterSpacing: "0.06em", textTransform: "uppercase" }}>
                Nguồn <span style={{ textTransform: "none", letterSpacing: 0 }}>· tự động nhận diện</span>
              </span>
            </div>
            <textarea
              className="iasin"
              placeholder="Dán nội dung cần dịch — ví dụ: log, CVE advisory, báo cáo kỹ thuật…"
              style={{ flex: 1, resize: "none", border: "none", borderRadius: 0, background: "transparent", padding: "14px 15px", minHeight: 220 }}
              maxLength={MAX_CHARS}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
                fontSize: 11.5,
                color: "var(--muted2)",
                padding: "9px 15px",
                borderTop: "1px solid var(--line2)",
              }}
            >
              <span
                style={{ display: "flex", alignItems: "center", gap: 6 }}
                title="Máy chủ luôn che (mask) tài khoản/mật khẩu/token trước khi gửi mô hình rồi khôi phục nguyên văn khi trả kết quả — không thể tắt."
              >
                <ShieldCheck size={13} strokeWidth={1.5} color="var(--teal)" />
                Giữ verbatim tài khoản/mật khẩu
              </span>
              <span className="mono">
                {text.length} / {MAX_CHARS}
              </span>
            </div>
          </div>

          {/* ── actions ── */}
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12 }}>
            <button
              className="iasbtn"
              style={{ padding: "11px 0", width: 46, height: 46, borderRadius: "50%" }}
              onClick={submit}
              disabled={busy || !text.trim()}
              aria-label="Dịch"
              title="Dịch"
            >
              <Languages size={18} strokeWidth={1.5} />
            </button>
            <button
              className="iasbtn ghost"
              style={{ padding: 9, width: 38, height: 38, borderRadius: "50%" }}
              onClick={swap}
              disabled={!result.trim() || busy}
              aria-label="Hoán đổi"
              title="Đưa kết quả vào ô nguồn và đổi hướng dịch"
            >
              <ArrowLeftRight size={16} strokeWidth={1.5} />
            </button>
          </div>

          {/* ── Kết quả ── */}
          <div className="iascard" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div className="iascard-head" style={{ justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 11, color: "var(--muted2)", letterSpacing: "0.06em", textTransform: "uppercase" }}>
                  Dịch sang
                </span>
                <div className="iasseg">
                  {LANG_OPTS.map((o) => (
                    <div
                      key={o.id}
                      className={"opt" + (language === o.id ? " on" : "")}
                      onClick={() => setLanguage(o.id)}
                    >
                      {o.label}
                    </div>
                  ))}
                </div>
              </div>
              <button className="iasbtn ghost" style={{ padding: "4px 10px", fontSize: 11.5 }} onClick={copyResult} disabled={!result.trim()}>
                <Copy size={12} strokeWidth={1.5} />
                Sao chép
              </button>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: "14px 15px" }}>
              {!result && !busy && (
                <div
                  style={{
                    height: "100%",
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 8,
                    color: "var(--muted2)",
                    fontSize: 13,
                    textAlign: "center",
                  }}
                >
                  <Languages size={22} strokeWidth={1.5} style={{ opacity: 0.5 }} />
                  Kết quả dịch sẽ hiển thị tại đây
                </div>
              )}
              {(result || busy) && (
                <div style={{ fontSize: 14.5, lineHeight: 1.6 }}>
                  <Markdown>{result}</Markdown>
                  {busy && <span className="blink" style={{ color: "var(--teal)", fontWeight: 700 }}>▌</span>}
                </div>
              )}
            </div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
                fontSize: 11.5,
                color: "var(--muted2)",
                padding: "9px 15px",
                borderTop: "1px solid var(--line2)",
              }}
            >
              <span>Dịch bởi qwen2.5 · security-tuned</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
