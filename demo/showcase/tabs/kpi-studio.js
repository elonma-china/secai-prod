// Tab "KPI Studio" — mở rộng tab "KPI Bench" (vốn chỉ phát lại log 1 bản ghi hồ sơ gốc đóng
// băng) thành 1 studio đủ 5 mảng người vận hành cần: (1) catalog 19 KPI kèm công thức/ngưỡng,
// (2) duyệt phân trang bộ dữ liệu bench thật, (3) trạng thái sẵn sàng của 4 dịch vụ backend,
// (4) chạy bench từng KPI/cả nhóm/tất cả với log realtime, (5) lịch sử các lần chạy + biểu đồ.
//
// THẬT vs MÔ PHỎNG (ghi rõ để không ai hiểu nhầm):
//   - Catalog, danh sách/nội dung dataset, trạng thái dịch vụ, chạy bench (SSE log + kết quả):
//     TẤT CẢ đọc/gọi thật qua /showcase-api/kpi-studio/* và /showcase-api/kpi-bench/* (đã có
//     sẵn, đọc thẳng hồ sơ gốc — xem kpi_studio.py / kpi_bench.py).
//   - "Tải lên": THẬT — parse file .jsonl người dùng chọn ngay trên trình duyệt, không giả.
//   - "Nhập từ Hugging Face": MÔ PHỎNG — chưa có endpoint backend tải file từ HF Hub, form này
//     chỉ diễn hoạt luồng UI (progress giả) để chốt thiết kế trước khi làm phần nối API thật.
//   - "Lịch sử & biểu đồ": lưu trong localStorage của TRÌNH DUYỆT NÀY (chưa có DB phía server) —
//     mỗi lần bench thật qua mục (4) sẽ tự thêm 1 dòng lịch sử.

const esc = (t) => String(t ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
const fmtNum = (n) => (typeof n === "number" ? (Number.isInteger(n) ? String(n) : n.toFixed(2)) : String(n ?? "—"));
const LS_HISTORY = "secai_kpi_studio_history";
const LS_CUSTOM_DATASETS = "secai_kpi_studio_custom_datasets";

function enforcedPill(enforced) {
  if (enforced === true) return `<span class="pill ok">Gate thật</span>`;
  if (enforced === "partial") return `<span class="pill warn">Gate một phần</span>`;
  return `<span class="pill fail">Không gate</span>`;
}
function passPill(pass) {
  return pass ? `<span class="pill ok">Đạt</span>` : `<span class="pill fail">Chưa đạt</span>`;
}

// ── Shell: sub-nav 5 mục, mỗi mục lazy-render 1 lần ─────────────────────────
const SECTIONS = [
  { id: "overview", label: "Tổng quan (19 KPI)", fn: renderOverview },
  { id: "datasets", label: "Bộ dữ liệu", fn: renderDatasets },
  { id: "status", label: "Trạng thái dịch vụ", fn: renderServiceStatus },
  { id: "run", label: "Chạy bench", fn: renderRunBench },
  { id: "history", label: "Lịch sử & biểu đồ", fn: renderHistory },
];

export async function render(container, api) {
  container.innerHTML = `
    <nav class="row" id="ks-nav"></nav>
    <div id="ks-body"><p class="hint">Đang tải…</p></div>
  `;
  const nav = container.querySelector("#ks-nav");
  const body = container.querySelector("#ks-body");
  const rendered = new Set();

  // Cache catalog 1 lần — nhiều mục dùng chung (overview, run, history đối chiếu ngưỡng).
  const state = { catalog: null, api };
  state.catalog = await api.get("/showcase-api/kpi-studio/catalog");

  function open(id) {
    [...nav.children].forEach((b) => b.classList.toggle("active", b.dataset.id === id));
    [...body.children].forEach((el) => (el.hidden = el.dataset.id !== id));
    if (!rendered.has(id)) {
      rendered.add(id);
      const sec = SECTIONS.find((s) => s.id === id);
      const panel = body.querySelector(`[data-id="${id}"]`);
      sec.fn(panel, state).catch((err) => {
        panel.innerHTML = `<p class="error">Lỗi tải mục "${sec.label}": ${err.message}</p>`;
      });
    }
  }

  SECTIONS.forEach((s, i) => {
    const btn = document.createElement("button");
    btn.className = "run";
    btn.textContent = s.label;
    btn.dataset.id = s.id;
    btn.addEventListener("click", () => open(s.id));
    nav.appendChild(btn);

    const panel = document.createElement("section");
    panel.dataset.id = s.id;
    panel.hidden = i !== 0;
    body.appendChild(panel);
  });
  body.querySelector("p.hint")?.remove();
  open(SECTIONS[0].id);
}

// ── 1) Tổng quan — catalog 19 KPI ───────────────────────────────────────────
async function renderOverview(panel, state) {
  const { meta, groups, kpis } = state.catalog;

  panel.innerHTML = `
    <div class="card">
      <h3>19 KPI nghiệm thu secAI</h3>
      <p class="hint">Nguồn nội dung (công thức/ngưỡng/dataset): <code>${esc(meta.source)}</code></p>
      <div class="row">
        ${groups.map((g) => `<span class="pill warn">Nhóm ${g.id} — ${esc(g.name)}: ${g.count} KPI</span>`).join("")}
      </div>
      <p class="hint" style="margin-top:8px">
        + KPI thứ 12 không ngưỡng riêng <code>${esc(meta.kpi_thu_12_khong_nguong.code)}</code>
        (${esc(meta.kpi_thu_12_khong_nguong.note)})
      </p>
      <p class="hint">
        + Mã thứ 20 mồ côi <code>${esc(meta.ma_thu_20_mo_coi.code)}</code>
        (${esc(meta.ma_thu_20_mo_coi.note)})
      </p>
    </div>
    <div class="row" id="ks-ov-filters" style="margin-bottom:4px"></div>
    <div id="ks-ov-list"></div>
  `;

  const filters = panel.querySelector("#ks-ov-filters");
  const list = panel.querySelector("#ks-ov-list");
  const groupBtns = [{ id: "*", name: "Tất cả" }, ...groups].map((g) => {
    const btn = document.createElement("button");
    btn.className = "run";
    btn.textContent = g.id === "*" ? "Tất cả" : `Nhóm ${g.id}`;
    btn.dataset.g = g.id;
    return btn;
  });
  const search = document.createElement("input");
  search.placeholder = "Tìm theo mã hoặc tên KPI…";
  search.style.cssText = "flex:1;min-width:180px;padding:8px 10px;background:var(--panel-2);" +
    "border:1px solid var(--border);border-radius:8px;color:var(--text);font-family:inherit;font-size:13px";

  let activeGroup = "*";
  function draw() {
    groupBtns.forEach((b) => b.classList.toggle("active", b.dataset.g === activeGroup));
    const q = search.value.trim().toLowerCase();
    const rows = kpis.filter((k) =>
      (activeGroup === "*" || k.group === activeGroup) &&
      (!q || k.code.toLowerCase().includes(q) || k.name.toLowerCase().includes(q))
    );
    list.innerHTML = rows.length
      ? rows.map(kpiCard).join("")
      : `<p class="hint">Không có KPI khớp bộ lọc.</p>`;
  }
  groupBtns.forEach((b) => { b.addEventListener("click", () => { activeGroup = b.dataset.g; draw(); }); filters.appendChild(b); });
  filters.appendChild(search);
  search.addEventListener("input", draw);
  draw();
}

function kpiCard(k) {
  const deps = (k.depends_on || []).map((d) => `<code>${esc(d)}</code>`).join(", ") || "<span class=\"hint\">không</span>";
  const dsKeys = (k.dataset_keys || []).map((d) => `<code>${esc(d)}</code>`).join(", ");
  return `
    <div class="card" data-code="${esc(k.code)}">
      <h3><code>${esc(k.code)}</code> · ${esc(k.name)} <span class="hint">— nhóm ${esc(k.group)}</span></h3>
      <div class="row">${enforcedPill(k.enforced)}<strong>${esc(k.threshold_display)}</strong></div>
      <p><strong>Đạt/không đạt:</strong> ${esc(k.pass_rule)}</p>
      <p><strong>Tính toán:</strong> ${esc(k.formula)}</p>
      <p><strong>Dữ liệu:</strong> ${esc(k.dataset)} ${dsKeys ? `(xem tab Bộ dữ liệu: ${dsKeys})` : ""}</p>
      <p class="hint"><strong>Phụ thuộc:</strong> ${deps} · <strong>Script:</strong> <code>${esc(k.script)}</code></p>
      ${k.note ? `<p class="hint">⚠ ${esc(k.note)}</p>` : ""}
    </div>`;
}

// ── 2) Bộ dữ liệu — danh sách + phân trang + tải lên (thật) + HF import (mô phỏng) ─────────
async function renderDatasets(panel, state) {
  panel.innerHTML = `
    <div class="card" id="ks-ds-import">
      <h3>Nhập thêm bộ dữ liệu</h3>
      <div class="row">
        <div style="flex:1;min-width:260px">
          <p class="hint">Tải lên từ máy (.jsonl, mỗi dòng 1 JSON) — xử lý ngay trên trình duyệt, không cần server.</p>
          <input type="file" id="ks-upload-input" accept=".jsonl,.json,.txt" />
          <p class="hint" id="ks-upload-msg"></p>
        </div>
        <div style="flex:1;min-width:260px;border-left:1px solid var(--border);padding-left:16px">
          <p class="hint">Nhập từ Hugging Face — <strong>demo mô phỏng</strong>, chưa nối API HF Hub thật.</p>
          <div class="row">
            <input id="ks-hf-repo" placeholder="vd. DuyTa/Cyber_F1_v2" style="flex:1;min-width:160px;padding:8px 10px;background:var(--panel-2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-family:inherit;font-size:13px" />
            <button class="run" id="ks-hf-go">Nhập (mô phỏng)</button>
          </div>
          <div class="progress" id="ks-hf-progress" hidden><div></div></div>
          <p class="hint" id="ks-hf-msg"></p>
        </div>
      </div>
    </div>
    <div id="ks-ds-list"></div>
    <div id="ks-ds-viewer"></div>
  `;

  const listEl = panel.querySelector("#ks-ds-list");
  const viewerEl = panel.querySelector("#ks-ds-viewer");

  async function loadList() {
    const { datasets } = await state.api.get("/showcase-api/kpi-studio/datasets");
    const custom = readCustomDatasets();
    listEl.innerHTML = `
      <div class="card">
        <h3>Bộ dữ liệu dùng để bench (đọc thật từ hồ sơ gốc)</h3>
        <table>
          <thead><tr><th>Bộ dữ liệu</th><th>Mô tả</th><th>Số dòng</th><th>Dùng cho KPI</th><th></th></tr></thead>
          <tbody>
            ${datasets.map((d) => `
              <tr>
                <td><code>${esc(d.key)}</code><br/><span class="hint">${esc(d.label)}</span></td>
                <td>${esc(d.description)}</td>
                <td>${d.available ? fmtNum(d.row_count) : `<span class="pill fail">thiếu file</span>`}</td>
                <td>${d.used_by.map((c) => `<code>${esc(c)}</code>`).join(" ")}</td>
                <td><button class="run" data-view="${esc(d.key)}" ${d.available ? "" : "disabled"}>Xem</button></td>
              </tr>`).join("")}
            ${custom.map((d) => `
              <tr>
                <td><code>${esc(d.key)}</code><br/><span class="hint pill warn">tải lên / mô phỏng</span></td>
                <td>${esc(d.description)}</td>
                <td>${fmtNum(d.rows.length)}</td>
                <td><span class="hint">chưa gắn KPI nào</span></td>
                <td><button class="run" data-view-custom="${esc(d.key)}">Xem</button> <button class="run" data-del-custom="${esc(d.key)}">Xoá</button></td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>`;
    listEl.querySelectorAll("[data-view]").forEach((b) =>
      b.addEventListener("click", () => viewRemoteDataset(viewerEl, state, b.dataset.view)));
    listEl.querySelectorAll("[data-view-custom]").forEach((b) =>
      b.addEventListener("click", () => viewLocalDataset(viewerEl, b.dataset.viewCustom)));
    listEl.querySelectorAll("[data-del-custom]").forEach((b) =>
      b.addEventListener("click", () => { removeCustomDataset(b.dataset.delCustom); loadList(); }));
  }
  await loadList();

  // Tải lên thật: FileReader đọc .jsonl trên trình duyệt, không gửi server.
  panel.querySelector("#ks-upload-input").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    const msg = panel.querySelector("#ks-upload-msg");
    if (!file) return;
    msg.textContent = "Đang đọc…";
    try {
      const text = await file.text();
      const rows = text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
        try { return JSON.parse(l); } catch { return { _raw: l }; }
      });
      if (!rows.length) throw new Error("File rỗng hoặc không đọc được dòng nào.");
      const key = `upload_${Date.now()}`;
      saveCustomDataset({ key, description: `Tải lên từ "${file.name}" (${rows.length} dòng)`, rows });
      msg.textContent = `Đã nạp ${rows.length} dòng từ ${file.name}.`;
      await loadList();
    } catch (err) {
      msg.textContent = `Lỗi: ${err.message}`;
    }
  });

  // HF import — MÔ PHỎNG có chủ đích (xem ghi chú đầu file).
  panel.querySelector("#ks-hf-go").addEventListener("click", async () => {
    const repo = panel.querySelector("#ks-hf-repo").value.trim();
    const bar = panel.querySelector("#ks-hf-progress");
    const inner = bar.querySelector("div");
    const msg = panel.querySelector("#ks-hf-msg");
    if (!repo) { msg.textContent = "Nhập dataset id trên Hugging Face trước (vd DuyTa/Cyber_F1_v2)."; return; }
    bar.hidden = false; inner.style.width = "0%";
    msg.textContent = "Đang mô phỏng tải… (chưa nối API Hugging Face thật)";
    for (const pct of [20, 45, 70, 90, 100]) {
      await new Promise((r) => setTimeout(r, 220));
      inner.style.width = pct + "%";
    }
    const key = `hf_${Date.now()}`;
    saveCustomDataset({
      key, description: `Mô phỏng nhập từ Hugging Face: ${repo}`,
      rows: [{ _note: `Đây là dữ liệu MÔ PHỎNG — cần backend gọi HF Hub API thật để tải "${repo}".` }],
    });
    msg.textContent = `Xong (mô phỏng) — đã thêm mục "${key}" vào danh sách bên dưới.`;
    await loadList();
  });
}

function readCustomDatasets() {
  try { return JSON.parse(localStorage.getItem(LS_CUSTOM_DATASETS) || "[]"); } catch { return []; }
}
function saveCustomDataset(d) {
  try {
    const all = readCustomDatasets();
    all.push(d);
    localStorage.setItem(LS_CUSTOM_DATASETS, JSON.stringify(all));
  } catch { /* localStorage không khả dụng (private mode…) — bỏ qua êm */ }
}
function removeCustomDataset(key) {
  try {
    const all = readCustomDatasets().filter((d) => d.key !== key);
    localStorage.setItem(LS_CUSTOM_DATASETS, JSON.stringify(all));
  } catch { /* noop */ }
}

function rowsToTable(rows) {
  if (!rows.length) return `<p class="hint">Không có dòng nào.</p>`;
  const cols = Object.keys(rows[0]);
  const cell = (v) => {
    if (v == null) return "";
    if (typeof v === "object") { const s = JSON.stringify(v); return esc(s.length > 140 ? s.slice(0, 140) + "…" : s); }
    const s = String(v);
    return esc(s.length > 200 ? s.slice(0, 200) + "…" : s);
  };
  return `<table><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
    ${rows.map((r) => `<tr>${cols.map((c) => `<td>${cell(r[c])}</td>`).join("")}</tr>`).join("")}
  </tbody></table>`;
}

async function viewRemoteDataset(viewerEl, state, key, page = 1, pageSize = 20) {
  viewerEl.innerHTML = `<p class="hint">Đang tải…</p>`;
  const data = await state.api.get(`/showcase-api/kpi-studio/datasets/${encodeURIComponent(key)}?page=${page}&page_size=${pageSize}`);
  viewerEl.innerHTML = `
    <div class="card">
      <h3>${esc(data.label)} <span class="hint">(${esc(data.source)})</span></h3>
      <div class="row" id="ks-pg-ctrl"></div>
      ${rowsToTable(data.rows)}
      <div class="row" id="ks-pg-ctrl-bottom"></div>
    </div>`;
  const ctrl = () => `
    <button class="run" data-p="prev" ${data.page <= 1 ? "disabled" : ""}>‹ Trước</button>
    <span class="hint">Trang ${data.page}/${data.total_pages} — tổng ${data.total_rows} dòng</span>
    <button class="run" data-p="next" ${data.page >= data.total_pages ? "disabled" : ""}>Sau ›</button>`;
  ["#ks-pg-ctrl", "#ks-pg-ctrl-bottom"].forEach((sel) => { viewerEl.querySelector(sel).innerHTML = ctrl(); });
  viewerEl.querySelectorAll("[data-p]").forEach((b) =>
    b.addEventListener("click", () => viewRemoteDataset(viewerEl, state, key, data.page + (b.dataset.p === "next" ? 1 : -1), pageSize)));
  viewerEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function viewLocalDataset(viewerEl, key) {
  const d = readCustomDatasets().find((x) => x.key === key);
  if (!d) { viewerEl.innerHTML = `<p class="error">Không tìm thấy bộ dữ liệu cục bộ "${esc(key)}".</p>`; return; }
  viewerEl.innerHTML = `
    <div class="card">
      <h3>${esc(d.description)} <span class="pill warn">cục bộ (trình duyệt này)</span></h3>
      ${rowsToTable(d.rows.slice(0, 200))}
      ${d.rows.length > 200 ? `<p class="hint">… còn ${d.rows.length - 200} dòng, chỉ hiện 200 dòng đầu.</p>` : ""}
    </div>`;
  viewerEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ── 3) Trạng thái dịch vụ — kiểm tra thật LLM/embedding/reranker/Qdrant ─────
async function renderServiceStatus(panel, state) {
  panel.innerHTML = `
    <div class="card">
      <div class="row">
        <h3 style="margin:0">4 dịch vụ backend cần để bench</h3>
        <button class="run" id="ks-status-refresh">Kiểm tra lại</button>
      </div>
      <p class="hint">Gọi thật: LLM <code>GET /models</code>; embedding <code>POST /v1/embeddings</code>;
        reranker <code>POST /v1/rerank</code> (đúng tên model cấu hình — bắt được lỗi "serving load
        nhầm model mặc định" theo đúng cảnh báo trong CLAUDE.md); Qdrant <code>GET /collections/&lt;tên&gt;</code>.</p>
      <div id="ks-status-body"><p class="hint">Đang kiểm tra…</p></div>
    </div>`;
  const body = panel.querySelector("#ks-status-body");
  panel.querySelector("#ks-status-refresh").addEventListener("click", () => load());

  async function load() {
    body.innerHTML = `<p class="hint">Đang kiểm tra…</p>`;
    const data = await state.api.get("/showcase-api/kpi-studio/service-status");
    const banner = data.ready_to_bench
      ? `<p><span class="pill ok">SẴN SÀNG</span> cả 4 dịch vụ phản hồi OK lúc ${esc(data.checked_at)}.</p>`
      : `<p><span class="pill fail">CHƯA SẴN SÀNG</span> ít nhất 1 dịch vụ lỗi lúc ${esc(data.checked_at)} — xem chi tiết bên dưới trước khi bấm chạy bench.</p>`;
    body.innerHTML = banner + `<div class="row">${data.checks.map(statusCard).join("")}</div>`;
  }
  await load();
}

function statusCard(c) {
  const rows = [];
  if (c.url) rows.push(`URL: <code>${esc(c.url)}</code>`);
  if (c.model) rows.push(`Model: <code>${esc(c.model)}</code>`);
  if (c.collection) rows.push(`Collection: <code>${esc(c.collection)}</code>` + (c.points_count != null ? ` (${fmtNum(c.points_count)} điểm)` : ""));
  if (c.latency_ms != null) rows.push(`Độ trễ: ${fmtNum(c.latency_ms)} ms`);
  if (c.http_status != null) rows.push(`HTTP: ${c.http_status}`);
  if (c.detail) rows.push(`<span class="hint">${esc(c.detail)}</span>`);
  return `
    <div class="card" style="flex:1;min-width:220px">
      <h3>${esc(c.role.toUpperCase())} ${c.ok ? `<span class="pill ok">OK</span>` : `<span class="pill fail">LỖI</span>`}</h3>
      ${rows.map((r) => `<p class="hint">${r}</p>`).join("")}
    </div>`;
}

// ── 4) Chạy bench — từng KPI / cả nhóm / tất cả 19, log realtime ────────────
async function renderRunBench(panel, state) {
  const { groups, kpis } = state.catalog;
  panel.innerHTML = `
    <div class="card">
      <h3>Chọn phạm vi chạy</h3>
      <div class="row" id="ks-scope-btns"></div>
      <div class="row" id="ks-scope-detail"></div>
      <div class="row"><button class="run" id="ks-run-go">Chạy</button><span class="hint run-elapsed" id="ks-run-elapsed"></span></div>
      <div class="progress" id="ks-run-progress" hidden><div></div></div>
    </div>
    <div class="card">
      <h3>Log realtime</h3>
      <pre class="run-log" id="ks-run-log"></pre>
    </div>
    <div id="ks-run-results"></div>
  `;
  const scopeBtns = panel.querySelector("#ks-scope-btns");
  const scopeDetail = panel.querySelector("#ks-scope-detail");
  const goBtn = panel.querySelector("#ks-run-go");
  const logEl = panel.querySelector("#ks-run-log");
  const progress = panel.querySelector("#ks-run-progress");
  const progressInner = progress.querySelector("div");
  const elapsedEl = panel.querySelector("#ks-run-elapsed");
  const resultsEl = panel.querySelector("#ks-run-results");

  let scope = "single"; // single | group | all
  const SCOPES = [
    { id: "single", label: "Từng KPI" },
    { id: "group", label: "Cả 1 nhóm" },
    { id: "all", label: "Tất cả 19" },
  ];
  SCOPES.forEach((s) => {
    const b = document.createElement("button");
    b.className = "run"; b.textContent = s.label; b.dataset.s = s.id;
    b.addEventListener("click", () => { scope = s.id; drawScope(); });
    scopeBtns.appendChild(b);
  });
  function drawScope() {
    [...scopeBtns.children].forEach((b) => b.classList.toggle("active", b.dataset.s === scope));
    if (scope === "single") {
      scopeDetail.innerHTML = `<select id="ks-pick-kpi" style="padding:8px 10px;background:var(--panel-2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-family:inherit">
        ${kpis.map((k) => `<option value="${esc(k.code)}">${esc(k.code)} — ${esc(k.name)}</option>`).join("")}
      </select>`;
    } else if (scope === "group") {
      scopeDetail.innerHTML = groups.map((g) => `<label class="hint"><input type="radio" name="ks-pick-group" value="${g.id}" ${g.id === "A" ? "checked" : ""}/> Nhóm ${g.id} — ${esc(g.name)} (${g.count})</label>`).join(" ");
    } else {
      scopeDetail.innerHTML = `<p class="hint">Chạy tuần tự cả 19 KPI theo thứ tự A → B → C. Có thể mất vài phút.</p>`;
    }
  }
  drawScope();

  function testsForScope() {
    if (scope === "single") return kpis.filter((k) => k.code === panel.querySelector("#ks-pick-kpi").value);
    if (scope === "group") {
      const g = panel.querySelector('input[name="ks-pick-group"]:checked')?.value || "A";
      return kpis.filter((k) => k.group === g);
    }
    return kpis;
  }

  goBtn.addEventListener("click", async () => {
    const tests = testsForScope();
    if (!tests.length) return;
    goBtn.disabled = true;
    logEl.textContent = "";
    resultsEl.innerHTML = "";
    progress.hidden = false;
    progressInner.style.width = "0%";
    const started = performance.now();
    const ticker = setInterval(() => {
      const s = Math.floor((performance.now() - started) / 1000);
      elapsedEl.textContent = `đang chạy… ${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    }, 200);

    const results = [];
    for (let i = 0; i < tests.length; i++) {
      const t = tests[i];
      logEl.textContent += `\n===== [${i + 1}/${tests.length}] ${t.code} — ${t.name} =====\n`;
      logEl.scrollTop = logEl.scrollHeight;
      try {
        const r = await runOneTestStream(t.code, logEl, (frac) => {
          progressInner.style.width = Math.min(99, ((i + frac) / tests.length) * 100) + "%";
        });
        results.push(r);
      } catch (err) {
        logEl.textContent += `LỖI: ${err.message}\n`;
        results.push({ test_id: t.code, name: t.name, error: err.message });
      }
      resultsEl.innerHTML = renderRunSummary(results);
    }
    progressInner.style.width = "100%";
    clearInterval(ticker);
    elapsedEl.textContent = "";
    goBtn.disabled = false;
    saveHistoryRun({ scope, results, at: new Date().toISOString() });
    resultsEl.innerHTML = renderRunSummary(results) + `<p class="hint">Đã lưu vào mục "Lịch sử & biểu đồ".</p>`;
  });
}

// 1 test = 1 kết nối SSE tới endpoint run-stream THẬT đã có sẵn (kpi_bench.py) — không mô phỏng gì thêm ở đây.
function runOneTestStream(testId, logEl, onProgress) {
  return new Promise((resolve, reject) => {
    const es = new EventSource(`/showcase-api/kpi-bench/tests/${encodeURIComponent(testId)}/run-stream`);
    es.addEventListener("line", (e) => {
      const d = JSON.parse(e.data);
      logEl.textContent += d.text + "\n";
      logEl.scrollTop = logEl.scrollHeight;
      if (typeof d.pct === "number") onProgress(d.pct / 100);
    });
    es.addEventListener("done", (e) => { es.close(); resolve(JSON.parse(e.data)); });
    es.onerror = () => { es.close(); reject(new Error("Mất kết nối SSE khi chạy " + testId)); };
  });
}

function renderRunSummary(results) {
  const ok = results.filter((r) => r.pass).length;
  const total = results.filter((r) => !r.error).length;
  return `
    <div class="card">
      <h3>Kết quả (${ok}/${total} đạt${results.some((r) => r.error) ? `, ${results.filter((r) => r.error).length} lỗi` : ""})</h3>
      <table><thead><tr><th>Mã</th><th>Tên</th><th>Giá trị</th><th>Ngưỡng</th><th></th></tr></thead><tbody>
        ${results.map((r) => r.error
          ? `<tr><td><code>${esc(r.test_id)}</code></td><td>${esc(r.name)}</td><td colspan="2" class="error">${esc(r.error)}</td><td></td></tr>`
          : `<tr><td><code>${esc(r.test_id)}</code></td><td>${esc(r.name)}</td><td>${fmtNum(r.value)} ${esc(r.unit || "")}</td><td>${esc(r.threshold)}</td><td>${passPill(r.pass)}</td></tr>`
        ).join("")}
      </tbody></table>
    </div>`;
}

// ── 5) Lịch sử & biểu đồ — localStorage của trình duyệt này ─────────────────
function readHistory() {
  try { return JSON.parse(localStorage.getItem(LS_HISTORY) || "[]"); } catch { return []; }
}
function saveHistoryRun(run) {
  try {
    const all = readHistory();
    all.unshift(run); // mới nhất lên đầu
    localStorage.setItem(LS_HISTORY, JSON.stringify(all.slice(0, 50))); // giữ tối đa 50 lần chạy gần nhất
  } catch { /* localStorage không khả dụng — bỏ qua êm, chỉ mất tính năng lịch sử */ }
}

async function renderHistory(panel, state) {
  const runs = readHistory();
  if (!runs.length) {
    panel.innerHTML = `<div class="card"><p class="hint">Chưa có lần chạy nào được lưu. Sang tab "Chạy bench" để chạy — mỗi lần chạy xong sẽ tự thêm 1 dòng ở đây (lưu trong trình duyệt này, chưa có DB phía server).</p></div>`;
    return;
  }
  // Ngưỡng số THẬT (để vẽ % cột) lấy từ catalog — result.threshold trả về từ kpi-bench là chuỗi
  // hiển thị đã format sẵn (vd "20–35", "> 96"), không parse ngược ra số được.
  const thrByCode = new Map(state.catalog.kpis.map((k) => [k.code, k]));

  panel.innerHTML = `<div class="card"><h3>${runs.length} lần chạy gần nhất</h3><div id="ks-hist-list"></div></div>`;
  const list = panel.querySelector("#ks-hist-list");
  list.innerHTML = runs.map((run, i) => {
    const ok = run.results.filter((r) => r.pass).length;
    const total = run.results.filter((r) => !r.error).length;
    const when = new Date(run.at).toLocaleString("vi-VN");
    return `
      <details class="hist-log" ${i === 0 ? "open" : ""}>
        <summary>${esc(when)} — phạm vi <strong>${esc(run.scope)}</strong> — ${ok}/${total} đạt
          <span class="pill ${ok === total && total > 0 ? "ok" : "warn"}">${ok}/${total}</span></summary>
        ${historyBarsChart(run.results, thrByCode)}
        ${renderRunSummary(run.results)}
      </details>`;
  }).join("");
}

// Tái dùng .hist-bars/.hist-bar sẵn có trong style.css (đã dùng cho tab "Huấn luyện lại")
// thay vì tự bịa 1 hệ class biểu đồ mới. Chiều cao cột = % so ngưỡng (100% = vừa chạm ngưỡng),
// kẹp trong [4%, 100%] để cột luôn thấy được kể cả khi vượt xa hoặc gần 0.
function historyBarsChart(results, thrByCode) {
  const bars = results.filter((r) => !r.error && typeof r.value === "number").map((r) => {
    const cat = thrByCode.get(r.test_id);
    const thrNum = cat ? (Array.isArray(cat.threshold) ? cat.threshold[1] : Number(cat.threshold)) : NaN;
    const pct = Number.isFinite(thrNum) && thrNum
      ? Math.max(4, Math.min(100, (Number(r.value) / thrNum) * 100))
      : 50; // không tra được ngưỡng số (không nên xảy ra vì catalog phủ đủ 19 mã) — vẽ cột giữa thay vì NaN
    return `<div class="hist-bar"><i class="${r.pass ? "" : "fail"}" style="height:${pct.toFixed(0)}%"></i><span>${esc(r.test_id.replace("R-", ""))}</span></div>`;
  }).join("");
  return bars ? `<div class="hist-bars">${bars}</div><p class="hint">Chiều cao cột = % so với ngưỡng (100% = vừa chạm ngưỡng).</p>` : "";
}
