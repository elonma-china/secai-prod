// Ví dụ phủ đủ 5 loại mà bộ dò của KPI R-SEC-PII (detector.py) bắt được:
// email · IP · số điện thoại · password (hardcoded) · api_key/token.
const EXAMPLE = `Sự cố rò rỉ trong CI log, nhờ kiểm tra hộ:
Liên hệ: nguyen.van.a@company.vn · hotline 0912345678
Server nội bộ: 192.168.10.5
DB_PASSWORD = "Sup3rS3cret_2024!"
AWS: AKIAIOSFODNN7EXAMPLE
GitHub: ghp_16C7e42F292c6912E7710c838347Ae178B4a
Google: AIzaSyD-1a2b3c4d5e6f7g8h9i0jklmnopqrst
OpenAI: sk-abcd1234efgh5678ijklmnop
api_key=ABCDEF1234567890ABCDEF
JWT: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.dozjgNryP4J3jVmNHl0w5N`;

export async function render(container, api) {
  container.innerHTML = `
    <div class="card">
      <h3>Làm sạch dữ liệu nhạy cảm trước khi đưa vào mô hình</h3>
      <p class="hint">Dán văn bản có email, IP, số điện thoại, mật khẩu (<code>password=</code>), API key/token…
         rồi bấm Kiểm tra. Chạy đúng bộ dò mà KPI <strong>R-SEC-PII (F1 98.06%)</strong> đo —
         <code>detector.py</code> trong hồ sơ gốc (Regex + entropy), không phải bản mock. Không lưu nội dung.</p>
      <textarea id="san-input" style="min-height:220px">${EXAMPLE}</textarea>
      <div class="row"><button class="run" id="san-run">Kiểm tra</button></div>
      <div id="san-result"></div>
    </div>
  `;

  const input = container.querySelector("#san-input");
  const btn = container.querySelector("#san-run");
  const result = container.querySelector("#san-result");

  async function run() {
    btn.disabled = true;
    result.innerHTML = '<p class="hint">Đang kiểm tra…</p>';
    try {
      const data = await api.post("/showcase-api/sanitize-demo", { text: input.value });
      const pills = data.entities_found
        .map((e) => `<span class="pill warn">${e.type} × ${e.count}</span>`)
        .join(" ") || '<span class="pill ok">Không phát hiện PII/secret</span>';
      result.innerHTML = `
        <p><strong>Văn bản sau khi làm sạch:</strong></p>
        <pre>${data.sanitized_text.replace(/</g, "&lt;")}</pre>
        <p>${pills}</p>
        <p class="hint">Nhãn theo loại: <code>[REDACTED_EMAIL]</code> · <code>[REDACTED_IP]</code> ·
           <code>[REDACTED_PHONE]</code> · <code>[REDACTED_PASSWORD]</code> · <code>[REDACTED_API_KEY]</code>.</p>
      `;
    } catch (err) {
      result.innerHTML = `<p class="error">${err.message}</p>`;
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener("click", run);
}
