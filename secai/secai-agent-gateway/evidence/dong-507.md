Phạm vi Configuration & Deployment Weaknesses
Nhóm "Configuration & Deployment Weaknesses" được kiểm chứng bằng các bản ghi hỏi–đáp thật đã chạy trong bộ 1.000 mẫu (`03. Kpi_results/Model/quality/raw-logs/answers.jsonl`).

Bản ghi `id=acc_kp_0456` (capability=cve_qa; gold: cve_id=CVE-2020-28909, cwe=CWE-732 Incorrect Default Permissions, severity=HIGH). `question` (nguyên văn):

> Hệ thống của tôi đang chạy fusion và bị ảnh hưởng bởi CVE-2020-28909. Tôi cần làm gì để khắc phục?

`answer` (trích nguyên văn, cắt giữa ghi [...]):

```
## CVE-2020-28909 — Nagios Fusion: Incorrect File Permissions (Privilege Escalation)

**Severity:** HIGH | **CVSS 3.1: 8.8**  
**CWE:** CWE-732 (Incorrect Default Permissions)
[...]
Nagios Fusion 4.1.8 và các phiên bản cũ hơn có lỗi **quyền file không đúng**, cho phép người dùng có quyền thấp (low-privilege users) **sửa đổi các file script** có thể được thực thi qua `sudo`. Từ đó, họ có thể **nâng quyền lên root** bằng cách chỉnh sửa các file này trước khi chúng được chạy với đặc quyền cao.
```

Bản ghi `id=acc_kp_0419` (capability=cve_qa; gold: cve_id=CVE-2020-22475, cwe=CWE-276 Incorrect Default Permissions, severity=MEDIUM). `question` (nguyên văn):

> Đề xuất biện pháp khắc phục cho CVE-2020-22475.

`answer` (trích nguyên văn):

```
- **CWE**: [CWE-276](https://cwe.mitre.org/data/definitions/276.html) — *Incorrect Default Permissions*
[...]
- **Lỗ hổng**: Thành phần `VoiceCommandActivity` không đặt `exported="false"`, cho phép bất kỳ ứng dụng nào trên thiết bị gọi activity này mà không cần xác thực hay phân quyền.
```

**Kết luận:** Trợ lý trả lời đúng nhóm Configuration & Deployment Weaknesses — quyền file/sudo sai gây leo thang root/CWE-732 (CVE-2020-28909) và cấu hình mặc định exported sai/CWE-276 (CVE-2020-22475) — trích trực tiếp từ kết quả 1.000 mẫu đã chạy trong answers.jsonl.
