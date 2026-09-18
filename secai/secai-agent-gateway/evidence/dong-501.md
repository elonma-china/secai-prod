Phạm vi Memory Safety
Bộ kiểm thử chất lượng gồm 1000 câu hỏi–đáp thật đã chạy trên hệ thống, log tại `HO-SO-GOC-NGHIEM-THU-AI/03. Kpi_results/Model/quality/raw-logs/answers.jsonl`. Dưới đây là bản ghi hệ thống đã trả lời đúng chủ đề của chỉ tiêu này, trích nguyên văn câu hỏi và câu trả lời của hệ thống.

**Bản ghi `acc_dd_0141`** — capability `cve_qa`, nhóm `dinh_danh`; công cụ hệ thống đã gọi khi trả lời: `qdrant_search` → `neo4j_search` → `qdrant_search` → `neo4j_search` → `neo4j_search`.

Câu hỏi (trích nguyên văn):

Dựa trên thông tin trong KB, CVE-2018-3610 thuộc nhóm lỗ hổng nào (ví dụ: SQL injection, XSS, buffer overflow...)? Giải thích ngắn gọn.

Trả lời của hệ thống (trích nguyên văn):

## CVE-2018-3610 — Phân loại

- **Nhóm lỗ hổng**: **Bộ nhớ (Memory corruption)** — cụ thể là **buffer overflow / bộ nhớ không an toàn**
- **CWE**: **CWE-119** — *Improper Restriction of Operations within the Bounds of a Memory Buffer* (ràng buộc không đúng các thao tác trong biên của bộ nhớ đệm)
- **CAPEC** liên quan: CAPEC-45 (Buffer Overflow), CAPEC-14 (Buffer Overflow), CAPEC-9, CAPEC-123 — đều là các mẫu tấn công lợi dụng ghi tràn bộ nhớ
- **Severity**: **MEDIUM** (CVSS 6.0)
- **Sản phẩm bị ảnh hưởng**: `driver_&_support_assistant` (Intel Driver & Support Assistant)
- **CVSS v3 vector**: `AV:L / AC:L / PR:L / UI:N / S:U / C:N/I:N/A:H` — cần **local access**, **low privileges**, **low complexity** để khai thác, tác động chính là **Availability (A:H)**.

### Giải thích ngắn

CVE-2018-3610 là lỗ hổng **lợi dụng ghi tràn bộ nhớ (buffer overflow)** trong Intel Driver & Support Assistant. Kẻ tấn công đã có mã thực thi ở mức low-privileged trên hệ thống có thể thao tác ghi dữ liệu vượt ra ngoài biên của buffer, từ đó gây **mất ổn định dịch vụ (DoS)** hoặc trong một số trường hợp có thể leo thang đặc quyền. CVSS 6.0 phản ánh mức độ nghiêm trọng trung bình: cần điều kiện local + low privilege, tác động chủ yếu là Availability.

---

**Bản ghi `acc_dd_0133`** — capability `cve_qa`, nhóm `dinh_danh`; công cụ hệ thống đã gọi khi trả lời: `qdrant_search`.

Câu hỏi (trích nguyên văn):

Dựa trên thông tin trong KB, CVE-2025-47127 thuộc nhóm lỗ hổng nào (ví dụ: SQL injection, XSS, buffer overflow...)? Giải thích ngắn gọn.

Trả lời của hệ thống (trích nguyên văn):

## CVE-2025-47127 — Out-of-Bounds Write (CWE-787)

**Nhóm lỗ hổng:** **Out-of-Bounds Write** — một dạng **buffer overflow** (ghi vượt vùng nhớ được phân bổ).

**Giải thích ngắn:**

- **CWE-787** là lỗ hổng bộ nhớ, nơi chương trình ghi dữ liệu ra ngoài ranh giới của bộ đệm (buffer) đã được cấp phát. Điều này có thể ghi đè lên các cấu trúc bộ nhớ lân cận (như con trỏ hàm, con trỏ trả về), từ đó cho phép kẻ tấn công thực thi **mã tùy ý (arbitrary code execution)**.

- **Severity: HIGH (CVSS 3.1: 7.8)**
- **Sản phẩm bị ảnh hưởng:** Adobe FrameMaker (các phiên bản 2020.8, 2022.6 và trước đó)
- **Vector tấn công:**
  - `AV:L` — cần **local access** (tiếp cận cục bộ)
  - `UI:R` — **cần tương tác người dùng** — nạn nhân phải mở một tệp độc hại
  - `PR:N` — không cần quyền đặc quyền (permission)
- **Nhãn bổ sung:** `buffer_overflow`, `memory_corruption`

**Tóm lại:** CVE-2025-47127 là một lỗ hổng **ghi tràn bộ nhớ (buffer overflow / out-of-bounds write)** trong Adobe FrameMaker, cho phép thực thi mã tùy ý nếu người dùng mở một file độc hại đã được kẻ tấn công thiết kế sẵn.

---

**Kết luận:** Hệ thống phân loại và giải thích đúng nhóm lỗ hổng an toàn bộ nhớ — buffer overflow (CWE-119) và out-of-bounds write (CWE-787) — kèm cơ chế, tác động (DoS/thực thi mã tùy ý) và điều kiện khai thác — đạt yêu cầu phạm vi Memory Safety.
