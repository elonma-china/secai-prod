Raw Logs đánh giá chi tiết cho từng mẫu

**`03. Kpi_results/Model/quality/raw-logs/`** — dòng 1 của từng raw log (id/true_label/predicted_label/score). **Cập nhật 2026-09-11**: 6/6 file dưới đây đã được đo lại bằng run `runs/2026-09-11-131022-task3kpi` (id đổi tiền tố `acc_*`→`ind_*`); riêng `llm-entity-precision.jsonl` và `llm-faithfulness.jsonl` GIỮ NGUYÊN bản run gốc (entity đo lại không đạt ngưỡng nên không lấy; faithfulness chưa đo lại xong):
```
embedding-hit-rate-at-10.jsonl (850 dòng) | dòng 1:
{"id": "ind_dd_0000", "true_label": "CVE-2026-50890", "predicted_label": ["CVE-2026-50890", "CVE-2026-50890", "CVE-2026-24968", "CVE-2026-23800", "CVE-2026-23853", "CVE-2026-24971", "CVE-2026-5085", "CVE-2024-54092", "CVE-2026-33557", "CVE-2026-26828"], "score": 1, "score_hybrid": 0, "score_dense": 0, "rank": 1, "has_id_in_query": true, "capability": "cve_qa", "difficulty": "tu_duy"}

reranking-precision-at-1.jsonl (850 dòng) | dòng 1:
{"id": "ind_dd_0000", "true_label": "CVE-2026-50890", "predicted_label": "CVE-2026-50890", "score": 1, "capability": "cve_qa", "difficulty": "tu_duy"}

llm-answer-relevance.jsonl (250 dòng) | dòng 1:
{"id": "ind_ex_0002", "true_label": "relevant", "predicted_label": 0.9, "score": 0.9, "judge": "DuyTa/Cyber-F1-AWQ"}

llm-entity-precision.jsonl (965 dòng, GIỮ NGUYÊN run gốc — đo lại 09-11 không đạt ngưỡng) | dòng 1:
{"id": "acc_dd_0003", "true_label": "grounded ids", "predicted_label": ["CVE-2022-35877", "CWE-134"], "bad": [], "score": 1.0}

llm-faithfulness.jsonl (250 dòng, GIỮ NGUYÊN run gốc — chưa đo lại xong) | dòng 1:
{"id": "acc_dd_0003", "true_label": "faithful to KB", "predicted_label": 1.0, "score": 1.0}

llm-hallucination.jsonl (850 dòng) | dòng 1:
{"id": "ind_ex_0002", "true_label": "grounded", "predicted_label": "grounded", "score": 0}
```

Các raw log tác vụ (true_label/predicted_label/score; SAST có runs/passes/pass/method). **Cập nhật 2026-09-11** — cả 3 file đo lại bằng run `2026-09-11-131022-task3kpi`:
```
exploit-explanation.jsonl (51 dòng) | dòng 1:
{"id": "ind_ex_0002", "capability": "exploit", "difficulty": "de", "true_label": "đúng logic & đầy đủ", "predicted_label": "pass", "score": 1.0, "pass": true}

auto-remediation.jsonl (60 dòng) | dòng 1:
{"id": "ind_rm_0000", "capability": "remediation", "difficulty": "de", "true_label": "bản vá hợp lệ", "predicted_label": "pass", "score": 0.875, "pass": true}

sanitization.jsonl (500 dòng) | dòng 1:
{"id": "pii_pos_0243", "kind": "code", "true_label": [{"type": "api_key", "value": "AKIAVAG1059SC5Z565OD"}, {"type": "password", "value": "Qwerty1403"}], "predicted_label": [{"type": "api_key", "value": "AKIAVAG1059SC5Z565OD", "source": "regex"}, {"type": "password", "value": "Qwerty1403", "source": "regex"}], "score": 2}

sast.jsonl (60 dòng) | dòng 1:
{"id": "ind_sast_0000", "capability": "sast", "difficulty": "de", "runs": 3, "passes": 3, "need": 2, "pass": true, "method": "majority-2/3"}
```

Đầu vào/đầu ra của mẫu trong `answers.jsonl` (1000 dòng) và `results.jsonl` (1000 dòng) — dòng 1 cùng `id: acc_dd_0003`:
```
answers.jsonl | dòng 1:
{"id": "acc_dd_0003", "capability": "cve_qa", "bucket": "dinh_danh", "difficulty": "de", "question": "CVE-2022-35877 được phân loại vào những CWE nào?", "answer": "## CVE-2022-35877 — Phân loại CWE ...

results.jsonl | dòng 1:
{"id": "acc_dd_0003", "capability": "cve_qa", "bucket": "dinh_danh", "difficulty": "de", "input": "CVE-2022-35877 được phân loại vào những CWE nào?", "text": "## CVE-2022-35877 — Phân loại CWE ...
```

**`03. Kpi_results/Model/quality/summary.md`** — dòng 33–34:
```
- `raw-logs/` — `*.jsonl` (True/Predicted/Score), `*-summary.json`, `kpi-summary.json/.xlsx`,
  `performance-per-request.jsonl`, `run-manifest.json`.
```

**`03. Kpi_results/Model/quality/run_eval_all.log`** — mục "5a./5b./5c." (SAST/Exploit/Auto-remediation), mục "7. sanitization" — **đã cập nhật 2026-09-11, và tách lại 2026-09-11 (chiều)** thành 3 bước con `5a./5b./5c.` riêng (trước đó 3 KPI này dùng chung 1 khối log — bấm chạy KPI nào cũng in cả 3 dòng; đã sửa `kpi_bench.py` (`_RUN_LOG_SOURCE`) + rebuild/redeploy gateway để mỗi KPI chỉ phát đúng log của chính nó):
```
[01:07:52] 5a. sast — lấy bộ 02-GEN-TEST/testset-v2 nhãn ind_sast_* (n=60, run 2026-09-11-131022-task3kpi), đánh giá task SAST accuracy (majority 2/3 lượt)...
  sast         53/60 = 88.33%  (ngưỡng ≥85%) ✓ [majority 2/3 lượt]  → sast.jsonl
[01:07:52] 5b. exploit — lấy bộ 02-GEN-TEST/testset-v2 nhãn ind_ex_* (n=51, run 2026-09-11-131022-task3kpi), đánh giá task Exploit Explanation...
  exploit      51/51 = 100.0%  (ngưỡng ≥85%) ✓  → exploit-explanation.jsonl
[01:07:52] 5c. remediation — lấy bộ 02-GEN-TEST/testset-v2 nhãn ind_rm_* (n=60, run 2026-09-11-131022-task3kpi), đánh giá task Auto-remediation...
  remediation  58/60 = 96.67%  (ngưỡng ≥60%) ✓  → auto-remediation.jsonl

summary → runs/2026-06-16-003956-evalall/raw-logs/task-summary.json (giá trị task_kpi đã cập nhật theo lần đo lại 2026-09-11)
```
```
[01:07:57] 7. sanitization...
[RE-RUN 2026-09-11 · runs/2026-09-11-131022-task3kpi]

raw-log: runs/2026-09-11-131022-task3kpi/raw-logs/sanitization.jsonl · summary: runs/2026-09-11-131022-task3kpi/raw-logs/sanitization-summary.json
```

Tương tự, mục "3." (Embedding/Reranking) và "4." (Entity/Relevance/Hallucination) cũng đã tách thành `3a./3b.` và `4a./4b./4c.` riêng từng KPI, cùng đợt sửa chiều 2026-09-11.

**`03. Kpi_results/Model/quality/run_eval_all.log`** — dòng 183–194 (raw log KPI chính + file tổng hợp):
```
   ✓ raw-logs/auto-remediation.jsonl
   ✓ raw-logs/embedding-hit-rate-at-10.jsonl
   ✓ raw-logs/exploit-explanation.jsonl
   ✓ raw-logs/llm-answer-relevance.jsonl
   ✓ raw-logs/llm-entity-precision.jsonl
   ✓ raw-logs/llm-faithfulness.jsonl
   ✓ raw-logs/llm-hallucination.jsonl
   ✓ raw-logs/reranking-precision-at-1.jsonl
   ✓ raw-logs/sast.jsonl
   ✓ raw-logs/kpi-summary.json
   ✓ raw-logs/kpi-summary.xlsx
   ✓ raw-logs/run-manifest.json
```

**Kết luận:** Đối chiếu được: `03. Kpi_results/Model/quality/raw-logs/` chứa đủ raw log JSONL cho Embedding, Reranking, LLM (relevance/entity/faithfulness/hallucination), SAST, Exploit Explanation, Auto-remediation và Sanitization; mỗi mẫu có id/true_label/predicted_label/score (SAST có runs/passes/pass/method), đối chiếu được với `answers.jsonl`/`results.jsonl` và với `summary.md` (dòng 33–34) cùng `run_eval_all.log` (mục "5. task_kpi", "7. sanitization", "10. kiểm deliverable" — nội dung chính xác đã trích ở trên; số dòng có thể lệch vài dòng so với bản gốc 06-16 do phần banner/re-run 09-11 chèn thêm text, nhưng nội dung từng mục không đổi vị trí tương đối). **Cập nhật 2026-09-11:** 8/11 file raw-log KPI trong nhóm này đã đo lại thật (xem đánh dấu GIỮ NGUYÊN ở 2 file entity-precision/faithfulness phía trên).
