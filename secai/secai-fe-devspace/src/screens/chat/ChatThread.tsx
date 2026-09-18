import type { KeyboardEvent, RefObject } from "react";
import {
  Bot,
  Copy,
  Lock,
  MessageSquarePlus,
  Paperclip,
  RefreshCw,
  Search,
  Send,
  Shield,
  ShieldX,
  Sparkles,
  Square,
} from "lucide-react";
import type { ChatView } from "../../types";
import Markdown from "../../components/Markdown";
import ToolTimeline from "../../components/shared/ToolTimeline";
import PhaseChips from "../../components/shared/PhaseChips";
import Blueprint from "../../components/shared/Blueprint";
import { SUGGESTIONS, detectCve, splitCveTitle, type ThreadItem } from "./threadItems";

export default function ChatThread({
  view,
  projectName,
  items,
  input,
  busy,
  threadRef,
  textareaRef,
  onInputChange,
  onComposerKey,
  onSend,
  onStop,
  onCopy,
  onRegenerate,
  onSuggestion,
}: {
  view: ChatView;
  projectName: string | null;
  items: ThreadItem[];
  input: string;
  busy: boolean;
  threadRef: RefObject<HTMLDivElement>;
  textareaRef: RefObject<HTMLTextAreaElement>;
  onInputChange: (v: string) => void;
  onComposerKey: (e: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  onStop: () => void;
  onCopy: (text: string) => void;
  onRegenerate: (assistantId: string) => void;
  onSuggestion: (text: string) => void;
}) {
  const isDraftEmpty = view.kind === "draft" && items.length === 0;

  return (
    <div className="thread-wrap">
      <div className="thread" ref={threadRef}>
        <div className="thread-inner">
          {isDraftEmpty ? (
            <div className="thread-empty-wrap">
              <Blueprint className="thread-empty">
                <div className="icon">
                  <MessageSquarePlus size={22} strokeWidth={1.5} />
                </div>
                <h4>{projectName ? `Hỏi trong dự án ${projectName}` : "Bạn muốn hỏi gì hôm nay?"}</h4>
                <p>Đặt câu hỏi về CVE, cấu hình hoặc log — trợ lý sẽ trả lời ngay bên dưới.</p>
                <div className="composer-suggest" style={{ justifyContent: "center" }}>
                  {SUGGESTIONS.map((s, i) => (
                    <span key={s} className="chip click" onClick={() => onSuggestion(s)}>
                      {i === 0 ? <Sparkles size={12} strokeWidth={1.5} /> : i === 2 ? <Shield size={12} strokeWidth={1.5} /> : <Search size={12} strokeWidth={1.5} />}
                      {s}
                    </span>
                  ))}
                </div>
              </Blueprint>
            </div>
          ) : items.length === 0 ? (
            <div className="rail-empty">Chưa có tin nhắn. Hỏi trợ lý điều gì đó về CVE, cấu hình hoặc log.</div>
          ) : (
            items.map((it) => {
              if (it.kind === "user") {
                return (
                  <div key={it.id} className="msg-row user">
                    <div className="bubble-user">{it.text}</div>
                  </div>
                );
              }
              if (it.kind === "blocked422") {
                return (
                  <div key={it.id} className="banner-row">
                    <div className="banner-422">
                      <ShieldX size={18} strokeWidth={1.5} color="#f0897c" className="icon" />
                      <div style={{ fontSize: 13, lineHeight: 1.5 }}>
                        <strong>Đầu vào chứa bí mật / PII — đã chặn (422).</strong>{" "}
                        <span style={{ color: "var(--text)" }}>
                          Nội dung không được gửi tới mô hình để bảo vệ bạn. Hãy loại bỏ thông tin nhạy cảm rồi thử lại.
                        </span>
                      </div>
                    </div>
                  </div>
                );
              }

              const cve = !it.streaming && it.text ? detectCve(it.text) : null;
              const foot = !it.streaming && it.text && (
                <div className="msg-foot">
                  {it.runId && <span className="mono runid">run_id: {it.runId}</span>}
                  <div className="actions">
                    <span onClick={() => onCopy(it.text)}>
                      <Copy size={13} strokeWidth={1.5} />
                      Sao chép
                    </span>
                    <span onClick={() => onRegenerate(it.id)}>
                      <RefreshCw size={13} strokeWidth={1.5} />
                      Tạo lại
                    </span>
                  </div>
                </div>
              );

              if (cve) {
                const { title, body } = splitCveTitle(it.text, cve.id);
                return (
                  <div key={it.id} className="msg-row">
                    <div className="msg-avatar">
                      <Bot size={16} strokeWidth={1.5} />
                    </div>
                    <div className="msg-col">
                      {it.tools.length > 0 && <ToolTimeline tools={it.tools} defaultOpen={false} />}
                      <Blueprint className="iascard cve-card">
                        <h4>{title}</h4>
                        <div className="cve-tags">
                          {cve.cvss && (
                            <span className={"chip " + (Number(cve.cvss) >= 9 || cve.severity === "CRITICAL" ? "crit" : "warn")}>
                              CVSS {cve.cvss}
                              {cve.severity ? ` · ${cve.severity}` : ""}
                            </span>
                          )}
                          {cve.cwe.map((c) => (
                            <span key={c} className="chip">
                              {c}
                            </span>
                          ))}
                          {cve.kev && <span className="chip warn">CISA KEV</span>}
                        </div>
                        <Markdown>{body}</Markdown>
                        {foot}
                      </Blueprint>
                    </div>
                  </div>
                );
              }

              return (
                <div key={it.id} className="msg-row">
                  <div className="msg-avatar">
                    <Bot size={16} strokeWidth={1.5} />
                  </div>
                  <div className="msg-col">
                    {it.tools.length > 0 && <ToolTimeline tools={it.tools} defaultOpen={it.streaming} />}
                    {it.phases.length > 0 && <PhaseChips items={it.phases} />}
                    {it.streaming && !it.text && (
                      <span className="chip accent" style={{ gap: 5 }}>
                        <span className="blink" style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--teal)" }} />
                        đang soạn…
                      </span>
                    )}
                    {(it.text || !it.streaming) && (
                      <div className="bubble-assistant">
                        <Markdown>{it.text}</Markdown>
                        {it.streaming && it.text && <span className="blink" style={{ color: "var(--teal)", fontWeight: 700 }}>▌</span>}
                      </div>
                    )}
                    {it.error && <div style={{ color: "var(--danger)", fontSize: 12 }}>{it.error}</div>}
                    {foot}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      <div className="composer-wrap">
        <div className="composer-inner">
          {!isDraftEmpty && (
            <div className="composer-suggest">
              {SUGGESTIONS.map((s, i) => (
                <span key={s} className="chip click" onClick={() => onSuggestion(s)}>
                  {i === 0 ? <Sparkles size={12} strokeWidth={1.5} /> : i === 2 ? <Shield size={12} strokeWidth={1.5} /> : <Search size={12} strokeWidth={1.5} />}
                  {s}
                </span>
              ))}
            </div>
          )}
          <div className="composer-box">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => onInputChange(e.target.value)}
              onKeyDown={onComposerKey}
              placeholder="Nhập nội dung…"
              disabled={busy}
            />
            <div className="composer-actions">
              <button className="iasbtn ghost" style={{ padding: "9px 11px" }} aria-label="Đính kèm" disabled>
                <Paperclip size={16} strokeWidth={1.5} />
              </button>
              {busy ? (
                <button className="iasbtn ghost" style={{ padding: "9px 13px", fontSize: 13 }} onClick={onStop}>
                  <Square size={12} strokeWidth={1.5} fill="currentColor" />
                  Dừng
                </button>
              ) : (
                <button className="iasbtn" style={{ padding: "9px 22px" }} onClick={onSend} disabled={!input.trim()}>
                  <Send size={15} strokeWidth={1.5} />
                  Gửi
                </button>
              )}
            </div>
          </div>
          <div className="composer-hint">
            <Lock size={12} strokeWidth={1.5} />
            Đầu vào được kiểm tra secret/PII · nội dung Markdown được làm sạch (DOMPurify) trước khi hiển thị
          </div>
        </div>
      </div>
    </div>
  );
}
