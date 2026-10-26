# Đánh giá live luồng KB và model — 2026-10-09

## Kết quả

Chạy evaluator trên source hiện tại với LinchRunner, Qdrant, Neo4j và provider thật; catalog, session, ledger và cache được chuyển vào thư mục tạm. Bộ test không gọi `/ai/exec`, không chạy mã do model sinh, không dựng Docker lab và không bật tìm kiếm web.

Lệnh đã chạy:

```sh
.venv/bin/python evals/kb_grounding/run_live_eval.py \
  --cve CVE-2023-32315 --absent-cve CVE-2099-99999 \
  --supplemental-collection cve_kb_knowledge_lab \
  --timeout 180 --max-provider-calls 8 --private-raw \
  --output /tmp/secai-kb-live-ghsa-20261009/summary.json
```

Đánh giá cấu trúc: **4/4 trường hợp pass**, dùng **2 lượt provider**. CVE vắng (`CVE-2099-99999`) trả `not_found`/`not_generated` qua JSON và SSE, không gọi runner hay model. CVE `CVE-2023-32315` có mô tả chính xác trong KB và 5 chunk GHSA liên kết; JSON và SSE đều trả `synthesized_cve`/`synthesized_not_executed`. Raw prompt không đổi, context được truyền riêng, không lộ vào lịch sử người dùng; token SSE ghép bằng final content.

Kiểm tra trực tiếp trước khi gọi model xác nhận context giữ primary trước advisory GHSA, có URL nguồn `GHSA-gw42-f939-fhvm` và phần `patched-versions` nêu các bản phát hành đã vá. Tổng context không vượt 6.000 ký tự. Tên collection phụ được cấu hình tường minh cho lượt đánh giá; cấu hình ứng dụng mặc định vẫn để trống.

Một lượt trước đó trên `CVE-2021-41773` cũng pass các kiểm tra cấu trúc, nhưng CVE này không có GHSA liên kết trong collection mới. Vì vậy nó không dùng để đánh giá phần bổ sung advisory.

## Chất lượng nội dung model

Đầu ra được gắn đúng trạng thái bản nháp chưa thực thi. Tuy nhiên JSON và SSE sinh nội dung khác nhau rõ rệt dù dùng cùng CVE và prompt; JSON nêu URL advisory, SSE không nêu. Cả hai có bản vá được nêu trong context. Kết quả này không chứng minh tính lặp lại hay khả năng khai thác của mã.

Ở lượt `CVE-2021-41773` trước khi trường KEV được thêm vào formatter, model khẳng định CVE có trong KEV dù dữ liệu lúc đó không được đưa vào context. Đây là một khẳng định chưa được grounding. Formatter hiện truyền `is_in_kev` khi record có giá trị; cần giữ kiểm thử claim ledger để tránh lặp lại lỗi này. Không thực thi PoC ở lượt đánh giá nào.

## Kiểm chứng collection mới

Đã khảo sát read-only ba collection Qdrant. `cve_kb_knowledge_lab` có 51.012 điểm; dữ liệu CVE chính trùng với `cve_kb_hybrid`. Trong 308 điểm riêng biệt, toàn bộ là mitigation GHSA liên kết chính xác bằng `cve_refs`; chúng bổ sung advisory và phiên bản vá chứ không tạo CVE primary độc lập.

Đã triển khai `QDRANT_KB_SUPPLEMENTAL_COLLECTIONS` làm cấu hình danh sách tùy chọn. Với cấu hình này, truy vấn chính vẫn dùng collection hiện tại; truy vấn phụ dùng scroll, filter `source=ghsa` và exact một CVE trong `cve_refs`, tối đa 8 điểm/collection. Collection phụ lỗi hoặc trả payload sai không làm hỏng primary. Nó không thể tự nâng `not_found`/`insufficient` thành `found`. Nội dung GHSA được gắn nhãn riêng, ưu tiên patched-version trong budget và deduplicate theo nội dung mà vẫn giữ URL nguồn. Bỏ trống cấu hình giữ nguyên hành vi cũ.

Để bật sau khi xác nhận collection có trong môi trường triển khai:

```sh
QDRANT_KB_SUPPLEMENTAL_COLLECTIONS=cve_kb_knowledge_lab
```

Không tự sửa `.env` hoặc bật cấu hình production trong lượt này.

## Test

Ma trận retrieval/API/hook: **159 passed**; kiểm thử riêng ưu tiên `patched-versions` khi context đầy: **1 passed**. Các ca bao gồm exact GHSA filter, collection isolation, dedup, cross-CVE rejection, collection lỗi/malformed fail-open, context budget, JSON/SSE và retry guard.

Full suite sau thay đổi GHSA: **2253 passed, 6 skipped, 7 xfailed, 64 warnings** trong 378,23 giây; exit code 0. Một test ưu tiên `patched-versions` được thêm sau khi full suite thu thập test nên đã chạy riêng và pass. Full suite không ghi nhận test fail. Các skip: ba smoke test cần clone/catalog bên ngoài và ba test tùy chọn `test_reward_qwen.py` không chạy do không import được `huggingface_hub.is_offline_mode`. `py_compile` và `git diff --check` cũng pass.

Summary live riêng tư: `/tmp/secai-kb-live-ghsa-20261009/summary.json`; response artifacts có mode `0600` và không được commit.
