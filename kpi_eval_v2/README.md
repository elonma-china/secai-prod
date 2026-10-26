# KPI Eval v2 — bộ đánh giá KPI mới (độc lập với `kpi_bench/` cũ)

Mục tiêu: đo **trung thực & khách quan**, khớp yêu cầu hợp đồng. **KHÔNG động vào `kpi_bench/` cũ.**

## Nguyên tắc chống thiên vị (áp dụng cho MỌI KPI)
- **Judge độc lập**: chấm bằng model KHÁC model bị đo (`config.JUDGE_MODEL` = OpenRouter deepseek). Không tự-chấm.
- **Embedding độc lập** cho RAGAS relevancy: `config.RAGAS_EMBED_MODEL` (sentence-transformers đa ngữ), KHÔNG dùng `sec-embedding` của hệ bị đo.
- **Không mớm đáp án**: retrieval đo hybrid/dense THẬT, không có bộ lọc exact-CVE (oracle).
- **Fail-closed**: lỗi tra cứu/parse → KHÔNG mặc định "đạt"; loại khỏi mẫu hoặc tính là lỗi.
- **Gold độc lập, không vòng tròn**: bộ test PII KHÔNG được sinh từ chính regex của bộ dò.
- **Số thật**: token đếm bằng `usage.completion_tokens`; throughput model đo TẠI-HOST; capacity dò trần thật.
- Số nào chưa đo được → `value=None`, `pass=None`, ghi rõ lý do trong `notes` (framework/TODO). **Không bịa số.**

## Kiến trúc
```
kpi_eval_v2/
  config.py      # endpoint/model/key (đọc .env thật), NGƯỠNG (THRESHOLDS)  ── ĐÃ CÓ, dùng chung
  schema.py      # make_result(), eval_pass(), write_summary()             ── ĐÃ CÓ, dùng chung
  run_all.py     # orchestrator: gọi 3 runner, ghi results/                ── ĐÃ CÓ
  quality/run.py       -> run_quality() -> list[dict]     # Agent 1
  performance/run.py   -> run_performance() -> list[dict] # Agent 2
  pii/run.py           -> run_pii() -> list[dict]         # Agent 2
  datasets/      # gold độc lập (PII v2, …)
  results/       # kpi_v2_summary.json + kpi_v2_report.md + evidence *.jsonl
  requirements.txt  # ragas, datasets, sentence-transformers, … (Agent 1 lập)
  .venv/         # venv riêng (ragas CHƯA có trong .venv gateway)
```
Mỗi KPI = 1 dict qua `schema.make_result(id, value, method=..., basis=..., n=..., evidence=..., extra={...})`.
`id` phải khớp key trong `config.THRESHOLDS` (đã định nghĩa sẵn toàn bộ 29 KPI).

## Hệ thống bị đo (từ recon — đã nạp sẵn trong config.py)
- Gateway RAG: `http://127.0.0.1:8131` (header `X-API-Key: config.GATEWAY_KEY`).
- LLM: `Cyber-F1-AWQ` @ `:13905/v1` · Embed: `sec-embedding` @ `:13717` · Rerank: `sec-rerank` @ `:13542`.
- Qdrant: `config.QDRANT_URL`. Gold chất lượng: `config.GOLD_QUALITY_DIR` (`acceptance_indep_*.jsonl`).
- Judge độc lập: `config.JUDGE_MODEL` @ `config.JUDGE_BASE_URL`, key `config.JUDGE_KEY`.

## Danh mục KPI (29) — xem `config.THRESHOLDS`
- **quality (Agent 1):** FAITH98, RELEV90 (RAGAS) · EMB96, RERANK90 (IR không-oracle) · ENTITY100, HALLU2 (fail-closed) · SAST85, EXPLOIT85, REMED60 (rubric judge độc lập) · RADAR90 (gộp có clamp, đánh dấu dẫn xuất).
- **performance (Agent 2):** RESP15, CTX16K, TPS, CHATFLOW, TTFT, RPS, CCU, EMB1200, RERANK180, LLMTPS28.
- **pii (Agent 2):** EMAIL/PHONE/KEY/PWD × (Recall, Precision) + F1 tổng — trên **gold độc lập**.

## Chạy
```
cd secai-agent-gateway
python -m kpi_eval_v2.run_all                 # cả bộ
python -m kpi_eval_v2.run_all --only quality  # 1 nhóm
```
