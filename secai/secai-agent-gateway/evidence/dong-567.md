Gợi ý phương pháp khai thác tối ưu
Bản ghi hỏi–đáp thật `acc_dd_0365` (capability `poc_search`) — `03. Kpi_results/Model/quality/raw-logs/answers.jsonl`:

**Câu hỏi:**
```
Tìm PoC cho CVE-2022-47986 trong Exploit-DB. Nếu có, cho biết loại exploit (remote, local, webapps hoặc dos) và nền tảng.
```

**Câu trả lời của hệ thống:**
```
## Kết quả tìm kiếm PoC cho CVE-2022-47986

**1 PoC tìm thấy trong Exploit-DB:**

| Trường | Giá trị |
|---|---|
| **Tên lỗ hổng** | IBM Aspera Faspex 4.4.1 — YAML deserialization (RCE) |
| **EDB-ID** | EDB-51316 |
| **CVE** | CVE-2022-47986 |
| **Loại exploit** | **Remote** |
| **Nền tảng (Platform)** | **Multiple** |
| **Loại tệp** | `remote` (web application) |
| **Ngày thêm** | 2023-04-07 |
| **Đường dẫn PoC** | `/opt/exploitdb/exploits/multiple/remote/51316.py` |

### Tóm tắt
- **CVE-2022-47986** là lỗ hổng **YAML deserialization** trong IBM Aspera Faspex 4.4.1, cho phép攻击者 thực thi mã từ xa (**RCE**) thông qua việc tấn công unserialize.
- Đây là exploit dạng **remote**, thuộc nhóm **webapps**, chạy trên nền tảng **multiple** (không giới hạn OS cụ thể — lợi dụng qua HTTP/API).
- PoC là file Python: `51316.py`

Nếu bạn muốn xem chi tiết code PoC, hãy cho tôi biết — tôi sẽ đọc và phân
[...]
```

**Kết luận:** Hệ thống tra cứu PoC/phương pháp khai thác từ Exploit-DB và kho tri thức, trả kết quả có dẫn nguồn (bản ghi thật).
