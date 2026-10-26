# Kế hoạch mở rộng sample và đo CCU dịch qua production

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Người thực hiện tự triển khai trong phiên; kế hoạch này không yêu cầu phân công subagent.

**Goal:** Đo CCU qua gateway production `http://127.0.0.1:8131` với sample đa định dạng, nhiều độ dài, và báo riêng tệp chữ, ảnh, PDF scan cùng tải hỗn hợp.

**Architecture:** Mở rộng công cụ CCU hiện có bằng manifest sample có metadata thực tế. VU gửi một yêu cầu, chờ kết quả cuối, kiểm đầu ra rồi nghỉ 5–20 giây; tăng tải theo từng mức, giữ nguyên cấu hình dịch vụ khi đo.

**Tech Stack:** Python, runner mới `fixtures/tools/bench_ccu_manifest.py` tái sử dụng multipart/percentile từ `ccu_translate.py`, các thư viện sinh tài liệu đã dùng trong `gen_format_fixtures.py`, JSON và HTML tự chứa.

**Spec:** Yêu cầu và ma trận đã trình bày trong cuộc trao đổi ngày 13/10/2026. Người dùng đã yêu cầu chuẩn bị dữ liệu trong giới hạn production và chạy đo thật. Ma trận dưới đây cập nhật theo yêu cầu tối thiểu 10 trang và giới hạn đang chạy.

## Global Constraints

- Môi trường người dùng chọn: gateway production `:8131`; kết quả không đại diện riêng cho đường UI IAS.
- Giữ dataset/kết quả text cũ riêng; thêm 15 text trong ngân sách chunk thực tế, dùng preflight và mixed. Không gộp số đo khác môi trường.
- Không triển khai backend, restart container, thuê GPU hoặc nới rate limit/queue để cải thiện kết quả đo.
- Giữ dataset và bằng chứng cũ; sample và kết quả mới lưu vào thư mục mới.
- Nội dung sample là dữ liệu tổng hợp, thông tin đăng nhập giả; không in token hay đưa secret vào artifact.
- Các mức tải dự kiến: 5, 10, 20, 25, 30 VU; mỗi mức 3 lượt, mỗi lượt 180 giây gồm ramp 30 giây, nghỉ ít nhất 40 giây giữa lượt và chỉ chuyển lượt khi job benchmark đã kết thúc.
- Drain tối đa 600 giây; hết drain ghi timeout và theo dõi/hủy đúng các job benchmark còn chạy, không đụng job của người dùng khác.
- Ngưỡng so sánh cũ: p95 ngắn ≤30 giây, dài ≤120 giây, job nhận thành công ≥99%, không 5xx/lỗi kết nối/poll_error/timeout. Mức vừa dùng ngưỡng 120 giây tạm thời và ghi rõ giả định này.
- Tiêu chí 1–3 giây/trang và 20 trang ≤60 giây được báo riêng; không dùng số trang ước lượng chung cho mọi định dạng.

## Review Focus

- Poll hoặc lấy result gặp 429 phải tôn trọng Retry-After, có deadline, không biến văn bản rỗng thành kết quả thành công.
- Chỉ có job succeeded chưa chứng minh giữ thông tin: phải lấy result và kiểm literal/marker.
- Một nhóm không có đủ mẫu quan sát phải ghi thiếu dữ liệu, không tự kết luận PASS.
- DOCX/DOC không có số trang đã kiểm chứng và XLSX không có phân trang cố định: không suy ra trang/phút.
- Bộ đếm dùng chung qua proxy, sample lặp nội dung và tải nền có thể làm sai cách diễn giải CCU.

---

### Task 1: Kiểm tra môi trường và chốt baseline

**Files:** Đọc `ccu_translate.py`, `10-nhat-ky-toi-uu.txt`, `optimization/results/review-phuong-phap-do-20261011.md`; tạo `runtime.json`, `preflight-summary.json` và kết quả preflight trong `results.json` dưới `optimization/results/ccu-production-20261013/`.

**Interfaces:** Xuất preflight gồm URL, thời điểm, route/capability, các hạn chế quan sát được và hash sample/config benchmark; không chứa secret.

- [x] Đối chiếu số đo text đã có: endpoint, model, cấu hình và thời điểm; không gộp kết quả khác môi trường như cùng một phép đo.
- [x] Kiểm capability hiện tại; tại lần đọc trước, v3 không công bố hướng tiếng Anh. Chọn hướng đang bật cho text, không giả định mọi hướng hoạt động.
- [x] Smoke một sample hợp lệ mỗi nhóm qua 8131 và lấy result cuối; kiểm DOC converter, PDF, XLSX và OCR thực sự chạy được.
- [x] Ghi nhận rate limit và 429; client benchmark dùng một đường gọi qua proxy, không giả header IP để vượt giới hạn production. Không có 429 trong phép đo này.
- [ ] Chưa xác minh đầy đủ tải nền/GPU serving. Đã ghi runtime/limits/container image/start/restart; không dùng health gateway để chứng minh phần cứng hoặc isolation. Các smoke dependency đã thành công.

### Task 2: Bổ sung sample và manifest

**Files:** Tạo `module-map/translate/fixtures/tools/gen_ccu_fixtures.py`, `fixtures/ccu-within-limits/manifest.json`, `fixtures/tools/test_ccu_fixtures.py`. Tái sử dụng helper/thư viện sinh mẫu hiện có; không chạy generator cũ theo cách ghi đè fixture.

**Interfaces:** `load_samples(manifest_path: Path) -> list[dict]`; mỗi sample có `id`, `path` hoặc `text`, `route`, `group`, `format`, `size_class`, `source_lang`, `target_lang`, `bytes`, `chars`, `words`, `preserve_literals`. Tài liệu thêm `pages` và nguồn xác minh; XLSX thêm `rows`, `sheets`, `cells`; ảnh thêm kích thước và loại ảnh.

| Nhóm | Định dạng | Các mức |
|---|---|---|
| Text | Text trực tiếp v3, bộ mới trong giới hạn | 200 / 350 / 450 từ; đã kiểm source token và số chunk specialist |
| Tệp chữ | TXT | 2.000 / 5.000 / 10.000 từ |
| Tài liệu | DOCX, DOC, PDF có chữ | 10 / 30 / 60 trang đã xác minh |
| Bảng tính | XLSX | 1.000 / 3.000 / 4.900 dòng dữ liệu |
| Ảnh | PNG, JPG | 500 / 1.000 / 1.500 từ; ít / vừa / dày chữ |
| File scan | PDF scan | 10 / 30 / 60 trang |

- [x] Viết kiểm tra manifest: ID duy nhất, file tồn tại, metadata đúng, nhóm/độ dài hợp lệ; kiểm generator không thay file cũ.
- [x] Sinh ít nhất 5 sample khác nội dung cho mỗi ô định dạng × độ dài: mục tiêu 120 tệp/ảnh (8 định dạng × 3 mức × 5), ngoài 15 text mới trong giới hạn.
- [x] Dùng nhiều đoạn và nhiều literal khác nhau, hạn chế đoạn/ô lặp; báo tỉ lệ block trùng để thấy ảnh hưởng memo/cache.
- [ ] Chưa phủ nguồn CJK/RTL. Bộ đã đo dùng nguồn en/vi/fr, có đích ja/de và các hướng đang bật. Giới hạn này được báo trong summary; không khẳng định phủ mọi ngôn ngữ.
- [x] Bổ sung ca khó ngoài bộ chính: ảnh nghiêng/mờ/nền tối, PDF trộn scan và chữ, bảng nhiều sheet. Báo riêng, không trộn âm thầm vào baseline ảnh sạch.
- [x] Xác minh số trang PDF bằng parser, DOCX/DOC bằng bản render; nếu chưa có công cụ render thì ghi pages=null và giới hạn báo cáo tương ứng. DOC thiếu converter ghi BLOCKED, không đổi đuôi DOCX để giả DOC.
- [x] Chạy kiểm tra generator và manifest; smoke từng sample mới tuần tự trước khi đưa vào tải. Sample bị dịch vụ từ chối được báo, không xóa để làm đẹp kết quả.

### Task 3: Mở rộng runner CCU và kiểm chứng offline

**Files:** Tạo `fixtures/tools/bench_ccu_manifest.py` và `fixtures/tools/test_ccu_manifest.py`; giữ runner cũ để bảo toàn phép đo trước. Không sửa request path của backend.

**Interfaces:** Thêm CLI `--manifest`, `--scenarios files,images,scan,mixed`, `--allow-production`; `--levels` nhận số VU dương. `cycle` tiêu thụ record manifest; kết quả giữ `sample_id`, nhóm, format, độ dài, thời gian queue/total (gồm upload/poll/result, chưa tách upload riêng), HTTP, lỗi, integrity và số lần 429.

- [x] Viết test HTTP giả cho vòng đời upload → poll → result, poll/result 429 rồi phục hồi, 429 kéo dài tới deadline, terminal failed và thiếu result.
- [x] Viết test phân nhóm đúng, sample rotation có phủ từng ô, đầu ra mất literal bị đánh dấu, thiếu dữ liệu không PASS; test production vẫn bị chặn khi thiếu cờ chủ động.
- [x] Cho phép production chỉ khi truyền `--allow-production`, phù hợp lựa chọn 8131 của người dùng; giữ chặn mặc định.
- [x] Chọn mẫu theo ô định dạng/độ dài trước rồi luân phiên sample; phân bố ba mức độ dài đều nhau trong từng nhóm để đủ quan sát.
- [x] Lấy kết quả cuối, kiểm đủ literal và marker; tách thành công xử lý khỏi đạt integrity. Không coi nội dung thành công là chứng minh nghĩa dịch đúng.
- [x] Dùng thời gian monotonic và deadline cho mọi nhánh, kể cả 429; poll tối thiểu 2 giây, ưu tiên Retry-After, ghi mọi lần bị giới hạn.
- [x] Xuất JSON thô và HTML: mỗi lượt, số liệu gộp, p95 từng lượt và p95 gộp các kết quả được đặt tên rõ (runner mới dùng phân vị gộp, không lấy trung bình p95). Thông lượng tính theo thời gian có ghi nhận ramp/drain.
- [x] Chạy `python3 -m unittest discover -s module-map/translate/fixtures/tools -p 'test_ccu*.py'` và `git diff --check`; test phải mock dịch vụ, không tự gọi production.

### Task 4: Chạy đo thật theo từng giai đoạn

**Files:** Kết quả mới dưới `optimization/results/ccu-production-20261013/`; không ghi đè báo cáo cũ.

**Interfaces:** Mỗi lượt xuất JSON ngay khi kết thúc, gồm seed, manifest hash, cấu hình benchmark, URL, thời điểm và coverage. Kết quả kiểm nội dung giữ riêng với số liệu tải.

- [x] Preflight từng mẫu tuần tự (một request), và pilot VU5; xác nhận số đo/integrity, sửa sampler trước formal. Pilot lưu riêng, không gộp.
- [x] Đo riêng tệp chữ/bảng, ảnh, PDF scan, 3 lượt/mức theo stop rule: file/ảnh tới 30; scan tới 20 rồi dừng 25/30.
- [x] Đo hỗn hợp với tỉ trọng công bố: 25% text, 25% tệp chữ/bảng, 25% ảnh, 25% scan. Đây là tải cân bằng phục vụ đánh giá, chưa phải tỉ lệ sử dụng thực tế.
- [x] Mỗi ô định dạng × độ dài cần ít nhất 20 kết quả hoàn tất tại mức tải dùng để kết luận; thiếu thì chạy bổ sung hoặc ghi thiếu dữ liệu. Không dùng p95 của 1–2 mẫu để kết luận.
- [x] Ghi 429 là giới hạn tiếp nhận production; không nới giới hạn để tiếp tục. Nếu bị giới hạn, báo CCU phục vụ qua gateway, không suy ra trần GPU.
- [x] Dừng tăng tải của nhóm sau mức có lỗi hạ tầng/timeout; kết thúc đúng job benchmark trước khi chuyển lượt. Quy tắc này thận trọng hơn ngưỡng 5% ban đầu. Kết luận mức cao hơn chưa đo, không tự suy diễn.
- [x] Sau mỗi lượt chờ job benchmark kết thúc, theo dõi hàng đợi; nếu cần hủy chỉ dùng ID job do runner tạo. Hết lượt vẫn lưu lỗi và kết quả chưa hoàn tất.

### Task 5: Tổng hợp báo cáo và cập nhật map

**Files:** `optimization/results/ccu-production-20261013/report.html`, `summary.md`; thêm mục mới vào `module-map/translate/10-nhat-ky-toi-uu.txt` sau khi có số đo thật.

- [x] Báo coverage: bao nhiêu sample duy nhất đã chạy theo nhóm, format, độ dài, ngôn ngữ; mẫu thiếu/BLOCKED và lý do.
- [x] Báo CCU lớn nhất đã đo đạt tiêu chí cho từng nhóm và tải hỗn hợp; nếu mọi mức đều đạt thì chỉ ghi ≥ mức cao nhất đã đo.
- [x] Báo p50/p95 tổng và queue, request/tài liệu/phút; trang/phút chỉ cho tài liệu có số trang xác minh, dòng/ô cho XLSX, request hoặc ký tự cho text.
- [x] Báo riêng tỷ lệ xử lý thành công, giữ literal, lỗi/429; chấm chất lượng nghĩa chỉ trên mẫu có gold, dùng `quality_score.py` khi phù hợp và ghi mẫu không có gold chưa chấm nghĩa.
- [x] So với kết quả 11/10 có ghi rõ khác dataset, tỉ trọng, độ dài và đường gọi; không coi chênh lệch là cải thiện backend nếu không có phép so sánh cùng điều kiện.
- [x] Kiểm tổng hợp khớp JSON thô và báo cáo HTML đọc được; ghi kết quả thật vào nhật ký, kèm việc chưa đo và hạn chế dữ liệu tổng hợp.

## Thời lượng dự kiến

Một kịch bản đủ 5 mức × 3 lượt × 180 giây cần 45 phút tải, chưa gồm smoke, nghỉ, drain và chạy bổ sung coverage. Bốn kịch bản cần tối thiểu 3 giờ tải; thời gian tổng phụ thuộc tốc độ scan, giới hạn production và số ô còn thiếu quan sát. Việc chuẩn bị sample/runner thực hiện trước, không tính là thời gian đo production.

## Kết quả đã kết thúc

Đã đo qua production 8131: preflight 155 mẫu, 154 xử lý thành công/146 giữ đủ literal; formal 1.538 lần gửi, 1.528 hoàn tất, 48 lượt/16 mức trong khoảng 5,75 giờ (chưa gồm preflight/chuẩn bị). 128/135 mẫu chính xuất hiện trong tải; ca khó chỉ kiểm tuần tự. File/ảnh tới VU30; scan/mixed dừng tăng sau VU20 có lỗi. Scan có 7 job chạm hạn benchmark 780 giây và được hủy; mixed có 3 text HTTP504 TRANSLATION_TIMEOUT 120 giây. Không 429, registry active0. 24 kiểm thử công cụ đạt.

File xử lý ổn định trong các mức đã thử nhưng VU30 p95 570 giây; ảnh giữ literal 627/744 (84,3%); mixed10 là mức cao nhất đã thử không có lỗi xử lý trên tập sẵn sàng, mixed20 có timeout. Chưa xác nhận CCU đáp ứng đầy đủ SLA/chất lượng, chưa chấm nghĩa dịch. Đa số ô có ít n, nguồn dùng mẫu câu lặp, chưa có CJK/RTL nguồn hoặc xác minh tải nền GPU; các ô checklist còn mở phía trên là giới hạn bằng chứng được công bố, không phải một phép đo đang chạy.

Kết quả: `module-map/translate/optimization/results/ccu-production-20261013/{summary.md,report.html,results.json,assessment.json,verification.json}`. JSON gốc và mã thực sự dùng khi đo trong runner-snapshot được giữ nguyên. Sau formal mới sửa guard sau sleep để phân loại BENCH_DEADLINE đúng và fingerprint helper cho các phiên mới. Không thay backend, triển khai hoặc nới production. Diff theo phạm vi đạt; full-repository diff-check còn 11 trailing whitespace có sẵn ở routes.py ngoài tác vụ.
