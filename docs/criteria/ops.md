# Tiêu chí vận hành (ops) cho secai-agent-gateway

Tài liệu này định nghĩa thế nào là "vận hành đạt" cho các service chạy image dựng từ repo này và các kho dữ liệu chúng phụ thuộc. Mỗi tiêu chí có ID, mức, cách đo và ngưỡng đạt. Phần lớn đo tự động bằng `scripts/ops-check.py` (chỉ đọc trạng thái thật) hoặc pytest (kiểm nội dung repo, chạy offline). Phần còn lại là kiểm tay, có ghi bước làm.

Tài liệu này không thay cho nghiệm thu tính năng ở [stack/ACCEPTANCE.md](../../stack/ACCEPTANCE.md). Ở đây chỉ chấm phần build, release, phiên bản, dữ liệu, giám sát, bảo mật và dọn dẹp.

## Phạm vi

Chấm như prod. Cột runbook chỉ ghi link khi tài liệu đó có đủ lệnh tạo lại container, release và rollback cho đúng service; thiếu thì ghi "chưa có" (OPS-DOC-3).

| Container | Vai trò | Runbook release/rollback |
| --- | --- | --- |
| `secai-docker-gateway` | API chính, public :8131. Cùng image với hai container dịch bên dưới | [CLAUDE.md, mục "Sau khi merge PR"](../../CLAUDE.md), [release-prod.sh](../../scripts/release-prod.sh) |
| `secai-translation-gateway` | Dịch file/ảnh và text v3, :18185 (network host) | [release-prod.sh](../../scripts/release-prod.sh) (không dùng `release.py` của translation-release cho container này: nó đổi lẻ, phá OPS-VER-5) |
| `secai-translation-worker` | Worker job dịch (network host) | [release-prod.sh](../../scripts/release-prod.sh) |
| `secai-translation-frontend` | Devspace dịch :5179 (network host) | [translation-release](../../deploy/translation-release/README.md) |
| `secai-report-backend` | Báo cáo v2, 127.0.0.1:18183 | [deploy/report](../../deploy/report/README.md) |
| `secai-review-frontend` | Giao diện expert review :5174 | chưa có (review-docker-deployment.md ghi rollback chưa sẵn sàng) |
| `secai-review-canary` | Canary review, 127.0.0.1:8189 | chưa có (như trên) |
| `secai-translation-converter` | Chuyển DOC sang DOCX trong sandbox (network host) | chưa có |
| `secai-docker-pentestcode`, `secai-docker-pentestcode-1` … `-24` | Pool 25 container pentestcode cho exploit/PoC | chưa có (deploy/pentestcode/README.md chỉ có build, mô tả pool 8) |
| `secai-docker-qdrant` | Vector DB (RAG) | chưa có (deploy/README.md đã deprecated, không có quy trình nâng cấp/rollback) |
| `secai-docker-neo4j` | Graph DB (RAG) | chưa có (như trên) |
| `secai-docker-redis` | Session/cache | chưa có (như trên) |
| `secai-docker-es` | Elasticsearch | chưa có (như trên) |
| `secai-devspace` (unit systemd `--user`) | Devspace FE `serve_static.py`, nghe :5173 trên host, proxy sang prod :8131; request đến từ loopback được gắn token review nếu có file `.review-loopback.local`, từ tailnet chỉ khi bật `DEVSPACE_REVIEW_ALLOW_TAILNET=true` (mặc định tắt) | chưa có |
| `secai-devspace-cloudflare` (unit systemd `--user`) | `cloudflared tunnel` trỏ vào `127.0.0.1:5173`: request qua tunnel tới devspace với peer loopback, nên được gắn token review | chưa có (tunnel công khai vào service nội bộ không được phép, OPS-SEC-2) |

Ba container đầu là một nhóm: chạy chung một image bất biến `secai-agent-gateway:main-<sha8>-<YYYYMMDD>` và chỉ đổi qua `scripts/release-prod.sh`. Stack dev `secai-gw-dev-*` của từng worktree chỉ dùng để tham chiếu (OPS-RES-7 đếm stack mồ côi), không chấm như prod. Không chấm lab CVE, OpenVAS, ClearML hay project khác trên máy.

Unit systemd `secai-*` (tunnel LLM/serving, OCR, MLOps, timer dọn dẹp) được tính ở nhóm giám sát vì gateway phụ thuộc vào chúng. Hai unit devspace ở cuối bảng được chấm ở nhóm bảo mật: devspace proxy vào prod và có thể gắn token review, nên cổng của nó và mọi tunnel trỏ vào nó tính như cổng prod. Script cron đồng bộ CVE vào KB (`~/Downloads/CVE-DB/run_sync.sh`) được tính ở nhóm secret vì log của nó được mount vào gateway.

## Cách chạy

```sh
# Trạng thái thật, chỉ đọc.
python3 -I scripts/ops-check.py
python3 -I scripts/ops-check.py --json > /tmp/ops.json
python3 -I scripts/ops-check.py --group VER,SEC
python3 -I scripts/ops-check.py --list            # danh mục, không đo
SECAI_BACKUP_DIR=/mnt/backup/secai python3 -I scripts/ops-check.py --group BAK

# Kiểm repo và hành vi release-prod.sh, offline (docker/git/curl giả).
scripts/dev.sh test tests/test_ops_repo.py tests/test_ops_check.py tests/test_release_prod.py
```

Mã thoát của `ops-check.py`:

- **0**: không có FAIL ở tiêu chí bắt buộc và mọi tiêu chí bắt buộc tự động đều đo được.
- **1**: có ít nhất một FAIL ở tiêu chí bắt buộc.
- **3**: không có FAIL bắt buộc nhưng có tiêu chí bắt buộc tự động không đo được (docker hay systemctl lỗi, thiếu quyền, checker lỗi). Thiếu dữ liệu GitHub (`gh` chưa đăng nhập, `--no-gh`, token không phải admin nên không đọc được `security_and_analysis`) không tính, vì đó là cài đặt repo chứ không phải trạng thái máy.
- **2**: sai tham số.

Riêng trường hợp docker không trả lời (không phải lỗi quyền), OPS-MON-1 là FAIL: với máy này, docker chết nghĩa là prod chết.

Checker chỉ gọi đúng các dạng lệnh sau, hàm `assert_read_only` kiểm từng lệnh theo danh sách trắng trước khi chạy (cờ lạ đều bị từ chối, kể cả dạng dính liền như `-iXDELETE` hay `--output=`):

- `docker ps -a --no-trunc --format '{{json .}}'`, `docker inspect <id>…`, `docker image inspect <ref>…`, `docker images --no-trunc <repo> --format '{{json .}}'` (`<repo>` là một tên cụ thể hoặc đúng pattern `secai-*`), `docker system df --format '{{json .}}'`;
- `gh api repos/<owner>/<repo>[/…]` (GET, không cờ nào khác);
- `df -P <path>`, `stat`;
- `systemctl [--user] show <unit>… -p <Prop>…`, `systemctl [--user] list-units|list-timers …`;
- git chỉ đọc, luôn dạng `git --no-optional-locks -C <dir> <lệnh>` với `rev-parse --is-inside-work-tree`, `merge-base --is-ancestor`, `cat-file -e`, `ls-files -z`, `diff --quiet --no-ext-diff --no-textconv <a> <b> -- <path>`, `status --porcelain --untracked-files=no`, `worktree list --porcelain`, `branch --show-current|-r|-r --merged <ref>`, `remote get-url origin`, `rev-list --count <range>`. Không `fetch`, nên `origin/main` là bản fetch gần nhất.

Ngoài ra checker chỉ đọc file thường (bỏ qua FIFO, socket, symlink), `/proc/net/tcp{,6}` và `os.stat`. Với secret, output chỉ có tên biến, đường dẫn và quyền file; các giá trị đọc được trong bộ nhớ bị xoá khỏi output trước khi in.

Trạng thái mỗi tiêu chí:

- **PASS**: đạt ngưỡng.
- **FAIL**: không đạt.
- **WARN**: đạt một phần (ngưỡng ghi rõ ở từng dòng), hoặc checker gặp lỗi khi đo (bằng chứng ghi "lỗi checker").
- **SKIP**: không đo được tự động (thiếu `gh`, thiếu quyền, tiêu chí kiểm tay).

Điểm nhóm = (PASS + 0,5 × WARN) / (PASS + FAIL + WARN), bỏ SKIP.

Trong pytest, hành vi hiện tại chưa đạt tiêu chí được đánh `xfail(strict=True)` kèm lý do, không sửa script. Khi có người sửa đúng, test chuyển XPASS và strict làm suite đỏ, nhắc gỡ marker.

## Build tái lập được (BLD)

| ID | Tiêu chí | Mức | Cách đo | Ngưỡng đạt |
| --- | --- | --- | --- | --- |
| OPS-BLD-1 | Image cài dependency đúng theo `uv.lock`, không resolve lại từ `pyproject.toml` | bắt buộc | `ops-check` đọc `Dockerfile` (gộp dòng nối `\`); pytest `test_ops_repo.py::test_dockerfile_installs_from_lock` (xfail) dùng cùng hàm | Có `COPY … uv.lock`, và cài bằng `uv sync --frozen`/`--locked`, hoặc `uv export --frozen`/`--locked` rồi `uv pip sync` hay `pip install --require-hashes` |
| OPS-BLD-2 | Lần release gần nhất không trôi version package bên thứ ba khi `uv.lock` không đổi | bắt buộc | `ops-check` đọc `pip-freeze.diff` trong thư mục release của `LATEST-PROD`, so `uv.lock` giữa commit PREV và NEW bằng `git diff --quiet` | 0 package bên thứ ba đổi version (bỏ dòng của chính `secai-agent-gateway`). Build OVERLAY không có `pip-freeze.diff` thì đạt. `uv.lock` có đổi thì WARN, đối chiếu tay |
| OPS-BLD-3 | Base image trong `Dockerfile` ghim digest | nên có | `ops-check`; pytest `test_dockerfile_base_pinned_by_digest` (xfail) | Mọi dòng `FROM` (trừ tham chiếu stage nội bộ) có `@sha256:` |
| OPS-BLD-4 | Image prod build từ `git archive` của commit trên `origin/main`; không container nào mount `src/` sống | bắt buộc | `ops-check`: `release-prod.sh` còn `git -C "$REPO" archive` và `diff -rq` so `/app/src`; không mount nào có đích `/app/src`. Pytest `test_release_script_builds_from_git_archive` | Cả hai điều kiện đúng |
| OPS-BLD-5 | `.dockerignore` loại secret và dữ liệu khỏi build context | bắt buộc | `ops-check`; pytest `test_dockerignore_excludes_secrets_and_data` | Có đủ các dòng `.env`, `.env.*`, `.venv/`, `data/`, `*.db` |
| OPS-BLD-6 | Build lại image prod từ commit ghi trong tag cho ra cùng danh sách package | nên có | Kiểm tay: (1) `git archive <sha8>` giải nén vào thư mục tạm; (2) `docker build -t secai-agent-gateway:dev-repro-<sha8>` từ thư mục đó; (3) so `pip freeze` của image đó với image prod bằng `docker run --rm --entrypoint pip` cho từng image rồi `diff`; (4) xoá tag `dev-repro-*` | 0 dòng khác ngoài dòng của chính gói |

## Release và rollback (REL)

| ID | Tiêu chí | Mức | Cách đo | Ngưỡng đạt |
| --- | --- | --- | --- | --- |
| OPS-REL-1 | Lần release gần nhất có đủ hồ sơ | bắt buộc | `ops-check` đọc thư mục ghi trong `~/secai-releases/LATEST-PROD` | Có `state.env`, `RELEASE-NOTES.md`, `pytest.log`, `health-8131-before.json`, `health-8131-after.json` và file `COMPOSE_BAK` ghi trong `state.env`. Prod đang chạy `PREV_TAG` (release đó đã bị rollback) thì thiếu hồ sơ chỉ là WARN |
| OPS-REL-2 | Rollback khả dụng ngay | bắt buộc | `ops-check`: `docker image inspect` tag `PREV_TAG`, `stat` `COMPOSE_BAK` và `TRANS_PREV_PROFILE` | Cả ba còn trên máy |
| OPS-REL-3 | `release-prod.sh` có test hành vi offline | bắt buộc | pytest `tests/test_release_prod.py` (docker/git/curl giả); `ops-check` chỉ kiểm có đủ 5 test bắt buộc, kết quả pass/fail đọc từ pytest | 5 test pass: `plan` không đổi gì; `run`/`rollback` thiếu `--yes` thì từ chối; 3 container lệch image thì dừng; prod không phải tổ tiên của main thì dừng; `gc` giữ image của lần release liền trước và image đang có container dùng |
| OPS-REL-4 | Rollback tự kiểm tra trước và sau khi chuyển | bắt buộc | pytest `test_rollback_waits_for_health_after_switch`, `test_rollback_fails_when_health_stays_wrong`, `test_rollback_refuses_before_touching_compose_when_previous_image_is_gone` (xfail); `ops-check` đọc nhánh rollback của script | Trước khi chép compose: `docker image inspect` PREV_TAG. Sau `compose up`: chờ `/health` 8131 và 18185, xác nhận 3 container về PREV_TAG, health sai thì mã thoát khác 0 |
| OPS-REL-5 | Mọi container trong phạm vi do compose quản lý, file cấu hình còn tồn tại | bắt buộc | `ops-check`: label `com.docker.compose.project` và `com.docker.compose.project.config_files` | 100% container có project, mọi file trong `config_files` còn |
| OPS-REL-6 | Compose prod (`secai-docker/docker-compose.yml`) được quản lý phiên bản | bắt buộc | `ops-check`: `git -C <secai-docker> rev-parse --is-inside-work-tree`, `git ls-files` và `git status --porcelain --untracked-files=no` ở đó | Nằm trong repo Git private, `docker-compose.yml` đã được track (chỉ `git init` là FAIL), không track `.env*`. Compose có sửa chưa commit thì WARN |
| OPS-REL-7 | Thư mục compose prod không tích file sao lưu | nên có | `ops-check` đếm `docker-compose.yml.bak*` | ≤ 3 file |
| OPS-REL-8 | Không bind-mount sống từ Git checkout vào container prod | bắt buộc | `ops-check` so nguồn bind mount với `git worktree list` | FAIL: mount `src/`, mount từ worktree khác ngoài checkout chính và `*-deploy-main`, checkout chính không ở `main`, hoặc HEAD của `*-deploy-main` nằm ngoài `origin/main`. WARN: mount từ checkout chính đang ở `main` (nơi agent có thể đổi nhánh). PASS: không mount từ checkout, hoặc chỉ từ worktree `*-deploy-main` cố định như CLAUDE.md quy định |

## Quản lý phiên bản container (VER)

| ID | Tiêu chí | Mức | Cách đo | Ngưỡng đạt |
| --- | --- | --- | --- | --- |
| OPS-VER-1 | Container được tạo từ tag, không từ image ID trần | bắt buộc | `ops-check`: `docker inspect` `Config.Image` | Không container nào có `Config.Image` dạng `sha256:…` hay chuỗi hex |
| OPS-VER-2 | Tag image first-party (`secai-*`) bất biến, truy được về commit | bắt buộc | `ops-check` phân loại tag của container đang chạy | PASS: dạng `<kênh>-<sha 7–40 hex>-<YYYYMMDD>` theo đúng vị trí (sha toàn chữ số vẫn tính), ví dụ `main-2dcff27c-20261127`; dạng khác thì cần một token sha (không phải ngày) và một token ngày. WARN: chỉ có ngày hoặc semver `x.y.z`. FAIL: `latest`, không tag, số trần như `:1`, tên mô tả |
| OPS-VER-3 | Image bên thứ ba ghim version đầy đủ | bắt buộc | `ops-check` cho container prod; pytest `test_stack_compose_pins_third_party_versions` (xfail) cho `stack/compose.yml` | PASS: `x.y.z` hoặc `@sha256`. WARN: `x.y`. FAIL: chỉ major (`redis:7-alpine`), `latest`, không tag |
| OPS-VER-4 | Image first-party có label `org.opencontainers.image.revision` trỏ tới commit của repo này | bắt buộc | `ops-check`: label của image và container, `org.opencontainers.image.source`, `git cat-file -e <sha>^{commit}`; pytest `test_full_build_labels_revision`, `test_dockerfile_labels_revision` (xfail) | Mọi container first-party có label. FAIL khi thiếu label hoặc label kế thừa từ base image của repo khác (source khác repo này). WARN khi sha không có trong repo (commit chưa push hoặc đã mất) |
| OPS-VER-5 | Ba container gateway chạy cùng image | bắt buộc | `ops-check`: `Config.Image` và image ID của `secai-docker-gateway`, `secai-translation-gateway`, `secai-translation-worker` | Cùng tag và cùng image ID |
| OPS-VER-6 | Tag prod gateway khớp main và hồ sơ release | bắt buộc | `ops-check`: regex tag, `git merge-base --is-ancestor <sha8> origin/main`, `NEW_TAG` trong `state.env`, biến `GIT_SHA` của 3 container | Tag dạng `main-<sha8>-<YYYYMMDD>`, sha8 nằm trên `origin/main`, bằng `NEW_TAG`, bằng `GIT_SHA` của cả 3 (thiếu `GIT_SHA` là không đạt). Prod chạy `PREV_TAG` sau rollback thì WARN |
| OPS-VER-7 | Service chạy code gateway không tụt quá xa prod | nên có | `ops-check`: `Created` của image `secai-agent-gateway:*` ngoài nhóm 3 so với image prod gateway | Chênh ≤ 30 ngày |
| OPS-VER-8 | Sổ phiên bản dịch vụ khớp trạng thái thật | nên có | `ops-check` SKIP khi `scripts/versions.py` chưa có trên `origin/main`; khi đã có thì đối chiếu tay sổ với `docker inspect` | Mọi container trong phạm vi có dòng trong sổ, tag khớp `docker inspect` |

## CI và chất lượng (CI)

| ID | Tiêu chí | Mức | Cách đo | Ngưỡng đạt |
| --- | --- | --- | --- | --- |
| OPS-CI-1 | Có workflow CI chạy pytest cho mọi PR vào main | bắt buộc | `ops-check`: file trong `.github/workflows/` chứa cả `pytest` và `pull_request`, và file đó active trên GitHub (`gh api repos/{repo}/actions/workflows`); pytest `test_ci_workflow_runs_pytest` (xfail) | ≥ 1 workflow như vậy |
| OPS-CI-2 | `main` có branch protection hoặc ruleset | bắt buộc | `ops-check`: `gh api …/branches/main/protection` và `…/rules/branches/main`; yêu cầu nào classic protection thiếu mà ruleset có (`pull_request`, `required_status_checks`, `non_fast_forward`) thì tính là có. Tài khoản không phải admin mà branch báo `protected` thì SKIP | Bắt PR, có ít nhất một status check bắt buộc (`contexts`/`checks` không rỗng), cấm force-push |
| OPS-CI-3 | Repo tự xoá nhánh sau merge | nên có | `ops-check`: `gh api repos/{repo}` | `delete_branch_on_merge=true` |
| OPS-CI-4 | Gate release chạy toàn bộ pytest và lần release gần nhất xanh | bắt buộc | `ops-check`: `release-prod.sh` còn `-m pytest`; dòng cuối `pytest.log` của release gần nhất; pytest `test_release_script_has_full_pytest_gate` | Dòng cuối có `passed`, không có `failed`/`error` |
| OPS-CI-5 | Có cập nhật bảo mật dependency tự động | nên có | `ops-check`: `.github/dependabot.yml` (hoặc `.yaml`), không có thì `security_and_analysis.dependabot_security_updates`. Token không phải admin không đọc được trường này: SKIP | `enabled` hoặc có file |
| OPS-CI-6 | Script shell vận hành chạy ở chế độ chặt | nên có | `ops-check`; pytest `test_shell_scripts_parse`, `test_shell_scripts_strict_mode` | Mọi script có shebang trong `scripts/`, `stack/`, `stack/lib/`, `deploy/` (cấp 1) có `set -u` và `pipefail` (script `sh`: chỉ cần `-u`); script đổi prod (danh sách `MUTATING_SCRIPTS` trong checker) có thêm `-e`; `bash -n` không lỗi |

## Backup và khôi phục dữ liệu (BAK)

Quy ước đích backup: một thư mục khai báo qua `SECAI_BACKUP_DIR` (hoặc `--backup-dir`), mỗi kho một thư mục con `qdrant/`, `neo4j/`, `sqlite/` (DB gateway, session, review, report, translate).

| ID | Tiêu chí | Mức | Cách đo | Ngưỡng đạt |
| --- | --- | --- | --- | --- |
| OPS-BAK-1 | Có lịch backup định kỳ phủ Qdrant, Neo4j và DB SQLite | bắt buộc | `ops-check`: `systemctl [--user] list-timers`, tên timer chứa backup/snapshot/dump và secai/qdrant/neo4j/sqlite/report/review. Cron: kiểm tay `crontab -l` | Mỗi loại kho có timer khớp tên (qdrant; neo4j; sqlite/report/review/session), hoặc có một timer chung `secai-backup*.timer` (độ phủ khi đó đo ở OPS-BAK-2) |
| OPS-BAK-2 | Backup mới nhất của từng kho còn trong hạn | bắt buộc | `ops-check`: mtime file mới nhất trong từng thư mục con của đích backup | `sqlite/` và `neo4j/` ≤ 26 giờ, `qdrant/` ≤ 7 ngày. Không khai báo đích backup thì FAIL |
| OPS-BAK-3 | Bản backup nằm ngoài đĩa chứa dữ liệu prod | bắt buộc | `ops-check` so `st_dev` của đích backup với `secai-data`. Bản sao ngoài máy: kiểm tay nhật ký rsync/rclone | Khác thiết bị; có thêm một bản ngoài máy |
| OPS-BAK-4 | Đã diễn tập khôi phục gần đây | bắt buộc | `ops-check` tìm `docs/archive/test-results/restore-drill-YYYYMMDD.md` | Biên bản mới nhất ≤ 92 ngày, ghi thời gian khôi phục và kết quả kiểm |
| OPS-BAK-5 | DB SQLite của service gateway nằm trên volume/bind | bắt buộc | `ops-check`: với container chạy `uvicorn`/`secai_gateway`, các biến `*_DB_PATH` đang dùng, kể cả giá trị mặc định của `config.py` khi biến không đặt (session khi backend là `sqlite`, mặc định `sqlite`; review khi `REVIEW_ENABLED` bật; report khi `REPORT_FEATURE_ENABLED` bật), đường dẫn tương đối tính từ `WorkingDir` | 100% DB đang dùng nằm dưới một mount |

## Giám sát và cảnh báo (MON)

| ID | Tiêu chí | Mức | Cách đo | Ngưỡng đạt |
| --- | --- | --- | --- | --- |
| OPS-MON-1 | Mọi container trong phạm vi đang chạy và healthy | bắt buộc | `ops-check`: `State.Status`, `State.Health.Status`. Docker không trả lời (không phải lỗi quyền) thì FAIL | Đủ 8 container app, 4 kho dữ liệu, pool 25; tất cả `running`; container có healthcheck ở `healthy`. Healthcheck của gateway chỉ là liveness (`/health` trả 200 cả khi degraded), readiness đo ở OPS-MON-7 |
| OPS-MON-2 | Mọi container có healthcheck | nên có | `ops-check`: `Config.Healthcheck` | 100% |
| OPS-MON-3 | Container tự chạy lại khi máy khởi động lại | bắt buộc | `ops-check`: `HostConfig.RestartPolicy` | 100% `unless-stopped` hoặc `always` |
| OPS-MON-4 | Có cảnh báo chủ động khi dịch vụ chết hoặc health xấu | bắt buộc | `ops-check` tìm unit/timer `secai-*` có alert/monitor/watchdog/healthcheck/uptime. Kênh nhận: kiểm tay bằng cách gửi thử một cảnh báo | ≥ 1 job giám sát; cảnh báo thử tới được người nhận ghi trong `docs/operations/incident-runbook.md` trong 5 phút |
| OPS-MON-5 | Unit systemd `secai-*` không hỏng hay restart liên tục | bắt buộc | `ops-check`: `systemctl [--user] show` (`ActiveState`, `SubState`, `NRestarts`). Không đọc được bus systemd của user thì SKIP và mã thoát 3 | FAIL: unit `failed` hoặc đang `auto-restart`. WARN: đang chạy nhưng `NRestarts` > 100 (bộ đếm tính từ lần start tay gần nhất) |
| OPS-MON-6 | Log container có giới hạn dung lượng | bắt buộc | `ops-check`: `HostConfig.LogConfig` | Driver `json-file` phải có `max-size` (hoặc dùng driver `local`/`journald`) |
| OPS-MON-7 | `/health` của prod báo sẵn sàng phục vụ | bắt buộc | Kiểm tay: `curl -fsS http://127.0.0.1:8131/health` và `:18185/health` (checker không được gọi HTTP) | `status` là `ok`, `problems` rỗng, `git_sha` khớp tag prod |

## Bảo mật và secret (SEC)

| ID | Tiêu chí | Mức | Cách đo | Ngưỡng đạt |
| --- | --- | --- | --- | --- |
| OPS-SEC-1 | Kho dữ liệu không publish cổng ra mọi interface | bắt buộc | `ops-check`: `HostConfig.PortBindings` của Qdrant, Neo4j, Redis, ES. Firewall (chain `DOCKER-USER`): kiểm tay | Không cổng nào có HostIp rỗng, `0.0.0.0` hay `::` |
| OPS-SEC-2 | Chỉ gateway :8131 public | bắt buộc | `ops-check`: port publish của container app và pool; với container `network=host` có cổng biết trước (translation-gateway 18185, translation-frontend 5179) và unit `secai-devspace` (5173) thì đọc socket LISTEN trong `/proc/net/tcp{,6}`; `ExecStart` của unit `secai-*` có `cloudflared … tunnel`, `ngrok` hay `tailscale funnel`; cấu hình `tailscale serve status --json` (chỉ đọc; máy không có tailscale hoặc tailscaled không chạy thì bỏ qua phần này, không tính là không đo được). Container `network=host` chưa biết cổng: kiểm tay `ss -ltnp` | Ngoài `secai-docker-gateway` cổng 8131, không cổng nào nghe trên `0.0.0.0`/`::`; không unit nào đang chạy tunnel công khai; không lối vào `tailscale serve` nào có `AllowFunnel` trỏ vào cổng trong phạm vi (8131, 18183, 18185, 8189, 5173, 5174, 5179, cổng Qdrant/Neo4j/Redis/ES), kể cả phiên foreground. WARN: unit tunnel còn cài nhưng không chạy, hoặc `tailscale serve` (chỉ tailnet) trỏ vào 5173/5179 (request tới với peer loopback nên được gắn token) |
| OPS-SEC-3 | File chứa secret chỉ chủ sở hữu đọc được | bắt buộc | `ops-check` quét `~/secai-releases` và `secai-docker` sâu 3 cấp (bỏ `tree/`, `imgsrc/`). `.env*` (trừ `*.example`) không được có quyền group/other. File khác có gán biến dạng secret bằng giá trị literal (kể cả mặc định `${X:-…}` và giá trị thay thế `${X:+…}`) thì không được world-readable, tính cả quyền đi qua của thư mục cha. File chỉ còn tham chiếu (`${VAR}`, `${VAR:?thông báo}`, `${VAR:-}`) không tính là lộ secret; literal mà toàn bộ là giá trị giữ chỗ hiển nhiên (`sk-placeholder`, `change-me`, `xxxx`, `<…>`) không tính, còn literal chỉ chứa các chữ đó (`Changeme2026!`) vẫn tính | 0 file vi phạm |
| OPS-SEC-4 | Compose prod không chứa secret dạng literal | bắt buộc | `ops-check` đọc `secai-docker/docker-compose.yml` | 0 biến tên dạng secret có giá trị literal hay mặc định literal `${X:-…}` (literal toàn bộ là giá trị giữ chỗ như ở OPS-SEC-3 không tính; comment cuối dòng tính là một phần giá trị); mọi giá trị qua `${VAR}` không mặc định hoặc `env_file` |
| OPS-SEC-5 | Script cron đồng bộ KB không chứa khoá plaintext | bắt buộc | `ops-check` quét `~/Downloads/CVE-DB/*` (đổi bằng `--cron-glob`), kể cả bản `.bak`. Tên biến secret theo cùng quy tắc với OPS-SEC-4; giá trị là tham chiếu `$X`, lời gọi hàm hay subscript (`os.environ.get(…)`) không tính, nhưng tham số thứ hai literal của `.get("X_PASSWORD", "…")`/`getenv("X_KEY", "…")` (mặc định nằm thẳng trong script) thì tính; literal toàn bộ là giá trị giữ chỗ như ở OPS-SEC-3 không tính | 0 khoá plaintext trong script; file khoá riêng có mode 600 |
| OPS-SEC-6 | Không secret thật trong file tracked | bắt buộc | `ops-check` và pytest `test_ops_check.py::test_real_tracked_tree_has_no_secret_outside_fixtures` (mẫu khoá phổ biến). Miễn trừ trong `FAKE_SECRET_FIXTURES`: thư mục benchmark `evals/sanitize/`, còn lại theo từng file với số match đã khai báo cho từng loại. Lịch sử Git: kiểm tay bằng `gitleaks detect` | 0 match ngoài miễn trừ; file được miễn không có thêm match; gitleaks không thấy khoá thật |
| OPS-SEC-7 | Không truyền secret qua argv | bắt buộc | `ops-check`: `Cmd`/`Entrypoint`/`Args` của container và `ExecStart` của unit `secai-*`; đọc được một nửa thì WARN | 0 cờ `--api-key`/`--token`/`--password` hay `key=…` có giá trị |
| OPS-SEC-8 | Container chạy user không phải root | nên có | `ops-check`: `Config.User` | 100% (pool pentestcode xét riêng theo nhu cầu tool) |
| OPS-SEC-9 | Image không bake secret vào ENV | nên có | `ops-check`: `Config.Env` của image | 0 biến dạng secret có giá trị (`GPG_KEY` của image python là fingerprint công khai, không tính) |
| OPS-SEC-10 | Repo public phải bật quét secret | bắt buộc | `ops-check`: `gh api repos/{repo}`, trường `security_and_analysis` (GitHub chỉ trả cho admin; token không phải admin thì SKIP, không tính mã thoát 3) | Repo private, hoặc public với secret scanning và push protection đều bật |
| OPS-SEC-11 | Cổng public :8131 được bảo vệ | bắt buộc | Kiểm tay: (1) từ máy ngoài LAN gọi `http://<IP public>:8131/health`, phải bị chặn hoặc chỉ đi được qua https; (2) trên máy, gọi một route ghi/quản trị không kèm khoá, ví dụ `POST /ai/v2/reports` khi bật report, hay `/review-api/…` không Bearer; thêm một lần qua devspace: từ máy khác trong LAN gọi `GET http://<IP LAN>:5173/review-api/…` kèm header `X-Review-Role: admin` (devspace chỉ gắn token review cho kết nối loopback khi có `.review-loopback.local`, tailnet khi bật `DEVSPACE_REVIEW_ALLOW_TAILNET=true`) | (1) không có HTTP trần ra Internet; (2) trả 401/403, kể cả lần qua :5173 kèm `X-Review-Role` |

## Tài nguyên, đĩa, dọn dẹp (RES)

| ID | Tiêu chí | Mức | Cách đo | Ngưỡng đạt |
| --- | --- | --- | --- | --- |
| OPS-RES-1 | Đĩa chứa Docker và `secai-data` còn chỗ | bắt buộc | `ops-check`: `df -P` cho `/`, `secai-data`, `secai-releases` | PASS ≤ 80%; WARN 80–90%; FAIL > 90% |
| OPS-RES-2 | Số tag `secai-agent-gateway` vừa phải | nên có | `ops-check`: `docker images secai-agent-gateway` | ≤ 20 tag |
| OPS-RES-3 | Không tích image gateway nằm ngoài mọi cơ chế dọn | bắt buộc | `ops-check`: tag không theo `main-`/`smoke-` (do `release-prod.sh gc` dọn) hay `dev-` (do `wt-rm.sh`/`wt-prune.sh` dọn), cũ hơn 14 ngày, không container nào dùng (so cả tag lẫn image ID) | 0 tag |
| OPS-RES-4 | Image của dự án không dùng tới chiếm ít chỗ | nên có | `ops-check`: `docker images 'secai-*'`, bỏ image ID mà container nào đó (kể cả đã dừng) đang dùng theo `docker inspect`, cộng cột `Size` theo image ID. Là cận trên vì layer chung bị cộng trùng. Image của project khác trên máy không tính (đĩa chung đo ở OPS-RES-1) | ≤ 30 GB |
| OPS-RES-5 | `gc` chạy đều và thật sự dọn | bắt buộc | `ops-check`: container `*-rollback-*`/`*-smoke-*` đã dừng; image `main-`/`smoke-` cũ hơn 4 ngày không phải `PREV_TAG` và không ai dùng; unit `secai-wt-prune.timer`/`.service` | 0 container dừng quá 3 ngày; 0 image quá hạn; timer `active`; service `Result=success` (runner của timer nuốt lỗi của gc, nên gc hỏng chỉ lộ ra qua image/container quá hạn) |
| OPS-RES-6 | Container có giới hạn bộ nhớ | nên có | `ops-check`: `HostConfig.Memory` | 100% có `mem_limit` |
| OPS-RES-7 | Không có stack dev mồ côi | nên có | `ops-check`: container compose project `secai-gw-dev-*` đang chạy, label `working_dir` còn tồn tại | 0 stack mồ côi |
| OPS-RES-8 | Build cache Docker gọn | nên có | `ops-check`: dòng `Build Cache` của `docker system df` | ≤ 20 GB |

## Git hygiene (GIT)

| ID | Tiêu chí | Mức | Cách đo | Ngưỡng đạt |
| --- | --- | --- | --- | --- |
| OPS-GIT-1 | Checkout làm nguồn mount của prod sạch và đúng nhánh | bắt buộc | `ops-check` trên mọi worktree có bind mount vào prod (và checkout chính): `branch --show-current`, `rev-list --count origin/main..HEAD`, `merge-base --is-ancestor` cho `*-deploy-main`, `status --porcelain` | Checkout chính ở `main`, không ahead; `*-deploy-main` có HEAD nằm trên `origin/main`; không file sửa dở nằm trong thư mục đang mount. Sửa dở ngoài mount, hoặc lệch nhánh khi checkout chính không được mount: WARN |
| OPS-GIT-2 | Không tích nhánh remote đã merge | nên có | `ops-check`: `git branch -r --merged origin/main` | ≤ 10 nhánh (squash merge không đếm được nên con số thật có thể cao hơn) |
| OPS-GIT-3 | Không track `.env`, khoá, DB, log | bắt buộc | `ops-check`: `git ls-files`; pytest `test_no_forbidden_tracked_files` | 0 file `.env`/`.env.*` (trừ `*.example`), `*.pem`, `*.key`, `id_rsa*`, `*.db`, `*.sqlite*`, `*.log`. Log bằng chứng trong `docs/archive/` và `evidence/` được phép |
| OPS-GIT-4 | Không có worktree đã merge bị bỏ quên | nên có | `ops-check`: worktree có HEAD nằm trong `origin/main`, tuổi thư mục, `git status --porcelain --untracked-files=no` (worktree có sửa chưa commit là đang làm dở, `wt-prune.sh` cũng bỏ qua, nên không tính) | 0 worktree như vậy cũ hơn 24 giờ |
| OPS-GIT-5 | `.gitignore` chặn file runtime | nên có | `ops-check`; pytest `test_gitignore_blocks_runtime_files` | Có đủ `.env`, `.env.*`, `data/`, `logs/`, `*.log`, `.venv/` |

## Tài liệu và runbook (DOC)

| ID | Tiêu chí | Mức | Cách đo | Ngưỡng đạt |
| --- | --- | --- | --- | --- |
| OPS-DOC-1 | Tài liệu tiêu chí này khớp với checker | bắt buộc | `ops-check`; pytest `test_criteria_doc_matches_checker`, `test_criteria_doc_rows_are_measurable` | Tập ID trong các bảng tiêu chí bằng tập ID của `ops-check.py --list`, không trùng, mức giống nhau |
| OPS-DOC-2 | Có runbook release/rollback cho nhóm gateway | bắt buộc | `ops-check`; pytest `test_release_runbook_is_documented` | `CLAUDE.md` có lệnh `release-prod.sh rollback --yes`; `scripts/README.md` nhắc `release-prod.sh` |
| OPS-DOC-3 | Mỗi service trong phạm vi có runbook release/rollback | bắt buộc | `ops-check` và pytest đọc bảng "Phạm vi" ở đầu tài liệu này (link phải tồn tại). Nội dung runbook: kiểm tay khi sửa bảng | Mọi dòng link tới tài liệu có lệnh tạo lại container, release và rollback; không dòng nào ghi "chưa có" |
| OPS-DOC-4 | Có runbook khôi phục dữ liệu prod từ backup | bắt buộc | `ops-check`: `docs/operations/restore-from-backup.md` có nhắc Qdrant, Neo4j, SQLite; lệnh cụ thể: kiểm tay. Pytest `test_restore_runbook_exists` (xfail) | Có lệnh cho Qdrant snapshot, Neo4j dump/load, SQLite `.backup`/khôi phục và thứ tự dừng/chạy service |
| OPS-DOC-5 | `scripts/README.md` liệt kê script vận hành | nên có | `ops-check`; pytest `test_scripts_readme_lists_ops_scripts` | Nhắc mọi `scripts/*.sh` và `ops-check.py` |
| OPS-DOC-6 | Baseline ops được đo lại định kỳ | nên có | `ops-check` tìm `docs/archive/test-results/ops-baseline-YYYYMMDD.md` | Bản mới nhất ≤ 31 ngày; đo lại sau mỗi thay đổi hạ tầng lớn |
| OPS-DOC-7 | Có runbook sự cố | bắt buộc | `ops-check`: `docs/operations/incident-runbook.md` có người nhận/người trực, tunnel, đĩa, `/health`, KB sync; pytest `test_incident_runbook_exists` (xfail) | Ghi người nhận cảnh báo và bước xử lý cho: tunnel LLM/serving chết, đĩa > 90%, `/health` degraded, KB sync cũ, khi nào rollback |

## Những gì bộ kiểm chưa phủ

- Firewall host (`DOCKER-USER`, ufw): checker không gọi `iptables`, nên OPS-SEC-1/2 chỉ thấy port publish của Docker, socket LISTEN của các cổng biết trước (18185, 5179, 5173), tunnel khai trong `ExecStart` của unit `secai-*` và cấu hình `tailscale serve`/funnel. Tunnel khác chạy ngoài systemd (cloudflared/ngrok chạy tay, cron, unit không mang tên `secai-*`) không thấy được.
- Cron của user: `crontab -l` không nằm trong danh sách lệnh của checker, OPS-BAK-1 cần kiểm tay phần cron.
- Lịch sử Git (OPS-SEC-6), bản backup ngoài máy (OPS-BAK-3), readiness `/health` (OPS-MON-7) và bảo vệ cổng :8131 (OPS-SEC-11) cần kiểm tay.
- `origin/main` dùng bản fetch gần nhất của repo; chạy `git fetch` trước nếu cần số liệu mới nhất cho OPS-VER-6.

Baseline đầu tiên: [ops-baseline-20261127.md](../archive/test-results/ops-baseline-20261127.md).
