# Mục lục tài liệu

Toàn bộ tài liệu dự án nằm trong `docs/`, chia theo **loại tài liệu**. Gốc `docs/` chỉ có file này. README của từng thành phần (`deploy/*`, `scripts/`, `secai-fe-devspace/`, `pentestcode/`, `evals/`) và dữ liệu bằng chứng (`evidence/`) ở lại cạnh code vì được code/dịch vụ đọc trực tiếp.

| Thư mục | Nội dung | Hỏi khi nào |
| --- | --- | --- |
| [api/](api/) | Contract và hướng dẫn gọi API: chat/session, PoC, exploit stream, dịch, report, expert review | Cần biết endpoint, request/response |
| [design/](design/) | Kiến trúc và thiết kế: PoC-Verify, bộ phân loại/sanitize đầu vào | Cần hiểu vì sao hệ thống làm vậy |
| [operations/](operations/) | Vận hành/triển khai: operator guide review, reward Qwen, dịch chuyên dụng, Docker, phiên bản container | Cần chạy, deploy, xử lý sự cố |
| [plans/](plans/) | Kế hoạch triển khai | Cần biết dự định/tiêu chí đã chốt |
| [criteria/](criteria/) | Tiêu chí kiểm chứng và nghiệm thu từng tính năng, kể cả tiêu chí vận hành (ops) | Cần biết thế nào là đạt |
| [acceptance/](acceptance/), [nghiem-thu-showcase/](nghiem-thu-showcase/) | Hồ sơ nghiệm thu, validation review MVP, showcase | Cần bằng chứng nghiệm thu |
| [reports/](reports/) | Báo cáo phân tích, rà soát mã nguồn, kết quả live, báo cáo tính năng | Cần đọc kết quả đã đo |
| [benchmarks/](benchmarks/), [test-results/](test-results/), [security-tests/](security-tests/) | Benchmark, kết quả test gần đây, kế hoạch/báo cáo test bảo mật | Cần số liệu |
| [frontend/](frontend/) | Brief thiết kế lại FE và yêu cầu FE cho báo cáo thông minh | Làm việc với `secai-fe-devspace/` |
| [examples/](examples/) | Cấu hình mẫu và lệnh chạy local | Cần ví dụ chạy |
| [loop/](loop/), [superpowers/](superpowers/) | Trạng thái/nhật ký các vòng làm việc và kế hoạch của agent | Cần lịch sử quá trình |
| [archive/](archive/) | Kết quả test và review cũ (`archive/test-results/`, `archive/reviews/`) | Cần truy vết lịch sử |

**Quy ước:** tài liệu mới đặt vào thư mục đúng loại ở bảng trên, tên file kebab-case, ngày dạng `YYYYMMDD` nếu là bản ghi theo thời điểm. Không thả file vào gốc `docs/`. Khi đổi chỗ file, sửa luôn link và comment trong code trỏ tới nó (tìm bằng `git grep <tên-file>`).

## Đọc theo tính năng

- API dịch đang dùng trên giao diện: [Dịch Devspace hiện tại](api/dich-devspace-hien-tai.md).

- API ngắn gọn, có ví dụ và đối chiếu cũ/mới: [Dịch](api/dich-api-tom-tat.md), [Báo cáo](api/bao-cao-api-tom-tat.md).
- Báo cáo: [API MVP](api/report-mvp.md), [tiêu chí](criteria/report-mvp.md), [Docker](../deploy/report/README.md).
- Dịch file/ảnh: [contract v2](api/translate-v2-contract.md), [chạy local](examples/translate-v2-local-operator.md), [worker Docker](../deploy/file-ocr/README.md).
- Đa ngôn ngữ: [contract v3](api/multilang-contract.md), [operator](api/multilang-translate.md), [triển khai](../deploy/multilang/README.md).
- Expert review: [API](api/review-mvp.md), [operator](operations/review-operator-guide.md).
- Reward Qwen3-1.7B: [chuẩn bị dữ liệu, huấn luyện, đánh giá và dịch vụ chấm điểm](operations/reward-qwen-operator.md).
- Chat/session: [API chung](api/api-guide.md), [project/session](api/session-project-api-guide.md).
- PoC: [API](api/poc-api-guide.md), [verify](design/poc-verify-architecture.md).
- Vận hành: [tiêu chí ops](criteria/ops.md), chấm bằng `scripts/ops-check.py` (chỉ đọc), kết quả đo [baseline 2026-11-27](archive/test-results/ops-baseline-20261127.md); [quản lý phiên bản container](operations/container-versions.md) bằng `scripts/versions.py`.

## Triển khai và kiểm chứng hiện tại

- [Devspace: demo user/Admin và duyệt phản hồi A/B](archive/test-results/devspace-ab-two-roles-20261006.md).
- [Devspace → Vast: job fine-tune Qwen3-1.7B thật, 1.000 cặp QA](archive/test-results/reward-vast-devspace-training-20261006.md).

- [Fine-tune Qwen3-1.7B với 1.000 cặp QA, tối đa 5 vòng](archive/test-results/reward-qwen3-17b-1000-pairs-20261006.md).

- [Luồng đánh giá → huấn luyện Qwen3-1.7B thật → API chấm điểm (dataset QA)](archive/test-results/reward-qwen3-17b-real-flow-20261006.md).

- [Xuất báo cáo TXT/DOCX/PDF và kiểm chứng](archive/test-results/report-export-20261005.md).

- [Grid dịch tệp, preview và báo cáo vào thẳng workspace](archive/test-results/file-preview-report-workspace-20261005.md).

- [Dịch v3 stream và cập nhật Devspace 05/10](archive/test-results/translation-v3-stream-devspace-20261005.md).

- [Gói triển khai `stack/` cho máy mới](../stack/README.md), [tiêu chí nghiệm thu](../stack/ACCEPTANCE.md), [smoke 2026-11-23](archive/test-results/stack-package-smoke-20261123.md); thay `deploy/docker-compose.yml`/`deploy/up.sh` (deprecated).
- [Full-feature image và routing Devspace](../deploy/all-features/README.md).
- [Tích hợp main và giới hạn suite](archive/test-results/all-features-main-integration.md).
- [Docker/Vast smoke, persist báo cáo và rollback](archive/test-results/all-features-deployment-20261004.md).
- Serving năm model và matrix GPU: [CLAUDE.md](../CLAUDE.md); chi tiết trong repo Git riêng `secai-serving-vast/vast/gpu_specialists/README.md`.

Không di chuyển hoặc sửa dữ liệu/kết quả đánh giá để đổi verdict lịch sử. Log và snapshot private của máy triển khai không nằm trong tài liệu versioned.
