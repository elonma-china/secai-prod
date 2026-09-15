Báo cáo phân tích sai, False Positive và kế hoạch cải thiện

Thư mục `03. Kpi_results/Model/quality/reports/` có đủ ba báo cáo được nêu (kèm các báo cáo khác):

```
ai-task-kpi-report.md
baseline-comparison.xlsx
embedding-report.md
error-analysis-and-improvement-plan.md
failed-samples.xlsx
false-negative-analysis.md
false-positive-analysis.md
improvement-plan.md
llm-kpi-report.md
radar-data.json
radar-error.txt
radar.svg
reranking-report.md
sanitization-report.md
```

**`03. Kpi_results/Model/quality/reports/false-positive-analysis.md`** — dòng 20–40 (kết quả tổng hợp + bảng ID mẫu, dấu hiệu, nguyên nhân):

```
## 2. Kết quả tổng hợp

- `llm-hallucination.jsonl`: 8/1.000 mẫu bị đánh dấu `hallucinated`, tương ứng 0,8%; KPI < 2% nên **đạt**.
- `llm-entity-precision.jsonl`: 965 mẫu có nhắc CVE/CWE, tất cả có `bad=[]`, Entity Precision được tổng hợp là 100%.
- 35/1.000 mẫu không nhắc CVE/CWE nên không nằm trong mẫu số Entity Precision.
- Không có lỗi gọi model: `results.jsonl` và `answers.jsonl` đều có đủ 1.000 ID duy nhất, không có trường `error` và không có câu trả lời rỗng.

## 3. Phân tích 8 ca bị đánh dấu False Positive/Hallucination

| ID mẫu | Dấu hiệu trong kết quả thô | Đánh giá nguyên nhân |
|---|---|---|
| `acc_kp_0600` | Câu hỏi/gold là `CVE-2025-14174`, nhưng phần tham chiếu ghi `CVE-2025-1414174` trong khi URL lại trỏ về `CVE-2025-14174`. | Lỗi sao chép ID CVE khi sinh nội dung. Đây là False Positive có độ tin cậy cao; cần chặn ID không có trong tập thực thể truy hồi. |
| `acc_kp_0796` | Gold là `CWE-328`; câu trả lời lần lượt đưa ra `CWE-768`, `CWE-916`, `CWE-321`, `CWE-763`, `CWE-769`, `CWE-798`, `CWE-920` và tự nhận một số ánh xạ “không chính xác”. | Model suy diễn quá mức và tự sửa nhiều lần, tạo ánh xạ CWE mâu thuẫn. Cần dùng danh mục CWE kiểm soát và chỉ xuất nhãn đã xác minh. |
```

(bảng còn tiếp các ID `acc_th_0806`, `acc_th_0831`, `acc_th_0855`, `acc_th_0871`, `acc_th_0900`, `acc_th_0936`; dòng 40 ghi rõ tham chiếu tới kết quả thô: "Chi tiết câu hỏi, gold label và toàn bộ câu trả lời của từng ID trên nằm trong `../raw-logs/answers.jsonl`; nhãn phát hiện nằm tại các bản ghi cùng ID trong `../raw-logs/llm-hallucination.jsonl`.")

**`03. Kpi_results/Model/quality/reports/false-negative-analysis.md`** — dòng 20–33 (False Negative tầng Embedding: tỷ lệ lỗi, ID mẫu, nguồn truy vết raw-logs):

```
## 2. False Negative tại tầng Embedding

Kết quả: 9/735 mẫu không có gold CVE trong top-10, tỷ lệ lỗi 1,224%; Hit Rate@10 = 98,78%, đạt ngưỡng > 96%.

Các ID lỗi: `acc_dd_0344`, `acc_dd_0346`, `acc_dd_0348`, `acc_dd_0350`, `acc_dd_0352`, `acc_dd_0354`, `acc_dd_0356`, `acc_dd_0360`, `acc_dd_0362`.
```

Dòng 51–68 (False Negative nhiệm vụ SAST: bảng nhóm/độ khó/CWE/ID lỗi + nguyên nhân, có tham chiếu `answers.jsonl`, `results_sast_2.jsonl`, `results_sast_3.jsonl`):

```
## 4. False Negative của nhiệm vụ SAST

Kết quả: 9/60 mẫu không đạt majority 2/3; SAST accuracy = 51/60 = 85%, vừa bằng ngưỡng nghiệm thu.

| Nhóm | Kết quả |
|---|---|
| Theo độ khó | 4 `de`, 3 `trung_binh`, 1 `kho`, 1 `tu_duy`. |
| Theo CWE kỳ vọng | `CWE-22`: 3; `CWE-89`: 2; `CWE-611`, `CWE-434`, `CWE-327`, `CWE-79`, `CWE-95`: mỗi loại 1. |
| ID lỗi | `acc_kp_0684`, `acc_kp_0688`, `acc_kp_0692`, `acc_kp_0696`, `acc_kp_0705`, `acc_kp_0713`, `acc_kp_0720`, `acc_kp_0731`, `acc_kp_0738`. |
```

**`03. Kpi_results/Model/quality/reports/error-analysis-and-improvement-plan.md`** — dòng 5–16 (kết luận tổng quan, xác nhận KPI vẫn đạt ngưỡng):

```
## 1. Kết luận tổng quan

Bộ kết quả có đủ 1.000/1.000 mẫu trong `../raw-logs/results.jsonl` và `../raw-logs/answers.jsonl`, không trùng ID, không có lỗi gọi model và không có câu trả lời rỗng. Theo `../raw-logs/kpi-summary.json`, 11/11 KPI chất lượng đạt ngưỡng.

Tuy nhiên, đọc chi tiết kết quả thô cho thấy bốn vùng cần cải thiện:

1. Retrieval theo tên sản phẩm: 9 lỗi Hit@10 và 13 lỗi P@1, tập trung ở `poc_search` không chứa CVE ID.
2. SAST: 9/60 mẫu không đạt; nguyên nhân chính là file nguồn không có trong môi trường chạy.
3. Grounding đầu ra: 8/1.000 mẫu bị đánh dấu hallucination, gồm sai ID CVE, ánh xạ CWE mâu thuẫn và mở rộng thực thể ngoài context.
4. Chất lượng hồ sơ: Hallucination không thống nhất với Entity Precision; số mẫu Faithfulness trong summary không khớp Raw Log.
```

Dòng 55–68 (Kế hoạch cải thiện: bảng Ưu tiên / Hành động / Cách thực hiện / Tiêu chí hoàn thành):

```
## 4. Kế hoạch cải thiện

| Ưu tiên | Hành động | Cách thực hiện | Tiêu chí hoàn thành |
|---|---|---|---|
| P0 | Hoàn thiện test fixture SAST | Bàn giao đủ file dưới `eval/vuln-samples/acceptance/`; ghi SHA-256 vào manifest; kiểm tra đường dẫn/mount trước khi chạy. | 60/60 file đọc được; không còn câu trả lời “file không tồn tại”. |
| P0 | Loại rò nhãn SAST | Đổi tên file thành ID trung tính, không chứa `cweNN`; gold CWE chỉ nằm trong evaluator. | Model không thể suy ra nhãn từ tên file; SAST vẫn đạt ≥85%. |
| P0 | Đồng bộ Raw Logs | Sinh Hallucination và Entity Precision trong cùng run; thêm `run_id`, hash dataset, hash evaluator, `reason_code`, `unsupported_entities`, `evidence_ids`. | Mọi mẫu hallucination có lý do tái kiểm tra được; không còn mâu thuẫn `hallucinated` với `bad=[]`. |
```

(bảng còn tiếp các hành động P0/P1/P2 tới dòng 68). Dòng 70–77 là "Quy trình tái kiểm tra" và dòng 79–86 là "Tiêu chí đóng hành động cải thiện" (mức ngưỡng tái kiểm tra: Hit@10 >96%, P@1 >90%, Faithfulness >98%, Answer Relevance >90%, Entity Precision =100%, Hallucination <2%, SAST ≥85%, Exploit Explanation ≥85%, Auto-remediation ≥60%).

**Kết luận:** Đối chiếu được: ba báo cáo tồn tại đúng trong `03. Kpi_results/Model/quality/reports/`, có thống kê mẫu lỗi, ID mẫu, nguyên nhân và tham chiếu kết quả thô trong `raw-logs/`; kế hoạch cải thiện có biện pháp, mức ưu tiên (P0/P1/P2) và tiêu chí tái kiểm tra, và báo cáo ghi 11/11 KPI chất lượng đạt ngưỡng.
