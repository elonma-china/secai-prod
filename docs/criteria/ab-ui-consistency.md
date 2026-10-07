# Tiêu chí nghiệm thu — giao diện "Đánh giá A/B" (admin) đồng nhất với các chức năng AI

Ngày lập: 2026-10-06. Phạm vi: `secai-fe-devspace/src/screens/review/*` (phần admin: `ReviewOverview`, `TrainingWorkspace`, `TrainingBatchDetail`, `ReviewSamplingSettings`) và khối A/B phía user (`ReviewPair`, `DemoFeedback`, `LiveReviewWidget`) chỉ ở mức token/nút.

Cơ sở: kiểm kê hiện trạng (`before-*.png`) và trích chuẩn từ Chat/Dịch/Exploit/Báo cáo (`ref-*.png`) trong `output/playwright/ab-ui-audit/`.

Cách chấm: mỗi tiêu chí **ĐẠT / KHÔNG ĐẠT / KHÔNG KIỂM ĐƯỢC** kèm bằng chứng (số đo, ảnh, tên test). Nhóm **BẮT BUỘC (B)** phải đạt hết; nhóm **NÊN (N)** có thể ghi nhận ngoại lệ có lý do. Đo ở 1440×900 (D) và 390×844 (M) trừ khi ghi khác. Không chấm ĐẠT khi chưa có bằng chứng chạy thật.

## 1. Cấu trúc trang và cuộn

| ID | Mức | Tiêu chí | Cách kiểm |
|---|---|---|---|
| S1 | B | Màn A/B có thanh header riêng full-bleed như các màn AI khác: viền dưới `--line`, padding `16px 26px` (mobile ≤680: 12), tiêu đề h3 21px/600 màu `--text`. | computed style so với Báo cáo/Dịch |
| S2 | B | Body có vùng cuộn riêng (`overflow:auto`, `flex:1`, `min-height:0`), padding `22px 26px`. Cuộn bằng wheel/touch tới được nút cuối trang ở mọi tab, kể cả khi mở chi tiết/batch/log dài. | D+M: nội dung > khung, `scrollTop` đổi sau wheel; nút "Duyệt", "Xác nhận", log cuối nhìn thấy được |
| S3 | B | Bề rộng nội dung ổn định: cùng x và cùng width ở Phản hồi/Fine-tune/chi tiết batch (không nhảy 913/742/712/1144); không `max-width` co theo nội dung (cap ≥1280 nếu có, canh trái đúng lề màn khác x=274 khi sidebar mở). | đo `getBoundingClientRect` ở 3 tab |
| S4 | B | Không tràn ngang: `documentElement.scrollWidth == innerWidth` ở D và M, ở mọi tab và khi mở chi tiết. | evaluate |
| S5 | N | Tiêu đề màn có icon lucide cùng cỡ/ màu với các màn khác. | ảnh |

## 2. Token, màu, kiểu chữ

| ID | Mức | Tiêu chí | Cách kiểm |
|---|---|---|---|
| T1 | B | Chỉ dùng token `theme.css` (`--teal*`, `--line*`, `--panel*`, `--text`, `--muted*`, `--ok/--warn/--danger*`). Không dùng `--border`, `--accent`, `--surface`, `--teal-soft` hay màu fallback hex (`#dce2e8`, `#2563eb`, `#103c3b`...) trong CSS/TSX của A/B. | grep CSS khối `.ab-*` + file TSX |
| T2 | B | Không `!important`, không ghi đè `.iasbtn` toàn cục trong phạm vi A/B (không ép mọi nút thành ghost). | grep |
| T3 | B | Font: `var(--font-body)` / `var(--font-heading)`, không khai font cứng. Cỡ chữ tiêu đề phần trong body 16px; chữ phụ ≥12px `--muted`. | computed |
| T4 | N | Tối đa 2 cấp phụ đề liền nhau; mô tả dài nằm trong card hoặc `details`, không thành 3–4 dòng `review-muted` rời. | ảnh |

## 3. Thành phần

| ID | Mức | Tiêu chí | Cách kiểm |
|---|---|---|---|
| C1 | B | Tab "Phản hồi / Fine-tune" dùng `.iasseg` đặt trong header như Báo cáo; trạng thái đang chọn nhìn rõ; giữ `role="tablist"` aria-label "Quản lý phản hồi" và `role="tab"`/`aria-selected`. | ảnh + vitest |
| C2 | B | Panel/bảng/chi tiết/cài đặt dùng `.iascard` (bo 10, nền `--panel2`, viền `--line`); lồng bên trong bo 7. Cùng một cặp A/B ở admin và user có cùng kiểu card đáp án. | computed |
| C3 | B | Nút: primary teal chỉ cho hành động chính duy nhất mỗi vùng (Duyệt/Xác nhận/Fine-tune); ghost cho phụ (Làm mới, Đóng, Trước/Sau); `.sm` cho nút trong hàng bảng. Chiều cao đồng đều trong một hàng (không 46px vs 39px). Nút "Đóng" cùng kiểu ở mọi dialog (admin và demo user). | computed |
| C4 | B | Trạng thái dùng `.chip` (accent/ok/warn/crit): "User chọn A/B/Ngang nhau/Cả hai kém/Bỏ qua/Mâu thuẫn", trạng thái batch/attempt (Hoàn tất/Lỗi/Đang chạy…). Không dùng `<strong>` trơn làm badge. | ảnh + DOM |
| C5 | B | Chi tiết/xác nhận (Xem, Duyệt toàn bộ, Fine-tune, Xem batch) là panel/modal thật có backdrop hoặc vùng riêng không chen làm đẩy bảng khỏi tầm nhìn; `aria-modal` + focus vào dialog khi mở, Esc đóng, trả focus về nút gọi. Giữ aria-label: "Chi tiết phản hồi", "Xác nhận duyệt toàn bộ", "Xác nhận fine-tune", "Chi tiết batch". | thao tác + vitest |
| C6 | B | Trạng thái tải/rỗng/lỗi thống nhất: tải = spinner `.spin` + chữ; rỗng = icon + một dòng `--muted` trong card; lỗi = khối `--danger-dim/--danger-line` `role="alert"` kèm nút "Thử lại" ở cả hai tab. | ép lỗi 500 + rỗng |
| C7 | B | Bảng: cột câu hỏi co giãn, cột phụ (User chọn, Thời gian, Thao tác) không xuống dòng vô lý ở 1280; mã batch hiển thị dạng mã (monospace) và vẫn `id.slice(0,8)`; header bảng 12px `--muted`; hàng hover. | ảnh D |
| C8 | N | Cài đặt tỷ lệ mời toàn hệ thống có chỗ rõ ràng (không details lồng details); nút "Lưu tỷ lệ" nhìn thấy khi disabled; input/nút cùng hàng, cùng chiều cao. | ảnh |
| C9 | N | Phân trang có số trang/tổng dòng, nút Trước/Sau ghost; mobile ≥ 44px chạm cho nút chính. | đo |

## 4. Responsive và truy cập

| ID | Mức | Tiêu chí | Cách kiểm |
|---|---|---|---|
| R1 | B | ≤680: bảng thành thẻ (`data-label`), toolbar xuống dòng gọn (không nút lẻ một mình), `.iasseg` rộng 100%; ≤1100: lưới 2 cột về 1. | ảnh M |
| R2 | B | Đủ focus-visible, tên accessible cho mọi nút/field (tìm kiếm "Tìm câu hỏi", combobox "Lần chạy"), tương phản chữ ≥ 4.5:1 cho chữ thường trên nền panel. | snapshot a11y + đo |

## 5. Luồng chức năng (không được vỡ sau restyle)

| ID | Mức | Tiêu chí | Cách kiểm |
|---|---|---|---|
| F1 | B | User chọn → có bản ghi (câu hỏi, 2 đáp án, lựa chọn, người gửi, thời điểm); user không thấy trang A/B; không có đoạn giới thiệu trước hai đáp án. | canary + vitest |
| F2 | B | Admin Xem → thấy câu hỏi, hai đáp án, lựa chọn user; Duyệt/chọn lại/loại; quyết định lưu riêng, phiếu user giữ nguyên. | canary API + UI |
| F3 | B | Duyệt toàn bộ: hiển thị số lượng theo bộ lọc hiện tại (kể cả trang chưa hiện), xác nhận mới thực thi; chỉ winner rõ ràng, đủ phiếu, nhất quán; tie/cả hai kém/bỏ qua/thiếu phiếu/mâu thuẫn không bị duyệt. | canary |
| F4 | B | Mẫu đã duyệt rời "Chờ duyệt", vào hàng chờ Fine-tune; đếm đúng, dedup, ngưỡng 1.000; chưa đủ thì nút Fine-tune bị khoá và nói rõ lý do. | canary |
| F5 | B | Fine-tune cần xác nhận; chốt batch bất biến; mẫu duyệt sau không vào batch đang có; double-submit/replay không tạo batch thứ hai. | canary API |
| F6 | B | Lịch sử: ngày, mã, số mẫu, trạng thái, "Xem batch"; chi tiết chọn từng attempt, log tải đủ trang, reload vẫn giữ. Không tạo tiến trình/log giả (lỗi kết nối GPU hiển thị là lỗi thật). | canary + test hiện có |
| F7 | B | Mẫu bị loại/đã duyệt vẫn truy xuất được qua bộ lọc; tìm kiếm và phân trang đúng sau khi lọc. | canary |
| F8 | B | Reload giữ tab/dữ liệu hợp lý; đổi vai trò sang User không lộ tab admin. | trình duyệt |

## 6. Phân quyền (backend)

| ID | Mức | Tiêu chí | Cách kiểm |
|---|---|---|---|
| P1 | B | Không token → 401; reviewer/user → 403 trên mọi route admin A/B (overview, bulk preview/approve, eligibility, batches GET/detail/items/retry, runs, events, live-settings, export). Ẩn tab FE không thay thế kiểm quyền. | curl/pytest ma trận |
| P2 | B | Admin không sửa được phiếu user qua API duyệt (409/không có route ghi đè). | pytest |

## 7. Hồi quy

| ID | Mức | Tiêu chí | Cách kiểm |
|---|---|---|---|
| X1 | B | `npx vitest run` toàn bộ pass; `vite build` (+`tsc -b`) pass với ba cờ report/file/image. | chạy thật |
| X2 | B | Backend review/reward pytest không giảm so với baseline (78 passed, 3 skipped). | chạy thật |
| X3 | B | Màn Chat/Dịch/Exploit/Báo cáo không đổi (so ảnh trước/sau, computed style header). Không sửa file của người khác đang dirty (`LiveReviewWidget.tsx`, `ReviewAdmin.test.tsx`, `secai-fe-devspace/README.md`). | git diff đối chiếu |

## 8. Ngoài phạm vi lần này (báo riêng, không tính vào ĐẠT/KHÔNG ĐẠT giao diện)

Mẫu chat chưa vào bộ đếm Fine-tune (purpose `production` bị chặn), cooldown tính từ lúc mời và chỉ trong bộ nhớ, danh tính user thật (IAS), provenance model đáp án B, bộ lọc riêng cho ngang nhau/cả hai kém/mâu thuẫn, hoàn tác "Loại", font Barlow chưa nạp (lỗi chung app). Chi tiết: `/tmp/ab-ui/reports/03-requirements-gap.md`.
