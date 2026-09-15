Hỗ trợ truy xuất thông tin
Bản ghi hỏi–đáp thật `acc_dd_0006` (capability `cve_qa`) — `03. Kpi_results/Model/quality/raw-logs/answers.jsonl`:

**Câu hỏi:**
```
CVE-2024-10402 có điểm CVSS bao nhiêu? Mức phân loại tương ứng?
```

**Câu trả lời của hệ thống:**
```
## CVE-2024-10402

| Thuộc tính | Giá trị |
|---|---|
| **CVSS v3.1 Score** | **7.5** |
| **Severity** | **HIGH** |
| **Vector String** | `CVSS:3.1/AV:N/AC:H/PR:L/UI:N/S:U/C:H/I:H/A:H` |
| **CWE** | CWE-862 (Missing Authorisation) |
| **CAPEC** | CAPEC-665 |
| **MITRE Techniques** | T1211, T1556, T1542.002 |
| **Sản phẩm** | Forminator Forms (WordPress plugin) |
| **CISA KEV** | ❌ Không |

### Mô tả
Plugin **Forminator Forms** – Contact Form, Payment Form & Custom Form Builder cho WordPress có lỗ hổng **thiếu kiểm tra quyền hạn (missing capability check)** ở một số function, ảnh hưởng tất cả phiên bản **từ 1.35.1 trở về trước**.

- **Yêu cầu điều kiện**: Tấn công viên cần ít nhất quyền **Contributor** (hoặc cao hơn) và sự cấp phép từ Administrator.
- **Hậu quả**: Có thể **tạo hoặc chỉnh sửa form**, bao gồm việc **nâng vai trò người dùng mặc định lên Administrator** qua form User Registra
[...]
```

**Kết luận:** Hệ thống truy xuất và trả lời thông tin CVE/CWE có thật từ kho tri thức (bản ghi đã ghi nhận trong bộ 1000 mẫu KPI).
