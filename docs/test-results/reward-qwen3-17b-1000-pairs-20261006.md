# Qwen3-1.7B — thử fine-tune với 1.000 cặp QA, tối đa 5 vòng

Ngày: 2026-10-06 theo giờ Việt Nam. Pipeline hiện tại: commit `5a9a344`; không sửa hành vi sản phẩm trong lần thử này.

## Phạm vi và kết luận

Đã chạy trọng số Qwen3-1.7B thật, review/admin qua HTTP thật, train LoRA + scalar head, lưu và reload artifact, rồi kiểm tra API scoring trên 200 cặp test. Đã chạy **1 vòng**, chọn vòng **1** theo validation. Không tối ưu thêm sau khi xem test.

**Dataset là 1.000 cặp tổng hợp từ 50 nhóm tình huống, không phải 1.000 tình huống độc lập hoặc phản hồi chuyên gia thật.** Mỗi nhóm có 20 biến thể: 5 loại cặp × 4 cách hỏi. Prompt cung cấp chính sách nội bộ giả lập; nhãn đúng được script xác định bằng đối chiếu chính sách trong prompt. Không gọi chatbot để sinh câu trả lời hoặc mời chuyên gia gán nhãn. Bài thử đo khả năng xếp hạng câu trả lời theo nguồn cung cấp, chưa đánh giá kiến thức ATTT tổng quát, OOD hay đồng thuận với chuyên gia.

## Flow đã chạy

1. Tạo campaign QA riêng qua `/review-api`.
2. Import 1.000 cặp tiếng Việt bằng 4 request, mỗi request 250 cặp.
3. Phân công reviewer QA, đọc assignment đã shuffle A/B và gửi 1.000 judgments left/right theo đáp án định sẵn. Role reviewer là tài khoản kỹ thuật; không đại diện chuyên gia.
4. Admin QA duyệt 1.000 winner và đóng campaign; SQLite có **3006 audit events**.
5. Export raw/preference qua HTTP; đối chiếu SHA-256, nội dung và approval bằng `prepare_dataset(purpose="demo")`.
6. Tách theo nguyên nhóm: **train 600 / validation 200 / test 200**, tương ứng **30 / 10 / 10 nhóm**. Không có prompt/source_group trùng giữa partition. Mỗi partition cân bằng 5 loại cặp.
7. Dùng model từ bộ 40 cặp trước làm baseline trên validation mới; chạy các candidate mới từ cùng base revision. Chọn theo validation accuracy, dùng log_loss để phân xử khi bằng nhau.
8. Đánh giá và niêm phong artifact được chọn trên test cuối; phân tích theo nhóm lỗi, không dùng kết quả này để chọn lại model.
9. Service QA loopback 8198 nạp trọng số thật; gateway QA 8199 gọi service bằng TCP HTTP. Chấm toàn bộ 200 cặp test, kiểm Bearer, đảo thứ tự và hai câu giống nhau; kiểm gate chặn production.

## Thiết lập cố định trước test

Base model: `Qwen/Qwen3-1.7B`, revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`.

Máy: RTX 4090 24 GB; BF16, max_length=384, LoRA rank=8, alpha=16, target q_proj/v_proj, modules_to_save=score. Batch mỗi bước gồm 2 câu của một cặp, AdamW, clip gradient=1.0. Tokenizer không âm thầm cắt câu. Seed=20261006. Phiên bản: peft 0.17.1, torch 2.14.1, transformers 4.57.1.

Giới hạn **5 candidate** đặt trước: (epochs, learning_rate) = (1, 1e-4), (2, 5e-5), (2, 1e-4), (3, 3e-5), (3, 5e-5). Các candidate bắt đầu từ base model, không tiếp tục học trên candidate trước. Dừng sớm khi validation accuracy ≥85% và mỗi loại cặp ≥75%. Đây là mục tiêu QA thử nghiệm, không phải tiêu chí đủ để triển khai production.

## Kết quả validation và lựa chọn

Baseline là LoRA đã học bộ demo 40 cặp trước, **không phải base model chưa fine-tune**. Baseline trên validation mới: **68/200 = 34.0%**.

| Vòng | Epoch | Learning rate | Validation | Log loss | Nhóm thấp nhất | Train + validation + save | Peak GPU allocated |
|---|---|---|---|---|---|---|---|
| 1 | 1 | 0.0001 | 200/200 (100.0%) | 1.3789e-06 | 100.0% | 118.5 s | 4204 MiB |

Vòng được chọn: **1**. Mục tiêu validation: **đạt**. Thời gian quy trình tối ưu, gồm baseline/load/train/evaluation: **247.4 giây**; không gồm download base, review HTTP hoặc API benchmark.

## Test cuối và API

- Offline evaluator: **200/200 = 100.0%**, ties=0, log_loss=6.65179e-07.
- Inference theo cặp: **200/200**; qua gateway HTTP thật: **200/200**, tất cả HTTP 200. Điểm HTTP khớp dự đoán offline theo cặp.
- Chênh lệch số cặp đúng giữa evaluator chấm từng câu và inference batch hai câu: **0**. BF16 và padding có thể tạo khác biệt số học; không coi điểm scalar là xác suất.
- Max swap/equal score delta: **0.0 / 0.0**. HTTP đảo vị trí trả thứ hạng tương ứng; hai câu giống nhau trả preferred_index=null.
- Median HTTP sau warmup: **103.8 ms**, max **139.8 ms**, 200 request tuần tự cùng máy, prompt/câu ngắn, model đã nạp trên GPU. Không phải latency CPU, throughput tải đồng thời hoặc độ trễ toàn chatbot.
- Thiếu Bearer gateway: 401; reviewer gọi endpoint admin: 403; service thiếu Bearer: 401. Khi dừng scorer, gateway trả 503, không tạo điểm giả.

| Loại cặp | Baseline validation | Selected validation | Final test |
|---|---|---|---|
| Câu tương đương độ dài, khác nội dung | 28/40 | 40/40 | 40/40 |
| Câu đúng dài hơn | 40/40 | 40/40 | 40/40 |
| Câu sai không trả lời trọng tâm | 0/40 | 40/40 | 40/40 |
| Câu sai tự khẳng định đã kiểm tra hồ sơ | 0/40 | 40/40 | 40/40 |
| Câu sai dài hơn | 0/40 | 40/40 | 40/40 |

Wilson lower bound 95% theo 200 cặp: **0.9812**, chưa điều chỉnh tương quan. Test có 10 nhóm; bootstrap theo nhóm (10.000 lần, seed cố định) bị suy biến vì cả 10 nhóm đều đúng. Không dùng bootstrap này để suy ra chất lượng production. Corpus giả lập có các mẫu câu lặp lại; các chỉ số không chứng minh chất lượng trên người dùng thật.

Ví dụ test:

> Chỉ căn cứ thông tin được cung cấp sau đây. Hồ sơ giả lập của Tệp nghi có mã độc: cách gửi phân tích được quy định là kênh cách ly được duyệt. Trạng thái hồ sơ: đang hiệu lực. Hồ sơ dùng cho kiểm thử, không mô tả tổ chức thật.
Câu hỏi: Với Tệp nghi có mã độc, cách gửi phân tích là gì?

- Chosen: Theo hồ sơ, cách gửi phân tích của Tệp nghi có mã độc là kênh cách ly được duyệt. Điểm **5.8125**.
- Rejected: Theo hồ sơ, cách gửi phân tích của Tệp nghi có mã độc là email quảng bá chung. Điểm **-10.125**.

Điểm reward là scalar tương đối, không phải thang 10.

Lỗi ranking theo cặp: 0.

Không có lỗi ranking trong các dự đoán theo cặp của tập test QA này.

## Gate và bằng chứng

- Dataset production bị chặn: `QA/demo campaigns cannot train a production model`.
- Artifact `purpose=demo`, `eligible=false`; production verifier từ chối. Bootstrap QA bind loopback và giữ Bearer auth, không sửa gate/service CLI production.
- Không bật scoring tự động cho chatbot hoặc đổi gateway production/Devspace. Hai process QA đã dừng và GPU đã được giải phóng sau kiểm thử.
- Campaign QA: `93075b6a-1efb-4cb4-bc6e-b261a0a7cdab`.
- Artifact manifest SHA-256: `6cd4bd3ff85524279e26f7df6792543d5cb19daf4628cb5085679221410335f9`.
- Dataset manifest SHA-256: `35a15944514ae682e33d3b78f4cabab693d5621c5b7478ff2d1369cd791807b4`.
- Kế hoạch trước khi đánh giá SHA-256: `20abf0f9007378be21fb7f080d91db748ee36a618aa80c01358ff6cb0e806481`.

Bằng chứng private ngoài Git: `/tmp/secai-qwen3-opt1000-20261006`, gồm `review-flow.json`, export ZIP, dataset/manifest, `experiment-plan.json`, `optimization-results.json`, `final-test-predictions.json`, `http-results.json`, `verified-summary.json` và artifact được chọn. Runtime token, SQLite, dữ liệu QA và trọng số không commit. Các file trong /tmp là bằng chứng tạm thời, không bảo đảm lưu lâu dài.

Các script tái hiện nằm trong thư mục run: `setup.py`, `review_1000.py`, `optimize.py`, `demo_service.py`, `score_http.py`, `finish_checks.py`. Dùng env `/tmp/secai-reward-qwen-env`, PYTHONPATH trỏ `src`, POC/GATEWAY DB riêng và `flock -n /tmp/secai-reward-local-gpu-0.lock` khi train/serve. `optimize.py` gọi các hàm production pipeline, không giả lập đầu ra model. Các script tạo run mới trước khi chạy lại; không ghi đè artifact hoặc kết quả test đã niêm phong.
