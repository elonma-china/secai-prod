Phạm vi Cryptographic Weaknesses
Nhóm "Cryptographic Weaknesses" được kiểm chứng bằng các bản ghi hỏi–đáp thật đã chạy trong bộ 1.000 mẫu (`03. Kpi_results/Model/quality/raw-logs/answers.jsonl`).

Bản ghi `id=acc_dd_0163` (capability=cve_qa; gold: cve_id=CVE-2017-11317, cwe=CWE-326 Inadequate Encryption Strength, severity=CRITICAL). `question` (nguyên văn):

> Theo CVSS vector, CVE-2017-11317 có thể bị khai thác qua mạng không? Giải thích.

`answer` (trích nguyên văn, cắt giữa ghi [...]):

```
**Có, CVE-2017-11317 hoàn toàn có thể bị khai thác qua mạng (network).**
[...]
CVE-2017-11317 liên quan đến **Telerik UI for ASP.NET AJAX** (trước R1 2017 và R2 2017 SP2):

- **RadAsyncUpload** sử dụng mã hóa yếu (weak encryption) cho encryption key.
- Kẻ tấn công từ xa có thể **decrypt và forge** dữ liệu Upload, cho phép:
  - **Upload file tùy ý** lên server
  - **Thực thi code từ xa (RCE)** bằng cách upload và trigger shellcode/asmx handler
```

Bản ghi `id=acc_th_0877` (capability=exploit; gold: cve_id=CVE-2021-32520, cwe=CWE-798/CWE-321 Hard-coded Cryptographic Key, severity=CRITICAL). `question` (nguyên văn):

> Giải thích cơ chế của lỗ hổng CVE-2021-32520 (Use of hard-coded cryptographic key vulnerability in QSAN Storage Manager allows attackers to obtain users’ credentials and related permissions. Suggest...). Vì sao lỗ hổng này nguy hiểm?

`answer` (trích nguyên văn):

```
**Cả hai CWE liên quan:**
- **CWE-798** (Use of Hard-coded Credentials): Khóa mật mã cứng-coded tương đương với việc sử dụng hard-coded credential — tất cả thiết bị đều "chia sẻ một bí mật".
- **CWE-321** (Use of Hard-coded Cryptographic Key): Trực tiếp mô tả việc sử dụng khóa mật mã cố định thay vì sinh khóa động.
```

**Kết luận:** Trợ lý trả lời đúng nhóm Cryptographic Weaknesses — mã hoá yếu/CWE-326 (CVE-2017-11317) và khoá mật mã hard-coded/CWE-321 (CVE-2021-32520) — trích trực tiếp từ kết quả 1.000 mẫu đã chạy trong answers.jsonl.
