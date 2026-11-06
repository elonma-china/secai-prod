# Kế hoạch: nâng tỉ lệ PoC chạy được (khắc phục khâu consolidate làm hỏng logic)

## Bối cảnh / nguyên nhân gốc (đo được 2026-10-15)

Test 2 CVE có artifact thật trong DB, tier `real_consolidated`, lab đều KHAI THÁC ĐƯỢC, nhưng mã phục vụ **không chạy được**:
- **CVE-2026-24061** (metasploit Ruby): bản hợp nhất tự viết lại máy trạng thái đàm phán telnet → thoát sớm, không bypass.
- **CVE-2026-27944** (BimaBalance Python): bản hợp nhất coi cả body `/api/backup` là ciphertext (thực tế là ZIP ngoài chứa `nginx-ui.zip`) → lỗi padding, không giải mã.

Nguyên nhân gốc nằm ở `src/secai_gateway/poc/consolidate.py`:
1. `consolidate_poc` nhờ **LLM viết lại chính BYTE mã thực thi** thành 1 tệp. LLM có thể "chạy được về cú pháp" nhưng **sai ngữ nghĩa/định dạng**.
2. Hai bộ kiểm hiện có đều **execution-free**:
   - `validation_failure` (compile + AST: cú pháp, import tương đối, tên chưa định nghĩa),
   - `fidelity_failure` (so CLI surface / hardcode bind).
   Cả hai **không chạy code** nên không bắt được lỗi ngữ nghĩa (đàm phán telnet sai, format backup sai).
3. Đường `slice_entry`/`prune` (deterministic, byte-là-verbatim) chỉ chạy được cho Python có cấu trúc; **entry non-Python (Ruby) hoặc slice thất bại thì rơi xuống LLM-merge** → đúng 2 ca bị hỏng.
4. Hệ quả: tier `real_consolidated` được gắn cho code **chưa từng chạy** → che giấu lỗi.

## Nguyên tắc sửa (không dựa vào "lấy y nguyên code trong DB")

Không có phương án "dump byte gốc" nào được coi là giải pháp. Mục tiêu: **mọi thứ phục vụ dưới nhãn real phải đã được chứng minh chạy**, còn code do hệ thống biến đổi/sinh ra phải qua **kiểm chứng thực thi** và **vòng tự sửa**.

### P0 — Phân tầng trung thực, không đánh nhãn "đã chạy" cho code chưa chạy
- Chỉ `section1_tier=real_verified` khi mã **đã vượt kiểm chứng thực thi** trên lab có `success_signal`.
- `real_consolidated`/`real_pruned` chỉ nói "nguồn thật, đã reshape" — banner phải ghi rõ **CHƯA kiểm chứng chạy**; không được đồng nhất với "khai thác được".

### P1 — Kiểm chứng thực thi (runtime oracle) trên lab
- Với mỗi CVE có lab (nguồn: `evals/exploit_lab/labs/<cve>/lab.yaml` + các entry `vulhub`/`lab` trong catalog), chạy PoC trong sandbox (`poc/runners/docker_exec`, timeout, chỉ mở tới target lab) và so `success_signal`.
- Tái dùng harness sẵn có: `evals/exploit_lab/run_lab_eval.py` (`run_poc_adaptive`, `verdict`, `feasibility`).

### P2 — Vòng tự sửa có phản hồi runtime (thay vì retry mù execution-free)
- Khi verify FAIL: đưa cho LLM (profile `poc_consolidate`) **mã hiện tại + stderr/stdout thực tế + `success_signal` mong đợi + mô tả lab**, yêu cầu sửa đúng lỗi, rồi **verify lại**. Lặp bounded `N` vòng (`poc_consolidate_max_attempts`).
- Đây chính là cách `run_lab_eval.repair()` đã làm cho eval → nâng lên thành cơ chế trong pipeline (offline, cache lại).

### P3 — Giảm xác suất hỏng từ gốc: "hợp nhất không viết lại byte thực thi"
- Áp nguyên tắc của `poc/slicer.py` ("LLM không bao giờ phát sinh byte code; output là tập con verbatim") cho cả đường merge:
  - entry **non-Python đơn tệp** hoặc **slice thất bại**: dựng tệp gộp **deterministic** (nối byte nguồn verbatim + header/runner), không cho LLM viết lại logic.
  - đa tệp: LLM chỉ quyết định giữ/bỏ + nối, byte code lấy từ nguồn; **thay đổi ở byte thực thi bị từ chối**.
- Mục 2 (how-to) vẫn do LLM viết từ `cli_surface` (đã có ở `fidelity.py`).

### P4 — Cache + rollout an toàn
- Kết quả đã verify lưu `poc_canonical` (đã có bảng) để không phải chạy lại mỗi request; chỉ nâng tier khi bytes khớp bản đã verify.
- Bật `poc_consolidate_verify` theo feature-flag; mặc định off để không đổi hành vi production cho tới khi có đủ lab coverage.

## Thứ tự thực hiện & kiểm chứng
1. P0/P1: thêm tier `real_verified` + oracle. (đo bằng harness)
2. P2: nối vòng repair-runtime; đo lại tỉ lệ khai thác trên bộ lab 2026.
3. P3: chuyển merge sang deterministic; đo lại + regression unit test (`tests/test_consolidate.py`, `test_multifile`).
4. P4: cache + flag.

## Trạng thái phiên này
- Đã dựng 2 lab 2026 (`inetutils/CVE-2026-24061`, `nginx-ui/CVE-2026-27944`) — cả hai xác nhận khai thác được.
- Sẽ chạy vòng **verify + repair runtime** (P1+P2) cho 2 served PoC để chứng minh cơ chế nâng được tỉ lệ thành công mà KHÔNG lấy y nguyên code DB; kết quả ghi ở `results/`.

## Kết quả thực hiện (2026-10-15)

### Chẩn đoán đo được (khác giả thuyết ban đầu)
- **CVE-2026-27944**: artifact GỐC trong catalog (`BimaBalance/27944.py`) **giống hệt** bản hệ thống phục vụ (diff chỉ ở docstring) và **cũng sai với lab** (coi cả body `/api/backup` là ciphertext). → **consolidate KHÔNG làm hỏng** ca này; lỗi thật là **chọn artifact chưa kiểm chứng + thiếu verify runtime**.
- **CVE-2026-24061**: consolidate **viết lại** module metasploit (phụ thuộc framework, không chạy standalone) thành Ruby standalone → **hỏng máy trạng thái đàm phán telnet** ⇒ lỗi thật ở **consolidate viết lại byte thực thi + chọn nguồn framework**.
- Ngoài ra: `poc_github` cho 24061 (`jacubes/CVE-2026-24061/rce.py`) **là malware** (tải `main.exe` từ C2 `2.27.63.236`) ⇒ "có PoC thì đưa ra" tuyệt đối phải qua **relevance-gate + verify**, không tin tên nguồn.

### Đã làm
1. **Công cụ offline `scripts/poc_verify_repair.py`** (P1+P2): lấy mã hệ thống phục vụ → chạy nhắm lab → fail thì đưa lỗi runtime + mô tả lab cho LLM tự sửa → chạy lại (bounded, `--rounds`) → thành công thì lưu vào store verified (`poc/fixed_pocs.py` shape, `fixed=True`). Không hardcode; mã ra là bản **đã biến đổi**, không phải byte gốc.
   - Kết quả: **CVE-2026-27944 hội tụ sau 1 vòng** → lấy được `JwtSecret`, `crypto.Secret`, `node.Secret` trên lab thật; bản verify lưu `/tmp/verified_fixed/CVE-2026-27944.json`.
   - CVE-2026-24061: chưa hội tụ trong N vòng (model sửa đàm phán telnet chưa đúng) — cần prompt/hint negotiation cụ thể hơn hoặc thêm nguồn standalone sạch.
2. **Sửa bug wiring store verified (P4/P0)**: `poc/fixed_pocs.py::resolve_fixed_dir` trả `/srv/pocs-fixed` (không mount, không tạo được) trong khi store thật ở `/srv/poc/pocs-fixed` (đang chứa `CVE-2017-5638.json`, `CVE-2026-42533.json`…) ⇒ **mọi PoC đã verify của operator bị bỏ rơi**. Đã ưu tiên dir cạnh `poc_catalog_db` khi nó tồn tại. Có test hồi quy (`tests/test_fixed_pocs.py`).

### Còn lại (chưa làm)
- P3: chế độ **lossless** trong `consolidate.py` (LLM không viết lại byte thực thi) — cần cho 24061.
- P2: nối vòng verify+repair vào job offline tự động (hiện chạy tay), và **chọn nguồn standalone** trong `prefetch.py::_rank` (hạ metasploit/kèm verify).
- P0: tier `real_verified` khi bytes khớp bản đã verify.

