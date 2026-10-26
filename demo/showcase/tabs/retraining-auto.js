// Tab "Retraining tự động" — điều khiển + theo dõi THẬT pipeline MLOps (mlops/trigger_api trên
// host, gateway reverse-proxy /showcase-api/retraining/*). Khác tab "Huấn luyện lại" (chỉ hiển
// thị chính sách/lịch sử tĩnh): tab này có nút kích hoạt thật + log từng giai đoạn realtime (SSE).
//
// Mutation (smoke / cả vòng đời) CẦN X-API-Key (MLOPS_TRIGGER_API_KEY trên host) — nhập 1 lần,
// lưu localStorage của trình duyệt này. Xem/đọc lịch + log không cần key.

const esc = (t) => String(t ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
const LS_BASE = "secai_retraining_base";
const LS_KEY = "secai_mlops_trigger_api_key";

// Mặc định same-origin qua reverse-proxy gateway (có sau khi rebuild gateway). Trước rebuild,
// hoặc khi mở trang từ máy khác, nhập trực tiếp URL trigger_api (vd http://localhost:8877).
function base() {
  try { return (localStorage.getItem(LS_BASE) || "/showcase-api/retraining").replace(/\/$/, ""); }
  catch { return "/showcase-api/retraining"; }
}
function apiKey() { try { return localStorage.getItem(LS_KEY) || ""; } catch { return ""; } }

async function jget(path) {
  // no-store: các endpoint status/runs đổi liên tục — không để browser trả response cũ đã cache.
  const r = await fetch(base() + path, { headers: { Accept: "application/json" }, cache: "no-store" });
  const b = await r.json().catch(() => null);
  if (!r.ok) throw new Error((b && (b.detail || b.message)) || `HTTP ${r.status}`);
  return b;
}
async function jpost(path, body) {
  const k = apiKey();
  if (!k) throw new Error("Chưa nhập X-API-Key (mục Cấu hình bên dưới).");
  const r = await fetch(base() + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json", "X-API-Key": k },
    body: JSON.stringify(body || {}),
  });
  const b = await r.json().catch(() => null);
  if (!r.ok) throw new Error((b && (b.detail || b.message)) || `HTTP ${r.status}`);
  return b;
}

const STATE_PILL = {
  pending: ["", "chờ"], running: ["warn", "đang chạy"], done: ["ok", "xong"],
  skipped: ["", "bỏ qua"], failed: ["fail", "lỗi"],
};
function pill(state) {
  const [cls, label] = STATE_PILL[state] || ["", state];
  return `<span class="pill ${cls}">${esc(label)}</span>`;
}

let evtSource = null;

export async function render(container) {
  container.innerHTML = `
    <div class="card">
      <h3>Retraining tự động — điều khiển vòng đời</h3>
      <p class="hint">Kích hoạt & theo dõi THẬT pipeline: Thu thập CVE mới → Phát hiện suy giảm →
        Tự động Finetune/DPO → Quality Gate → Triển khai. Nút kích hoạt cần API key; xem lịch/log không cần.</p>
      <div id="ra-conn"></div>
    </div>
    <div class="card"><h3>Lịch đối soát</h3><div id="ra-schedule"><p class="hint">Đang tải…</p></div></div>
    <div class="card">
      <h3>Cấu hình tham số retraining</h3>
      <p class="hint">Thông số để chạy retraining: HF token (quyền ghi model), judge (chấm điểm độc lập),
        nhịp đối soát. Giá trị secret KHÔNG hiển thị lại (chỉ báo đã cấu hình ✓ / chưa ✗); lưu vào
        <code>~/.config/secai-mlops/mlops.env</code> trên host. Sửa cần API key; áp dụng cho lần chạy kế tiếp.</p>
      <div id="ra-config"><p class="hint">Đang tải…</p></div>
      <div id="ra-config-msg"></div>
    </div>
    <div class="card">
      <h3>Kích hoạt</h3>
      <div id="ra-actions"></div>
      <div id="ra-msg"></div>
    </div>
    <div class="card">
      <h3>Tiến trình theo giai đoạn <span id="ra-run-label" class="hint"></span></h3>
      <div id="ra-stages"><p class="hint">Chưa có lần chạy nào đang theo dõi. Bấm "Chạy smoke" hoặc chọn 1 run ở lịch sử.</p></div>
    </div>
    <div class="card"><h3>Lịch sử các lần chạy</h3><div id="ra-runs"><p class="hint">Đang tải…</p></div></div>
    <div class="card">
      <h3>Cấu hình kết nối</h3>
      <label class="hint">Base URL của service kích hoạt (mặc định same-origin qua gateway proxy):</label>
      <input id="ra-base" style="width:100%;box-sizing:border-box" placeholder="/showcase-api/retraining hoặc http://localhost:8877">
      <label class="hint" style="margin-top:8px;display:block">X-API-Key (MLOPS_TRIGGER_API_KEY trên host) — lưu localStorage trình duyệt này:</label>
      <input id="ra-key" type="password" style="width:100%;box-sizing:border-box" placeholder="dán API key…">
      <div style="margin-top:8px"><button id="ra-save" type="button">Lưu cấu hình</button>
        <span id="ra-save-msg" class="hint"></span></div>
    </div>`;

  // ── cấu hình ────────────────────────────────────────────────────────────────
  const baseInput = container.querySelector("#ra-base");
  const keyInput = container.querySelector("#ra-key");
  try { baseInput.value = localStorage.getItem(LS_BASE) || ""; keyInput.value = localStorage.getItem(LS_KEY) || ""; } catch {}
  container.querySelector("#ra-save").addEventListener("click", () => {
    const m = container.querySelector("#ra-save-msg");
    try {
      if (baseInput.value.trim()) localStorage.setItem(LS_BASE, baseInput.value.trim());
      else localStorage.removeItem(LS_BASE);
      localStorage.setItem(LS_KEY, keyInput.value.trim());
      m.textContent = "Đã lưu.";
    } catch { m.textContent = "Không lưu được (localStorage bị chặn) — vẫn dùng trong phiên này."; }
    checkConn(container); renderActions(container);
  });

  renderActions(container);
  await checkConn(container);
  await loadSchedule(container);
  await loadConfig(container);
  await loadRuns(container);
  await autoTrackLatest(container);
}

// Tự mở run MỚI NHẤT khi vào tab — để luôn thấy trạng thái/log/chỗ fail của lần chạy gần nhất
// mà không phải bấm gì.
async function autoTrackLatest(container) {
  try {
    const d = await jget("/runs?limit=1");
    if (d.runs && d.runs.length) await track(container, d.runs[0].run_id);
  } catch { /* không có run hoặc chưa kết nối — giữ nguyên placeholder */ }
}

const CADENCES = ["monthly", "weekly", "daily", "quarterly"];

async function loadConfig(container) {
  const el = container.querySelector("#ra-config");
  try {
    const c = await jget("/config");
    const secretRow = (k, label) => `<tr><td>${esc(label)}</td><td>${
      c.secrets && c.secrets[k] ? '<span class="pill ok">đã cấu hình</span>' : '<span class="pill warn">chưa cấu hình</span>'
    }</td></tr>`;
    const repos = Object.entries(c.hf_repos || {}).map(([k, v]) => `${esc(k)}=<code>${esc(v)}</code>`).join(" · ");
    const sched = c.schedule || {};
    const opts = CADENCES.map((x) => `<option value="${x}"${x === sched.cadence ? " selected" : ""}>${x}</option>`).join("");
    const vast = c.vast || {};
    const vastSecret = vast.api_key_set ? '<span class="pill ok">đã cấu hình</span>' : '<span class="pill warn">chưa cấu hình</span>';
    el.innerHTML = `
      <table>
        ${secretRow("HF_TOKEN", "HF token (push model)")}
        ${secretRow("JUDGE_API_KEY", "Judge API key")}
        <tr><td>Vast.ai API key</td><td>${vastSecret} <span id="cf-vast-balance" class="hint">· đang đọc số dư…</span></td></tr>
        <tr><td>Judge base URL</td><td><input id="cf-judge-url" style="width:100%;box-sizing:border-box" value="${esc((c.judge || {}).JUDGE_BASE_URL || "")}"></td></tr>
        <tr><td>Judge model</td><td><input id="cf-judge-model" style="width:100%;box-sizing:border-box" value="${esc((c.judge || {}).JUDGE_MODEL || "")}"></td></tr>
        <tr><td>HF token (mới)</td><td><input id="cf-hf" type="password" style="width:100%;box-sizing:border-box" placeholder="để trống = giữ nguyên"></td></tr>
        <tr><td>Judge API key (mới)</td><td><input id="cf-judge-key" type="password" style="width:100%;box-sizing:border-box" placeholder="để trống = giữ nguyên"></td></tr>
        <tr><td>Vast.ai API key (mới)</td><td><input id="cf-vast" type="password" style="width:100%;box-sizing:border-box" placeholder="để trống = giữ nguyên (để thuê GPU khi retrain/deploy)"></td></tr>
        <tr><td>Nhịp đối soát</td><td><select id="cf-cadence">${opts}</select> ngày <input id="cf-day" type="number" min="1" max="28" value="${esc(String(sched.day ?? 7))}" style="width:70px"></td></tr>
        <tr><td>Ngưỡng CVE mới tối thiểu</td><td><input id="cf-mincves" type="number" min="1" value="${esc(String(c.min_new_cves_to_run ?? 20))}" style="width:100px"></td></tr>
        <tr><td>Model serving (vast)</td><td class="hint">embed=<code>${esc(vast.embed_model || "—")}</code> · rerank=<code>${esc(vast.rerank_model || "—")}</code></td></tr>
        <tr><td>HF repo production</td><td class="hint">${repos || "—"}</td></tr>
      </table>
      <div style="margin-top:8px"><button id="cf-save" type="button">Lưu cấu hình (cần API key)</button></div>`;

    // SSH note + public key máy triển khai (để add vào vast) — public key an toàn hiển thị
    jget("/vast/ssh").then((s) => {
      const keys = (s.public_keys || []).map((k) => esc(k.key)).join("\n");
      el.insertAdjacentHTML("beforeend", `
        <details class="hist-log" style="margin-top:8px">
          <summary><b>SSH vào vast</b> — public key máy triển khai (add vào vast.ai → Account → SSH Keys)</summary>
          <p class="hint">${esc(s.note || "")}</p>
          <pre class="run-log">${keys || "&lt;không tìm thấy ~/.ssh/*.pub&gt;"}</pre>
        </details>`);
    }).catch(() => {});

    // số dư vast (best-effort, không chặn render)
    jget("/vast/status").then((v) => {
      const b = container.querySelector("#cf-vast-balance");
      if (!b) return;
      if (v.ok) b.innerHTML = v.can_rent
        ? `· số dư credit <b>$${esc(String(v.credit))}</b>`
        : `· <span class="pill fail">hết tiền</span> credit $${esc(String(v.credit))} (nạp mới thuê được GPU)`;
      else b.textContent = "· không đọc được số dư: " + (v.error || "?");
    }).catch(() => {});

    el.querySelector("#cf-save").addEventListener("click", async () => {
      const msg = container.querySelector("#ra-config-msg");
      const payload = {};
      const put = (id, key, isNum) => {
        const v = el.querySelector(id).value.trim();
        if (v !== "") payload[key] = isNum ? Number(v) : v;
      };
      put("#cf-hf", "HF_TOKEN"); put("#cf-judge-key", "JUDGE_API_KEY");
      put("#cf-judge-url", "JUDGE_BASE_URL"); put("#cf-judge-model", "JUDGE_MODEL");
      put("#cf-vast", "VAST_API_KEY");
      payload.schedule_cadence = el.querySelector("#cf-cadence").value;
      put("#cf-day", "schedule_day", true); put("#cf-mincves", "min_new_cves_to_run", true);
      msg.innerHTML = `<span class="hint">Đang lưu…</span>`;
      try {
        const r = await jpost("/config", payload);
        msg.innerHTML = `<span class="pill ok">Đã lưu</span> <span class="hint">env: ${esc((r.updated_env_keys || []).join(", ") || "—")}${r.updated_config ? " + schedule/threshold" : ""} (áp dụng lần chạy kế tiếp)</span>`;
        loadConfig(container); loadSchedule(container);
      } catch (e) { msg.innerHTML = `<span class="error">${esc(e.message)}</span>`; }
    });
  } catch (e) {
    el.innerHTML = `<p class="error">Không tải được cấu hình: ${esc(e.message)}</p>`;
  }
}

async function checkConn(container) {
  const el = container.querySelector("#ra-conn");
  el.innerHTML = `<span class="hint">Đang kiểm tra kết nối tới ${esc(base())}…</span>`;
  try {
    await jget("/health");
    el.innerHTML = `<span class="pill ok">Đã kết nối</span> <span class="hint">${esc(base())}</span>`;
  } catch (e) {
    el.innerHTML = `<span class="pill fail">Chưa kết nối</span> <span class="hint">${esc(base())} — ${esc(e.message)}.
      Nếu gateway chưa rebuild proxy, nhập trực tiếp URL trigger_api ở mục Cấu hình (vd http://localhost:8877).</span>`;
  }
}

async function loadSchedule(container) {
  const el = container.querySelector("#ra-schedule");
  try {
    const d = await jget("/schedule");
    const s = d.schedule || {};
    const refs = (s.contract_refs || []).map((r) => `<code>${esc(r)}</code>`).join(" ");
    const timer = d.timer && d.timer.installed
      ? `<span class="pill ok">systemd timer đã cài</span>`
      : `<span class="pill warn">systemd timer CHƯA cài</span> <span class="hint">(chạy setup/install_schedule.sh để bật tự động)</span>`;
    el.innerHTML = `
      <table>
        <tr><td>Nhịp đối soát</td><td><b>${esc(s.cadence || "?")}</b>, ngày <b>${esc(String(s.day ?? "?"))}</b> hằng tháng</td></tr>
        <tr><td>Căn cứ hợp đồng</td><td>${refs || "—"}</td></tr>
        <tr><td>Tự động (timer)</td><td>${timer}</td></tr>
      </table>
      ${d.timer && d.timer.detail ? `<details class="hist-log"><summary>Chi tiết timer</summary><pre class="run-log">${esc(d.timer.detail)}</pre></details>` : ""}`;
  } catch (e) {
    el.innerHTML = `<p class="error">Không tải được lịch: ${esc(e.message)}</p>`;
  }
}

function renderActions(container) {
  const el = container.querySelector("#ra-actions");
  el.innerHTML = `
    <p class="hint">Chế độ:
      <b>smoke/dry</b> = chỉ đo (giai đoạn 1-3), an toàn, không tốn GPU/không đụng production ·
      <b>execute</b> = train + push candidate + requalify (thuê GPU, CHƯA đụng production) ·
      <b>deploy</b> = execute + promote lên production nếu đạt (ẢNH HƯỞNG HỆ THỐNG LIVE + tốn tiền).</p>
    <button id="ra-smoke" type="button">Chạy smoke (dry-run, an toàn)</button>
    <select id="ra-mode" style="margin-left:12px">
      <option value="execute">execute (train + requalify, chưa deploy)</option>
      <option value="deploy">deploy (promote lên production)</option>
    </select>
    <button id="ra-full" type="button" style="margin-left:6px">Kích hoạt cả vòng đời…</button>`;

  el.querySelector("#ra-smoke").addEventListener("click", async () => {
    const msg = container.querySelector("#ra-msg");
    msg.innerHTML = `<span class="hint">Đang khởi động smoke…</span>`;
    try {
      const r = await jpost("/smoke", {});
      msg.innerHTML = `<span class="pill ok">Đã khởi động</span> <span class="hint">run ${esc(r.run_id)}</span>`;
      track(container, r.run_id);
      setTimeout(() => loadRuns(container), 1500);
    } catch (e) { msg.innerHTML = `<span class="error">${esc(e.message)}</span>`; }
  });

  el.querySelector("#ra-full").addEventListener("click", async () => {
    const msg = container.querySelector("#ra-msg");
    const mode = el.querySelector("#ra-mode").value;
    const warn = mode === "deploy"
      ? "DEPLOY sẽ ghi đè model production trên HF và redeploy gateway đang phục vụ ias.id.vn nếu candidate đạt, và TỐN TIỀN thuê GPU."
      : "EXECUTE sẽ thuê GPU (tốn tiền) để train + requalify, nhưng CHƯA đụng production.";
    const ok = window.prompt(`${warn}\n\nGõ ĐÚNG "${mode}" để xác nhận kích hoạt:`);
    if (ok !== mode) { msg.innerHTML = `<span class="hint">Đã huỷ (xác nhận không khớp).</span>`; return; }
    msg.innerHTML = `<span class="hint">Đang khởi động ${esc(mode)}…</span>`;
    try {
      const r = await jpost("/trigger", { mode });
      msg.innerHTML = `<span class="pill ok">Đã khởi động</span> <span class="hint">run ${esc(r.run_id)} (${esc(mode)})</span>`;
      track(container, r.run_id);
      setTimeout(() => loadRuns(container), 1500);
    } catch (e) { msg.innerHTML = `<span class="error">${esc(e.message)}</span>`; }
  });
}

async function loadRuns(container) {
  const el = container.querySelector("#ra-runs");
  try {
    const d = await jget("/runs?limit=20");
    if (!d.runs || !d.runs.length) { el.innerHTML = `<p class="hint">Chưa có lần chạy nào.</p>`; return; }
    el.innerHTML = `<table><thead><tr><th>Run</th><th>Chế độ</th><th>Trạng thái</th><th>Giai đoạn</th><th></th></tr></thead>
      <tbody>${d.runs.map((r) => `<tr>
        <td><code>${esc(r.run_id)}</code></td><td>${esc(r.mode || "")}</td>
        <td>${pill(r.overall === "running" ? "running" : r.overall === "failed" ? "failed" : "done")} ${esc(r.overall || "")}</td>
        <td>${esc(String(r.stages_done))}/${esc(String(r.stages_total))}</td>
        <td><button type="button" data-run="${esc(r.run_id)}" class="ra-open">Xem</button></td>
      </tr>`).join("")}</tbody></table>`;
    el.querySelectorAll(".ra-open").forEach((b) =>
      b.addEventListener("click", () => track(container, b.dataset.run)));
  } catch (e) {
    el.innerHTML = `<p class="error">Không tải được lịch sử: ${esc(e.message)}</p>`;
  }
}

// Theo dõi 1 run: LUÔN hiện trạng thái/chỗ fail (đọc /runs/{id}, không cần key); nếu có key thì
// mở thêm SSE để xem LOG chi tiết realtime từng giai đoạn (log có thể chứa secret nên cần key).
async function track(container, runId) {
  container.querySelector("#ra-run-label").textContent = "— " + runId;
  const stagesEl = container.querySelector("#ra-stages");
  const logBuf = {};    // stage id -> accumulated log text
  const openState = {}; // stage id -> <details> open?

  if (evtSource) { try { evtSource.close(); } catch {} evtSource = null; }

  function overallLine(status) {
    const ov = status && status.overall;
    const cls = (ov === "failed" || ov === "stopped") ? "fail" : ov === "running" ? "warn" : "ok";
    const failed = (status.stages || []).find((s) => s.state === "failed");
    const where = failed ? ` — fail ở giai đoạn <b>${esc(failed.id)} ${esc(failed.name)}</b>${failed.note ? ": " + esc(failed.note) : ""}` : "";
    return `<p>Trạng thái tổng: <span class="pill ${cls}">${esc(ov || "?")}</span> <span class="hint">(chế độ ${esc(status.mode || "?")})</span>${where}</p>` +
      (ov === "running" ? `<p><button type="button" class="ra-stop-btn" style="background:#b91c1c;color:#fff;border:0;padding:6px 14px;border-radius:6px;cursor:pointer">⏹ Dừng pipeline</button> <span class="hint">Dừng ngay tiến trình đang chạy (cần X-API-Key). Instance vast đã thuê KHÔNG tự huỷ — kiểm tra sau khi dừng.</span></p>` : "");
  }

  function renderStages(status) {
    const stages = (status && status.stages) || [];
    stagesEl.innerHTML = overallLine(status) + stages.map((s) => {
      const wasOpen = openState[s.id] ?? (s.state === "running" || s.state === "failed");
      const log = logBuf[s.id] || "";
      const note = s.note ? ` <span class="hint">${esc(s.note)}</span>` : "";
      const ex = s.exit_code !== null && s.exit_code !== undefined ? ` <span class="hint">exit=${esc(String(s.exit_code))}</span>` : "";
      return `<details class="hist-log" data-stage="${esc(s.id)}"${wasOpen ? " open" : ""}>
        <summary><b>${esc(s.id)}</b> ${esc(s.name)} — ${pill(s.state)}${ex}${note}</summary>
        <pre class="run-log" data-log="${esc(s.id)}">${esc(log) || "&lt;log cần API key để xem&gt;"}</pre>
      </details>`;
    }).join("");
    stagesEl.querySelectorAll("details").forEach((d) =>
      d.addEventListener("toggle", () => { openState[d.dataset.stage] = d.open; }));
    const stopBtn = stagesEl.querySelector(".ra-stop-btn");
    if (stopBtn) stopBtn.addEventListener("click", async () => {
      if (!confirm("Dừng pipeline đang chạy ngay bây giờ?\n(Nếu đang ở giai đoạn deploy, production có thể ở trạng thái dở dang.)")) return;
      stopBtn.disabled = true;
      try {
        const r = await jpost("/runs/" + encodeURIComponent(runId) + "/stop", {});
        const inst = (r.vast_instances_still_running || []);
        alert("Đã dừng pipeline (" + r.overall + ")." + (inst.length
          ? "\nInstance vast CÒN CHẠY (đang tính tiền), cần huỷ tay: " + inst.map((i) => i.id + " [" + i.label + "]").join(", ") : ""));
        renderStages(await jget("/runs/" + encodeURIComponent(runId)));
        loadRuns(container);
      } catch (e) { alert("Dừng thất bại: " + e.message); stopBtn.disabled = false; }
    });
  }

  // 1) Trạng thái + chỗ fail (không cần key)
  let status;
  try { status = await jget("/runs/" + encodeURIComponent(runId)); renderStages(status); }
  catch (e) { stagesEl.innerHTML = `<p class="error">Không đọc được trạng thái run: ${esc(e.message)}</p>`; return; }

  // 2) Log chi tiết realtime (cần key)
  const k = apiKey();
  if (!k) {
    stagesEl.insertAdjacentHTML("beforeend",
      `<p class="hint">Nhập X-API-Key ở "Cấu hình kết nối" để xem LOG chi tiết từng giai đoạn (log có thể chứa endpoint/khoá serving nội bộ nên cần key). Trạng thái/chỗ fail ở trên không cần key.</p>`);
    return;
  }
  const running = (status.overall === "running");
  const url = base() + "/runs/" + encodeURIComponent(runId) + "/stream?api_key=" + encodeURIComponent(k);
  const es = new EventSource(url);
  evtSource = es;
  es.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "status") {
      renderStages(msg.status);
    } else if (msg.type === "log") {
      logBuf[msg.stage] = (logBuf[msg.stage] || "") + msg.chunk;
      const pre = stagesEl.querySelector(`pre[data-log="${CSS.escape(msg.stage)}"]`);
      if (pre) { pre.textContent = logBuf[msg.stage]; pre.scrollTop = pre.scrollHeight; }
    } else if (msg.type === "end") {
      es.close(); if (evtSource === es) evtSource = null;
      if (running) loadRuns(container);
    }
  };
  es.onerror = () => {
    es.close(); if (evtSource === es) evtSource = null;
    jget("/runs/" + encodeURIComponent(runId)).then(renderStages).catch(() => {});
  };
}
