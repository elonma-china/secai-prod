import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  ApiError,
  chatStream,
  createProject,
  createSession,
  createStandaloneSession,
  deleteProject,
  deleteSession,
  getSessionMessages,
  listProjects,
  listUserSessions,
  renameProject,
  updateSession,
} from "../api";
import type { ChatView, HealthStatus, Project, SessionSummary } from "../types";
import StatusChips from "../components/shared/StatusChips";
import ChatSidebar from "./chat/ChatSidebar";
import ChatThread from "./chat/ChatThread";
import { foldPhase, rowsToItems, upsertTool, type ThreadItem } from "./chat/threadItems";

import { getReviewIdentity, setLiveReviewEnabled, useReviewIdentity } from "../reviewIdentity";
import LiveReviewWidget, { type LiveReviewTurn } from "./review/LiveReviewWidget";

type Editing = { kind: "project" | "session"; id: string; value: string } | null;

function byActivityDesc(a: SessionSummary, b: SessionSummary): number {
  const av = a.last_activity ?? a.created_at;
  const bv = b.last_activity ?? b.created_at;
  return av < bv ? 1 : av > bv ? -1 : 0;
}

// ── "which chat was open" persistence (survives screen switches + reloads) ──────────
//
// App.tsx conditionally renders this screen (`{screen === "chat" && <ChatbotScreen/>}`), so
// navigating away and back fully unmounts/remounts it and wipes all local state. Without this,
// every trip back to the chatbot lands on a blank draft even though the session is untouched
// server-side. Keyed BY userId so switching the active user_id never leaks one user's open
// session to another — the mount effect below only ever reads the record for the CURRENT
// userId, and a stale/foreign/deleted sid falls back to a clean draft instead of erroring.
const OPEN_VIEW_KEY_PREFIX = "chat_open_view:";

function readSavedView(userId: string): ChatView | null {
  try {
    const raw = localStorage.getItem(OPEN_VIEW_KEY_PREFIX + userId);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ChatView> | null;
    if (!parsed || typeof parsed !== "object") return null;
    const projectId = typeof parsed.projectId === "string" ? parsed.projectId : null;
    if (parsed.kind === "session" && typeof parsed.sid === "string" && parsed.sid) {
      return { kind: "session", sid: parsed.sid, projectId };
    }
    if (parsed.kind === "draft") {
      return { kind: "draft", projectId };
    }
    return null;
  } catch {
    return null;
  }
}

function writeSavedView(userId: string, view: ChatView) {
  try {
    localStorage.setItem(OPEN_VIEW_KEY_PREFIX + userId, JSON.stringify(view));
  } catch {
    /* best-effort — private mode / quota errors are harmless to ignore here */
  }
}

export default function ChatbotScreen({ userId, health }: { userId: string; health: HealthStatus | null }) {
  const reviewIdentity = useReviewIdentity();
  const [liveReviewTurn, setLiveReviewTurn] = useState<LiveReviewTurn | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [view, setView] = useState<ChatView>({ kind: "draft", projectId: null });
  const [items, setItems] = useState<ThreadItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Editing>(null);
  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [search, setSearch] = useState("");
  const [railCollapsed, setRailCollapsed] = useState(false);

  const sendingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const loadSeqRef = useRef(0);
  const threadRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Reload everything whenever the active user_id changes (owner-scoped listing) — this also
  // fires on every mount (e.g. switching back to the chatbot screen, which fully remounts this
  // component). Restore whatever session was last open FOR THIS userId, if any; a different user
  // (or no saved record, or a since-deleted session) always lands on a clean draft — see
  // restoreSession's own fallback and readSavedView's userId-scoped key.
  useEffect(() => {
    let alive = true;
    setLiveReviewTurn(null);
    setProjects([]);
    setSessions([]);
    setExpanded(new Set());
    setLoadError(null);
    setSearch("");
    setEditing(null);
    setCreatingProject(false);
    setNewProjectName("");
    setRailCollapsed(false);
    abortRef.current?.abort();
    sendingRef.current = false;
    setBusy(false);

    const saved = readSavedView(userId);
    if (saved && saved.kind === "session") {
      void restoreSession(saved.sid, saved.projectId);
    } else {
      setView(saved ?? { kind: "draft", projectId: null });
      setItems([]);
    }

    Promise.all([
      listProjects(userId).catch(() => [] as Project[]),
      listUserSessions(userId, "all")
        .then((rows) => ({ ok: true as const, rows }))
        .catch(() => ({ ok: false as const, rows: [] as SessionSummary[] })),
    ]).then(([ps, sRes]) => {
      if (!alive) return;
      setProjects(ps);
      if (sRes.ok) setSessions(sRes.rows);
      else setLoadError("Không tải được danh sách cuộc trò chuyện.");
    });
    return () => {
      alive = false;
    };
  }, [userId]);

  /** Set the view AND persist it as "what's open" for this userId, synchronously, at the same
   * call site that decided the navigation. Deliberately NOT a `useEffect([view])` catch-all: under
   * React.StrictMode's dev double-invoke (mount → cleanup → mount), an effect keyed on `view`
   * fires during the FIRST mount pass with the still-default `draft` state — before
   * `restoreSession`'s async `setView({session})` has committed — and would write that stale
   * draft over the just-restored session, which the second StrictMode mount would then read back
   * and get stuck on. Writing at the point of intent instead ties the localStorage write to the
   * same synchronous call as the state update, so no render/effect ordering can race it. */
  function openView(v: ChatView) {
    setLiveReviewTurn(null);
    setView(v);
    writeSavedView(userId, v);
  }

  const standaloneSessions = useMemo(() => sessions.filter((s) => !s.project_id).sort(byActivityDesc), [sessions]);
  const sessionsByProject = useMemo(() => {
    const map: Record<string, SessionSummary[]> = {};
    for (const s of sessions) {
      if (!s.project_id) continue;
      (map[s.project_id] ??= []).push(s);
    }
    for (const k of Object.keys(map)) map[k].sort(byActivityDesc);
    return map;
  }, [sessions]);
  const recentSessions = useMemo(() => [...sessions].sort(byActivityDesc).slice(0, 8), [sessions]);

  function scrollToBottom() {
    requestAnimationFrame(() => threadRef.current?.scrollTo(0, threadRef.current.scrollHeight));
  }

  function patchAssistant(id: string, fn: (m: Extract<ThreadItem, { kind: "assistant" }>) => Partial<Extract<ThreadItem, { kind: "assistant" }>>) {
    setItems((prev) => prev.map((it) => (it.id === id && it.kind === "assistant" ? { ...it, ...fn(it) } : it)));
  }

  function focusComposer() {
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  function toggleExpand(pid: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid);
      else next.add(pid);
      return next;
    });
  }

  function newChat() {
    loadSeqRef.current += 1; // invalidate any in-flight selectSession/restoreSession load
    abortRef.current?.abort();
    openView({ kind: "draft", projectId: null });
    setItems([]);
    setInput("");
    focusComposer();
  }

  function newChatInProject(pid: string) {
    loadSeqRef.current += 1; // invalidate any in-flight selectSession/restoreSession load
    abortRef.current?.abort();
    setExpanded((prev) => new Set(prev).add(pid));
    openView({ kind: "draft", projectId: pid });
    setItems([]);
    setInput("");
    focusComposer();
  }

  async function selectSession(sid: string, pid: string | null) {
    abortRef.current?.abort();
    openView({ kind: "session", sid, projectId: pid });
    setInput("");
    const seq = ++loadSeqRef.current;
    setItems([]);
    try {
      const res = await getSessionMessages(sid, userId);
      if (loadSeqRef.current !== seq) return;
      setItems(rowsToItems(res.messages));
      scrollToBottom();
    } catch (e) {
      if (loadSeqRef.current !== seq) return;
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

  /** Re-open a session restored from `chat_open_view:<userId>` on mount (screen-switch/reload).
   * Unlike `selectSession` (a live user click on a session that's visibly still in the sidebar),
   * a restored sid may no longer exist or be readable (deleted, revoked, backend hiccup) — fail
   * closed to a clean draft rather than surfacing a load-error banner for a chat the user never
   * explicitly opened this visit. This does a real network round-trip, so the user CAN interact
   * (new chat / send / pick another session) before it resolves — `loadSeqRef` is what protects
   * against a late resolution clobbering whatever the user did in the meantime (see newChat,
   * newChatInProject and send, which all bump it). */
  async function restoreSession(sid: string, pid: string | null) {
    openView({ kind: "session", sid, projectId: pid });
    const seq = ++loadSeqRef.current;
    setItems([]);
    try {
      const res = await getSessionMessages(sid, userId);
      if (loadSeqRef.current !== seq) return;
      setItems(rowsToItems(res.messages));
      scrollToBottom();
    } catch {
      if (loadSeqRef.current !== seq) return;
      // sid no longer exists/readable — clean the dead pointer out of localStorage too, else the
      // next mount would try (and fail) to restore it again.
      openView({ kind: "draft", projectId: null });
      setItems([]);
    }
  }

  function startCreateProject() {
    setCreatingProject(true);
    setNewProjectName("");
  }
  function cancelCreateProject() {
    setCreatingProject(false);
    setNewProjectName("");
  }
  async function submitCreateProject() {
    const name = newProjectName.trim();
    setCreatingProject(false);
    setNewProjectName("");
    if (!name) return;
    try {
      const proj = await createProject(name, userId);
      setProjects((prev) => [proj, ...prev]);
      setExpanded((prev) => new Set(prev).add(proj.project_id));
      abortRef.current?.abort();
      openView({ kind: "draft", projectId: proj.project_id });
      setItems([]);
    } catch (e) {
      window.alert(`Không tạo được dự án: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  function startRenameProject(pid: string, name: string) {
    setEditing({ kind: "project", id: pid, value: name });
  }
  function startRenameSession(sid: string, title: string) {
    setEditing({ kind: "session", id: sid, value: title });
  }
  function editingValueChange(v: string) {
    setEditing((prev) => (prev ? { ...prev, value: v } : prev));
  }
  function cancelEdit() {
    setEditing(null);
  }
  async function commitEdit() {
    const current = editing;
    if (!current) return;
    const value = current.value.trim();
    setEditing(null);
    if (!value) return;
    if (current.kind === "project") {
      try {
        const updated = await renameProject(current.id, value, userId);
        setProjects((prev) => prev.map((p) => (p.project_id === current.id ? updated : p)));
      } catch (e) {
        window.alert(`Không đổi tên được: ${e instanceof Error ? e.message : String(e)}`);
      }
    } else {
      try {
        const updated = await updateSession(current.id, { title: value }, userId);
        setSessions((prev) => prev.map((s) => (s.session_id === current.id ? updated : s)));
      } catch (e) {
        window.alert(`Không đổi tên được: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
  }

  async function deleteProj(pid: string, name: string) {
    const count = (sessionsByProject[pid] || []).length;
    if (!window.confirm(`Xoá dự án "${name}" và ${count} cuộc trò chuyện bên trong?`)) return;
    try {
      await deleteProject(pid, userId);
      setProjects((prev) => prev.filter((p) => p.project_id !== pid));
      setSessions((prev) => prev.filter((s) => s.project_id !== pid));
      setExpanded((prev) => {
        const next = new Set(prev);
        next.delete(pid);
        return next;
      });
      if (view.projectId === pid) {
        abortRef.current?.abort();
        openView({ kind: "draft", projectId: null });
        setItems([]);
      }
    } catch (e) {
      window.alert(`Không xoá được dự án: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function moveSession(sid: string, projectId: string | null) {
    try {
      const updated = await updateSession(sid, { project_id: projectId }, userId);
      setSessions((prev) => prev.map((s) => (s.session_id === sid ? updated : s)));
      if (view.kind === "session" && view.sid === sid) {
        openView({ kind: "session", sid, projectId: updated.project_id ?? null });
      }
      if (updated.project_id) setExpanded((prev) => new Set(prev).add(updated.project_id as string));
    } catch (e) {
      window.alert(`Không chuyển được cuộc trò chuyện: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function deleteSessionRow(sid: string, title: string) {
    if (!window.confirm(`Xoá cuộc trò chuyện "${title}"? Không thể hoàn tác.`)) return;
    const row = sessions.find((s) => s.session_id === sid);
    try {
      await deleteSession(sid, userId);
      setSessions((prev) => prev.filter((s) => s.session_id !== sid));
      if (view.kind === "session" && view.sid === sid) {
        abortRef.current?.abort();
        openView({ kind: "draft", projectId: row?.project_id ?? null });
        setItems([]);
      }
    } catch (e) {
      window.alert(`Không xoá được cuộc trò chuyện: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  /** A just-created session's create-call succeeded but the stream that was supposed to use it
   * failed before producing anything (422/429/5xx) — undo the creation server-side (best-effort)
   * and drop everything from this attempt except the error/blocked banner (`aid`), restoring the
   * typed text so the user can just hit send again. */
  async function rollbackJustCreated(sid: string, projectId: string | null, aid: string, text: string) {
    try {
      await deleteSession(sid, userId);
    } catch {
      /* best-effort — a leftover empty session is harmless, keeping the UI honest matters more */
    }
    setSessions((prev) => prev.filter((s) => s.session_id !== sid));
    openView({ kind: "draft", projectId });
    setItems((prev) => prev.filter((it) => it.id === aid));
    setInput(text);
  }

  /** LAZY CREATION — clicking "new" never POSTs anything; the session is only created here,
   * on the first message actually sent into a draft. Mirrors ChatGPT: an empty new-chat tab
   * costs nothing server-side until you type.
   *
   * The AbortController is created up front (before the `POST /sessions` create-call, which
   * doesn't itself take a signal) so that switching to another chat while creation is still
   * in flight — via selectSession's `abortRef.current?.abort()` — reliably cancels THIS attempt:
   * we notice `ac.signal.aborted` as soon as the create-call resolves and unwind instead of
   * hijacking the view/thread the user has since navigated to. */
  async function send(overrideText?: string) {
    const text = (overrideText ?? input).trim();
    if (!text || sendingRef.current) return;
    loadSeqRef.current += 1; // invalidate any in-flight selectSession/restoreSession load — a
    // late-resolving getSessionMessages must never clobber the message we're about to append
    sendingRef.current = true;
    setBusy(true);
    setInput("");

    const ac = new AbortController();
    abortRef.current = ac;
    let aborted = false;
    let rolledBack = false;

    try {
      let currentView = view;
      let justCreated = false;
      if (currentView.kind === "draft") {
        let created: SessionSummary;
        try {
          created = currentView.projectId
            ? await createSession(currentView.projectId, userId)
            : await createStandaloneSession(userId);
        } catch (e) {
          setInput(text);
          const msg =
            e instanceof ApiError ? `Không tạo được cuộc trò chuyện (${e.status}): ${e.message}` : `Không tạo được cuộc trò chuyện: ${String(e)}`;
          window.alert(msg);
          return;
        }

        if (ac.signal.aborted) {
          // User switched to another chat while the create-call was in flight — the session
          // exists server-side now but nothing here should touch state anymore; clean it up.
          deleteSession(created.session_id, userId).catch(() => {});
          return;
        }

        justCreated = true;
        const optimisticTitle = text.replace(/\n/g, " ").slice(0, 60);
        const row: SessionSummary = { ...created, title: created.title || optimisticTitle };
        setSessions((prev) => [row, ...prev]);
        currentView = { kind: "session", sid: row.session_id, projectId: row.project_id ?? null };
        openView(currentView);
        if (row.project_id) setExpanded((prev) => new Set(prev).add(row.project_id as string));
      }

      if (ac.signal.aborted) return;

      const sid = currentView.sid;
      const uid = `u-${Date.now()}`;
      const aid = `a-${Date.now()}`;
      setItems((prev) => [
        ...(justCreated ? [] : prev),
        { kind: "user", id: uid, text },
        { kind: "assistant", id: aid, text: "", tools: [], phases: [], streaming: true },
      ]);
      scrollToBottom();

      let publicAnswer = "";
      let completedAnswer: string | null = null;
      let streamFailed = false;
      const reviewContext = (justCreated ? [] : items).flatMap(item => item.kind === "user" || (item.kind === "assistant" && !item.streaming && !item.error) ? [{role: item.kind === "user" ? "user" : "assistant", content: item.text}] : []).filter(message => message.content.trim());
      reviewContext.push({role: "user", content: text});
      setLiveReviewTurn(null);
      try {
        await chatStream(
          sid,
          text,
          userId,
          {
            onToken: (delta) => {
              publicAnswer += delta;
              patchAssistant(aid, (m) => ({ text: m.text + delta, regenerating: false }));
              scrollToBottom();
            },
            // Backend (require_chat_grounding) discarded a fabricated draft and is restarting the
            // answer on purpose — don't just blank the buffer back to the plain empty placeholder
            // (looked like a glitch); flag it so the thread can show a meaningful "đang tra cứu
            // lại…" indicator until the next token/final lands.
            onRegenerate: () => { publicAnswer = ""; patchAssistant(aid, () => ({ text: "", regenerating: true })); },
            onToolCall: (t) => patchAssistant(aid, (m) => ({ tools: upsertTool(m.tools, t) })),
            onToolResult: (t) => patchAssistant(aid, (m) => ({ tools: upsertTool(m.tools, t) })),
            onPhase: (p) => patchAssistant(aid, (m) => ({ phases: foldPhase(m.phases, p) })),
            onError: (msg) => { streamFailed = true; patchAssistant(aid, () => ({ error: msg })); },
            onFinal: (payload) => {
              completedAnswer = String(payload.content ?? payload.answer ?? publicAnswer);
              patchAssistant(aid, (m) => ({
                text: String(payload.content ?? payload.answer ?? m.text),
                streaming: false,
                regenerating: false,
                runId: (payload.metadata?.run_id as string | undefined) ?? undefined,
              }));
            },
          },
          ac.signal,
        );
        if (!ac.signal.aborted && !streamFailed && completedAnswer && getReviewIdentity()?.liveEnabled) setLiveReviewTurn({id: aid, prompt_messages: reviewContext, answer: completedAnswer, source_group: sid});
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          aborted = true;
          patchAssistant(aid, () => ({ streaming: false }));
        } else if (e instanceof ApiError && e.status === 422) {
          setItems((prev) => prev.map((it) => (it.id === aid ? { kind: "blocked422", id: aid, detail: e.message } : it)));
          if (justCreated) {
            await rollbackJustCreated(sid, currentView.projectId, aid, text);
            rolledBack = true;
          }
        } else {
          const msg = e instanceof ApiError ? `Lỗi ${e.status}: ${e.message}` : String(e);
          patchAssistant(aid, () => ({ streaming: false, error: msg }));
          if (e instanceof ApiError && justCreated) {
            await rollbackJustCreated(sid, currentView.projectId, aid, text);
            rolledBack = true;
          }
        }
      }

      if (ac.signal.aborted) return;

      if (!aborted && !rolledBack) {
        if (justCreated) {
          try {
            setSessions(await listUserSessions(userId, "all"));
          } catch {
            /* keep the optimistic row — the server title just won't show up until next reload */
          }
        } else {
          const now = new Date().toISOString();
          setSessions((prev) => prev.map((s) => (s.session_id === sid ? { ...s, last_activity: now } : s)));
        }
      }
    } finally {
      sendingRef.current = false;
      setBusy(false);
      if (abortRef.current === ac) abortRef.current = null;
      scrollToBottom();
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  function onComposerKey(e: KeyboardEvent<HTMLTextAreaElement>) {
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

  function projectNameOf(pid: string | null): string | undefined {
    return pid ? projects.find((p) => p.project_id === pid)?.name : undefined;
  }

  function breadcrumb(): string {
    const pname = projectNameOf(view.projectId);
    if (view.kind === "draft") {
      return pname ? `📁 ${pname} › Cuộc trò chuyện mới` : "Cuộc trò chuyện mới";
    }
    const title = sessions.find((s) => s.session_id === view.sid)?.title || "Cuộc trò chuyện mới";
    return pname ? `📁 ${pname} › ${title}` : `Cuộc trò chuyện › ${title}`;
  }

  return (
    <div className="chat-screen">
      <div className="chat-topbar">
        <h3>{breadcrumb()}</h3>
        <StatusChips health={health} />
        {reviewIdentity && <label className="review-check review-live-toggle"><input type="checkbox" checked={reviewIdentity.liveEnabled} onChange={e => setLiveReviewEnabled(e.target.checked)}/>Nhận lời mời đánh giá A/B</label>}
      </div>

      <div className={"chat-body" + (railCollapsed ? " rail-collapsed" : "")}>
        <ChatSidebar
          collapsed={railCollapsed}
          onToggleCollapsed={() => setRailCollapsed((v) => !v)}
          search={search}
          onSearchChange={setSearch}
          searchInputRef={searchInputRef}
          loadError={loadError}
          projects={projects}
          standaloneSessions={standaloneSessions}
          sessionsByProject={sessionsByProject}
          recentSessions={recentSessions}
          expanded={expanded}
          onToggleExpand={toggleExpand}
          view={view}
          onNewChat={newChat}
          onSelectSession={selectSession}
          creatingProject={creatingProject}
          newProjectName={newProjectName}
          onNewProjectNameChange={setNewProjectName}
          onStartCreateProject={startCreateProject}
          onSubmitCreateProject={submitCreateProject}
          onCancelCreateProject={cancelCreateProject}
          onNewChatInProject={newChatInProject}
          onDeleteProject={deleteProj}
          editing={editing}
          onEditingValueChange={editingValueChange}
          onStartRenameProject={startRenameProject}
          onStartRenameSession={startRenameSession}
          onCommitEdit={commitEdit}
          onCancelEdit={cancelEdit}
          onMoveSession={moveSession}
          onDeleteSession={deleteSessionRow}
        />

        <ChatThread
          view={view}
          projectName={projectNameOf(view.projectId) ?? null}
          items={items}
          input={input}
          busy={busy}
          threadRef={threadRef}
          textareaRef={textareaRef}
          onInputChange={setInput}
          onComposerKey={onComposerKey}
          onSend={() => send()}
          onStop={stop}
          onCopy={copyText}
          onRegenerate={regenerateFrom}
          onSuggestion={setInput}
          reviewWidget={<LiveReviewWidget turn={liveReviewTurn}/>}
        />
      </div>
    </div>
  );
}
