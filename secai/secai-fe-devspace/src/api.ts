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
export type { FinalPayload, PhaseEvent, StreamHandlers, ToolCall } from "./lib/sse";

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

export async function createSession(
  pid: string,
  userId: string,
  title?: string,
): Promise<{ session_id: string; project_id: string; title: string }> {
  return postJson(`/projects/${encodeURIComponent(pid)}/sessions`, { user_id: userId, title });
}

export async function listSessions(pid: string, userId: string): Promise<SessionSummary[]> {
  return getJson<SessionSummary[]>(`/projects/${encodeURIComponent(pid)}/sessions${qs({ user_id: userId })}`);
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

// ── admin ────────────────────────────────────────────────────────────────────────────

export async function refreshModels(apiKey: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE}/admin/refresh-models`, {
    method: "POST",
    headers: { "X-API-Key": apiKey },
  });
  return parseJson(res);
}
