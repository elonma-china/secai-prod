# `stack/` - gói triển khai SecAI cho một máy mới

Một thư mục, một `compose.yml`, một `.env`: dựng backend SecAI (gateway + kho dữ liệu + các dịch vụ tuỳ chọn) trên máy
chỉ có Docker, không cần đường dẫn riêng của máy nào. Thay thế `deploy/docker-compose.yml` và `deploy/up.sh` (đã đánh dấu
deprecated).

## Gói này là gì, không là gì

**Có:** gateway IAS v2, Qdrant, Neo4j, Redis, Elasticsearch (core, luôn chạy); tuỳ chọn theo profile: engine exploit-v2
(2 container VM), dịch tệp/ảnh, backend báo cáo, UI review. Cộng script đóng gói/khôi phục dữ liệu, kiểm tra, nghiệm thu.

**Không có (ngoài phạm vi, chủ dự án đã loại):**

- Serving model (LLM, embedding, rerank, OCR, Hy-MT2) và GPU. Máy đích chỉ chạy container; model là **endpoint ngoài**
  khai trong `.env`. Tên model trong `.env` phải khớp đúng serving, lệch thì 404 và RAG rơi về BM25-only dù health xanh.
- Lab CVE và executor PoC-Verify. Engine exploit chỉ cần mạng lab (`POC_LABS_NETWORK`) và `VERIFY_ALLOWED_TARGETS`.
- Frontend sản phẩm (IAS), Devspace và `translation-frontend`.

## Bố cục

| Đường dẫn | Việc |
|---|---|
| `compose.yml` | Toàn bộ service, mạng, volume. Không `container_name`, không `:latest`, không docker.sock |
| `.env.example` | Mẫu cấu hình, mỗi biến một chú thích. `.env` thật (0600) và `secrets/` bị gitignore |
| `bootstrap.sh` | `init` tạo `.env` và sinh secret; `preflight` kiểm `.env`, đường dẫn, quyền, máy chủ |
| `pack.sh` | `image` dựng image gateway từ `git archive`; `data` đóng gói dữ liệu từ máy nguồn |
| `restore-data.sh` | Nạp gói dữ liệu vào `DATA_ROOT` (kiểm sha256 trước, không ghi đè dữ liệu có sẵn) |
| `check.sh` | `env`, `config`, `models`, `health`, `all`: kiểm trước và sau `up` |
| `eval-vast.sh`, `destroy-eval.sh` | Chạy thử gói trên một máy Vast thuê rẻ rồi huỷ đúng ID đã ghi. Chỉ cho người vận hành, cần ngân sách do chủ dự án duyệt |
| `images/` | Dockerfile dựng trên máy đích: `pentestcode-vm`, `converter`, `review-ui`, `tunnel` |
| `lib/` | `pool.sh` (sinh `IAS_SERVER_POOL`), `netns_watch.py` (worker dịch tự thoát khi mất netns) |
| `acceptance/`, `ACCEPTANCE.md` | Bộ ca nghiệm thu UI (Playwright) và API, số gốc prod, cách đọc kết quả |

## Dựng trên máy mới (5 bước)

Yêu cầu: Docker Compose v2, `zstd`, `curl`, `sha256sum`, `python3`; `vm.max_map_count >= 262144` (Elasticsearch;
`sudo sysctl -w vm.max_map_count=262144`); RAM đủ cho 2 container VM (mỗi cái `VM_MEM_LIMIT`, mặc định 48g) cộng các kho
dữ liệu; truy cập registry để kéo image nền (qdrant, neo4j, redis, elasticsearch, ubuntu, alpine, python) hoặc nạp sẵn
bằng `docker load`; internet lúc dựng image VM.

**1. Image.** Image gateway phải dựng từ nhánh này (Dockerfile gốc tạo sẵn `/srv/reports` và `/app/review-data` cho user
app; image dựng từ bản cũ hơn thì ghi báo cáo/review lỗi `Permission denied` trong khi `/health` vẫn xanh):

```sh
stack/pack.sh image                               # in tag ghcr.io/<org>/secai-agent-gateway:main-<sha8>-<ngày>
stack/images/pentestcode-vm/build.sh              # profile exploit; dòng cuối là tag
docker build -t secai-doc-convert:<ngày> stack/images/converter          # profile translation
docker build --build-arg TUNNEL_UID=$(id -u) -t secai-specialist-tunnel:<ngày> stack/images/tunnel   # profile translation
docker build -t secai-review-ui:<ngày> stack/images/review-ui            # profile review
```

Gateway có thể kéo từ GHCR (private: `docker login ghcr.io` bằng token đọc) thay vì dựng. VM, converter, review-ui, tunnel
dựng trên máy đích. Mọi tag phải cố định (không `:latest`). **`docker push` là việc làm tay của chủ dự án**; không script
nào trong gói push image.

**2. Gói dữ liệu.** Trên máy nguồn: `stack/pack.sh data <src_root> <out_dir>` (chi tiết ở mục dưới). Trên máy đích:

```sh
export DATA_ROOT=/srv/secai-data        # đường dẫn tuyệt đối, ghi cùng giá trị vào .env
stack/restore-data.sh --qdrant-url http://127.0.0.1:6333 <thư mục gói>   # Qdrant chưa chạy thì bỏ cờ và làm theo lệnh in ra
```

**3. Cấu hình.**

```sh
cd stack
./bootstrap.sh init         # tạo .env (0600) từ .env.example, sinh secret còn trống, pool, secrets/review-credentials.json
$EDITOR .env
```

Các biến phải điền tay (init để trống): `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `EMBEDDING_API_URL`, `EMBEDDING_MODEL`,
`EMBEDDING_API_KEY`, `RERANKER_API_URL`, `RERANKER_MODEL`, `RERANKER_API_KEY`, `DATA_ROOT`, `GATEWAY_IMAGE` (cộng `VM_IMAGE`,
`CONVERTER_IMAGE`, `TUNNEL_IMAGE`, `REVIEW_UI_IMAGE` khi bật profile tương ứng), `VERIFY_ALLOWED_TARGETS` (profile exploit),
khoá và tên model OCR/dịch (profile translation). `LLM_*` là tên chuẩn; `.env` kiểu cũ dùng `QWEN_*` vẫn chạy (`LLM_*`
thắng, preflight cảnh báo khi hai bên khác nhau). Gateway chọn tên giống pydantic: dòng **có mặt** đầu tiên thắng, kể cả
khi rỗng, nên dòng `LLM_X=` rỗng che `QWEN_X` (LLM_MODEL rỗng làm gateway không khởi động). `.env` do `init` sinh có sẵn
các dòng `LLM_BASE_URL=`, `LLM_API_KEY=`, `LLM_MODEL=` (phải điền) và `LLM_CONTEXT_WINDOW=131072`. Người vận hành giữ
`.env` kiểu prod chỉ có `QWEN_*` thì **xoá các dòng `LLM_*`** (cả `LLM_CONTEXT_WINDOW` nếu dùng `QWEN_CONTEXT_WINDOW`);
preflight và `check.sh` báo lỗi "xoá dòng LLM_X rỗng hoặc điền giá trị" khi thấy dòng rỗng che `QWEN_X`.
Biến `export` trong shell chỉ đi vào phần thay thế `${...}` của compose (ví dụ `ORCA_LOCAL_*` của VM, cổng, image), KHÔNG
vào môi trường gateway: gateway/worker/report chỉ nhận `.env` qua `env_file`. Mọi cấu hình gateway phải nằm trong `.env`. `NEO4J_PASSWORD` do init sinh; compose đòi `DATA_ROOT`, `GATEWAY_IMAGE`,
`NEO4J_PASSWORD` (không đặt thì `docker compose` báo lỗi ngay). Profile translation cần thêm tokenizer HY-MT2
(`tokenizer.json`, `tokenizer_config.json`) trong `TOKENIZER_DIR` (mặc định `stack/tokenizer/`, gitignore).

**4. Preflight.** `./bootstrap.sh preflight`: mỗi vấn đề một dòng trên stderr (tên biến + cách sửa), thoát khác 0 nếu có.
Kiểm: biến bắt buộc theo profile, tag image cố định, dạng URL, quyền ghi của gateway lên `poc` và `poc/store`, pool khớp
`VM_SLOTS`, `vm.max_map_count`, khoá tunnel. Không sửa gì, không gọi docker để đổi trạng thái.

**5. Bật và kiểm.**

```sh
docker compose up -d                    # đọc COMPOSE_PROFILES trong .env
./check.sh all                          # env -> config -> models -> health, dừng ở giai đoạn trượt đầu tiên
```

`check.sh models` probe thật LLM/embedding/rerank bằng đúng request gateway dùng; `check.sh health` thêm một truy vấn RAG
thật (CVE-2021-44228) và, với profile translation, kiểm tunnel từ trong netns. `/health` đơn lẻ không chứng minh model
hoạt động. Cuối cùng chạy nghiệm thu: `acceptance/run-api.sh http://127.0.0.1:<GATEWAY_HOST_PORT>` (API), và bộ
Playwright (`PLAYWRIGHT_BASE_URL=<url frontend>`) khi frontend sản phẩm đã trỏ sang gateway mới; ca, hạn, lệnh chạy và cách
đọc kết quả: `ACCEPTANCE.md`.

## Profile và cổng

Đặt `COMPOSE_PROFILES` trong `.env` (phân tách dấu phẩy). Core luôn chạy.

| Profile | Service | Cổng ra host |
|---|---|---|
| core | `gateway`, `qdrant`, `neo4j`, `redis`, `es` | gateway `GATEWAY_BIND:GATEWAY_HOST_PORT` (mặc định `0.0.0.0:8131`). Các kho dữ liệu không mở cổng |
| `exploit` | `vm-a`, `vm-b` (mỗi cái `VM_SLOTS`=12 engine, pool 24) | không (gateway gọi qua `secai-net`; tham gia thêm `POC_LABS_NETWORK`) |
| `translation` | `translation-gateway`, `translation-worker`, `translation-converter`, `specialist-tunnel` | không (gateway chuyển tiếp qua `TRANSLATION_UPSTREAM_URL`) |
| `report` | `report-backend` | `REPORT_BIND:REPORT_HOST_PORT` (mặc định `127.0.0.1:18183`) |
| `review` | `review-frontend` | `REVIEW_BIND:REVIEW_HOST_PORT` (mặc định `127.0.0.1:5174`) |

`bootstrap.sh init` đặt `REVIEW_ENABLED` và `TRANSLATION_UPSTREAM_URL` theo profile bật.

## Gói dữ liệu

```sh
stack/pack.sh data [--mcp-dir D] [--neo4j-dir D | --neo4j-dump F] <src_root> <out_dir>
```

Tạo `exploitdb`, `poc` (kèm `catalog.db` và `store`), `poc-sources`, `exploitdb-mcp-server` (tar.zst), snapshot Qdrant,
dump Neo4j, `MANIFEST.md`, `SHA256SUMS`. Không dừng hay đụng Neo4j/Qdrant đang chạy (Qdrant tạo snapshot rồi xoá ngay
trên máy nguồn). `restore-data.sh` kiểm `SHA256SUMS` trước khi đụng `DATA_ROOT`, bỏ qua thư mục đã có, chỉ nạp Neo4j khi
store chưa có, và không bao giờ xoá dữ liệu.

Quyền: gateway chạy uid 10001 (gid 999, thêm nhóm chung `VM_SHARED_GID`=10100). `DATA_ROOT/poc` và `DATA_ROOT/poc/store`
phải **gateway ghi được** (WAL của `catalog.db`, kho PoC); preflight báo lỗi nếu không, và in lệnh sửa:
`sudo chown -R 10001:10100 $DATA_ROOT/poc && sudo chmod -R g+w $DATA_ROOT/poc`. `exploitdb`, `poc-sources`,
`exploitdb-mcp-server` mount `:ro`. Thư mục thiếu mà Docker tự tạo sẽ thuộc root và gateway không ghi được, nên tạo trước.

### Thử gói thật trên VM thuê (`packtest`)

Máy nguồn ít đĩa nên pack và restore đều chạy **trên VM**; máy nguồn chỉ rsync nguồn (chỉ đọc) và mở tunnel ngược tới Qdrant
qua `qdrant-guard-proxy.py` (proxy chặn ghi), chỉ trong pha pack.

```sh
EVAL_MODE=packtest EVAL_DISK_GB=150 stack/eval-vast.sh up   # không cần EVAL_ENV_FILE/EVAL_DATA_PKG; không build image
stack/eval-vast.sh packtest      # kiểm cục bộ -> đo băng thông -> rsync nguồn -> tunnel -> pack + restore + kiểm trên VM
stack/eval-vast.sh collect       # kéo results/ (có packtest-summary.json) về local/stack-eval/
stack/eval-vast.sh down
```

`packtest` từ chối (chưa tạo gì) khi `/` cục bộ còn < 45 GB (snapshot ghi lên đĩa máy Qdrant), Qdrant không đọc được, thiếu
nguồn, hoặc còn < 10 phút tới deadline. Sau khi đo băng thông, nó dừng trước khi chuyển dữ liệu (ghi `ALERT.txt`, rồi `down`)
nếu tốc độ < 8 MB/s hoặc (nguồn + snapshot ước tính) / tốc độ + `PACKTEST_PACK_RESTORE_MIN` (45 phút) vượt thời gian còn tới
deadline - 10 phút. Mọi lệnh bị giới hạn tới deadline - 10 phút.

Tunnel VM `127.0.0.1:16333` trỏ vào proxy, không vào Qdrant. Proxy chỉ cho collection đã chọn: đọc (info, danh sách/tải
snapshot, scroll <= 10000 điểm (<= 1000 nếu kèm vector), lấy <= 1000 điểm theo id, count), tạo snapshot **một lần**, và xoá
**đúng** snapshot mà chính proxy thấy được tạo; mọi thứ khác (recover, upload, PUT/PATCH/TRACE/CONNECT, collection khác,
query string, Content-Length lạ hoặc > 1 MiB) trả 403, ghi `ALERT.txt` và làm lần chạy trượt. Tối đa 16 kết nối, client treo
bị cắt sau 120 s. Lệnh tạo vẫn chạy tới hết dù tunnel chết, tên được ghi lại. Proxy tự thoát khi script chết (kể cả
`kill -9`), khi hết thời gian còn lại hoặc khi nhận SIGTERM: chờ yêu cầu đang chạy rồi tự xoá snapshot nó đã ghi tên.
Snapshot mới không do lần chạy tạo chỉ được báo trong `ALERT.txt`, không bị xoá. Xong `pack.sh` là đóng tunnel, chờ proxy hết yêu cầu
đang chạy (`PACKTEST_DRAIN_SECS`, 300, giới hạn 30..400), rồi xoá đúng các tên đã ghi (snapshot có sẵn và snapshot người khác tạo không bao
giờ bị đụng). Khoá Qdrant (`QDRANT_API_KEY` hoặc `PACKTEST_QDRANT_KEY_FILE` 0600) chỉ đi vào stdin của proxy; không lên
argv, không lên VM. Số liệu tham chiếu (số điểm, cấu hình, 20 điểm ngẫu nhiên trên toàn bộ id kèm vector/payload) lấy trước
khi đóng tunnel; số điểm khôi phục được so với số điểm proxy đếm ngay trước/sau lúc tạo snapshot, không so với số lúc bắt đầu.
`catalog.db` được chép nhất quán bằng sqlite backup trước khi rsync. `/` cục bộ không về trong 1 GB chỉ là cảnh báo.

Biến khác: `PACKTEST_SRC_ROOT`, `PACKTEST_MCP_DIR`, `PACKTEST_QDRANT_URL` (chỉ `http://127.0.0.1:<cổng>`),
`PACKTEST_QDRANT_COLLECTION`, `PACKTEST_NEO4J_NODES` (không đặt thì Neo4j là `not_verified`: dump không cho đếm node mà không
nạp), `PACKTEST_VM_MIN_FREE_GB` (60). Nguồn trên VM bị xoá sau khi đo để restore vừa đĩa; `restore-data.sh` chép snapshot
thêm một bản nên dùng đĩa ~150 GB. Giới hạn đã biết: rsync `--no-links` bỏ symlink trong nguồn, nên symlink không nằm trong
phần được chứng minh.

## Dịch tệp/ảnh: tunnel tới model chuyên dụng và OCR

Gateway dịch chỉ chấp nhận Hy-MT2/OCR ở `http://127.0.0.1:<cổng>` hoặc `https://<IP private>` (chặn SSRF, không đổi).
Trên mạng bridge `127.0.0.1` là chính container, nên sidecar `specialist-tunnel` chung netns với `translation-gateway`
và `translation-worker`, mở `127.0.0.1:18868` (Hy-MT2) và `127.0.0.1:18083` (OCR). `TUNNEL_MODE`:

| Chế độ | Dùng khi | Ghi chú |
|---|---|---|
| `ssh` (mặc định) | máy thật | `ssh -L` tới loopback máy model. Cần `TUNNEL_SSH_HOST/PORT/USER`, `secrets/tunnel/key` (0600, chủ = `TUNNEL_UID`), `secrets/tunnel/known_hosts` đã đối chiếu fingerprint; `StrictHostKeyChecking=yes` |
| `off` | model có `https://<IP private>` | không tunnel; URL trong `.env` trỏ thẳng |
| `direct` | **chỉ test** | socat tới stub trong `secai-net`; phải đặt `TUNNEL_DIRECT_ALLOW_TEST=1` và khoá giả, preflight từ chối khoá thật |

Kết quả `direct`/stub chỉ chứng minh đường ống, không chứng minh chất lượng dịch hay OCR. Xem `ACCEPTANCE.md`.

## Cập nhật phiên bản

1. Dựng/kéo image mới, đặt tag mới vào `.env` (`GATEWAY_IMAGE`, `VM_IMAGE`, ...).
2. `./bootstrap.sh preflight && ./check.sh config`.
3. `docker compose up -d` (chỉ service đổi image bị tạo lại). Volume và `DATA_ROOT` giữ nguyên.
4. `./check.sh all`.

Không chạy `down -v`: volume `report-data`, `gw-data`, `translation-data`, `review-data` giữ DB báo cáo/job/review.

## Rollback

Đổi tag trong `.env` về bản trước (image cũ vẫn còn trên máy nếu chưa `docker rmi`), rồi `docker compose up -d` và
`./check.sh all`. Dữ liệu không đụng tới. Giữ image phiên bản liền trước cho đến khi bản mới ổn định.

## Xử lý sự cố (từ smoke thật 2026-11-23)

| Triệu chứng | Nguyên nhân và cách sửa |
|---|---|
| `all predefined address pools have been fully subnetted` khi `up` | Host hết dải địa chỉ Docker. Đặt `default-address-pools` trong `/etc/docker/daemon.json` hoặc dùng tệp override (`docker compose -f compose.yml -f <override>`) cho `networks.secai-net.ipam.config[].subnet` (và `poc-labs-internal` nếu compose tự tạo); smoke đã dùng `10.231.0.0/24` |
| Elasticsearch thoát ngay | `vm.max_map_count < 262144`; `sudo sysctl -w vm.max_map_count=262144` (preflight đã kiểm) |
| `translation-gateway`, worker, `report-backend` restart liên tục | `REVIEW_ENABLED=true` trong `.env` chung nhưng service đó không mount `review-credentials.json`. Compose đã ép `REVIEW_ENABLED: "false"` cho chúng; chỉ gateway bật theo `.env` |
| Mọi yêu cầu exploit-v2 trả HTTP 403 | `VERIFY_ALLOWED_TARGETS` trống (và không `VERIFY_ALLOW_ALL=true`). Đặt dải/host lab được phép (dạng host hoặc CIDR, phân cách dấu phẩy) |
| `/health` xanh nhưng RAG chỉ BM25 hoặc lỗi 404 model | Tên model embedding/rerank lệch serving; `./check.sh models` chỉ ra đúng biến |
| Ghi báo cáo/review `Permission denied` | Image gateway dựng từ bản trước thay đổi Dockerfile; dựng lại bằng `pack.sh image` từ nhánh này |
| `config` báo lỗi `:?` | Thiếu `DATA_ROOT`, `GATEWAY_IMAGE` hoặc `NEO4J_PASSWORD` trong `.env` |
| HOME slot VM có mode `0770` thay vì `2770` | **Bình thường**: container không có `CAP_FSETID` nên setgid bị bỏ; supervisor tự chuyển nhóm cây `.ias/engagements` sang `VM_SHARED_GID` trong khoảng 0,5 s nên gateway vẫn dọn được |
| `translation-worker` thoát sau khi `docker restart translation-gateway` | Chủ ý: worker và tunnel chung netns, thoát để compose tạo lại trong netns mới |

## Chưa kiểm / giới hạn đã biết

Những gì dưới đây **chưa kiểm**; không suy ra là đạt.

- **RAG parity 100 câu**: cần Qdrant đầy đủ (~22 GB). Smoke chỉ có tập con 360 điểm nên so sánh với prod không có nghĩa;
  mới kiểm đường nhanh CVE chính xác và truy vấn CVE-2021-44228.
- **Hy-MT2 và OCR thật**: UI-TR-2/TR-3 chỉ đạt với **stub** (`direct` và `ssh` tới sshd thử). Pipeline được chứng minh,
  chất lượng dịch/OCR thật thì chưa. Dịch vụ chuyên dụng trên prod lúc đo đang từ chối kết nối.
- **ssh thật tới máy model**: chưa kiểm (mới thử với sshd dựng tạm).
- **TUNNEL_MODE=off**: preflight và `check.sh` không thăm dò URL dịch chuyên dụng/OCR (chỉ kiểm dạng URL), nên xanh dù đầu xa
  chết; và `ocr_client.py` chỉ nhận OCR `http://` (IP private), không nhận `https://`.
- **N=24 exploit-v2 trên mạng bridge**: đã đo 2026-11-24 (`docs/archive/test-results/stack-package-n24-20261124.md`): stack 72/72 exploit ok,
  PoC chạy lại ra bằng chứng 31/72 (ngưỡng 71/72 và 27/72) — đạt. Lưu ý: prod luôn chạy trước stack trong mỗi vòng, LLM dùng chung, vòng prod r2/r3
  phải chạy lại do chat 503 (lỗi phía gateway prod), stack có `VERIFY_ENABLED=false` và Qdrant tập con; vòng stack r3 chạy không có guard
  tự dừng (không có phút nào > 50% lỗi). UI-POC-2: prod chỉ qua 3/71 nên baseline không ổn định.
- **Gói dữ liệu thật (~37 GB nguồn)**: `pack.sh`/`restore-data.sh` mới kiểm trên tập con. `eval-vast.sh packtest` (mục dưới)
  **sẽ** chứng minh trên VM: pack đủ (snapshot Qdrant qua API, dump Neo4j có sẵn), restore vào `DATA_ROOT` trống, số điểm/cấu
  hình/payload Qdrant khớp prod, số tệp và byte từng cây khớp nguồn, SHA256SUMS, quyền, cùng thời gian và đỉnh đĩa. **Chưa chạy**,
  nên chưa tuyên bố đạt. Không chứng minh: dump store Neo4j đang ghi (`--neo4j-dir`), symlink trong nguồn (rsync `--no-links`).
- **Khởi động lại máy chủ**: đã thử khởi động lại Docker daemon trên VM thuê (11 dịch vụ tự lên healthy trong ~100 s, `check.sh health` đạt); reboot cả máy chưa kiểm.
- **Máy 2/4 GPU và máy đích sạch hoàn toàn**: đã dựng trên một VM Vast mới (image build tại chỗ từ `git archive`, nạp gói dữ liệu nhỏ,
  `check.sh all` + 4/9 ca đạt, 5 ca bỏ qua có lý do; xem `docs/archive/test-results/stack-package-vast-eval-20261124.md`). Chưa kiểm: máy
  2/4 GPU, Docker Hub bị chặn / không internet / registry nội bộ.
- **Bộ kiểm trên UI sản phẩm**: chỉ làm được khi frontend IAS trỏ sang gateway mới; mới chạy qua API.
- Giới hạn khác đã ghi ở `docs/archive/test-results/stack-package-smoke-20261123.md`: tệp bàn giao lab ghi ở `/tmp/ias`
  trong container gateway chưa được kiểm quyền đọc; bản dựng VM còn `gyp ERR` của `tree-sitter-powershell` (đã biết, không
  chặn).
