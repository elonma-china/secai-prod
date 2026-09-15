Nhớ ngữ cảnh: Duy trì ngữ cảnh hội thoại qua các lượt tương tác
Bản ghi hỏi–đáp thật `acc_kp_0567` (capability `cve_qa`, tình huống nhiều CVE) — `03. Kpi_results/Model/quality/raw-logs/answers.jsonl`:

**Câu hỏi:**
```
Hệ thống của tôi dính cả CVE-2025-0282 và CVE-2021-25337. Nên xử lý theo thứ tự nào và kế hoạch vá ra sao?
```

**Câu trả lời của hệ thống:**
```
Để tư vấn thứ tự ưu tiên và kế hoạch vá, tôi cần tra cứu chi tiết cả hai CVE này.

 Kết quả cho CVE-2025-0282 không khớp chính xác (chỉ ra các CVE năm khác có đuôi 0282). Tôi sẽ thử query trực tiếp hơn.

 Dưới đây là thông tin chi tiết cho 2 CVE bạn hỏi:

---

## CVE-2025-0282

| Trường | Giá trị |
|---|---|
| **Severity** | CRITICAL |
| **CVSS 3.1** | 9.0 |
| **Vector** | `CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:C/C:H/I:H/A:H` |
| **CWE** | CWE-121 (Stack-based Buffer Overflow), CWE-787 (Out-of-bounds Write) |
| **CISA KEV** | Có (Known Exploited Vulnerability) |
| **Affected Products** | Ivanti Connect Secure < 22.7R2.5, Ivanti Policy Secure < 22.7R1.2, Ivanti Neurons for ZTA Gateways < 22.7R2.3 |

**Mô tả**: Một lỗ hổng **stack-based buffer overflow** (CWE-121 + CWE-787) trong Ivanti Connect Secure, Policy Secure và Neurons for ZTA gateways cho phép **tấn công từ xa, không cần xác thực** (unauthenticated) để thực thi mã độc từ xa (**RCE**).

**Đánh giá**: Đây là CVE **CRITICAL** nằm trong CISA KEV — nghĩa là đã được ghi nhận là đang bị khai thác tích cực trong thực tế. Nếu bạn đang vận hàn
[...]
```

**Kết luận:** Hệ thống giữ ngữ cảnh nhiều CVE trong một câu hỏi và trả lời theo thứ tự ưu tiên xử lý (bản ghi tình huống thật).
