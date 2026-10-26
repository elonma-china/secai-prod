# Review A/B và dịch — 2026-10-07

**Kết luận: chưa đủ cơ sở xác nhận cả hai chức năng đã ổn để nghiệm thu production.** Các luồng chính có nhiều kiểm thử offline đạt, nhưng worker dịch có lỗi mất đầu vào khi task bị huỷ từ ngoài; frontend A/B còn vấn đề trạng thái bất đồng bộ. Chất lượng dịch thực và tính hợp lệ của thí nghiệm A/B cần bằng chứng riêng.

Phạm vi là working tree tại HEAD `98d2b72874f325a3d7e9e7c1b4e6f0cff2289220`, nhánh `feat/ab-ui-restyle`, gồm thay đổi chưa commit. Ba agent Sol độc lập kiểm backend A/B, backend dịch và frontend/contract; agent chính đối chiếu và tái chạy reproducer worker. Không sửa source, commit, triển khai hay gọi model/GPU thật. Các file dirty và frontend sibling bị xoá đã tồn tại trước review, được giữ nguyên.

## Phát hiện cần xử lý

### R1 — P2: Hủy task worker từ bên ngoài tạo thành công giả và mất đầu vào

- Vị trí: `src/secai_gateway/translation/worker.py:178–179`, `translation/jobs.py:285–298`.
- `CancelledError` bị bắt và gọi `finish(job_id, generation)` không có result/error. Nếu DB còn `running`, store ghi `succeeded`, `result_json=NULL` và cleanup xoá đầu vào. Job terminal không còn được reclaim để thử lại; route result đọc `json.loads(None)` và lỗi.
- Tái hiện offline bằng specialist giả đang chờ rồi `task.cancel()`: `task_result=True`, `status=succeeded`, `result_json=None`, `input_path=None`, `error_code=None`, `input_exists=False`. Agent chính chạy lại và nhận cùng kết quả.
- Đây là external task cancellation. DELETE của người dùng chuyển DB sang `cancelling` trước và các test tương ứng pass. Đường SIGINT qua `asyncio.run(main()) → serve → run_once` có thể truyền cancellation này; đây là suy luận từ entrypoint, chưa chạy signal test. Không khẳng định SIGTERM gặp lỗi tương tự.
- Sửa tối thiểu: phân biệt hủy job với dừng worker; không cho ghi succeeded nếu không có kết quả hợp lệ; giữ job/đầu vào có thể phục hồi và truyền lại cancellation khi dừng worker.

Reproducer đã chạy, không gọi dịch vụ ngoài:

```python
import asyncio
import tempfile
from pathlib import Path
from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.jobs import JobStore
from secai_gateway.translation import worker

async def main():
    with tempfile.TemporaryDirectory() as t:
        settings = TranslationSettings(db_path=t+'/jobs.db',
            artifact_dir=t+'/artifacts', _env_file=None)
        store = JobStore(settings)
        started = asyncio.Event()
        class Slow:
            def __init__(self, *args): pass
            async def translate(self, *args, **kwargs):
                started.set()
                await asyncio.sleep(100)
        worker.TranslationAgent = Slow
        job = store.admit('file', b'Hello world', '.txt', 'en', 'vi', 'peer', None)
        task = asyncio.create_task(worker.Worker(store, settings, None).run_once())
        await started.wait()
        task.cancel()
        print('task_result', await task)
        row = store.get(job['job_id'])
        print({k: row[k] for k in ('status', 'result_json', 'input_path', 'error_code')})
        print('input_exists', Path(job['input_path']).exists())
asyncio.run(main())
```

### R2 — P2: Lượt chat đến trước danh tính làm mất lời mời A/B

- Vị trí: `secai-fe-devspace/src/screens/review/LiveReviewWidget.tsx:19–30`.
- Component đánh dấu turn đã thử trước khi kiểm `auth.liveEnabled`. Effect chỉ phụ thuộc `turn`; `/me` hoàn tất sau đó không tạo lời mời cho cùng turn. Việc đổi danh tính cũng xóa assignment nhưng không tạo lại cho turn hiện tại.
- Đã tái hiện bằng Vitest/jsdom: render turn khi identity null, sau đó set reviewer; `livePair` không được gọi.
- Sửa tối thiểu: chỉ đánh dấu khi thực sự gửi request và gắn vòng đời request với danh tính/lượt chat, vẫn chặn response cũ và không gửi trùng.

### R3 — P2: Response chi tiết cũ mở lại sau khi đổi bộ lọc A/B

- Vị trí: `secai-fe-devspace/src/screens/review/ReviewOverview.tsx:26`, bộ lọc tại `:48–51`.
- `overviewDetail()` đang chờ trong khi người dùng vẫn đổi filter được; filter xóa detail, nhưng response cũ không có version guard và gọi `setDetail` trở lại. Có thể hiện mẫu thuộc bộ lọc trước trong màn hiện tại.
- Chưa có bằng chứng backend cho phép duyệt sai ID; đây là lỗi nhất quán giao diện. Sửa bằng bỏ response hết hiệu lực hoặc khóa thao tác đổi ngữ cảnh khi request đang chờ.
- Đã tái hiện bằng deferred promise: đổi source sang `qa` khi tải detail, resolve detail cũ; dialog chứa `Old A` xuất hiện trong khi filter vẫn `qa`.

### R4 — P3: Bản nháp duyệt mất khi Escape/Đóng

- Vị trí: `secai-fe-devspace/src/screens/review/AbParts.tsx:53,74,76`; `ReviewOverview.tsx:26,57`.
- `dirty` chỉ chặn click backdrop. Escape/Đóng vẫn đóng; mở lại reset winner/comment. Không ảnh hưởng phiếu đã lưu, nhưng thao tác nhầm làm mất nhận xét chưa gửi.
- Cần quyết định rõ UX: xác nhận bỏ bản nháp hoặc giữ bản nháp; không coi mọi hành động Đóng là lỗi bảo mật.
- Vitest/jsdom xác nhận `dirty=true` rồi Escape vẫn gọi callback đóng.

### R5 — Rủi ro có điều kiện: gateway chuyển nguyên field event từ training upstream

- Vị trí: `src/secai_gateway/api/review_training.py:202`.
- Mock upstream trả event chứa `stdout=WORKER-STDOUT`, `token=WORKER-TOKEN`; response admin giữ nguyên các field. Test `test_e_training_responses_pass_through_remote_fields_unfiltered` ghi nhận bằng xfail.
- Service training hiện có whitelist riêng và test producer thực cũng pass offline. Vì vậy đây là thiếu kiểm tra tại gateway, **không phải bằng chứng token production đã lộ**, không chứng minh reviewer/user không quyền đọc được.
- Có thể project/validate các field telemetry được phép ở gateway để không phụ thuộc hoàn toàn vào producer. Agent A/B xếp P2 có điều kiện; bản tổng hợp tách khỏi lỗi đã xảy ra trong luồng bình thường.

## Giới hạn sản phẩm và bằng chứng

- Live A/B là thu thập preference giữa đáp án trước đó và một lần sinh mới. `api/reviews.py:290–299` không giữ cùng evidence snapshot; provenance model là `unknown`, evidence rỗng, A do client gửi. Không thể suy ra tác động riêng của model/seed; original chat đã hiển thị cũng làm giảm tính blind. Luồng import/assignment được đảo vị trí và giấu provenance là trường hợp khác.
- `state/review_store.py:237–239` trả số lượng vận hành, không có confidence interval/power hay kiểm định chất lượng model. Thiếu những phần này không làm sai chức năng lưu phiếu; nó giới hạn kết luận được phép rút ra.
- `state/review_training_store.py:159` chủ động chặn production vì chưa nối danh tính IAS/policy. Đây là cổng đúng, không đề nghị bỏ để chạy fine-tune.
- Dịch v2 dùng job ID làm capability theo contract hiện hữu, không yêu cầu login/user ID. Không gán lỗi thiếu ownership tài khoản cho thiết kế đã quy định này; cần giữ ID khó đoán, lọc log và no-store.
- UI file tính trần 5 tệp trên cả phiên, gồm job terminal; chưa có reset/remove. Đây là giới hạn UX cần chốt. `secai-fe-devspace/README.md:76` còn ghi 20, khác implementation 5; README đang dirty nên review không ghi đè.
- Không chạy browser/mobile/screen reader hay inference. Tài liệu chất lượng cũ `docs/reports/translate-real-quality-evaluation.md` ghi 216 lượt trên implementation/model trước, gồm 154 PASS/56 FAIL/6 BLOCKED. Chỉ là bằng chứng lịch sử, không phải tỷ lệ chất lượng của checkout hiện tại.

## Ma trận 40 tiêu chí

`ĐẠT offline` chỉ áp dụng đường đi được test/mock hoặc kiểm source ghi tại cột bằng chứng; không chứng minh runtime production. `CHƯA XÁC MINH` không được tính pass. `GIỚI HẠN` mô tả phạm vi hiện có.

| ID | Tiêu chí | Kết quả / bằng chứng |
|---|---|---|
| AB01 | Bearer và phân quyền route admin/reviewer | ĐẠT request bình thường; 4 route JSON sai trả 422 trước auth (xfail), chưa thấy bypass |
| AB02 | Assignment gắn đúng reviewer | ĐẠT offline: store và test ownership |
| AB03 | Vị trí A/B ngẫu nhiên, ổn định khi reload | ĐẠT offline: vị trí lưu trong assignment |
| AB04 | Phiếu user bất biến, admin duyệt riêng | ĐẠT offline: store/API regressions |
| AB05 | Tie/both_bad/skip không tự biến thành winner | ĐẠT offline: bulk/export guards |
| AB06 | Bulk approval đúng snapshot/filter | ĐẠT offline: snapshot/version và test batch |
| AB07 | Batch bất biến, replay không tạo trùng | ĐẠT offline: request key và snapshot tests |
| AB08 | Dedup và nhóm nguồn không rò holdout | ĐẠT offline: fingerprint/group split tests |
| AB09 | Production identity/policy đủ để fine-tune | GIỚI HẠN: đang chủ động chặn |
| AB10 | Model/evidence provenance đủ so sánh kiểm soát | CHƯA ĐẠT: unknown, independent regeneration |
| AB11 | Blind live comparison không bị ảnh hưởng đáp án đã xem | CHƯA ĐẠT cho live; không áp dụng kết luận này cho import |
| AB12 | CI/cỡ mẫu/độ đồng thuận đủ kết luận model tốt hơn | CHƯA CÓ bằng chứng; stats chỉ số đếm |
| AB13 | Danh tính tải chậm không mất lời mời | KHÔNG ĐẠT: R2 |
| AB14 | Response detail cũ không vượt ngữ cảnh filter | KHÔNG ĐẠT: R3 |
| AB15 | Bảo toàn/cảnh báo bỏ bản nháp | KHÔNG ĐẠT: R4 |
| AB16 | Tỷ lệ mời bền vững, cooldown áp dụng | DB settings có test; cooldown trong process, tính từ lời mời là GIỚI HẠN |
| TR01 | Legacy ngôn ngữ/masking/glossary không hồi quy | ĐẠT offline: legacy translation tests |
| TR02 | Capability v3 và cặp ngôn ngữ khớp UI | ĐẠT offline/source |
| TR03 | Giới hạn Unicode, context/output token | ĐẠT offline: v3 budget/core tests |
| TR04 | Bảo toàn CVE/IP/URL/code/số/version | ĐẠT offline: literal mutation regressions; live CHƯA XÁC MINH |
| TR05 | Chống nội dung model sai cấu trúc/prompt injection | ĐẠT trong phạm vi test multilang_security/specialist |
| TR06 | SSE thiếu final không báo thành công | ĐẠT offline frontend/API tests |
| TR07 | Đổi input/hủy text chặn response cũ | ĐẠT offline frontend tests |
| TR08 | File loại/kích thước/magic/field đúng | ĐẠT offline boundary/document tests |
| TR09 | Admission/idempotency/quota nguyên tử | ĐẠT offline jobs tests |
| TR10 | Lease/fencing/reclaim không publish worker cũ | ĐẠT offline jobs/worker process tests |
| TR11 | DELETE job và deadline không publish sau hủy | ĐẠT offline worker/jobs tests |
| TR12 | External task cancellation không thành công giả | KHÔNG ĐẠT: R1 tái hiện |
| TR13 | TTL/tombstone/cleanup giữ accounting | ĐẠT offline jobs/access tests |
| TR14 | Polling/retry/reload không mất idempotency | ĐẠT offline frontend; kiểm ID/kind/language trước cập nhật |
| TR15 | OCR correction/warning trung thực | ĐẠT offline mocks; OCR thực CHƯA XÁC MINH |
| TR16 | TXT/DOCX/DOC và ảnh extraction đúng | ĐẠT offline fixtures/mocks; corpus thực CHƯA XÁC MINH |
| TR17 | Nghĩa/phủ định/số liệu đúng từng 18 hướng | CHƯA XÁC MINH trên runtime hiện tại |
| TR18 | p95/throughput/VRAM/cost đạt ngưỡng | CHƯA XÁC MINH; không inference/GPU |
| SH01 | Frontend regression suite | ĐẠT: 19 files / 156 tests |
| SH02 | TypeScript và production bundle | ĐẠT: build; có cảnh báo chunk >500 kB |
| SH03 | CORS nhất quán và test không lệ thuộc env | CHƯA ĐẠT kiểm thử: một test fail theo env, explicit origin rerun pass |
| SH04 | Proxy translation/training/SSE đúng contract | ĐẠT kiểm source; network E2E CHƯA XÁC MINH |
| SH05 | Responsive, focus, contrast, screen reader thực | CHƯA XÁC MINH browser; test DOM không thay kiểm thực tế |
| SH06 | Full backend suite và deployment hiện tại | CHƯA CHẠY trong review này |

## Kiểm chứng đã chạy

### Backend A/B

`.venv/bin/python -m pytest -q tests/test_review_auth.py tests/test_review_store.py tests/test_review_api.py tests/test_review_export.py tests/test_review_ab_flow_matrix.py tests/test_review_ab_security_edge.py tests/test_review_batches.py tests/test_review_live.py tests/test_review_live_settings.py`: **370 passed, 8 xfailed**, 244,66s, exit 0.

Tám xfail ghi nhận: JSON sai parse trước auth ở 4 route, cooldown tính từ lời mời, sinh B lỗi vẫn tiêu cooldown, event upstream không lọc field, JSON trùng key dùng giá trị cuối. Không tính xfail là tiêu chí đã đạt. Chưa có bằng chứng các parser caveat vượt quyền hay sửa phiếu người khác. Xfail event được mô tả tại R5.

### Backend dịch

1. `.venv/bin/python -m pytest -q tests/test_translate_v2_jobs.py tests/test_translate_v2_job_access.py tests/test_translate_v2_worker.py tests/test_translate_v2_service.py tests/test_translate_v2_literal_mutations.py tests/multilang tests/multilang_security tests/test_translate_validation.py tests/test_translate_mask.py tests/test_translate_glossary.py`: **284 passed, 1 failed** (26,03s).
2. `.venv/bin/python -m pytest -q tests/test_translate_v2_documents.py tests/test_translate_v2_boundary.py tests/test_translate_v2_language.py tests/test_translate_v2_ocr_client.py tests/test_translate_v2_ocr_tokens.py tests/test_translate_v2_ocr_preprocess.py tests/test_translate_v2_ocr_correction.py tests/test_translate_v2_ocr_score.py tests/test_translate_v2_doc_converter.py tests/test_translate_v2_worker_process.py tests/test_translate_v2_launcher.py tests/test_translate_v2_sampling.py tests/test_translation_specialist.py tests/test_multilang_file_ocr_merge.py`: **232 passed** (11,43s).
3. Node fail: `tests/multilang/test_api.py::test_composition_off_on_legacy_and_cors`, nhận `Access-Control-Allow-Origin: *`. Chạy lại với `ALLOWED_ORIGINS=https://trusted.example`: **1 passed**. Không đổi env file; không coi rerun này xóa failure của lượt chính và không suy thành lỗ hổng code đã chứng minh.
4. Reproducer R1: `.venv/bin/python /tmp/translation-cancel-repro-sol.py`, agent dịch và agent chính đều xác nhận false success/input deleted.

Tổng hai suite dịch riêng biệt: **516 passed, 1 failed**; rerun 1 pass báo riêng.

### Frontend

- `npm test -- --reporter=dot`: **19 files, 156 tests passed** (4,83s).
- `npm run build -- --outDir /tmp/secai-fe-independent-sol-build`: thành công, JS bundle 711,72 kB; cảnh báo chunk >500 kB.
- A/B reviewer cũng chạy subset frontend **9 files, 89 tests passed**; đây là tập con, không cộng vào 156.
- Test tái hiện tạm `npx vitest run src/screens/review/IndependentSolAudit.tmp.test.tsx --reporter=verbose`: **3 assertions pass**, 1,67s, xác nhận hành vi lỗi R2/R3/R4; không phải chứng minh đã sửa. File tạm được xóa sau chạy; nguyên mã và output lưu trong `/tmp/frontend-review-sol.md`.
- `git diff --check`: exit 0. Source giữ nguyên; thay đổi mới của review là báo cáo này.

## Điều kiện đóng review

Ưu tiên sửa R1 và thêm regression external cancellation; sau đó sửa R2/R3 và quyết định UX R4. Tách fixture CORS khỏi env máy chạy. Muốn nghiệm thu chất lượng, cần corpus/reference/rubric và bằng chứng theo từng hướng dịch trên model/config thực; muốn dùng live A/B để kết luận model, cần provenance/evidence kiểm soát, danh tính và phương pháp phân tích phù hợp. Không mở cổng production fine-tune chỉ để đạt luồng demo.
