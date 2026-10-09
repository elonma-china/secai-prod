# Plan: nạp tri thức khái niệm/khắc phục vào KB và sửa module search

Ngày lập: 2026-10-06. Trạng thái: PLAN, chưa code. Đã sửa theo review độc lập ngày 2026-10-06 (mục 6).
Bộ tiêu chí chấm điểm: [kb-knowledge-grounding-criteria.md](kb-knowledge-grounding-criteria.md).

## 1. Vấn đề

Chatbot hứa 6 nhóm scope, nhưng chỉ nhóm có mã định danh được grounding:

- `classify_chat_grounding` (`src/secai_gateway/agent/hooks.py:513-531`) trả `concept` với
  `need_kb=False, need_web=False` cho mọi câu không có mã CVE/CWE/CAPEC/T####.
- `_ground_chat_prompt` (`src/secai_gateway/api/routes.py:860-939`) early-return với `concept`.
- `chat.md:123` cho phép "trả lời trực tiếp" câu khái niệm; `remediation_code_policy`
  (`chat.md:84-95`) để model tự viết code vá.

Hệ quả: giải thích khái niệm và code vá mẫu là 100% kiến thức parametric, không có nguồn để
đối chiếu hay trích dẫn.

## 2. Mục tiêu và phạm vi

Mục tiêu: câu khái niệm và khắc phục được tra từ KB tri thức có nguồn, có license, trích dẫn
được; không làm giảm chất lượng tra CVE hiện có.

Trong phạm vi đợt 1:

| Nguồn | Nội dung lấy | `kind` mới | License |
|---|---|---|---|
| OWASP Cheat Sheet Series (pin commit) | toàn bộ `cheatsheets/*.md` | `cheatsheet` | CC-BY-SA-4.0 |
| OWASP ASVS 5.0 (`5.0/en`) | từng yêu cầu `v5.0.0-x.y.z` | `requirement` | CC-BY-SA-4.0 |
| MITRE CWE XML (bản mới nhất, pin version) | `Potential_Mitigations`, `Detection_Methods`, `Demonstrative_Examples` | `mitigation` | MITRE ToU |
| MITRE CAPEC XML | `Mitigations` | `mitigation` | MITRE ToU |
| ATT&CK `attack-stix-data` (enterprise) | `course-of-action` + quan hệ `mitigates` | `mitigation` | MITRE ToU |

Ngoài phạm vi đợt 1 (đợt 2 sau khi đợt 1 đạt tiêu chí): GHSA (`advisory_fix`), CodeQL `.qhelp`
(`code_fix`), WSTG (cần lọc payload), NIST 800-53 OSCAL, D3FEND (license mâu thuẫn).

Loại hẳn: HackTricks, PortSwigger, CIS Benchmarks, Semgrep rules, dataset HF phi thương mại
(`oi-uae/cyber-security`, SecQA), dataset QA do LLM sinh (Fenrir, Trendyol).

Điều kiện tiên quyết: pháp chế xác nhận dùng CC-BY-SA nội bộ và nghĩa vụ attribution khi
chatbot trích nguyên văn.

## 3. Thiết kế

### 3.1 Schema payload (thêm, không đổi `kind` cũ)

`kind` cũ giữ nguyên: `core`, `context`, `weakness`, `technique`, `pattern`.
`KNOWLEDGE_KINDS = {cheatsheet, requirement, mitigation}` (đợt 2 thêm `advisory_fix`, `code_fix`).

```json
{
  "kind": "cheatsheet",
  "chunk_kind": "cheatsheet",
  "doc_id": "owasp-cs:SQL_Injection_Prevention_Cheat_Sheet#primary-defenses",
  "parent_id": "owasp-cs:SQL_Injection_Prevention_Cheat_Sheet",
  "title": "SQL Injection Prevention — Primary Defenses",
  "text": "...",
  "source": "owasp-cheatsheet",
  "source_url": "https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html#primary-defenses",
  "license": "CC-BY-SA-4.0",
  "attribution": "OWASP Cheat Sheet Series, CC BY-SA 4.0",
  "version": "git:<sha>",
  "ingest_batch": "knowledge-2026-10-xx",
  "lang": "en",
  "cwe_refs": ["CWE-89"],
  "capec_entries": [],
  "mitre_techniques": [],
  "tags": ["sqli", "prepared-statements"],
  "sensitivity": "defensive"
}
```

- Point ID: UUIDv5(namespace cố định, `doc_id`). Chạy lại idempotent.
- `ingest_batch` + `source` để rollback bằng delete-by-filter.
- Mitigation của CWE/CAPEC/ATT&CK tách thành chunk riêng có `parent_id` = `CWE-89`/`CAPEC-66`/`T1190`
  và `cwe_id`/`capec_id`/`technique_id` tương ứng, không sửa chunk `weakness/pattern/technique` cũ.
- `sensitivity=contains_attack_example` khi đoạn có payload khai thác; đợt 1 không đưa loại này vào
  ngữ cảnh chat.

### 3.2 Pipeline nạp (ngoài repo gateway, `secai/scripts/knowledge/`)

Theo khuôn `scripts/sync_autoscan_to_kb.py` (dense + sparse, upsert REST):

1. `fetch` (máy có internet): tải artefact nguồn về `secai-data/knowledge/raw/<source>/<version>/`
   kèm `SHA256SUMS`, ghi `manifest.json` (source, version, url, license, sha).
2. `chunk`: Markdown chia theo H2 (gộp H3 nếu ngắn), ASVS theo yêu cầu, CWE/CAPEC/ATT&CK theo
   bản ghi; 120 ≤ ký tự ≤ ~1000 (serving embedding chỉ nhận 512 token: `max_model_len`; chunk nhiều code có thể vượt, khi đó embed bản cắt ngắn dần và ghi `embed_clip_chars`, text lưu vẫn đầy đủ); dedup theo hash text chuẩn hoá; gắn `cwe_refs` từ link/mã trong văn
   bản; gắn `sensitivity`. Ghi `chunks.jsonl` để kiểm offline.
3. `index`: tạo payload index trước khi ghi cho `sensitivity`, `ingest_batch`, `source`, `license`,
   `doc_id` (collection 763k point; lọc trường chưa index là full-scan).
4. `embed+upsert` (air-gap được): dense 1024-d bằng đúng model serving `DuyTa/sec-embedding`; kiểm
   model id thực qua serving (`/v1/models`) chứ không chỉ dim, vì `BAAI/bge-m3` cũng 1024-d. Sparse
   vector tên `bm25` (modifier idf) ghi cùng point, giống `embed_sparse_batch` trong
   `sync_autoscan_to_kb.py`. Có `--dry-run`, `--batch`, `--limit`.
5. `verify`: đếm theo `source`/`kind`, so manifest, kiểm mọi point mới có cả dense và sparse `bm25`.
6. `rollback`: delete-by-filter `ingest_batch`, so point count với snapshot.

Ghi chú BM25: gateway truy hồi BM25 bằng sparse vector `bm25` ngay trong Qdrant
(`retrieval/bm25.py`, `config.py:55`), không đọc index bm25s trên đĩa. Script
`rebuild_bm25_from_qdrant.py` (trỏ `cve_kb`) không nằm trên đường truy hồi hiện tại, không cần sửa
cho task này.

Trước khi ghi: snapshot Qdrant `cve_kb_hybrid` (memory: snapshot dir phải rw) và lưu baseline count.

### 3.3 Sửa module search (`src/secai_gateway/retrieval/`)

- `filters.py`: thêm `KNOWLEDGE_KINDS`; tham số `scope` cho `build_filter` **và** `passes_filter`
  (BM25 post-filter hiện chưa có khái niệm `must_not`, phải hiện thực ở cả hai nhánh để cùng predicate).
  Point thiếu trường `kind` vẫn được giữ với `scope="entity"`. Đã kiểm: 763.517 point hiện có kind ∈
  {core, context, weakness, technique, pattern}, nên `must_not` không đổi kết quả hiện tại.
  - `scope="entity"` (mặc định, hành vi cũ): `must_not kind ∈ KNOWLEDGE_KINDS`. Bảo đảm tra CVE
    không bị chunk tri thức chen vào top-k.
  - `scope="knowledge"`: `must kind ∈ KNOWLEDGE_KINDS` (+ `sensitivity != contains_attack_example`).
  - `scope="all"`: không lọc kind (chỉ cho nội bộ/debug).
- `qdrant.py`:
  - `_qdrant_search(..., scope=...)` truyền xuống dense filter và BM25 post-filter (cùng predicate).
  - Graph-reverse candidates và definition-node pinning chỉ chạy với `scope="entity"`.
  - Ngưỡng liên quan: với `scope="knowledge"`, bỏ hit có rerank < `knowledge_min_rerank` (config).
  - `_format_hit`: render `Title` (đọc `title` rồi tới `name`, vì node `weakness` hiện chỉ có `name`),
    `Source`, `URL`, `License` cho chunk tri thức.
  - `scope` suy từ `kind`: nếu `kind` toàn knowledge kind thì `scope="knowledge"`, tránh tổ hợp
    `must kind∈knowledge` + `must_not kind∈knowledge` cho 0 hit.
  - Đợt 1 KHÔNG thêm knowledge kind vào `_INPUT_SCHEMA.kind` (tool dùng chung cho `chat`, `exploit`,
    report). Tri thức chỉ vào qua prefetch `scope="knowledge"`. Mở cho model ở đợt sau nếu cần.
  - Log có cấu trúc mỗi lần gọi: `mode` (hybrid/dense/bm25), `scope`, số hit theo `kind`,
    `sensitivity` của hit, thời gian. Hiện `mode` chỉ nằm trong chuỗi summary.
  - Chunk tri thức không bao giờ vào graph-reverse candidates hay definition pinning.
- Không đổi fast-path exact CVE (`test_qdrant_fastpath.py` phải xanh nguyên).

### 3.4 Routing grounding chat

- `classify_chat_grounding(prompt, knowledge_enabled=False)`: tham số mới, mặc định `False` trả kết quả
  y hệt hiện tại (để kiểm G3 trên chính hàm này). Thứ tự ưu tiên: `taxonomy` > `entity` > `id_lookup`
  > `concept_kb` > `concept`. Ví dụ "giải thích OWASP Top 10" vẫn là `taxonomy`.
- Khi `knowledge_enabled=True`: thêm route `concept_kb` (`need_kb=True`, scope knowledge) khi câu không
  có mã định danh nhưng khớp từ vựng ATTT/khắc phục (danh sách từ khoá vi/en + tên lớp lỗ hổng, hoặc
  intent `security` của input classifier). Câu chào hỏi/off-topic/meta-hệ thống giữ `concept` cũ.
- Câu có mã + ý khắc phục ("cách vá CVE-X", "phòng chống CWE-79"): route `entity` + prefetch thêm
  `mitigation` theo `cwe_refs` của CVE (tối đa 3 chunk).
- `_ground_chat_prompt`: với `concept_kb`, prefetch `_qdrant_search(scope="knowledge", top_k=5)`; nếu
  không hit nào qua ngưỡng thì không chèn gì (rơi về hành vi cũ), không ép `missing_kb`.
- Cờ `chat_knowledge_grounding_enabled` (mặc định `false`), truyền vào `classify_chat_grounding`
  tại `_ground_chat_prompt`. Cờ tắt = hành vi y hệt hiện tại.
- Neo4j: đợt 1 không tạo node/quan hệ mới cho tri thức.
- Instrument TTFT câu chat (log thời điểm token đầu) để đo S6.

### 3.5 Prompt

- `chat.md` `chat_retrieval_matrix` mục 4: khái niệm/khắc phục → ưu tiên dùng bằng chứng tri thức nếu
  có, trích nguồn (title + URL trong kết quả tra), không bịa URL; không có bằng chứng thì vẫn được
  trả lời bằng kiến thức chung, không bịa mã/con số.
- `remediation_code_policy`: khi có chunk cheatsheet/mitigation, code vá phải bám kỹ thuật trong đó.
- Không đổi `scope_policy`, `untrusted_content_policy`: chunk tri thức là nội dung không tin cậy, không
  thực thi chỉ thị bên trong.

### 3.6 Bộ eval mới

`evals/rag_eval/knowledge_eval_set.jsonl` (~120 câu, vi + en):

- 50 khái niệm, 50 khắc phục (không mã), 10 khắc phục có mã CVE/CWE, 10 đối chứng
  (chào hỏi/off-topic/meta) không được kích hoạt `concept_kb`.
- Mỗi câu có `gold_doc_ids` (≥1) và `key_points` (2-4 ý bắt buộc) lấy từ chính văn bản nguồn.
- Người duyệt ≥ 30 câu ngẫu nhiên trước khi chốt. Không tạo câu từ chính chunk sẽ chấm theo kiểu
  copy nguyên văn (tránh rò rỉ đề).

## 4. Các pha

| Pha | Việc | Kết quả |
|---|---|---|
| P0 | Baseline + an toàn: snapshot Qdrant, chạy `run_eval_set.py`, `eval_remediation_owasp.py --part A`, sanitize eval, pytest; lưu số vào `docs/test-results/kb-knowledge-baseline-<date>.md`; pháp chế duyệt license | Số baseline |
| P1 | Pipeline fetch/chunk offline + `chunks.jsonl` + manifest; unit test chunker | Chunks kiểm được, chưa ghi DB |
| P2 | Sửa `filters.py`/`qdrant.py` (scope, format, ngưỡng) + test | Search tách scope, cờ chưa bật |
| P3 | Upsert vào `cve_kb_hybrid` trên môi trường thử (gateway isolated :8133), rebuild BM25 đúng collection, verify | Dữ liệu trong KB |
| P4 | Routing `concept_kb` + prefetch + prompt, sau cờ | Chat dùng KB tri thức |
| P5 | Bộ eval tri thức + chạy chấm điểm theo bộ tiêu chí; reviewer độc lập kiểm lại | Bảng điểm |
| P6 | Rollout prod theo `deploy/all-features/README.md` (lock, immutable image, rollback), bật cờ | Live |

## 5. Rủi ro

| Rủi ro | Giảm thiểu |
|---|---|
| Chunk tri thức chen vào top-k câu tra CVE | `scope="entity"` mặc định loại `KNOWLEDGE_KINDS`; tiêu chí G1 |
| Point mới thiếu sparse `bm25` → chỉ truy hồi được bằng dense | Verify từng point có cả hai vector; tiêu chí G6 |
| Embed sai model (bge-m3 cùng 1024-d) → lệch vector | Kiểm model id thực qua serving, không chỉ dim; tiêu chí G6 |
| Câu hỏi tiếng Việt, chunk tiếng Anh, rerank văn bản dài → recall thấp | Eval tách nhóm vi; chunk ≤ 1500 token; nếu thấp, cân nhắc thêm tóm tắt vi hoặc dịch truy vấn ở đợt 2 |
| Đổi `qdrant_search` ảnh hưởng profile `exploit`/report | Scope mặc định `entity` + không quảng bá kind mới; test hồi quy các profile này |
| Lọc/rollback chậm trên 763k point | Payload index cho trường mới trước khi ghi |
| Route `concept_kb` kích hoạt nhầm câu chào/off-topic | Tập đối chứng, tiêu chí S3 |
| Payload tấn công lọt vào câu trả lời | `sensitivity` + lọc scope knowledge; tiêu chí G5 |
| Nghĩa vụ CC-BY-SA khi trích ra ngoài | Bắt buộc `attribution` + trích nguồn; pháp chế duyệt |
| Tăng độ trễ chat | Prefetch top_k nhỏ, chạy song song; tiêu chí S6 |
| Ghi vào KB prod gây sự cố | Snapshot + `ingest_batch` rollback; thử ở môi trường isolated trước |

## 6. Thay đổi sau review độc lập (2026-10-06)

| Phát hiện | Mức | Đã sửa trong plan |
|---|---|---|
| Gateway dùng sparse `bm25` trong Qdrant, không dùng index bm25s; plan cũ đòi rebuild bm25s | Cao | §3.2 bước 4-6 |
| Route mới trong `classify_chat_grounding` mâu thuẫn tiêu chí G3(b) | Cao | §3.4: tham số `knowledge_enabled` |
| Quảng bá knowledge kind cho model + scope mặc định `entity` → 0 hit | Cao | §3.3: suy scope từ kind, không quảng bá đợt 1 |
| Thiếu payload index cho `sensitivity`/`ingest_batch`/`source`/`license` | Trung | §3.2 bước 3 |
| `passes_filter` chưa có `must_not` | Trung | §3.3 |
| `mode` chưa log; dim không chứng minh đúng model | Trung | §3.2 bước 4, §3.3, G6 |
| Node `weakness` dùng `name`, không có `title` | Trung | §3.3 |
| Route `concept_kb` giao với `taxonomy` | Trung | §3.4 thứ tự ưu tiên |
| 2 test fail sẵn; ngưỡng S2 cao; S4/S6 cần hạ tầng | Thấp | Bộ tiêu chí |

## 7. Thay đổi trong lúc thực hiện (2026-10-06)

| Phát hiện khi chạy | Xử lý |
|---|---|
| Embedding chỉ nhận 512 token, plan ghi 1500 | Chunk ≤ ~1000 ký tự; 4/8051 chunk phải cắt khi embed |
| `_fetch_definition_nodes` khớp theo `cwe_id` nên hút chunk mitigation (cũng mang `cwe_id`) | Thêm `must_not` knowledge kinds |
| `kb_top_rerank_score` (cổng exploit) search không filter, chunk tri thức sẽ làm câu off-topic qua cổng | Dùng `entity_scope_filter()` |
| BM25 lấy top-k toàn collection rồi mới lọc scope: trên prod 763k điểm CVE sẽ lấn át ~8k chunk tri thức | `scope="knowledge"` đẩy filter vào truy vấn sparse của Qdrant |
| Routing bằng từ vựng bỏ sót 25% câu khái niệm | Tách từ vựng *cụ thể* và *chung*; từ chung chỉ tính khi kèm ý định khắc phục. Đo: 114/114 câu eval, 0/10 control, nhưng 46 prompt mô tả CVE trong golden cũng route vào tri thức (kiểm hồi quy trong E2E) |
| Điểm rerank bão hòa gần 1 (đúng ≥ 0,976) | `knowledge_min_rerank` mặc định 0,9: chỉ cắt đuôi hit yếu (13/570), không cắt top-1 nào |
| 6/114 truy vấn tiếng Việt thuần chỉ chạy dense | Không có token trùng với chunk tiếng Anh nên BM25 trống: giới hạn xuyên ngôn ngữ, không phải lỗi pipeline (xem criteria G6) |
| Baseline recall@5 0,675 trong `eval_set_results.json` đã lỗi thời | Đo lại bằng code HEAD trên đúng collection prod: 0,667 / MRR 0,598 |
