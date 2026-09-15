# Quy ước file hồ sơ minh chứng — Loại 2 (`/ai/evidence/*`)

> Hình thức hoá quy ước đã thống nhất trong họp hôm nay, để `GET /ai/evidence/*` ([01-API-DESIGN.md](./01-API-DESIGN.md)) đọc được nhất quán. Team chỉ cần **thả đúng file vào đúng thư mục** — không cần biết API, không cần sửa code.

> **Đây là quy ước cho Loại 2 (tóm tắt curated).** Loại 1 (xem nguyên bản hồ sơ gốc) không cần quy ước file — đó là duyệt trực tiếp cây thư mục `HO-SO-GOC-NGHIEM-THU-AI/` qua `GET /ai/source-dossier/*`, xem `00-TONG-QUAN.md` §5. Tài liệu này chỉ nói về file tóm tắt tay theo từng tiêu chí.

## 1. Cấu trúc 1 file

```
Dòng 1  : nguyên văn nội dung điều khoản/tiêu chí trong tài liệu hợp đồng
          (dùng để tìm kiếm & mapping — so khớp EQUALS, không phải chứa/gần đúng)
Dòng 2+ : nội dung minh chứng — Markdown hoặc HTML thuần
          BẮT BUỘC có ít nhất 1 dòng "Nguồn: <đường dẫn trong hồ sơ gốc>" — xem mục 1.1
```

## 1.1 Bắt buộc trích dẫn hồ sơ gốc (mới, theo yêu cầu bổ sung)

Mọi con số/khẳng định trong nội dung (dòng 2+) **phải lấy từ hồ sơ gốc** `HO-SO-GOC-NGHIEM-THU-AI/` (mirror cục bộ của Drive `17Qq9hXVl3EOu9eCWtYkBVPRGPhFON-66` — xem `00-TONG-QUAN.md` §0), **không được tự viết số** không có nguồn. Mỗi file phải có ít nhất 1 dòng dạng:

```
Nguồn: 03. Kpi_results/Model/quality/raw-logs/task-summary.json
```

— đường dẫn tương đối tính từ gốc `HO-SO-GOC-NGHIEM-THU-AI/`, để ai đọc cũng bấm được `GET /ai/source-dossier/file?path=...` (§S, `01-API-DESIGN.md`) đối chiếu ngay, không phải tin suông. Nếu số liệu trùng với 1 `test_id` đã có trong `/ai/kpi-bench` (xem §K), nên ghi kèm `test_id` đó (vd `Nguồn: R-LLM-SAST85 (xem /ai/kpi-bench/tests/R-LLM-SAST85/result)`) — 2 nơi cùng trỏ 1 nguồn thì không thể lệch số.

Ví dụ file `sast-85pct.md`:
```
Hệ thống hỗ trợ phân tích mã nguồn tĩnh (SAST) với độ chính xác tối thiểu 85%
## Kết quả đo thực tế

Đo trên bộ 200 mẫu SAST (OWASP Top 10 + CWE Top 25), ngày 2026-08-26.

| Chỉ tiêu | Kết quả | Ngưỡng |
|---|---|---|
| Độ chính xác | **85.0%** | ≥ 85% |

Nguồn: `03. Kpi_results/Model/quality/raw-logs/task-summary.json` — test_id `R-LLM-SAST85` (xem `/ai/kpi-bench/tests/R-LLM-SAST85/result`).
```

## 2. Định dạng cho phép

- Chỉ nhận đuôi **`.md`** hoặc **`.html`** — đúng 2 loại note họp đã chốt. File đuôi khác bị bỏ qua khi list (không lỗi, chỉ không xuất hiện trong `/ai/evidence`).
- Không giới hạn nội dung Markdown/HTML (bảng, ảnh embed base64/link, code block…) miễn dòng 1 vẫn là 1 dòng thuần văn bản (không markdown heading, không thẻ HTML) — vì dòng 1 dùng để so khớp `equals`.
- Khuyến nghị dung lượng mỗi file **< 2MB** (để load nhanh trên web) — ảnh minh chứng nên để dạng link tới `dashboards/figures/*` hoặc ảnh đã nén, không nhúng ảnh gốc độ phân giải cao base64.

## 3. Tên file → `id`

- `id` trong API = tên file bỏ đuôi (`sast-85pct.md` → `id: "sast-85pct"`).
- Khuyến nghị đặt tên file kebab-case, không dấu, không khoảng trắng — nhưng **không bắt buộc**, vì việc tra cứu chính luôn đi qua `contract_text` (dòng 1), không qua tên file.
- Trùng tên file → file mới ghi đè. Trùng `contract_text` giữa 2 file khác tên → `GET /ai/evidence/match` trả về file có `updated_at` (mtime) mới nhất, đồng thời nên rà lại vì đây thường là dấu hiệu 2 người cùng làm 1 tiêu chí.

## 4. Thư mục & đồng bộ (Phase 1 — làm tay)

> ⚠️ **2 folder Drive khác nhau, đừng nhầm:** `1D8KpUTaLSe2QCvy2AwPIC602PWQFy00O` là nơi up file tóm tắt **Loại 2** (nội dung của tài liệu này). `17Qq9hXVl3EOu9eCWtYkBVPRGPhFON-66` là **hồ sơ gốc Loại 1** (đã tải mirror về `HO-SO-GOC-NGHIEM-THU-AI/`, xem `00-TONG-QUAN.md` §0) — nơi Loại 2 phải trích dẫn tới, không phải nơi up file tóm tắt.

1. Nơi làm việc chung vẫn là Google Drive: `https://drive.google.com/drive/folders/1D8KpUTaLSe2QCvy2AwPIC602PWQFy00O` — mọi người trong team up file `.md`/`.html` vào đây như note họp đã mô tả, **không đổi quy trình này**.
2. Trên server gateway, thêm 1 thư mục đọc riêng cho API, ví dụ `evidence/` ở gốc `secai-agent-gateway/` (đường dẫn cấu hình qua biến môi trường `EVIDENCE_DIR`, mặc định `./evidence`).
3. Trước mỗi lần demo/nghiệm thu: tải thư mục Drive về và copy đè vào `EVIDENCE_DIR` trên server (thủ công, 1 lệnh `rclone copy` hoặc kéo-thả qua `scp` — không cần app riêng ở Phase 1).
4. API chỉ **đọc** thư mục này tại thời điểm gọi (list lại mỗi request hoặc cache theo mtime thư mục — chi tiết implement, không ảnh hưởng thiết kế) → cứ copy file mới vào là `/ai/evidence` thấy ngay, không cần restart gateway.

**Phase 2 (đề xuất, chưa làm ngay):** đồng bộ tự động từ Drive xuống `EVIDENCE_DIR` bằng Google Drive API hoặc `rclone` chạy cron — bỏ hẳn bước tải/copy tay ở mục 3. Không chặn đợt nghiệm thu này vì quy trình tay ở trên đã đủ dùng và có sẵn theo đúng thói quen team đang làm với Drive.

## 5. Việc KHÔNG nằm trong quy ước này

- Không dùng cơ chế này để đăng dữ liệu **động** (số liệu đổi liên tục theo thời gian thực) — loại đó đi qua API riêng có cấu trúc (nhóm F, D trong `01-API-DESIGN.md`), không phải file tĩnh.
- Không đăng code/script, không đăng file cấu hình chứa secret (API key, mật khẩu SSH…) — hồ sơ minh chứng là nội dung **trình bày cho khách xem**, không phải kho lưu vận hành nội bộ.
