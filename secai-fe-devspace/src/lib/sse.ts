/**
 * SSE frame parser + typed dispatch for the gateway's streaming endpoints
 * (`/sessions/{sid}/chat/stream`, `/ai/translate/stream`, `/ai/exploit/stream`,
 * `/ai/generate-report/stream`, `/ai/generate-statistical-report/stream`).
 *
 * Mirrors `src/secai_gateway/api/sse.py::event_to_sse` exactly — frame shapes below are the
 * REAL wire contract read from that file, not a guess:
 *   - `token`        {delta}
 *   - `tool_call`     {id, name, input, summary}                    (ToolCallStartEvent)
 *   - `tool_result`   {id, name, is_error, duration_ms, result}      (ToolCallEndEvent)
 *   - `phase`         {kind, title, call_key, occurrence, subagent_type,
 *                       result_text, structured_output, structured_error}  (WorkflowEvent)
 *   - `regenerate`    {hook, reason} — client MUST clear the in-flight assistant text buffer
 *   - `section1`      {body, preview} — PoC preview pushed early by /ai/exploit/stream
 *   - `error`         {error}
 *   - `final`         {content, answer, message, metadata: {run_id, profile, tool_registry}}
 *   - `data: [DONE]`  — end of stream (not JSON — must NOT be parsed/dispatched as a frame)
 *
 * `/ai/exploit-v2/stream` (poc/exploit_v2.py — different shape, own handlers below):
 *   - `step`          {index, name, status: "running"|"done"|"error", detail, verdict}
 *   - `log`           {stream, line}                      (shares `onLog` with the above)
 *   - `question`      {run_id, question_id, text}          — agent paused, needs operator input
 *   - `final`         {ok, reason, code, run_instructions, run_id}  (DIFFERENT shape from the
 *                       FinalPayload above — see `ExploitV2FinalPayload`)
 *
 * `event: <name>\ndata: <json>\n\n` framing, terminated by a literal `data: [DONE]` line.
 */

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown, readonly retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** Merged shape a UI can accumulate a `tool_call` + its later `tool_result` into (same `id`). */
export interface ToolCall {
  id: string;
  name: string;
  input?: unknown;
  summary?: string;
  result?: string;
  is_error?: boolean;
  duration_ms?: number;
}

/** Deterministic WorkflowEvent phase (bughunt/exploit-advance Recon→Hunt→Validate→…). */
export interface PhaseEvent {
  kind?: string;
  title?: string;
  call_key?: string;
  occurrence?: number;
  subagent_type?: string;
  result_text?: string | null;
  structured_output?: Record<string, unknown> | null;
  structured_error?: string | null;
}

/** `/ai/exploit-v2/stream` step event — terminal-log line, one per tool call lifecycle. */
export interface StepEvent {
  index?: number;
  name?: string;
  status?: "running" | "done" | "error";
  detail?: string;
  verdict?: string | null;
}

/** `/ai/exploit-v2/stream` question event — agent paused, needs operator input. Answer via
 * `POST /ai/exploit-v2/reply {run_id, question_id, answer}` (see api.ts `exploitV2Reply`). */
export interface QuestionEvent {
  run_id?: string;
  question_id?: string;
  text?: string;
}

/** `/ai/exploit-v2/stream` final event — DIFFERENT shape from `FinalPayload` above (that one
 * is for /ai/exploit, /ai/translate, etc.). `ok=false` means FAILED, not a stream error —
 * `reason` is always human-readable (target thiếu gì / cần gì / không xác định được...). */
export interface ExploitV2FinalPayload {
  ok: boolean;
  reason: string | null;
  code: string | null;
  run_instructions: string | null;
  run_id: string | null;
}

export interface FinalPayload {
  content?: string;
  answer?: string;
  message?: string;
  metadata?: { run_id?: string | null; profile?: string; tool_registry?: string[]; [k: string]: unknown };
  [k: string]: unknown;
}

export interface StreamHandlers {
  onToken?: (delta: string) => void;
  onToolCall?: (t: ToolCall) => void;
  onToolResult?: (t: ToolCall) => void;
  onPhase?: (p: PhaseEvent) => void;
  /** A hook forced the model to redo its answer — clear the assistant text buffer NOW; the
   * eventual `final` event is always the corrected, authoritative text regardless. */
  onRegenerate?: (r: { hook?: string; reason?: string }) => void;
  /** exploit-only: PoC evidence pushed early, in parallel with the model still writing the
   * guide (§2+). `preview=true` means `final` may still replace/correct it. */
  onSection1?: (body: string, preview: boolean) => void;
  onLog?: (p: { stream?: string; line?: string; run_id?: string }) => void;
  onError?: (msg: string) => void;
  onErrorPayload?: (payload: Record<string, unknown>) => void;
  onFinal?: (payload: FinalPayload) => void;
  /** exploit-v2 only. */
  onStep?: (s: StepEvent) => void;
  onQuestion?: (q: QuestionEvent) => void;
  onExploitV2Final?: (payload: ExploitV2FinalPayload) => void;
}

function dispatch(ev: string, payload: Record<string, unknown>, h: StreamHandlers): void {
  switch (ev) {
    case "token":
      h.onToken?.(String(payload.delta ?? payload.content ?? payload.text ?? ""));
      break;
    case "tool_call":
      h.onToolCall?.(payload as unknown as ToolCall);
      break;
    case "tool_result":
      h.onToolResult?.(payload as unknown as ToolCall);
      break;
    case "phase":
    case "agent_start":
    case "agent_end":
    case "agent_replayed":
      h.onPhase?.({ ...(payload as object), kind: (payload.kind as string) || ev } as PhaseEvent);
      break;
    case "regenerate":
      h.onRegenerate?.(payload as { hook?: string; reason?: string });
      break;
    case "section1":
      h.onSection1?.(String(payload.body ?? payload.content ?? ""), Boolean(payload.preview));
      break;
    case "log":
      h.onLog?.(payload as { stream?: string; line?: string; run_id?: string });
      break;
    case "step":
      h.onStep?.(payload as StepEvent);
      break;
    case "question":
      h.onQuestion?.(payload as QuestionEvent);
      break;
    case "error":
      h.onErrorPayload?.(payload);
      h.onError?.(String(payload.error ?? payload.message ?? "stream error"));
      break;
    case "final":
      h.onFinal?.(payload as FinalPayload);
      h.onExploitV2Final?.(payload as unknown as ExploitV2FinalPayload);
      break;
    default:
      // Unknown/legacy event carrying token-shaped content — still surface it as a token
      // rather than silently dropping it.
      if (payload.content || payload.delta) {
        h.onToken?.(String(payload.delta ?? payload.content ?? ""));
      }
  }
}

/**
 * POST `url` with `body`, stream the SSE response into `handlers`.
 *
 * Throws `ApiError` on a non-2xx response — the gateway validates BEFORE it starts streaming
 * (e.g. the secret/PII input gate returns a plain `422` here, never a mid-stream `error`
 * frame; an unknown/foreign session returns `404`/`403`), so callers can catch this the same
 * way they'd catch a JSON POST failure.
 */
export async function streamRequest(
  url: string,
  body: Record<string, unknown>,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    let detail = res.statusText;
    let body: unknown;
    try {
      const j = await res.json();
      body = j;
      const d = j?.detail;
      detail = (typeof d === "string" ? d : d?.message) || j?.error?.message || j?.error || detail;
    } catch {
      /* ignore — non-JSON error body */
    }
    const retryAfter = Number(res.headers.get("Retry-After"));
    throw new ApiError(res.status, detail, body, Number.isInteger(retryAfter) && retryAfter >= 1 ? retryAfter : null);
  }
  if (!res.body) {
    throw new ApiError(res.status, "empty SSE body");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let eventName = "message";
  let dataLines: string[] = [];

  const flush = () => {
    if (!dataLines.length) {
      eventName = "message";
      return;
    }
    const raw = dataLines.join("\n");
    dataLines = [];
    const ev = eventName;
    eventName = "message";
    if (raw.trim() === "[DONE]") return; // sentinel, not a frame — do not dispatch
    let payload: Record<string, unknown> = {};
    try {
      payload = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
    } catch {
      payload = { content: raw };
    }
    dispatch(ev, payload, handlers);
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n")) >= 0) {
      let line = buf.slice(0, idx);
      buf = buf.slice(idx + 1);
      if (line.endsWith("\r")) line = line.slice(0, -1);
      if (line === "") {
        flush();
        continue;
      }
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
  }
  flush();
}
