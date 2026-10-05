# Qwen3-1.7B — chạy luồng đánh giá đến chấm điểm bằng trọng số thật

Ngày: 2026-10-06. Mã pipeline: commit `5a9a344`. Model chính thức [Qwen/Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B).

## Phạm vi và dữ liệu

**Trọng số model thật, HTTP thật, dataset và nhãn đánh giá synthetic DEMO/QA.** Không có expert dataset thật. Không sửa dữ liệu production hoặc bật scorer cho chatbot. Gateway thử nghiệm dùng router `/review-api` và ReviewStore thật với SQLite riêng; không phải giao diện public Devspace hoặc gateway production.

Revision base model: `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`. Scalar classifier có **1,720,577,024 tham số**. Máy thử: RTX 4090 24 GB, BF16, Transformers 4.57.1, PEFT 0.17.1, PyTorch 2.14.1+cu130. Không thuê GPU. Dùng lock GPU riêng trong suốt training/service.

## Flow đã chạy

1. Tạo campaign `QA DEMO Qwen3-1.7B real HTTP flow 20261006` bằng admin qua HTTP.
2. Import 40 cặp câu trả lời tiếng Việt ATTT tổng hợp, 20 chủ đề × 2 tình huống, source_group theo chủ đề/hội thoại.
3. Phân công reviewer QA, lấy assignment đã shuffle A/B, gửi lựa chọn left/right và nhãn lỗi qua API. Nhãn do script thử nghiệm lựa chọn từ đáp án định sẵn; không phải ý kiến chuyên gia thật.
4. Admin QA duyệt 40 winner; thêm 3 phiếu kiểm soát tie/both_bad/skip và duyệt tương ứng. Đóng campaign.
5. Export raw: **43 phiếu**; preference: **40 cặp**. Kiểm SHA-256 và đối chiếu adjudication. SQLite riêng có **135 audit events**.
6. Chuẩn bị dataset purpose=demo, chia theo nhóm hội thoại: **train 24 / validation 8 / test 8**; test có 4 nhóm độc lập. Không có nhóm hội thoại trùng giữa partition.
7. Huấn luyện scalar head và LoRA riêng trên trọng số Qwen3-1.7B thật; lưu, đọc lại, đánh giá test và niêm phong artifact.
8. Nạp artifact LoRA trong service QA loopback 8198; gateway QA 8199 gọi `/review-api/reward/score` → service `/score` bằng TCP HTTP thật. Không dùng FakeRunner hoặc MockTransport.
9. Kiểm tra thiếu token/reviewer bị từ chối, đảo thứ tự đáp án và câu trả lời giống nhau. Dừng cả hai process test và giải phóng GPU.

## Kết quả model

| Chế độ | Epoch / learning rate | Validation trước → sau | Test demo | Lệnh train | Lệnh evaluate | Peak GPU allocated |
|---|---|---|---|---|---|---|
| head | 4 / 0.003 | 2/8 → 8/8 | 8/8 | 13.99 s | 10.80 s | 3321 MiB |
| lora | 2 / 0.0003 | 2/8 → 8/8 | 8/8 | 11.60 s | 3.49 s | 3706 MiB |

Max swap score delta và equal-content delta: **0** cho cả hai chế độ. Wilson lower bound 95% theo 8 cặp: **0,6756**. Đây là interval theo cặp, không điều chỉnh tương quan trong 4 nhóm hội thoại; không suy ra độ chính xác production. Nhãn tổng hợp cố ý có đáp án sai rõ và có thiên lệch độ dài/phong cách; kết quả 8/8 chỉ chứng minh học/ranking trên fixture nhỏ này.

Thời gian train bao gồm load/save và validation; evaluate bao gồm reload, chấm điểm và hash artifact. Base model được cache khi đo. Không phải benchmark throughput/độ trễ phục vụ khách hàng.

## Kết quả API thật

- HTTP 200 cho **8 / 8** cặp; preferred_index trùng nhãn demo **8 / 8**.
- Median gateway HTTP sau warmup: **103.0 ms**, max **115.0 ms**, 8 request tuần tự, câu ngắn, max_length=384. Bao gồm hop gateway và service trên cùng máy, không gồm tải model. Không cam kết latency CPU hoặc nhiều người dùng.
- Thiếu Bearer: 401; reviewer gọi API admin-only: 403; service trực tiếp thiếu Bearer: 401.
- Đảo thứ tự: preferred_index chuyển 0 → 1; hai câu giống nhau: preferred_index=null, margin=0.

Ví dụ từ tập test:

> Tình huống giả lập 2: Truy cập quản trị từ xa nên có biện pháp gì?

| Đáp án | Điểm reward |
|---|---|
| Giới hạn người và nguồn truy cập, dùng xác thực phù hợp và ghi nhận hoạt động quản trị. | 16.875 |
| Mở giao diện quản trị cho toàn Internet và bỏ xác thực để truy cập nhanh. | -1.4375 |

Kết quả: `preferred_index=0`, `margin=18.3125`. Điểm scalar tương đối, không phải thang 10.

## Chặn production và bằng chứng

- Thử nhận campaign này làm production training: **bị từ chối vì QA/demo**, dù ID reviewer/campaign được cung cấp.
- Artifact head và LoRA đều `eligible=false`: purpose=demo và chưa xác lập release policy. Production verifier từ chối artifact demo.
- Service QA dùng bootstrap riêng, assert purpose=demo, bind 127.0.0.1 và giữ Bearer auth. Không thay CLI/service production hoặc bỏ gate trong mã nguồn.
- Các process thử nghiệm đã dừng; không thay gateway 8131 hoặc Devspace 5173/5179.

Artifact manifest SHA-256:

- head: `5d4f330f8f1b38c05756463674b4a534895d8275ddaaf2c704148e845e4c5c0e`
- lora: `ba071983b592e48e90087427388659a4a907cb8ee6882dd8158805fb684da1e4`

Campaign QA: `32f2979d-307a-4031-9ced-c51013959f2a`. Snapshot, SQLite, ZIP, metrics và artifact nằm trong thư mục private `/tmp/secai-qwen3-real-flow-20261006` (ngoài Git). Token không xuất vào tài liệu hoặc commit.

Các lệnh pipeline gọi thật: `reward.cli prepare` cho demo dataset, `reward.transformer.train` (cùng hàm CLI train), `evaluate_artifact` (cùng hàm CLI evaluate), `verify_artifact`, `load_scorer`, `create_service`. Script QA nằm trong thư mục run, chỉ dùng HTTP cho mọi thao tác đánh giá/admin; không tải dữ liệu production.

Kết luận kỹ thuật: luồng đánh giá → duyệt → export → huấn luyện Qwen3-1.7B → reload → API scoring chạy được. Chưa kiểm chứng đồng thuận với chuyên gia thật, bộ ATTT đại diện, bias/OOD hoặc readiness production.
