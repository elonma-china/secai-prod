import { useEffect, useState } from "react";
import { BarChart3, CalendarRange, ClipboardList, Download, FileText, Maximize2, Minimize2, ShieldX, Sparkles } from "lucide-react";
import { ApiError, reportStream, statisticalReport } from "../api";
import Markdown from "../components/Markdown";
import ReportMvpPanel from "./ReportMvpPanel";
import ReportTemplatePanel from "./review/ReportTemplatePanel";

/** "Báo cáo thông minh" — Tạo báo cáo (free text → markdown, streamed) / Thống kê kỳ (structured
 * JSON → report_title/summary/detail, one-shot). Field-name notes from
 * `api/messages.py::report_prompt`/`statistical_prompt`: the create-report body key the
 * backend actually reads is `logs` (not `data`) — `data` only feeds the generic
 * `supplied_text()` fallback used when nothing else is set. The stats body has no `period`
 * field server-side; we resolve the picked period into concrete `from_date`/`to_date` +
 * a short Vietnamese `message` client-side so the prompt the model sees is meaningful. */

type Tab = "create" | "stats" | "templates" | "review-template";
const REPORT_MVP_ENABLED = import.meta.env.VITE_REPORT_FEATURE_ENABLED === "true";
type Period = "week" | "month" | "quarter";

const PERIOD_OPTS: { id: Period; label: string }[] = [
  { id: "week", label: "Tuần này" },
  { id: "month", label: "Tháng này" },
  { id: "quarter", label: "Quý này" },
];

function periodRange(period: Period): { from: string; to: string } {
  const now = new Date();
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  let start: Date;
  if (period === "week") {
    const dow = (now.getDay() + 6) % 7; // Monday = 0
    start = new Date(now);
    start.setDate(now.getDate() - dow);
  } else if (period === "month") {
    start = new Date(now.getFullYear(), now.getMonth(), 1);
  } else {
    const q = Math.floor(now.getMonth() / 3);
    start = new Date(now.getFullYear(), q * 3, 1);
  }
  return { from: iso(start), to: iso(now) };
}

interface StatResult {
  title: string;
  summary: string;
  detail: string;
}

// Structured `detail[]` shape the report team asked for (per-machine findings, 6-column
// table: tên máy/IP/ngày quét/lỗ hổng/mức độ/mô tả) — rendered server-side in code, never
// paraphrased by the LLM. See secai-agent-gateway messages.py::render_scan_detail_table.
const SAMPLE_DETAIL = [
  {
    name: "WEB-SRV-01",
    ip: "10.0.0.5",
    scaned: "2026-09-10",
    cves: [
      { cve: "CVE-2021-44228", severity: "critical", description: "Log4Shell — RCE qua JNDI lookup trong Log4j2, cho phép thực thi mã từ xa không cần xác thực." },
      { cve: "CVE-2019-0708", severity: "high", description: "BlueKeep — lỗ hổng RCE trong RDP (Remote Desktop Services) trên Windows." },
    ],
  },
  {
    name: "DB-SRV-02",
    ip: "10.0.0.9",
    scaned: "2026-09-11",
    cves: [
      { cve: "CVE-2012-1823", severity: "medium", description: "PHP-CGI query string injection, cho phép thực thi mã tuỳ ý." },
    ],
  },
];

export default function ReportScreen({ userId }: { userId: string }) {
  const [tab, setTab] = useState<Tab>("create");

  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (!expanded) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setExpanded(false); };
    const shell = document.querySelectorAll<HTMLElement>(".ias-header, .ias-sidebar");
    const previous = Array.from(shell, (element) => element.inert);
    shell.forEach((element) => { element.inert = true; });
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("keydown", escape);
      shell.forEach((element, index) => { element.inert = previous[index]; });
    };
  }, [expanded]);

  // Tạo báo cáo
  const [logsText, setLogsText] = useState("");
  const [reportResult, setReportResult] = useState("");
  const [reportBusy, setReportBusy] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<Date | null>(null);

  // Thống kê kỳ
  const [period, setPeriod] = useState<Period>("week");
  const [statBusy, setStatBusy] = useState(false);
  const [statError, setStatError] = useState<string | null>(null);
  const [statResult, setStatResult] = useState<StatResult | null>(null);
  const [statSummary, setStatSummary] = useState(
    "Tổng số mục tiêu thiết bị được đánh giá: 3. Tổng số lỗ hổng phát hiện: 3. Số loại lỗ hổng phát hiện: 3.",
  );
  const [statDetailJson, setStatDetailJson] = useState(JSON.stringify(SAMPLE_DETAIL, null, 2));
  const [statDetailError, setStatDetailError] = useState<string | null>(null);

  async function submitReport() {
    const value = logsText.trim();
    if (!value || reportBusy) return;
    setReportBusy(true);
    setReportError(null);
    setBlocked(null);
    setReportResult("");
    setGeneratedAt(null);
    const ac = new AbortController();
    try {
      await reportStream(
        { logs: value, user_id: userId },
        {
          onToken: (delta) => setReportResult((r) => r + delta),
          onFinal: (payload) => {
            setReportResult((prev) => String(payload.content ?? payload.report ?? prev));
            setGeneratedAt(new Date());
          },
          onError: (msg) => setReportError(msg),
        },
        ac.signal,
      );
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        /* aborted — no-op */
      } else if (e instanceof ApiError && e.status === 422) {
        setBlocked(e.message);
      } else if (e instanceof ApiError) {
        setReportError(`Lỗi ${e.status}: ${e.message}`);
      } else {
        setReportError(String(e));
      }
    } finally {
      setReportBusy(false);
    }
  }

  function exportPdf() {
    window.print();
  }

  async function submitStats() {
    if (statBusy) return;
    setStatDetailError(null);
    let detail: unknown = undefined;
    const trimmedDetail = statDetailJson.trim();
    if (trimmedDetail) {
      try {
        detail = JSON.parse(trimmedDetail);
      } catch (e) {
        setStatDetailError(`JSON "Dữ liệu chi tiết" không hợp lệ: ${String(e instanceof Error ? e.message : e)}`);
        return;
      }
    }
    setStatBusy(true);
    setStatError(null);
    setStatResult(null);
    const { from, to } = periodRange(period);
    try {
      const res = await statisticalReport({
        period,
        from_date: from,
        to_date: to,
        summary: statSummary.trim() || undefined,
        detail,
        user_id: userId,
      });
      setStatResult({
        title: String(res.report_title ?? ""),
        summary: String(res.report_summary ?? ""),
        detail: String(res.report_detail ?? ""),
      });
    } catch (e) {
      setStatError(e instanceof ApiError ? `Lỗi ${e.status}: ${e.message}` : String(e));
    } finally {
      setStatBusy(false);
    }
  }

  return (
    <div className={`report-screen${expanded ? " report-screen-expanded" : ""}`}>
      <style>{`
        @media print {
          body * { visibility: hidden; }
          #report-print-area, #report-print-area * { visibility: visible; }
          #report-print-area { position: absolute; left: 0; top: 0; width: 100%; padding: 24px; }
        }
      `}</style>

      <div className="report-screen-toolbar">
        <h3 style={{ fontSize: 21 }}>Báo cáo thông minh</h3>
        <div className="iasseg">
          <button type="button" className={"opt" + (tab === "create" ? " on" : "")} aria-pressed={tab === "create"} onClick={() => setTab("create")}>
            Tạo báo cáo
          </button>
          <button type="button" className={"opt" + (tab === "stats" ? " on" : "")} aria-pressed={tab === "stats"} onClick={() => setTab("stats")}>
            Thống kê kỳ
          </button>
          {REPORT_MVP_ENABLED && (
            <button type="button" className={"opt" + (tab === "templates" ? " on" : "")} aria-pressed={tab === "templates"} onClick={() => setTab("templates")}>
              Theo mẫu
            </button>
          )}
          <button type="button" className={"opt" + (tab === "review-template" ? " on" : "")} aria-pressed={tab === "review-template"} onClick={() => setTab("review-template")}>Template bằng prompt</button>
        </div>
        <button className="iasbtn ghost report-expand" type="button" aria-pressed={expanded}
          onClick={() => setExpanded((value) => !value)} title={expanded ? "Thu gọn (Esc)" : "Mở toàn màn hình"}>
          {expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          {expanded ? "Thu gọn" : "Toàn màn hình"}
        </button>
      </div>

      <div className={`report-screen-body${tab === "templates" || tab === "review-template" ? " report-screen-body-templates" : ""}`}>
        {tab === "create" && (
          <>
            {blocked && (
              <div className="banner-row">
                <div className="banner-422">
                  <ShieldX size={18} strokeWidth={1.5} color="#f0897c" className="icon" />
                  <div style={{ fontSize: 13, lineHeight: 1.5 }}>
                    <strong>Đầu vào bị chặn (422).</strong>{" "}
                    <span style={{ color: "var(--text)" }}>
                      Dữ liệu có thể chứa bí mật/PII nên không được gửi tới mô hình. Chi tiết: {blocked}
                    </span>
                  </div>
                </div>
              </div>
            )}
            {reportError && !blocked && <div className="error-box">{reportError}</div>}

            <div className="iascard">
              <div className="iascard-head">
                <ClipboardList size={14} strokeWidth={1.5} color="var(--teal)" />
                <span style={{ color: "var(--text)", fontWeight: 600, fontSize: 13.5 }}>Dữ liệu / log nguồn</span>
              </div>
              <textarea
                className="iasin"
                placeholder="Dán log, kết quả quét hoặc dữ liệu cần tổng hợp…"
                style={{ minHeight: 140, resize: "vertical", border: "none", borderRadius: 0, background: "transparent", padding: "14px 15px" }}
                value={logsText}
                onChange={(e) => setLogsText(e.target.value)}
              />
              <div style={{ display: "flex", gap: 10, padding: "10px 15px", borderTop: "1px solid var(--line2)" }}>
                <button className="iasbtn" onClick={submitReport} disabled={reportBusy || !logsText.trim()}>
                  <FileText size={15} strokeWidth={1.5} />
                  Tạo báo cáo
                </button>
                <button className="iasbtn ghost" onClick={exportPdf} disabled={!reportResult.trim()}>
                  <Download size={15} strokeWidth={1.5} />
                  Xuất PDF
                </button>
              </div>
            </div>

            <div className="iascard">
              <div className="iascard-head" style={{ justifyContent: "space-between" }}>
                <span style={{ display: "flex", alignItems: "center", gap: 7 }}>
                  <Sparkles size={14} strokeWidth={1.5} color="var(--teal)" />
                  <span style={{ color: "var(--text)", fontWeight: 600, fontSize: 13.5 }}>Kết quả (Markdown)</span>
                </span>
                {generatedAt && (
                  <span className="mono" style={{ fontSize: 11, color: "var(--muted2)" }}>
                    tạo lúc {generatedAt.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}
                  </span>
                )}
              </div>
              <div id="report-print-area" style={{ padding: "16px 18px", lineHeight: 1.6, minHeight: 80 }}>
                {!reportResult && !reportBusy && (
                  <div style={{ color: "var(--muted)", fontSize: 13.5 }}>Kết quả báo cáo sẽ hiển thị tại đây…</div>
                )}
                {(reportResult || reportBusy) && (
                  <>
                    <Markdown>{reportResult}</Markdown>
                    {reportBusy && <span className="blink" style={{ color: "var(--teal)", fontWeight: 700 }}>▌</span>}
                  </>
                )}
              </div>
            </div>
          </>
        )}

        {tab === "stats" && (
          <>
            {statError && <div className="error-box">{statError}</div>}

            <div className="iascard">
              <div className="iascard-head">
                <CalendarRange size={14} strokeWidth={1.5} color="var(--teal)" />
                <span style={{ color: "var(--text)", fontWeight: 600, fontSize: 13.5 }}>Kỳ báo cáo</span>
              </div>
              <div style={{ padding: "16px 18px", display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                <div className="iasseg">
                  {PERIOD_OPTS.map((o) => (
                    <div key={o.id} className={"opt" + (period === o.id ? " on" : "")} onClick={() => setPeriod(o.id)}>
                      {o.label}
                    </div>
                  ))}
                </div>
                <button className="iasbtn" onClick={submitStats} disabled={statBusy}>
                  <BarChart3 size={15} strokeWidth={1.5} />
                  Tạo thống kê
                </button>
              </div>
            </div>

            <div className="iascard">
              <div className="iascard-head">
                <ClipboardList size={14} strokeWidth={1.5} color="var(--teal)" />
                <span style={{ color: "var(--text)", fontWeight: 600, fontSize: 13.5 }}>Tổng quan (summary)</span>
              </div>
              <textarea
                className="iasin"
                placeholder="Số máy đã quét, tổng số lỗ hổng, phân loại theo mức độ…"
                style={{ minHeight: 60, resize: "vertical", border: "none", borderRadius: 0, background: "transparent", padding: "14px 15px" }}
                value={statSummary}
                onChange={(e) => setStatSummary(e.target.value)}
              />
            </div>

            <div className="iascard">
              <div className="iascard-head">
                <ClipboardList size={14} strokeWidth={1.5} color="var(--teal)" />
                <span style={{ color: "var(--text)", fontWeight: 600, fontSize: 13.5 }}>
                  Dữ liệu chi tiết (mảng máy/lỗ hổng — name, ip, scaned, cves[cve, severity, description])
                </span>
              </div>
              <textarea
                className="iasin mono"
                style={{ minHeight: 220, resize: "vertical", border: "none", borderRadius: 0, background: "transparent", padding: "14px 15px", fontSize: 12 }}
                value={statDetailJson}
                onChange={(e) => setStatDetailJson(e.target.value)}
              />
              {statDetailError && (
                <div style={{ padding: "8px 15px", color: "#f0897c", fontSize: 12, borderTop: "1px solid var(--line2)" }}>
                  {statDetailError}
                </div>
              )}
            </div>

            <div className="iascard">
              <div className="iascard-head mono" style={{ color: "var(--text)", fontWeight: 600, fontSize: 13.5 }}>
                report_title / summary / detail
              </div>
              <div style={{ padding: "16px 18px" }}>
                {!statResult && !statBusy && (
                  <div style={{ color: "var(--muted)", fontSize: 13.5 }}>Kết quả thống kê kỳ sẽ hiển thị tại đây…</div>
                )}
                {statBusy && (
                  <div style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 12.5, color: "var(--muted)" }}>
                    <span className="spin" />
                    Đang tổng hợp thống kê…
                  </div>
                )}
                {statResult && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {statResult.title && <h4 style={{ margin: 0, fontSize: 16, color: "var(--tealb)" }}>{statResult.title}</h4>}
                    {statResult.summary && <Markdown>{statResult.summary}</Markdown>}
                    {statResult.detail && (
                      <div style={{ borderTop: "1px solid var(--line2)", paddingTop: 10 }}>
                        <div style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--muted2)", marginBottom: 6 }}>
                          Chi tiết
                        </div>
                        <Markdown>{statResult.detail}</Markdown>
                      </div>
                    )}
                    {!statResult.title && !statResult.summary && !statResult.detail && (
                      <div style={{ color: "var(--muted)", fontSize: 13.5 }}>Không có dữ liệu.</div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </>
        )}
        {REPORT_MVP_ENABLED && <div className="report-template-tab" hidden={tab !== "templates"}><ReportMvpPanel /></div>}
        <div className="report-template-tab" hidden={tab !== "review-template"}><ReportTemplatePanel logs={logsText} onLogsChange={setLogsText} /></div>
      </div>
    </div>
  );
}
