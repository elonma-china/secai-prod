# KB tri thức + sửa search + bài test 100 câu core: kết quả (2026-10-06 → 07)

Plan: [kb-knowledge-grounding.md](../plans/kb-knowledge-grounding.md). Tiêu chí: [kb-knowledge-grounding-criteria.md](../plans/kb-knowledge-grounding-criteria.md).
Mọi thử nghiệm chạy trên **collection thử** `cve_kb_knowledge_lab` (42.318 điểm CVE/định nghĩa sao từ prod + 8.386 chunk tri thức) và hai gateway cô lập :8133 (cờ tắt) / :8134 (cờ bật). **Prod (`cve_kb_hybrid`, :8131) không bị ghi hay thay đổi.** Chưa commit, chưa deploy.

## 1. Đã làm

| Hạng mục | Nội dung |
|---|---|
| Dữ liệu | 6 nguồn → 8.386 chunk: OWASP Cheat Sheets (3.750), ASVS 5.0 (345), mitigations CWE/CAPEC/ATT&CK (3.960), OWASP Top 10 2021/2025 + CWE Top 25 2019–2025 (331, kind `taxonomy`). Có license/attribution/URL/version trên 100% chunk. |
| Pipeline | `secai/scripts/knowledge/` (`chunkers.py`, `kb_knowledge.py`, `make_lab_collection.py`): chunk → ingest (dense 1024 + sparse `bm25`, idempotent theo `text_sha`, chặn ghi prod nếu thiếu `--allow-prod`) → verify → rollback (đã diễn tập thật: 50.369 → 42.318, đúng 8.051 điểm tri thức bị xoá, prod giữ 763.518). |
| Search | `scope` entity/knowledge/all; BM25 lọc trong Qdrant; truy vấn có mã CWE/CAPEC/ATT&CK = 2 lượt (ngữ nghĩa + ghim mã); mở rộng thuật ngữ Việt→Anh cho BM25; tối đa 2 chunk/tài liệu; ngưỡng dense 0,5; tool của model không còn truyền được `scope`/kind tri thức; mọi nơi gọi trực tiếp Qdrant (node định nghĩa, cổng exploit, 2 script KPI) đều loại chunk tri thức. |
| Chat (sau cờ `CHAT_KNOWLEDGE_GROUNDING_ENABLED`, mặc định tắt) | route `concept_kb` + pre-fetch có trích nguồn; khối bọc `untrusted_retrieved_content`, tối đa 6000 ký tự, timeout 3 s; `taxonomy` trả đủ danh sách đúng phiên bản; chính sách trích dẫn nằm ở `prompts/chat_knowledge.md`, chỉ nạp khi cờ bật (cờ tắt: prompt giống hệt cũ). |
| Chat (cờ riêng `CHAT_CVE_SEARCH_PREFETCH_ENABLED`, mặc định bật) | sửa lỗi nền do bài test 100 câu phát hiện: tự tra KB cho câu CVE không có mã ("Lỗ hổng Log4Shell là gì?", "CVE nào…"), khối PoC catalog xác định (đếm khử trùng), bảng tên chính thức CWE/CAPEC/ATT&CK, nhắc ngôn ngữ cho câu tiếng Anh. |

## 2. Cổng và tiêu chí (số tự đo, sau sửa)

| Mục | Kết quả |
|---|---|
| G1 không giảm chất lượng tra CVE | Prod code cũ vs mới: recall@5 0,667, MRR 0,598, **giống hệt**. Lab trước/sau nạp: 0,846 / 0,767, 0/117 hạng gold đổi. 0 chunk tri thức lọt vào kết quả entity. |
| G2 test | pytest toàn repo trên code cuối: **2.225 pass, 2 fail có sẵn** (`tests/multilang/test_api.py::test_composition_off_on_legacy_and_cors`, `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal`). |
| G3 rollback / cờ tắt | (a) rollback đã chạy thật trên lab (xem trên). (b) `classify_chat_grounding` không cờ ≡ HEAD trên 171 prompt golden; prompt chat byte-identical khi cờ tắt (có test). |
| G4 license | 8.386/8.386 chunk đủ license, attribution, URL, version; không có nguồn bị cấm. Version ATT&CK ghi đúng 19.2. |
| G5 an toàn | 0 chunk nhãn `contains_attack_example` trong context; nhãn mở rộng từ 18 lên 56 chunk. Sanitize eval không chạy (cần serving). |
| G6 embedding + hybrid | `check_embed_model` xác nhận `DuyTa/sec-embedding` qua `/v1/models` trước khi ghi; 100% điểm có dense + sparse; **114/114 truy vấn chạy hybrid** (sau khi thêm mở rộng Việt→Anh; trước đó 94,7%). |
| S2 truy hồi tri thức | recall@5 **0,965**, MRR 0,798 (114 câu), tiếng Việt 0,963. Gold theo tài liệu cha; lưu ý agent chấm nhận xét bộ này dễ (tên chủ đề trùng tên cheat sheet). |
| S3 routing | Bộ 114 câu: 114/114, control 0/10 (in-sample). **Tập 115 câu viết mới, lần chạy đầu: 82% bắt đúng, 4% nhầm** (chưa đạt ≥90%/≤2%); sau khi chỉnh luật theo tập đó: 100%/0% (số này không còn độc lập). CVE hỏi theo sản phẩm: 0 câu bị route nhầm sang tri thức. |
| S6 độ trễ | Tra tri thức p50 1,36 s (trước 1,90 s). Cờ bật vẫn thêm ~1,2–2 s TTFT cho câu khái niệm/khắc phục: **không đạt ngưỡng +300 ms** của tiêu chí. |
| Chất lượng câu trả lời (S4) | Bản bật tốt hơn 29/50 câu khái niệm-khắc phục, ngang 17, kém 4 (agent đọc tay 60 cặp, không mù); trích nguồn đúng 88%; 0 URL bịa ở bản bật (bản tắt ≥5). Độ phủ ý chính không đổi (đã gần trần). Đây **không** phải điểm judge LLM độc lập. |

Agent chấm độc lập lần đầu kết luận KHÔNG ĐẠT (~65/100) với 6 lỗi mức Cao; tất cả đã sửa và có test chạy pipeline thật (H0 `&` trần trong prompt, H1/H2 id làm filter trả 0 hit, H3 BM25 entity bị tri thức chen, H4 kết quả rỗng, H5 độ trễ/timeout). Chưa có lần chấm lại toàn bộ tiêu chí sau khi sửa, ngoài bài test 100 câu bên dưới.

## 3. Bài test 100 câu chức năng core (15 nhóm)

Bộ câu: `evals/core_eval/core_eval_set.jsonl` (đáp án đối chiếu đọc từ chính KB và catalog PoC). Gateway thử dùng cấu hình **giống prod**: `INPUT_CLASSIFIER_BLOCK_OFFTOPIC=false`, cascade bật, air-gap. Chấm bởi agent độc lập, 3 vòng (PASS=1, PARTIAL=0,5, FAIL=0), mỗi vòng chạy 1 lần.

| Nhóm | n | Cờ tắt R1→R2→R3 (%) | Cờ bật R1→R2→R3 (%) | Kết luận R3 (tắt / bật) |
|---|---|---|---|---|
| cve_lookup | 12 | 96 → 96 → 100 | 96 → 96 → 100 | đáp ứng / đáp ứng |
| cve_alias | 8 | 62 → 94 → 94 | 62 → 62 → 62 | đáp ứng / **chưa** |
| cve_discovery | 8 | 50 → 81 → 81 | 44 → 75 → 75 | chưa / chưa |
| cwe_lookup | 6 | 92 → 92 → 92 | 92 → 92 → 92 | đáp ứng / đáp ứng |
| capec_attack | 6 | 83 → 83 → 83 | 92 → 92 → 92 | chưa (sát) / đáp ứng |
| poc | 10 | 40 → 75 → 80 | 45 → 80 → 90 | chưa / đáp ứng |
| concept | 11 | 100 → 95 → 95 | 100 → 100 → 100 | đáp ứng / đáp ứng |
| remediation | 11 | 86 → 82 → 82 | 86 → 95 → 95 | chưa / đáp ứng |
| taxonomy | 3 | 50 → 50 → 50 | 50 → 67 → **100** | chưa / đáp ứng |
| meta | 6 | 67 → 100 → 100 | 67 → 100 → 100 | đáp ứng / đáp ứng |
| offtopic | 6 | 100 → 100 → 100 | 100 → 100 → 100 | đáp ứng / đáp ứng |
| safety | 5 | 100 → 100 → 100 | 90 → 90 → 90 | đáp ứng / đáp ứng |
| multilingual | 3 | 67 → 83 → 83 | 50 → 67 → 67 | chưa (sát) / chưa |
| followup | 3 | 67 → 83 → 100 | 67 → 100 → 100 | đáp ứng / đáp ứng |
| edge | 2 | 100 → 100 → 100 | 100 → 100 → 100 | đáp ứng / đáp ứng |
| **Tổng /100** | 100 | 78,0 → 88,5 → **90,0** | 77,5 → 88,5 → **91,0** | |

- FAIL còn lại: 3 mỗi cấu hình (026 Citrix Bleed không truy hồi được; 050 model tự viết mã rồi gắn nhãn "trích từ PoC"; 074 tắt / 094 bật: bịa Top 25 hoặc tham số giảm thiểu Log4Shell). Không có lỗi nặng ở safety/offtopic/edge; không có hồi quy FAIL mới.
- Sau vòng 3 còn sửa thêm một lỗi do agent chỉ ra: khối PoC chọn nhầm CVE anh em (CVE-2021-45046, 12 PoC) cho câu "Log4Shell có PoC…"; nay chọn CVE có nhiều PoC nhất trong các CVE cùng tên (CVE-2021-44228, 64 PoC, đã kiểm qua log). Chưa chạy lại cả 100 câu sau sửa này.
- Độ tin cậy: model gần tất định theo ngữ cảnh (81 câu tắt và 75 câu bật giống hệt giữa R2 và R3); 0/86 verdict đổi ở các nhóm không liên quan. Mỗi vòng chỉ chạy 1 lần, n nhỏ ở taxonomy/multilingual/followup/edge/safety: một câu đổi = 17–33 điểm % của nhóm.
- Cờ bật không cho tổng điểm khác đáng kể trên bài test này (91,0 vs 90,0); lợi ích của nó nằm ở trích nguồn (concept/remediation có URL cheat sheet 19/22 câu so với 1/22) và taxonomy.

## 4. Nhóm chưa đáp ứng và việc tiếp theo

| Nhóm | Nguyên nhân gốc | Loại | Đề xuất |
|---|---|---|---|
| cve_alias (bật), capec_attack (tắt) | model tự thêm chi tiết ngoài ngữ cảnh (người phát hiện, mốc vá, tên CAPEC/ATT&CK sai dù đã có bảng tên) | lỗi nền model | hậu kiểm đầu ra trong `agent/hooks.py::chat_final_text`; thay tên sai bằng tên chính thức ở tầng code |
| cve_discovery | 026: truy hồi không ra Citrix Bleed; 027: truy hồi theo CWE-78 trả CVE tuỳ ý | code + KB thử nhỏ | đồng nghĩa Citrix=NetScaler; xếp KEV-first như `_graph_reverse_candidates` |
| poc | 050: model viết mã giả gắn nhãn PoC mà không gọi `poc_fetch` | lỗi nền model + code | lọc khối code nhãn "trích từ PoC" khi không có `poc_fetch`; ép `poc_fetch` cho "cho tôi xem" |
| remediation (tắt) | câu tiếng Anh bị trả tiếng Việt khi không có grounding | code | gắn nhắc ngôn ngữ độc lập grounding (`EphemeralTurnContextHook`) |
| taxonomy (tắt) | không có dữ liệu danh sách khi cờ tắt | cờ | bật cờ tri thức, hoặc luôn gọi `_taxonomy_grounding` |
| multilingual / 094 | bịa tham số giảm thiểu Log4Shell | model + KB | thêm chunk khắc phục Log4Shell vào KB tri thức |
| chung | `&lt;` thô trong khối code | code | `security.py::neutralize_active_content`: cần quyết định có nới ở code fence không (liên quan anti-XSS) |

## 5. Chưa làm / cần bạn quyết định

- **P6 (đưa lên prod): chưa làm có chủ đích.** Cần: (1) pháp chế duyệt license CC-BY-SA (OWASP) và điều khoản MITRE khi trích nguyên văn; (2) snapshot Qdrant prod (đĩa còn ~46 GB, collection 22 GB); (3) deploy image có bộ lọc scope **trước** khi ghi chunk tri thức vào `cve_kb_hybrid` (image prod hiện tại không có `must_not`, chunk sẽ lọt vào kết quả CVE); (4) bật cờ theo canary và theo dõi TTFT p95 + tỉ lệ 0 hit.
- `CHAT_CVE_SEARCH_PREFETCH_ENABLED` mặc định **bật**: đổi hành vi chat của prod khi deploy (tốt cho nhóm alias/discovery, TTFT +~2 s cho các câu đó). Muốn giữ như cũ thì đặt `false`.
- Chưa chạy: S5 (`eval_remediation_owasp.py`, cần gateway live), sanitize eval, judge LLM độc lập cho faithfulness, đo TTFT tại host, bài test 100 câu lặp ≥3 lần.
- Mã chưa commit: nhiều file thay đổi nằm lẫn với thay đổi sẵn có của bạn (FE devspace, multilang…); tôi không stage gì.
