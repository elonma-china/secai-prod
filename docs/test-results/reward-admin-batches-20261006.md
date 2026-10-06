# Nghiệm thu admin A/B và batch fine-tune — 2026-10-06

Đã triển khai giao diện admin hai tab **Phản hồi / Fine-tune**. Link public tại lúc nghiệm thu:
https://worlds-vids-terry-opinions.trycloudflare.com (Cloudflare quick tunnel).

## Thay đổi

- Bảng Phản hồi gồm câu hỏi, user chọn, thời gian và thao tác; phân trang server 20 dòng.
- Duyệt từng câu sau khi xem hai đáp án; quyết định admin riêng, không sửa phiếu user.
- Duyệt toàn bộ có preview và xác nhận snapshot theo bộ lọc, áp dụng cả các trang chưa hiển thị. Chỉ duyệt canonical winner nhất quán khi tất cả phiếu đã nộp và campaign còn mở; tie/cả hai kém/bỏ qua/mâu thuẫn giữ lại.
- Mẫu đã duyệt sang hàng chờ Fine-tune. Ngưỡng mặc định 1000 cặp hợp lệ chưa thuộc batch; count theo cặp sau dedup, không theo số click.
- Server chốt immutable batch trong SQLite review hiện hữu, lưu sample, approval, user votes, hash và partition mapping. Mẫu mới sau chốt nằm ngoài batch.
- Pair fingerprint reserve cả các bản sao; alias snapshot/source groups vẫn được giữ. Không tự replay các mẫu trùng vào batch kế tiếp.
- Retry giữ batch, tạo attempt mới, giữ lịch sử; request key gateway→Vast khôi phục mất response mà không chạy GPU hai lần.
- Lịch sử xem sample, chọn từng attempt, cursor log đến cuối kể cả job đã hoàn tất; tải structured log JSON. Worker stdout/token/prompt không được gửi vào telemetry.
- Tiến trình dùng optimizer step/epoch/loss thật. Polling có thể gộp các step; không giả log hoặc ETA. Chỉ Hoàn tất sau evaluation và GPU cleanup.
- Tỷ lệ A/B toàn hệ thống vẫn 10%, cooldown600giây; admin chỉnh trong phần cài đặt thu gọn.

## Kiểm chứng

- Backend liên quan sau fix canary: **46 passed** (`test_review_batches`, `test_reward_training_jobs`, `test_review_live`, `test_review_store`, `test_review_api`, `test_reward_pipeline`), log `/tmp/ab-batch-final-check.log`.
- Frontend checkout chính: **85 passed / 17 files**, build có report/file/image feature flags thành công; cảnh báo bundle >500kB vẫn còn. Logs `/tmp/ab-batch-main-frontend-last.log`, `/tmp/ab-batch-main-build-last.log`.
- Full suite checkout riêng sau review: **1799 passed, 2 failed, 3 skipped**, 64 warnings /668.40s. Hai lỗi đã biết: `tests/multilang/test_api.py::test_composition_off_on_legacy_and_cors` (wildcard CORS); `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal` (thiếu `/home/ict/AIEngines/secai/claude-bug-bounty`). Log `/tmp/ab-batch-full-backend-final.log`. Fix canary public-input validation được chạy riêng RED→GREEN trước rollout.
- Full suite checkout chung được chạy riêng ở revision13c086e, trong lúc có thay đổi ngoài scope tại `prompts/chat.md` và các test knowledge-grounding chưa commit. Kết quả **1833 passed, 9 failed, 3 skipped**,64warnings /667.40s; log `/tmp/ab-batch-full-release.log`. Bảy lỗi thêm cùng thay đổi prompt/chat ngoài scope: XML parse tại line171 và shadow protocol tags; đã tái hiện riêng. Không coi checkout chung là suite pass.
- e2e canary cuối **PASS**, run `01a1119d-57fb-7777-a4f1-0347a1a76808`.
- e2e public **PASS**, run `01a1119e-48ac-7f26-a5c0-85816f8c31c6`: user bị403 với overview; user vote→admin duyệt riêng→bulk confirm; tie vẫn pending; queue tăng đúng2; reload/đổi role không lộ tab admin.
- e2e history/GPU **PASS**, run `01a111a0-7a90-718f-9a48-1705c27f7a69`: Hoàn tất, log có thể tải, snapshot1008mẫu còn phiếu user/approval, reload giữ batch.
- Desktop1280px và mobile390px: documentWidth==viewportWidth, bảng mobile thành row cards. Screenshot tại `output/playwright/ab-admin-overview-desktop.png`, `ab-admin-overview-mobile.png`, `ab-batch-training-live.png`, `ab-batch-completed.png`.

## Batch GPU thật

Bắt đầu bằng nút xác nhận Fine-tune trên **giao diện public**, không giả job hoặc progress.

| Trường | Kết quả |
|---|---|
| Batch | `90029494-17b2-462c-bc13-9457475c9751` |
| Attempt | `6e8306e7-ffce-4eb8-9864-1303d79bb21b` |
| Vast job | `994e8bd9-54b9-4005-ab99-480439720f16` |
| Dữ liệu |1008cặpQA tổng hợp/script, gồm1000mẫuQA hiện có và8mẫuQA/demo đã duyệt; không phải nhãn chuyên gia production |
| Train / validation / test |566 /261 /181; group holdout được pin |
| Model |Qwen/Qwen3-1.7B, LoRA+scalar head; revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e` |
| Cấu hình |1epoch, lr1e-4, max length384, LoRA rank8 |
| Validation |261/261đúng |
| Test |181/181đúng; Wilson lower95%=97.9217%; swap/equal-score delta0 |
| Thời gian |Created21:29:44 →succeeded21:33:12 (UTC+7) |
| Telemetry |419sự kiện có seq tăng, qua waiting_gpu→loading→training→evaluating→cleanup→succeeded |
| Artifact SHA-256 |`006305c544f2052b84487a7ac320d5d6063e7ffbf560745850a9f88f6ae03102` |
| Release gate |false: artifactdemo và chưa chốt production policy; không tự kích hoạt model |

Worker riêng sử dụng khoảng4894MiB. Sau job, child đã được reaped; GPU trở về baseline72374MiBused /8676MiBfree; ba residentPID3203145,3257875,3276685 còn nguyên. Durable owner summary: `clean_exit_attested=true`, child containerPID79603; không reset lock/poison hay unload resident. Raw proof local `/tmp/ab-batch-proof.json`, GPU baseline/after `/tmp/ab-batch-gpu-*.log`, remote metadata/attestation `/tmp/ab-batch-vast-attest.log`.

## Triển khai và giới hạn

Gateway image immutable: `sha256:a7a21f320c25861a6441baaf0aa9fb85091804374429f469d42908f97377a7ad`. Patch nguồn review revision13c086e; giữ phần runtime khác trên baseimage đã xác minh. Releaseprofile, sourcehashes, DBbackup nhất quán và ownership ở `/home/ict/secai-releases/review-batches-20261006` private0700/0600. Không thay volume, không restore DB khi rollbackcode. Relay dùng cùng reviewDB/credentials; serviceGPU riêng trên Vast được update, giữ `.env` và GPU owner.

Các service canary được dọn sau test; Devspace public vẫn đi5173. Cổng5179 đã có từ trước, không thuộc canary vừa tạo, không tự dừng service của công việc khác.

Danh tính user/admin vẫn là demo dùng tài khoản vai trò chung. Production training bị chặn tới khi nối authIAS thật và releasepolicy. API/campaign/job legacy được giữ; không dựng batch hoặc log giả cho lượt legacy trước khi có batchledger. Không đổi cách sinh hai đáp án trong lần cập nhật này.

## Quyết định khi thực hiện

1. Bulk approval chỉ lấy user winner rõ ràng và đầy đủ phiếu, cần snapshot confirmation; mẫu mơ hồ duyệt riêng. Chi phí: admin vẫn phải xử lý những mẫu đó.
2. Mặc định1000cặp và QA-only như kế hoạch được duyệt; không giả bridgeIAS. Chi phí: production còn chờ tích hợp danh tính/policy.
3. Componentcompact mới giữ legacy campaign/job APIs và moduleUI cũ để tương thích. Chi phí: còn mã legacy cần duy trì.
4. Holdout mới deterministic theo connected prompt/source keys, pin qua các batch; xung đột split bị chặn. Chi phí: nhóm nhỏ thiếu đủ ba split chưa được train.
5. Re-grade journal tail bị ghi dở thành Important vì có thể làm mất event mới sau crash; đã có RED→GREEN recovery. Chi phí: thêm sửa tail journal nhỏ.
6. Re-grade stale request key thành Important vì lần fine-tune kế tiếp có thể trả batch cũ; đã RED→GREEN clear key khi history xác minh batch/attempt. Chi phí: đồng bộ reference với durable history.
7. Canary fix giữ mẫu không qua public-input validation ngoài bộ đếm train, giữ nguyên bộ lọc bảo mật; không triển khai prompt/chat dirty ngoài scope. Chi phí: mẫu không hợp lệ vẫn được lưu để audit, không được huấn luyện.

Không còn minor review chưa xử lý. Reviewer độc lập `review_batch_history` phát hiện3Important và2Minor; tất cả đã có repro/fix. Các lỗi ngoài scope/full suite được ghi rõ, không đổi dữ liệu hay benchmark để làm pass.

## Flow thử ngắn

1. Vai trò User: gửi lựa chọn A/B; chỉ thấy hai đáp án và nút chọn trong chat khi được mời.
2. Chuyển Admin → Đánh giá A/B → Phản hồi.
3. Xem và duyệt từng câu, chọn đáp án rồi xác nhận; hoặc Duyệt toàn bộ → xem số lượng → Xác nhận duyệt.
4. Fine-tune: kiểm hàng chờ và ngưỡng; tie/mẫu không hợp lệ không tự được tính.
5. Đủ điều kiện → Fine-tune → Xác nhận: chốt một batch.
6. Xem stage/step/epoch/loss, mở log; model không tự kích hoạt sau Hoàn tất.
7. Lịch sử fine-tune → Xem batch: kiểm sample/phiếu/approval và từng attempt; reload vẫn giữ lịch sử.

Tên các test ngoài scope thêm ở checkout chung:

- `tests/test_chat_owasp_version_anchor_fix.py::test_composed_chat_prompt_still_well_formed_xml`
- `tests/test_chat_poc_drilldown_regression.py::test_uc5_composed_chat_prompt_still_well_formed_xml`
- `tests/test_chat_remediation_xxe_fix.py::test_composed_chat_prompt_still_well_formed_xml`
- `tests/test_chat_response_language_mirror.py::test_composed_chat_prompt_still_well_formed_xml`
- `tests/test_chat_response_language_mirror.py::test_composed_chat_prompt_still_well_formed_after_round3`
- `tests/test_prompts.py::test_composed_system_prompts_are_well_formed_xml[chat]`
- `tests/test_prompts.py::test_custom_prompts_do_not_shadow_qwen_protocol_tags`

Bổ sung UX: log đang tải hiển thị Đang tải log, chưa kết luận rỗng; đổi attempt xóa log cũ ngay trước fetch, tránh tải nhầm log gắn nhãn attempt mới. Test promise pending RED→GREEN.
