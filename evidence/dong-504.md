Phạm vi Authentication & Authorization
Nhóm "Authentication & Authorization" được kiểm chứng bằng các bản ghi hỏi–đáp thật đã chạy trong bộ 1.000 mẫu (`03. Kpi_results/Model/quality/raw-logs/answers.jsonl`).

Bản ghi `id=acc_dd_0045` (capability=cve_qa; gold: cve_id=CVE-2025-53786, cwe=CWE-287, severity=HIGH, products=exchange_server). `question` (nguyên văn):

> CVE-2025-53786 là lỗ hổng gì? Mức độ nghiêm trọng thế nào?

`answer` (trích nguyên văn, cắt giữa ghi [...]):

```
## Thông tin tổng quan

| Trường | Giá trị |
|---|---|
| **ID** | CVE-2025-53786 |
| **Severity** | **HIGH** |
| **CVSS v3.1** | **8.0** |
| **CWE** | CWE-287 (Improper Identification, Authentication, and Authorization Handling) |
| **Sản phẩm bị ảnh hưởng** | Microsoft Exchange Server |
[...]
- **CWE-287** → Lỗ hổng liên quan đến xử lý xác thực/xác định danh không đúng đắn. Trong ngữ cảnh Exchange hybrid, điều này có thể dẫn đến **lỗi ủy quyền hoặc xác thực** trong môi trường lai (hybrid), cho phép attacker giả mạo hoặc bypass các cơ chế bảo mật.
```

Bản ghi `id=acc_dd_0046` (capability=cve_qa; gold: cve_id=CVE-2025-0516, cwe=CWE-863, severity=MEDIUM, products=gitlab). `question` (nguyên văn):

> CVE-2025-0516 có điểm CVSS bao nhiêu? Mức phân loại tương ứng?

`answer` (trích nguyên văn):

```
| **CVE ID** | CVE-2025-0516 |
| **CVSS 3.1 Score** | **4.3** |
| **Severity (Mức phân loại)** | **MEDIUM** |
| **CWE liên quan** | CWE-863 (Incorrect or Missing Authorization) |
| **Sản phẩm ảnh hưởng** | GitLab |
| **Năm** | 2025 |

→ CVSS **4.3** nằm trong ngưỡng **MEDIUM** (4.0 – 6.9).
```

**Kết luận:** Trợ lý trả lời đúng nhóm Authentication & Authorization — một ca xác thực (CWE-287, CVE-2025-53786, HIGH/8.0) và một ca ủy quyền (CWE-863, CVE-2025-0516, MEDIUM/4.3) — trích trực tiếp từ kết quả 1.000 mẫu đã chạy trong answers.jsonl.
