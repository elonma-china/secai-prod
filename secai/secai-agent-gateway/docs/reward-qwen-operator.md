# Pipeline reward Qwen3-1.7B

Luồng đã triển khai trong mã nguồn: export chuyên gia/admin → kiểm tra dữ liệu → chia train/validation/test → huấn luyện đầu điểm số Qwen3 → đánh giá tập test → artifact có checksum → dịch vụ chấm điểm riêng → API gateway.

Chat tiếp tục dùng `REVIEW_LIVE_SAMPLE_RATE=0.1`, `REVIEW_LIVE_COOLDOWN_SECONDS=600`. Các biến này lấy mẫu xác suất, không bảo đảm mỗi 10 câu có đúng một lượt A/B. Cooldown hiện tính theo review actor và bắt đầu khi tạo lượt A/B. Nếu nhiều khách dùng chung một reviewer token, họ dùng chung cooldown; cần danh tính review riêng nếu muốn cooldown riêng mỗi khách.

Pipeline không tự huấn luyện khi đạt số lượng lượt hỏi. Một đợt huấn luyện được operator khởi chạy; không tự đổi model và không tự sửa đáp án đang trả khách. Chưa có expert dataset thật: chưa có artifact Qwen3 đủ điều kiện chạy sản phẩm.

Devspace hiện có nút khởi chạy **job QA thật trên Vast**, với trạng thái và kết quả lưu riêng. API này chỉ nhận campaign QA/demo, không thay luồng production bên dưới và không kích hoạt artifact cho chatbot. Xem [deployment job](../deploy/reward-training/README.md) và [kiểm thử 1.000 cặp trên Vast](test-results/reward-vast-devspace-training-20261006.md).

## 1. Thu thập và duyệt

Dùng quy trình `/review-api` hiện có: campaign, import/live pair, assignment, judgment, admin adjudication. Chỉ expert review được phân công bằng danh tính reviewer tin cậy và kết quả admin đã duyệt mới dùng cho dataset này. Token của người dùng thu feedback và token chuyên gia cần được phân biệt trong vận hành; hiện vai trò API chỉ có reviewer/admin, không có cơ chế tự xác minh chuyên môn. Không xem mọi lựa chọn của khách là nhãn chuyên gia.

Xuất cả `format=preference` và `format=raw` của campaign đã đóng/không còn thay đổi. Nếu hai export không khớp bản ghi đã duyệt, xuất lại. Preference dùng tên trường `prompt`, `chosen`, `rejected`, `item_id`, `adjudication_id`, `source_group`, `rubric_version`. Chỉ winner được xuất vào preference; tie/both_bad/skip/insufficient_context không ép thành cặp thắng/thua.

Manifest mới chứa `campaign_title` để loại QA/demo. Export cũ thiếu trường này cần xuất lại. Kiểm checksum không thay thế việc lấy export từ nguồn admin tin cậy. Không đưa ZIP dữ liệu thật hoặc token vào Git.

## 2. Chuẩn bị dataset

Chạy bằng môi trường gateway, không cần PyTorch:

```sh
.venv/bin/python -m secai_gateway.reward.cli prepare \
  --exports /private/campaign-preference.zip /private/campaign-raw.zip \
  --purpose production --expert-id <trusted-expert-actor-id> \
  --campaign-id <eligible-campaign-id> \
  --output /private/reward-dataset-v1
```

Có thể lặp `--exports <preference.zip> <raw.zip>` để gộp campaign. Không gộp các rubric mâu thuẫn; operator chọn bộ tiêu chí nhất quán trước khi chuẩn bị.

Production bắt buộc khai báo `--expert-id` (có thể lặp nhiều lần). Mỗi cặp phải có ít nhất một judgment left/right chọn đúng winner được admin duyệt của reviewer trong danh sách này; chỉ phản hồi khách đã được admin duyệt vẫn chưa đủ. Production cũng bắt buộc `--campaign-id` để chọn tường minh campaign dữ liệu thật; tên campaign không tự chứng minh dữ liệu thật. Hai danh sách được lưu trong dataset manifest. Operator chịu trách nhiệm xác định danh tính chuyên gia tin cậy, không đưa token khách vào danh sách.

Lệnh xác minh manifest SHA-256, approval, nội dung chosen/rejected và loại QA/demo; loại cặp trùng, giữ liên kết tới mọi hội thoại nguồn. Production bắt buộc source_group là ID hội thoại ổn định; không nhận unknown/none/null. Nhóm prompt/hội thoại liên thông được giữ trong cùng partition. Chia xấp xỉ 60/20/20 theo số nhóm; cần ít nhất ba nhóm độc lập. Dataset lưu JSONL từng partition và manifest. Không tự ghi đè thư mục đã tồn tại.

Để đặt ngưỡng phát hành, dùng `--policy /private/release-policy.json` lúc prepare. Policy phải được xác lập trước khi xem kết quả test. Các trường bắt buộc: `min_test_records` (số nguyên dương), `min_accuracy` (>0.5, ≤1), `min_wilson_lower_95` (>0.5, ≤min_accuracy), `max_invariance_delta` (0..0.01). Không có ngưỡng mặc định do hiện chưa biết quy mô dataset thật. Thiếu policy thì vẫn train/evaluate được, nhưng artifact không đủ điều kiện serving.

Dữ liệu giả lập chỉ dùng `--purpose demo`, bị chặn khi phục vụ sản phẩm.

## 3. Huấn luyện offline

Tạo môi trường riêng, không cài extra reward vào container gateway CPU:

```sh
python3 -m venv /private/reward-env
/private/reward-env/bin/python -m pip install -e '.[reward]'
```

Transformers 4.57.1, tokenizers 0.22.1, PEFT 0.17.1 được pin; PyTorch dùng khoảng phiên bản hỗ trợ và phiên bản thực được ghi vào metadata. Xác định GPU/CPU và dependency phù hợp máy training trước khi chạy thật.

Dùng commit SHA 40 ký tự của `Qwen/Qwen3-1.7B`, không dùng `main`. Trọng số cần có trong cache; mặc định không tải mạng. Chỉ thêm `--allow-download` khi chủ động muốn tải model.

```sh
/private/reward-env/bin/python -m secai_gateway.reward.cli train \
  --dataset /private/reward-dataset-v1 --output /private/reward-model-v1 \
  --revision <immutable-model-commit> --mode lora --device cuda \
  --epochs 1 --learning-rate 0.0001 --max-length 2048
```

`--mode head` chỉ huấn luyện scalar head, giữ backbone; `--mode lora` huấn luyện adapter q_proj/v_proj và lưu cả score head. Pairwise Bradley–Terry loss khuyến khích score(chosen)>score(rejected). Không sinh văn bản trong quá trình chấm điểm. Training dùng train; báo cáo validation dùng validation. Tập test chưa được chấm ở bước này. Không cắt câu trả lời âm thầm khi vượt token limit; cần thay giới hạn hoặc chuẩn bị lại dữ liệu.

Runner hiện dùng một cặp mỗi bước, phù hợp pilot. Chưa có gradient accumulation, distributed training hoặc tự chọn hyperparameter; đo tài nguyên thực trước khi mở rộng.

## 4. Đánh giá và quyết định dùng artifact

```sh
/private/reward-env/bin/python -m secai_gateway.reward.cli evaluate \
  --dataset /private/reward-dataset-v1 --artifact /private/reward-model-v1 --device cuda
```

Lệnh đối chiếu dataset manifest với training, chấm test, ghi số mẫu, pairwise accuracy, Wilson lower bound 95%, tie count, log loss và kiểm tra đảo thứ tự/đáp án giống nhau. Ghi `evaluation.json`, niêm phong toàn bộ file bằng `artifact.json`, in checksum SHA-256 của manifest artifact. Không ghi đè đánh giá đã tồn tại.

Trước khi chọn artifact cho sản phẩm, chuyên gia cần kiểm thêm các tình huống ATTT: thông tin sai nhưng trôi chảy, CVE bịa, thiếu chứng cứ, độ dài, ngoài miền và nội dung không an toàn. Gate số liệu là điều kiện cần; không tự thay thế kiểm tra chuyên môn này. Tránh chọn cấu hình theo test rồi báo test đó là đánh giá độc lập; dùng validation để chọn, test cuối để xác nhận.

Điểm là scalar ưu tiên tương đối, không phải thang 0–10 hoặc xác suất đúng. Không so điểm giữa hai artifact khác nhau để suy ra chất lượng trực tiếp.

## 5. Dịch vụ riêng và gateway

Chỉ artifact production vượt gate mới được service CLI nhận. Tạo token service private; file token-hash chứa SHA-256 của token, giữ quyền truy cập riêng. Không truyền token thật trên dòng lệnh.

```sh
/private/reward-env/bin/python -m secai_gateway.reward.service \
  --artifact /private/reward-model-v1 --sha256 <artifact-manifest-sha256> \
  --token-hash-file /private/reward-service-token.sha256 \
  --device cuda --host 127.0.0.1 --port 8198
```

Service `/score` dùng Bearer riêng, chỉ đọc model local; không tự tải base LoRA còn thiếu. Checksum/metadata được kiểm trước load. Mỗi worker chỉ chạy một inference tại một thời điểm, không chặn event loop. Không mở ra Internet trực tiếp; dùng mạng private/TLS khi service nằm trên máy khác. Cần triển khai một worker cho một thiết bị đã được phân bổ. Rollback bằng artifact/path/checksum trước đó.

Gateway giữ model ở ngoài, cấu hình:

```text
REWARD_ENABLED=true
REWARD_SERVICE_URL=http://127.0.0.1:8198
REWARD_SERVICE_TOKEN=<private-service-token>
REWARD_ARTIFACT_SHA256=<artifact-manifest-sha256>
REWARD_TIMEOUT_SECONDS=30
```

`POST /review-api/reward/score` yêu cầu Bearer **admin**, body:

```json
{"prompt":[{"role":"user","content":"MFA là gì?"}],"answers":["Câu A","Câu B"]}
```

Trả `artifact_id`, `scores`, `preferred_index` (0-based, null khi hòa/đơn đáp án), `margin`. Tối đa 4 đáp án; chỉ user/assistant trong prompt. Auth kiểm trước parse body, giới hạn body 256 KiB. Service lỗi hoặc artifact sai trả 503, không tạo điểm giả.

**Mặc định `REWARD_ENABLED=false`.** API chỉ hỗ trợ chấm điểm quản trị; chưa nối vào luồng chat để tự động sinh thêm đáp án hoặc chọn đáp án cho khách. Bước đó cần đánh giá chất lượng/độ trễ model thật và cấu hình phạm vi lượt chat riêng.

## Kiểm chứng kỹ thuật

Các test dùng dataset giả lập và Qwen3 rất nhỏ khởi tạo ngẫu nhiên tại chỗ, không tải Qwen3-1.7B. Chúng kiểm tra data validation, partition leakage, pairwise gradient, head/LoRA save/load, sealed evaluation, checksum, auth và relay. Đây là bằng chứng pipeline kỹ thuật, không phải benchmark tiếng Việt ATTT hay latency model 1.7B.

Nguồn: [Qwen3 model card](https://huggingface.co/Qwen/Qwen3-1.7B), [Qwen3 architecture](https://huggingface.co/docs/transformers/model_doc/qwen3), [Reward modeling loss](https://huggingface.co/docs/trl/reward_trainer).
