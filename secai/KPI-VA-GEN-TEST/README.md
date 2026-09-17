# Bộ nghiệm thu KPI AI — secAI (KPI AI · Performance · Sanitization)

Gói đánh giá **độc lập** (tách khỏi mã sản phẩm `packages/`), bám `docs/task-AI-requirements.md`.
Sinh **raw logs True/Predicted/Score**, **báo cáo riêng từng mô hình**, **radar chart**, **tổng hợp JSON/Excel**,
**phân tích False Positive** — đúng yêu cầu `R-EVAL-RAWLOGS`.

> Model mặc định **deepseek-v4-flash** (đổi sang Qwen: set `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL` rồi chạy lại — harness model-agnostic).

## ⭐ Chạy turnkey (1 lệnh, endpoint-driven) — `bench.sh`

Cấp URL các service qua `.env.bench` rồi chạy **một lệnh**, không cần SSH/VPS. Mọi kết quả
gom vào **một folder timestamped** `runs/<YYYY-MM-DD-HHMMSS>/`.

```bash
cd eval/kpi
cp .env.bench.example .env.bench     # điền LLM/EMBED/RERANK/QDRANT/ACC_BASE + provider
bash scripts/bench.sh                # full 1000 mẫu (chạy lâu — full agent qua backend)
bash scripts/bench.sh --limit 5      # smoke nhanh
```

`bench.sh` tự: **preflight** 5 endpoint + BM25 → **Phase 0** drive full agent secAI sinh đáp án
(1000 mẫu, `--resume`) → **1–6** retrieval/LLM-KB/perf/task/sanitization(500)/faithfulness →
**7** build reports + deliverable. Output:

```
eval/kpi/runs/<ts>/
  summary.md                      # bảng KPI + endpoint đã dùng + đường dẫn artifact
  raw-logs/   *.jsonl (True/Predicted/Score), kpi-summary.json/.xlsx,
              performance-per-request.jsonl, run-manifest.json (ghi endpoint thực)
  reports/    ai-task-kpi/embedding/reranking/llm-kpi/performance/sanitization-report.md,
              radar.png, improvement-plan.md, error-analysis-and-improvement-plan.md,
              false-negative-analysis.md, false-positive-analysis.md, failed-samples.xlsx
runs/latest -> <ts>               # symlink run mới nhất
```

Yêu cầu reachable (chọn "đủ cả stack"): **LLM + Embedding + Reranker + Qdrant cve_kb + backend secAI**.
BM25 artifacts dùng local `data/knowledge_base/bm25_cve_kb/` (bench.sh tự set `DATA_ROOT`).

> Biến thể cũ chạy qua **VPS `ssh secai`** vẫn còn ở `scripts/run_all.sh` (loopback embed:8010/rerank:8011/qdrant:6333).

## Chỉ tiêu ↔ Metric (bám hợp đồng)

| Mã hợp đồng | Metric đo | Script | Chạy ở |
|-------------|-----------|--------|--------|
| `R-KPI-EMB96` Hit Rate@10 >96% | hybrid dense+BM25, gold=cve_id | `retrieval_kpi.py` | VPS |
| `R-KPI-RERANK90` Precision@1 >90% | rerank top-1 | `retrieval_kpi.py` | VPS |
| `R-KPI-FAITH98` Faithfulness >98% | RAGAS faithfulness (LLM judge) | `llm_kpi.py` (RAGAS) | LOCAL |
| `R-KPI-RELEV90` Answer Relevance >90% | cosine(answer, question) bge | `llm_kpi.py` | VPS/tunnel |
| `R-KPI-ENTITY100` Entity Precision =100% | regex CVE/CWE grounded vs KB | `llm_kpi.py` | VPS |
| `R-KPI-HALLU2` Hallucination <2% | id bịa / trap-CVE giả | `llm_kpi.py` | VPS |
| `R-KPI-REASONING` | heuristic bước suy luận | `llm_kpi.py` | offline |
| `R-KPI-RADAR90` coverage >90% | 5 trục radar | `build_reports.py` | LOCAL |
| `R-LLM-SAST85` ≥85% | grader SAST | `task_kpi.py` | LOCAL |
| `R-LLM-EXPLOIT85` ≥85% | grader exploit | `task_kpi.py` | LOCAL |
| `R-LLM-REMED60` ≥60% | grader remediation | `task_kpi.py` | LOCAL |
| `R-PERF-*`, `R-CTRL-RESP15` | TTFT/TPS/response/throughput/CCU | `perf_kpi.py` | VPS |
| `R-SEC-PII` F1 >90% | detector PII/Secret + dataset | `sanitization_kpi.py` | LOCAL |

## Cấu trúc

```
eval/kpi/
  config.py              # env model-agnostic + ngưỡng KPI
  datasets/
    sanitization_pii.jsonl    # bộ test PII/secret có nhãn (gen_pii_dataset.py)
  sanitizer/
    detector.py          # regex + entropy classifier; detect() & redact()
    gen_pii_dataset.py    # sinh dataset PII có nhãn + hard negatives
  scripts/
    retrieval_kpi.py     # [VPS] Hit@10 + P@1 + contexts (dense+BM25+rerank)
    bm25_py.py           # đọc BM25 CSC pure-python (không numpy)
    gen_answers.py       # answers.jsonl từ run acceptance (run2)
    llm_kpi.py           # [VPS/LOCAL] faithfulness(RAGAS)/relevance/entity/hallucination/reasoning
    task_kpi.py          # [LOCAL] accuracy SAST/exploit/remediation
    perf_kpi.py          # [VPS] TTFT/TPS/response/throughput/CCU
    sanitization_kpi.py  # [LOCAL] recall/precision/F1 PII + confusion + FP analysis
    build_reports.py     # [LOCAL] reports .md + radar.png + xlsx + run-manifest.json
    run_all.sh           # điều phối
  raw-logs/              # *.jsonl (True/Predicted/Score) + *-summary.json + kpi-summary.{json,xlsx} + run-manifest.json
  reports/               # ai-task-kpi-report.md, embedding/reranking/llm-kpi/performance/sanitization-report.md, radar.png
```

## Chạy từng phần

```bash
# ── chuẩn bị answers (1 lần, từ run acceptance đã có) ──
python3 scripts/gen_answers.py

# ── [VPS] retrieval + perf + LLM-KB metrics (rsync eval/kpi → /opt/secai/kpi) ──
ssh secai 'cd /opt/secai/kpi/scripts && DATA_ROOT=/opt/secai/data python3 retrieval_kpi.py'
ssh secai 'cd /opt/secai/kpi/scripts && DATA_ROOT=/opt/secai/data python3 llm_kpi.py --no-ragas'   # entity/hallu/reasoning/relevance
ssh secai 'cd /opt/secai/kpi/scripts && DATA_ROOT=/opt/secai/data python3 perf_kpi.py'

# ── [LOCAL] task accuracy + sanitization + faithfulness(RAGAS) ──
python3 scripts/task_kpi.py
python3 sanitizer/gen_pii_dataset.py && python3 scripts/sanitization_kpi.py
# faithfulness: pull contexts.jsonl + answers.jsonl từ VPS rồi:
LLM_API_KEY=$DEEPSEEK_API_KEY python3 scripts/llm_kpi.py --no-embed --no-kb   # chỉ faithfulness

# ── tổng hợp báo cáo ──
python3 scripts/build_reports.py --model deepseek-v4-flash --date $(date +%F)
```

Hoặc `bash scripts/run_all.sh` (tự điều phối VPS↔local).

## Đổi model (Qwen / khác)

```bash
LLM_BASE_URL=https://<runpod>/v1 LLM_API_KEY=$RUNPOD_API_KEY LLM_MODEL=Qwen/Qwen3-30B-A3B-FP8 \
  bash scripts/run_all.sh   # sinh lại toàn bộ report cho model mới
```
(Retrieval/sanitization không phụ thuộc LLM → giữ nguyên; chỉ LLM-KPI + perf chạy lại.)

## Phương pháp & lưu ý

- **Test set**: tái dùng 1000 mẫu `eval/datasets-acceptance` (40-40-20), gold = `metadata.cve_id/cwe`.
- **Faithfulness**: RAGAS (LLM judge=DeepSeek) trên (question, answer, contexts truy hồi); chạy subset đại diện nếu cần kiểm soát chi phí.
- **Entity/Hallucination grounding**: đối chiếu KB Qdrant thật (id có tồn tại không), không chỉ so gold.
- **Performance**: đo trên **stack deploy thực** (deepseek cloud + bge endpoint), **không phải 3×A100/Qwen on-prem** như cam kết phần cứng — số liệu mang tính tham chiếu, đổi sang Qwen on-prem để đối chiếu ngưỡng A100.
- **False-Positive analysis**: mỗi report có mục FP + raw log để truy ca sai (R-EVAL-RAWLOGS).
- **Kiểm chứng độc lập (R-EVAL-INDEP)**: harness tái lập được; chủ đầu tư có thể đưa bộ test mới, sai lệch ≤5%.
