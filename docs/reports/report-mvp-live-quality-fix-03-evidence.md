# Đo lại báo cáo MVP — fix-03 (bằng chứng, chờ chấm nội dung độc lập)

Manifest v2 giữ nguyên 12 ca gốc và 6 ca bổ sung, SHA-256 `23b33f114e87513a4f434369d16b3369deab00d83bb9bef01a49471ea59b553f`. Dữ liệu hoàn toàn tổng hợp. Product patch gốc `2b2b3e8`, cherry-pick thực chạy `96a9c16e26c049cb90c80470ca0e30fd8b354b37`; blob SHA file sản phẩm trong `results.json`. LLM hiện tại `DuyTa/Cyber-F1-AWQ`, endpoint host hash `fbea5c0eff8d`; temperature 0.2, max_tokens 3000, timeout 45 giây, một request model tại một thời điểm.

| Cohort | Generation lưu | Edit lưu | Fresh readback + original retained |
|---|---:|---:|---:|
| original12 | 12/12 | 12/12 | 12/12 |
| supplemental6 | 6/6 | 6/6 | 6/6 |

Tổng 18/18 generation và 18/18 edit, 37 model attempts (một edit cần retry). Median API generation 1882.8 ms; edit 1799.6 ms. HTTP 200/validator không phải điểm semantic; chưa kết luận ngưỡng chất lượng 70%.

SQLite `reports.db` + `reports.db.backup` đều quick_check=ok, 3 templates/18 reports/36 revisions; checksum backup `40f70aef23dce01f3e9f724a67d7fefa1022bda3d20719ace6ed8e5f7e092e8f`.

Bằng chứng bền vững: [index 36 báo cáo/revision](/home/ict/secai-report-evidence/report-mvp-quality-fix-03/reports/INDEX.md), [raw attempts/responses](/home/ict/secai-report-evidence/report-mvp-quality-fix-03/results.json), DB `/home/ict/secai-report-evidence/report-mvp-quality-fix-03/reports.db`; bản versioned ở `evals/report_mvp/outputs/report-mvp-quality-fix-03/`. Các run gốc, fix-01 và fix-02 vẫn giữ nguyên.

Manifest không có ca gần giới hạn context, dữ liệu khách hàng hay hash mẫu. Reviewer độc lập cần chấm nội dung generation và edit theo rubric đã đóng băng.
