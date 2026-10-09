# Bộ tiêu chí chấm điểm: KB tri thức + search scope

Áp dụng cho plan [kb-knowledge-grounding.md](kb-knowledge-grounding.md). Chấm sau khi code xong,
bởi reviewer độc lập (không phải người code). Mọi số đo phải có lệnh tái lập và file kết quả.

**Đạt** = qua TẤT CẢ cổng G1-G6 **và** tổng điểm S ≥ 80/100.
Baseline = số đo P0 trên cùng môi trường, cùng model serving, trước khi nạp dữ liệu (cờ tắt).

Baseline đã đo offline ngày 2026-10-06 (reviewer độc lập):

| Mục | Giá trị |
|---|---|
| pytest toàn repo | 1786 pass, 2 fail, 3 skip. Fail sẵn: `tests/multilang/test_api.py::test_composition_off_on_legacy_and_cors`, `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal` |
| Retrieval CVE (`eval_set_results.json`, n=117) | recall@5 0.675, recall@10 0.675, MRR 0.599 |
| Route `eval_set.jsonl` (n=139) | concept 97, entity 30, id_lookup 12 |
| 20 câu khái niệm/khắc phục | 19 `concept` (không tra KB), 1 `taxonomy`; 0% được grounding |
| Qdrant `cve_kb_hybrid` | 763.518 point, dense 1024 Cosine, sparse `bm25` idf; 0 point knowledge kind |

## A. Cổng bắt buộc (fail một cổng = không đạt)

| ID | Tiêu chí | Ngưỡng | Cách đo |
|---|---|---|---|
| G1 | Không giảm chất lượng tra CVE | `evals/rag_eval/eval_set.jsonl` với `scope="entity"` (mặc định): recall@10 và MRR tổng ≥ baseline − 0.01; không nhóm nào giảm > 0.03. Hồi quy cả profile `exploit`/report gọi `qdrant_search` | `run_eval_set.py` trước và sau khi nạp dữ liệu + code mới |
| G2 | Không vỡ test | pytest toàn repo: 0 lỗi ngoài 2 fail baseline đã liệt kê; `test_qdrant_fastpath.py`, `test_chat_grounding_hook.py` xanh | `pytest -q` |
| G3 | Rollback được và cờ tắt = hành vi cũ | (a) rollback theo `ingest_batch` đưa point count về đúng baseline, BM25 rebuild lại; (b) `classify_chat_grounding(prompt, knowledge_enabled=False)` cho kết quả y hệt baseline trên toàn bộ prompt của eval set cũ + mới; cờ tắt thì `_ground_chat_prompt` không prefetch tri thức | Script rollback trên môi trường thử; test golden |
| G4 | License và nguồn | 100% chunk tri thức có `license`, `attribution`, `source_url`, `version` không rỗng; 0 chunk từ danh sách loại (HackTricks, PortSwigger, CIS Benchmarks, Semgrep, dataset NC) | Scroll Qdrant theo `kind ∈ KNOWLEDGE_KINDS`, kiểm trường |
| G5 | An toàn không suy giảm | Sanitize eval: tỉ lệ chặn injection và off-topic thay đổi trong ±1 điểm %; 0 chunk `contains_attack_example` lọt vào ngữ cảnh chat (unit test + quét log prefetch trên eval set) | `evals/sanitize`, test filter |
| G6 | Đúng model embedding và chế độ hybrid | Model id thực của serving embed (`/v1/models`) là `DuyTa/sec-embedding` và khớp manifest ingest (dim 1024 là chưa đủ vì bge-m3 cũng 1024); 100% point mới có cả dense và sparse `bm25`; ≥ 95% truy vấn eval chạy `mode=hybrid` | Log có cấu trúc `mode`; scroll kiểm vector; manifest |

## B. Điểm chất lượng (100)

| ID | Tiêu chí | Điểm | Thang chấm |
|---|---|---|---|
| S1 | Toàn vẹn dữ liệu nạp | 15 | Số chunk mỗi nguồn khớp manifest (5); 0 text rỗng, 0 trùng `doc_id`/point id (4); độ dài chunk p99 ≤ 1200 ký tự (cửa sổ embedding 512 token) và p1 ≥ 100 ký tự (3); chạy lại ingest tạo 0 point mới (3) |
| S2 | Chất lượng truy hồi tri thức | 25 | Trên `knowledge_eval_set.jsonl` (scope knowledge): recall@5 tổng ≥ 0.75 → 12đ (tuyến tính 0.45→0.75); MRR ≥ 0.60 → 8đ (tuyến tính 0.30→0.60); recall@5 riêng câu tiếng Việt ≥ 0.65 → 5đ (tuyến tính 0.35→0.65) |
| S3 | Định tuyến grounding | 10 | Với `knowledge_enabled=True`, câu khái niệm/khắc phục (không phải taxonomy) vào `concept_kb` ≥ 90% (5); câu đối chứng kích hoạt nhầm ≤ 2% (3); câu có mã giữ route cũ 100% (2) |
| S4 | Chất lượng câu trả lời | 25 | Judge độc lập trên 60 câu (30 khái niệm, 30 khắc phục), so cờ bật vs cờ tắt: độ phủ `key_points` ≥ baseline + 10 điểm % (10); faithfulness với ngữ cảnh tra được ≥ 0.90 (8); có trích nguồn đúng (title/URL có trong kết quả tra) ≥ 85% câu có hit (5); 0 URL/mã CVE/CWE bịa (2, có một lỗi = 0đ) |
| S5 | Eval khắc phục OWASP sẵn có | 5 | `eval_remediation_owasp.py --part A` ≥ 9/10 và ≥ baseline |
| S6 | Độ trễ (cần instrument TTFT trước) | 10 | p95 thời gian đến token đầu tiên câu khái niệm tăng ≤ 300 ms (5); p95 `qdrant_search` scope knowledge ≤ p95 baseline + 150 ms (3); fast-path exact CVE không đổi trong ±10% (2) |
| S7 | Chất lượng code và tài liệu | 10 | Test mới cho `scope` filter, BM25 post-filter, route `concept_kb`, `_format_hit`, chunker (5); README/AGENTS cập nhật cờ + quy trình nạp/rollback (3); manifest + kết quả eval lưu trong `docs/test-results/` (2) |

## C. Quy tắc chấm

- Judge LLM cho S4 phải khác model đang phục vụ chatbot; ghi rõ model, prompt judge, seed/temperature.
- Chạy mỗi số đo ngẫu nhiên ≥ 2 lần; báo trung bình và độ lệch. Chênh lệch giữa 2 lần > 3 điểm % thì chạy lần 3.
- Không chỉnh eval set sau khi đã thấy kết quả của cờ bật. Mọi sửa đổi phải ghi lý do và chấm lại cả baseline.
- S4 và S6 cần hạ tầng (judge độc lập, log TTFT). Nếu không đo được thì ghi 0 điểm cho mục đó kèm lý do, không ước lượng.
- Báo cáo cuối: bảng G/S với số đo, lệnh, đường dẫn file kết quả, và danh sách tiêu chí không đo được kèm lý do.

## D. Điều chỉnh sau khi thấy số liệu (ghi rõ để không bị coi là đổi luật chơi)

- **G6, vế "≥ 95% truy vấn hybrid"**: đo được 108/114 = 94,7% (không đạt theo câu chữ gốc). 6 truy vấn còn lại đều là tiếng Việt thuần, BM25 không có token trùng với chunk tiếng Anh nên rơi về dense. Báo cáo chấm điểm ghi G6 vế này là **không đạt theo tiêu chí gốc**; đề xuất tiêu chí sửa: "100% truy vấn tiếng Anh chạy hybrid; truy vấn tiếng Việt được phép dense-only", kèm hạng mục cải thiện xuyên ngôn ngữ cho đợt sau.
- **Baseline retrieval CVE**: dùng số đo lại bằng code HEAD trên collection prod (recall@5 0,667, MRR 0,598), thay cho 0,675/0,599 trong file kết quả cũ.

## E. Kết quả sau khi triển khai (2026-10-07)

Số đo, bảng điểm 100 câu và các tiêu chí chưa đạt: [kb-knowledge-grounding-2026-10-07.md](../test-results/kb-knowledge-grounding-2026-10-07.md). Tóm tắt trạng thái theo tiêu chí gốc:

- **Đạt (số đo tự động hoặc đo trực tiếp):** G1 (prod, sau khi nạp tri thức, giống hệt baseline), G2 (chỉ còn 2 fail có sẵn), G4, G5 (theo cấu trúc), G6 (kể cả hybrid 114/114 sau khi thêm mở rộng Việt→Anh), S1, S2, S5 (9/10).
- **Đạt có điều kiện:** G3 (rollback đã diễn tập trên lab; trên prod mới dry-run theo batch thật, cờ tắt ≡ HEAD ở routing/ngữ cảnh/prompt).
- **Chưa đạt hoặc không đo được:** S3 (routing độc lập: 81% bắt đúng, 32% nhầm ở nhóm từ vựng trùng), S4 (không có judge LLM độc lập; dùng agent đọc tay), S6 (TTFT +~1,5 s thay vì +300 ms), S7 (thiếu bản lưu một số JSON gốc: nay đã đặt trong `kb-knowledge-grounding-artifacts/`).
- Điều chỉnh ở mục D vẫn giữ nguyên; riêng vế "≥95% truy vấn hybrid" nay **đạt** (114/114) nhờ mở rộng thuật ngữ Việt→Anh cho BM25 thay vì miễn trừ tiếng Việt.
