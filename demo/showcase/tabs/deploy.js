// Nhóm Triển khai — Docker Compose + Helm chart Kubernetes.
export async function render(container, api) {
  container.innerHTML = `<div class="card"><h3>Triển khai</h3><p class="hint">Đang tải…</p></div>`;
  let data;
  try {
    data = await api.get("/showcase-api/deploy/manifests");
  } catch (err) {
    container.innerHTML = `<div class="card"><p class="error">${err.message}</p></div>`;
    return;
  }

  const dk = data.docker || {};
  const helm = data.helm || {};
  const esc = (t) => (t || "").replace(/</g, "&lt;");

  const helmFileBtns = (helm.files || [])
    .map((f, i) => `<button class="run" data-hi="${i}" style="margin:0 6px 6px 0">${f.path}</button>`)
    .join("");

  container.innerHTML = `
    <div class="card">
      <h3>Docker</h3>
      <table><tbody>
        <tr><td>Docker image — gateway</td><td>${dk.image_sizes?.gateway ?? "—"}</td></tr>
        <tr><td>Trọng số mô hình (serving)</td><td>${dk.image_sizes?.serving_weights ?? "—"}</td></tr>
      </tbody></table>
      ${dk.compose ? `<p style="margin:10px 0 4px"><strong>${dk.compose.name}</strong></p>
        <pre style="max-height:420px;overflow:auto">${esc(dk.compose.content)}</pre>` : '<p class="hint">Không tìm thấy docker-compose.</p>'}
    </div>
    <div class="card">
      <h3>Helm chart (Kubernetes)</h3>
      ${helm.files?.length ? `
        <p class="hint">${helm.files.length} file trong chart — bấm để xem:</p>
        <div class="row" id="helm-btns">${helmFileBtns}</div>
        <pre id="helm-view" style="max-height:420px;overflow:auto"></pre>
      ` : '<p class="hint">Chưa có Helm chart.</p>'}
    </div>`;

  const view = container.querySelector("#helm-view");
  container.querySelectorAll("#helm-btns .run").forEach((b) => {
    b.addEventListener("click", () => {
      container.querySelectorAll("#helm-btns .run").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      view.textContent = helm.files[Number(b.dataset.hi)].content;
    });
  });
  const first = container.querySelector("#helm-btns .run");
  if (first) first.click();
}
