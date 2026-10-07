# KB tri thức + sửa search + bài test 100 câu core: kết quả và triển khai (2026-10-06 → 07)

Plan: [kb-knowledge-grounding.md](../plans/kb-knowledge-grounding.md). Tiêu chí: [kb-knowledge-grounding-criteria.md](../plans/kb-knowledge-grounding-criteria.md). Bằng chứng gốc: [kb-knowledge-grounding-artifacts/](kb-knowledge-grounding-artifacts/).

**Trạng thái:** đã triển khai lên gateway prod :8131 (image `secai-agent-gateway:knowledge-kb-20261007`, cờ tri thức BẬT) và đã nạp 8.386 chunk vào `cve_kb_hybrid`. Mã nằm trên nhánh `feat/knowledge-kb-grounding` (chưa push, chưa merge).

## 1. Đã làm

| Hạng mục | Nội dung |
|---|---|
| Dữ liệu | 8.386 chunk từ 6 nguồn: OWASP Cheat Sheets (3.750), ASVS 5.0 (345), mitigation CWE/CAPEC/ATT&CK (3.960 gồm CWE 1.947, CAPEC 436, ATT&CK 1.577), OWASP Top 10 2021/2025 và CWE Top 25 2019–2025 (331, kind `taxonomy`; 317 + 14). Mọi chunk có license, attribution, URL, version. 56 chunk gắn `contains_attack_example` (không bao giờ vào ngữ cảnh chat). |
| Pipeline | `secai/scripts/knowledge/` (`chunkers.py`, `kb_knowledge.py` chunk/ingest/verify/rollback, `make_lab_collection.py`). Nạp idempotent theo `text_sha`, chặn ghi prod nếu thiếu `--allow-prod`, rollback chỉ xoá kind tri thức của đúng batch. |
| Search | `scope` entity/knowledge/all. Entity (mặc định) không bao giờ trả chunk tri thức: bộ lọc nằm trong Qdrant cho cả dense lẫn BM25 và ở mọi nơi gọi trực tiếp (tra node định nghĩa, cổng exploit, KPI). Scope knowledge: lượt ghim mã CWE/CAPEC/ATT&CK, mở rộng thuật ngữ Việt→Anh cho BM25, cổng cosine 0,55, tối đa 2 chunk/tài liệu. Tool của model không còn truyền được `scope`/kind tri thức. Tra node định nghĩa theo point id (UUIDv5 của `chunk_id`, đúng 2.275/2.275 node): 2 ms thay vì scroll ~2,1 s trên prod. |
| Chat, cờ `CHAT_KNOWLEDGE_GROUNDING_ENABLED` | route `concept_kb` với pre-fetch có bọc `untrusted_retrieved_content`, tối đa 6.000 ký tự, timeout 3 s; câu hỏi OWASP Top 10 / CWE Top 25 nhận đủ danh sách đúng phiên bản (kèm các phiên bản có trong KB); chính sách trích dẫn ở `prompts/chat_knowledge.md` (dòng `Nguồn: {Title} — {URL} ({License})`). |
| Chat, cờ `CHAT_CVE_SEARCH_PREFETCH_ENABLED` (mặc định bật) | sửa lỗi nền do bài test 100 câu lộ ra: tự tra KB cho câu CVE không có mã; khối PoC catalog (đếm khử trùng, chọn CVE có nhiều PoC nhất trong các CVE cùng tên) và mã PoC thật khi người dùng xin xem mã; tên chính thức CWE/CAPEC/ATT&CK đi kèm mã; nhắc trả lời tiếng Anh cho câu tiếng Anh. Quy tắc prompt của nhóm này ở `prompts/chat_prefetch.md`. |
| Cờ tắt | cả hai cờ tắt: routing, ngữ cảnh và prompt giống bản cũ (golden 661 prompt; `chat.md` byte-identical với main, có test sha256). |

## 2. Các phép đo chính

### 2.1 Tra CVE không giảm (G1)

| Phép đo | Kết quả |
|---|---|
| Prod, code HEAD cũ (trước mọi thay đổi) | recall@5 0,667 · MRR 0,598 |
| Prod, code mới, **sau khi nạp tri thức vào prod** | recall@5 0,667 · MRR 0,598 (từng nhóm trùng khớp) |
| Chunk tri thức trong 121 truy vấn entity | 0 |
| Lab trước/sau nạp tri thức | 0,846 / 0,767 cả hai; 0/117 hạng đáp án đổi |

(File kết quả cũ ghi 0,675/0,599 đã lỗi thời; baseline đúng là số đo lại bằng code HEAD.)

### 2.2 Truy hồi tri thức (bộ 114 câu)

recall@5 **0,965**, MRR 0,798; tiếng Việt 0,963; **114/114 truy vấn chạy hybrid**; 0 chunk gắn nhãn payload trong kết quả; độ trễ tìm kiếm p50 1,36 s. Lưu ý: bộ này dễ (chủ đề trùng tên cheat sheet); với gold chỉ là cheat sheet, reviewer đo 0,92/0,73.

### 2.3 Bài test 100 câu chức năng core (15 nhóm)

Bộ câu: `evals/core_eval/core_eval_set.jsonl`; đáp án đối chiếu đọc từ chính KB và catalog PoC. Chấm bởi agent độc lập (PASS=1, PARTIAL=0,5, FAIL=0), mỗi lượt chạy 1 lần. Cấu hình thử giống prod (`INPUT_CLASSIFIER_BLOCK_OFFTOPIC=false`, cascade bật, air-gap).

**Vòng lặp trên KB thử (cờ tắt / cờ bật):** 78,0 / 77,5 → 88,5 / 88,5 → 90,0 / 91,0 → **92,5 / 91,5**. Không có hồi quy FAIL ở các vòng; nhiễu đo được 0/21 câu ngoài phạm vi thay đổi.

**A/B trên KB prod thật (763.518 CVE), canary cô lập, cờ tri thức tắt:**

| Nhóm | Image đang chạy trước đó | Image mới | Image mới + cờ bật (tri thức đã nạp) |
|---|---|---|---|
| cve_lookup | 95,8 | 100 | 100 |
| cve_alias | 75,0 | 93,8 | 87,5 |
| cve_discovery | 25,0 | 56,2 | 56,2 |
| cwe_lookup | 100 | 100 | 91,7 |
| capec_attack | 91,7 | 100 | 100 |
| poc | 45,0 | 95,0 | 95,0 |
| concept | 86,4 | 100 | 100 |
| remediation | 86,4 | 100 | 90,9 |
| taxonomy | 50,0 | 50,0 | **100** |
| meta / offtopic / edge | 100 | 100 | 100 |
| safety | 100 | 90 | 90 |
| multilingual | 83,3 | 100 | 100 |
| followup | 83,3 | 100 | 100 |
| **Tổng /100** | **80,0** | **93,5** | **93,0** |

- Tên mã CWE/CAPEC/ATT&CK gắn sai: image cũ 3 câu, bản đầu của image mới 6 (bảng tên bị timeout trên prod vì scroll không index), **bản cuối 0 câu** sau khi tra theo point id.
- Số PoC trong câu trả lời khớp catalog khử trùng 100% (64/21/15/18/11/9/16/0/0).
- Cờ bật giữ nguyên tổng điểm nhưng: taxonomy 50% → 100% (đủ 10 mục OWASP Top 10:2025 và 25 mục CWE Top 25 đúng thứ hạng, nêu rõ "mới nhất trong KB"), câu khái niệm/khắc phục bám tài liệu (66 URL nguồn, 100% khớp `source_url` trong chunk, trừ 1 URL model tự dựng ở câu 074), tránh được khuyến nghị sai (JWT: bản tắt khuyên RS256, trái tài liệu).
- Độ trễ (TTFT p50/p95, giây): image cũ 1,77/5,28; image mới cờ tắt 1,84/4,47; cờ bật tăng ~1,5 s TTFT riêng cho concept/remediation (`3,26` vs `1,78`), p95 toàn cục ngang nhau. Nhóm alias/discovery +~2 s do tự tra KB.

### 2.4 Các phép đo khác

| Mục | Kết quả |
|---|---|
| pytest toàn repo (code cuối) | **2.243 pass, 2 fail có sẵn** (`tests/multilang/test_api.py::test_composition_off_on_legacy_and_cors`, `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal`). Lượt chạy cuối chạy trên đúng code đã triển khai (sau sửa chính sách trích dẫn). |
| Sanitize eval (`scripts/eval_sanitize.py`, endpoint thật) | FP 0/219, injection recall 36/36, PII guard 54,6% (báo cáo trước 54,0%): trong ±1 điểm; `sanitize/` không bị sửa |
| S5 `eval_remediation_owasp.py` part A | 9/10 cả hai cấu hình (câu lỗi duy nhất thiếu khối code) |
| Rollback | diễn tập thật trên lab (50.369 → 42.318, đúng 8.051 điểm tri thức bị xoá); dry-run trên prod cho batch thật `knowledge-2026-10-07` = 8.386 điểm |

## 3. Các lỗi được phát hiện và đã sửa (tóm tắt)

- Lần chấm độc lập đầu (KHÔNG ĐẠT, ~65/100): 6 lỗi mức Cao (prompt có `&` làm vỡ XML; id mã CVE biến thành filter làm 0 kết quả; BM25 entity bị chunk tri thức chen; kết quả rỗng thay vì "không tìm thấy"; độ trễ/timeout; thiếu test chạy pipeline thật) — đã sửa, có test chạy pipeline thật.
- Lần chấm độc lập thứ hai (KHÔNG ĐẠT, S≈61 theo quy tắc chặt): 1 test đỏ do double thiếu `grounding_context`, cờ tắt chưa giống HEAD (bảng tên và 2 luật prompt vô điều kiện), Top 10 sai loại (LLM/API/Mobile), URL `.md` chết, lượt ghim CWE lẫn danh sách taxonomy, file KPI của người khác bị đưa vào commit — đã sửa.
- Phát hiện khi chạy trên prod thật: scroll theo `cwe_id`/`capec_id`/`technique_id` (chưa index) mất ~2,1 s trên 763k điểm: làm bảng tên chính thức hết hạn và là nguyên nhân độ trễ 5 s của câu hỏi CWE/CAPEC có sẵn từ trước. Đã tra theo point id và thêm payload index.
- Rò rỉ chỉ dẫn vào câu trả lời (câu 034 lặp lại "nếu có trong ngữ cảnh… CC BY-SA"): đã đổi chính sách trích dẫn sang một dòng `Nguồn:` có cấu trúc, cấm nhắc tới cơ chế; kiểm lại 27 câu, 0 rò rỉ.
- Cấu hình thử ban đầu khác prod (`BLOCK_OFFTOPIC` mặc định true, prod false) làm lộ các ca "classifier chặn nhầm" không có trên prod; đã chạy lại đúng cấu hình prod.

## 4. Triển khai prod (2026-10-07)

1. **Bước 1 (09:25):** gateway đổi sang image overlay 12 file (nền: image đang chạy `ab-backend-fix-20261007`), cờ tri thức tắt; profile container giống hệt baseline; smoke 7 câu đạt.
2. **Bước 2:** nạp 8.386 chunk vào `cve_kb_hybrid` (`--allow-prod`, 720 s), tạo thêm payload index (`sensitivity`, `ingest_batch`, `source`, `license`, `doc_id`, `parent_id`, `cwe_id`, `capec_id`, `technique_id`); verify 8.386/8.386 (đủ dense + sparse `bm25`, 0 trùng, text khớp `chunks.jsonl`); điểm không phải tri thức vẫn 763.518. Không chụp snapshot 22 GB (đĩa còn ~44 GB): bảo vệ bằng rollback theo batch đã diễn tập.
3. **Bước 3 (10:06):** image sửa lỗi chính sách trích dẫn + `CHAT_KNOWLEDGE_GROUNDING_ENABLED=true`; smoke 10 câu đạt, 0 traceback, health ok.

Vận hành (thư mục private `/home/ict/secai-releases/knowledge-kb-20261007`, dùng khóa `gateway-deploy.lock`; ghi chú phối hợp đã thêm vào `DEPLOYMENT-STATE.md`):

```sh
./rollback-gateway.sh flag   # tắt cờ tri thức, giữ image mới
./rollback-gateway.sh full   # về image cũ 5d2f6e816189… (giữ nguyên image, không prune)
python3 secai/scripts/knowledge/kb_knowledge.py rollback --collection cve_kb_hybrid --batch knowledge-2026-10-07 --allow-prod
```

Mọi release gateway sau này phải overlay lên **image hiện tại** (`46fa8f385b1d…`); baseline của các release trước đó đã lỗi thời.

## 5. Còn tồn tại, giới hạn và chưa đo

- **Chưa đạt tiêu chí gốc:** S6 (TTFT +300 ms; thực tế +~1,5 s cho concept/remediation khi bật cờ, +~2 s cho câu alias); routing trên tập độc lập của reviewer chỉ bắt đúng 81% và nhầm 32% ở nhóm "từ vựng trùng" (upload, log, ssh…) — cổng cosine 0,55 chỉ loại ~62% câu không liên quan; chưa có judge LLM độc lập cho faithfulness (đã thay bằng agent đọc tay); mỗi vòng test chạy 1 lần, n nhỏ ở taxonomy/multilingual/followup/edge/safety (một câu = 17–33 điểm % của nhóm).
- **Lỗi nội dung còn lại:** cve_discovery 56% (026 Citrix Bleed không truy hồi được, 023/027/028 không đưa CVE gold lên; 025 trả CVE ProxyShell cho chuỗi ProxyLogon); 014 CVSS lấy từ trí nhớ; 050 mã PoC thật nhưng bị cắt giữa mảng; 065 truy hồi trôi chủ đề; câu chốt tiếng Việt lẫn trong thân tiếng Anh (062/063/070/072); `&lt;` thô trong khối code (lớp anti-XSS của backend có chủ đích: sửa ở FE).
- **Rủi ro chưa kiểm chứng:** 10 chunk trong LLM Prompt Injection Prevention / RAG Security cheat sheet chứa chuỗi tấn công thật ("ignore previous instructions"); đã bọc untrusted nhưng bộ test chưa có câu hỏi nhắm vào chúng. Nhãn payload còn yếu ở sheet XSS Filter Evasion (22/55 chunk).
- **Chưa làm:** pháp chế duyệt giấy phép CC-BY-SA/MITRE ToU (nội dung được trích kèm nguồn và URL, license nằm trong metadata); đo TTFT tại host; instrument TTFT trong gateway; bài test lặp ≥3 lần; chunk khắc phục Log4Shell và nguồn GHSA (kind `advisory_fix`); bản dịch tiếng Việt của chunk; nối pipeline nạp vào cron hằng đêm.
- **Theo dõi 30–60 phút sau bật cờ:** 5xx, câu rỗng/fallback, TTFT p95, tỉ lệ `hits=0` trong log `qdrant_search scope=knowledge`, và các câu 001/014/020/025/039/068/091 để tách nhiễu khỏi lỗi hệ thống.
