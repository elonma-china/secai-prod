# Đo báo cáo MVP — fix-06 (bằng chứng, chờ chấm nội dung độc lập)

Manifest v2 giữ nguyên 12 ca gốc và 6 ca bổ sung, SHA-256 `23b33f114e87513a4f434369d16b3369deab00d83bb9bef01a49471ea59b553f`. Dữ liệu hoàn toàn tổng hợp. Product patch gốc `ab2f45c`, cherry-pick thực chạy `3c53d42312712dd9756687422472d347925e8145`; blob SHA file sản phẩm trong `results.json`. LLM hiện tại `DuyTa/Cyber-F1-AWQ`, endpoint host hash `fbea5c0eff8d`; temperature 0.2, max_tokens 3000, timeout 45 giây, một request model tại một thời điểm.

| Cohort | Generation lưu | Edit lưu | Fresh readback + original retained |
|---|---:|---:|---:|
| original12 | 12/12 | 12/12 | 12/12 |
| supplemental6 | 6/6 | 6/6 | 6/6 |

Tổng 18/18 generation và 18/18 edit, 39 model attempts: generation `compliance-missing`, `compliance-noisy`, `incident-2-vs-3` cần retry. Median API generation 1750.6 ms; edit 1596.0 ms. Tất cả 18 ca kể cả lỗi API nếu có đều được tính trong mẫu số; lần này không có HTTP 422 cuối. Ba nháp bị từ chối có trong `results.json` và Markdown riêng ở index. HTTP 200/validator không phải điểm semantic; chưa kết luận ngưỡng chất lượng 70%.

SQLite `reports.db` + `reports.db.backup` đều quick_check=ok, 3 templates/18 reports/36 revisions; checksum backup `fabbd03be863324b9ef1eb24c1f021ec427ad90e098bda6e5a9049e327639bc1`.

Bằng chứng bền vững: [index 36 báo cáo/revision và ba nháp bị từ chối](/home/ict/secai-report-evidence/report-mvp-quality-fix-06/reports/INDEX.md), [raw attempts/responses](/home/ict/secai-report-evidence/report-mvp-quality-fix-06/results.json), DB `/home/ict/secai-report-evidence/report-mvp-quality-fix-06/reports.db`; bản versioned ở `evals/report_mvp/outputs/report-mvp-quality-fix-06/`. Các run trước giữ nguyên.

Hai báo cáo `scan-cross-host-ip` chứa khoảng trắng cuối dòng do model sinh ra. Bản evidence giữ nguyên byte nội dung đã lưu để đối chiếu; kiểm tra diff cho phần artifact dùng cấu hình bỏ qua duy nhất cảnh báo khoảng trắng cuối dòng.

Manifest không có ca gần giới hạn context, dữ liệu khách hàng hay hash mẫu. Reviewer độc lập cần chấm generation và edit theo rubric đã đóng băng.
