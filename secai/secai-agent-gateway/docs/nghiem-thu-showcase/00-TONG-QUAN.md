# Showcase hồ sơ nghiệm thu AI trên giao diện — Tổng quan & quyết định thiết kế

> Trạng thái: **Stage 1 ĐÃ TRIỂN KHAI & chạy được** (15 endpoint + trang demo `/showcase-demo/`); nhóm KB và nhóm S chưa làm. Bản v2 (2026-09-08) — đọc lại **toàn bộ 121 dòng** cột "Hướng hiển thị bổ sung" (bản v1 trước chỉ trích được 24 dòng do công cụ tóm tắt bỏ sót), cộng thêm 3 điều chỉnh: (1) tải "hồ sơ gốc" về máy, (2) cơ chế mock-benchmark có thanh tiến độ, (3) evidence 2 loại.

## 0. "Hồ sơ gốc" — đã tải về máy

Tải toàn bộ nội dung (trừ ảnh chụp màn hình `.png`/`.jpg`, giữ `.md/.json/.jsonl/.csv/.py/.svg`... — xem lý do loại ảnh ở mục 0.2) từ Drive `17Qq9hXVl3EOu9eCWtYkBVPRGPhFON-66` về:

```
/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/
├── 01. Ho_so_huan_luyen/       (datasets, logs, dashboards, checkpoints, reports — ~ trùng FINETUNE/ho_so_ft-finetuning cục bộ)
├── 02. Bo_build_finetuning/    (source code build finetuning — ~ trùng FINETUNE/source-finetuning)
├── 03. Kpi_results/            (kết quả đo KPI — ~ trùng KPI-VA-GEN-TEST/KET-QUA cục bộ)
└── 04. Huong_dan_cai_dat/      (tài liệu cài đặt/triển khai)
```

Đây là **nguồn số liệu duy nhất** cho mọi API trả về ở đợt này — API không tự tính lại, không lấy số từ chỗ khác.

### 0.1 Vì sao bản cục bộ (FINETUNE/, KPI-VA-GEN-TEST/) trước đây không dùng làm nguồn trực tiếp

Hai thư mục đó là bản làm việc/derive từ cùng một đợt đo — nội dung khớp với hồ sơ gốc ở mọi chỗ đã đối chiếu, nhưng **hồ sơ gốc trên Drive mới là bản chính thức nộp cho khách**. Từ nay API phải trỏ vào `HO-SO-GOC-NGHIEM-THU-AI/` làm chuẩn; `FINETUNE/`/`KPI-VA-GEN-TEST/` chỉ dùng tham khảo chéo khi cần đối chiếu.

### 0.2 Vì sao loại ảnh khi tải về, nhưng chart-image API (nhóm F) vẫn còn dùng được

Danh sách file thật trong hồ sơ gốc cho thấy `.png`/`.jpg` gồm 2 loại rất khác nhau:
- **149 ảnh chụp màn hình rời ở gốc Drive** (`Trí tuệ nhân tạo 01–116 - <mô tả>.png`) — đây là ảnh người làm hồ sơ tự chụp lại các file JSON/MD/log để minh chứng thủ công. Loại này **chính là thứ API phải thay thế** (khách xem trực tiếp qua API/UI, không cần ảnh chụp màn hình nữa) → **loại khỏi bản tải**.
- **16 ảnh chart thật** nằm trong `01. Ho_so_huan_luyen/.../dashboards/figures/` (`training_*.png`, `metrics_*.png`, cộng 14 `.svg` loss/learning-rate/eval-metrics) — đây là **dữ liệu** (biểu đồ render từ số đo thật), đúng thứ API nhóm F cần trả → **giữ lại khi nhập .zip** và API đọc thẳng từ hồ sơ gốc, không phụ thuộc bản cục bộ nữa.

### 0.3 Giới hạn khi expose hồ sơ gốc qua API (xem nhóm S — mục 5)

Hồ sơ gốc có vài file dataset gốc rất nặng (`real_cybersec_instruction.jsonl` 231MB, các `dataset_*.jsonl` sinh ra 34–52MB/file). API duyệt hồ sơ gốc (Loại 1) chỉ trả **danh sách + metadata** cho các file này, **không nhúng nguyên nội dung vào response** — xem quy tắc "ngưỡng preview" ở `01-API-DESIGN.md` §S.

---

## 1. Đọc lại cột "Hướng hiển thị bổ sung" — toàn bộ 121 dòng, phân nhóm hành động

| Nhóm | Ý note họp | Số dòng liên quan | Hành động |
|---|---|---|---|
| **A — Đã có sẵn màn hình app** | Chat/dịch/báo cáo | ~13 dòng | Không cần API mới, demo trực tiếp trên app. |
| **B — Trả lời qua chatbot** | CWE Top25/OWASP Top10, liệt kê nhóm CWE (thay SSH+Neo4j) | 2 dòng | Không cần API mới — đổi hướng dẫn demo, không code. |
| **C — Model & phần cứng** | "Show hết thông tin model + phần cứng + **bản quyền**, tên phải khớp đúng chữ trong hợp đồng (vd `Qwen 3 30B 2507`)"; LLM bổ trợ chỉ cần ghi tên; bảng model quản trị: tên/model gốc/dataset dùng (links); click model → xem meta+sample dataset; hạ tầng GPU/RAM/SSD ghi log lúc deploy; menu bật/tắt Hybrid Search hiển thị cấu hình mặc định | Rows 8,9,39,40,65,77-80 | **`GET /ai/model-info`** (mở rộng, xem §D 01-API-DESIGN.md) |
| **D — Log huấn luyện cần bổ sung nội dung** | Thêm vào log: phương pháp PEFT/LoRA rank, nền tảng phần cứng, tổng thời gian, tên nhóm dữ liệu, 3 tab log độc lập/model, ma trận dữ liệu train/test/val mỗi model | Rows 43-54 | **Đã làm:** `log_stream` (log tiếng Anh dựng từ `training-log.jsonl` thật) + `training_summary` (loss · GPU · thời gian · dataset) trong `GET /ai/finetune/runs/{run_id}/log`; ma trận train/val/test ở `GET /ai/finetune/data-processing` + `dataset-appendix`. Chỉ số quy đổi "% khớp dữ liệu" **đã bỏ** theo yêu cầu (chỉ hiện số thật). |
| **E — KPI có nút chạy + thanh tiến độ (mock-benchmark)** | Tab theo từng nhóm kiểm thử (SAST/Exploit-Explain/Auto-remediation, Embedding Hit-rate/Reranker Precision, RAGAS Faithfulness/Relevance/EntityPrecision/Hallucination, Radar, Context-window/TPS/TTFT/RPS "giống speed test") → bấm chạy → thanh tiến độ (giả lập, có thể mock) → hiện % thật; RAGAS/model-quality: click vào xem bảng input/output/score từng dòng; **kết quả PHẢI khớp hồ sơ gốc và khớp câu trả lời của trợ lý AI khi hỏi trực tiếp** | Rows 29,33,36,56-63,68,73-75,81-93 | **Nhóm mới `/ai/kpi-bench/*`** — xem §K 01-API-DESIGN.md |
| **F — Demo trực tiếp bằng code thật (không mock số)** | Sanitization PII/Secret: "vẫn là chạy đoạn code kia" — demo sống, không phải xem log tĩnh | Rows 15, 94 | **`POST /ai/sanitize-demo`** — chạy sanitizer thật, xem §L |
| **G — Log vận hành khác** | Log crontab đồng bộ KB hằng ngày (không cho sửa qua UI, chỉ xem tần suất/lịch sử) | Row 70 | `GET /ai/kb/sync-log` — **Stage 2, chưa làm** |
| **H — Hồ sơ minh chứng chung (2 loại)** | Xem "nguyên bản" hồ sơ gốc trực tuyến; xem tóm tắt ngắn theo từng tiêu chí đã tách nhỏ (bám hồ sơ gốc) | rải rác + note riêng của bạn (mục 3 yêu cầu) | Loại 2 **`/ai/evidence/*`** đã làm; Loại 1 `/ai/source-dossier/*` — **Stage 2, chưa làm** |
| **I — Ngoài phạm vi / đã biết là lệch, không xử lý ở đây** | Helm chart K8s chưa có; Docker image chưa đúng dung lượng cam kết; "kiểm chứng độc lập" là việc phối hợp nhân sự, không phải tính năng UI | Rows 71, 95, 101 | Giữ nguyên là gap đã ghi nhận (memory nội bộ), không phải việc của API. |

Chi tiết từng dòng gốc (trích nguyên văn tiếng Việt trong note họp) đã đối chiếu và liệt kê đầy đủ trong quá trình làm — không lặp lại toàn bộ 121 dòng ở đây để tránh trùng lặp với `01-API-DESIGN.md`.

## 2. Nguyên tắc bắt buộc: số liệu KPI phải "khớp chính xác" — 1 nguồn sự thật

Note họp nhấn hai lần: *"kết quả cần khớp với khi hỏi ở tab trợ lý AI"* và *"kết quả này phải lấy base từ hồ sơ gốc (khớp chính xác về con số)"*.

> **⚠️ SỬA 2026-09-08:** bản thiết kế v2 ban đầu (và bản code đầu tiên) đọc nhầm từ `KPI-VA-GEN-TEST/01-KPI/kpi-registry.json` — đây là **bản tự kiểm toán lại cục bộ**, KHÔNG phải hồ sơ gốc, và có số liệu **khác thật** ở vài chỗ quan trọng (vd Faithfulness: bản kiểm toán ghi 96.71%/**FAIL**, hồ sơ gốc thật ghi 98.48%/**PASS**). Đã sửa lại toàn bộ để đọc đúng từ hồ sơ gốc — xem mục dưới.

**Nguồn đúng: `HO-SO-GOC-NGHIEM-THU-AI/03. Kpi_results/kpi-summary.json`** (mirror cục bộ của đúng file trong Drive `17Qq9hXVl...`) — file rollup 20 KPI, mỗi KPI có sẵn `name`/`unit`/`thr`/`value`/`pass`/`group`. Đây là **input trực tiếp** cho `/ai/kpi-bench/*`, không qua bước tính toán/diễn giải nào thêm. Một số file con (mẫu chi tiết input/output/score, `radar-data.json`, các `summary.md`) cũng đã đồng bộ về cùng thư mục — xem `01-API-DESIGN.md` §Nhóm K để biết chính xác file nào đã có.

**Quyết định thiết kế:** API `/ai/kpi-bench/*` đọc **thẳng** từ `kpi-summary.json` này — không tính lại, không làm tròn khác đi, và **không trộn** số liệu từ `KPI-VA-GEN-TEST` (kể cả khi số ở đó "trông hợp lý hơn" hay có vẻ chi tiết hơn) — đúng theo yêu cầu đã chốt.

**Về đợt tự kiểm toán cục bộ (`KPI-VA-GEN-TEST`):** vẫn có giá trị tham khảo nội bộ — nó phát hiện & ghi chú vài rủi ro phương pháp luận đáng chú ý trong hồ sơ gốc (vd 1 KPI thứ 20, `R-CHATFLOW-TPS25`, có `pass:true` được ghi nhận là **gán tay trong script build chứ không có script nào tính lại được**; Answer Relevance dùng LLM-judge chấm chính bằng model đang được đo dù hợp đồng ghi "Cosine Similarity"). Những rủi ro này **không được đưa vào API/số hiển thị chính** (đúng nguyên tắc chỉ dùng hồ sơ gốc) — nếu cần trình bày cho chủ đầu tư, nên làm ở kênh riêng (báo cáo audit nội bộ), không lẫn vào bản demo "khớp hồ sơ gốc" này.

**Rủi ro cần chốt trước khi demo (không phải việc của API, nhưng phải nêu):** nếu khách **cũng** hỏi thẳng chatbot "Faithfulness của bạn bao nhiêu %" thì câu trả lời của LLM phải lấy từ **cùng** file này (qua KB/tool), nếu không hai nơi có thể lệch số. Cần xác nhận với đội chatbot việc này đã được ingest vào KB hay chưa — **ghi vào việc-còn-thiếu**, không tự suy đoán.

**Trạng thái đồng bộ:** hồ sơ gốc đã tải **đủ 394/394 file** (tải tay 1 lần dạng .zip rồi nhập bằng `scripts/import_hosogoc_zip.py`, bỏ 149 ảnh chụp màn hình rời ở gốc). Cả 11 KPI nhóm quality đều có bảng mẫu chi tiết từ `raw-logs/*.jsonl` thật. Kiểm tra lại bất cứ lúc nào: `python3 scripts/audit_hosogoc.py --api <base>` (đủ file · nguồn API tồn tại · số khớp `kpi-summary.json`).

## 3. "Giả lập" nghĩa là gì — ranh giới rõ ràng

- **ĐƯỢC giả lập:** thời gian chờ + hiệu ứng thanh tiến độ/spinner (API không thật sự chạy benchmark khi bấm nút — vì benchmark thật mất hàng giờ/GPU, không hợp để chạy live mỗi lần khách bấm). FE tự dựng animation dùng field `simulate_ms` API trả về.
- **KHÔNG được giả lập:** con số kết quả cuối (%, input/output/score mẫu) — luôn là số thật đọc từ hồ sơ gốc như mục 2.

## 4. "Mức độ khớp dữ liệu %" thay cho loss/hội tụ

Khách không hiểu khái niệm "loss giảm dần = hội tụ". Đề xuất: field `data_match_percent` tính từ loss thật theo công thức đơn giản, minh bạch:

```
match% = max(0, (1 − eval_loss_hiện_tại / loss_bước_đầu) × 100)
```

Cả 2 số (`eval_loss`, `loss_bước_đầu`) đều là số thật đã ghi nhận (`hyperparameters.md`, `training-log.jsonl`), công thức nêu rõ trong response để không ai hiểu nhầm đây là 1 KPI cam kết hợp đồng — nó là **chỉ số minh hoạ** giúp khách không phải hiểu "loss". Số cụ thể từng model xem `03-BO-SUNG-LOG-HUAN-LUYEN.md`.

## 5. Evidence — 2 loại (theo yêu cầu mới)

| | Loại 1 — Source dossier (nguyên bản) | Loại 2 — Evidence tóm tắt (curated) |
|---|---|---|
| Nội dung | Duyệt trực tiếp cây thư mục hồ sơ gốc đã tải (mục 0) — file JSON/MD/CSV thật, không chỉnh sửa | Tóm tắt ngắn 1 file/tiêu chí, đúng cách làm cũ (dòng 1 = câu hợp đồng để mapping) |
| Nguồn | `HO-SO-GOC-NGHIEM-THU-AI/` | Do team viết tay, **bắt buộc trích dẫn** đường dẫn cụ thể trong hồ sơ gốc làm bằng chứng (không được nêu số mà không dẫn nguồn) |
| Dùng khi | Khách muốn "soi tận gốc", không tin tóm tắt | Khách muốn đọc nhanh, không cần tìm trong hồ sơ gốc |
| API | `/ai/source-dossier/*` | `/ai/evidence/*` |

Chi tiết API + quy ước file: xem `01-API-DESIGN.md` và `02-EVIDENCE-FILE-CONVENTION.md` (đã cập nhật).

## 6. Việc còn thiếu / cần xác nhận trước khi code

1. ~~Hồ sơ gốc đang tải~~ **Xong** — 394/394 file, audit 3 lớp sạch (0 thiếu · 0 nguồn hỏng · 20/20 KPI khớp).
2. Xác nhận chatbot có đọc cùng nguồn KPI khi được hỏi trực tiếp không (mục 2) — nếu chưa, cần một việc riêng (ingest KPI registry vào KB) trước khi demo, không thuộc phạm vi 3 nhóm API thiết kế ở đây.
3. `04. Huong_dan_cai_dat/` đã có trong mirror nhưng Stage 1 chưa dùng tới — nếu cần trưng bày thì thuộc nhóm S (duyệt hồ sơ gốc), chưa làm ở Stage 1.
