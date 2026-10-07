# Admin chỉnh tỷ lệ A/B toàn hệ thống — 2026-10-06

Đã thêm `GET/PUT /review-api/live-settings` chỉ Bearer admin. PUT kiểm quyền trước đọc body, cap 1 KiB, `sample_percent` nguyên 0–100 và cấm field khác. Cấu hình được lưu trong bảng `live_settings` của SQLite review đang có, write + audit trước/sau trong cùng transaction. Khi chưa có override, dùng `REVIEW_LIVE_SAMPLE_RATE` hiện hữu. Mỗi lượt live-pairs đọc DB để áp dụng chung; không đổi cooldown.

Giao diện admin có phần thu gọn Tỷ lệ mời A/B, nhập và Lưu tỷ lệ. User không được render control và API reviewer trả 403. Role trong Devspace vẫn là demo tài khoản chung; không coi dropdown Demo là xác thực IAS production.

## Kiểm chứng

- Backend liên quan: 32 passed (settings/live/API/store/auth/export/static proxy).
- Frontend: 14 files, 80 tests passed; build thành công, cảnh báo chunk >500 KiB còn.
- Full backend: 1786 passed, 2 failed, 3 skipped, 64 warnings, 589.85s. Hai lỗi đã có trong nghiệm thu trước: `tests/multilang/test_api.py::test_composition_off_on_legacy_and_cors` và `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal` (thiếu cây claude-bug-bounty). Không gọi full suite pass.
- e2e runner 0.17.0 trên web public: admin đổi 10→50, reload vẫn 50; input 101 bị khóa; 0% khiến live-pairs không offer; reviewer GET/PUT settings đều 403. Sau test trả lại 10%. Run passed `01a11173-d3b8-790f-82f9-e0d7a45466fc`.
- Backend unit: hai reviewer dùng cùng override 100%; fresh ReviewStore vẫn nhận DB override; cooldown chặn offer tiếp; đổi 0 chặn model call. Audit before/after đúng actor.
- Fresh process trong container đọc DB thực trả 10%; audit thực có 10→50→0→10.
- Reviewer độc lập không còn Critical/Important sau sửa integration fixture.

## Triển khai

Base image immutable `sha256:232c5e90739233ce55a33074a5bd6fc17ac20ac71027cab01a946fd2da2a05f8`; candidate `sha256:8659e1801c587f51e4ef1fa1563e18e3c667a9e16b0e6d18fb5d600447825de6`. Chỉ patch `api/reviews.py` từ source đang chạy và thay `state/review_store.py`; giữ footer/export cũ của runtime, không kéo reward modules ngoài scope vào main gateway. Candidate import/DB test đạt.

Snapshot profile/DB/ownership và rollback nằm private `/home/ict/secai-releases/review-live-rate-20261006`. Chỉ recreate gateway bằng pinned profile, giữ review volume, session volume và hai network; không đụng worker/model GPU. `/health` 200, admin settings 200, reviewer 403 sau rollout. Frontend static phục vụ dist mới.

DB thực: `/app/review-data/reviews.db`, volume `secai-review-data`, host `/var/lib/docker/volumes/secai-review-data/_data/reviews.db`. Không tạo DB mới cho cấu hình tỷ lệ. Bằng chứng screenshot local `output/playwright/admin-global-ab-rate-50.png`; logs `/tmp/ab-rate-*`; không commit runtime dữ liệu hoặc private profile.
