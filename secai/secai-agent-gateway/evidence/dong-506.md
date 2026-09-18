Phạm vi Logic & Business Logic Errors
Nhóm "Logic & Business Logic Errors" được kiểm chứng bằng các bản ghi hỏi–đáp thật đã chạy trong bộ 1.000 mẫu (`03. Kpi_results/Model/quality/raw-logs/answers.jsonl`).

Bản ghi `id=acc_th_0900` (capability=exploit; câu hỏi khái niệm về lỗi logic, ánh xạ CWE-367 TOCTOU / CWE-362 Race Condition). `question` (nguyên văn):

> Race condition (TOCTOU) bị khai thác như thế nào trong thực tế?

`answer` (trích nguyên văn, cắt giữa ghi [...]):

```
**TOCTOU** (Time of Check to Time of Use) xảy ra khi giữa hai bước:

1. **Check** — kiểm tra điều kiện (file tồn tại? user có quyền? file chưa tồn tại?)
2. **Use** — thực thi hành động (ghi file? tạo file mới? mở file?)

Thì giữa hai bước này một tác nhân khác (process/thread khác) có thể **thay đổi trạng thái**, khiến kết quả "check" lúc đầu **không còn đúng** khi tới lúc "use".
[...]
Attacker gửi **đồng thời 2 request** với cùng session token trước khi token bị invalidate → bypass rate limit, double spend, hoặc lấy tài nguyên gấp đôi.
[...]
| **CWE** | CWE-367 (TOCTOU), CWE-59 (Symlink Following) |
```

Bản ghi `id=acc_kp_0578` (capability=cve_qa; gold: cve_id=CVE-2024-29181, cwe=CWE-639 Authorization Bypass Through User-Controlled Key, severity=LOW). `question` (nguyên văn):

> CVE-2024-29181 thuộc loại điểm yếu CWE-639. Biện pháp khắc phục đặc thù cho loại điểm yếu này là gì?

`answer` (trích nguyên văn):

```
Theo đúng nguyên tắc phân quyền, Author chỉ nên thấy danh sách các item **do họ tạo**, nhưng lỗ hổng khiến họ vô tình nhìn thấy **toàn bộ** các item liên kết từ các collection khác. Đây là dạng **CWE-639: Authorization Bypass Through User-Controlled Key** — cơ chế ủy quyền không kiểm tra đúng quyền sở hữu (ownership) trên mỗi item trong mối quan hệ liên kết.
```

**Kết luận:** Trợ lý trả lời đúng nhóm Logic & Business Logic Errors — cơ chế race/TOCTOU và double-spend ở tầng nghiệp vụ (CWE-367/362) và lỗi ủy quyền theo khoá do người dùng kiểm soát/CWE-639 (CVE-2024-29181) — trích trực tiếp từ kết quả 1.000 mẫu đã chạy trong answers.jsonl.
