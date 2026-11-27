# Baseline vận hành ngày 2026-11-27

Lần chấm đầu tiên theo [tiêu chí vận hành](../../ops/OPS-CRITERIA.md), chạy trên máy prod lúc 20:09 (+07):

```sh
git fetch -q origin            # origin/main = 2dcff27c
python3 -I scripts/ops-check.py --json > ops.json   # mã thoát 1
```

Checker chỉ đọc (docker ps/inspect/images/system df, gh api GET, df, systemctl show/list-*, git chỉ đọc, đọc file). Không đổi container, file hay cài đặt GitHub nào. Lệnh chạy từ worktree `feat/ops-criteria`, nên năm tiêu chí về chính bộ tiêu chí này (OPS-REL-3, OPS-DOC-1, OPS-DOC-2, OPS-DOC-5, OPS-DOC-6) đạt nhờ nhánh. Chạy từ `main` trước khi merge thì cả năm sẽ FAIL.

Trạng thái máy đổi trong lúc đo: lần chạy thử lúc 19:13 thấy 60 tag `secai-agent-gateway` (47 tag ngoài quy ước gc) và đĩa `/` 93%. Tới 19:50 có người khác (ngoài phiên này) đã xoá bớt image, còn 11 tag và đĩa 92%. Số liệu dưới đây là của lần chạy 20:09.

## Kết quả theo nhóm

71 tiêu chí: **25 PASS, 40 FAIL (29 bắt buộc, 11 nên có), 2 WARN, 4 SKIP**. Điểm nhóm = (PASS + 0,5 × WARN) / số tiêu chí đo được.

| Nhóm | PASS | FAIL (trong đó bắt buộc) | WARN | SKIP | Điểm |
| --- | --- | --- | --- | --- | --- |
| BLD Build tái lập được | 2 | 3 (2) | 0 | 1 | 40% |
| REL Release/rollback | 3 | 4 (3) | 1 | 0 | 44% |
| VER Phiên bản container | 2 | 5 (4) | 0 | 1 | 29% |
| CI CI và chất lượng | 2 | 4 (2) | 0 | 0 | 33% |
| BAK Backup/khôi phục | 1 | 4 (4) | 0 | 0 | 20% |
| MON Giám sát/cảnh báo | 2 | 4 (3) | 0 | 1 | 33% |
| SEC Bảo mật và secret | 2 | 8 (7) | 0 | 1 | 20% |
| RES Tài nguyên/đĩa/dọn dẹp | 5 | 3 (1) | 0 | 0 | 62% |
| GIT Git hygiene | 2 | 2 (0) | 1 | 0 | 50% |
| DOC Tài liệu/runbook | 4 | 3 (3) | 0 | 0 | 57% |

Kiểm tay làm thêm trong lần này:

- **OPS-MON-7 (bắt buộc): FAIL.** `GET /health` trên :8131 và :18185 đều trả `status: degraded`, `git_sha` đúng `2dcff27c`. `problems` có `llm`, `embedding`, `reranker` (endpoint Vast không kết nối được) và `kb_sync` (lần nạp KB thành công gần nhất 2026-11-24, quá 36 giờ). Docker healthcheck vẫn báo `healthy` vì chỉ kiểm HTTP 200.
- **OPS-BAK-1, phần cron**: `crontab -l` chỉ có `run_sync.sh` lúc 03:00 và một watchdog của project khác, không có job backup.
- Chưa làm: OPS-SEC-11 (cần máy ngoài LAN; không gửi request ghi vào prod để thử auth), OPS-BLD-6 (build lại image mất khoảng 9 phút, ngoài phạm vi chỉ đọc), quét lịch sử Git bằng gitleaks cho OPS-SEC-6 (máy chưa cài).

## Năm vấn đề nên sửa trước

1. **Kho dữ liệu mở ra mọi interface (OPS-SEC-1, OPS-SEC-2).** Qdrant 6333/6334, Neo4j 7474/7687, Redis 6379 publish trên `0.0.0.0`; translation gateway :18185, Devspace dịch :5179 (network host) và review UI :5174 cũng nghe trên mọi interface, trong khi chỉ :8131 được phép public. Checker không đọc được firewall nên chưa biết có chain `DOCKER-USER` chặn không. Sửa: bind `127.0.0.1` (gateway và stack dev đã đi qua mạng `secai-net`), kiểm lại bằng `ss -ltnp` và quét từ máy ngoài.
2. **Không có backup dữ liệu (OPS-BAK-1..4, OPS-DOC-4).** Không timer hay cron nào backup Qdrant, Neo4j hay các DB SQLite report/review/session. Snapshot Qdrant duy nhất (`cve_kb.snapshot`) đã 150 ngày và nằm cùng đĩa với dữ liệu, đĩa đó đang 92%. Chưa từng diễn tập khôi phục, chưa có `docs/ops/RESTORE.md`. Sửa: timer hằng ngày (Qdrant snapshot API, `neo4j-admin dump` trong container tạm, `sqlite3 .backup`) ghi ra đĩa hoặc máy khác, rồi diễn tập khôi phục vào stack dev và ghi biên bản.
3. **Secret lộ (OPS-SEC-3, -4, -5, -7, -10).** Compose prod (mode 664, mọi user đọc được) có mật khẩu Neo4j viết thẳng trong `NEO4J_AUTH`, và 14 bản sao compose/bak khác cũng vậy; 18 file `.env*` không phải mode 600. Script cron `run_sync.sh` và 2 bản `.bak` của nó có `NVD_API_KEY` dạng plaintext, file khoá riêng mode 664. Unit `secai-local-embedding` và `secai-local-reranker` truyền `--api-key` qua argv (ai cũng xem được bằng `ps`). Repo PUBLIC nhưng tắt secret scanning và push protection. Sửa: `chmod 600`, chuyển giá trị sang `.env`/EnvironmentFile, **xoay** mật khẩu Neo4j và khoá NVD, bật secret scanning + push protection.
4. **Dịch vụ phụ thuộc chết mà không ai được báo (OPS-MON-4, OPS-MON-5, OPS-MON-7).** `secai-vast-llm-tunnel` và `secai-reward-training-tunnel` đã restart khoảng 524 nghìn lần, `secai-5090-tunnel` khoảng 241 nghìn lần, `secai-mlops-pipeline` đang `failed`. Hệ quả thấy ngay ở prod: `/health` degraded vì LLM, embedding và reranker không gọi được. Không có job giám sát hay cảnh báo nào. Sửa: sửa đích tunnel hoặc disable unit của instance đã huỷ; thêm timer 5 phút kiểm docker health, `/health` và unit failed rồi gửi cảnh báo; viết `docs/ops/INCIDENT.md` (OPS-DOC-7) ghi người nhận.
5. **Build không tái lập được và rollback không tự kiểm (OPS-BLD-1, OPS-BLD-2, OPS-VER-4, OPS-REL-4).** Dockerfile bỏ qua `uv.lock`, nên lần release gần nhất (main-2dcff27c) trôi 3 package (`charset-normalizer`, `filelock`, `redis`) dù `uv.lock` không đổi. Build FULL không gắn label `org.opencontainers.image.revision`, nên 3 container gateway đang chạy không có label. Nhánh `rollback` của `release-prod.sh` không kiểm image PREV_TAG còn hay không trước khi sửa compose, không chờ `/health`, không xác nhận tag sau khi chuyển (đã có test xfail chỉ rõ). Sửa: `uv sync --frozen` trong Dockerfile kèm `LABEL ... revision=$GIT_SHA`; thêm kiểm image và `wait_health` vào rollback.

Cần để ý thêm: đĩa `/` 92% (OPS-RES-1) trong khi log của 13 container `json-file` không giới hạn (OPS-MON-6). Một container ghi log dồn dập có thể làm đầy đĩa và kéo đổ mọi service.

## Toàn bộ FAIL ở tiêu chí bắt buộc và đề xuất

Ưu tiên: **P0** làm ngay (rủi ro lộ dữ liệu, mất dữ liệu, sự cố không ai biết), **P1** trong vài tuần (độ tin cậy của release), **P2** khi có thời gian.

| Ưu tiên | ID | Bằng chứng (rút gọn) | Đề xuất |
| --- | --- | --- | --- |
| P0 | OPS-SEC-1 | Qdrant 6333/6334, Neo4j 7474/7687, Redis 6379 publish `0.0.0.0` | Bind 127.0.0.1 hoặc bỏ `ports:` |
| P0 | OPS-SEC-2 | :18185, :5179 (network host) và :5174 nghe trên mọi interface | Bind 127.0.0.1/IP LAN |
| P0 | OPS-SEC-3 | 15 file có secret mà mọi user đọc được; 18 file `.env*` không phải 600 | `chmod 600`, thư mục release 700, umask 077 |
| P0 | OPS-SEC-4 | Compose prod (664) có `NEO4J_AUTH` literal | `${VAR}` không mặc định, xoay mật khẩu |
| P0 | OPS-SEC-5 | `NVD_API_KEY` plaintext trong `run_sync.sh` và 2 bản `.bak`; file khoá 664 | Đưa khoá vào file 600, xoá bản bak, xoay khoá |
| P0 | OPS-SEC-7 | `--api-key` trong ExecStart của `secai-local-embedding`, `secai-local-reranker` | EnvironmentFile |
| P0 | OPS-SEC-10 | Repo PUBLIC, secret scanning và push protection tắt | Bật cả hai |
| P0 | OPS-MON-4 | Không có unit/timer giám sát hay cảnh báo | Timer kiểm health + gửi cảnh báo |
| P0 | OPS-MON-5 | 3 tunnel `auto-restart` (240–525 nghìn lần), `secai-mlops-pipeline` failed | Sửa hoặc disable unit |
| P0 | OPS-MON-6 | 13 container `json-file` không `max-size` | `log-opts max-size=50m,max-file=3` |
| P0 | OPS-RES-1 | `/` 92% | Dọn image/build cache, chuyển snapshot sang đĩa khác |
| P0 | OPS-BAK-1 | Không timer backup; cron chỉ có job sync CVE | Timer backup hằng ngày |
| P0 | OPS-BAK-2 | Chưa có đích backup | Khai báo `SECAI_BACKUP_DIR` với `qdrant/ neo4j/ sqlite/` |
| P0 | OPS-BAK-3 | Snapshot Qdrant 150 ngày, cùng đĩa | Đích backup khác đĩa và một bản ngoài máy |
| P1 | OPS-BAK-4 | Chưa có biên bản diễn tập khôi phục | Diễn tập mỗi quý |
| P1 | OPS-DOC-4 | Chưa có `docs/ops/RESTORE.md` | Viết runbook khôi phục |
| P1 | OPS-DOC-7 | Chưa có `docs/ops/INCIDENT.md` | Viết runbook sự cố, ghi người nhận |
| P1 | OPS-BLD-1 | Dockerfile bỏ qua `uv.lock` | `uv sync --frozen` |
| P1 | OPS-BLD-2 | 3 package trôi ở main-2dcff27c, `uv.lock` không đổi | Hết khi sửa OPS-BLD-1 |
| P1 | OPS-VER-4 | 6 container thiếu label revision; pool kế thừa label của `oven-sh/bun`; report-backend và translation-frontend trỏ tới commit không có trong repo | `LABEL` trong Dockerfile, build lại từ commit đã push |
| P1 | OPS-REL-4 | Rollback không kiểm image, health, tag (3 test xfail) | Sửa nhánh rollback của `release-prod.sh` |
| P1 | OPS-VER-1 | report-backend, translation-frontend tạo từ image ID trần | Tạo lại từ tag qua compose |
| P1 | OPS-VER-2 | `secai-doc-convert-svc:1` là tag trôi; review dùng tag không có sha | Tag `<kênh>-<sha8>-<ngày>` |
| P1 | OPS-VER-3 | `redis:7-alpine` (chỉ major), `neo4j:5.20-community` (x.y) | Ghim x.y.z hoặc digest |
| P1 | OPS-REL-5 | `secai-translation-converter` tạo bằng `docker run` tay | Đưa vào compose |
| P1 | OPS-REL-6 | Compose `secai-docker` nằm ngoài Git, 12 file `.bak` thay cho lịch sử | Repo Git private, chặn `.env*` từ đầu |
| P1 | OPS-CI-1 | Không có workflow CI | Workflow pytest trên `pull_request` |
| P1 | OPS-CI-2 | `main` không có protection/ruleset | Bắt PR + check CI, cấm force-push |
| P1 | OPS-DOC-3 | 8 service chưa có runbook release/rollback (review ×2, converter, pool, 4 kho dữ liệu) | Runbook ngắn cho từng service |

FAIL ở tiêu chí nên có (P2): OPS-BLD-3 (base image chưa ghim digest), OPS-REL-7 (12 file `.bak` cạnh compose), OPS-VER-7 (4 service chạy code gateway cũ 31–53 ngày), OPS-CI-3 (`delete_branch_on_merge=false`), OPS-CI-5 (Dependabot tắt), OPS-MON-2 (converter không có healthcheck), OPS-SEC-8 (Neo4j, Qdrant, Redis và pool 25 container chạy root), OPS-RES-4 (89,87 GB image thu hồi được), OPS-RES-6 (12 container không `mem_limit`), OPS-GIT-2 (15/27 nhánh remote đã merge chưa xoá), OPS-GIT-4 (4 worktree đã merge cũ hơn 24 giờ: wt-ev2-handoff, wt-ev2-handoff-cve, wt-ev2-target-scope, wt-stack-package).

WARN:

- OPS-REL-8: 33 bind mount sống từ checkout chính (đang ở `main`): `demo`, `evidence`, `deploy/helm`, `data/hardware-check.json` vào gateway và canary, `pentestcode` vào pool. Không mount `src/`, nhưng các thư mục này đổi theo checkout chứ không theo image. CLAUDE.md yêu cầu nguồn mount là worktree "main" cố định (`*-deploy-main`).
- OPS-GIT-1: checkout chính có `.claude/settings.json` sửa chưa commit, nằm ngoài thư mục được mount.

## Những gì đang tốt

- Ba container gateway chạy cùng image `main-2dcff27c-20261127`, tag nằm trên `origin/main`, khớp `state.env` và `GIT_SHA` (OPS-VER-5, OPS-VER-6).
- Rollback dùng được ngay: image PREV_TAG `main-033b2aa3-20261123`, compose bak và profile translation trước đều còn (OPS-REL-2); hồ sơ release đủ (OPS-REL-1); gate pytest của lần release gần nhất xanh, 3315 passed (OPS-CI-4).
- 15 DB SQLite đang dùng đều nằm trên volume/bind (OPS-BAK-5); 37 container trong phạm vi đều chạy, restart policy đúng (OPS-MON-1, OPS-MON-3).
- `release-prod.sh gc` và timer `secai-wt-prune` chạy tốt, không còn container rollback/smoke hay image `main-`/`smoke-` quá hạn (OPS-RES-5).
- Không secret nào trong file tracked ngoài các fixture giả đã khai báo; không track `.env`, khoá, DB (OPS-SEC-6, OPS-GIT-3).

## Giới hạn của lần đo

- Firewall host chưa kiểm; cổng của `secai-translation-worker` và `secai-translation-converter` (network host) chưa biết nên chưa chấm.
- Checker không gọi HTTP, nên readiness `/health` (OPS-MON-7) và bảo vệ cổng :8131 (OPS-SEC-11) là kiểm tay; lần này mới làm OPS-MON-7.
- Hai label revision `c4c3c802…` (report-backend) và `4c590be8…` (translation-frontend) không có trong repo; có thể là commit chưa push hoặc của repo khác, chưa truy được.
- `scripts/versions.py` chưa có trên `origin/main` nên OPS-VER-8 SKIP.
