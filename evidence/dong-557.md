Cung cấp nhân sự miễn phí để kiểm chứng độc lập với 01 bộ test mới; sai lệch kết quả không vượt quá 5%, vượt ngưỡng sẽ fine-tune mô hình miễn phí trước nghiệm thu.

Kịch bản mô tả quy trình kiểm chứng độc lập thực hiện tại buổi nghiệm thu với 01 bộ test mới do hai bên thống nhất (không trùng bộ huấn luyện và bộ KPI chính). Vì bộ test độc lập phụ thuộc lựa chọn tại buổi nghiệm thu nên hồ sơ gốc không có sẵn kết quả tiền-trích cho riêng bộ đó; đây là kịch bản chạy trực tiếp khi nghiệm thu.

Công cụ và mốc đối chiếu hỗ trợ quy trình này có sẵn trong hồ sơ gốc:

- Mã đánh giá KPI: `03. Kpi_results/Model/quality/evaluation-code/` (`acceptance_runner.py`, `llm_kpi.py`, `retrieval_kpi.py`, `task_kpi.py`, `faith_judge.py`).
- Baseline đã nghiệm thu để so sánh: `03. Kpi_results/Model/quality/reports/baseline-comparison.xlsx`.

Ngưỡng sai lệch 5% và cơ chế fine-tune lại khi vượt ngưỡng được mã hóa trong chính sách quality-gate:

**`02. Bo_build_finetuning/finetune/configs/finetune_thresholds.yml`** — dòng 5 (điều kiện kích hoạt retrain) và dòng 14 (ngưỡng suy giảm tương đối 5%):
````
  "trigger": "A failed required check means its finetune_target should be retrained. Missing reports are not a failure; they mean the component was not assessed.",
````
````
      "metric": "recall@10",
      "min": 0.96,
      "max_relative_drop": 0.05,
````

**Kết luận:** Kịch bản là quy trình nghiệm thu độc lập chạy trực tiếp: kết quả trên bộ test độc lập chưa có sẵn trong hồ sơ gốc vì phụ thuộc bộ test hai bên thống nhất khi nghiệm thu; ngưỡng sai lệch 5% (`max_relative_drop: 0.05`) và cơ chế fine-tune lại khi vượt ngưỡng đã được mã hóa trong `finetune_thresholds.yml`, cùng mã đánh giá và baseline có sẵn để chạy đối chiếu.
