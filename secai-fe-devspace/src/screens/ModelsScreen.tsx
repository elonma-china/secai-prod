import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { Bot, Boxes, BrainCircuit, KeyRound, Layers, RefreshCw, Tags } from "lucide-react";
import { ApiError, health as fetchHealth, refreshModels } from "../api";
import type { HealthStatus } from "../types";

/** "Quản lý huấn luyện, triển khai" — the gateway has no model-inventory API (only
 * `POST /admin/refresh-models` → `{status, refreshed}` behind X-API-Key ==
 * `ADMIN_API_KEY`, and `GET /health` → llm/qwen booleans). So this table is a labeled status
 * view built from the design's representative deployed-stack rows, with only the primary LLM
 * row's "Trạng thái" pulled live from `/health` — everything else is static informational
 * content (called out explicitly below the table), never a live inventory. */

type RowStatus = "ok" | "warn" | "unknown";

interface ModelRow {
  icon: typeof BrainCircuit;
  model: string;
  role: string;
  status: RowStatus;
  statusLabel: string;
  updated: string;
  deployment: string;
}

const STATIC_ROWS: Array<Omit<ModelRow, "status" | "statusLabel">> = [
  { icon: BrainCircuit, model: "qwen2.5-72b-instruct", role: "LLM chính", updated: "2026-09-10", deployment: "gateway-01" },
  { icon: Layers, model: "bge-m3-embedding", role: "RAG embedding", updated: "2026-08-28", deployment: "qdrant-cluster" },
  { icon: Tags, model: "cve-classifier-v3", role: "Phân loại CVE", updated: "—", deployment: "train-node-2" },
  { icon: Bot, model: "bughunt-react-agent", role: "Agent ReAct", updated: "2026-09-14", deployment: "gateway-01" },
];

function StatusChip({ status, label }: { status: RowStatus; label: string }) {
  const cls = status === "ok" ? "chip ok" : status === "warn" ? "chip warn" : "chip";
  return (
    <span className={cls} style={{ cursor: "default" }}>
      {label}
    </span>
  );
}

const thStyle: CSSProperties = {
  textAlign: "left",
  fontSize: 11,
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "var(--muted2)",
  padding: "9px 12px",
  borderBottom: "1px solid var(--line)",
};

const tdStyle: CSSProperties = {
  padding: "11px 12px",
  borderBottom: "1px solid var(--line2)",
  color: "var(--text)",
};

export default function ModelsScreen({ userId }: { userId: string }) {
  const [apiKey, setApiKey] = useState("");
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    fetchHealth()
      .then((h) => alive && setHealth(h))
      .catch(() => alive && setHealth(null));
    return () => {
      alive = false;
    };
  }, []);

  async function doRefresh() {
    setRefreshBusy(true);
    setRefreshMsg(null);
    try {
      const res = await refreshModels(apiKey);
      const ok = res.status === "ok";
      setRefreshMsg({ ok, text: `status: ${String(res.status)} · refreshed: ${String(res.refreshed)}` });
      const h = await fetchHealth().catch(() => null);
      if (h) setHealth(h);
    } catch (e) {
      setRefreshMsg({
        ok: false,
        text: e instanceof ApiError ? `Lỗi ${e.status}: ${e.message}` : String(e),
      });
    } finally {
      setRefreshBusy(false);
    }
  }

  const llmOk = health?.llm ?? health?.qwen;
  const rows: ModelRow[] = STATIC_ROWS.map((r) => {
    if (r.model === "qwen2.5-72b-instruct") {
      const status: RowStatus = health == null ? "unknown" : llmOk ? "ok" : "warn";
      const statusLabel = health == null ? "Không rõ (chưa có /health)" : llmOk ? "Đang hoạt động" : "Không phản hồi";
      return { ...r, status, statusLabel };
    }
    if (r.model === "cve-classifier-v3") return { ...r, status: "warn", statusLabel: "Đang huấn luyện" };
    return { ...r, status: "ok", statusLabel: "Đang hoạt động" };
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ padding: "16px 26px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
        <h3 style={{ fontSize: 21 }}>Quản lý huấn luyện, triển khai</h3>
        <span className="mono" style={{ fontSize: 11, color: "var(--muted2)" }}>
          user_id: {userId}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <KeyRound size={14} strokeWidth={1.5} color="var(--muted)" />
            <input
              className="iasin"
              type="password"
              placeholder="X-API-Key"
              style={{ width: 160, padding: "7px 10px", fontSize: 12.5, minHeight: 0 }}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </div>
          <button className="iasbtn" style={{ padding: "7px 16px", fontSize: 13 }} onClick={doRefresh} disabled={refreshBusy}>
            <RefreshCw size={14} strokeWidth={1.5} className={refreshBusy ? "ias-spin" : undefined} />
            Làm mới models
          </button>
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "22px 26px" }}>
        {refreshMsg && (
          <div className={refreshMsg.ok ? "chip ok" : "chip crit"} style={{ cursor: "default", marginBottom: 14, display: "inline-flex" }}>
            {refreshMsg.text}
          </div>
        )}

        <div className="iascard">
          <div className="iascard-head" style={{ justifyContent: "space-between" }}>
            <span style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <Boxes size={14} strokeWidth={1.5} color="var(--teal)" />
              <span style={{ color: "var(--text)", fontWeight: 600, fontSize: 13.5 }}>Models đã triển khai</span>
            </span>
            <span className="mono" style={{ fontSize: 11, color: "var(--muted2)" }}>
              {rows.length} model
            </span>
          </div>
          <table style={{ width: "100%", fontSize: 13.5, borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={thStyle}></th>
                <th style={thStyle}>Model</th>
                <th style={thStyle}>Vai trò</th>
                <th style={thStyle}>Trạng thái</th>
                <th style={thStyle}>Cập nhật</th>
                <th style={thStyle}>Triển khai</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.model}>
                  <td style={tdStyle}>
                    <r.icon size={15} strokeWidth={1.5} color="var(--teal)" />
                  </td>
                  <td style={tdStyle} className="mono">
                    {r.model}
                  </td>
                  <td style={tdStyle}>{r.role}</td>
                  <td style={tdStyle}>
                    <StatusChip status={r.status} label={r.statusLabel} />
                  </td>
                  <td style={{ ...tdStyle, color: "var(--muted)" }} className="mono">
                    {r.updated}
                  </td>
                  <td style={tdStyle}>{r.deployment}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ marginTop: 10, fontSize: 11.5, color: "var(--muted2)" }}>
          Backend chưa có API liệt kê model runtime — bảng trên mô tả stack đã triển khai; chỉ dòng LLM chính lấy trạng thái trực tiếp
          từ <span className="mono">/health</span>.
        </div>
      </div>
    </div>
  );
}
