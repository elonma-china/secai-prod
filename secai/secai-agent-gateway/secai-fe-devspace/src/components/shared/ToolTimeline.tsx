import { useState } from "react";
import { Check, ChevronDown, ChevronUp, Loader, Wrench, X } from "lucide-react";
import type { ToolCall } from "../../lib/sse";

/** Collapsible "Hoạt động công cụ · N bước · Ns" list — renders merged tool_call/tool_result
 * rows (see api.ts's chatStream handlers, which upsert tool_result onto the matching
 * tool_call by `id`). Defaults open while streaming reads better; collapse to keep the
 * answer clean once it's settled (matches `output_hygiene` on the backend). */
export default function ToolTimeline({ tools, defaultOpen = true }: { tools: ToolCall[]; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  if (!tools.length) return null;

  const totalMs = tools.reduce((s, t) => s + (t.duration_ms || 0), 0);
  const seconds = (totalMs / 1000).toFixed(1);

  return (
    <div className="iascard timeline-card">
      <div
        className="iascard-head"
        role="button"
        tabIndex={0}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((v) => !v);
          }
        }}
      >
        <Wrench size={14} strokeWidth={1.5} color="var(--teal)" />
        <span className="label">Hoạt động công cụ</span>
        <span className="chip" style={{ cursor: "default", padding: "2px 9px", fontSize: 11 }}>
          {tools.length} bước · {seconds}s
        </span>
        {open ? (
          <ChevronUp size={15} strokeWidth={1.5} style={{ marginLeft: "auto", color: "var(--muted)" }} />
        ) : (
          <ChevronDown size={15} strokeWidth={1.5} style={{ marginLeft: "auto", color: "var(--muted)" }} />
        )}
      </div>
      {open && (
        <div className="timeline-rows">
          {tools.map((t) => (
            <div key={t.id} className={"timeline-row" + (t.is_error ? " err" : "")}>
              {t.result === undefined ? (
                <Loader size={13} strokeWidth={1.5} className="ias-spin" color="var(--teal)" />
              ) : t.is_error ? (
                <X size={13} strokeWidth={1.5} color="var(--danger)" />
              ) : (
                <Check size={13} strokeWidth={1.5} color="var(--ok)" />
              )}
              <span className="mono tname">{t.name}</span>
              <span className="tsum">
                {t.summary || (t.result ? t.result.slice(0, 140) : "đang tra…")}
                {t.duration_ms != null ? ` · ${t.duration_ms}ms` : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
