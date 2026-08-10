/** Thin client for SecAI gateway HTTP + SSE. */

import type { StreamHandlers, ToolCall } from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") || "";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
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
    const msg =
      (data.error as string) ||
      (data.detail as string) ||
      (typeof data.message === "string" ? data.message : "") ||
      res.statusText ||
      "request failed";
    throw new ApiError(res.status, msg, data);
  }
  return data;
}

export async function health(): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE}/health`);
  return parseJson(res);
}

export async function chat(
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE}/ai/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  return parseJson(res);
}

export async function translate(
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE}/ai/translate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  return parseJson(res);
}

export async function exploit(
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE}/ai/exploit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  return parseJson(res);
}

/** Consume gateway SSE (`event:` / `data:` frames) into StreamHandlers. */
export async function stream(
  path: string,
  body: Record<string, unknown>,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = (j.detail as string) || (j.error as string) || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
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

function dispatch(ev: string, payload: Record<string, unknown>, h: StreamHandlers) {
  switch (ev) {
    case "token":
    case "delta":
      h.onToken?.(String(payload.delta ?? payload.content ?? payload.text ?? ""));
      break;
    case "tool_call":
      h.onToolCall?.(payload as unknown as ToolCall);
      break;
    case "tool_result":
      h.onToolResult?.(payload as unknown as ToolCall);
      break;
    case "regenerate":
      h.onRegenerate?.(payload as { hook?: string; reason?: string });
      break;
    case "section1":
      h.onSection1?.(
        String(payload.body ?? payload.content ?? ""),
        Boolean(payload.preview),
      );
      break;
    case "phase":
    case "agent_start":
    case "agent_end":
    case "agent_replayed":
      h.onPhase?.({ ...(payload as object), kind: (payload.kind as string) || ev } as never);
      break;
    case "log":
      h.onLog?.(payload as { stream?: string; line?: string; run_id?: string });
      break;
    case "error":
      h.onError?.(String(payload.error ?? payload.message ?? "stream error"));
      break;
    case "final":
      h.onFinal?.(payload);
      break;
    default:
      if (payload.content || payload.delta) {
        h.onToken?.(String(payload.delta ?? payload.content ?? ""));
      }
      break;
  }
}
