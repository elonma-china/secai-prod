# KPI Eval v2 — kết quả

- Sinh lúc: 2026-09-20T16:51:47
- Model bị đo: `DuyTa/Cyber-F1-AWQ` · Judge độc lập: `deepseek/deepseek-chat-v3.1`

| KPI | Ngưỡng | Giá trị | Đạt | Cách đo |
|---|---|---|---|---|
| Hit Rate@10 (KHÔNG oracle) (R2-EMB96) | > 96% | 86.67% | ❌ | Dense(sec-embedding)+BM25 sparse(fastembed Qdrant/bm25) -> RRF(k=60), top-10, hit=gold cve_id on a chunk_kind='core' point. NO exact-CVE-id oracle. |
| Reranker Precision@1 (KHÔNG oracle) (R2-RERANK90) | > 90% | 100.0% | ✅ | rerank top-20 hybrid(non-oracle) candidates with sec-rerank; P@1 = rerank's #1 result cve_id == gold. |
| Faithfulness (RAGAS) (R2-FAITH98) | > 98% | 83.33% | ❌ | RAGAS Faithfulness, judge=deepseek (independent) |
| Answer Relevancy (RAGAS) (R2-RELEV90) | > 90% | 77.12% | ❌ | RAGAS Answer Relevancy (ResponseRelevancy), judge=deepseek + embeddings=paraphrase-multilingual-MiniLM (independent) |
| Entity Precision CVE/CWE (fail-closed) (R2-ENTITY100) | >= 100% | 100.0% | ✅ | regex-extract CVE/CWE ids from real gateway answers -> Qdrant existence check (fail-closed: lookup error excluded from denominator, not counted real) |
| Hallucination Rate (fail-closed) (R2-HALLU2) | < 2% | 0.0% | ✅ | fail-closed hallucination rate = (fabricated CVE/CWE mentions in real answers + non-refusals/fabricated-citation-URLs on known-nonexistent CVE probes) / (checked mentions + checked probes) |
| SAST (LLM-judge rubric) (R2-SAST85) | >= 85% | 100.0% | ✅ | task run through real gateway :8131 chat, graded PASS/FAIL by a direct rubric prompt to the independent judge LLM (fail-closed: unparseable judge output excluded) |
| Giải thích exploit (rubric) (R2-EXPLOIT85) | >= 85% | 100.0% | ✅ | task run through real gateway :8131 chat, graded PASS/FAIL by a direct rubric prompt to the independent judge LLM (fail-closed: unparseable judge output excluded) |
| Auto-remediation (rubric) (R2-REMED60) | >= 60% | 100.0% | ✅ | task run through real gateway :8131 chat, graded PASS/FAIL by a direct rubric prompt to the independent judge LLM (fail-closed: unparseable judge output excluded) |
| Radar coverage (clamped, dẫn xuất) (R2-RADAR90) | >= 90% | 92.09% | ✅ | clamped equal-weight mean of 5 axes: Faithfulness/Relevance/Entity/Reasoning/Safety |
| Email/IP Recall (R2-PII-EMAIL-R) | > 98% | 78.95% | ❌ | Entity-level match (fuzzy-normalized) vs independent gold, bucket=EMAIL_IP |
| Email/IP Precision (R2-PII-EMAIL-P) | > 95% | 93.75% | ❌ | Entity-level match (fuzzy-normalized) vs independent gold, bucket=EMAIL_IP |
| SĐT Recall (R2-PII-PHONE-R) | > 95% | 71.43% | ❌ | Entity-level match (fuzzy-normalized) vs independent gold, bucket=PHONE |
| SĐT Precision (R2-PII-PHONE-P) | > 90% | 100.0% | ✅ | Entity-level match (fuzzy-normalized) vs independent gold, bucket=PHONE |
| API Key Recall (R2-PII-KEY-R) | > 85% | 80.0% | ❌ | Entity-level match (fuzzy-normalized) vs independent gold, bucket=APIKEY |
| API Key Precision (R2-PII-KEY-P) | > 80% | 92.31% | ✅ | Entity-level match (fuzzy-normalized) vs independent gold, bucket=APIKEY |
| Password Recall (R2-PII-PWD-R) | > 80% | 50.0% | ❌ | Entity-level match (fuzzy-normalized) vs independent gold, bucket=PASSWORD |
| Password Precision (R2-PII-PWD-P) | > 75% | 100.0% | ✅ | Entity-level match (fuzzy-normalized) vs independent gold, bucket=PASSWORD |
| PII F1 tổng thể (R2-PII-F1) | > 90% | 81.9% | ❌ | Micro-F1 across all 4 buckets (EMAIL_IP/PHONE/APIKEY/PASSWORD) vs independent gold |
| Chatbot response p50 (R2-RESP15) | <= 15000ms | 9314.3ms | ✅ | p50 of non-streaming POST /ai/chat full response, sequential real queries |
| TTFT (R2-TTFT) | 900–1500ms | 2244.6ms | ❌ | p50 time-to-first-SSE-token, POST /ai/chat/stream |
| Full chatflow (token thật, ~25) (R2-CHATFLOW) | 20–35tok/s | 137.21tok/s | ❌ | end-to-end decode tok/s through gateway SSE: completion tokens counted by tokenizing the reassembled final answer with the REAL model tokenizer (vLLM native /tokenize — the gateway's own SSE carries no usage block) |
| Throughput (token thật) (R2-TPS) | 20–35tok/s | 145.2tok/s | ❌ | decode tok/s from usage.completion_tokens (stream_options.include_usage), generation window = first content delta -> stream end (excludes TTFT/prefill) |
| LLM throughput (token thật) (R2-LLMTPS28) | >= 28tok/s | 145.2tok/s | ✅ | decode tok/s from usage.completion_tokens (stream_options.include_usage), generation window = first content delta -> stream end (excludes TTFT/prefill) |
| Context hiệu quả (mục tiêu 16k) (R2-CTX16K) | >= 12000tok | 40027tok | ✅ | needle-in-haystack: unique fact at ~50% depth, prompt length measured exactly via vLLM /tokenize; effective context = largest tested length still answered correctly |
| RPS capacity (3xA100) (R2-RPS) | >= 0.6req/s | 22.833req/s | ✅ | sustained RPS at the highest clean concurrency level in the ramp (raw LLM, short completions) |
| CCU (dò trần thật) (R2-CCU) | >= 30user | 60user | ✅ | highest concurrency level with 0 errors and p95 latency within bound (raw LLM, short completions) — a REAL ramp, not an echo of the input concurrency |
| Embedding throughput (TẠI-HOST) (R2-EMB1200) | >= 1200emb/s | 1520.4emb/s | ✅ | batched+concurrent /v1/embeddings throughput, measured ON-HOST via SSH (loopback on the serving A100 box, bypassing WAN) |
| Rerank throughput (TẠI-HOST) (R2-RERANK180) | >= 180doc/s | 2771.3doc/s | ✅ | concurrent /v1/rerank throughput, measured ON-HOST via SSH (loopback on the serving A100 box, bypassing WAN) |