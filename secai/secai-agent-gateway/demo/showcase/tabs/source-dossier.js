// Nhóm S — duyệt HỒ SƠ GỐC nguyên bản: cây thư mục bên trái, nội dung file bên phải.
import { mdToHtml } from "../md.js";
export async function render(container, api) {
  container.innerHTML = `
    <div class="card">
      <h3>Hồ sơ gốc — duyệt nguyên bản</h3>
      <p class="hint" id="sd-crumb"></p>
      <div class="sd-split">
        <div class="sd-tree" id="sd-tree"></div>
        <div class="sd-view" id="sd-view"><p class="hint">Chọn một file bên trái để xem nội dung.</p></div>
      </div>
    </div>
  `;
  const treeEl = container.querySelector("#sd-tree");
  const viewEl = container.querySelector("#sd-view");
  const crumbEl = container.querySelector("#sd-crumb");

  await openDir("", treeEl, viewEl, crumbEl, api);
}

function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

async function openDir(path, treeEl, viewEl, crumbEl, api) {
  treeEl.innerHTML = `<p class="hint">Đang tải…</p>`;
  let data;
  try {
    data = await api.get(`/showcase-api/source-dossier/tree?path=${encodeURIComponent(path)}`);
  } catch (err) {
    treeEl.innerHTML = `<p class="error">${err.message}</p>`;
    return;
  }

  crumbEl.textContent = path ? `/ ${path}` : "/ (gốc hồ sơ)";
  treeEl.innerHTML = "";

  if (path) {
    const up = document.createElement("button");
    up.className = "sd-item sd-up";
    up.textContent = "⬆ lên thư mục trên";
    const parent = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    up.addEventListener("click", () => openDir(parent, treeEl, viewEl, crumbEl, api));
    treeEl.appendChild(up);
  }

  for (const e of data.entries) {
    const btn = document.createElement("button");
    btn.className = "sd-item";
    if (e.type === "dir") {
      btn.textContent = `📁 ${e.name}`;
      btn.addEventListener("click", () => openDir(e.path, treeEl, viewEl, crumbEl, api));
    } else {
      const icon = e.kind === "image" ? "🖼" : e.kind === "binary" ? "📦" : "📄";
      btn.innerHTML = `${icon} ${e.name} <span class="hint">${fmtSize(e.size_bytes)}</span>`;
      btn.addEventListener("click", () => openFile(e.path, viewEl, api, treeEl));
    }
    treeEl.appendChild(btn);
  }
}

async function openFile(path, viewEl, api, treeEl) {
  treeEl.querySelectorAll(".sd-item").forEach((b) => b.classList.remove("active"));
  [...treeEl.querySelectorAll(".sd-item")].find((b) => b.textContent.includes(path.split("/").pop()))?.classList.add("active");

  viewEl.innerHTML = `<p class="hint">Đang mở ${path}…</p>`;
  let f;
  try {
    f = await api.get(`/showcase-api/source-dossier/file?path=${encodeURIComponent(path)}`);
  } catch (err) {
    viewEl.innerHTML = `<p class="error">${err.message}</p>`;
    return;
  }

  const head = `<p class="hint"><code>${f.path}</code> · ${fmtSize(f.size_bytes)}${f.truncated ? ` · <strong>chỉ hiện ${f.preview_lines} dòng đầu</strong>` : ""}</p>`;

  if (f.kind === "image") {
    viewEl.innerHTML = `${head}<img src="${f.image_url}" alt="${f.name}" style="max-width:100%" />`;
    return;
  }
  if (f.kind === "binary" || f.content == null) {
    viewEl.innerHTML = `${head}<p class="hint">File nhị phân — không xem trực tiếp được trên web.</p>`;
    return;
  }

  viewEl.innerHTML = head;
  if (/\.md$/i.test(f.path || f.name || "")) {
    const div = document.createElement("div");
    div.className = "md";
    div.style.maxHeight = "560px";
    div.style.overflow = "auto";
    div.innerHTML = mdToHtml(f.content);
    viewEl.appendChild(div);
  } else {
    const pre = document.createElement("pre");
    pre.style.maxHeight = "560px";
    pre.style.overflow = "auto";
    pre.textContent = f.content;
    viewEl.appendChild(pre);
  }
}
