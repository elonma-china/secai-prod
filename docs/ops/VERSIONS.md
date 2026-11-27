# Phiên bản container

`scripts/versions.py` trả lời ba câu cho các service của `secai-agent-gateway`, và chỉ các service này:

1. Mỗi service đang chạy bản nào: image, git sha, ngày dựng image, lúc khởi động.
2. Service đó đã từng chạy những bản nào.
3. Container nào đang ở trạng thái version không quản lý được: ID trần, tag trôi, tag đã bị gắn sang image khác, thiếu revision, các container lẽ ra cùng image lại lệch nhau.

Công cụ chỉ đọc Docker (`docker ps -a`, `docker inspect`, `docker image inspect`) và chỉ ghi vào sổ của chính nó. Đổi phiên bản vẫn chỉ qua script release có gate (`scripts/release-prod.sh` cho gateway); trang web không có nút thao tác nào.

## Catalog: `deploy/services.json`

Danh sách service nằm trong Git, không chứa secret hay đường dẫn tuyệt đối. Mỗi service có:

| Trường | Ý nghĩa |
| --- | --- |
| `id` | Tên ngắn, dùng trong sổ và trong lệnh (`gateway`, `report-backend`, ...) |
| `title` | Mô tả tiếng Việt |
| `containers` hoặc `container_pattern` | Tên container chính xác, hoặc regex khớp toàn bộ tên (pool pentestcode, stack dev) |
| `role` | `prod`, `canary`, `dependency` hoặc `dev` |
| `group` | Tuỳ chọn. Các container của một service luôn phải cùng image; `group` mở rộng ràng buộc đó sang các service cùng giá trị |
| `built_from_repo` | `true` nếu image dựng từ repo này; khi đó thiếu revision là một cờ cảnh báo |
| `upgrade`, `rollback` | Lệnh hoặc hướng dẫn dạng chữ. Chỗ nào chưa có script thì ghi thẳng "làm tay, chưa có script" |

Catalog hiện có:

| id | Container | role | Đổi phiên bản |
| --- | --- | --- | --- |
| `gateway` | `secai-docker-gateway`, `secai-translation-gateway`, `secai-translation-worker` (cùng một image) | prod | `scripts/release-prod.sh run --yes` / `rollback --yes` |
| `translation-frontend` | `secai-translation-frontend` | prod | làm tay, profile compose project `secai-translation-release` |
| `report-backend` | `secai-report-backend` | prod | `deploy/report/switch.py apply` / `rollback` |
| `review-frontend` | `secai-review-frontend` | prod | làm tay |
| `review-canary` | `secai-review-canary` | canary | làm tay |
| `translation-converter` | `secai-translation-converter` | prod | làm tay; đang là `docker run` tay, không compose |
| `pentestcode-pool` | `secai-docker-pentestcode`, `-1`..`-24` | dependency | làm tay, compose `secai-docker` |
| `qdrant`, `neo4j`, `redis`, `elasticsearch` | `secai-docker-qdrant`, `-neo4j`, `-redis`, `-es` | dependency | làm tay, compose `secai-docker` |
| `dev-stacks` | `secai-gw-dev-*` | dev | `scripts/dev.sh`; chỉ hiện khi hỏi (`--role dev` hoặc `all`) |

Thêm service: thêm một mục vào catalog, chạy `scripts/dev.sh test tests/test_versions.py`.

## Lệnh

Chạy từ gốc repo, luôn với `python3 -I`:

```sh
python3 -I scripts/versions.py status                  # bảng tiếng Việt, mặc định mọi role trừ dev
python3 -I scripts/versions.py status --role prod --strict   # thoát 1 nếu service prod có cờ cảnh báo
python3 -I scripts/versions.py status --json --role all
python3 -I scripts/versions.py history gateway -n 10   # mới nhất trước; bỏ SERVICE để xem mọi service
python3 -I scripts/versions.py snapshot --dry-run      # xem trước; bỏ --dry-run để ghi
python3 -I scripts/versions.py backfill                # chỉ in kế hoạch; --yes mới ghi
python3 -I scripts/versions.py serve                   # http://127.0.0.1:18190/
```

`record` dành cho script release:

```sh
python3 -I scripts/versions.py record --service gateway --action release \
    --from secai-agent-gateway:main-033b2aa3-20261123 --to secai-agent-gateway:main-2dcff27c-20261127 \
    --release-dir ~/secai-releases/main-2dcff27c-20261127
```

Mã thoát: 0 ổn; 1 khi `--strict` thấy cờ ở service prod, hoặc không ghi được sổ; 2 khi sai cú pháp, catalog lỗi hay không gọi được Docker.

### Version hiển thị

- Nếu `Config.Image` của container là tag và tag đó vẫn trỏ đúng image đang chạy: hiện tag đó.
- Nếu không (ID trần, tag đã chuyển sang image khác): hiện tag bất biến đầu tiên của image.
- Không có tag bất biến nào: hiện 12 ký tự đầu của image ID.

Revision lấy từ label `org.opencontainers.image.revision` (của container hoặc image); không có thì từ env `GIT_SHA`. Từ env chỉ đọc đúng `GIT_SHA` và `GIT_BRANCH`, mọi biến khác bị bỏ ngay khi phân tích `docker inspect`, không đi tới bất kỳ đầu ra nào.

### Các cờ

| Cờ | Nghĩa | Tính cho `--strict` |
| --- | --- | --- |
| `bare-id` | Container tạo từ ID image trần, không qua tag | có |
| `tag-moved` | Tag trong `Config.Image` không còn trỏ image đang chạy: đã gắn sang image khác hoặc bị xoá | có |
| `floating-tag` | Tag không bất biến: `latest`, `7-alpine`, `5.20-community`, `1`, `review-mvp-20261004`, ... | có |
| `floating-tag:minor` | Semver đủ ba số kèm hậu tố (`1.3.14-tools`): gần bất biến nhưng vẫn ghi đè được | không |
| `no-revision` | Image dựng từ repo này nhưng không có label revision lẫn env `GIT_SHA` | có |
| `group-drift` | Các container lẽ ra cùng image (cùng service hoặc cùng `group`) đang chạy image khác nhau | có |
| `behind-main:<n>` | Revision đang chạy kém `origin/main` n commit. Chỉ có khi có git và commit đó có trong repo | không |
| `missing` | Không thấy container, hoặc container không chạy | có |

Tag được coi là bất biến khi có dạng `…-<sha 7–40 hex>-<yyyymmdd>…` (ví dụ `main-2dcff27c-20261127`, `056ed5db-20261123`), ghim `@sha256:`, hoặc là semver đủ ba số như `v1.10.1`, `8.15.3`.

`--strict` chỉ xét service `prod` nằm trong phạm vi `--role` đang chọn. `behind-main` không làm strict thất bại vì giữa lúc merge và lúc release thì prod luôn kém main vài commit.

## Sổ phiên bản

`$SECAI_RELEASES_DIR/versions.jsonl`, mặc định `~/secai-releases/versions.jsonl`. Mỗi dòng một JSON, chỉ nối thêm, quyền 0600, ghi dưới `flock` (snapshot đọc, so và ghi trong cùng một khoá). Không mở qua symlink.

```json
{"ts": "2026-11-27T07:32:39Z", "service": "gateway",
 "containers": ["secai-docker-gateway", "secai-translation-gateway", "secai-translation-worker"],
 "action": "release",
 "from": {"ref": "secai-agent-gateway:main-033b2aa3-20261123", "image_id": null, "revision": null},
 "to": {"ref": "secai-agent-gateway:main-2dcff27c-20261127", "image_id": "sha256:2955a2c3…", "revision": "2dcff27c"},
 "release_dir": "/home/ict/secai-releases/main-2dcff27c-20261127", "by": "ict", "note": null}
```

| `action` | Ai ghi | Khi nào |
| --- | --- | --- |
| `release`, `rollback` | `scripts/release-prod.sh` qua `record` | Sau khi switch xong (sau RELEASE-NOTES), sau `rollback --yes` |
| `baseline` | `snapshot` | Lần đầu thấy một service trong sổ; hoặc ghim `image_id` khi dòng cuối của container chỉ có ref |
| `observed` | `snapshot` | Image đang chạy khác với dòng cuối của container đó trong sổ, tức là đã đổi ngoài release được ghi |
| `backfill` | `backfill --yes` | Lịch sử dựng lại từ thư mục release cũ |

Sổ không bao giờ chứa env. Khi đọc, mọi trường lạ trong một dòng (nếu ai đó sửa tay) bị bỏ, chỉ trả ra các trường ở trên, đúng kiểu chuỗi; dòng hỏng (JSON lỗi, bị cắt giữa ký tự, sai kiểu) bị bỏ qua kèm cảnh báo thay vì làm lệnh dừng. Khi ghi, nếu dòng cuối bị cụt (ghi dở, sửa tay thiếu xuống dòng) thì chèn xuống dòng trước, để dòng mới không dính vào dòng hỏng. Trạng thái hiện tại của một container là `to` của dòng mới nhất theo `ts` có chứa container đó; backfill mang `ts` của quá khứ nên không đè lên các dòng mới.

`image_id` để `null` khi không chắc: image đã bị xoá, hoặc trong backfill ref là tag trôi (gán image hiện tại của tag đó cho quá khứ là sai). Khi một trong hai phía thiếu `image_id`, sổ so sánh theo ref; lần `snapshot` kế tiếp ghi một dòng `baseline` để ghim `image_id` đang chạy, nên về sau tag đó bị dựng lại và container được tạo lại từ cùng ref thì vẫn ra `observed`.

`snapshot` bỏ qua cả lượt chạy (thoát 0, không ghi gì) khi `release-prod.sh` đang giữ `gateway-deploy.lock` hoặc `translation-deploy.lock`, để một lần release đang switch không bị ghi nhầm thành `observed`. Việc kiểm tra đọc `/proc/locks`, không tự lấy lock, nên timer không bao giờ làm `flock -n` của release thất bại.

### Nối với `release-prod.sh`

Sau khi ghi `RELEASE-NOTES.md`, `release-prod.sh run` gọi `record --action release` với `--from` là tag cũ, `--to` là tag mới; `rollback --yes` gọi `record --action rollback` với hai tag đảo lại. Script gọi `versions.py` cạnh nó (`$(dirname "$0")`), truyền `SECAI_RELEASES_DIR` theo biến `RELEASES`, giới hạn 60 s. Bên trong, `record` chỉ chờ Docker 15 s mỗi lệnh; Docker treo thì vẫn ghi được ref (không có `image_id`). Ghi sổ lỗi chỉ in `>> cảnh báo: ...`, không đổi mã thoát. Nếu dòng release bị lỡ, lần `snapshot` kế tiếp sẽ ghi `observed`.

### Backfill

Quét các thư mục con của `$SECAI_RELEASES_DIR` (và từng `--extra-dir`):

- `state.env` của `release-prod.sh`: chỉ đọc `PREV_TAG` và `NEW_TAG`, và chỉ khi thư mục có `RELEASE-NOTES.md` (switch đã xong). Mốc thời gian lấy từ tiêu đề `# Release … (ISO)`, không có thì mtime của file.
- Profile JSON dạng compose ở thư mục release và một tầng con (bỏ `tree/`): chỉ lấy `services.*.image` và `services.*.container_name`, mốc là mtime của file. File lớn hơn 1 MiB và file có tên chứa `credential`, `token`, `secret`, `passw`, `key`, `.env` không được mở. Container không thuộc catalog (canary, candidate, smoke) bị bỏ.
- Profile có tên kiểu `before`, `prev`, `old`, `rollback`, `backup` là trạng thái trước; nó được xếp ngay trước bản mới của cùng thư mục, và chỉ dùng khi ref là ID hoặc tag bất biến.

Với từng container, các quan sát được xếp theo thời gian; mỗi lần image đổi thành một dòng `backfill`. Các container đổi cùng image trong cùng thư mục, cách nhau không quá 120 s, gộp thành một dòng mang mốc muộn nhất, trừ khi một container trong dòng đó đã đổi tiếp ở giữa. Chạy lại không ghi trùng: dòng có cùng service, cùng tên thư mục release và cùng `to.ref` với một dòng đã có (kể cả dòng `release` do `record` ghi) bị bỏ qua.

Trên `~/secai-releases` ngày 2026-11-27, kế hoạch backfill có 62 dòng, từ 2026-10-04 tới release `main-2dcff27c` (đã đọc lại output: không có giá trị nào từ `environment`). Backfill chỉ thấy những gì còn lại trong thư mục release; các lần đổi làm tay không để lại profile thì không có trong lịch sử.

## Trang web chỉ xem

```sh
python3 -I scripts/versions.py serve               # cổng 18190
ssh -L 18190:127.0.0.1:18190 <máy-prod>            # rồi mở http://127.0.0.1:18190/ trên máy mình
```

Server chỉ bind `127.0.0.1`, không có cờ nào để mở ra ngoài. Chỉ nhận GET, method khác trả 405. Header `Host` phải là `127.0.0.1`, `localhost` hoặc `[::1]` (chặn DNS rebinding), sai thì 403. Kết quả Docker được giữ 10 s.

| Đường dẫn | Nội dung |
| --- | --- |
| `/` | Bảng service, version, revision, ngày, cờ; mỗi service mở ra được lệnh nâng cấp/rollback dạng chữ để copy và 10 dòng lịch sử gần nhất. Tự làm mới 60 s, sáng/tối theo hệ điều hành, dùng được trên điện thoại, không tải tài nguyên ngoài, không JavaScript |
| `/?role=all` | Thêm stack dev |
| `/api/status` | JSON giống `status --json` (`?role=` như trên) |
| `/api/history?service=ID&n=50` | JSON lịch sử; bỏ `service` để lấy mọi service |

`scripts/install-versions-ui.sh` cài hai unit `systemd --user`: `secai-versions-ui.service` (serve) và `secai-versions-snapshot.timer` (snapshot mỗi giờ). Runner lấy `scripts/versions.py` và `deploy/services.json` từ `origin/main` mới nhất mỗi lần chạy, giống timer `wt-prune`. Gỡ bằng `--remove` (sổ giữ nguyên). Script chưa được chạy trên máy prod; cài sau khi PR đã merge. Bản serve đang chạy chỉ nạp code mới khi `systemctl --user restart secai-versions-ui`.

## Trạng thái đo ngày 2026-11-27

`status` trên Docker thật:

| Service | Version | Revision | Cờ |
| --- | --- | --- | --- |
| gateway ×3 | `secai-agent-gateway:main-2dcff27c-20261127` | 2dcff27c (env) | không |
| translation-frontend | `1e0ecfacdb52` | 4c590be8 (label) | `bare-id` |
| report-backend | `ac510772daeb` | c4c3c802 (label) | `bare-id` |
| review-frontend | `secai-agent-gateway:review-mvp-20261004` | không có | `floating-tag`, `no-revision` |
| review-canary | `secai-agent-gateway:review-mvp-20261027b` | không có | `floating-tag`, `no-revision` |
| translation-converter | `secai-doc-convert-svc:1` | không có | `floating-tag` |
| pentestcode-pool ×25 | `secai-pentestcode:1.3.14-tools` | 0d9b296a (label, repo pentestcode) | `floating-tag:minor` |
| qdrant | `qdrant/qdrant:v1.10.1` | không có | không |
| elasticsearch | `docker.elastic.co/elasticsearch/elasticsearch:8.15.3` | f97532e6 (label của image upstream) | không |
| neo4j, redis | `neo4j:5.20-community`, `redis:7-alpine` | không có | `floating-tag` |

Revision `4c590be8` và `c4c3c802` không có trong repo này (dựng từ nhánh không còn), nên hai service đó không có `behind-main`.

## Đề xuất đưa về tag bất biến

Đây chỉ là khuyến nghị, chưa làm gì trên máy prod. Mỗi việc dưới đây đổi container đang chạy nên phải đi qua script có lock, kiểm ownership và rollback, đúng quy tắc trong `CLAUDE.md`.

- `translation-frontend`, `report-backend` (ID trần): gắn tag bất biến cho đúng image đang chạy, theo revision trong label, ví dụ `secai-agent-gateway:fe-4c590be8-20261007` và `secai-agent-gateway:report-c4c3c802-20261005`, rồi sửa profile compose cho trỏ tag thay vì ID. Gắn tag không đổi container; đổi profile chỉ có hiệu lực ở lần `up` kế tiếp, nên gộp vào lần release sau của từng service.
- `review-frontend`, `review-canary` (tag trôi, không revision): lần dựng tới đặt `--label org.opencontainers.image.revision=<sha>` và tag dạng `review-<sha8>-<ngày>`. Không gắn lại `review-mvp-*` cho image khác nữa.
- `translation-converter`: chuyển từ `docker run` tay sang compose (đã có service `translation-converter` trong `stack/compose.yml`), image tag theo sha của nguồn (`stack/images/converter/`).
- `neo4j:5.20-community`, `redis:7-alpine`: ghim bản vá (`neo4j:5.20.0-community`, `redis:7.x.y-alpine`) hoặc ghim `@sha256:` theo digest đang chạy, để lần `pull` sau không âm thầm đổi image.
- `secai-pentestcode:1.3.14-tools`: thêm sha của repo pentestcode vào tag (`1.3.14-tools-<sha8>`) vì label revision đã có sẵn.
- Viết script release có gate cho `translation-frontend` và `review-*` theo khuôn `release-prod.sh`, rồi cho các script đó gọi `versions.py record`.

## Kiểm chứng

```sh
scripts/dev.sh test tests/test_versions.py
bash -n scripts/release-prod.sh scripts/install-versions-ui.sh
```

Test dùng `docker` và `git` giả trên `PATH`, sổ trong thư mục tạm. Một secret giả đặt trong env container, env image, label, `Cmd`, `environment` của profile JSON và `state.env`; test kiểm nó không xuất hiện trong bất kỳ đầu ra nào (status, history, snapshot, record, backfill, HTTP, sổ). Test cũng phủ: từng cờ strict, ID ngắn, sổ có dòng hỏng hoặc dòng cuối bị cụt, `flock`, ghim `image_id`, snapshot nhường lock release, thứ tự gộp của backfill. `release-prod.sh run --yes` và `rollback --yes` được chạy thật trên docker/git/curl giả, có và không có lỗi ghi sổ, để chắc mã thoát không đổi.
