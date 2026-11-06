# Baseline chất lượng dịch — chuẩn bị đợt 1

Trạng thái: **chưa có baseline live mới**. Tài liệu này xác định công cụ dùng lại và bằng chứng còn thiếu; test synthetic không chứng minh bản dịch đúng nghĩa.

Sau khi người dùng yêu cầu đo thực, đã hoàn tất [chẩn đoán live ngày 2026-10-07](../test-results/translation-live-diagnostic-20261007.md): v3 có 16 response 503 và 20 mẫu chưa chạy; probe specialist trả `POISONED`. Luồng legacy vi↔en riêng trả đủ bốn bản dịch, không phát hiện lỗi nghĩa trọng yếu theo rubric đã cố định. Đây là mẫu chẩn đoán nhỏ, chưa thay thế baseline đủ 18 hướng hay nghiệm thu model.

Đã bổ sung [lệnh diễn tập offline đầy đủ](translation-offline-rehearsal-20261007.md), gồm tạo fixture và kiểm kết quả, không cần chuẩn bị corpus thật. Luna chạy hai lượt trên thư mục mới: cả hai exit 0, hash của bảy artifact giống nhau; chạy lại cùng thư mục bị từ chối trước khi ghi đè. Ba lượt có tổng lịch 6 mẫu: một output dựng sẵn chưa chấm, một timeout và bốn output thiếu; quality/release đều `BLOCKED`. Đây là kiểm tra tính tái lập của harness, chưa phải baseline ngôn ngữ. Evidence: `/tmp/secai-quality-followup-20261007/rehearsal-verification.md`.

## Công cụ có sẵn và đường chạy

| Luồng | Công cụ dùng lại | Giới hạn |
|---|---|---|
| Manifest/corpus | `evals.multilang.validate_corpus` | Kiểm revision/license, aligned item, source group, chống trùng giữa split; không tự tạo gold |
| Text DuyTa model/legacy/v3 | `evals.multilang.run` | Dry-run mặc định; live yêu cầu inventory/lease. Runner và scorer hiện pin `DuyTa/Cyber-F1-AWQ` |
| Chấm text DuyTa | `evals.multilang.score` | Từng hướng/lượt, mẫu số gồm lỗi/thiếu, hash kết quả và review, diagnostic metrics; thiếu evidence giữ BLOCKED |
| File/ảnh v2 qua specialist | `evals.multilang.multifile_batch` | Gửi/poll/resume bounded, lưu manifest private và kết quả; đây là công cụ thu kết quả, không phải bộ chấm nghĩa |
| OCR | `evals/translation_ocr/` | Chấm OCR tách với lỗi dịch downstream; không dùng chỉ số bảo toàn token để suy ra OCR chính xác |

Không gán model DuyTa vào output Hy-MT/specialist chỉ để scorer chấp nhận. Nếu đường production dùng specialist, cần inventory thực và đường chấm tương ứng trước kết luận. Không tạo thêm harness trùng lặp trong đợt sửa độ tin cậy này.

## Baseline cần ghi gì

Mỗi lượt ghi code SHA + hash diff nếu dirty; endpoint/route (không token); model/checkpoint revision và cấu hình runtime đã xác minh; prompt/decoding/config hash; manifest hash; split; số source group độc lập; lịch chạy, timeout/rate/retry và giới hạn tài nguyên. Dữ liệu nhạy cảm và capability job ID giữ trong thư mục private, không đưa vào Git/report công khai.

Mỗi mẫu có ID/hướng/stratum, source/reference có provenance, protected spans, source/aligned group, output hash, lỗi hoặc bản dịch, latency, model evidence, người/nguồn chấm và các chiều rubric. Giữ trường hợp fail/timeout/empty trong mẫu số; không loại để tăng tỷ lệ pass. Chấm nghĩa cần target đúng, đủ ý, phủ định/điều kiện/số đúng, không thêm khẳng định, thuật ngữ đúng, dễ đọc; kết quả metric chuỗi chỉ là hỗ trợ.

Tách DEV dùng sửa lỗi và TEST chưa bị xem khi tối ưu. Tiêu chí hiện có `docs/criteria/multilang-assessment.md` vẫn là nguồn chuẩn: tối thiểu 30 mẫu mỗi 18 hướng, 3 lượt, báo từng hướng; không coi 3 lần cùng nguồn là 3 mẫu độc lập. Những ngưỡng gốc và điều kiện evidence không được hạ bằng cờ CLI.

## Lệnh chuẩn bị offline

Thực hiện từ project root, sử dụng manifest đã được chuẩn bị độc lập; các đường dẫn dưới đây là placeholder input, không phải file đã có sẵn.

```sh
PYTHONPATH=src .venv/bin/python -m evals.multilang.validate_corpus /private/corpus.jsonl --min-per-direction 30
PYTHONPATH=src .venv/bin/python -m evals.multilang.run --manifest /private/corpus.jsonl --mode model-only --split DEV --rounds 3 --output /private/baseline-results.jsonl
```

Lệnh thứ hai không có `--live`: chỉ in kế hoạch, không gọi mạng và không tạo kết quả inference. `model-only` là đường DuyTa hiện có; không dùng để giả nhận kết quả specialist. Xem `evals/multilang/README.md` để chuẩn bị inventory, lease, attestation và chấm điểm đúng định dạng.

Các regression có sẵn để Luna xác minh: `tests/multilang_assessment/` (CLI, manifest, scoring, evidence, paired comparison), `tests/test_multifile_batch.py`, `tests/test_multifile.py`, `tests/test_multifile_grouped_preparation.py`. Ghi lệnh và kết quả thực tế trong báo cáo test đợt này.

## Bằng chứng còn thiếu trước live và quyết định chất lượng

- Endpoint và model/config thực của luồng muốn đo; đặc biệt phân biệt DuyTa và specialist.
- Corpus/reference được phép dùng và split phù hợp; registry hiện chỉ là metadata, không đồng nghĩa đã có corpus.
- Người/nguồn chấm độc lập và calibrated; thiếu chấm nghĩa thì verdict là chưa xác minh.
- Giới hạn số request, thời gian, chi phí và lịch GPU không tranh OCR/incumbent; không lấy health làm bằng chứng model chạy đúng.
- Baseline trước/sau trên cùng manifest/config được ghi rõ; holdout giữ độc lập.

Đợt này cải thiện độ tin cậy phần mềm và khả năng kiểm chứng. Không đưa ra tỷ lệ tăng chất lượng ngôn ngữ khi chưa đo thật.

## Kiểm chứng offline đã hoàn tất (Luna)

Lệnh `PYTHONPATH=$PWD/src POC_CATALOG_DB=/tmp/secai-quality-20261007/eval-poc.db .venv/bin/python -m pytest -q tests/multilang_assessment tests/test_multifile_batch.py tests/test_multifile.py tests/test_multifile_grouped_preparation.py`: **222 passed**, 41,99s, exit 0.

Luna chạy validator, dry plan và scorer trên hai fixture synthetic `vi>ja` từ test có sẵn, không dùng dữ liệu production. Dry plan báo `live=false`, 6 scheduled requests/3 lượt, không tạo file inference. Scorer với một output giả và một timeout giữ mẫu số `scheduled=2`, `errors=1`; thiếu annotation khiến `quality_gate=BLOCKED`, `release_gate=BLOCKED`. `sacrebleu 2.5.1` hoạt động, diagnostic coverage 1/2; không phải điểm chất lượng dịch thật.

Manifest synthetic file SHA-256: `72c7d1d1bd811d56dfa68eb02525f8a11daa2f3756ad7bcdd5c33f784ca47284`. Log, lệnh đầy đủ và artifact local: `/tmp/secai-quality-20261007/eval-verification.md`, `/tmp/secai-quality-20261007/eval/`. Không sửa corpus hay giảm quality gate.
