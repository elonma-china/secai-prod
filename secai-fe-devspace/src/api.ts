/**
 * Thin client for the SecAI gateway (HTTP + SSE). Endpoint shapes are read from
 * `secai-agent-gateway/src/secai_gateway/api/{routes,projects,schemas}.py` — every request
 * body accepted by `/ai/*` is the permissive `LegacyRequest` (extra fields allowed), so the
 * translate/exploit/report/statisticalReport functions below take a loose body object rather
 * than a narrow type.
 */

import { ApiError, streamRequest } from "./lib/sse";
import type { StreamHandlers } from "./lib/sse";
import type {
  HealthStatus,
  Project,
  SessionDetail,
  SessionMessageRow,
  SessionSummary,
} from "./types";

export { ApiError };
export type {
  ExploitV2FinalPayload,
  FinalPayload,
  PhaseEvent,
  QuestionEvent,
  StepEvent,
  StreamHandlers,
  ToolCall,
} from "./lib/sse";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") || "";

function qs(params: Record<string, string | undefined>): string {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") usp.set(k, v);
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

async function parseJson(res: Response): Promise<Record<string, unknown>> {
  const text = await res.text();
  let data: Record<string, unknown> = {};
  try {
    data = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const detail = data.detail as unknown;
    const msg =
      (typeof detail === "string" ? detail : (detail as { message?: string } | undefined)?.message) ||
      (data.error as string) ||
      (typeof data.message === "string" ? data.message : "") ||
      res.statusText ||
      "request failed";
    throw new ApiError(res.status, msg, data);
  }
  return data;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  return parseJson(res) as Promise<T>;
}

async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  return parseJson(res) as Promise<T>;
}

async function patchJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  return parseJson(res) as Promise<T>;
}

async function deleteJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE", signal });
  return parseJson(res) as Promise<T>;
}

// ── health ────────────────────────────────────────────────────────────────────────────

export async function health(): Promise<HealthStatus> {
  return getJson<HealthStatus>("/health");
}

// ── projects (owner = user_id) ───────────────────────────────────────────────────────

export async function createProject(name: string, userId: string): Promise<Project> {
  return postJson<Project>("/projects", { name, user_id: userId });
}

export async function listProjects(userId: string): Promise<Project[]> {
  return getJson<Project[]>(`/projects${qs({ user_id: userId })}`);
}

export async function getProject(pid: string, userId: string): Promise<Project> {
  return getJson<Project>(`/projects/${encodeURIComponent(pid)}${qs({ user_id: userId })}`);
}

export async function renameProject(pid: string, name: string, userId: string): Promise<Project> {
  return patchJson<Project>(`/projects/${encodeURIComponent(pid)}`, { name, user_id: userId });
}

export async function deleteProject(pid: string, userId: string): Promise<{ deleted: number }> {
  return deleteJson<{ deleted: number }>(`/projects/${encodeURIComponent(pid)}${qs({ user_id: userId })}`);
}

// ── sessions (scoped under a project, plus flat /sessions/{sid} lookups) ──────────────

/** Fills in whatever an older/partial BE response leaves out — kept for the transition where
 * `POST /projects/{pid}/sessions` had only returned `{session_id, project_id, title}`. */
function normalizeSession(raw: Partial<SessionSummary> & { session_id: string }): SessionSummary {
  const now = new Date().toISOString();
  const created = raw.created_at ?? now;
  const updated = raw.updated_at ?? created;
  return {
    session_id: raw.session_id,
    title: raw.title ?? "",
    created_at: created,
    updated_at: updated,
    project_id: raw.project_id ?? null,
    last_activity: raw.last_activity ?? updated,
  };
}

export async function createSession(pid: string, userId: string, title?: string): Promise<SessionSummary> {
  const res = await postJson<Partial<SessionSummary> & { session_id: string }>(
    `/projects/${encodeURIComponent(pid)}/sessions`,
    { user_id: userId, title },
  );
  return normalizeSession({ ...res, project_id: res.project_id ?? pid });
}

/** `POST /sessions` — standalone session, belongs to no project. */
export async function createStandaloneSession(userId: string, title?: string): Promise<SessionSummary> {
  const res = await postJson<Partial<SessionSummary> & { session_id: string }>("/sessions", { user_id: userId, title });
  return normalizeSession({ ...res, project_id: res.project_id ?? null });
}

/** `GET /sessions?user_id&scope=` — `scope="all"` includes project-owned sessions too (their
 * `project_id` is set); `"standalone"` is only the ones with no project. Sorted by the BE
 * `last_activity desc`, but callers should still re-sort after any local mutation. */
export async function listUserSessions(userId: string, scope: "standalone" | "all" = "all"): Promise<SessionSummary[]> {
  const rows = await getJson<Array<Partial<SessionSummary> & { session_id: string }>>(
    `/sessions${qs({ user_id: userId, scope })}`,
  );
  return rows.map((r) => normalizeSession(r));
}

export async function listSessions(pid: string, userId: string): Promise<SessionSummary[]> {
  const rows = await getJson<Array<Partial<SessionSummary> & { session_id: string }>>(
    `/projects/${encodeURIComponent(pid)}/sessions${qs({ user_id: userId })}`,
  );
  return rows.map((r) => normalizeSession({ ...r, project_id: r.project_id ?? pid }));
}

/** `PATCH /sessions/{sid}` — `project_id` ABSENT from `patch` means "don't move it"; pass
 * `project_id: null` explicitly to move it out of its project, or a project id to move it in. */
export async function updateSession(
  sid: string,
  patch: { title?: string; project_id?: string | null },
  userId: string,
): Promise<SessionSummary> {
  const body: Record<string, unknown> = { user_id: userId };
  if (patch.title !== undefined) body.title = patch.title;
  if ("project_id" in patch) body.project_id = patch.project_id;
  const res = await patchJson<Partial<SessionSummary> & { session_id: string }>(
    `/sessions/${encodeURIComponent(sid)}`,
    body,
  );
  return normalizeSession(res);
}

export async function getSession(sid: string, userId: string): Promise<SessionDetail> {
  return getJson<SessionDetail>(`/sessions/${encodeURIComponent(sid)}${qs({ user_id: userId })}`);
}

export async function deleteSession(sid: string, userId: string): Promise<{ deleted: boolean }> {
  return deleteJson<{ deleted: boolean }>(`/sessions/${encodeURIComponent(sid)}${qs({ user_id: userId })}`);
}

export async function getSessionMessages(sid: string, userId: string): Promise<{ messages: SessionMessageRow[] }> {
  return getJson(`/sessions/${encodeURIComponent(sid)}/messages${qs({ user_id: userId })}`);
}

// ── chat (session-scoped) ───────────────────────────────────────────────────────────

export interface ChatResponse {
  response: string;
  answer: string;
  message: string;
  metadata: Record<string, unknown>;
}

export async function chat(sid: string, message: string, userId: string, signal?: AbortSignal): Promise<ChatResponse> {
  return postJson(`/sessions/${encodeURIComponent(sid)}/chat`, { message, user_id: userId }, signal);
}

export function chatStream(
  sid: string,
  message: string,
  userId: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamRequest(`${BASE}/sessions/${encodeURIComponent(sid)}/chat/stream`, { message, user_id: userId }, handlers, signal);
}

// ── translate / exploit / report / statisticalReport ───────────────────────────────
// All accept the gateway's permissive LegacyRequest shape: {message|question|prompt|content|
// text, language?, data?, target?, cve?, port?, command?, user_id, ...}. Callers (Dev B)
// build whichever subset their screen needs; `user_id` is the only field this client requires.

export async function translate(body: Record<string, unknown> & { user_id: string }, signal?: AbortSignal): Promise<Record<string, unknown>> {
  return postJson("/ai/translate", body, signal);
}

export function translateStream(
  body: Record<string, unknown> & { user_id: string },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamRequest(`${BASE}/ai/translate/stream`, body, handlers, signal);
}

export async function exploit(body: Record<string, unknown> & { user_id: string }, signal?: AbortSignal): Promise<Record<string, unknown>> {
  return postJson("/ai/exploit", body, signal);
}

export function exploitStream(
  body: Record<string, unknown> & { user_id: string },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamRequest(`${BASE}/ai/exploit/stream`, body, handlers, signal);
}

/** `POST /ai/exploit-v2/stream` — body: {cve, target, goal?, extra_requirements?, user_id}.
 * `target` PHẢI đầy đủ host:port — gateway trả `final {ok:false, reason: "..."}` ngay (không
 * chạy pentestcode) nếu thiếu, xem `poc/exploit_v2_prompt.py::validate_full_target`. */
export function exploitV2Stream(
  body: { cve: string; target: string; goal?: string; extra_requirements?: string; user_id?: string },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamRequest(`${BASE}/ai/exploit-v2/stream`, body, handlers, signal);
}

/** `POST /ai/exploit-v2/reply` — trả lời event `question` nhận được TRÊN kết nối stream đang
 * mở; KHÔNG mở stream mới, kết nối gốc sẽ tự tiếp tục nhận step/log sau khi gọi hàm này. */
export async function exploitV2Reply(
  body: { run_id: string; question_id: string; answer: string; user_id?: string },
  signal?: AbortSignal,
): Promise<{ ok: boolean }> {
  return postJson("/ai/exploit-v2/reply", body, signal);
}

export async function report(body: Record<string, unknown> & { user_id: string }, signal?: AbortSignal): Promise<Record<string, unknown>> {
  return postJson("/ai/generate-report", body, signal);
}

export function reportStream(
  body: Record<string, unknown> & { user_id: string },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamRequest(`${BASE}/ai/generate-report/stream`, body, handlers, signal);
}

export async function statisticalReport(body: Record<string, unknown> & { user_id: string }, signal?: AbortSignal): Promise<Record<string, unknown>> {
  return postJson("/ai/generate-statistical-report", body, signal);
}

export function statisticalReportStream(
  body: Record<string, unknown> & { user_id: string },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return streamRequest(`${BASE}/ai/generate-statistical-report/stream`, body, handlers, signal);
}
