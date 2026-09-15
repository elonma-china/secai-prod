Phạm vi Injection
Bộ kiểm thử chất lượng gồm 1000 câu hỏi–đáp thật đã chạy trên hệ thống, log tại `HO-SO-GOC-NGHIEM-THU-AI/03. Kpi_results/Model/quality/raw-logs/answers.jsonl`. Dưới đây là bản ghi hệ thống đã trả lời đúng chủ đề của chỉ tiêu này, trích nguyên văn câu hỏi và câu trả lời của hệ thống.

**Bản ghi `acc_th_0834`** — capability `pentest`, nhóm `tinh_huong`; công cụ hệ thống đã gọi khi trả lời: trả lời trực tiếp, không gọi công cụ RAG.

Câu hỏi (trích nguyên văn):

Cách kiểm tra lỗ hổng SQL injection trên một form đăng nhập một cách có kiểm soát trong pentest?

Trả lời của hệ thống (trích nguyên văn, đã lược bớt phần giữa và đánh dấu `[...]`):

Đây là một câu hỏi hoàn toàn nằm trong scope **Pentest** của tôi. Dưới đây là hướng dẫn có kiểm soát để kiểm thử SQL Injection trên form đăng nhập.

---

## Quy trình kiểm thử SQL Injection trên Form Login

### 1. Chuẩn bị môi trường
- Sử dụng **lab riêng** hoặc môi trường đã có **authorization bằng văn bản** từ chủ sở hữu ứng dụng.
- Tuyệt đối không thử trên production hoặc hệ thống chưa được phê duyệt.

### 2. Nhận diện parameter
Form login thường POST lên endpoint như `/login`, `/auth`, `/api/v1/login` với các tham số:
```
username=admin&password=admin123
```
Bạn cần xác định đúng **tham số** và **method** (GET/POST).

### 3. Thử nghiệm có kiểm soát (theo tầng)

#### Bước A: Thử payload đơn giản
Gửi request với các payload sau, quan sát response xem có khác biệt không:

| Payload | Mục đích |
|---|---|
| `' OR '1'='1` | Kiểm tra boolean-based blind |
| `' OR 1=1 --` | Kiểm tra comment-based bypass |
| `' UNION SELECT NULL--` | Kiểm tra UNION-based |
| `admin' --` | Kiểm tra comment-based bypass với username hợp lệ |
| `1; DROP TABLE users--` | Kiểm tra stacked query (cảnh báo: **chỉ trong lab**) |

Dùng **Burp Suite** intercept request, sửa giá trị `username` hoặc `password`, forward request.

[...]

- Input validation: check username/password length, reject `--`, `;`, `'`
- Log và alert khi phát hiện SQLi pattern

---

## Tài liệu tham khảo
- **CWE-89** — [SQL Injection](https://cwe.mitre.org/data/definitions/89.html)
- **CAPEC-66** — [SQL Injection](https://capec.mitre.org/data/passed/66)
- **OWASP Testing Guide** — [Testing for SQL Injection (OTG-INVIT-005)](https://owasp.org/www-project-web-security-testing-guide/)

Bạn muốn tôi tìm chi tiết hơn về một kỹ thuật cụ thể (blind, UNION-based, second-order) hay tìm PoC cho một CVE cụ thể liên quan đến SQLi không?

---

**Kết luận:** Hệ thống giải thích đúng cơ chế, dấu hiệu nhận biết và cách phòng tránh SQL Injection trên form đăng nhập (CWE-89, tham số hoá truy vấn/prepared statement, input validation, WAF) — đạt yêu cầu phạm vi Injection.
