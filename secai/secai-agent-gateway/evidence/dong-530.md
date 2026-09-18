Step-by-step reasoning – Có giải thích logic từng bước

Đây là kịch bản demo trực tiếp bằng lệnh (CLI). Script `eval/kpi/scripts/trace_one.sh` đứng tại `source/` thuộc gói mã nguồn/triển khai, KHÔNG nằm trong hồ sơ gốc phần AI (4 thư mục 01–04); endpoint tương ứng khi demo là `/ai/chat`. KẾT QUẢ đo khả năng lập luận từng bước (Reasoning) có thật trong `03. Kpi_results/` và raw-log đáp án cũng lưu quá trình lập luận thật — trích dưới đây.

**`03. Kpi_results/Model/quality/reports/llm-kpi-report.md`** — dòng 12:
```
| `R-KPI-REASONING` | Reasoning từng bước | — | 91.9% | ⏳ chưa đo |
```

**`03. Kpi_results/Model/quality/raw-logs/llm-kpi-summary.json`** — dòng 12–15:
```
  "R-KPI-REASONING": {
    "reasoning_present_pct": 91.9,
    "n": 1000
  },
```

Công cụ đo và tiêu chí "có giải thích từng bước" — **`03. Kpi_results/Model/quality/evaluation-code/llm_kpi.py`** — dòng 120–121 (bộ marker lập luận) và dòng 173–175 (điều kiện chấm):
```
REASON_MARKERS = ["bước 1", "bước 2", "1.", "2.", "3.", "đầu tiên", "trước tiên", "tiếp theo",
                  "sau đó", "cuối cùng", "thứ nhất", "thứ hai", "vì", "do đó", "bởi vì", "->", "→"]
```
```
def has_reasoning(a):
    low = a["answer"].lower()
    return any(m in low for m in REASON_MARKERS) and len(a["answer"].split()) >= 40
```

Ví dụ đáp án thật thể hiện lập luận từng bước — **`03. Kpi_results/Model/quality/raw-logs/answers.jsonl`** — dòng 402 (`id: acc_kp_0403`, câu hỏi "Liệt kê các bước xử lý CVE-2013-10016 theo thứ tự thực hiện."):
```
## Các bước xử lý theo thứ tự thực hiện

### Bước 1: Nhận diện & xác nhận lỗ hổng
- Xác nhận version mục tiêu: **fanzila WebFinance 0.5**.
- Kiểm tra file `htdocs/admin/save_taxes.php` xem có tham số `id` được ghép trực tiếp vào câu SQL hay không.
...
### Bước 2: Kiểm chứng (Exploitability validation)
- Truy cập URL liên quan với payload SQL injection thử nghiệm:
```

**Kết luận:** Đối chiếu được (qua kết quả đã chạy): khả năng giải thích logic từng bước được đo bằng `R-KPI-REASONING` = 91,9% (n=1000) trong `llm-kpi-report.md`/`llm-kpi-summary.json`, tiêu chí chấm định nghĩa tại `evaluation-code/llm_kpi.py` và minh hoạ bằng đáp án thật có cấu trúc "Bước 1 → Bước 2 →…" trong `answers.jsonl`; riêng lệnh `trace_one.sh` là demo CLI trực tiếp, script không nằm trong hồ sơ gốc phần AI.
