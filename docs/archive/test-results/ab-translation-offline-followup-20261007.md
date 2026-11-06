# Kiểm chứng offline A/B và dịch — lượt hoàn thiện 2026-10-07

Phân công: Sol chuẩn bị lệnh diễn tập, Luna sửa/chạy test, Astra tạo tiêu chí và review độc lập. Đây là lượt tiếp theo của [đợt sửa độ tin cậy](ab-translation-improvement-20261007.md). Không thay đổi thêm product code, model, corpus thật hay ngưỡng chất lượng.

## Xử lý bảy failure phụ thuộc môi trường

- Các test search/fetch PoC dùng SQLite catalog và artifact vô hại trong `tmp_path`, đi qua `LocalPocBackend` thật. Kiểm đúng nội dung/hash, ref cùng run/khác run, ref mới và giới hạn fetch. Nhánh metadata-only nay luôn chạy, không phụ thuộc snapshot ngoài.
- Test SSE dùng catalog/artifact/ledger tạm, giữ prefetch và route thật; chỉ mock lookup Neo4j ngoài phạm vi. Vẫn yêu cầu `section1` trước `final` và `[DONE]`.
- Test Bughunt kiểm ưu tiên settings/env, đường dẫn script và trạng thái arsenal bằng đường dẫn tạm cùng `shutil.which` được kiểm soát.
- Ba kiểm tra integration thật vẫn được collect, có tên và opt-in riêng: `RUN_BUGBOUNTY_EXTERNAL_SMOKE=1` hoặc `RUN_POC_CATALOG_EXTERNAL_SMOKE=1`. Mặc định skip có lý do. Fixture không được dùng để chứng nhận catalog hay clone ngoài checkout.

Không bỏ assertion để làm xanh. Astra yêu cầu bổ sung so sánh ref giữa hai run và kiểm prerequisite rõ; các thay đổi này đã được thực hiện trước khi chạy full suite.

## Kết quả

| Kiểm chứng | Kết quả |
|---|---|
| PoC/Bughunt/SSE focused | 24 passed, 2 skipped, 31,85s; exit 0 |
| Backend toàn bộ lượt mới | 2.138 passed, 0 failed, 6 skipped, 7 xfailed, 64 warnings; exit 0, 757,36s (12:37) |
| Diễn tập harness offline | Hai lượt exit 0; hash của cả bảy artifact giống nhau |
| Chạy lại cùng thư mục evidence | Exit 1 tại `mkdir`, artifact cũ không đổi |
| External smoke bật nhưng thiếu prerequisite | 3 failed, 0 skipped như kỳ vọng của phép kiểm âm; không phải integration pass |
| Astra source review | APPROVE ba file test và runbook; không sửa policy production |

Focused chỉ gồm hai smoke external; full suite có cả ba: Bughunt, Log4Shell và SSE. Chúng được tính trong 6 skip, không tính là integration pass. Không cộng focused vào tổng số test độc lập. Frontend không đổi ở lượt này nên không chạy lại; kết quả 175 pass/build thành công thuộc đợt trước.

Lệnh full suite đã chạy từ project trong worktree (venv dùng chung nhưng `PYTHONPATH` và đường dẫn test đều trỏ vào worktree):

```sh
POC_CATALOG_DB=/tmp/secai-quality-followup-20261007/full-poc-catalog.db \
PYTHONPATH=$PWD/src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 \
http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 all_proxy=http://127.0.0.1:9 \
NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1 \
RES_OPTIONS='attempts:1 timeout:1' CUDA_VISIBLE_DEVICES='' \
.venv/bin/python -m pytest -q \
  /home/ict/secai-quality-improvement-20261007-wt/secai/secai-agent-gateway/tests
```

Evidence local: `/tmp/secai-quality-followup-20261007/test-verification.md`, `focused-poc-bughunt-sse-final.log`, `full-backend.log`, `rehearsal-verification.md`, `external-guard-verification.md`, `astra-review.md`.

## Diễn tập baseline có thể chạy lại

[Runbook đầy đủ](../reports/translation-offline-rehearsal-20261007.md) chứa lệnh tạo fixture rồi chạy validator, dry plan và scorer hiện có, không cần import helper của test. Giữ minimum 30 của scorer. Hai mẫu synthetic × ba lượt tạo lịch 6 mẫu: một output dựng sẵn chưa chấm, một timeout và bốn output thiếu; quality/release giữ `BLOCKED`, không có file inference.

Hash manifest theo nội dung canonical: `a5d4df08e83d294d48c63f3f2b9a2b5cd138fae7f89a88418b518f728fb295c4`. SHA-256 của script đã kiểm: `3c2aa21b7dec6a7422df43a334d21ed71d5204fde07d7905496e002a4b3addc4`. Đây là bằng chứng tái lập harness, không phải baseline chất lượng bản dịch. [Baseline readiness](../reports/translation-baseline-readiness-20261007.md) liệt kê corpus, model inventory và chấm nghĩa còn thiếu trước đo thật.

## Giới hạn của external smoke

Phép kiểm âm đặt clone/catalog ở đường dẫn scratch thiếu dữ liệu. Cả ba smoke fail khi opt-in. Tuy nhiên `secai_gateway.__init__` import ứng dụng có `app = create_app()`, nên bootstrap có thể tạo catalog SQLite rỗng trước khi test kiểm đường dẫn. Hai catalog smoke khi đó fail vì thiếu dữ liệu/source, không phải thiếu file. Guard trong smoke chỉ chặn việc tạo backend của chính smoke sau import; không chứng minh package import là read-only. Không thay kiến trúc bootstrap trong lượt sửa test này.

Nếu cần dừng trước mọi import khi kiểm snapshot thật, người vận hành kiểm `test -f "$POC_CATALOG_DB"` trước lệnh pytest và dùng cấu hình catalog/nguồn đã chuẩn bị. Không trỏ phép kiểm âm vào dữ liệu thật. Ba external integration, model inference, semantic quality, OCR thực và GPU đều chưa được chứng nhận bằng kết quả offline này.
