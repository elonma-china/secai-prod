export async function render(container, api) {
  const data = await api.get("/showcase-api/model-info");

  const rows = data.components
    .map(
      (c) => `
    <tr>
      <td>${c.contract_display_name}</td>
      <td><a href="${c.base_model.hf_url}" target="_blank" rel="noopener">${c.base_model.id}</a></td>
      <td>${c.license ?? "—"}</td>
      <td><a href="${c.serving_model.hf_url}" target="_blank" rel="noopener">${c.serving_model.id}</a></td>
      <td>${(c.covered_tasks || []).join(", ") || "—"}</td>
    </tr>`
    )
    .join("");

  const hw = data.hardware || {};
  const rc = data.retrieval_config || {};
  const supp = (data.supplementary_models || [])
    .map((m) => `<li><a href="${m.hf_url}" target="_blank" rel="noopener">${m.contract_display_name}</a></li>`)
    .join("");

  container.innerHTML = `
    <div class="card">
      <h3>Các mô hình đang vận hành</h3>
      <table>
        <thead><tr><th>Tên (khớp hợp đồng)</th><th>Base model</th><th>Bản quyền</th><th>Model đang serving</th><th>Tác vụ</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${supp ? `<p style="margin:12px 0 4px"><strong>LLM bổ trợ:</strong></p><ul style="margin:0">${supp}</ul>` : ""}
    </div>
    <div class="card">
      <h3>Phần cứng</h3>
      <p>Huấn luyện: <strong>${hw.training ?? "—"}</strong></p>
      <p>Cam kết tối thiểu khi serving: ${hw.serving_min_commit?.gpu ?? "—"}, RAM ≥ ${hw.serving_min_commit?.ram_gb ?? "—"}GB, SSD ≥ ${hw.serving_min_commit?.ssd_tb ?? "—"}TB</p>
      <p>Hạ tầng đang chạy thật: ${hw.serving_actual ?? "—"}</p>
      ${hw.verification ? renderHwCheck(hw.verification) : ""}
    </div>
    <div class="card">
      <h3>Cấu hình tìm kiếm (Hybrid Search)</h3>
      <table>
        <tbody>
          <tr><td>Chế độ</td><td><strong>${rc.mode ?? "—"}</strong></td></tr>
          <tr><td>Dense model</td><td><code>${rc.dense_model ?? "—"}</code></td></tr>
          <tr><td>Sparse model</td><td><code>${rc.sparse_model ?? "—"}</code></td></tr>
          <tr><td>Qdrant collection</td><td><code>${rc.qdrant_collection ?? "—"}</code></td></tr>
          <tr><td>Sparse vector name</td><td><code>${rc.qdrant_sparse_vector_name ?? "—"}</code></td></tr>
        </tbody>
      </table>
      <h3 style="margin-top:14px">Số đo hybrid vs dense-only (${rc.measured?.n_query ?? "?"} query)</h3>
      <table>
        <thead><tr><th>Chỉ số</th><th>Kết quả</th></tr></thead>
        <tbody>
          <tr><td>Hit@10 (id-aware, hybrid đầy đủ)</td><td><strong>${rc.measured?.hit_rate_at_10_id_aware_pct ?? "?"}%</strong></td></tr>
          <tr><td>Hit@10 (hybrid)</td><td>${rc.measured?.hit_rate_at_10_hybrid_pct ?? "?"}%</td></tr>
          <tr><td>Hit@10 (chỉ dense)</td><td>${rc.measured?.hit_rate_at_10_dense_only_pct ?? "?"}%</td></tr>
          <tr><td>Precision@1</td><td>${rc.measured?.precision_at_1_pct ?? "?"}%</td></tr>
        </tbody>
      </table>
      <p class="hint">Nguồn: <code>${rc.measured?.source ?? ""}</code></p>
    </div>
    ${data.context ? `
    <div class="card">
      <h3>Ngữ cảnh (Context Window)</h3>
      <table><tbody>
        <tr><td>Context window tối đa (cam kết)</td><td><strong>${(data.context.committed_max_tokens/1000)}K tokens</strong></td></tr>
        <tr><td>Effective context (cam kết)</td><td>${(data.context.committed_effective_tokens/1000)}K tokens</td></tr>
        <tr><td>max_seq_length khi huấn luyện</td><td>${data.context.train_max_seq_length ? Number(data.context.train_max_seq_length).toLocaleString("vi-VN") + " tokens" : "—"}</td></tr>
      </tbody></table>
      <p class="hint">Nguồn: <code>${data.context.source ?? ""}</code></p>
    </div>` : ""}
  `;
}

function renderHwCheck(v) {
  const s = v.summary || {};
  return `
    <div style="margin-top:12px;border-top:1px solid var(--border);padding-top:10px">
      <p style="margin:0 0 6px"><strong>Kiểm tra phần cứng trực tiếp trên máy chủ</strong>
         <span class="hint"> · ${v.captured_at ?? ""} · ${v.host ?? ""}</span></p>
      <table>
        <tbody>
          <tr><td>GPU</td><td>${s.gpu ?? "—"}</td></tr>
          <tr><td>RAM</td><td>${s.ram ?? "—"}</td></tr>
          <tr><td>Ổ đĩa / SSD</td><td>${s.storage ?? "—"}</td></tr>
        </tbody>
      </table>
      <p class="hint" style="margin:8px 0 4px">Lệnh: <code>${v.command ?? ""}</code></p>
      ${v.raw ? `<pre class="run-log" style="max-height:260px">${v.raw.replace(/</g, "&lt;")}</pre>` : ""}
    </div>`;
}
