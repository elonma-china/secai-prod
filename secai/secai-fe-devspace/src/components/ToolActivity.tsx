import type { ToolCall } from "../types";

// Hiển thị hoạt động tool (poc_search / poc_fetch / qdrant …) tách khỏi câu trả lời.
export default function ToolActivity({ tools }: { tools: ToolCall[] }) {
  if (!tools.length) return null;
  return (
    <div className="tools">
      {tools.map((t) => (
        <div key={t.id + (t.result ? ":r" : ":c")} className={"tool" + (t.is_error ? " err" : "")}>
          <b>{t.name}</b>
          {t.summary ? ` · ${t.summary}` : ""}
          {t.result !== undefined && (
            <> · {t.is_error ? "✗" : "✓"} {t.duration_ms ? `${t.duration_ms}ms` : ""}</>
          )}
        </div>
      ))}
    </div>
  );
}
