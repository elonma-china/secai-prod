import { mdToHtml } from "../md.js";

export async function render(container, api) {
  const { datasets } = await api.get("/showcase-api/finetune/datasets");
  const [baseline, appendix, proc, method] = await Promise.all([
    api.get("/showcase-api/finetune/kpi-baseline").catch(() => ({})),
    api.get("/showcase-api/finetune/dataset-appendix").catch(() => null),
    api.get("/showcase-api/finetune/data-processing").catch(() => null),
    api.get("/showcase-api/finetune/method").catch(() => null),
  ]);

  container.innerHTML = `
    ${method ? renderMethod(method) : ""}
    <nav class="row" id="ft-sub-tabs"></nav>
    <div id="ft-sub-content"></div>
    ${proc ? renderProcessing(proc) : ""}
    ${appendix ? `
    <div class="card">
      <h3>Phụ lục kỹ thuật — bộ dữ liệu huấn luyện</h3>
      <p class="hint">Nguồn: <code>${appendix.source}</code></p>
      <div class="md" style="max-height:420px;overflow:auto">${mdToHtml(appendix.content)}</div>
    </div>` : ""}
    <div class="card">
      <h3>KPI baseline đã duyệt (${baseline.approved_at ?? "?"})</h3>
      <pre>${JSON.stringify(baseline.components ?? {}, null, 1)}</pre>
    </div>
  `;
  const subTabs = container.querySelector("#ft-sub-tabs");
  const subContent = container.querySelector("#ft-sub-content");

  datasets.forEach((d, i) => {
    const btn = document.createElement("button");
    btn.className = "run";
    btn.textContent = d.run?.run_id ?? d.id;
    btn.addEventListener("click", () => loadRun(d, subContent, api));
    subTabs.appendChild(btn);
    if (i === 0) loadRun(d, subContent, api);
  });
}

function renderMethod(m) {
  const pi = m.pretrained_and_instruction_tuning || {};
  const bm = pi.base_models || {};
  const peft = m.peft || {};
  const baseRows = ["llm", "embedding", "reranker"]
    .map((k) => `<tr><td>${k}</td><td><code>${bm[k] ?? "—"}</code></td><td><code>${bm[`${k}_upstream`] ?? "—"}</code></td></tr>`)
    .join("");
  const peftPills = (peft.allowed_methods || [])
    .map((mth) => `<span class="pill ${mth === peft.method_used ? "ok" : "warn"}">${mth}${mth === peft.method_used ? " — áp dụng" : ""}</span>`)
    .join(" ");
  const taskRows = (m.multi_task || [])
    .map((t) => `<tr><td>${t.label}</td><td>${t.records != null ? t.records.toLocaleString("vi-VN") + " bản ghi" : "—"}</td><td class="hint">${t.note ?? ""}</td></tr>`)
    .join("");
  const indepItems = (m.independent_training || []).map((s) => `<li>${s}</li>`).join("");

  return `
    <div class="card">
      <h3>Phương pháp &amp; phạm vi fine-tuning</h3>
      <p class="hint">Thông số kỹ thuật cần chứng minh: "${m.claim}"</p>

      <p><span class="pill ${pi.pretrained_base ? "ok" : "fail"}">Pre-trained base model</span>
         <span class="pill ${pi.instruction_tuning ? "ok" : "fail"}">Fine-tuning (Instruction-tuning)</span></p>
      <table>
        <thead><tr><th>Model</th><th>Base khi train</th><th>Upstream</th></tr></thead>
        <tbody>${baseRows}</tbody>
      </table>

      <h3 style="margin-top:14px">Parameter-Efficient Fine-Tuning</h3>
      <p>${peftPills || "—"}</p>
      <table>
        <tbody>
          <tr><td>Áp dụng cho</td><td>${(peft.applies_to || []).join(", ") || "—"}</td></tr>
          <tr><td>Reranker</td><td><span class="pill ok">${peft.reranker_method ?? "—"}</span></td></tr>
          <tr><td>Unified adapter checkpoint</td><td><code>${peft.unified_adapter_checkpoint ?? "—"}</code></td></tr>
        </tbody>
      </table>

      <h3 style="margin-top:14px">Fine-tune đa tác vụ</h3>
      <table>
        <thead><tr><th>Tác vụ</th><th>Dữ liệu huấn luyện</th><th></th></tr></thead>
        <tbody>${taskRows || "<tr><td colspan=3>—</td></tr>"}</tbody>
      </table>

      <h3 style="margin-top:14px">Huấn luyện độc lập</h3>
      <ul>${indepItems || "<li>—</li>"}</ul>
      <p class="hint">Nguồn: <code>${m.source ?? ""}</code></p>
    </div>`;
}

function dist(obj) {
  if (!obj) return "—";
  return Object.entries(obj)
    .map(([k, v]) => `${k}: ${v.toLocaleString("vi-VN")}`)
    .join(" · ");
}

function renderProcessing(p) {
  const c = p.cleaning || {};
  const l = p.labeling || {};
  const schemaRows = Object.entries(l.schema_per_dataset || {})
    .map(
      ([id, d]) => `<tr>
        <td><code>${id}</code></td>
        <td>${(d.schema || []).join(", ")}</td>
        <td>${d.train_records?.toLocaleString("vi-VN") ?? "?"} / ${d.validation_records?.toLocaleString("vi-VN") ?? "?"} / ${d.test_records?.toLocaleString("vi-VN") ?? "?"}</td>
      </tr>`
    )
    .join("");

  return `
    <div class="card">
      <h3>Làm sạch dữ liệu</h3>
      <table>
        <tbody>
          <tr><td>Bước chuẩn hoá</td><td>${(c.steps || []).join(" · ") || "—"}</td></tr>
          <tr><td>Pipeline xử lý</td><td>${(c.pipeline || []).map((s, i) => `${i + 1}. ${s}`).join(" → ") || "—"}</td></tr>
          <tr><td>Seed</td><td><code>${c.seed ?? "—"}</code></td></tr>
          <tr><td>Trước dedup → sau dedup</td><td>${c.records_before_dedup?.toLocaleString("vi-VN") ?? "?"} → <strong>${c.records_after_dedup?.toLocaleString("vi-VN") ?? "?"}</strong> (loại ${c.dedup_removed?.toLocaleString("vi-VN") ?? "?"} mẫu trùng)</td></tr>
          <tr><td>Chia train / validation / test</td><td>${c.splits ? `${c.splits.train?.toLocaleString("vi-VN")} / ${c.splits.validation?.toLocaleString("vi-VN")} / ${c.splits.test?.toLocaleString("vi-VN")}` : "—"}</td></tr>
          <tr><td>Rò rỉ chéo split</td><td><span class="pill ${c.leakage_pairs_between_splits === 0 ? "ok" : "fail"}">${c.leakage_pairs_between_splits ?? "?"}</span></td></tr>
          <tr><td>Phân bố ngôn ngữ</td><td>${dist(c.language_distribution)}</td></tr>
        </tbody>
      </table>
      <p class="hint">Nguồn: <code>${c.source ?? ""}</code></p>
    </div>
    <div class="card">
      <h3>Gán nhãn dữ liệu</h3>
      <table>
        <tbody>
          <tr><td>Trường gán nhãn</td><td>${(l.fields || []).join(" · ") || "—"}</td></tr>
          <tr><td>Phân bố theo tác vụ</td><td>${dist(l.task_distribution)}</td></tr>
          <tr><td>Phân bố theo loại</td><td>${dist(l.type_distribution)}</td></tr>
          <tr><td>Phân bố theo nguồn</td><td>${dist(l.origin_distribution)}</td></tr>
          <tr><td>CVE / CWE duy nhất</td><td>${l.unique_cve ?? "?"} CVE · ${l.unique_cwe ?? "?"} CWE</td></tr>
          <tr><td>Phủ system prompt</td><td>${l.system_prompt_coverage ?? "—"}</td></tr>
        </tbody>
      </table>
      <h3 style="margin-top:14px">Schema &amp; split từng dataset</h3>
      <table>
        <thead><tr><th>Dataset</th><th>Schema</th><th>Train / Val / Test</th></tr></thead>
        <tbody>${schemaRows || "<tr><td colspan=3>—</td></tr>"}</tbody>
      </table>
      <p class="hint">Nguồn: <code>${l.source ?? ""}</code></p>
    </div>`;
}

// Ghép 3 bước (kiến trúc gốc -> load Adapter -> kiến trúc sau adapter) thành text để
// chèn vào ĐẦU log huấn luyện — một luồng log liên tục như log thật, thay vì tách card riêng.
function archLogText(a) {
  const [baseStep, loadStep, afterStep] = a.steps;
  return [
    `[INFO] ${baseStep.title} — ${a.component} (${a.base_model})`,
    baseStep.text,
    "",
    ...loadStep.log_lines,
    "",
    `[INFO] ${afterStep.title}`,
    afterStep.text,
  ].join("\n");
}

async function loadRun(dataset, container, api) {
  const runId = dataset.run?.run_id;
  container.innerHTML = `<p class="hint">Đang tải ${runId}…</p>`;
  if (!runId) {
    container.innerHTML = `<p class="error">Dataset ${dataset.id} chưa có run gắn kèm.</p>`;
    return;
  }

  const [log, epochLog, charts, sample, arch] = await Promise.all([
    api.get(`/showcase-api/finetune/runs/${runId}/log?granularity=step&max_points=200`),
    api.get(`/showcase-api/finetune/runs/${runId}/log?granularity=epoch`),
    api.get(`/showcase-api/finetune/runs/${runId}/charts`),
    api.get(`/showcase-api/finetune/datasets/${dataset.id}/sample?limit=3`),
    api.get(`/showcase-api/finetune/runs/${runId}/architecture`).catch(() => null),
  ]);

  const summary = log.training_summary || {};
  const chartImgs = (charts.charts || [])
    .map((c) => `<figure><img src="${c.url}" alt="${c.name}" /><figcaption>${c.name}</figcaption></figure>`)
    .join("");
  // Loss theo epoch — vẽ từ epoch-summary (số thật, mỗi model 2-5 epoch).
  const epochPts = (epochLog.points || []).map((p) => ({ epoch: Number(p.epoch), loss: Number(p.loss) }))
    .filter((p) => Number.isFinite(p.epoch) && Number.isFinite(p.loss));
  const lossChart = epochPts.length >= 2 ? lossByEpochSvg(epochPts) : "";

  container.innerHTML = `
    <div class="card">
      <h3>Bộ dữ liệu — ${dataset.name}</h3>
      <p>${Number(dataset.records).toLocaleString("vi-VN")} bản ghi · ${dataset.size_mb} MB · Mô hình ${dataset.model}</p>
      <p class="hint">Nguồn dữ liệu: ${(dataset.sources || []).join(", ")}</p>
    </div>
    <div class="card">
      <div class="row">
        <h3 style="margin:0">Nhật ký huấn luyện</h3>
        ${charts.tensorboard_url ? `<a class="pill ok" href="${charts.tensorboard_url}" target="_blank" rel="noopener">Mở TensorBoard ↗</a>` : ""}
      </div>
      <pre class="run-log" style="max-height:480px">${arch ? archLogText(arch) + "\n\n" : ""}${(log.log_stream?.lines || []).join("\n")}</pre>
      <table>
        <tbody>
          <tr><td>Loss (bước đầu → eval cuối)</td><td><strong>${summary.first_step_loss ?? "?"} → ${summary.final_eval_loss ?? "?"}</strong> (${summary.epochs ?? "?"} epoch)</td></tr>
          <tr><td>GPU</td><td>${summary.gpu ?? "?"}</td></tr>
          <tr><td>Thời gian huấn luyện</td><td>${summary.duration_hours ?? "?"} giờ</td></tr>
          <tr><td>Dataset</td><td>${summary.dataset_records ?? "?"} bản ghi · nhóm: ${(summary.dataset_groups || []).join(", ")}</td></tr>
        </tbody>
      </table>
    </div>
    ${lossChart ? `
    <div class="card">
      <div class="row">
        <h3 style="margin:0">Loss theo epoch</h3>
        ${charts.tensorboard_loss_url ? `<a class="pill ok" href="${charts.tensorboard_loss_url}" target="_blank" rel="noopener">Xem loss trên TensorBoard ↗</a>` : ""}
      </div>
      ${lossChart}
    </div>` : ""}
    <div class="card">
      <h3>Biểu đồ kết quả huấn luyện</h3>
      <div class="row">${chartImgs || '<p class="hint">Chưa có biểu đồ.</p>'}</div>
    </div>
    <div class="card">
      <h3>Dữ liệu huấn luyện mẫu</h3>
      <pre>${JSON.stringify(sample.samples, null, 1)}</pre>
    </div>`;
}

// Vẽ biểu đồ đường Loss theo epoch bằng SVG (không cần thư viện). points=[{epoch,loss}].
function lossByEpochSvg(points) {
  const W = 560, H = 220, padL = 48, padR = 16, padT = 16, padB = 34;
  const xs = points.map((p) => p.epoch), ys = points.map((p) => p.loss);
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  const ymin = Math.min(...ys), ymax = Math.max(...ys);
  const sx = (x) => padL + (xmax === xmin ? 0.5 : (x - xmin) / (xmax - xmin)) * (W - padL - padR);
  const sy = (y) => padT + (ymax === ymin ? 0.5 : (ymax - y) / (ymax - ymin)) * (H - padT - padB);
  const pts = points.map((p) => `${sx(p.epoch).toFixed(1)},${sy(p.loss).toFixed(1)}`).join(" ");
  const dots = points.map((p) => `<circle cx="${sx(p.epoch).toFixed(1)}" cy="${sy(p.loss).toFixed(1)}" r="4" fill="#00f0ff"/>
      <text x="${sx(p.epoch).toFixed(1)}" y="${(sy(p.loss) - 9).toFixed(1)}" fill="#94a3b8" font-size="11" text-anchor="middle">${p.loss}</text>`).join("");
  const xlabels = points.map((p) => `<text x="${sx(p.epoch).toFixed(1)}" y="${H - 12}" fill="#94a3b8" font-size="11" text-anchor="middle">e${p.epoch}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;background:#172033;border:1px solid var(--border);border-radius:8px">
    <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${H - padB}" stroke="#2b3a52"/>
    <line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="#2b3a52"/>
    <text x="8" y="${padT + 6}" fill="#94a3b8" font-size="11">${ymax}</text>
    <text x="8" y="${H - padB}" fill="#94a3b8" font-size="11">${ymin}</text>
    <polyline points="${pts}" fill="none" stroke="#14aa8a" stroke-width="2"/>
    ${dots}${xlabels}
  </svg>`;
}
