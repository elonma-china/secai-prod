# Gói phát hành GHCR: dry-run trên máy dev, chưa push, chưa thử VM (2026-11-27)

Nhánh `feat/stack-ghcr`. Kế hoạch: `docs/superpowers/plans/2026-11-27-stack-ghcr.md`. Phần này ghi những gì đã chạy thật trên
máy dev (máy prod) và những gì còn chờ.

## Tiến độ phát hành thật (2026-11-28)

- 3 package image GHCR đã public; dữ liệu chuyển sang Docker Hub private `docker.io/secai1999/secai-data` (gói miễn phí).
- `secai-data:2026.11.27` đã push: digest đọc lại bằng token chỉ đọc `sha256:67508336e64c5692c4ee79c69e8e457ae2105fc6c903ef6c3a647a817f062179`.
- Lần chạy V1 đầu (c33427b7) dừng trước mọi push ở bước kiểm tag (tôi thay `registry.py` giữa lúc chạy); đã sửa, chạy lại.

## Trạng thái cũ (trước 2026-11-28): CHỜ chủ dự án bật Public 3 package image

Điều kiện push thật (chủ dự án, 2026-11-27): `secai-agent-gateway`, `secai-pentestcode-vm`, `secai-stack` phải **public**;
`secai-data` phải **private**. Kiểm lần cuối (23:05 +07) bằng
`gh api '/users/elonma-china/packages?package_type=container' --jq '.[] | [.name, .visibility] | @tsv'`: cả bốn package đều
**private** (mới có placeholder `:init` 10 kB, push lúc 19:40 +07). Vì vậy **chưa push image/dữ liệu thật và chưa thuê VM**.
`publish.sh` tự kiểm visibility ngay trước khi push và dừng (mã 5) nếu sai.

## Dry-run `publish` (đủ gate, không login, không push)

`stack/publish.sh publish --ref origin/feat/stack-ghcr --dry-run`, commit `d7c6cc0e`, 37 phút.

| Bước | Kết quả |
|---|---|
| Gate 1: pytest toàn bộ trên `git archive d7c6cc0e` | **3520 passed, 18 skipped, 7 xfailed** (19 phút 6 giây) |
| Gate 2: build FULL gateway (`GIT_SHA`, nhãn OCI), import app, `/app/src` == `git archive d7c6cc0e src` | đạt; `secai-agent-gateway:2026.11.27-d7c6cc0e` = `sha256:8079dbe9…` |
| Gate 3: smoke `secai-stack-gateway-smoke-20261127`, chỉ `127.0.0.1`, mạng prod chỉ đọc | đạt (`/health` đúng `git_sha`, không dep nào tệ hơn prod, `/ai/v3/translate/languages` 200); container đã xoá |
| Image VM từ `git archive d7c6cc0e pentestcode stack/images/pentestcode-vm` | `secai-pentestcode-vm:2026.11.27-d7c6cc0e` = `sha256:f7cc337e…`, nhãn revision đúng, có bun/nuclei/httpx/python3 |
| Digest image nền (registry gốc, ẩn danh) | qdrant `v1.10.1` `sha256:6a34a799…`, neo4j `5.20-community` `sha256:52d3dec8…`, redis `7-alpine` `sha256:7f7d30b7…`, elasticsearch `8.15.3` `sha256:01c17320…` |
| Quét secret bundle (`stack/` của commit + manifest) | sạch: mọi luật + secret thật của máy, không allowlist |
| Bundle `FROM scratch` | `secai-stack:2026.11.27-d7c6cc0e` = `sha256:cc88b279…` (1,9 MB) |

Mã thoát cuối là 2 vì tôi sửa `publish.sh` trong lúc lần chạy đó đang ở bước cuối (bash đọc script dần dần); mọi bước ở
trên đã in "đạt". Lần dry-run với commit cuối của PR được ghi ở mục "Dry-run cuối" bên dưới.

## Dry-run `publish-data` (đóng gói dữ liệu thật, quét, dựng image, không push)

Lần 1: `publish.sh publish-data --dry-run --neo4j-password-file <tệp 0600 tạm, đã shred>`, 9 phút 13 giây.

- `pack.sh data`: Neo4j xuất chỉ đọc 587.393 nút / 5.414.825 quan hệ; snapshot Qdrant `cve_kb_hybrid` 842.928 điểm (tạo rồi
  xoá trên Qdrant prod; hai snapshot cũ giữ nguyên); gói 5,2 GB, 32 tệp trong `SHA256SUMS`.
- Quét: 1.357.236 nội dung, 7,44 GB (gồm payload Qdrant qua API chỉ đọc). Secret thật: **0 khớp**. Một phát hiện ngoài corpus:
  `exploitdb-mcp-server/labs-cve2026/CVE-2026-65008-grav-cms-rce/exploit/exploit.py:48` (luật gán password). Dòng đó là
  `self.password = password` (gán từ tham số cùng tên), dương tính giả. Script dừng đúng thiết kế, không dựng image.
- Đã thu hẹp luật gán: giá trị là tham chiếu tới biến/thuộc tính **cùng tên** (`self.password = password`, `api_key=args.api_key`)
  không tính là literal; `password=password123`, `password=x` vẫn bị bắt. Có test.

Lần 2: `publish-data --dry-run --from-pkg <gói lần 1>` + quét payload Qdrant, 9 phút 30 giây.

- Quét sạch: 0 phát hiện chặn, 0 khớp secret thật. Đếm trong corpus công khai (không chặn, ghi vào hồ sơ phát hành):

| Cây corpus | Đếm theo luật |
|---|---|
| exploitdb | password 2.578, api_key 31, private key 40 |
| poc-sources | password 2.280, api_key 153, private key 150, AKIA 64, sk- 35 |
| poc/store | password 986, api_key 78, private key 17, AKIA 12, sk- 2 |
| poc/catalog.db | password 160, api_key 38, private key 4, AKIA 8, sk- 8 |
| exploitdb-mcp-server/node_modules | password 30, api_key 1, private key 2 |
| payload Qdrant (API) | password 23, api_key 5 |
| snapshot Qdrant (SST nén) | password 12, api_key 4 |

- Image `secai-data:2026.11.27` dựng cục bộ: `sha256:24978444…` (chỉ trên máy dev, không push). Gói ở
  `~/secai-releases/data-2026.11.27/pkg` để dùng lại (`--from-pkg`) hoặc rsync lên VM.

Nguồn secret thật đã nạp (chỉ đếm, giá trị không bao giờ in/ghi): `gh auth token` 1; `secai-docker/.env` 6; `.env` checkout
chính 4; compose prod 0; `run_sync.sh` 1; hồ sơ `~/secai-releases/*/*.json` 15 (1 tệp không đọc được:
`review-reward-fix-20261005/devspace-review-tokens.json`, chủ uid 10001, mode 0600); khoá Vast 1; token HF 1.
**Lưu ý:** mật khẩu Neo4j prod dài 9 ký tự nên bị luật "bỏ giá trị dưới 12 ký tự" loại, tức là không được so khớp.

## Review độc lập (5 góc nhìn, 28 phát hiện, 26 xác nhận, đã sửa hết)

Đáng kể nhất:
- Bộ nạp secret thật coi `TOKENIZER`, `*_FILE` là tên secret, nên hash/đường dẫn tokenizer trong `stack/compose.yml` sẽ làm
  bundle luôn dừng mã 6. Đã đổi sang so theo từng đoạn tên khoá, bỏ `_FILE/_PATH/_DIR/_SHA256/...`, `GPG_KEY`, giá trị đường dẫn.
  Đã đọc thêm `Env` của docker inspect trong hồ sơ JSON.
- `secai rollback`/`upgrade` có thể để `stack/` và `.env` lệch bản đang chạy khi kéo image lỗi: giờ kéo image trước, đổi tệp
  trong subshell, khôi phục khi preflight trượt, và `upgrade` cùng bản tự sửa trạng thái lệch.
- `--from-pkg` không quét payload Qdrant: giờ bắt buộc `--qdrant-url` và số điểm đã quét phải khớp MANIFEST.
- `check3` của bài thử VM đọc cache `/health` 30 s: thêm bước chờ gateway thấy lại stub.
- Khác: seed nhiều blob, regex quota không bắt nhầm timeout/hex, `git fetch` trước khi chọn ref, kiểm "bất biến" fail-closed và
  kiểm lại trước push, `promote` kéo theo digest, khoá phát hành, bắt lỗi thiếu image nền, kiểm manifest, quét phần đuôi sau EOF
  của tar, chặn `$ ' " \ \`` trong giá trị cấu hình, kiểm lỗi ghi `.env`, không in token review, README sudo không làm lộ khoá.

## Test

- `scripts/dev.sh test tests/ -k stack`: xem dòng tổng kết ở mục "Dry-run cuối".
- `bash -n` các script, `git diff --check`: sạch.

## Chưa làm / chưa kiểm

- **Push thật** (gateway, VM, bundle, `stable`, `secai-data`): chờ 3 package image public. Dữ liệu private: mỗi lần cài kéo
  ~5,5 GB tính vào lưu lượng GitHub Packages của chủ package (gói Free 1 GB/tháng cho package private) nên có thể bị chặn tới khi
  gắn thanh toán; push dữ liệu cũng có thể bị chặn vì quota lưu trữ 500 MB. `publish.sh` dừng với mã 7, không lách.
- **Thử trên VM Vast**: chưa thuê. Cần image public. Kế hoạch: `EVAL_MODE=ghcr stack/eval-vast.sh up`, rồi `ghcrtest` với
  `GHCR_TEST_DATA_PKG=~/secai-releases/data-2026.11.27/pkg` (rsync gói, vì VM không được giữ token gh có quyền ghi và chưa có
  token chỉ đọc), `collect`, `down`, chứng minh instance biến mất.
- Model thật: chỉ có stub (`stack/acceptance/stubs/model_stub.py`); chứng minh đường ống, không chứng minh chất lượng.
- Đường kéo `secai-data` bằng token chỉ đọc trên máy đích chỉ được kiểm bằng test giả (docker giả), chưa kiểm với GHCR thật.
