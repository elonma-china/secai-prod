// Nhóm KB — lịch + lịch sử các lần cập nhật tri thức KB (CVE/0-day) hằng ngày.
export async function render(container, api) {
  container.innerHTML = `<div class="card"><h3>Cập nhật tri thức (KB)</h3><p class="hint">Đang tải…</p></div>`;
  let data;
  try {
    data = await api.get("/showcase-api/kb/sync-log?limit=14");
  } catch (err) {
    container.innerHTML = `<div class="card"><p class="error">${err.message}</p></div>`;
    return;
  }
  const sc = data.schedule || {};

  const rows = (data.runs || [])
    .map((r) => `<tr><td>${r.started ?? "—"}</td><td>${r.ended ?? "—"}</td></tr>`)
    .join("");

  container.innerHTML = `
    <div class="card">
      <h3>Cập nhật tri thức (KB) hằng ngày</h3>
      <p>Hệ thống tự động cập nhật kho tri thức (CVE / lỗ hổng 0-day) theo lịch cố định, nạp vào Qdrant (vector) + BM25 + Neo4j.</p>
      <table><tbody>
        <tr><td>Tần suất</td><td><strong>${sc.frequency ?? "—"}</strong> (cron <code>${sc.cron ?? "—"}</code>)</td></tr>
        <tr><td>Nguồn dữ liệu</td><td>NVD · MITRE CVE/CWE · AutoScan CVE intel</td></tr>
        <tr><td>Đích cập nhật</td><td>Qdrant (dense) · BM25 (sparse) · Neo4j (graph)</td></tr>
        <tr><td>Sửa lịch qua giao diện</td><td>${sc.editable_via_ui ? "Có" : "Không — cấu hình khi khởi động hệ thống"}</td></tr>
      </tbody></table>
    </div>
    <div class="card">
      <h3>Các lần cập nhật gần đây</h3>
      <table>
        <thead><tr><th>Bắt đầu</th><th>Kết thúc</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="2">Chưa có dữ liệu.</td></tr>'}</tbody>
      </table>
    </div>`;
}
