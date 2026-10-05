# Mục lục tài liệu

Tài liệu theo chủ đề được giữ ở các thư mục dưới đây. Các hướng dẫn legacy ở gốc `docs/` giữ đường dẫn để liên kết và công cụ đọc dossier tiếp tục hoạt động.

| Nhóm | Nội dung |
| --- | --- |
| [api/](api/) | Contract report v2, dịch file/ảnh, text đa ngôn ngữ và expert review |
| [examples/](examples/) | Cấu hình mẫu và lệnh chạy local |
| [criteria/](criteria/) | Tiêu chí kiểm chứng và nghiệm thu từng tính năng |
| [plans/](plans/) | Kế hoạch triển khai |
| [reviews/](reviews/) | Kết quả review; verdict gắn với commit được review |
| [test-results/](test-results/) | Kết quả test và bằng chứng rollout |
| [benchmarks/](benchmarks/), [reports/](reports/) | Benchmark và báo cáo phân tích |
| [loop/](loop/) | Trạng thái và nhật ký các vòng làm việc |
| [nghiem-thu/](nghiem-thu/), [nghiem-thu-showcase/](nghiem-thu-showcase/) | Hồ sơ nghiệm thu và showcase |

## Đọc theo tính năng

- API dịch đang dùng trên giao diện: [Dịch Devspace hiện tại](api/dich-devspace-hien-tai.md).

- API ngắn gọn, có ví dụ và đối chiếu cũ/mới: [Dịch](api/dich-api-tom-tat.md), [Báo cáo](api/bao-cao-api-tom-tat.md).
- Báo cáo: [API MVP](api/report-mvp.md), [tiêu chí](criteria/report-mvp.md), [Docker](../deploy/report/README.md).
- Dịch file/ảnh: [contract v2](api/translate-v2-contract.md), [chạy local](examples/translate-v2-local-operator.md), [worker Docker](../deploy/file-ocr/README.md).
- Đa ngôn ngữ: [contract v3](api/multilang-contract.md), [operator](api/multilang-translate.md), [triển khai](../deploy/multilang/README.md).
- Expert review: [API](api/review-mvp.md), [operator](review-operator-guide.md).
- Reward Qwen3-1.7B: [chuẩn bị dữ liệu, huấn luyện, đánh giá và dịch vụ chấm điểm](reward-qwen-operator.md).
- Chat/session: [API chung](api-guide.md), [project/session](session-project-api-guide.md).
- PoC: [API](poc-api-guide.md), [verify](poc-verify-architecture.md).

## Triển khai và kiểm chứng hiện tại

- [Fine-tune Qwen3-1.7B với 1.000 cặp QA, tối đa 5 vòng](test-results/reward-qwen3-17b-1000-pairs-20261006.md).

- [Luồng đánh giá → huấn luyện Qwen3-1.7B thật → API chấm điểm (dataset QA)](test-results/reward-qwen3-17b-real-flow-20261006.md).

- [Xuất báo cáo TXT/DOCX/PDF và kiểm chứng](test-results/report-export-20261005.md).

- [Grid dịch tệp, preview và báo cáo vào thẳng workspace](test-results/file-preview-report-workspace-20261005.md).

- [Dịch v3 stream và cập nhật Devspace 05/10](test-results/translation-v3-stream-devspace-20261005.md).

- [Full-feature image và routing Devspace](../deploy/all-features/README.md).
- [Tích hợp main và giới hạn suite](test-results/all-features-main-integration.md).
- [Docker/Vast smoke, persist báo cáo và rollback](test-results/all-features-deployment-20261004.md).
- Serving năm model và matrix GPU: [CLAUDE.md](../CLAUDE.md); chi tiết trong repo Git riêng `secai-serving-vast/vast/gpu_specialists/README.md`.

Không di chuyển hoặc sửa dữ liệu/kết quả đánh giá để đổi verdict lịch sử. Log và snapshot private của máy triển khai không nằm trong tài liệu versioned.
