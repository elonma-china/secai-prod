# Báo cáo đo CCU — dịch tệp/ảnh (API dịch v2) — 11/10/2026

## 0. Giải thích thuật ngữ
- **CCU (Concurrent Users):** số người dùng ảo (VU) gửi yêu cầu dịch đồng thời, mỗi VU lặp: tải 1 tệp → chờ dịch xong → nghỉ ngẫu nhiên 5-20 s → tải tệp tiếp.
- **p95 (percentile 95):** sắp xếp thời gian xử lý mọi tài liệu từ nhanh đến chậm, lấy giá trị ở vị trí 95% — tức 95% tài liệu xử lý nhanh hơn hoặc bằng con số này, chỉ 5% chậm hơn. Dùng p95 thay vì trung bình vì phản ánh đúng trải nghiệm xấu nhất mà đa số người dùng còn gặp, không bị số ít tài liệu quá nhanh làm đẹp số liệu.
- **Tài liệu ngắn / dài** (định nghĩa trong `ccu_translate.py`, `PAGES = {"short": 1, "long": 4}`): ngắn ≈ 1 trang, dài ≈ 4 trang. Mỗi lần đo chọn ngẫu nhiên khoảng 20% tài liệu là loại dài, 80% còn lại là ngắn.
- **p50:** tương tự p95 nhưng lấy giá trị ở vị trí giữa (trung vị) — đại diện cho trường hợp "bình thường".
- **Thời gian xếp hàng (queue):** thời gian tệp nằm chờ trước khi hệ thống bắt đầu xử lý, tính riêng với thời gian xử lý thật.

## 1. Script CCU có sẵn trong dự án
| Nơi | Đo gì | Cách đo | Ngưỡng / kết quả |
|---|---|---|---|
| `kpi_bench/scripts/perf_kpi.py` (mục B, `--concurrency`), registry `kpi_bench/01-KPI/kpi-registry.json` mã `R-PERF-CCU` | CCU chat `/ai/chat` | N request đồng thời; registry ghi CCU là tham số đầu vào (30, `--trial` 15), chỉ ràng buộc thật "không request nào lỗi" | >= 30 CCU, ~0,6 RPS |
| `kpi_eval_v2/performance/load_ramp.py` (`KPI2_LOAD=1` mới chạy full) | Trần CCU thật của LLM thô | Ramp 1,2,4,…60 luồng, dừng khi lỗi >2% hoặc p95 > 8 s; trần = mức sạch cuối | >= 30 CCU, >= 0,6 req/s |
| `docs/nghiem-thu/benchmark-3xA100-2026-09-20/locustfile_30ccu.py` (+ `load_30ccu_*_report.html`, `scripts/bench_ccu_gpus.sh`) | 30 CCU qua `/ai/chat` | Locust `-u 30 -r 6 -t 90s`, think time 0 (stress, không nghỉ) | 3×A100, báo cáo HTML cùng thư mục |
| `module-map/translate/fixtures/tools/loadtest.py` (03 mục 1e) | Dịch tệp: N tệp gửi cùng lúc (barrier) | 1 vòng/N luồng, không think time | 20 tệp: p95 <= 60 s, >= 25 tài liệu/phút; 0 5xx; succeeded >= 95% |

Dự án chưa có KPI CCU riêng cho dịch tệp/ảnh; không có script CCU theo kiểu "người dùng ảo có think time" nên viết mới `fixtures/tools/ccu_translate.py` theo mô hình đề bài (ramp-up 30 s, think time 5-20 s). KPI CCU có sẵn (>= 30) là của chat, không so trực tiếp được.

## 2. Phương pháp
- Stack thử cô lập `http://127.0.0.1:18285` (stack_exp.sh: MAX_RUNNING_JOBS=6, ACTIVE_PER_IP=500, QUEUED=500, rate 100000). Gateway lấy peer = `request.client.host` (routes.py), không đọc X-Forwarded-For => không giả lập nhiều IP được; mọi VU cùng 127.0.0.1, nên dùng stack đã nới giới hạn per-IP.
- Mỗi VU: tải 1 tệp → poll 1 s tới xong → nghỉ ngẫu nhiên 5-20 s → lặp. Ramp 30 s, chạy 240 s, sau đó chờ job dở dang tối đa 100 s (còn dở = timeout = thất bại). Tổng mỗi mức <= ~6 min. Bao `flock /tmp/translate-gpu-test.lock`.
- Trộn: 70% chữ (docx/xlsx/pdf), 30% OCR (pdf-scan/png/jpg); 20% dài; ngôn ngữ nguồn en (40%) + de/es/fr/ru/ja/zh-Hans; seed cố định.
- Tiêu chí ĐẠT: 5xx/đứt/poll_error/timeout = 0; succeeded >= 99%; p95 tổng tệp ngắn <= 30 s, dài <= 120 s.
- Lưu ý nhiễu: lượt đầu (5, 40, 80 VU) trùng lúc proxy SSH Vast đứt, sinh OCR_UNAVAILABLE/TRANSLATION_UNAVAILABLE (5 VU: 1 lỗi; 40 VU: 42 lỗi; 80 VU: 87 lỗi) => ĐÁNH DẤU "nhiễu do đứt tunnel", lưu ở `ccu/noisy/`, và ĐO LẠI cả ba sau khi coordinator nối lại tunnel. Bảng dưới là số liệu đo lại (10, 20 không có lỗi hạ tầng nên giữ nguyên; 30 đo thêm sau cùng).

## 3. Kết quả từng mức (JSON/log: `optimization/results/ccu/`)
| VU | Chu trình | Nhận/từ chối | Succeeded | p50/p95 ngắn (s) | p50/p95 dài (s) | Xếp hàng p50/p95 (s) | Tài liệu/phút | Trang/phút | 5xx/timeout | Peak job dở | Kết luận |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 61 | 61/0 | 61 (100%) | 4,5 / 13,9 | 6,6 / 32,7 | 1,2 / 11,7 | 14,5 | 18,8 | 0 | 5 | ĐẠT |
| 10 | 130 | 130/0 | 130 (100%) | 4,5 / 11,2 | 5,7 / 14,5 | 1,1 / 1,8 | 30,7 | 47,7 | 0 | 8 | ĐẠT |
| 20 | 209 | 209/0 | 209 (100%) | 9,9 / 18,8 | 11,5 / 20,8 | 3,8 / 8,9 | 48,5 | 73,6 | 0 | 15 | ĐẠT |
| 30 | 171 | 171/0 | 171 (100%) | 31,9 / 41,5 | 33,0 / 43,1 | 24,0 / 30,4 | 38,4 | 60,6 | 0 | 26 | KHÔNG (p95 ngắn > 30 s) |
| 40 | 172 | 172/0 | 172 (100%) | 52,8 / 66,0 | 55,8 / 66,4 | 44,4 / 53,7 | 36,5 | 57,5 | 0 | 36 | KHÔNG (p95 ngắn 66 s) |
| 80 | 159 | 159/0 | 128 (80,5%) | 166 / 215 | 136 / 224 | 155 / 201 | 22,5 | 35,6 | 31 timeout | 78 | GÃY |

Ghi chú: mức 5 VU có 1 job xếp hàng 32 s (p95 dài 32,7 s) ở lượt đo lại — outlier đơn lẻ, không lặp lại ở lượt đầu (p95 8,5 s); thông lượng thấp ở 5 VU do ít người dùng, không phải giới hạn hệ thống. Ở 30 VU thông lượng thấp hơn 20 VU (38 vs 48/phút) vì mẫu ngẫu nhiên có tỉ lệ OCR/dài khác nhau và hệ thống đã bão hòa.

## 4. Kết luận
- **CCU tối đa đạt tiêu chí: 20 người dùng đồng thời** (p95 ngắn 18,8 s, dài 20,8 s, 0 lỗi, 100% thành công, ~48 tài liệu/phút). Mức 30 vẫn 0 lỗi và 100% thành công, chỉ vượt ngưỡng p95 tệp ngắn (41 s > 30 s); tài liệu dài vẫn trong ngưỡng 120 s => nếu nới ngưỡng ngắn lên 45 s thì chịu được ~30 CCU.
- **Điểm gãy:** độ trễ tăng tuyến tính khi vượt ~20-25 VU (bắt đầu xếp hàng 24 s ở 30 VU). Chưa thấy 429, chưa có 5xx ở bất kỳ mức nào (kể cả 80 VU). Gãy thật ở 80 VU: xếp hàng p50 155 s, 31/159 job không xong trong thời gian drain (timeout phía công cụ, job vẫn nằm trong hàng đợi, không phải lỗi dịch vụ), thông lượng giảm còn 22,5 tài liệu/phút.
- **Nút thắt:** thông lượng bão hòa ~36-48 tài liệu/phút (~57-74 trang/phút), khớp T48 (8 job: ~38-43 docs/phút) => giới hạn là OCR/dịch dùng chung 1 GPU (specialist qua tunnel Vast, khóa admission/nạp tuần tự), không phải gateway/hàng đợi (xếp hàng chiếm ~85% thời gian tổng ở 40 VU; chạy thực tế chỉ ~8-10 s). Thêm worker/`MAX_RUNNING_JOBS` không giúp quá mức bão hòa GPU.
- Tunnel Vast là điểm lỗi đơn: khi đứt, job fail OCR_UNAVAILABLE/TRANSLATION_UNAVAILABLE (xem phần nhiễu).
- So với KPI sẵn có: KPI CCU dự án (>= 30) áp cho chat (LLM, 3×A100) — chức năng dịch tệp nặng hơn nhiều mỗi yêu cầu nên 30 CCU cùng hạn mức p95 30 s chưa đạt (20 CCU đạt). Với tham chiếu 03-1e (20 tệp đồng thời: p95 <= 60 s, >= 25 tài liệu/phút) hệ thống đạt dư.

## 5. Khuyến nghị
1. Công bố năng lực dịch tệp/ảnh: ~20 CCU (think time 5-20 s) với SLA 30 s/120 s; ~30 CCU nếu SLA ngắn nới 45 s.
2. Muốn >30 CCU: tăng năng lực OCR/MT (thêm GPU/replica specialist hoặc GPU phụ) — hiện cấu hình không tự tăng replica.
3. Production: giữ `MAX_QUEUED_JOBS` đủ lớn và trả hàng đợi/ETA cho người dùng; ở quá tải hệ thống chỉ chậm chứ không lỗi, nhưng trên ~40 VU thời gian chờ > 1 phút.
4. Giám sát tunnel/specialist (health + cảnh báo) vì đứt tunnel gây lỗi hàng loạt; giữ thời gian job deadline đủ dài.
5. Đo thêm bằng nhiều IP thật qua nginx để kiểm giới hạn per-IP production (chưa giả lập được ở đây).
