# Backup SQLite hằng ngày (cùng máy) và khôi phục

`scripts/backup-sqlite.py` sao mỗi ngày các DB SQLite của service thuộc dự án (gateway/session, expert review, báo cáo, job dịch) vào `~/secai-backups/sqlite/`. Timer `systemd --user` cài bằng `scripts/install-backup-timer.sh`. Bản sao nằm **cùng máy, cùng đĩa** với prod: chống xoá nhầm, DB hỏng, migration lỗi; không chống mất máy hay hỏng đĩa (OPS-BAK-3 vẫn FAIL, cần thêm bản ngoài máy).

## Lệnh

```sh
python3 -I scripts/backup-sqlite.py --dry-run    # docker inspect + in kế hoạch; không chạy container, không ghi gì
python3 -I scripts/backup-sqlite.py              # backup thật (chỉ đọc DB prod)
scripts/install-backup-timer.sh                  # cài timer 01:30–01:45 hằng ngày (chỉ sau khi script đã lên origin/main)
scripts/install-backup-timer.sh --remove         # gỡ timer/runner, giữ nguyên bản backup
systemctl --user start secai-sqlite-backup.service; journalctl --user -u secai-sqlite-backup.service   # chạy tay, xem log
scripts/dev.sh test tests/test_backup_sqlite.py  # test offline, docker/systemctl giả
```

Tham số: `--backup-dir` (mặc định `$SECAI_BACKUP_DIR`, không có thì `~/secai-backups`), `--containers a,b` (hoặc `SECAI_BACKUP_CONTAINERS`), `--image` (hoặc `SECAI_BACKUP_IMAGE`), `--keep-days 7`, `--min-free-gb 5` (từ chối sao một DB nếu sau khi sao đích còn ít hơn ngần ấy), `--timeout` (giây, mặc định 2 giờ).

Mã thoát: 0 đủ; 1 có DB hay container lỗi (DB khác vẫn được sao và công bố, nhưng **không dọn** bản cũ; không sao được DB nào thì không tạo thư mục trong `sqlite/`, manifest nằm lại ở `.staging/`); 2 sai tham số hay thiếu docker. Unit `secai-sqlite-backup.service` lỗi thì OPS-MON-5 báo `failed`.

## DB nào được sao

Script đọc `docker inspect` của `secai-docker-gateway`, `secai-translation-gateway`, `secai-translation-worker`, `secai-report-backend`, `secai-review-canary`, `secai-review-frontend`. Từ env **chỉ lấy biến `*_DB_PATH`** (giá trị là đường dẫn), không in hay ghi biến nào khác; thêm đường dẫn mặc định của `config.py`/`translation/settings.py` cho biến không đặt (tính từ `WorkingDir`). Mỗi đường dẫn được gắn với volume/bind chứa nó; cùng volume + cùng file chỉ sao một lần dù nhiều container dùng (`MANIFEST.json` ghi đủ container/biến dùng file đó).

- Biến khai báo rõ mà không có file: lỗi (mã thoát 1). Đường dẫn mặc định không có file: bỏ qua, ghi `skipped` trong manifest.
- Đường dẫn không nằm dưới volume/bind (lớp container): không sao được, in cảnh báo. Hiện có `REVIEW_DB_PATH=/app/review-data/reviews.db` của hai container dịch (không mount `/app/review-data`) và `GATEWAY_DB_PATH` của `secai-review-frontend` (chỉ phục vụ static, không mount gì).
- Không thuộc phạm vi: DB không qua biến `*_DB_PATH` (catalog PoC `POC_CATALOG_DB`, `poc.sqlite3`/`catalog.db` cũ), Qdrant, Neo4j.

Kết quả lần chạy thật 2026-11-27 23:55 (`~/secai-backups/sqlite/20261127-2355`, 13 DB, 891 MB, 8,4 s, mọi `integrity_check` = `ok`):

| Thư mục/file | Nguồn | Kích thước |
| --- | --- | --- |
| `gateway/gateway.sqlite`, `sessions.sqlite`, `reviews.sqlite`, `jobs.sqlite` | volume `secai-docker_secai-gw-data` (`secai-docker-gateway`; `jobs` là mặc định `translate-v2/jobs.sqlite3`) | 62 MB, 183 MB, 8 MB, 36 KB |
| `translation/gateway.sqlite`, `sessions.sqlite`, `jobs.sqlite`, `translation-worker.sqlite` | volume `secai-translation-data` (gateway + worker dịch) | 57 KB, 61 KB, **679 MB**, 57 KB |
| `report/reports.sqlite`, `report/gateway.sqlite` | bind `~/secai-workspaces/secai-report-devspace/{data,docker-report/gateway-data}` | 36 KB mỗi file |
| `review-canary/reviews.sqlite`, `gateway.sqlite`, `sessions.sqlite` | volume `secai-review-data`, thư mục `canary/` | 164 KB, 36 KB, 86 KB |

`translation/jobs.sqlite` có 165 786/165 795 trang trống (dữ liệu thật vài chục KB): bản sao giữ nguyên các trang như nguồn, không VACUUM (VACUUM có thể đổi `rowid` của bảng không có `INTEGER PRIMARY KEY`). Giữ 7 ngày (7–8 bản, tuỳ giờ chạy ngẫu nhiên) tốn khoảng 6–7 GB; VACUUM chính DB prod trong một lần bảo trì sẽ giảm còn vài trăm MB.

## Bố cục đích và lưu giữ

```text
~/secai-backups/                     0700  (= SECAI_BACKUP_DIR; OPS-BAK-2 đo thư mục con sqlite/)
├── .sqlite-backup.lock              flock: hai lần chạy chồng nhau thì lần sau thoát mã 1
├── .staging/sqlite-YYYYMMDD-HHMM/   đang ghi; đủ xong mới rename sang sqlite/
└── sqlite/YYYYMMDD-HHMM/            0700
    ├── SHA256SUMS                   0600, `sha256sum -c` được
    ├── MANIFEST.json                0600: nguồn (volume/bind, đường dẫn trong mount), container/biến dùng, phương pháp, integrity, số trang, sha256
    └── <service>/<db>.sqlite        0600, chủ là user chạy backup; file tự đủ (journal_mode=DELETE, không cần -wal)
```

Dọn: chỉ sau một lần chạy **không lỗi**; chỉ xoá thư mục trong `sqlite/` có tên khớp đúng `^\d{8}-\d{4}$`, là thư mục thật (không symlink, không file) và có thời điểm trong tên cũ hơn 7 ngày; thêm staging `.staging/sqlite-YYYYMMDD-HHMM` sót lại quá 1 ngày. Không đụng gì khác trong `~/secai-backups`.

## Cách sao: nhất quán và không đụng prod

Bản sao chạy trong container tạm, không `docker exec/stop/restart` container prod:

```text
docker run --rm -i --name secai-sqlite-backup-<YYYYMMDD-HHMM> --network none --read-only --tmpfs /tmp
  --user 0:0 --cap-drop ALL --cap-add DAC_OVERRIDE --cap-add CHOWN --security-opt no-new-privileges --memory 1g
  --mount type=volume|bind,source=<nguồn prod>,target=/src/<i>,readonly      (mỗi nguồn một lần)
  --mount type=bind,source=~/secai-backups/.staging/sqlite-<stamp>,target=/out
  --mount type=bind,source=scripts/backup-sqlite.py,target=/job/backup-sqlite.py,readonly
  --entrypoint python3 <image secai-agent-gateway:* của secai-docker-gateway> -I /job/backup-sqlite.py --inner
```

- Image là image gateway đang chạy (có Python 3.12, SQLite 3.46.1), không build hay pull gì. Danh sách job đi qua stdin, kết quả qua stdout; argv chỉ có đường dẫn.
- Trong container: `sqlite3.connect("file:<src>?mode=ro")` rồi `Connection.backup()` một bước (một read transaction, nên là snapshot nhất quán), `PRAGMA journal_mode=DELETE` và `PRAGMA integrity_check` trên **bản sao**. Không `cp` file DB đang chạy.
- Vì sao root + `DAC_OVERRIDE` an toàn: file prod thuộc uid 10001 (có file 0600 trong thư mục 0700) và file report thuộc uid 1000, nên cần quyền đọc vượt chủ sở hữu. Mọi nguồn prod mount `readonly`: kernel từ chối ghi bất kể capability. Chỗ ghi duy nhất là thư mục staging; file sao được tạo 0600 và `fchown` về user chạy backup ngay khi tạo (`CHOWN`). Không mạng, rootfs read-only, `no-new-privileges`, `--rm`.
- `--mount` thay `-v`: nguồn bind không còn thì docker báo lỗi chứ không tự tạo thư mục rỗng.

### Thử đọc WAL trên mount read-only (2026-11-27)

Writer chạy trên host, bản sao đọc qua container tạm với mount `:ro` (image prod, SQLite 3.46.1). Kiểm nhất quán bằng bất biến `sum(bal) = 10000` và bảng log không thủng số:

| Tình huống | Kết quả |
| --- | --- |
| WAL, writer đang ghi liên tục (DB 234 MB, autocheckpoint mặc định), 6 lần sao | Cả 6 `integrity_check=ok`, bất biến giữ, có cả commit mới nhất; ~0,5 s/lần |
| WAL, writer mở nhưng nghỉ, 595 commit chỉ nằm trong `-wal` (`wal_autocheckpoint=0`) | Bản sao đủ 595 dòng (đọc `-shm` read-only, vẫn giữ read-lock) |
| WAL, writer bị `kill -9`, còn `-wal`/`-shm` | Đủ 376/376 dòng đã commit |
| Rollback journal (DELETE), writer đang ghi, 6 lần sao | Cả 6 ok; lần sao chờ lock của writer (tới 9 s) rồi đọc nhất quán |
| WAL đã đóng sạch (không có `-wal`/`-shm`) | `unable to open database file`: SQLite cần tạo `-shm` mà mount read-only |

Ca cuối được xử lý bằng `immutable=1` có kiểm: không có `-wal`/`-shm` nghĩa là mọi commit đã nằm trong file DB; script so `inode/size/mtime/ctime` của file DB trước và sau khi đọc (và chờ nếu file vừa được ghi trong 2 s), đổi thì đọc lại, tối đa 5 lần, quá thì báo lỗi. Chỉ có một trong hai file `-wal`/`-shm` thì từ chối. Vì vậy không cần mount nguồn prod `rw`. Lần chạy thật ngày 2026-11-27: 13/13 DB đi đường `ro` thường (DB WAL nào cũng đang có `-wal`/`-shm`, còn lại là rollback journal).

### Nguồn bind đã đổi chỗ: `secai-report-backend`

`docker inspect` của `secai-report-backend` ghi bind `/home/ict/secai-report-devspace/{data,docker-report/gateway-data}`, nhưng thư mục đó đã bị chuyển sang `/home/ict/secai-workspaces/secai-report-devspace/` sau khi container chạy (từ 2026-10-25); đường dẫn cũ là thư mục rỗng do root tạo lại ngày 2026-11-23. Container vẫn thấy inode cũ, nên script lấy nguồn bind thật từ `/proc/<pid>/mountinfo` và in cảnh báo khi khác inspect; nguồn đã bị xoá (`//deleted`) thì báo lỗi cho DB đó.

Rủi ro ngoài phạm vi backup: nếu `secai-report-backend` được tạo lại bằng compose với đường dẫn cũ, report sẽ chạy trên thư mục rỗng. Cần sửa nguồn bind trong compose của report trước lần tạo lại tới.

## Đối chiếu tiêu chí ops (`docs/criteria/ops.md`, nhóm BAK)

| ID | Sau khi cài timer |
| --- | --- |
| OPS-BAK-1 | `secai-sqlite-backup.timer` phủ phần SQLite. Tiêu chí vẫn FAIL "chưa phủ: qdrant, neo4j" cho tới khi có timer backup cho hai kho đó. Không đặt tên `secai-backup*.timer` vì đó là quy ước cho một timer chung phủ mọi kho |
| OPS-BAK-2 | `ops-check.py` mặc định đọc `~/secai-backups` (cùng mặc định với script, đổi bằng `SECAI_BACKUP_DIR`/`--backup-dir`); `sqlite/` mới hơn 26 giờ thì không còn trong danh sách thiếu. Qdrant, Neo4j vẫn thiếu |
| OPS-BAK-3 | FAIL theo thiết kế: `~/secai-backups` cùng đĩa với `secai-data`. Cần rsync/rclone (mã hoá) sang đĩa hoặc máy khác |
| OPS-BAK-4 | Chưa có biên bản diễn tập khôi phục `restore-drill-YYYYMMDD.md` |
| OPS-BAK-5 | Không đổi (PASS: DB gateway đều nằm trên mount) |

`secai-versions-snapshot.timer` hiện cũng bị OPS-BAK-1 đếm là timer backup (tên có `secai` và `snapshot`) dù không phải.

## Khôi phục

Khôi phục là thao tác đổi prod: cần user cho phép, làm từng DB, giữ bản đang có.

1. Chọn bản, kiểm checksum và tra nguồn trong manifest:

   ```sh
   B=~/secai-backups/sqlite/20261127-2355
   (cd "$B" && sha256sum -c --quiet SHA256SUMS) && echo checksum-ok
   python3 -I -c 'import json,sys; [print(d["file"], d["mount"]["kind"], d["mount"]["source"], d["path_in_mount"], [u["container"] for u in d["used_by"]]) for d in json.load(open(sys.argv[1]))["databases"] if d.get("ok")]' "$B/MANIFEST.json"
   ```

2. Dừng **mọi** container trong `used_by` của DB đó qua compose của service (với nhóm gateway: theo quy trình trong `CLAUDE.md`, không `docker stop` tay). Writer còn chạy thì sẽ ghi đè hoặc giữ `-wal` cũ.
3. Thay file. Volume (root-only trên host) dùng container tạm; ví dụ `sessions.db` của `secai-docker-gateway`:

   ```sh
   docker run --rm -i --network none --user 0:0 --cap-drop ALL --cap-add DAC_OVERRIDE --cap-add CHOWN --cap-add FOWNER \
     --name secai-sqlite-restore-$(date +%Y%m%d) \
     --mount type=volume,source=secai-docker_secai-gw-data,target=/data \
     --mount type=bind,source="$B/gateway",target=/bk,readonly \
     --entrypoint python3 <image gateway prod> -I - /data/sessions.db /bk/sessions.sqlite <<'PY'
   import os, shutil, sqlite3, sys, time
   dst, src = sys.argv[1], sys.argv[2]
   st = os.stat(dst)
   tag = time.strftime("pre-restore-%Y%m%d-%H%M")  # giờ UTC trong container
   for s in ("", "-wal", "-shm", "-journal"):      # giữ bản đang có, kèm -wal/-shm của nó
       if os.path.exists(dst + s):
           os.rename(dst + s, f"{dst}.{tag}{s}")
   shutil.copyfile(src, dst)
   os.chown(dst, st.st_uid, st.st_gid)              # chủ cũ, thường 10001:999
   os.chmod(dst, st.st_mode & 0o777)
   db = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
   print("integrity:", db.execute("PRAGMA integrity_check").fetchone()[0])
   PY
   ```

   Không bao giờ để `-wal`/`-shm` cũ nằm cạnh file vừa khôi phục: SQLite sẽ áp WAL cũ lên DB mới và làm hỏng nó. Bind thuộc user (report) làm tương tự trên host: đổi tên DB cùng `-wal`/`-shm` rồi `cp` bản sao vào, giữ chủ và quyền cũ.
4. Khởi động lại service qua compose, kiểm `/health` và dữ liệu nghiệp vụ (vd danh sách report, session của một user).
5. Ổn định rồi mới xoá `*.pre-restore-*`.

Bước 3 đã chạy thử ngày 2026-11-27 trên một bản sao trong thư mục tạm (không phải volume prod), DB đích còn `-wal`/`-shm` dở dang: đổi tên đủ 3 file, chép bản sao vào, giữ chủ và quyền, `integrity_check=ok`. Chưa diễn tập khôi phục trên prod hay stack dev (OPS-BAK-4).
