import { useEffect, useRef, useState } from "react";
import { stream, ApiError } from "../api";
import type { PhaseEvent, ToolCall } from "../types";

type LogKind = "info" | "phase" | "tool" | "result" | "token" | "ok" | "err" | "sys";

interface LogLine {
  id: number;
  ts: string;
  kind: LogKind;
  text: string;
}

interface Finding {
  kind?: string;
  title?: string;
  severity?: string;
  path?: string;
  evidence?: string;
}

interface Hypothesis {
  vul_class?: string;
  cve?: string | null;
  confidence?: number;
  evidence?: string;
  source?: string;
}

const PHASES = ["AgentHunt", "Validate", "MapCVE", "Report", "Exploit"] as const;

function nowTs() {
  const d = new Date();
  return d.toLocaleTimeString("en-GB", { hour12: false }) + "." + String(d.getMilliseconds()).padStart(3, "0");
}

/** Decode JSON / \\uXXXX so Vietnamese (and other Unicode) renders, not escapes. */
function displayText(v: unknown, max = 280): string {
  try {
    let s: string;
    if (typeof v === "string") {
      const t = v.trim();
      if (
        (t.startsWith("{") && t.endsWith("}")) ||
        (t.startsWith("[") && t.endsWith("]"))
      ) {
        try {
          s = JSON.stringify(JSON.parse(t));
        } catch {
          s = unescapeUnicode(v);
        }
      } else {
        s = unescapeUnicode(v);
      }
    } else if (v === undefined || v === null) {
      return "";
    } else {
      s = JSON.stringify(v);
    }
    return s.length > max ? s.slice(0, max) + "…" : s;
  } catch {
    return String(v);
  }
}

function unescapeUnicode(s: string): string {
  return s.replace(/\\u([0-9a-fA-F]{4})/g, (_, h) =>
    String.fromCharCode(parseInt(h, 16)),
  );
}

export default function BughuntPanel({ streaming }: { streaming: boolean }) {
  const [target, setTarget] = useState(() => localStorage.getItem("secai_bh_target") || "127.0.0.1:8765");
  const [scopeNote, setScopeNote] = useState(() => localStorage.getItem("secai_bh_scope") || "local demo");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [activePhase, setActivePhase] = useState<string>("");
  const [donePhases, setDonePhases] = useState<string[]>([]);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [engineCounts, setEngineCounts] = useState<Record<string, number> | null>(null);
  const [summary, setSummary] = useState("");
  const [runMeta, setRunMeta] = useState<Record<string, unknown> | null>(null);
  const [enableExploit, setEnableExploit] = useState(false);
  const [reportMd, setReportMd] = useState("");
  const [reportPath, setReportPath] = useState("");
  const [reportMeta, setReportMeta] = useState<{ lead_count?: number; cve_count?: number } | null>(null);
  const [validatedLeads, setValidatedLeads] = useState<Hypothesis[]>([]);

  const abortRef = useRef<AbortController | null>(null);
  const termRef = useRef<HTMLDivElement>(null);
  const logId = useRef(0);
  const tokenBuf = useRef("");

  useEffect(() => localStorage.setItem("secai_bh_target", target), [target]);
  useEffect(() => localStorage.setItem("secai_bh_scope", scopeNote), [scopeNote]);

  useEffect(() => {
    const el = termRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs]);

  function append(kind: LogKind, text: string) {
    logId.current += 1;
    setLogs((prev) => [...prev, { id: logId.current, ts: nowTs(), kind, text }]);
  }

  function flushTokens() {
    const t = tokenBuf.current.trim();
    if (t) {
      append("token", t.length > 400 ? t.slice(0, 400) + "…" : t);
      tokenBuf.current = "";
    }
  }

  function resetRun() {
    setError("");
    setActivePhase("");
    setDonePhases([]);
    setLogs([]);
    setFindings([]);
    setHypotheses([]);
    setEngineCounts(null);
    setSummary("");
    setRunMeta(null);
    setReportMd("");
    setReportPath("");
    setReportMeta(null);
    setValidatedLeads([]);
    tokenBuf.current = "";
    logId.current = 0;
  }

  function onPhase(p: PhaseEvent) {
    const kind = p.kind || "phase";
    const title = p.title || "";
    if (kind === "phase" && title) {
      flushTokens();
      setActivePhase(title);
      setDonePhases((prev) => (prev.includes(title) ? prev : [...prev, title]));
      append("phase", `▶ phase ${title}`);
      return;
    }
    // agent_start / agent_end / agent_replayed — terminal detail
    if (kind === "agent_start") {
      append("sys", `⋯ start ${title}${p.subagent_type ? ` [${p.subagent_type}]` : ""}`);
      return;
    }
    flushTokens();
    const bits: string[] = [`✓ ${title || kind}`];
    if (p.result_text) bits.push(displayText(p.result_text, 220).replace(/\s+/g, " "));
    append(kind === "agent_end" ? "ok" : "info", bits.join(" — "));

    const so = p.structured_output;
    if (so && title === "recon-probe" && Array.isArray(so.findings)) {
      setFindings(so.findings as Finding[]);
    }
    if (so && title === "recon-engine") {
      if (so.counts && typeof so.counts === "object") {
        setEngineCounts(so.counts as Record<string, number>);
      }
      if (Array.isArray(so.live_hosts) && so.live_hosts.length) {
        append("info", `live hosts: ${(so.live_hosts as string[]).slice(0, 5).join(", ")}`);
      }
    }
    if (so && (title === "hunt-hypotheses" || title === "hunt-worker")) {
      const hyps = (so.hypotheses as Hypothesis[]) || [];
      if (hyps.length) setHypotheses(hyps);
    }
    if (so && title === "validate-leads") {
      const leads = (so.leads as Hypothesis[]) || [];
      if (leads.length) {
        setValidatedLeads(leads);
        setHypotheses(leads);
      }
    }
    if (so && title === "report-draft") {
      const md = String(so.markdown || so.preview || "");
      if (md) setReportMd(md);
      if (so.path) setReportPath(String(so.path));
      setReportMeta({
        lead_count: typeof so.lead_count === "number" ? so.lead_count : undefined,
        cve_count: typeof so.cve_count === "number" ? so.cve_count : undefined,
      });
    }
  }

  function onToolCall(t: ToolCall) {
    flushTokens();
    append("tool", `$ ${t.name}${t.summary ? " · " + t.summary : ""}`);
    if (t.input !== undefined) append("info", `  args ${displayText(t.input)}`);
  }

  function onToolResult(t: ToolCall) {
    const flag = t.is_error ? "✗" : "✓";
    const dur = t.duration_ms != null ? ` ${t.duration_ms}ms` : "";
    append(t.is_error ? "err" : "result", `${flag} ${t.name}${dur}`);
    if (t.result) append("info", `  ← ${displayText(t.result, 360)}`);
  }

  async function start() {
    const t = target.trim();
    if (!t || busy) return;
    if (!streaming) {
      setError("Bughunt cần Streaming (SSE). Bật toggle Streaming trên topbar.");
      return;
    }
    resetRun();
    setBusy(true);
    append("sys", `bughunt start target=${t}`);
    if (scopeNote.trim()) append("sys", `scope: ${scopeNote.trim()}`);

    const ac = new AbortController();
    abortRef.current = ac;
    const body = {
      target: t,
      scope_note: scopeNote.trim() || undefined,
      enable_exploit: enableExploit,
      run_cve_sweep: true,
    };

    try {
      await stream(
        "/ai/bughunt/stream",
        body,
        {
          onPhase,
          onToken: (d) => {
            tokenBuf.current += d;
            // flush periodically so long LLM output doesn't sit forever
            if (tokenBuf.current.length > 500) flushTokens();
          },
          onToolCall,
          onToolResult,
          onLog: (p) => {
            const stream = (p.stream || "stdout").toLowerCase();
            const line = (p.line || "").trimEnd();
            if (!line) return;
            const kind =
              stream === "stderr" || stream === "err"
                ? "err"
                : stream === "sys"
                  ? "sys"
                  : "info";
            append(kind, stream === "sys" ? line : `[${stream}] ${line}`);
          },
          onError: (e) => {
            append("err", e);
            setError(e);
          },
          onFinal: (p) => {
            flushTokens();
            const ok = p.ok !== false;
            const result = (p.result as Record<string, unknown>) || {};
            setRunMeta((p.metadata as Record<string, unknown>) || null);
            setSummary(String(p.content || result.message || ""));
            const surface = (result.surface as Record<string, unknown>) || {};
            if (Array.isArray(surface.findings)) setFindings(surface.findings as Finding[]);
            const hunt = (result.hunt as Record<string, unknown>) || {};
            if (Array.isArray(hunt.hypotheses)) setHypotheses(hunt.hypotheses as Hypothesis[]);
            const eng = (result.engine as Record<string, unknown>) || {};
            if (eng.counts && typeof eng.counts === "object") {
              setEngineCounts(eng.counts as Record<string, number>);
            }
            append(ok ? "ok" : "err", ok ? "final OK" : `final FAIL: ${p.error || "unknown"}`);
            if (result.status) append("info", `status=${result.status} llm_hunt=${result.llm_hunt}`);
            const mapCve = (result.map_cve as Record<string, unknown>) || {};
            if (Array.isArray(mapCve.cves) && mapCve.cves.length) {
              append("ok", `CVEs: ${(mapCve.cves as { cve?: string }[]).map((c) => c.cve).filter(Boolean).join(", ")}`);
            }
            const report = (result.report as Record<string, unknown>) || {};
            if (report.path) {
              append("info", `report: ${String(report.path)}`);
              setReportPath(String(report.path));
            }
            const md = String(report.markdown || report.preview || "");
            if (md) setReportMd(md);
            setReportMeta({
              lead_count: typeof report.lead_count === "number" ? report.lead_count : undefined,
              cve_count: typeof report.cve_count === "number" ? report.cve_count : undefined,
            });
            const validation = (result.validation as Record<string, unknown>) || {};
            if (Array.isArray(validation.leads) && validation.leads.length) {
              setValidatedLeads(validation.leads as Hypothesis[]);
              setHypotheses(validation.leads as Hypothesis[]);
            }
            const exploit = (result.exploit as Record<string, unknown>) || {};
            if (exploit.summary) append("info", String(exploit.summary));
            setActivePhase("");
          },
        },
        ac.signal
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        const msg = e instanceof ApiError ? `HTTP ${e.status}: ${e.message}` : String(e);
        setError(msg);
        append("err", msg);
      } else {
        append("sys", "aborted");
      }
    } finally {
      flushTokens();
      setBusy(false);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
    setBusy(false);
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      start();
    }
  }

  return (
    <div className="bughunt panel">
      <div className="card bh-form">
        <h3>Scan-first bughunt</h3>
        <p className="bh-hint">
          Target only — AgentHunt → Validate (triage) → MapCVE → Report → Exploit (opt-in).
          Không nhập CVE thủ công; CVE chỉ từ scanner evidence.
        </p>
        <div className="grid2">
          <label className="lbl">
            Target
            <input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              onKeyDown={onKey}
              placeholder="host:port hoặc domain"
              disabled={busy}
            />
          </label>
          <label className="lbl">
            Scope note
            <input
              value={scopeNote}
              onChange={(e) => setScopeNote(e.target.value)}
              onKeyDown={onKey}
              placeholder="optional"
              disabled={busy}
            />
          </label>
        </div>
        <label className="lbl bh-check" style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center" }}>
          <input
            type="checkbox"
            checked={enableExploit}
            onChange={(e) => setEnableExploit(e.target.checked)}
            disabled={busy}
          />
          Enable Exploit phase (cần POC_EXPLOIT_ENABLED + CVE evidence)
        </label>
        <div className="bh-actions">
          {busy ? (
            <button className="btn danger" onClick={stop}>Dừng</button>
          ) : (
            <button className="btn" onClick={start} disabled={!target.trim()}>Bắt đầu hunt</button>
          )}
          <button className="btn ghost sm" onClick={resetRun} disabled={busy}>Xóa log</button>
          {runMeta?.run_id != null && (
            <span className="bh-runid">run_id: {String(runMeta.run_id)}</span>
          )}
        </div>
        <div className="bh-phases">
          {PHASES.map((ph) => {
            const done = donePhases.includes(ph);
            const active = activePhase === ph;
            return (
              <span
                key={ph}
                className={"bh-phase" + (active ? " active" : "") + (done && !active ? " done" : "")}
              >
                {ph}
              </span>
            );
          })}
        </div>
      </div>

      {error && <div className="error-box">{error}</div>}

      <div className="bh-split">
        <div className="bh-term card">
          <div className="bh-term-bar">
            <span className="bh-term-dot" />
            <span className="bh-term-dot" />
            <span className="bh-term-dot" />
            <span className="bh-term-title">bughunt · SSE console</span>
            {busy && <span className="spin" />}
          </div>
          <div className="bh-term-body" ref={termRef}>
            {logs.length === 0 && (
              <div className="bh-term-empty">Nhập target rồi bấm «Bắt đầu hunt» — log phase / tool sẽ hiện ở đây.</div>
            )}
            {logs.map((l) => (
              <div key={l.id} className={"bh-line kind-" + l.kind}>
                <span className="bh-ts">{l.ts}</span>
                <span className="bh-txt">{l.text}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="bh-side">
          {engineCounts && (
            <div className="card">
              <h3>Engine counts</h3>
              <div className="bh-counts">
                {Object.entries(engineCounts).map(([k, v]) => (
                  <div key={k} className="bh-count">
                    <span>{k}</span>
                    <b>{v}</b>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="card">
            <h3>Findings ({findings.length})</h3>
            {findings.length === 0 ? (
              <div className="empty" style={{ padding: 12 }}>Chưa có</div>
            ) : (
              <ul className="bh-list">
                {findings.slice(0, 24).map((f, i) => (
                  <li key={i}>
                    <span className={"sev sev-" + (f.severity || "info")}>{f.severity || "info"}</span>
                    <div>
                      <b>{f.title}</b>
                      {f.path && <code> {f.path}</code>}
                      {f.evidence && <div className="bh-ev">{f.evidence}</div>}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="card">
            <h3>
              Leads / hypotheses ({hypotheses.length})
              {validatedLeads.length > 0 && (
                <span className="bh-conf"> · validated {validatedLeads.length}</span>
              )}
            </h3>
            {hypotheses.length === 0 ? (
              <div className="empty" style={{ padding: 12 }}>Chưa có</div>
            ) : (
              <ul className="bh-list">
                {hypotheses.map((h, i) => (
                  <li key={i}>
                    <span className="sev sev-info">{h.source || "lead"}</span>
                    <div>
                      <b>{h.vul_class}</b>
                      {h.confidence != null && <span className="bh-conf"> {Math.round(h.confidence * 100)}%</span>}
                      {h.cve && <code> {h.cve}</code>}
                      {h.evidence && <div className="bh-ev">{h.evidence}</div>}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="card bh-report-card">
            <div className="bh-report-head">
              <h3>
                Report
                {reportMeta?.lead_count != null && (
                  <span className="bh-conf">
                    {" "}
                    · {reportMeta.lead_count} leads
                    {reportMeta.cve_count != null ? `, ${reportMeta.cve_count} CVEs` : ""}
                  </span>
                )}
              </h3>
              {reportMd && (
                <button
                  type="button"
                  className="btn ghost sm"
                  onClick={() => {
                    const blob = new Blob([reportMd], { type: "text/markdown;charset=utf-8" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = "bughunt-report.md";
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                >
                  Tải .md
                </button>
              )}
            </div>
            {!reportMd ? (
              <div className="empty" style={{ padding: 12 }}>
                Chưa có — hiện sau phase Report
              </div>
            ) : (
              <>
                {reportPath && (
                  <div className="bh-report-path" title={reportPath}>
                    {reportPath}
                  </div>
                )}
                <pre className="bh-report-md">{reportMd}</pre>
              </>
            )}
          </div>

          {summary && (
            <div className="card">
              <h3>Summary</h3>
              <pre className="bh-summary">{summary}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
