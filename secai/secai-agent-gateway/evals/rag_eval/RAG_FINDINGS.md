# RAG research — findings + A/B (2026-09-04)

## Baseline (40 query VN, ground-truth CVE thật)
recall@5=0.375 · recall@10=0.375 · MRR=0.321 (15/40 hit@10). **POOR.**
- Câu HIT: mô tả kỹ thuật khớp trực tiếp (buffer overflow sudo, JNDI, Fortinet SSL VPN, F5 iControl).
- Câu FAIL: **tên phổ biến** (Log4Shell/EternalBlue/Zerologon/ProxyShell/BlueKeep/XZ/PrintNightmare/Follina/Spring4Shell/CitrixBleed/ProxyLogon).

## Chẩn đoán
recall@20 (pool trước rerank) chỉ **0.45** → với query tên, CVE đúng **KHÔNG có trong pool 20** ⇒ **RETRIEVAL-MISS**, không phải mis-rank. Nguyên nhân: **chunk KB thiếu ALIAS/tên gọi** (mô tả NVD kỹ thuật, không chứa "Log4Shell"). Phụ: boost recency kéo CVE-2026 lên; không dedup core/context; Neo4j tách rời retrieval (node ID-only).

## A/B — Contextual alias enrichment (fix #1)
Enrich 16 CVE nổi tiếng: prepend "Tên gọi phổ biến: <alias>." vào text + **re-embed dense** (giữ model bge-m3):
**recall 0.375→0.725, MRR 0.321→0.671** (≈gấp đôi, 15→29/40 hit). ⇒ cơ chế được chứng minh.
Lưu ý: POC enrich đúng CVE eval hỏi (proof-of-mechanism). Production cần **nguồn alias toàn diện** (map tên↔CVE cho mọi CVE nổi tiếng) + hook vào ingest (như CVSS overlay) để bền qua re-sync.

## Kết luận & đề xuất (WT1+WT3 hội tụ)
- **Neo4j: GIỮ** nhưng re-scope (chỉ đếm/tra-ngược/đa-hop) + làm giàu node (name + cạnh). Điểm yếu là graph TÁCH RỜI retrieval, không phải "không nên có graph".
- Ưu tiên áp production: **C. Alias/contextual enrichment (đã chứng minh +0.35 recall)** → **ranking fix** (dedup CVE + boost→tie-breaker + hạ RRF_K, không re-index) → **A. graph-augmented fusion** (1-hop Neo4j vào pool) → **B. HyDE cho câu concept**.
- Tham khảo: RAGFlow, MS GraphRAG, LlamaIndex PropertyGraph, CVE-KGRAG; Anthropic Contextual Retrieval (−35..49% miss).

## Deliverables
`evals/rag_eval/{queries.jsonl(40), run_rag_eval.py, baseline_results.json, RAG_FINDINGS.md}`; báo cáo chi tiết trong worktree agent (RAG_ANALYSIS.md, RAG_GITHUB_SURVEY.md).

## A/B áp cải tiến #1 + #3 (2026-09-04, eval 139 query)
| Nhóm | Baseline (post-CVSS) | +#1 alias enrich | +#3 graph-fusion |
|---|---|---|---|
| alias | 0.400 | **0.800** | 0.800 |
| product | 0.600 | 0.750 | 0.750 |
| multi_hop | 0.533 | 0.667 | 0.667 |
| description | 0.560 | 0.640 | 0.640 |
| cwe | 0.067 | 0.067 | **0.133** |
| exact_id | 1.000 | 1.000 | 1.000 |
| **TỔNG recall@10** | **0.504** | **0.667** | **0.675** |

- **#1 alias enrichment [DATA]**: `alias_enrich.py` — prepend "Tên gọi phổ biến: X" + re-embed dense+BM25, 59 CVE nổi tiếng. LIVE trong Qdrant (production ăn ngay). +0.163 tổng.
- **#3 graph-fusion [CODE]**: `qdrant.py::_graph_reverse_candidates` — query class (CWE/CAPEC/T-id) bơm CVE từ Neo4j (KEV/CVSS-ranked) vào pool rerank. CHỈ kích hoạt cho class query → zero regression. cwe metric x2; kiểm chứng functional trả CVE lớp đúng (nhãn eval hẹp nên metric under-measure). CẦN deploy vào container để prod dùng.
