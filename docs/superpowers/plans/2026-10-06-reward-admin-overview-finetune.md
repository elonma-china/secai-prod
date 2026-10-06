# Reward Admin Overview and Real Fine-tune Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking. Không tự phân công agent nếu người dùng chưa yêu cầu.

**Goal:** Một trang A/B cho admin quản lý phản hồi bằng bảng, duyệt dữ liệu sang hàng chờ, chủ động chạy fine-tune đủ điều kiện trên Vast và xem tiến trình/log thật.

**Architecture:** Tái sử dụng SQLite review và pipeline Qwen3-1.7B/Vast hiện có. Gateway quản lý phê duyệt, batch và snapshot; worker GPU giữ artifact/telemetry; gateway chuyển trạng thái và sự kiện đã lọc cho UI qua polling có cursor. Auth IAS thật là cổng trước khi mở luồng production; giữ demo tách biệt.

**Tech Stack:** FastAPI/Pydantic v2, SQLite, React/TypeScript/Vite, PyTorch/PEFT LoRA, HTTP private/SSH Vast, e2e/Chromium.

**Spec:** [Thiết kế](../specs/2026-10-06-reward-admin-overview.md).

**Trạng thái:** Đã triển khai và nghiệm thu QA trên Devspace/Vast ngày2026-10-06; xem `docs/test-results/reward-admin-batches-20261006.md`. IAS production identity/policy vẫn chờ tích hợp theo phạm vi đã chốt. Ô chỉnh tỷ lệ A/B toàn hệ thống đã triển khai và có nghiệm thu riêng; không nằm trong thay đổi UI/batch đang đề xuất.

## Global Constraints

- Chỉ admin xem overview, duyệt, chỉnh policy và bắt đầu job; auth trước body. User chỉ gửi lựa chọn của mình.
- Không thêm DB riêng. Dùng `REVIEW_DB_PATH` và volume hiện hữu, migration additive và backup nhất quán.
- Không dùng demo QA làm dữ liệu production. Không đổi số liệu benchmark để làm gate đạt.
- Không chạy GPU trong gateway CPU; giữ shared GPU lock, memory budget, ownership và cleanup của Vast.
- Không tự kích hoạt model hoặc tự fine-tune chatbot chính.
- Không bỏ cooldown; tỷ lệ mời và số lượng đủ huấn luyện là hai cấu hình khác nhau.
- Chỉ hiển thị số liệu worker thật; không giả log, loss, epoch, phần trăm hay ETA.

## Review Focus

1. Hai admin duyệt/bắt đầu cùng lúc: một quyết định hợp lệ và một batch/job, không reserve trùng.
2. Mất mạng sau gửi job: reconnect theo request ID, không chạy GPU lần hai.
3. User nhiều phiếu hoặc thay vị trí A/B: đếm một item chuẩn, lưu canonical candidate ID, không đếm số click.
4. Worker restart/OOM/cleanup lỗi: giữ lịch sử, trạng thái đúng, không báo thành công hoặc đánh dấu dùng dữ liệu nhầm.
5. Nhiều vòng huấn luyện: giữ holdout ổn định, không chuyển test cũ vào train hoặc trộn QA/production.

## Màn hình và luồng — bản tối giản theo yêu cầu mới nhất

Có đúng hai tab chính: **Phản hồi** và **Fine-tune**. Không thêm dashboard nhiều chỉ số, không đưa user ID/campaign/recipe/hash vào bảng chính. Admin duyệt từng câu hoặc dùng Duyệt toàn bộ với snapshot confirmation; chỉ tự lấy canonical user winner nhất quán, còn mẫu mơ hồ chờ duyệt riêng.

### Tab 1 — Phản hồi

Mặc định chỉ hiển thị mẫu chờ admin duyệt; ở đầu có số lượng Chờ duyệt, ô tìm kiếm và bộ lọc thời gian tùy chọn. Đã loại/đã xử lý có thể xem qua bộ lọc thu gọn.

| Cột | Nội dung |
|---|---|
| Câu hỏi | Preview một–hai dòng, bấm xem đầy đủ |
| User chọn | A/B hoặc ngang nhau/cả hai kém/bỏ qua; nhiều phiếu chỉ hiện số phiếu |
| Thời gian | Thời điểm nộp |
| Thao tác | Xem và duyệt; Loại |

Bấm Xem và duyệt mở panel: câu hỏi, hai đáp án và dấu User đã chọn; admin chọn đáp án dùng huấn luyện rồi xác nhận. Không duyệt mù bằng một click trên dòng. Nhận xét tùy chọn. User ID, nguồn dữ liệu, lịch sử và thông số kỹ thuật chỉ ở phần Chi tiết thu gọn.

Sau khi duyệt thành công, mẫu biến mất khỏi hàng chờ tab Phản hồi và xuất hiện ngay ở tab Fine-tune. Đây là chuyển góc nhìn/trạng thái, không chuyển sang DB khác hoặc xóa bản ghi gốc. Phiếu user giữ nguyên khi admin chọn đáp án khác.

### Tab 2 — Fine-tune

Phần mặc định: `mẫu đã duyệt đang chờ / ngưỡng`, danh sách câu hỏi + đáp án admin đã chọn, nút **Fine-tune**. Đề xuất ngưỡng 1.000 cặp mới; cần chốt, và vẫn kiểm nhóm/split/purpose trước khi bật nút. Lý do chưa đủ điều kiện hiển thị ngắn ngay cạnh nút.

Admin nhấn Fine-tune → xem xác nhận số mẫu/model/purpose → server chốt mẫu hợp lệ đang chờ thành **một batch cố định** → tạo training attempt và gửi job Vast. Mẫu được duyệt sau lúc chốt nằm ở hàng chờ cho batch sau, không chèn vào batch đang chạy.

Trong tab này có nút **Lịch sử fine-tune**. Mỗi dòng lịch sử chỉ cần: ngày chạy, mã batch ngắn, số mẫu, trạng thái, Xem. Chi tiết batch cho xem:

- Danh sách chính xác các sample/câu hỏi, lựa chọn user và quyết định admin tại lúc chốt.
- Snapshot/hash/model revision ở phần kỹ thuật; không sửa nội dung batch đã chốt.
- Từng lần chạy/retry, người bắt đầu, log, tiến trình và kết quả thật.

Khi đang chạy, mở job drawer hiển thị giai đoạn, optimizer step/tổng step, epoch và loss từ worker. Log thu gọn theo yêu cầu xem. Reload/reconnect giữ đúng job. Nếu lỗi, giữ batch và attempt thất bại trong lịch sử; retry tạo attempt mới cho cùng batch, không nhân đôi nhóm mẫu.

Luồng: **User chọn → Chờ duyệt → Admin xác nhận → Chờ fine-tune → Chốt batch → Huấn luyện → Lịch sử batch/kết quả**. Không tự chạy khi đủ số lượng, không tự bật model sau Hoàn tất.

## Trạng thái và dữ liệu

Giữ `items`, `judgments`, `adjudications`, `audit_events`. Bổ sung bằng migration trong cùng DB:

- Metadata actor/source: user tham chiếu danh tính IAS, source `live/qa/import`, purpose rõ ràng; không lấy role từ body/browser.
- `training_policy`: ngưỡng cặp mới và policy/version cho từng purpose. Server xác thực policy hợp lệ.
- `training_batches`: ID/request key, người chốt/thời điểm, purpose, policy version, immutable dataset hash và tổng mẫu.
- `training_batch_items`: batch ID/item ID, quyết định duyệt, candidate IDs, group và snapshot hash để xem lại chính xác mẫu.
- `training_runs`: ID attempt, batch ID, remote job ID, model revision, trạng thái, timestamps, kết quả; một batch có nhiều attempt khi retry.
- Reservation cho các mẫu mới: unique active reservation theo item; transaction `BEGIN IMMEDIATE`. Có thể đặt state trên linkage và partial unique index cho `reserved`.

Trạng thái duyệt: pending → approved hoặc excluded. Usage của mỗi job: reserved → succeeded/failed/interrupted. Mẫu mới chỉ được tính là đã dùng thành công sau train, evaluate và GPU cleanup đạt. Batch lỗi/gián đoạn giữ sample reservation gắn với batch để retry chính batch đó; không tự trả mẫu vào hàng chờ rồi vô tình tạo batch khác. Nếu cần hủy batch/trả mẫu, là hành động admin riêng, có audit và chỉ sau chứng minh không còn job active.

Mỗi cặp hợp lệ = một item có hai candidate khác nhau, phiếu user đã nộp, admin winner rõ ràng và nguồn/purpose đủ điều kiện. Tie/both-bad/skip chưa có quyết định winner thì không đếm. Ngưỡng tính sample mới đang chờ, không phải tổng click.

Batch mặc định gồm các mẫu hợp lệ đang chờ tại thời điểm xác nhận. Không tự gom thêm dữ liệu cũ từ lịch sử; nếu muốn training tích lũy/replay hoặc tiếp tục từ adapter trước, cần policy được chốt riêng và ghi rõ trong lineage. Holdout validation/test theo conversation/source group phải ổn định cho cùng dòng model; không đưa test cũ vào train vòng sau. Bước đầu giữ base revision pinned như hiện tại.

## API đề xuất

Giữ endpoint judgment/adjudication/export đã có. Thêm/extend:

- `GET /review-api/overview?status=&source=&search=&offset=&limit=`: admin; summary + rows đã tổng hợp, không N+1 frontend.
- `GET /review-api/reward/training/eligibility?purpose=`: valid_new_count, minimum_pairs, group counts, blocked reasons và snapshot preview.
- `POST /review-api/reward/training/batches`: admin; request key + purpose + expected policy/snapshot version; không nhận lệnh/path/credential từ browser. chốt batch và tạo attempt đầu, trả 202 hoặc batch/run đã tồn tại.
- `GET /review-api/reward/training/batches`: lịch sử batch/phân trang.
- `GET /review-api/reward/training/batches/{id}` và `/items`: chi tiết cùng sample snapshot phân trang.
- `POST /review-api/reward/training/batches/{id}/retry`: attempt mới của batch cũ sau kiểm tra không active.
- `GET /review-api/reward/training/runs`: lịch sử các lần chạy.
- `GET /review-api/reward/training/runs/{id}`: run, snapshot, real progress, metrics.
- `GET /review-api/reward/training/runs/{id}/events?after_seq=&limit=`: cursor event stream qua polling; không chuyển toàn bộ worker.log.

Legacy `/campaigns/{cid}` training vẫn tương thích. Các route run mới nằm dưới training relay đang có; không chuyển raw token hoặc đường SSH ra frontend.

## Task 1: Dữ liệu và danh tính

**Files:** `state/review_store.py`, `api/review_auth.py`, `api/review_schemas.py`, `tests/test_review_store.py`, `tests/test_review_auth.py`.

**Interfaces:** `ReviewStore.training_policy(purpose)`, `overview(filters,offset,limit)`, canonical actor mapping từ credential/JWT bridge đã được xác minh.

- [x] Viết test DB cũ migration không mất item/phiếu/history, nguồn QA tách biệt, actor không spoof từ JSON.
- [x] Chạy test đỏ; xác minh schema/auth IAS đang có trước khi chọn adapter. Nếu backend IAS ở máy khác chưa có access, giữ demo và ghi blocker, không giả tài khoản thật.
- [x] Thêm schema/migration additive, index cho trạng thái/thời gian/source/group; giữ môi trường `.env` và credential hiện hữu.
- [x] Chạy test xanh; snapshot DB nhất quán và ghi rollback chỉ code, không reset dữ liệu. Commit scoped.

## Task 2: Bảng tổng quan và duyệt

**Files:** `api/reviews.py`, `state/review_store.py`, `src/reviewApi.ts`, `src/reviewTypes.ts`, `screens/ReviewScreen.tsx`, `screens/review/ReviewAdmin.tsx`, `styles.css`; tests API/UI tương ứng.

**Interfaces:** overview page `{items,total,offset,limit,summary}`; dùng lại `PUT /items/{iid}/adjudication`.

- [x] Test đỏ: lọc/phân trang server; canonical A/B đảo vị trí; user không xem API; hai admin duyệt đồng thời không ghi đè; lỗi/reload không hiển thị duyệt giả.
- [x] Query summary và rows từ DB, index thích hợp; tránh tải hết 1.000/10.000 item hoặc gọi judgments từng dòng frontend.
- [x] Bảng desktop, mobile row cards; panel A/B có chọn winner/loại, nhận xét tùy chọn và audit mặc định trung thực.
- [x] Test xanh + build; e2e user → phiếu → admin xem/duyệt → khu fine-tune tăng đúng một cặp. Commit scoped.

## Task 3: Eligibility, ngưỡng và immutable batch

**Files:** `state/review_store.py`, `api/review_training.py`, `reward/dataset.py`, `reward/pipeline.py`, tests dataset/training/store.

**Interfaces:** `training_eligibility(purpose)`, `reserve_training_batch(request_key,purpose,expected_policy_version)`; exports kèm snapshot hash.

- [x] Test đỏ: 999 chưa đủ/1.000 đủ với policy đề xuất; 1.000 click cùng item không thành 1.000 cặp; QA/tie/loại không tính sai; nhóm/split thiếu vẫn bị chặn.
- [x] Tạo preview nhất quán; transaction kiểm lại số lượng và reserve; khóa snapshot trước gửi Vast; idempotency có DB constraint, không chỉ state process.
- [x] Giữ approval riêng, lineage batch/item/attempt; pin holdout/group mapping; test snapshot không đổi khi có phiếu mới và không leakage qua nhiều vòng.
- [x] Test mất response/ hai admin bấm: một batch/attempt; retry HTTP đọc trạng thái thay vì gửi batch mới. Commit scoped.

## Task 4: Job orchestration và khôi phục

**Files:** `api/review_training.py`, `reward/training_jobs.py`, `reward/training_service.py`, `reward/training_runner.py`, `deploy/reward-training/README.md`, tests training jobs.

**Interfaces:** request key truyền gateway→Vast; `reconcile_run(local_id,remote_job)`; remote identity/snapshot hash kiểm khớp.

- [x] Test đỏ: queue đầy, mất SSH, timeout admission, OOM, process restart, mất response sau publish; không báo thành công/consume mẫu sai.
- [x] Dùng existing durable Vast job và shared GPU owner. Reconcile trạng thái khi gateway restart; active job không được gửi lại.
- [x] Khi retry, có batch/attempt lineage và reservation đúng; succeeded chỉ sau result/evaluation/cleanup attestation. Không reset poison marker/resident để ép pass.
- [x] Test xanh CPU/mock bên ngoài; một smoke QA GPU được authorise sau khi worker telemetry sẵn. Commit scoped.

## Task 5: Tiến trình và log thật

**Files:** `reward/transformer.py`, `reward/training_worker.py`, `reward/training_runner.py`, `reward/training_service.py`, `api/review_training.py`, tests progress/event cursor.

**Interfaces:** thêm callback optional `train(..., on_progress=None)` để giữ tương thích; event `{seq,time,stage,epoch,step,total_steps,loss,message}`; `events(after_seq,limit)`.

- [x] Test đỏ: seq tăng, đọc lại cursor không mất/đúp event, job lỗi giữ cuối log; không lộ token/prompt/source nhạy cảm.
- [x] Emit telemetry sau optimizer steps thật, lưu `progress.json` atomic và structured events theo batch; không flush disk mỗi token.
- [x] Stage loading/prepare/admission/evaluate/cleanup có status riêng. Phần trăm training = completed optimizer steps / total; giai đoạn chưa biết tổng chỉ hiện trạng thái. ETA chỉ khi có tốc độ đo được.
- [x] Endpoint trả sự kiện allowlist/redacted, có limit/cursor và auth; không đưa arbitrary worker stdout ra UI. Kiểm reset/restart không nhảy thành Hoàn tất.
- [x] Chạy GPU QA thật đối chiếu epoch/step/loss với worker, kết quả và artifact; kiểm resident/cleanup. Commit scoped.

## Task 6: Khu fine-tune và nghiệm thu

**Files:** `screens/review/RewardTraining.tsx`, mới `screens/review/TrainingBatchDetail.tsx`, API/types/styles, tests UI/e2e, docs/test-results.

**Interfaces:** eligibility + runs + events đã có ở tasks 3–5; polling bounded 2–3 giây, cursor incremental.

- [x] Test đỏ: nút khóa khi thiếu cặp/nhóm/purpose, active run; đủ điều kiện mới mở xác nhận. Chuyển role/campaign không lộ/stale dữ liệu admin.
- [x] Hiển thị bộ đếm, lý do bị chặn, snapshot confirmation, history và log drawer; dataset đã duyệt chuyển sang khu này ngay.
- [x] Reload/reconnect đọc run thật; failed/interrupted có lý do và thử lại có kiểm soát. Không tự activate model.
- [x] Unit/API related → full backend → frontend build → e2e desktop/mobile → live QA Vast. Full-suite fail/skip phải ghi tên và nguyên nhân, không gọi pass chung.
- [x] Nghiệm thu với dữ liệu QA tách biệt: user nộp → admin duyệt → thiếu ngưỡng bị chặn → đủ ngưỡng → xác nhận → queued/waiting GPU/train/evaluate/cleanup → artifact/metrics; audit chứng minh không tự train.
- [x] Pin image, snapshot private release profile, kiểm ownership/volume trước rollout; chỉ update service trong scope, không `down -v`. Commit và báo link public + flow ngắn.

## Quyết định cần chốt sau khi review kế hoạch

1. Ngưỡng mặc định: đề xuất **1.000 cặp hợp lệ mới**, admin chỉ sửa trong policy được server kiểm tra; không đồng nhất với 1.000 mẫu đã đủ chất lượng.
2. Vận hành trước ở QA hay mở production sau khi nối danh tính IAS và release/evaluation policy? Đề xuất hoàn thiện end-to-end QA trước.
3. Batch theo yêu cầu mới: mỗi lần chốt lấy các mẫu đang chờ; lịch sử giữ nguyên batch và từng attempt. Training tích lũy/replay hoặc tiếp tục adapter là policy riêng, không tự bật.
4. Cách sinh A/B mới: shared evidence + controlled recipe là đề xuất riêng; chưa triển khai trong kế hoạch giao diện này.

Không ước lượng giờ hoàn thành khi chưa xác minh bridge auth IAS, độ đầy đủ evidence và GPU availability. Mỗi task có đầu ra kiểm thử độc lập; tasks 3–5 cần xong trước khi hứa tiến trình fine-tune thật trên UI.
