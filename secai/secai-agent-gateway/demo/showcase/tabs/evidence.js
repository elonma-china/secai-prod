// Nhóm E (Loại 2 — evidence tóm tắt theo tiêu chí). List + click để mở rộng nội dung.
// deep = id file (vd "dong-509"): deep-link #evidence/dong-509 sẽ tự mở đúng mục đó.
import { mdToHtml } from "../md.js";
export async function render(container, api, deep) {
  container.innerHTML = `
    <div class="card">
      <h3>Kết quả nghiệm thu theo từng tiêu chí</h3>
      <div id="ev-list"></div>
    </div>
  `;
  const listEl = container.querySelector("#ev-list");

  let items = [];
  try {
    ({ items } = await api.get("/showcase-api/evidence"));
  } catch (err) {
    listEl.innerHTML = `<p class="error">${err.message}</p>`;
    return;
  }

  if (!items.length) {
    listEl.innerHTML = `<p class="hint">Chưa có file evidence nào trong thư mục.</p>`;
    return;
  }

  // Nếu deep-link tới 1 dòng: lọc chỉ hiện đúng dòng đó + nút xem tất cả.
  if (deep) {
    const one = items.find((it) => it.id === deep);
    if (one) {
      items = [one];
      const back = document.createElement("button");
      back.className = "run";
      back.textContent = "← Xem tất cả tiêu chí";
      back.style.marginBottom = "10px";
      back.addEventListener("click", () => { location.hash = "evidence"; });
      listEl.appendChild(back);
    }
  }

  let firstBtn = null;
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "card";
    row.innerHTML = `
      <div class="row">
        <span class="pill ok">Dòng ${item.id.replace("dong-", "")}</span>
        <strong>${item.contract_text}</strong>
      </div>
      <button class="run" type="button">Xem nội dung</button>
      <div class="ev-body" hidden></div>
    `;
    const btn = row.querySelector("button.run");
    if (!firstBtn) firstBtn = btn;
    const body = row.querySelector(".ev-body");
    btn.addEventListener("click", async () => {
      if (!body.hidden) { body.hidden = true; return; }
      body.hidden = false;
      if (body.dataset.loaded) return;
      body.dataset.loaded = "1";
      try {
        const full = await api.get(`/showcase-api/evidence/${encodeURIComponent(item.id)}`);
        if (full.format === "html") {
          const iframe = document.createElement("iframe");
          iframe.sandbox = ""; // no scripts, no same-origin — untrusted authored content
          iframe.style.width = "100%";
          iframe.style.minHeight = "200px";
          iframe.style.border = "1px solid var(--border)";
          iframe.srcdoc = full.content;
          body.appendChild(iframe);
        } else if (full.format === "md") {
          const div = document.createElement("div");
          div.className = "md";
          div.innerHTML = mdToHtml(full.content);
          body.appendChild(div);
        } else {
          const pre = document.createElement("pre");
          pre.textContent = full.content;
          body.appendChild(pre);
        }
      } catch (err) {
        body.innerHTML = `<p class="error">${err.message}</p>`;
      }
    });
    listEl.appendChild(row);
  }

  // Deep-link tới 1 dòng: tự bung nội dung ngay.
  if (deep && firstBtn) firstBtn.click();
}
