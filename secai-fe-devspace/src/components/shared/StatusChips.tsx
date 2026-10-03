import { Cpu, Database, FileSearch } from "lucide-react";
import type { HealthStatus } from "../../types";

function Dot({ ok }: { ok: boolean | undefined }) {
  return (
    <span
      style={{
        width: 6,
        height: 6,
        borderRadius: "50%",
        flex: "none",
        background: ok ? "var(--ok)" : "var(--danger)",
      }}
    />
  );
}

/** `/health` → llm/qwen · redis/session_backend · searchsploit chips (REDESIGN-BRIEF §6.8).
 * Renders whatever the real `/health` payload carries — no invented model/RAG names. */
export default function StatusChips({ health }: { health: HealthStatus | null }) {
  if (!health) {
    return (
      <span className="chip" style={{ cursor: "default", gap: 6, color: "var(--muted2)" }}>
        <Dot ok={false} />
        health không phản hồi
      </span>
    );
  }
  return (
    <span className="chip" style={{ cursor: "default", gap: 12 }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <Dot ok={health.llm ?? health.qwen} />
        <Cpu size={11} strokeWidth={1.5} />
        LLM · {health.backend || "qwen"}
      </span>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <Dot ok={health.redis === "connected" || health.redis === "ok"} />
        <Database size={11} strokeWidth={1.5} />
        {health.session_backend || "session"}
      </span>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
        <Dot ok={health.searchsploit} />
        <FileSearch size={11} strokeWidth={1.5} />
        searchsploit
      </span>
    </span>
  );
}
