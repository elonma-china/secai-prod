import { useEffect, useRef, useState } from "react";
import {
  Bot,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  Folder,
  FolderOpen,
  History,
  Lock,
  MessageSquare,
  MessageSquarePlus,
  Paperclip,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Send,
  Shield,
  ShieldX,
  Sparkles,
  Square,
  Trash2,
} from "lucide-react";
import {
  ApiError,
  chatStream,
  createProject,
  createSession,
  deleteProject,
  getSessionMessages,
  listProjects,
  listSessions,
  renameProject,
} from "../api";
import type { PhaseEvent, ToolCall } from "../lib/sse";
import type { ContentBlock, HealthStatus, Project, SessionMessageRow, SessionSummary } from "../types";
import Markdown from "../components/Markdown";
import ToolTimeline from "../components/shared/ToolTimeline";
import PhaseChips, { type PhaseItem } from "../components/shared/PhaseChips";
import StatusChips from "../components/shared/StatusChips";
import Blueprint from "../components/shared/Blueprint";

// ── thread item model ────────────────────────────────────────────────────────────────

type ThreadItem =
  | { kind: "user"; id: string; text: string }
  | {
      kind: "assistant";
      id: string;
      text: string;
      tools: ToolCall[];
      phases: PhaseItem[];
      streaming: boolean;
      runId?: string;
      error?: string;
    }
  | { kind: "blocked422"; id: string; detail: string };

const SUGGESTIONS = ["Tóm tắt CVE mới nhất", "Phân tích log này", "Khuyến nghị vá"];

function upsertTool(tools: ToolCall[], t: ToolCall): ToolCall[] {
  const i = tools.findIndex((x) => x.id === t.id);
  if (i < 0) return [...tools, t];
  const next = [...tools];
  next[i] = { ...next[i], ...t };
  return next;
}

/** Fold an incoming `phase` SSE event into the running chip list: mark whatever was active
 * as done, add/re-activate the incoming one. */
function foldPhase(phases: PhaseItem[], p: PhaseEvent): PhaseItem[] {
  const label = p.title || p.kind || "phase";
  const key = `${p.kind || "phase"}:${p.call_key ?? p.occurrence ?? label}`;
  const marked = phases.map((ph) => (ph.status === "active" ? { ...ph, status: "done" as const } : ph));
  const idx = marked.findIndex((ph) => ph.key === key);
  if (idx >= 0) {
    marked[idx] = { ...marked[idx], status: "active", label };
    return marked;
  }
  return [...marked, { key, label, status: "active" }];
}

/** Best-effort CVE-record detection in a finished assistant answer, so we can frame it as the
 * rich CVE result card (title/CVSS/CWE/KEV tags) instead of a plain bubble. Doesn't reparse
 * the whole answer structure — the backend already writes a well-formed CVE writeup; this
 * only pulls out the bits needed for the tag header. */
function detectCve(text: string): { id: string; cvss?: string; severity?: string; cwe: string[]; kev: boolean } | null {
  const idm = /CVE-\d{4}-\d{4,7}/i.exec(text);
  if (!idm) return null;
  const cvssm = /CVSS[^0-9]{0,15}(\d{1,2}\.\d)/i.exec(text);
  const sevm = /\b(CRITICAL|HIGH|MEDIUM|LOW)\b/i.exec(text);
  const cwe = Array.from(new Set((text.match(/CWE-\d+/gi) || []).map((c) => c.toUpperCase()))).slice(0, 3);
  const kev = /\bKEV\b|CISA KEV|Known Exploited/i.test(text);
  return { id: idm[0].toUpperCase(), cvss: cvssm?.[1], severity: sevm?.[1]?.toUpperCase(), cwe, kev };
}

/** If the answer opens with a heading naming the CVE, use it as the card title and drop it
 * from the body (the card already renders it styled); otherwise title = the CVE id alone. */
function splitCveTitle(text: string, cveId: string): { title: string; body: string } {
  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length && !lines[i].trim()) i++;
  const first = (lines[i] || "").trim();
  const m = /^#{1,6}\s+(.+)$/.exec(first);
  if (m && m[1].toUpperCase().includes(cveId)) {
    return { title: m[1].trim(), body: lines.slice(i + 1).join("\n").replace(/^\n+/, "") };
  }
  return { title: cveId, body: text };
}

function rowsToItems(rows: SessionMessageRow[]): ThreadItem[] {
  const out: ThreadItem[] = [];
  for (const row of rows) {
    const role = row.message.role;
    const blocks = row.message.content || [];
    const text = blocks
      .filter((b): b is Extract<ContentBlock, { type: "text" }> => b.type === "text")
      .map((b) => b.text)
      .join("\n\n");
    if (role === "user") {
      if (text.trim()) out.push({ kind: "user", id: `u-${row.seq}`, text });
      continue;
    }
    if (role !== "assistant") continue;
    const tools: ToolCall[] = [];
    for (const b of blocks) {
      if (b.type === "tool_use") {
        tools.push({ id: b.id, name: b.name, input: b.input });
      } else if (b.type === "tool_result") {
        const content = typeof b.content === "string" ? b.content : JSON.stringify(b.content ?? "");
        const existing = tools.find((t) => t.id === b.tool_use_id);
        if (existing) {
          existing.result = content;
          existing.is_error = b.is_error;
        } else {
          tools.push({ id: b.tool_use_id, name: "tool", result: content, is_error: b.is_error });
        }
      }
    }
    out.push({ kind: "assistant", id: `a-${row.seq}`, text, tools, phases: [], streaming: false });
  }
  return out;
}

export default function ChatbotScreen({ userId, health }: { userId: string; health: HealthStatus | null }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [openProjectId, setOpenProjectId] = useState<string | null>(null);
  const [sessionsByProject, setSessionsByProject] = useState<Record<string, SessionSummary[]>>({});
  const [selected, setSelected] = useState<{ pid: string; sid: string } | null>(null);
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [railSearch, setRailSearch] = useState("");
  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [renamingProjectId, setRenamingProjectId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const [items, setItems] = useState<ThreadItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  // Reload the project list whenever the active user_id changes (owner-scoped listing).
  useEffect(() => {
    let alive = true;
    setProjects([]);
    setSessionsByProject({});
    setOpenProjectId(null);
    setSelected(null);
    setItems([]);
    listProjects(userId)
      .then((ps) => alive && setProjects(ps))
      .catch(() => alive && setProjects([]));
    return () => {
      alive = false;
    };
  }, [userId]);

  function scrollToBottom() {
    requestAnimationFrame(() => threadRef.current?.scrollTo(0, threadRef.current.scrollHeight));
  }

  function patchAssistant(id: string, fn: (m: Extract<ThreadItem, { kind: "assistant" }>) => Partial<Extract<ThreadItem, { kind: "assistant" }>>) {
    setItems((prev) =>
      prev.map((it) => (it.id === id && it.kind === "assistant" ? { ...it, ...fn(it) } : it)),
    );
  }

  async function toggleProject(pid: string) {
    const next = openProjectId === pid ? null : pid;
    setOpenProjectId(next);
    if (next && !sessionsByProject[pid]) {
      try {
        const list = await listSessions(pid, userId);
        setSessionsByProject((prev) => ({ ...prev, [pid]: list }));
      } catch {
        setSessionsByProject((prev) => ({ ...prev, [pid]: [] }));
      }
    }
  }

  async function selectSession(pid: string, sid: string) {
    setSelected({ pid, sid });
    setOpenProjectId(pid);
    setItems([]);
    try {
      const res = await getSessionMessages(sid, userId);
      setItems(rowsToItems(res.messages));
      scrollToBottom();
    } catch (e) {
      setItems([
        {
          kind: "assistant",
          id: "load-err",
          text: e instanceof ApiError ? `Không tải được lịch sử (${e.status}): ${e.message}` : `Không tải được lịch sử: ${String(e)}`,
          tools: [],
          phases: [],
          streaming: false,
        },
      ]);
    }
  }

  async function submitNewProject() {
    const name = newProjectName.trim();
    setCreatingProject(false);
    setNewProjectName("");
    if (!name) return;
    try {
      const proj = await createProject(name, userId);
      setProjects((prev) => [proj, ...prev]);
      await toggleProject(proj.project_id);
    } catch (e) {
      window.alert(`Không tạo được dự án: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function submitRename(pid: string) {
    const name = renameValue.trim();
    setRenamingProjectId(null);
    if (!name) return;
    try {
      const updated = await renameProject(pid, name, userId);
      setProjects((prev) => prev.map((p) => (p.project_id === pid ? updated : p)));
    } catch (e) {
      window.alert(`Không đổi tên được: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function newSessionIn(pid: string) {
    try {
      const res = await createSession(pid, userId);
      const row: SessionSummary = { session_id: res.session_id, title: res.title || "", created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      setSessionsByProject((prev) => ({ ...prev, [pid]: [row, ...(prev[pid] || [])] }));
      await selectSession(pid, res.session_id);
    } catch (e) {
      window.alert(`Không tạo được phiên: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function deleteProj(pid: string, name: string) {
    if (!window.confirm(`Xoá dự án "${name}" và toàn bộ phiên bên trong? Không thể hoàn tác.`)) return;
    try {
      await deleteProject(pid, userId);
      setProjects((prev) => prev.filter((p) => p.project_id !== pid));
      setSessionsByProject((prev) => {
        const next = { ...prev };
        delete next[pid];
        return next;
      });
      if (openProjectId === pid) setOpenProjectId(null);
      if (selected?.pid === pid) setSelected(null);
    } catch (e) {
      window.alert(`Không xoá được dự án: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  /** Empty-state "Tạo phiên" / "Hỏi mẫu": get the user into a working session with the
   * fewest clicks — reuse an already-open project if there is one, else spin up a default. */
  async function quickStartSession(prefill?: string) {
    let pid = openProjectId || projects[0]?.project_id;
    if (!pid) {
      try {
        const proj = await createProject("Dự án mới", userId);
        setProjects((prev) => [proj, ...prev]);
        pid = proj.project_id;
      } catch (e) {
        window.alert(`Không tạo được dự án: ${e instanceof Error ? e.message : String(e)}`);
        return;
      }
    }
    await toggleProject(pid);
    await newSessionIn(pid);
    if (prefill) setInput(prefill);
  }

  async function send(overrideText?: string) {
    const text = (overrideText ?? input).trim();
    if (!text || busy || !selected) return;
    setInput("");
    const uid = `u-${Date.now()}`;
    const aid = `a-${Date.now()}`;
    setItems((prev) => [
      ...prev,
      { kind: "user", id: uid, text },
      { kind: "assistant", id: aid, text: "", tools: [], phases: [], streaming: true },
    ]);
    setBusy(true);
    scrollToBottom();
    const ac = new AbortController();
    abortRef.current = ac;
    const sid = selected.sid;
    try {
      await chatStream(
        sid,
        text,
        userId,
        {
          onToken: (delta) => {
            patchAssistant(aid, (m) => ({ text: m.text + delta }));
            scrollToBottom();
          },
          onRegenerate: () => patchAssistant(aid, () => ({ text: "" })),
          onToolCall: (t) => patchAssistant(aid, (m) => ({ tools: upsertTool(m.tools, t) })),
          onToolResult: (t) => patchAssistant(aid, (m) => ({ tools: upsertTool(m.tools, t) })),
          onPhase: (p) => patchAssistant(aid, (m) => ({ phases: foldPhase(m.phases, p) })),
          onError: (msg) => patchAssistant(aid, () => ({ error: msg })),
          onFinal: (payload) =>
            patchAssistant(aid, (m) => ({
              text: String(payload.content ?? payload.answer ?? m.text),
              streaming: false,
              runId: (payload.metadata?.run_id as string | undefined) ?? undefined,
            })),
        },
        ac.signal,
      );
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        patchAssistant(aid, () => ({ streaming: false }));
      } else if (e instanceof ApiError && e.status === 422) {
        setItems((prev) => prev.map((it) => (it.id === aid ? { kind: "blocked422", id: aid, detail: e.message } : it)));
      } else {
        const msg = e instanceof ApiError ? `Lỗi ${e.status}: ${e.message}` : String(e);
        patchAssistant(aid, () => ({ streaming: false, error: msg }));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
      scrollToBottom();
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  function onComposerKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  async function copyText(text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* clipboard unavailable — silently ignore */
    }
  }

  /** "Tạo lại": re-ask the same question as a fresh turn (the gateway has no in-place replay
   * endpoint to mutate a past turn — this appends a new user+assistant pair instead). */
  function regenerateFrom(assistantId: string) {
    const idx = items.findIndex((it) => it.id === assistantId);
    for (let i = idx - 1; i >= 0; i--) {
      const it = items[i];
      if (it.kind === "user") {
        send(it.text);
        return;
      }
    }
  }

  const railFilter = (name: string) => !railSearch.trim() || name.toLowerCase().includes(railSearch.trim().toLowerCase());

  return (
    <div className="chat-screen">
      <div className="chat-topbar">
        <h3>Trợ lý ảo an toàn thông tin</h3>
        <StatusChips health={health} />
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="iasbtn ghost" style={{ padding: "7px 11px" }} aria-label="Lịch sử">
            <History size={15} strokeWidth={1.5} />
          </button>
        </div>
      </div>

      <div className={"chat-body" + (railCollapsed ? " rail-collapsed" : "")}>
        {/* ── project → session rail ── */}
        <aside className="rail">
          <div className="rail-head">
            {!railCollapsed && (
              <div className="rail-search">
                <Search size={12} strokeWidth={1.5} />
                <input className="iasin" placeholder="Tìm phiên…" value={railSearch} onChange={(e) => setRailSearch(e.target.value)} />
              </div>
            )}
            <button className="iasbtn ghost rail-collapse-btn" onClick={() => setRailCollapsed((v) => !v)} aria-label="Thu gọn rail">
              {railCollapsed ? <ChevronRight size={13} strokeWidth={1.5} /> : <ChevronLeft size={13} strokeWidth={1.5} />}
            </button>
          </div>

          {!railCollapsed ? (
            <div className="rail-list">
              {creatingProject ? (
                <div className="rail-new-row">
                  <input
                    className="iasin"
                    autoFocus
                    placeholder="Tên dự án…"
                    value={newProjectName}
                    onChange={(e) => setNewProjectName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") submitNewProject();
                      if (e.key === "Escape") {
                        setCreatingProject(false);
                        setNewProjectName("");
                      }
                    }}
                    onBlur={submitNewProject}
                  />
                </div>
              ) : (
                <button className="iasbtn rail-new" onClick={() => setCreatingProject(true)}>
                  <Plus size={14} strokeWidth={1.5} />
                  Dự án mới
                </button>
              )}

              {projects.length === 0 && <div className="rail-empty">Chưa có dự án nào.</div>}

              {projects.map((p) => {
                const sessions = (sessionsByProject[p.project_id] || []).filter((s) => railFilter(s.title || "Phiên chưa đặt tên"));
                const open = openProjectId === p.project_id;
                return (
                  <div key={p.project_id}>
                    <div className="rail-project" onClick={() => toggleProject(p.project_id)}>
                      {open ? <ChevronDown size={13} strokeWidth={1.5} color="var(--teal)" /> : <ChevronRight size={13} strokeWidth={1.5} />}
                      {open ? <FolderOpen size={14} strokeWidth={1.5} /> : <Folder size={14} strokeWidth={1.5} />}
                      {renamingProjectId === p.project_id ? (
                        <input
                          className="iasin"
                          style={{ padding: "3px 6px", fontSize: 12.5, minHeight: 0 }}
                          autoFocus
                          value={renameValue}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => setRenameValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") submitRename(p.project_id);
                            if (e.key === "Escape") setRenamingProjectId(null);
                          }}
                          onBlur={() => submitRename(p.project_id)}
                        />
                      ) : (
                        <span className="name">{p.name}</span>
                      )}
                      <button
                        type="button"
                        className="icon-btn"
                        aria-label="Đổi tên dự án"
                        onClick={(e) => {
                          e.stopPropagation();
                          setRenamingProjectId(p.project_id);
                          setRenameValue(p.name);
                        }}
                      >
                        <Pencil size={12} strokeWidth={1.5} />
                      </button>
                      <button
                        type="button"
                        className="icon-btn"
                        aria-label="Phiên mới"
                        onClick={(e) => {
                          e.stopPropagation();
                          newSessionIn(p.project_id);
                        }}
                      >
                        <Plus size={13} strokeWidth={1.5} />
                      </button>
                      <button
                        type="button"
                        className="icon-btn danger"
                        aria-label="Xoá dự án"
                        title="Xoá dự án"
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteProj(p.project_id, p.name);
                        }}
                      >
                        <Trash2 size={12} strokeWidth={1.5} />
                      </button>
                    </div>
                    {open && (
                      <div className="rail-sessions">
                        {sessions.length === 0 && <div className="rail-empty">Chưa có phiên nào.</div>}
                        {sessions.map((s) => (
                          <div
                            key={s.session_id}
                            className={"rail-session" + (selected?.sid === s.session_id ? " on" : "")}
                            onClick={() => selectSession(p.project_id, s.session_id)}
                          >
                            <MessageSquare size={12} strokeWidth={1.5} />
                            <span className="name">{s.title || "Phiên chưa đặt tên"}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="rail-collapsed-list">
              {(openProjectId ? sessionsByProject[openProjectId] || [] : []).map((s) => (
                <div
                  key={s.session_id}
                  title={s.title || "Phiên chưa đặt tên"}
                  className={"rail-collapsed-item" + (selected?.sid === s.session_id ? " on" : "")}
                  onClick={() => openProjectId && selectSession(openProjectId, s.session_id)}
                >
                  <MessageSquare size={14} strokeWidth={1.5} />
                </div>
              ))}
              <div title="Dự án mới" className="rail-collapsed-item rail-collapsed-new" onClick={() => setRailCollapsed(false)}>
                <Plus size={14} strokeWidth={1.5} />
              </div>
            </div>
          )}
        </aside>

        {/* ── thread + composer ── */}
        <div className="thread-wrap">
          {!selected ? (
            <div className="thread-empty-wrap">
              <Blueprint className="thread-empty">
                <div className="icon">
                  <MessageSquarePlus size={22} strokeWidth={1.5} />
                </div>
                <h4>Chưa có phiên nào được mở</h4>
                <p>Chọn một phiên ở rail trái, hoặc tạo phiên mới để bắt đầu hội thoại.</p>
                <div className="actions">
                  <button className="iasbtn" onClick={() => quickStartSession()}>
                    <Plus size={14} strokeWidth={1.5} />
                    Tạo phiên
                  </button>
                  <button className="iasbtn ghost" onClick={() => quickStartSession(SUGGESTIONS[0])}>
                    <Sparkles size={14} strokeWidth={1.5} />
                    Hỏi mẫu
                  </button>
                </div>
              </Blueprint>
            </div>
          ) : (
            <>
              <div className="thread" ref={threadRef}>
                <div className="thread-inner">
                  {items.length === 0 && <div className="rail-empty">Chưa có tin nhắn. Hỏi trợ lý điều gì đó về CVE, cấu hình hoặc log.</div>}
                  {items.map((it) => {
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
                          <span onClick={() => copyText(it.text)}>
                            <Copy size={13} strokeWidth={1.5} />
                            Sao chép
                          </span>
                          <span onClick={() => regenerateFrom(it.id)}>
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
                  })}
                </div>
              </div>

              <div className="composer-wrap">
                <div className="composer-inner">
                  <div className="composer-suggest">
                    {SUGGESTIONS.map((s, i) => (
                      <span key={s} className="chip click" onClick={() => setInput(s)}>
                        {i === 0 ? <Sparkles size={12} strokeWidth={1.5} /> : i === 2 ? <Shield size={12} strokeWidth={1.5} /> : <Search size={12} strokeWidth={1.5} />}
                        {s}
                      </span>
                    ))}
                  </div>
                  <div className="composer-box">
                    <textarea
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={onComposerKey}
                      placeholder="Nhập nội dung…"
                      disabled={busy}
                    />
                    <div className="composer-actions">
                      <button className="iasbtn ghost" style={{ padding: "9px 11px" }} aria-label="Đính kèm" disabled>
                        <Paperclip size={16} strokeWidth={1.5} />
                      </button>
                      {busy ? (
                        <button className="iasbtn ghost" style={{ padding: "9px 13px", fontSize: 13 }} onClick={stop}>
                          <Square size={12} strokeWidth={1.5} fill="currentColor" />
                          Dừng
                        </button>
                      ) : (
                        <button className="iasbtn" style={{ padding: "9px 22px" }} onClick={() => send()} disabled={!input.trim()}>
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
            </>
          )}
        </div>
      </div>
    </div>
  );
}
