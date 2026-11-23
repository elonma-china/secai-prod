# Nghiệm thu exploit-v2 N=24 xen kẽ prod/stack (2026-11-24): DỪNG GIỮA CHỪNG theo luật abort

Task 8 Step 6. Nhánh `feat/stack-package`, image dựng từ HEAD `ef07d511` (tag `*:ef07d511-smoke-20261124`, đã gỡ).
Kết luận: **chưa đánh giá được ngưỡng** (không PASS, không FAIL). Loạt 6 lần chạy dừng ở prod r2 vì luật abort
"chat lỗi > 50 % suốt 3 phút" kích hoạt; nguyên nhân quan sát được là đường mạng quốc tế tới máy LLM Vast mất gói,
không phải do stack. Chỉ có **1 lần đầy đủ mỗi bên** (prod r1, stack r1); ngưỡng tính trên 72 nên không áp được.

## Cách đo (giữ nguyên cách cũ của `exploit-v2-vm-20261123.md`)

- Cùng lab nginx-ui CVE-2026-27944 `http://192.168.96.21:9000` (host và mạng `poc-labs-internal` của stack đều trả 200),
  cùng LLM prod (Vast 62088958), cùng script `prod_ramp.py` (bản có lưu `final` đầy đủ), N=24 exploit + 24 luồng chat,
  `PR_MAX_LEVEL_S=1200`, chat timeout 30 s, nghỉ 60 s giữa các lần. Bản sao script chỉ đổi target, LLM base, nguồn khoá.
- Prod: `secai-docker-gateway` (image `main-033b2aa3-20261123`, 24 container `pentestcode-N`). Không dừng/đụng gì ở prod,
  kể cả `secai-ev2-vm-eval-a/b`.
- Stack: project `secai-stack-smoke`, profile core + `exploit` (vm-a/vm-b, 2 × 12 slot), mạng bridge `secai-net`
  (override subnet `10.231.0.0/24` vì host hết dải địa chỉ Docker), `POC_LABS_EXTERNAL=true`,
  `POC_LABS_NETWORK=poc-labs-internal`, `VERIFY_ALLOWED_TARGETS=192.168.96.0/20`, `VERIFY_ENABLED=false` (như smoke 8b;
  prod để `true`). LLM/embedding/rerank = giá trị của prod, đọc chỉ-đọc từ env container prod, không in ra.
  `DATA_ROOT` riêng (symlink `:ro` + bản copy `poc`), Qdrant tập con 228 điểm (CVE lab, products liên quan,
  CWE-79..98) scroll chỉ-đọc từ prod. `check.sh all` xanh trước khi chạy.
- Chất lượng PoC: `analyze_quality.py --rerun` y như cũ: chạy lại **code người dùng nhận được** trong container tạm
  (không mount, cap-drop ALL, user nobody, timeout 90 s), PASS khi output có bằng chứng impact (regex của gateway).

## Kết quả

| Chỉ số | prod r1 | stack r1 | prod r2 (dừng ở ~3 phút) |
|---|---|---|---|
| Exploit `ok` | 24/24 | 24/24 | 5 xong (đều ok, không code), 19 bị cắt |
| **PoC trả về chạy lại ra bằng chứng** | **0/24** | **8/24** | không chấm |
| Phiên có `code` / trong đó không phải code | 3 / 3 (2 file handoff markdown, 1 response HTTP) | 9 / 0 (1 code lỗi `false` của JSON) | — |
| Stage cuối | 3.2 ×24 | 3.2 ×21, 3.3 ×3 | — |
| watchdog / engine_retry | 1 / 0 | 0 / 0 | — |
| Exploit thời gian p50 / max | 134 / 451 s | 97 / 343 s | — |
| Chờ slot (pool → init) p50 / max | 0,8 / 2,1 s | 1,0 / 1,5 s | — |
| Chat: số gọi, lỗi | 786, 9,9 % (61 timeout, 17 HTTP 503) | 908, **0 %** | 278, 79,5 % (150 HTTP 503, 71 timeout) |
| Chat thành công p50 / p95 | 10,9 / 23,0 s | 8,2 / 16,1 s | 17,4 / 26,0 s |
| LLM hàng chờ max / KV max | 15 / 0,99 | 21 / 1,00 | 10 / 0,98 |
| MemAvailable thấp nhất / OOM | 29,9 GiB / 0 | 23,4 GiB / 0 | 23,8 GiB / 0 |
| `/health` gateway mục tiêu không ok | 0/60 mẫu | 0/49 mẫu | 0/21 mẫu |

So với ngưỡng (cố định, không sửa): exploit ok ≥ 71/72 **và** PoC chạy lại ≥ 27/72. Stack mới có 24/24 và 8/24 trên
1/3 khối lượng. Nhịp 8/24 (33 %) gần số VM cũ 27/72 (38 %), nhưng một lần chạy không đủ để kết luận. Prod 0/24 thấp hơn
số cũ 7/72; không suy ra gì từ một lần.

### UI-POC-2 (baseline từ lần chạy prod, theo `ACCEPTANCE.md`)

Chấm cấu trúc `final` giống `run-api.sh` (`run_id` có; `ok=true` thì phải có `code` và `run_instructions`; `ok=false`
thì phải có `reason`): **prod r1 3/24**, stack r1 9/24. 21/24 phiên prod là `ok=true` nhưng `code: null` (soft-pass), nên
theo luật cấu trúc hiện tại prod chỉ đạt ~1/8 lần. Baseline UI-POC-2 của prod vì thế **không ổn định** (đa số lần
trượt cấu trúc); stack không kém prod ở lần này. Chưa đủ dữ liệu cho r2/r3.

## Vì sao dừng

- Trước khi chạy (01:29–01:53): mất gói 40–90 % trên đường quốc tế tới `148.113.245.56` (mtr: mất từ hop 9–12, ngoài
  máy này; ping 1.1.1.1 0 %). Chat prod lúc rảnh timeout 14/15 lần. Đã **chờ**, không chạy, tới khi 3 mẫu liên tiếp
  sạch (0 % mất gói, chat 5,6 s) rồi mới bắt đầu lúc 01:55.
- prod r1 (01:55–02:04) và stack r1 (02:05–02:12) chạy trọn. prod r2 bắt đầu 02:13:20; lúc 02:16:09 guard thấy 3 phút
  liền chat lỗi > 50 % (24/24, 82/100, 111/141) và dừng loạt. Lỗi chủ yếu HTTP 503 từ `/ai/chat` (`routes.py`:
  lỗi provider/runtime → 503); log prod cùng lúc có `embed-failed:RemoteProtocolError`. `/health` prod vẫn 200 suốt
  thời gian chạy; RAM không thiếu; không OOM.
- Không chạy lại, không đổi luật/ngưỡng. Stack và prod dùng chung đường mạng nên lỗi mạng ảnh hưởng cả hai.

## Lưu ý khi đọc số

- **Nhiễu mạng/LLM dùng chung**: mạng tới Vast chập chờn trong cả khung đo; mỗi bên chỉ 1 lần, thứ tự prod trước stack
  sau. Chênh chat 9,9 % vs 0 % có thể do khung thời gian chứ không do hệ.
- **Stack nhẹ hơn ở RAG**: Qdrant stack chỉ 228 điểm, prod 829k; chat stack tìm kiếm ít hơn. Không dùng số chat để so
  chất lượng RAG.
- Prod chạy 24 container cũ (docker exec mỗi lệnh), stack chạy VM 2×12; đây là so sánh gói, không phải cùng engine.
- `VERIFY_ENABLED` khác prod (stack false).
- `/health` prod trước khi chạy đã `degraded` (embedding/rerank timeout, `kb_sync` cũ); guard bỏ qua các mục đó và chỉ
  kiểm llm/redis/qdrant/neo4j.

## Dữ liệu và dọn dẹp

- Kết quả thô (không commit): `local/n24-20261124/` trong worktree (`runs/*/N24/`, `series.log`, `guard.log`,
  `precheck.log`, `quality.out`, bản sao script).
- Teardown: `docker compose -p secai-stack-smoke down -v`; gỡ đúng 2 tag `secai-agent-gateway:ef07d511-smoke-20261124`,
  `secai-pentestcode-vm:ef07d511-smoke-20261124`; xoá `~/secai-stack-smoke-data` (bằng container root dùng một lần vì
  `poc` thuộc uid 10001); xoá bản sao `stack/` có `.env`/`secrets`. Không prune toàn host. Danh sách container prod
  trước/sau giống nhau (145, không tính container build tạm của chính lần build này); prod `/health` 200.
