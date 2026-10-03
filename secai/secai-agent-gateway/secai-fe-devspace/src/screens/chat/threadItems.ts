import type { PhaseEvent, ToolCall } from "../../lib/sse";
import type { PhaseItem } from "../../components/shared/PhaseChips";
import type { ContentBlock, SessionMessageRow } from "../../types";

export type ThreadItem =
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
      /** Set while waiting out a backend-triggered SSE `regenerate` (require_chat_grounding
       * discarding a fabricated draft and restarting the answer) — lets the thread show a
       * meaningful "đang tra cứu lại…" indicator instead of silently blanking back to the
       * generic "đang soạn…" placeholder. Cleared on the next token/final. */
      regenerating?: boolean;
    }
  | { kind: "blocked422"; id: string; detail: string };

export const SUGGESTIONS = ["Tóm tắt CVE mới nhất", "Phân tích log này", "Khuyến nghị vá"];

export function upsertTool(tools: ToolCall[], t: ToolCall): ToolCall[] {
  const i = tools.findIndex((x) => x.id === t.id);
  if (i < 0) return [...tools, t];
  const next = [...tools];
  next[i] = { ...next[i], ...t };
  return next;
}

/** Fold an incoming `phase` SSE event into the running chip list: mark whatever was active
 * as done, add/re-activate the incoming one. */
export function foldPhase(phases: PhaseItem[], p: PhaseEvent): PhaseItem[] {
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
export function detectCve(text: string): { id: string; cvss?: string; severity?: string; cwe: string[]; kev: boolean } | null {
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
export function splitCveTitle(text: string, cveId: string): { title: string; body: string } {
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

export function rowsToItems(rows: SessionMessageRow[]): ThreadItem[] {
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

/** Accent-insensitive fold for the rail search box: NFD-decompose, strip combining marks,
 * then fold đ/Đ (which NFD does NOT decompose) to d/D. */
export function accentFold(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[đĐ]/g, "d")
    .toLowerCase();
}
