# Tiêu chí nghiệm thu gói `stack/`

Một bộ ca kiểm chạy hai đường: **UI** (Playwright, giống người dùng thật trên giao diện IAS) và **API** (`run-api.sh`, gọi thẳng gateway, dùng cho stack smoke nơi frontend sản phẩm chưa trỏ tới). Cả hai đọc cùng `acceptance/cases.json`, nên input và điều kiện đạt luôn khớp nhau. Nguồn sự thật cho input/kỳ vọng là `cases.json`; bảng dưới là bản đọc được.

Giao diện production (https://ias.id.vn) là frontend sản phẩm IAS, nằm ngoài repo này; nó gọi backend IAS (`/api/ais/...`), backend gọi gateway :8131. Menu "Nền tảng AI" có 4 trang dùng gateway: `/ai/chat-bot`, `/ai/translate`, `/ai/exploit`, `/ai/report`. Trang `/ai/evidences` ngoài phạm vi. Không còn exploit v1 (gỡ ngày 2026-11-20): trang "Sinh mã khai thác" là **PoC v2**. Bộ `test/ai-ui-tests/` cũ (viết cho Devspace, có ca v1) không dùng làm tiêu chí.

## Bảng ca kiểm

| ID | Trang / ô nhập | Input | Bấm | Đầu ra phải có | Hạn |
|---|---|---|---|---|---|
| UI-CHAT-1 | `/ai/chat-bot`, ô `Nhập nội dung...` | `CVE-2021-44228 là lỗ hổng gì? Nêu sản phẩm bị ảnh hưởng, điểm CVSS và cách khắc phục.` | `Gửi` | Chứa `Log4Shell` hoặc `Log4j`, `10.0` (CVSS), `2.16.0` hoặc `2.15.0` (bản vá); không có `ProviderError`/`Connection error`; ≥ 500 ký tự | 90 s |
| UI-CHAT-2 | như trên | `CVE-2099-99999 là lỗ hổng gì?` (không tồn tại) | `Gửi` | Nói rõ không có dữ liệu; **không** bịa CVSS/sản phẩm | 90 s |
| UI-TR-1 | `/ai/translate` tab `Dịch trực tiếp`, nguồn `English` → đích `Việt Nam`, ô `Nội dung cần dịch` | `A remote code execution vulnerability in Apache Log4j2 allows an unauthenticated attacker to run arbitrary code on the server. Default credentials are admin / P@ssw0rd123.` | `Dịch` | Ô `Kết quả dịch` không rỗng, là tiếng Việt (có `lỗ hổng`, `máy chủ`), **giữ nguyên** `Apache Log4j2` và `admin / P@ssw0rd123` | 60 s |
| UI-TR-2 | `/ai/translate` tab `Dịch tệp và hình ảnh`, `English` → `Việt Nam`, `Chọn tệp` | `stack/acceptance/fixtures/sample-en.docx` (1 trang) | tải lên | Job hoàn tất (thẻ `Dịch hoàn tất`), có nút `Xem trước`, tệp tải về mở được, chứa tiếng Việt | 5 phút |
| UI-TR-3 | như UI-TR-2 | `stack/acceptance/fixtures/sample-en.png` (ảnh chữ, cần OCR) | tải lên | Như UI-TR-2. **Chỉ áp dụng khi `.env` có `TRANSLATE_V2_OCR_BASE_URL`**; nếu không: ghi "bỏ qua, chưa cấu hình OCR" (`STACK_HAS_OCR=0`) | 5 phút |
| UI-RPT-1 | `/ai/report`: `Từ ngày`/`Đến ngày` mặc định, chọn `Thiết bị`, template `1. Tổng quan, 2. Chi tiết`, định dạng `docx` | — | `Phân tích thông minh` | Khối `Kết quả phân tích` có tiêu đề `BÁO CÁO ĐÁNH GIÁ AN TOÀN THÔNG TIN`, mục `1. Tổng quan` và `2. Chi tiết`, khoảng ngày đúng với hai ô ngày; `Tải báo cáo` trả tệp .docx mở được | 120 s |
| UI-POC-1 | `/ai/exploit`: ô `Nhập CVE...` = `CVE-2022-22963`, ô `Nhập target...` = `192.168.96.7:8080` | — | `Sinh PoC` | Terminal lần lượt có `[check_cve]… tìm thấy`, `[check_target]… reachable ✓`, `[pool]Khởi tạo engine`, `[init]session ses_…`; kết thúc có **một** trong hai khối kết quả (xem "Ghi chú lệch" bên dưới) | 30 phút |
| UI-POC-2 | như trên, CVE `CVE-2026-27944`, target `192.168.96.21:9000` | — | `Sinh PoC` | như UI-POC-1 | 30 phút |
| UI-POC-3 | như trên, target `192.168.96.250:9` (không có máy) | — | `Sinh PoC` | Dừng ở `[check_target]` với thông báo không kết nối được; **không** chiếm slot, không treo | 60 s |

Ghi chú lệch so với mô tả ban đầu (đo trên UI thật 2026-11-23):

- Khoảng ngày mặc định của `/ai/report` là **60 ngày** (24/09 → 23/11/2026), không phải 30. Kiểm tra dùng đúng giá trị đọc từ hai ô ngày (`từ dd/mm/yyyy đến dd/mm/yyyy` phải có trong kết quả), nên không phụ thuộc số ngày mặc định.
- Tiêu đề thực tế là `BÁO CÁO ĐÁNH GIÁ AN TOÀN THÔNG TIN HỆ THỐNG` (chứa chuỗi yêu cầu). `Tải báo cáo` sinh `.docx` phía client từ văn bản kết quả (không có request mới tới `/api/`).
- Dịch tệp: UI chỉ cho tải bản dịch dạng **`Tải TXT` / `Tải JSON`** (trong khung xem trước), không có nút tải lại đúng định dạng `.docx`. "Tệp tải về mở được, chứa tiếng Việt" được kiểm trên tệp `.txt` đó. Chọn nguồn `English` thì ô đích bị khoá `Việt Nam` (một đầu luôn là tiếng Việt).
- PoC v2: khối **`Kết quả phiên chạy`** (trạng thái, `Run ID`, `Code`, `Reason`) chỉ xuất hiện ở ca **thất bại**. Ca **thành công** hiện biểu ngữ `Khai thác THÀNH CÔNG — đã verify chạy được`, khối `File exploit`, `Cách chạy`, `Thông tin lỗ hổng CVE`. `cases.json` chấp nhận một trong hai cấu trúc (`File exploit … Cách chạy` hoặc `Run ID … Code … Reason`).
- Mỗi ca chat mở **cuộc trò chuyện mới** (nút `＋ Cuộc trò chuyện mới`) để không nhiễm ngữ cảnh ca trước. Việc này để lại các phiên mới trong tài khoản test.

## Quy tắc đạt

- Chat, dịch, báo cáo: mọi ca đạt đúng cột "Đầu ra phải có".
- PoC v2 **không** tính đạt theo 1 lần chạy (lần thử ngày 2026-11-23 trên prod từng cho `FAILED`, watchdog 90 s ở STAGE 2 — kết quả dao động theo tải LLM). Ca UI-POC-1/2 chỉ kiểm **luồng và cấu trúc đầu ra**; chất lượng tính bằng N=24 ×3 ở Task 8: exploit ok ≥ 71/72 và PoC chạy lại ra bằng chứng ≥ 27/72.
- **So với prod:** mỗi ca chạy trên prod trước (số gốc), rồi trên stack; stack đạt khi không ca nào prod đạt mà stack trượt.
- Thêm RAG: `evals/core_eval/run_core_eval.py` với `core_eval_set.jsonl` (100 câu) — điểm stack ≥ điểm prod cùng ngày. Chấm cơ học bằng `acceptance/core_score.py` (xem bên dưới).

## Cách chạy

Điều kiện: `stack/acceptance/node_modules` là symlink tới `secai-agent-gateway/test/ai-ui-tests/node_modules` (đã gitignore, không commit); Chromium của Playwright đã cài (`npx playwright install chromium` nếu thiếu).

```sh
cd stack/acceptance
# Thông tin đăng nhập UI chỉ qua env, không ghi vào tệp trong repo, không in ra:
set -a; . /đường/dẫn/tệp-0600-ngoài-repo; set +a      # đặt IAS_UI_USER, IAS_UI_PASS
export ACCEPT_OUT=../../local/stack-acceptance/$(date +%Y%m%d)-prod   # hoặc -stack
# UI (mặc định https://ias.id.vn; đặt PLAYWRIGHT_BASE_URL cho stack):
./node_modules/.bin/playwright test --grep "UI-CHAT|UI-TR-1|UI-RPT|UI-POC-3"
./node_modules/.bin/playwright test --grep "UI-POC-1"     # mỗi ca PoC chạy RIÊNG, lần lượt, không song song
# API (không cần khoá; gateway :8131 không có xác thực):
SKIP_CASES="UI-POC-1 UI-POC-2" ./run-api.sh http://127.0.0.1:8131
# RAG:
python3 ../../evals/core_eval/run_core_eval.py --base http://127.0.0.1:8131 --tag prod --out $ACCEPT_OUT/core_eval.json [--only core-001,...]
python3 core_score.py $ACCEPT_OUT/core_eval.json [stack.json]
```

- Fixture (đã commit): `acceptance/fixtures/sample-en.docx`, `sample-en.png`; sinh lại bằng `python3 acceptance/make_fixtures.py` (cần `python-docx`, `Pillow`).
- Kết quả: mỗi ca ghi `<id>.json` (`pass`, `reason`, 600 ký tự đầu ra, `endpoints`, thời gian) và ảnh chụp `<id>.png` vào `local/stack-acceptance/<ngày>-<prod|stack|api>/` (đã bị git bỏ qua; **không commit**). Ca PoC ghi thêm `<id>.page.txt` (toàn bộ terminal) và `<id>.sse.txt` (SSE thô).
- `run-api.sh` thoát 0 khi không ca nào trượt (ca bỏ qua không tính). `SKIP_CASES` để bỏ ca (mỗi ca PoC chỉ chạy 1 lần trên LLM dùng chung), `STACK_HAS_OCR=0` bỏ UI-TR-3.
- Cú pháp kỳ vọng trong `cases.json`: `a|b` = một trong các chuỗi; `re:<regex>` = regex; không phân biệt hoa thường.

## Endpoint mà mỗi trang gọi

Bắt bằng `page.on('request')` trên prod. Bộ lọc `/ai/` ở mô tả ban đầu không khớp: trình duyệt không nói chuyện trực tiếp với gateway mà qua **backend IAS** (`/api/ais/...`, header `Authorization` do backend IAS cấp khi đăng nhập — cơ chế đăng nhập IAS, không phải khoá gateway). Cột phải là đường gateway tương ứng, xác nhận qua log container gateway/translation-gateway trong lúc chạy ca. `run-api.sh` gọi đúng cột phải.

| Trang | Trình duyệt → backend IAS | Gateway (`run-api.sh` gọi) |
|---|---|---|
| `/ai/chat-bot` | `POST /api/ais/sessions`, `POST /api/ais/sessions/{sid}/chat/stream` (nạp thêm `GET /api/ais/projects`, `GET /api/ais/sessions`, `GET .../messages`) | `POST /sessions`, `POST /sessions/{sid}/chat/stream` (SSE `token`/`final`) |
| `/ai/translate` Dịch trực tiếp | `POST /api/ais/translate` | `POST /ai/translate` |
| `/ai/translate` Dịch tệp | `POST /api/ais/v2/translate/files`, `GET /api/ais/v2/translate/jobs/{job}` | `POST /ai/v2/translate/files`, `GET /ai/v2/translate/jobs/{job}`, `GET .../result` |
| `/ai/translate` Dịch ảnh | `POST /api/ais/v2/translate/images`, `GET /api/ais/v2/translate/jobs/{job}` | `POST /ai/v2/translate/images`, `GET .../jobs/{job}`, `GET .../result` |
| `/ai/report` | `POST /api/ais/report-ai` (multipart: `from_date`, `to_date`, `scan_type`, `template`, `export_type`, `templateFile`) | `POST /ai/generate-template-report` (multipart; `run-api.sh` gửi `data`, `template_text`, `from_date`, `to_date`, `summary` synthetic, `output_format=markdown`) |
| `/ai/exploit` | `POST /api/ais/stream` (SSE; sau khi xong FE còn gọi `POST /api/ais/sessions` + `.../chat/stream` để sinh khối "Thông tin lỗ hổng CVE") | `POST /ai/exploit-v2/stream` (SSE `step`/`log`/`final`) |

Giới hạn của đường API: backend IAS gom dữ liệu quét thật rồi mới gọi gateway làm báo cáo; `run-api.sh` thay bằng dữ liệu tổng hợp cố định, nên chỉ kiểm cấu trúc/ngày, không kiểm số liệu. Với PoC, `run-api.sh` **không** dùng chuỗi hiển thị của FE: nó kiểm thẳng trên luồng SSE của `/ai/exploit-v2/stream` theo trường `api` của từng ca trong `cases.json` (các `step` phải có/không có, trạng thái `error` của `check_target` ở UI-POC-3, và các trường của sự kiện `final`: `run_id` bắt buộc; `ok=true` thì phải có `code` và `run_instructions`; `ok=false` thì phải có `reason`; UI-POC-3 phải `ok=false`, có `reason`, `run_id` rỗng). Dòng `final ok=… run_id=…` trong `output_head` chỉ để người đọc, không được dùng để chấm. Không có khoá API gateway cần cấu hình.

## Số gốc (prod, 2026-11-23, image `main-033b2aa3-20261123`)

UI chạy trên https://ias.id.vn; API chạy trên http://127.0.0.1:8131. Kết quả thô: `local/stack-acceptance/20261123-prod/{ui,api}/` (không commit).

| Ca | UI | API | Ghi chú |
|---|---|---|---|
| UI-CHAT-1 | đạt (27 s) | đạt (10 s) | |
| UI-CHAT-2 | đạt (15 s) | đạt (4 s) | trả lời "Không tìm thấy thông tin…" |
| UI-TR-1 | đạt (9 s) | đạt (1 s) | giữ nguyên `Apache Log4j2` và `admin / P@ssw0rd123` |
| UI-TR-2 | **trượt** | **trượt** | job `failed` sau < 1 s: `TRANSLATION_UNAVAILABLE`. Nguyên nhân quan sát được: dịch vụ chuyên dụng `127.0.0.1:18868` (Hy-MT2) từ chối kết nối trên host prod |
| UI-TR-3 | **trượt** | **trượt** | job `failed`: `OCR_UNAVAILABLE`. `127.0.0.1:18083` (OCR) từ chối kết nối; `TRANSLATE_V2_OCR_BASE_URL` có cấu hình nên ca áp dụng |
| UI-RPT-1 | đạt (26 s) | đạt (7 s) | khoảng ngày `24/09/2026`–`23/11/2026`, `.docx` hợp lệ (zip có `word/document.xml`) |
| UI-POC-1 | đạt cấu trúc (914 s) | không chạy | **thành công** (`ok=true`, stage 3.3, có code + `Cách chạy`); terminal vẫn in cảnh báo `[watchdog] Agent không có tiến triển mới trong 90s`. Lần chạy gốc báo trượt vì kỳ vọng ban đầu đòi khối `Kết quả phiên chạy`; chấm lại offline từ `UI-POC-1.page.txt` sau khi sửa kỳ vọng (không chạy lại) |
| UI-POC-2 | **chưa có baseline hợp lệ** | không chạy | Mục tiêu đúng là `192.168.96.21:9000` (nginx-ui; host 19000). Lần chạy đầu dùng nhầm cổng 80 (không có dịch vụ nghe) → dừng ở `[check_target]`, **không phải kết quả hợp lệ** (lỗi của đặc tả, đã bỏ; giữ ở `wrongport80-UI-POC-2.*`). Chạy lại một lần với `:9000`: `[check_target] 192.168.96.21:9000 reachable ✓`, `[pool]Khởi tạo engine`, `[init]session ses_df783d7f…`, agent bắt đầu chạy lệnh; sau **97 s** trình duyệt báo `Không thể kết nối tới API stream` / `Quá trình xử lý kết thúc với lỗi`, không có `final` (gateway ghi `client gone … grace 45s`, container gateway không khởi động lại). Đứt luồng phía client/đường truyền, kết quả khai thác **chưa xác định**; không tính là đạt hay trượt về nội dung. Theo quy tắc mỗi ca PoC chỉ chạy một lần nên không chạy lại. Baseline prod cho POC-2 lấy từ lần chạy N=24 xen kẽ prod/stack ở Task 8 (cùng lab); cho tới lúc đó POC-2 không được tính là đạt cũng không được loại như ca miễn |
| UI-POC-3 | đạt (13 s) | đạt (4 s) | dừng ở `[check_target]` "Không kết nối được tới 192.168.96.250:9" |

Hệ quả cho so sánh stack vs prod: UI-TR-2/3 là **ca prod trượt** (MT/OCR đang tắt) nên không dùng để kết luận "stack trượt khi prod đạt" (nhưng xem điều kiện hoàn tất bên dưới); stack phải đạt các ca còn lại. UI-POC-2 **không phải ca được miễn**: chưa có baseline hợp lệ nên chưa tính đạt, cũng không bị loại khỏi so sánh; baseline prod của nó đến từ lần chạy N=24 xen kẽ prod/stack ở Task 8 (cùng lab, CVE-2026-27944 @ 192.168.96.21:9000), và chỉ khi có số đó mới so được stack với prod cho ca này. **Nhưng điều kiện hoàn tất gói**: stack phải được chứng minh **ĐẠT UI-TR-2 và UI-TR-3** (dịch tệp và dịch ảnh, nhánh thành công: job hoàn tất, `Xem trước`, tệp tải về chứa tiếng Việt) trước khi gói được gọi là hoàn chỉnh; nếu không, đường dịch tệp được giao mà chưa được chứng minh. Dịch tệp/ảnh trên prod đang hỏng là phát hiện cần báo cho chủ dự án; chạy lại baseline khi tunnel dịch/OCR được dựng lại.

Ca PoC chạy **một lần mỗi ca** trên UI (UI-POC-1, -2, -3; riêng POC-2 xem trên) và API chỉ chạy UI-POC-3 (`SKIP_CASES="UI-POC-1 UI-POC-2"`), vì LLM dùng chung.

### RAG core eval (đủ 100 câu)

`run_core_eval.py` tuần tự, một request một lúc, đúng thứ tự `core_eval_set.jsonl`, trên `http://127.0.0.1:8131` (2026-11-23, ~16 phút, thô: `core_eval_full100.json`; lần chạy 20 câu đầu trước đó giữ ở `core_eval_first20.json`). Script gốc chỉ ghi câu trả lời; `acceptance/core_score.py` là **kiểm tra chuỗi con cơ học**, khác với điểm chất lượng của chính bộ eval (xem docstring): trả lời được (HTTP 200, ≥ 50 ký tự) và các giá trị `facts.cve`/`facts.cvss`/`facts.catalog_poc_count` có nguyên văn trong câu trả lời.

| Chỉ số (prod, 100 câu) | Số gốc |
|---|---|
| Trả lời được | 100/100 |
| Có đáp án máy kiểm được (`cve_lookup`, `cve_alias`, `poc`, một phần `multilingual`/`followup`) | 33 câu, **29/33 khớp** |
| Câu trượt kiểm chuỗi | `core-020` (cve_alias), `core-042`, `core-043`, `core-050` (nhóm `poc`: số PoC trong catalog) |
| Thời gian trung bình / p95 (nearest-rank) | 9.6 s / 17.2 s |

Điều kiện so sánh: "điểm stack ≥ điểm prod cùng ngày" chạy lại **đúng 100 câu** này trên stack bằng `core_score.py prod.json stack.json`. Lưu ý độ nhiễu: 20 câu đầu cho 17/20 ở lần chạy 20 câu và 19/20 ở lần chạy 100 câu trên cùng prod (`core-014`/`core-015` đổi từ trượt sang khớp), tức số cơ học dao động ±2 câu giữa hai lần chạy giống hệt. Nên so stack với prod chạy **cùng ngày, cùng điều kiện**, và coi chênh ≤ 2 câu là trong nhiễu; số này không thay cho điểm chất lượng ngữ nghĩa.
