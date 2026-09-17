// Contract khớp src/secai_gateway/api/{routes,sse,schemas,projects}.py

/** The 5 functional "Nền tảng AI" screens — the only routable content in the app. */
export type AiScreenId = "chat" | "translate" | "exploit" | "report" | "models";

// ── Project / Session (api/projects.py) ─────────────────────────────────────────────

export interface Project {
  project_id: string;
  name: string;
  owner: string;
  created_at: string; // ISO-8601
  updated_at: string; // ISO-8601
}

/** Row shape from `GET /projects/{pid}/sessions`. */
export interface SessionSummary {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

/** Row shape from `GET /sessions/{sid}` (full meta, not just the summary fields above). */
export interface SessionDetail {
  session_id: string;
  meta: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** linch `block_to_dict` shapes — see `GET /sessions/{sid}/messages`. */
export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input?: unknown }
  | { type: "tool_result"; tool_use_id: string; content: unknown; is_error?: boolean }
  | { type: "thinking"; thinking: string; signature?: string }
  | { type: "redacted_thinking"; data: string }
  | { type: "image"; source: unknown };

export interface SessionMessageRow {
  seq: number;
  appended_at: string;
  message: {
    role: "user" | "assistant" | "system" | string;
    content: ContentBlock[];
    provider_metadata?: unknown;
  };
}

// ── Health (`GET /health`) ───────────────────────────────────────────────────────────

export interface HealthStatus {
  status?: string;
  message?: string;
  backend?: string;
  redis?: string;
  session_backend?: string;
  postgres?: string;
  llm?: boolean;
  qwen?: boolean;
  searchsploit?: boolean;
  exploitdb_commit?: string | null;
  input_sanitize?: Record<string, unknown>;
  [k: string]: unknown;
}

// ── Streaming primitives — re-exported here for convenience; the real definitions (and the
// SSE frame parser) live in lib/sse.ts, which owns the actual gateway wire contract. ──────

export type { ApiError, FinalPayload, PhaseEvent, StreamHandlers, ToolCall } from "./lib/sse";
