// Shell: tab bar + lazy-load per-group tab module + shared fetch helper.
// Each tabs/<id>.js file exports `async function render(container, api)`.
// Same-origin (served by the gateway itself, see docs/nghiem-thu-showcase/01-API-DESIGN.md
// integration notes) — no base-URL config, no CORS.

const TABS = [
  { id: "finetune", label: "Finetuning", module: "./tabs/finetune.js" },
  { id: "testset-1000", label: "Dữ liệu huấn luyện chuẩn", module: "./tabs/testset-1000.js" },
  { id: "retraining", label: "Huấn luyện lại", module: "./tabs/retraining.js" },
  { id: "model-info", label: "Model & phần cứng", module: "./tabs/model-info.js" },
  { id: "kpi-bench", label: "KPI Bench", module: "./tabs/kpi-bench.js" },
  { id: "sanitize-demo", label: "Sanitize demo", module: "./tabs/sanitize-demo.js" },
  { id: "evidence", label: "Evidence", module: "./tabs/evidence.js" },
  { id: "kb-sync", label: "Cập nhật KB", module: "./tabs/kb-sync.js" },
  { id: "deploy", label: "Triển khai", module: "./tabs/deploy.js" },
  { id: "source-dossier", label: "Hồ sơ gốc", module: "./tabs/source-dossier.js" },
];

async function apiGet(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = body && body.message ? body.message : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body;
}

async function apiPost(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = body && body.message ? body.message : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body;
}

const api = { get: apiGet, post: apiPost };

const nav = document.getElementById("tabs");
const content = document.getElementById("tab-content");
const cache = new Map(); // tab id -> already-rendered flag (module renders once, keeps DOM)

function setActive(id) {
  [...nav.children].forEach((btn) => btn.classList.toggle("active", btn.dataset.id === id));
  [...content.children].forEach((el) => (el.hidden = el.dataset.id !== id));
}

// deep: tham số sâu sau dấu "/" trong hash (vd #evidence/dong-509 -> deep="dong-509").
// Module có thể nhận deep làm đối số thứ 3 của render() để tự mở đúng mục.
async function openTab(tab, deep) {
  setActive(tab.id);
  const ph = content.querySelector(":scope > p.hint");
  if (ph) ph.remove();
  if (!cache.has(tab.id)) {
    cache.set(tab.id, true);
    const panel = document.createElement("section");
    panel.dataset.id = tab.id;
    panel.innerHTML = `<p class="hint">Đang tải ${tab.label}…</p>`;
    content.appendChild(panel);
    try {
      const mod = await import(tab.module);
      panel.dataset.module = tab.module;
      panel._render = mod.render;
      panel.innerHTML = "";
      await mod.render(panel, api, deep);
    } catch (err) {
      panel.innerHTML = `<p class="error">Lỗi tải tab "${tab.label}": ${err.message}</p>`;
    }
    return;
  }
  // Tab đã mở sẵn: nếu có deep mới thì render lại để nhảy đúng mục.
  if (deep) {
    const panel = [...content.children].find((el) => el.dataset.id === tab.id);
    if (panel && panel._render) {
      panel.innerHTML = "";
      try { await panel._render(panel, api, deep); } catch (e) { /* giữ nội dung cũ */ }
    }
  }
}

// #tabId hoặc #tabId/deep  -> {tab, deep}
function parseHash() {
  const raw = decodeURIComponent(location.hash.replace(/^#/, ""));
  if (!raw) return null;
  const slash = raw.indexOf("/");
  const id = slash === -1 ? raw : raw.slice(0, slash);
  const deep = slash === -1 ? "" : raw.slice(slash + 1);
  const tab = TABS.find((t) => t.id === id);
  return tab ? { tab, deep } : null;
}

function openFromHash() {
  const m = parseHash();
  if (m) openTab(m.tab, m.deep);
  else openTab(TABS[0]);
}

TABS.forEach((tab) => {
  const btn = document.createElement("button");
  btn.textContent = tab.label;
  btn.dataset.id = tab.id;
  // Đổi hash thay vì gọi trực tiếp -> URL luôn phản ánh tab đang xem (chia sẻ/deep-link được).
  btn.addEventListener("click", () => { location.hash = tab.id; });
  nav.appendChild(btn);
});

window.addEventListener("hashchange", openFromHash);
openFromHash();
