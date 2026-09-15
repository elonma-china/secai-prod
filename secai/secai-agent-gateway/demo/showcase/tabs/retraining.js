// Tab "Huấn luyện lại" — cơ chế retraining tự động (gate >5% drop) + hướng dẫn CÀI/CHẠY
// + tải bộ build fine-tuning chạy được (hồ sơ gốc: 02. Bo_build_finetuning).
import { mdToHtml } from "../md.js";
const esc = (t) => String(t ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;");

// Nhãn nhịp đối soát: "hằng tháng (ngày 15)" khi policy có evaluation_day.
const CADENCE_VI = { monthly: "hằng tháng", weekly: "hằng tuần", daily: "hằng ngày", quarterly: "hằng quý" };
function cadenceLabel(p) {
  const base = CADENCE_VI[(p && p.cadence) || "monthly"] || (p && p.cadence) || "hằng tháng";
  return p && p.cadence_day ? `${base} (ngày ${p.cadence_day})` : base;
}

// Khối lệnh có nút Copy.
function code(cmd) {
  const id = "c" + Math.random().toString(36).slice(2, 9);
  return `<div class="code-wrap">
    <button class="copy-btn" data-copy="${id}" type="button">Copy</button>
    <pre class="run-log" id="${id}">${esc(cmd)}</pre>
  </div>`;
}

function wireCopy(container) {
  container.querySelectorAll(".copy-btn").forEach((b) => {
    b.addEventListener("click", () => {
      const pre = container.querySelector("#" + b.dataset.copy);
      navigator.clipboard?.writeText(pre.textContent).then(() => {
        const old = b.textContent; b.textContent = "Đã copy"; setTimeout(() => (b.textContent = old), 1200);
      });
    });
  });
}

export async function render(container, api) {
  container.innerHTML = `<div class="card"><h3>Huấn luyện lại</h3><p class="hint">Đang tải…</p></div>`;
  const [policy, guide] = await Promise.all([
    api.get("/showcase-api/finetune/retraining-policy").catch(() => null),
    api.get("/showcase-api/finetune/retrain-guide").catch(() => null),
  ]);
  if (!policy && !guide) {
    container.innerHTML = `<div class="card"><p class="error">Không tải được dữ liệu huấn luyện lại.</p></div>`;
    return;
  }

  container.innerHTML =
    renderGate(policy, guide) +
    renderHistory(policy) +
    renderThresholds(policy) +
    (guide ? renderInstall(guide) : "") +
    (guide ? renderRun(guide) : "") +
    (guide ? renderBundle(guide) : "");
  wireCopy(container);
}

// ── 1. Cơ chế retraining tự động (gate) ──────────────────────────────────
function renderGate(p, g) {
  const gate = (g && g.gate) || {};
  const dec = p && p.decision;
  const cycle = (gate.cycle || [])
    .map((s) => `<li><strong>${esc(s.step)}</strong>${s.detail ? ` — ${esc(s.detail)}` : ""}${s.cmd ? code(s.cmd) : ""}</li>`)
    .join("");
  const outcomes = (gate.outcomes || []).map((o) => `<li>${esc(o)}</li>`).join("");

  let decBlock = "";
  if (dec) {
    const rows = (dec.components || [])
      .flatMap((c) => (c.metrics || []).map((m) => `<tr>
        <td>${esc(c.component)}</td>
        <td>${esc(m.metric)}</td>
        <td>${m.baseline ?? "—"}</td>
        <td>${m.current ?? "—"}</td>
        <td>${m.relative_degradation != null ? (m.relative_degradation * 100).toFixed(1) + "%" : "—"}</td>
        <td>${m.breached ? '<span class="pill fail">vượt ngưỡng</span>' : '<span class="pill ok">đạt</span>'}</td>
      </tr>`)).join("");
    const ok = (dec.decision || "").includes("NO_FINETUNE");
    decBlock = `
      <h3 style="margin-top:14px">Kết quả đối soát gần nhất</h3>
      <p class="row">
        <span class="pill ${ok ? "ok" : "fail"}">${esc(dec.decision || "—")}</span>
        <span class="hint">đánh giá ${esc(dec.evaluated_at || "—")} · ngưỡng drift ${dec.drift_threshold_percent ?? 5}%</span>
      </p>
      <table>
        <thead><tr><th>Thành phần</th><th>Chỉ số</th><th>Baseline</th><th>Hiện tại</th><th>Suy giảm</th><th>Kết luận</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6">—</td></tr>'}</tbody>
      </table>`;
  }

  return `
    <div class="card">
      <h3>Cơ chế huấn luyện lại tự động</h3>
      <p>${esc(gate.summary || "Vòng lặp tự động: tự giám sát định kỳ, tự chấm ngưỡng và tự đặt cờ huấn luyện lại đúng thành phần khi chỉ số suy giảm quá ngưỡng.")}</p>
      ${cycle ? `<h3 style="margin-top:12px">Vòng đời gate</h3><ol class="gate-cycle">${cycle}</ol>` : ""}
      ${outcomes ? `<h3 style="margin-top:12px">Ba kết luận của gate</h3><ul>${outcomes}</ul>` : ""}
      ${gate.gate_script ? `<p class="hint">Script gate: <code>${esc(gate.gate_script)}</code> → ghi <code>${esc(gate.decision_file)}</code></p>` : ""}
      ${decBlock}
    </div>`;
}

// ── 2b. Lịch sử đối soát định kỳ (mục 3.1.17c) ───────────────────────────
function renderHistory(p) {
  const hist = (p && p.decision_history) || [];
  if (!hist.length) return "";
  const comps = ["embedding", "reranker", "llm"];
  const thr = (p.drop_threshold_pct ?? 5) / 100;

  const worstDeg = (run, name) => {
    const c = (run.components || []).find((x) => x.component === name);
    if (!c || !(c.metrics || []).length) return null;
    return Math.max(...c.metrics.map((m) => m.relative_degradation ?? 0));
  };
  // Overall = mức suy giảm xấu nhất toàn hệ thống trong lần đo (dùng chung cho chart + bảng + log).
  const overallDeg = (run) => {
    const vals = comps.map((c) => worstDeg(run, c)).filter((d) => d != null);
    return vals.length ? Math.max(...vals) : null;
  };
  const driftCell = (d) => {
    if (d == null) return `<td class="hint">—</td>`;
    const cls = d > thr ? "fail" : d > thr * 0.6 ? "warn" : "ok";
    return `<td><span class="pill ${cls}">${d > 0 ? "+" : ""}${(d * 100).toFixed(1)}%</span></td>`;
  };

  const rows = hist.map((r) => {
    const ok = (r.decision || "").includes("NO_FINETUNE");
    const need = (r.components_requiring_finetune || []).join(", ");
    return `<tr>
      <td>${esc(r.evaluated_at || "—")}</td>
      <td><span class="pill ${ok ? "ok" : "fail"}">${esc(r.decision || "—")}</span></td>
      ${comps.map((c) => driftCell(worstDeg(r, c))).join("")}
      ${driftCell(overallDeg(r))}
      <td>${need ? `<code>${esc(need)}</code>` : '<span class="hint">—</span>'}</td>
    </tr>`;
  }).join("");

  // Sparkline: overall (mức suy giảm xấu nhất) mỗi lần đo (thang 8% = full cột).
  const bars = hist.map((r) => {
    const d = overallDeg(r) ?? 0;
    const h = Math.min(100, Math.round((d / 0.08) * 100));
    const cls = d > thr ? "fail" : "ok";
    return `<div class="hist-bar" title="${esc(r.evaluated_at || "")} — overall ${(d * 100).toFixed(2)}%">
      <i class="${cls}" style="height:${Math.max(4, h)}%"></i>
      <span>${(d * 100).toFixed(1)}%</span>
    </div>`;
  }).join("");

  // Log phiên đối soát từng kỳ (bằng chứng dạng terminal) — số trong log khớp bảng trên.
  // Kèm log thành phần (raw per-item) tham chiếu format raw-logs của các lần chạy trước.
  const logs = hist
    .filter((r) => r.log)
    .map((r) => {
      const ok = (r.decision || "").includes("NO_FINETUNE");
      const compLogs = Object.entries(r.component_logs || {})
        .map(([name, text]) => `<details class="hist-log comp-log">
          <summary>Log thành phần — ${esc(name)}</summary>
          <pre class="run-log">${esc(text)}</pre>
        </details>`)
        .join("");
      return `<details class="hist-log" open>
        <summary>Log đối soát — ${esc(r.evaluated_at || "—")} <span class="pill ${ok ? "ok" : "fail"}">${esc(r.decision || "—")}</span></summary>
        <pre class="run-log">${esc(r.log)}</pre>
        ${compLogs}
      </details>`;
    })
    .join("");

  return `
    <div class="card">
      <h3>Lịch sử đối soát định kỳ</h3>
      <p>Pipeline chạy lại đánh giá trên bộ 1.000 mẫu held-out theo nhịp
         <strong>${esc(cadenceLabel(p))}</strong>, so với baseline đã nghiệm thu và ghi
         <code>finetune-decision.json</code> mỗi lần. Mỗi ô là mức suy giảm tương đối xấu nhất của
         thành phần đó trong lần đo.</p>
      ${bars ? `<div class="hist-bars">${bars}</div>` : ""}
      <table>
        <thead><tr>
          <th>Lần đo</th><th>Quyết định</th><th>Embedding</th><th>Reranker</th><th>LLM</th><th>Overall (max)</th><th>Thành phần cần FT</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${logs ? `<h3 style="margin-top:14px">Log phiên đối soát &amp; log thành phần</h3>${logs}` : ""}
      ${p.decision_history_source ? `<p class="hint">Nguồn dữ liệu (đường dẫn tuyệt đối): <code>${esc(p.decision_history_source)}</code></p>` : ""}
      <p class="hint">Ngưỡng kích hoạt ${p.drop_threshold_pct ?? 5}% · ${hist.length} lần đo được ghi nhận.</p>
    </div>`;
}

// ── 2. Ngưỡng kích hoạt theo thành phần ──────────────────────────────────
function renderThresholds(p) {
  if (!p) return "";
  const rows = (p.checks || [])
    .map((c) => `<tr>
      <td>${esc(c.component)}</td>
      <td><code>${esc(c.model)}</code></td>
      <td>${esc(c.metric)}${c.description ? ` <span class="hint">(${esc(c.description)})</span>` : ""}</td>
      <td>${c.max_relative_drop != null ? "> " + (c.max_relative_drop * 100) + "%" : "—"}</td>
    </tr>`).join("");
  return `
    <div class="card">
      <h3>Ngưỡng kích hoạt theo thành phần</h3>
      <p>Kích hoạt huấn luyện lại khi <strong>suy giảm tương đối &gt; ${p.drop_threshold_pct ?? 5}%</strong>
         so với baseline đã nghiệm thu. Đối soát định kỳ <strong>${esc(cadenceLabel(p))}</strong> trên bộ 1.000 mẫu held-out;
         cập nhật tri thức CVE mới hằng ngày (không phải retraining).</p>
      <table>
        <thead><tr><th>Thành phần</th><th>Mô hình</th><th>Chỉ số giám sát</th><th>Ngưỡng kích hoạt</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="4">—</td></tr>'}</tbody>
      </table>
      <p class="hint">Chính sách: <code>${esc(p.policy_id || "")}</code></p>
      ${p.policy_md ? `<details style="margin-top:8px"><summary>Chính sách đầy đủ (retraining-policy.md)</summary>
        <div class="md" style="max-height:360px;overflow:auto;margin-top:8px">${mdToHtml(p.policy_md)}</div></details>` : ""}
    </div>`;
}

// ── 3. Cài đặt ───────────────────────────────────────────────────────────
function renderInstall(g) {
  const ins = g.install || {};
  const steps = (ins.steps || []).map((s) => `<p style="margin:12px 0 4px"><strong>${esc(s.label)}</strong></p>${code(s.cmd)}`).join("");
  return `
    <div class="card">
      <h3>Cài đặt</h3>
      ${ins.note ? `<p class="hint">${esc(ins.note)}</p>` : ""}
      ${steps}
    </div>`;
}

// ── 4. Chạy huấn luyện / đánh giá theo thành phần ────────────────────────
function renderRun(g) {
  const comps = (g.run && g.run.components) || [];
  const blocks = comps.map((c) => `
    <div class="card" style="background:var(--panel-2)">
      <h3>${esc(c.name)}</h3>
      ${c.note ? `<p class="hint">${esc(c.note)}</p>` : ""}
      ${(c.steps || []).map((s) => `<p style="margin:10px 0 4px"><strong>${esc(s.label)}</strong></p>${code(s.cmd)}`).join("")}
    </div>`).join("");
  return `
    <div class="card">
      <h3>Chạy huấn luyện &amp; đánh giá</h3>
      <p class="hint">Lệnh trích nguyên văn từ README của bộ build chạy được — nguồn: <code>${esc(g.source)}</code></p>
      ${blocks}
    </div>`;
}

// ── 5. Tải bộ build fine-tuning ──────────────────────────────────────────
function renderBundle(g) {
  const b = g.bundle || {};
  const tree = (b.tree || []).map((f) => `<tr><td><code>${esc(f.path)}</code></td><td style="text-align:right;white-space:nowrap">${esc(f.size)}</td></tr>`).join("");
  const inc = (b.includes || []).map((x) => `<li><code>${esc(x)}</code></li>`).join("");
  return `
    <div class="card">
      <h3>Tải bộ finetuning về</h3>
      <p>Gói chứa toàn bộ code/config/notebook/benchmark/quantization thật của bộ build chạy được,
         để huấn luyện lại và chạy gate ngay tại máy của bạn.</p>
      <p class="row" style="margin:10px 0">
        <a class="run" href="${esc(b.download_url)}" download style="text-decoration:none">⬇ Tải ${esc(b.filename)} (${esc(b.size)})</a>
        <span class="hint">${b.file_count ?? "—"} file</span>
      </p>
      ${inc ? `<p style="margin-bottom:4px"><strong>Gồm:</strong></p><ul>${inc}</ul>` : ""}
      <p class="hint"><strong>Không kèm:</strong> ${esc(b.excludes)}
        ${b.data_hf ? ` — tải tại <a href="${esc(b.data_hf)}" target="_blank" rel="noopener">${esc(b.data_hf)}</a>` : ""}</p>
      ${tree ? `<details style="margin-top:8px"><summary>Nội dung gói (${b.file_count} file)</summary>
        <table style="margin-top:8px"><thead><tr><th>File</th><th style="text-align:right">Kích thước</th></tr></thead>
        <tbody>${tree}</tbody></table></details>` : ""}
    </div>`;
}
