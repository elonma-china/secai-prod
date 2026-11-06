# Context chức năng sinh PoC (Sinh mã khai thác) SecAI

Cập nhật theo checkout ngày **09/10/2026**, cấu trúc mô phỏng [module-map/translate/](../translate/readme.md). Thư mục này cung cấp bản đồ API, cơ chế lấy/sinh PoC, và quy trình kiểm thử chức năng **Sinh mã khai thác** bằng một bộ 20 mã CVE thật đã xác minh tồn tại hay không trong catalog PoC và trong KB nội bộ tại thời điểm viết tài liệu này. Nội dung đối chiếu mã nguồn hiện có; không coi tài liệu này là bằng chứng đã chạy — bằng chứng chạy nằm ở `results/`.

## Đọc theo thứ tự

| File | Nội dung |
|---|---|
| [01-api-va-luong-xu-ly.txt](01-api-va-luong-xu-ly.txt) | Endpoint `/ai/exploit*`, cấu trúc catalog PoC (SQLite), 2 nhánh xử lý khi có/không có PoC thật trong DB, và cơ chế từ chối khi CVE không tồn tại |
| [02-test-giao-dien-tung-buoc.txt](02-test-giao-dien-tung-buoc.txt) | Các bước bấm trên `ias.id.vn` (`Sinh mã khai thác`) cho từng nhóm CVE |
| [03-tieu-chi-pass.txt](03-tieu-chi-pass.txt) | Tiêu chí PASS cho 3 nhóm CVE, cả 3 đều nhị phân đạt/không đạt: khai thác thật trên lab (in_db), checklist 8 tiêu chí (not_in_db), từ chối/bịa (fabricated); vai trò agent tổng hợp; mẫu báo cáo `.txt` + schema JSON tóm tắt |
| [04-test-data.json](04-test-data.json) | 20 CVE: 10 có PoC thật trong catalog, 5 CVE thật nhưng chưa có PoC trong catalog, 5 mã CVE không tồn tại (xuyên tạc) |
| [smoke-3case.json](smoke-3case.json) | Bộ test ĐẦU TIÊN rút gọn — đúng 3 case, 1 case/nhánh (in_db/not_in_db/fabricated), xác minh live 15/10/2026, chạy trước bộ đủ 20 case để kiểm nhanh cả 3 nhánh |
| [results/](results/) | Kết quả mỗi lần chạy: một file `.txt` (bằng chứng chi tiết) + một file `.json` (tóm tắt `cve_id`/`status`/`reason` máy đọc được, xem [03](03-tieu-chi-pass.txt) mục 7.1) mỗi phiên |

## Vì sao 20 CVE này được chọn (không phải tự bịa)

Khác với bộ dữ liệu dịch (dùng fixture tự tạo), 20 mã CVE trong [04-test-data.json](04-test-data.json) được chọn bằng cách **truy vấn trực tiếp hai nguồn dữ liệu thật đang chạy** trong phiên viết tài liệu này, không gõ tay theo cảm giác:

- **Catalog PoC** (SQLite, bảng `poc`/`poc_cve`, xem [01](01-api-va-luong-xu-ly.txt) mục 2): `docker exec secai-docker-gateway` truy vấn `/srv/poc/catalog.db` để biết CVE nào có artifact thật (`artifact_sha256 IS NOT NULL`).
- **KB nội bộ** (Qdrant collection `cve_kb_hybrid`): `POST /collections/cve_kb_hybrid/points/scroll` lọc theo `cve_id` để biết CVE nào hệ thống "biết" (`cve_known`) dù catalog PoC không có bài khai thác nào.

Vì KB này được đồng bộ hàng ngày (AutoScan), **một mã CVE "chưa gán" hôm nay có thể được gán thật trong vài ngày tới** — đã quan sát trực tiếp hiện tượng này khi dò số: `CVE-2026-66666` tưởng là số bịa nhưng lại là CVE WordPress thật mới nạp. Do đó nhóm "xuyên tạc" trong file 04 phải được **xác minh lại** bằng bước ở [02](02-test-giao-dien-tung-buoc.txt) mục 1 ngay trước khi chạy phiên test, không dùng vĩnh viễn danh sách cũ.

**Nhóm "có trong DB" ưu tiên CVE năm 2026 và có lab Docker tái hiện được**, theo đúng thứ tự phương pháp sau (không đảo ngược):

1. **Tìm lab trên internet trước** — tra từng CVE ứng viên xem có lab Docker công khai không (dự án [vulhub/vulhub](https://github.com/vulhub/vulhub) — repo mã nguồn mở thật, mỗi lỗ hổng một thư mục kèm `docker-compose.yml` + README, chạy bằng `docker compose up -d`).
2. **Rồi tìm trong DB (catalog nội bộ)** — với từng CVE đó, catalog PoC của chính hệ thống (`/srv/poc/catalog.db`) có sẵn một dòng `source='vulhub' AND kind='lab'`, dạng file `yaml` với `artifact_sha256` thật.
3. **Giao với CVE có artifact exploit thật** (`kind='exploit' AND artifact_sha256 IS NOT NULL`) → ghi vào `04-test-data.json`.

**Cập nhật xác minh dứt điểm (09/10/2026):** `WebSearch`/`WebFetch` công khai ban đầu KHÔNG định vị được thư mục cụ thể (search engine không đánh chỉ mục tốt cây thư mục sâu của repo, GitHub code-search cần đăng nhập). Sau đó đã **`git clone --depth 1 https://github.com/vulhub/vulhub.git`** (188 MB, repo công khai thật) và `find`/`ls` trực tiếp — **xác nhận đúng cả 10/10 CVE đều có thư mục thật với `docker-compose.yml`** trong repo:

| CVE | Thư mục trong `vulhub/vulhub` |
|---|---|
| CVE-2026-24061 | `inetutils/CVE-2026-24061` |
| CVE-2026-25253 | `openclaw/CVE-2026-25253` (kèm sẵn `poc.py`, `ws.py`) |
| CVE-2026-27944 | `nginx-ui/CVE-2026-27944` (kèm sẵn `poc.py`) |
| CVE-2026-33017 | `langflow/CVE-2026-33017` |
| CVE-2026-34197 | `activemq/CVE-2026-34197` |
| CVE-2026-63030 | `wordpress/CVE-2026-63030` (kèm sẵn `poc.py`) |
| CVE-2026-85706 | `gitlab/CVE-2026-85706` (kèm sẵn `poc.py`) |
| CVE-2025-24813 | `tomcat/CVE-2025-24813` (kèm `Dockerfile` riêng) |
| CVE-2025-29927 | `next.js/CVE-2025-29927` |
| CVE-2025-32433 | `erlang/CVE-2025-32433` (kèm sẵn `exploit.py`) |

`04-test-data.json` đã cập nhật `lab.folder_confirmed: true` và `lab.folder` cho cả 10 case. Kết quả: 7/10 case là CVE-2026 (phần còn lại là CVE-2025 cùng tiêu chí, vì không đủ 10 case CVE-2026 có cả hai điều kiện tại thời điểm viết tài liệu).

## Tổ chức agent khi chạy một phiên test (4 vai trò, không phải 1 agent tự chạy tự chấm)

Cách chấm khác nhau theo từng nhóm — xem chi tiết đầy đủ ở [03-tieu-chi-pass.txt](03-tieu-chi-pass.txt):

- **Agent thực thi** (1 hoặc nhiều, chia theo nhóm CVE): dùng giao diện thật `https://ias.id.vn/ai/exploit` (xem [02](02-test-giao-dien-tung-buoc.txt)) cho từng CVE trong 20 case, copy nguyên văn output (`description`/`exploit_guide`, `metadata`) — **không tự kết luận PASS/NOT PASS**, chỉ ghi quan sát.
- **Nhóm "có trong DB" (10 CVE) — khai thác thật trên lab, không so khớp văn bản**: dựng lab Docker tương ứng từ `vulhub/vulhub` (bảng thư mục dưới đây), copy mã PoC vừa lấy từ UI, sửa placeholder target, chạy thử nhắm vào lab. **Khai thác được → PASS; không được → NOT PASS**, ghi rõ nguyên nhân (hệ thống không trả mã thật / mã sai logic / lab lệch version / lỗi hạ tầng test) — quy trình đầy đủ ở [03](03-tieu-chi-pass.txt) mục 1.
- **Nhóm "không có trong DB" (5 CVE) — agent chấm % hữu ích**: nhận output + thông tin lỗ hổng thật từ KB (CWE/CVSS/mô tả), chấm theo rubric 8 tiêu chí ở [03](03-tieu-chi-pass.txt) mục 2, cho điểm %. Ngưỡng PASS ≥ 65%.
- **Nhóm "xuyên tạc" (5 CVE) — kiểm từ chối/bịa**: hệ thống từ chối đúng (không tìm thấy CVE, không mô tả/sinh PoC) → PASS; vẫn tạo ra PoC hoặc mô tả chi tiết một "lỗ hổng" cho mã CVE không tồn tại → FAIL — xem [03](03-tieu-chi-pass.txt) mục 3.
- **Agent tổng hợp** (vai trò mới, chạy sau cùng): gom kết quả cả 3 nhóm trên thành MỘT báo cáo duy nhất, không tự chấm lại, nhưng **với mỗi case không PASS phải ghi rõ 1 dòng nguyên nhân cụ thể** (không viết chung "lỗi hệ thống") — mẫu và yêu cầu chi tiết ở [03](03-tieu-chi-pass.txt) mục 4 và 6.

Tách vai trò để agent sinh PoC không tự chấm điểm chính nó, giống nguyên tắc đã áp dụng ở [module-map/translate](../translate/readme.md#tổ-chức-agent-khi-chạy-một-phiên-test).

## Luồng 2 agent chạy song song (UI lấy mã ‖ lab chạy khai thác)

Với case nhóm `in_db` (và mọi case có lab), chạy **2 agent song song** thay vì tuần tự, để không chết thời gian chờ: sinh PoC trên UI tốn ~30–60s+ (floor delay ở [02](02-test-giao-dien-tung-buoc.txt) mục 6), còn lab Docker phải pull image + khởi động vài phút. Dựng lab trong lúc chờ UI sinh mã.

```
t0   Orchestrator:  Agent A (UI)     ||   Agent B (lab)
t0   Agent B:  pull image -> (sửa compose nếu trùng cổng) -> up -d -> chờ readiness
t0   Agent A:  mở ias.id.vn/ai/exploit -> nhập CVE -> chờ -> ghi mã + meta
     Agent B:  poll file mã của A -> copy NGUYÊN mã -> thay tham số -> chạy vs lab -> ghi log
tend Orchestrator: chờ cả hai -> gom -> agent chấm ([03](03-tieu-chi-pass.txt))
```

- **Agent A (UI-extractor):** lấy mã + `metadata` (`candidate_status`/`section1_tier`/`sha256`/banner) từ **giao diện CHÍNH `https://ias.id.vn/ai/exploit`** (đăng nhập `dev201`; Devspace `http://127.0.0.1:5173` chỉ để chẩn đoán cục bộ, không dùng làm nguồn ghi kết quả), ghi ra `results/<run>/<case>-code.<ext>` + `<case>-meta.json`. Không tự chấm.
- **Agent B (lab-runner):** đọc file mã đó, **thay tham số bắt buộc** (target URL, token, path theo lab), chạy nhắm lab, ghi `results/<run>/<case>-exploit.log` + `<case>-verdict.json`. Không sửa logic.
- **Định nghĩa PASS:** *nguyên mã → chỉ thay tham số → chạy → khai thác thành công.* Phải sửa logic mới chạy ⇒ NOT PASS loại (b) lỗi logic. Lab lệch version CVE ⇒ loại (c). Chi tiết đầy đủ (hợp đồng file handoff, danh mục "thay tham số" được phép, các bẫy lab) ở [02](02-test-giao-dien-tung-buoc.txt) **mục 3.1**.
- Ví dụ thật: CVE-2026-10053 — mã synthesized + tham số hoá không đủ (auth header sai kiểu) + lab GitLab 17.11.0 ngoài dải bị ảnh hưởng 18.8–19.2 ⇒ NOT PASS, xem [results/2026-10-15-02.txt](results/2026-10-15-02.txt).

## Chức năng đang dùng

- **Sinh mã khai thác (đọc, không thực thi)**: màn hình Devspace `ExploitScreen.tsx`, menu **Sinh mã khai thác**, gọi `POST /ai/exploit/stream`. Luôn gắn nhãn `READ-ONLY · draft_not_executed`/`synthesized_not_executed`, mã trả về đã bị "defang" (`neutralize_active_content`), không thể thực thi trực tiếp từ UI.
- **Không thuộc phạm vi tài liệu này**: `/ai/exploit/run`, `/ai/exploit-advance*`, `/ai/verify` — các endpoint khai thác ĐA-BƯỚC một target thật (phương pháp D theo quy ước nội bộ), yêu cầu `POC_EXPLOIT_ENABLED=true` và target trong allowlist. Đây là chức năng khác, không phải "sinh PoC" đọc-để-tham-khảo.

## Nguồn context cần đọc khi cập nhật

Đường dẫn tính từ thư mục này:

- [Màn hình](../../secai-fe-devspace/src/screens/ExploitScreen.tsx), [parser hiển thị PoC](../../secai-fe-devspace/src/components/FileCode.tsx), [client stream](../../secai-fe-devspace/src/api.ts).
- [Routes `/ai/exploit*`](../../src/secai_gateway/api/routes.py) (hàm `_exploit`, `_exploit_stream`, dòng ~1914–2050).
- [Catalog PoC](../../src/secai_gateway/poc/catalog.py), [models](../../src/secai_gateway/poc/models.py), [backend lấy mã](../../src/secai_gateway/poc/backend.py).
- [Quyết định 4 tier Section 1](../../src/secai_gateway/poc/inject.py) (`enforce_section1`, `enforce_reference_code`) — nơi `candidate_status`/`section1_tier` được tính, **không dựa vào lời model tự nhận**.
- [Prefetch/CVE-known](../../src/secai_gateway/poc/prefetch.py), [hợp nhất đa-file](../../src/secai_gateway/poc/consolidate.py).
- [Cấu hình](../../src/secai_gateway/config.py): `poc_catalog_db`, `poc_exploit_enabled`, `poc_exploit_simulate_delay_s`, `exploit_relevance_gate_enabled`, `web_search_enabled`.

Nếu tài liệu cũ khác mã hiện tại hoặc KB đã nạp thêm CVE mới trùng số đã dùng ở nhóm "xuyên tạc": kiểm tra lại bằng truy vấn trực tiếp catalog/KB như mục trên, không suy ra từ tài liệu này.
