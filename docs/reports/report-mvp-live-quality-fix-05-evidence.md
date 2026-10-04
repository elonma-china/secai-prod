# Đo báo cáo MVP — fix-05 (bằng chứng, chờ chấm nội dung độc lập)

Manifest v2 giữ nguyên 12 ca gốc và 6 ca bổ sung, SHA-256 `23b33f114e87513a4f434369d16b3369deab00d83bb9bef01a49471ea59b553f`. Dữ liệu hoàn toàn tổng hợp. Hai product patches gốc `a364097` và `77b9a46`, cherry-pick thực chạy lần lượt `6a5846d` và `0503e79b6a15ba2f087298586b813440739a3ce1`; blob SHA file sản phẩm trong `results.json`. LLM hiện tại `DuyTa/Cyber-F1-AWQ`, endpoint host hash `fbea5c0eff8d`; temperature 0.2, max_tokens 3000, timeout 45 giây, một request model tại một thời điểm.

| Cohort | Generation lưu | Edit lưu | Fresh readback + original retained |
|---|---:|---:|---:|
| original12 | 12/12 | 12/12 | 12/12 |
| supplemental6 | 6/6 | 6/6 | 6/6 |

Tổng 18/18 generation và 18/18 edit, 37 model attempts (`incident-2-vs-3` generation cần retry). Median API generation 1755.9 ms; edit 1872.3 ms. Tất cả 18 ca kể cả lỗi API nếu có đều được tính trong mẫu số; lần này không có HTTP 422 cuối. Nháp bị từ chối của `incident-2-vs-3` có trong `results.json` và [Markdown riêng](/home/ict/secai-report-evidence/report-mvp-quality-fix-05/reports/incident-2-vs-3-rejected-attempt1.raw.md). HTTP 200/validator không phải điểm semantic; chưa kết luận ngưỡng chất lượng 70%.

SQLite `reports.db` + `reports.db.backup` đều quick_check=ok, 3 templates/18 reports/36 revisions; checksum backup `4867b4fa231f23d9cff0bb9987ef5a0f7f4c1fb8726292381a27c10f88607935`.

Bằng chứng bền vững: [index 36 báo cáo/revision và một nháp bị từ chối](/home/ict/secai-report-evidence/report-mvp-quality-fix-05/reports/INDEX.md), [raw attempts/responses](/home/ict/secai-report-evidence/report-mvp-quality-fix-05/results.json), DB `/home/ict/secai-report-evidence/report-mvp-quality-fix-05/reports.db`; bản versioned ở `evals/report_mvp/outputs/report-mvp-quality-fix-05/`. Các run trước giữ nguyên.

Manifest không có ca gần giới hạn context, dữ liệu khách hàng hay hash mẫu. Reviewer độc lập cần chấm generation và edit theo rubric đã đóng băng.
