Phạm vi OWASP Top 10
Bộ kiểm thử chất lượng gồm 1000 câu hỏi–đáp thật đã chạy trên hệ thống, log tại `HO-SO-GOC-NGHIEM-THU-AI/03. Kpi_results/Model/quality/raw-logs/answers.jsonl`. Dưới đây là bản ghi hệ thống đã trả lời đúng chủ đề của chỉ tiêu này, trích nguyên văn câu hỏi và câu trả lời của hệ thống.

**Bản ghi `acc_th_0806`** — capability `pentest`, nhóm `tinh_huong`; công cụ hệ thống đã gọi khi trả lời: trả lời trực tiếp, không gọi công cụ RAG.

Câu hỏi (trích nguyên văn):

Khi pentest web app, những lỗ hổng nhóm OWASP Top 10 nào cần kiểm tra đầu tiên?

Trả lời của hệ thống (trích nguyên văn, đã lược bớt phần giữa và đánh dấu `[...]`):

# OWASP Top 10 — Lỗ hổng ưu tiên kiểm tra khi Pentest Web App

## 🎯 Nhóm ưu tiên cao nhất (Exploitable + Impact lớn)

### 1. **A01: Broken Access Control** ⭐⭐⭐
Kiểm tra trước nhất vì rất phổ biến và dễ khai thác.

- **Test gì**:
  - IDOR (Insecure Direct Object Reference): truy cập tài nguyên của user khác bằng cách thay đổi ID
  - Privilege escalation: user thường → admin (bằng cách thay đổi role, header, URL)
  - Path traversal: đọc file ngoài root (`../etc/passwd`)
  - Bypass endpoint restriction: truy cập API endpoint `/admin` với token user thường
- **Tools**: Burp Suite (`Automate` → `Scope`), custom scripts
- **CWE**: CWE-284, CWE-639, CWE-22

### 2. **A03: Injection** ⭐⭐⭐
Vẫn là nhóm lỗ hổng nghiêm trọng nhất.

- **SQL Injection**:
  - Parameter manipulation trong form, URL, header
  - Blind SQLi (boolean-based, time-based)
  - Tool: `sqlmap -u "URL" --batch`
- **Command Injection**:
  - Tham số truyền trực tiếp vào `system()`, `exec()`, `shell`
  - Test: `; id`, `| ls`, `` `whoami` ``
- **Template Injection (SSTI)**:
  - {{7*7}}, `${7*7}`, `<%= system('id') %>`
  - CWE-94, CWE-89

### 3. **A07: Identification and Authentication Failures** ⭐⭐⭐

[...]

| **Map all API endpoints** | Xem tất cả parameter, method, header có thể manipulate |
| **Test on every parameter** | Mỗi input là một potential attack vector (URL, body, header, cookie) |
| **Document everything** | Ghi lại từng finding ngay khi phát hiện |
| **Không bỏ qua 4xx** | Error 401/403 đôi khi hé lộ thông tin nhạy cảm về auth flow |

---

**Thứ tự ưu tiên tổng thể**:
> **Access Control > Injection > Auth > Misconfiguration > Logic > SSRF > Logging**

Bạn muốn đi sâu vào kỹ thuật kiểm tra nhóm nào cụ thể không?

---

**Kết luận:** Hệ thống liệt kê đúng các nhóm rủi ro OWASP Top 10 (A01 Broken Access Control, A03 Injection, A07 Auth Failures, A04 Insecure Design, A05 Misconfiguration, SSRF...) kèm nội dung cần kiểm tra và CWE tương ứng — đạt yêu cầu phạm vi OWASP Top 10.
