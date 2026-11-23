# Gói triển khai `stack/` — thiết kế

Ngày: 2026-11-23 · Trạng thái: chờ duyệt · Nhánh: `feat/stack-package`

## 1. Ý định

Gom mọi phần SecAI cần để chạy trên một máy mới vào **một thư mục `stack/` trong repo gateway**, có cấu trúc rõ, dễ nhìn, dễ chỉnh. Hiện chúng rải ở `secai/secai-docker/` (ngoài git gateway), `~/secai-releases/*` (compose sinh tay, image không tag), `~/secai-workspaces/*` (OCR/venv), `/tmp/*` và các `deploy/` đã cũ trong repo. Không thể mang sang máy khác chỉ bằng `git clone`.

Máy đích **chỉ chạy container Docker**. LLM, embedding, rerank, OCR, specialist dịch là **endpoint ngoài**, đặt trong `.env` rồi bật.

## 2. Phạm vi

**Trong gói**

| Nhóm (compose profile) | Container | Ghi chú |
|---|---|---|
| core (luôn bật) | `gateway`, `qdrant`, `neo4j`, `redis`, `es` | Giữ image/tag: `qdrant/qdrant:v1.10.1`, `neo4j:5.20-community`, `redis:7-alpine`, ES `8.15.3` |
| `exploit` | `vm-a`, `vm-b` (mỗi cái 12 engine) | Thay 25 container `pentestcode*` |
| `translation` | `translation-gateway`, `translation-worker`, `translation-converter` | Chỉ API; bridge `secai-net` |
| `report` | `report-backend` | DB báo cáo trong named volume |
| `review` | `review-frontend` (từ image gateway, `/app/review-ui`) | Cần `credentials.json` do bootstrap sinh |

**Ngoài gói (chủ dự án đã loại)**: showcase/nghiệm thu (HO-SO-GOC, KPI, secai-88, sync_cve, hardware-check), MLOps/clearml/mlops-trigger, reward scorer, TensorBoard, Devspace FE và `translation-frontend` (thực chất là bản build Devspace), lab CVE, PoC-Verify executor, dịch vụ systemd trên host (embedding/reranker cục bộ, OCR, tunnel, cloudflared), `secai-review-canary`.

## 3. Mục tiêu, phi mục tiêu

**Mục tiêu**: (1) một nguồn sự thật cho compose của máy mới; (2) dựng được từ `git clone` + gói dữ liệu + `.env`, không cần đường dẫn `/home/ict/...`; (3) image tái lập (tag SHA); (4) một lệnh kiểm trước khi bật.

**Phi mục tiêu**: không cutover prod hiện tại; không thay `release-prod.sh`/`release.py`; không dựng serving model; không gồm lab/executor; không sửa chất lượng PoC (soft-pass, chọn file PoC → PR riêng).

**Phản mục tiêu (coi là hỏng dù chạy được)**: stack khởi động nhưng RAG rơi về BM25-only vì sai tên model embedding/rerank (xem CLAUDE.md "Không thay embedding/rerank bằng model stock"); giảm chất lượng exploit-v2 so với bản đo trong PR #57 (71/72 ok; PoC chạy lại ra bằng chứng 27/72).

## 4. Các hướng đã cân nhắc

- **Vị trí**: (a) `stack/` trong repo gateway ← chọn; (b) repo mới `secai-stack` (thêm một repo phải đồng bộ); (c) dọn tại chỗ `secai-docker/` (ngoài git, khó theo dõi).
- **Pool exploit-v2**: (a) 2 container × 12 engine (image VM của PR #57) ← chủ dự án chọn; (b) giữ 24 container; (c) cả hai bằng profile. (a) ít container, supervisor tự restart slot, không `docker.sock`.
- **Mạng VM**: (a) bridge `secai-net` + `poc-labs-internal` ← chọn; (b) host + `firewall.sh` như đã đo. (a) bỏ yêu cầu sudo/firewall trên máy đích nhưng **chưa được đo** → phải chạy lại smoke exploit.
- **Phân phối image**: gateway lên **GHCR private** theo tag SHA; image VM **build lại trên máy đích** (cần internet: Docker Hub cho `oven/bun`, GitHub cho nuclei/httpx, apt); DB pull từ registry gốc. Không dùng Docker Hub từ máy hiện tại (bị chặn); không GHCR public (lộ mã nguồn, `.ias`, tool).
- **Dữ liệu**: gói riêng, có `MANIFEST` + sha256, khôi phục từ snapshot/dump, không copy `_state` đang chạy.

## 5. Thiết kế

### 5.1 Bố cục `stack/`

```
stack/
  README.md              # 1 trang: yêu cầu, 5 bước dựng, rollback
  compose.yml            # core + profile exploit/translation/report/review (một file)
  .env.example           # chia nhóm: ENDPOINTS, SECRETS, PATHS, POOL, TRANSLATION
  bootstrap.sh           # sinh secret nội bộ, tạo credentials.json, kiểm tiên quyết
  pack.sh                # build+push gateway (GHCR), xuất gói dữ liệu + manifest
  restore-data.sh        # nạp qdrant snapshot, neo4j dump, poc/exploitdb vào volume/đường dẫn
  check.sh               # compose config + kiểm model name + smoke có hạn (xem 5.6)
  images/
    gateway/             # tái dùng Dockerfile gốc của repo (không nhân bản)
    pentestcode-vm/      # Dockerfile gộp (base + tools.txt + supervisor), build.sh
    converter/           # từ deploy/translate-v2/Dockerfile.converter
```

Cũ cần dọn khi hoàn tất: `deploy/docker-compose.yml`, `deploy/up.sh` (đánh dấu deprecated, trỏ sang `stack/`).

### 5.2 Core

- Gateway chạy `uvicorn secai_gateway.unified_app:app` cổng 8131; `QDRANT_URL/NEO4J_URI/REDIS_URL/ELASTICSEARCH_URL` trỏ tên service; `GATEWAY_DB_PATH=/app/data/gateway.db` trong named volume `gw-data`.
- Mount chỉ dữ liệu cần thiết, qua biến: `${DATA_ROOT}/exploitdb`, `poc`, `poc/store`, `poc-sources` → `/srv/...`. Bỏ toàn bộ mount showcase; tắt tính năng phụ thuộc chúng bằng `.env` (reward, TensorBoard, retraining proxy).
- Bí mật chỉ trong `.env` (0600), không vào git/image: `QWEN_API_KEY`, `EMBEDDING_API_KEY`, `RERANKER_API_KEY`, `ORCAROUTER_API_KEY` (nhập tay); `NEO4J_PASSWORD`, `ADMIN_API_KEY`, `LOGS_UI_*`, `REPORT_API_KEY` (bootstrap sinh ngẫu nhiên).

### 5.3 exploit-v2 (2×12)

- Nền: **PR #57** (`feat/ev2-vm-containers`, 3 commit, lệch main 9 commit) rebase lên main trong cùng công việc.
- `vm-a`, `vm-b`: image `secai-pentestcode-vm:<sha8>-<ngày>`, `VM_SLOTS=12`, cổng engine 5101–5112 / 5201–5212 trong container (bridge, **không publish**), `cap_drop: ALL` + tập cap đã dùng, `no-new-privileges`, `mem_limit`/`pids_limit` theo biến, **không `docker.sock`**, `IAS_SANDBOX_MODE=inline`.
- Gateway gọi pool qua `IAS_SERVER_POOL=http://vm-a:5101|/ias-pool-home-a/slot1,...`; thư mục HOME slot là **named volume** `pool-home-a/b` mount vào cả VM và gateway (thay bind `~/ev2-vm-eval`). Mạng: `secai-net` + `poc-labs-internal` (tạo sẵn, `external` tuỳ chọn).
- Hoàn thiện 2 mục chặn của PR liên quan đóng gói: (1) **build tái lập**: Dockerfile gộp base (`oven/bun:1.3.14` + công cụ + nuclei/httpx ghim) và lớp VM, ghim pip transitive, `build.sh`; (2) **mật khẩu engine** `OPENCODE_SERVER_PASSWORD` + gateway gửi auth. Hai mục còn lại (soft-pass/chọn file PoC; kế hoạch cutover prod) **không** thuộc công việc này.
- Đường dẫn tuyệt đối trong Dockerfile/compose (`/home/ict/AIEngines/...`) thay bằng đường dẫn nội bộ cố định (`/opt/pentestcode`, `/srv/...`); mọi tham chiếu trong `.ias/ias.jsonc` và `config.py` đi theo biến.

### 5.4 Dịch, báo cáo, review

- Gateway/worker dịch dùng **cùng image gateway** (khác command/env), nối `secai-net`; gateway chính dùng `TRANSLATION_UPSTREAM_URL=http://translation-gateway:18185`. Bỏ `network_mode: host` và `host.docker.internal`.
- URL ngoài qua `.env`: `TRANSLATE_V2_OCR_BASE_URL`, `TRANSLATE_V2_SPECIALIST_BASE_URL`, `MULTILANG_SPECIALIST_BASE_URL`, `TRANSLATE_V2_CONVERTER_URL` (mặc định `http://translation-converter:18091`), tokenizer qua volume.
- Báo cáo: `REPORT_DB_PATH=/srv/reports/reports.db` trong named volume `report-data`; giữ DB khi rollback.
- Review UI: image gateway, `VITE_GATEWAY_PROXY=http://gateway:8131`, credentials mount từ file bootstrap sinh.

### 5.5 Dữ liệu và phân phối

- **Gói dữ liệu** (tách, ~40–50 GB nén ít): `qdrant/*.snapshot` (collection `cve_kb_hybrid`), `neo4j.dump`, `exploitdb/`, `poc/` (catalog.db + store), `poc-sources/` (13 GB, không re-clone khi air-gap), bm25 nếu cần; `MANIFEST.md` + `SHA256SUMS`; `restore-data.sh` idempotent (mở rộng `secai-data/restore.sh`).
- **Gateway**: `pack.sh` build từ `git archive <sha>`, tag `ghcr.io/<owner>/secai-agent-gateway:main-<sha8>-<ngày>`, đẩy private (mỗi lần đẩy cần chủ dự án xác nhận — hành động ra ngoài). Một image phục vụ 4 service.
- Tag theo SHA, không `latest`; compose đọc `GATEWAY_IMAGE` và `VM_IMAGE` từ `.env`.

### 5.6 Kiểm chứng (không đụng prod)

Không chạy `up`/`down` trên project `secai-docker`, `secai-translation-release`; không đặt tên container trùng prod. Kiểm trên **project `secai-stack-smoke`**, cổng LAN riêng, tên `*-smoke-<ngày>`:
1. `docker compose config` cho từng tổ hợp profile; kiểm không còn `/home/ict` và `docker.sock` trong kết quả.
2. `check.sh`: đúng tên model embedding/rerank so với endpoint (đã lường ở "phản mục tiêu"); health từng service; truy vấn RAG thật có hit từ Qdrant (không BM25-only).
3. **Smoke exploit-v2 theo tiêu chí N=24 (chủ dự án chốt)**: chạy lại đúng quy trình trong `docs/archive/test-results/exploit-v2-vm-20261123.md` (N=24 = 24 phiên exploit + 24 luồng chat song song, × 3 lần, nghỉ 60 s, tổng ~30 phút; script `prod_ramp.py`; lab nginx-ui CVE-2026-27944 tại 192.168.96.21 trên `poc-labs-internal`) trên mạng bridge. Đạt khi **không kém** bản VM host-network đã đo: exploit ok ≥ 71/72 và PoC trả về chạy lại ra bằng chứng ≥ 27/72. LLM = `QWEN_*` của prod (Vast, đã áp dụng cho gateway và pool hiện tại). Smoke dùng chung LLM với prod (nút thắt là LLM) nên **chỉ chạy trong khung ~40 phút vắng do chủ dự án chọn**. Smoke dịch + báo cáo dùng dữ liệu synthetic.
4. **Nơi chạy: máy prod này**, project `secai-stack-smoke`: mount **chỉ đọc** `secai-data` dùng chung (exploitdb/poc/poc-sources), qdrant/neo4j trống hoặc tập con nhỏ, nên không nhân đôi 49 GB (đĩa còn 69 GB, 93%). `restore-data.sh` vì vậy chỉ được kiểm với tập con nhỏ, **không** kiểm với gói đầy đủ; ghi rõ trong README.
5. Máy đích thật **chưa kiểm được** ở đây: ghi rõ trong README.

## 6. Tuỳ builder (không cần hỏi lại)

Tên biến phụ, thứ tự khởi động/`depends_on`, healthcheck chi tiết, cách nén gói dữ liệu, nội dung `README.md`, tên volume cụ thể.

**Các quyết định tôi (tác giả) đặt sau vòng review — chủ dự án kiểm khi đọc**

1. **ES và Redis** khởi động trống, không nằm trong gói dữ liệu (ES = log hoạt động, Redis = lịch sử hội thoại tạm). Gói dữ liệu chỉ chứa thứ gateway cần để trả lời.
2. **Rebase PR #57**: giữ 3 commit của nhánh, tái dùng `Dockerfile`, `supervisor.py`, `install-tools.sh`, `tools.txt`; **thay hẳn** `compose.yml`, `vm.sh`, `firewall.sh` bằng bản trong `stack/`. Không kéo theo các thay đổi nằm ngoài 3 commit.
3. **Mật khẩu engine**: một mật khẩu dùng chung cho mọi engine (`OPENCODE_SERVER_PASSWORD`), bootstrap sinh vào `.env`, gateway gửi trong header. Cần sửa nhỏ ở client pool (`ias_pool.py`) — đây là thay đổi mã nguồn gateway ngoài đóng gói, nhưng nằm trong mục chặn của PR #57.
4. **Mã pentestcode nướng vào image VM** tại `/opt/pentestcode`, `bun install --frozen-lockfile` lúc build; không mount nguồn.
5. **Registry**: `ghcr.io/elonma-china/secai-agent-gateway:main-<sha8>-<ngày>` (suy ra từ remote `origin`); mỗi lần `docker push` là bước thủ công có xác nhận, `pack.sh` mặc định chỉ build.
6. **Thứ tự dựng**: `bootstrap.sh` → `restore-data.sh` → `docker compose up` (credentials review phải có trước khi gateway khởi động).

**Đính chính khi lập plan (2026-11-23, đã kiểm trên máy)**

- Image gateway `main-*` **không chứa** `/app/review-ui`; mã frontend review không nằm trong git (chỉ có trong image `review-mvp-20261004`). → vendor bản tĩnh từ image đó vào `stack/images/review-ui/`, build image nhỏ riêng.
- Converter đang chạy dùng `/opt/server.py` (62 dòng, HTTP :18091) **không có trong repo** (repo chỉ có `convert.sh`). → lấy `server.py` từ container vào `stack/images/converter/`.
- `pentestcode/.ias/ias.jsonc` trỏ `exploitdb-mcp-server` (thư mục anh em ngoài repo, 113 MB). → đưa vào gói dữ liệu, mount `${DATA_ROOT}/exploitdb-mcp-server`.
- Báo cáo: prod chạy container riêng (`secai_gateway.app:app`, `REPORT_FEATURE_ENABLED=true`) và Devspace làm bộ chia đường. Gói không có Devspace nên `report-backend` publish cổng riêng `REPORT_HOST_PORT` (mặc định 18183).

## 7. Rủi ro đã biết

- Mạng bridge cho VM **chưa đo**; nếu smoke tụt chất lượng hoặc agent không tới được lab → quay về host+firewall (tài liệu hoá), không ép bridge.
- Build image VM cần internet (Docker Hub, GitHub, apt) trên máy đích; ghim version làm build **fail khi mirror đổi** (thiết kế có chủ ý).
- `oven/bun`, `python:3.12-slim` từ Docker Hub: nếu máy đích bị chặn như máy này thì phải dùng mirror; ngoài phạm vi, ghi vào README.
- `translation-converter` hiện chỉ có image `:1` chạy tay; cần xác nhận Dockerfile trong repo tái tạo đúng hành vi (builder kiểm).
- Dung lượng GHCR free có hạn; chỉ gateway (~1,4 GB) được đẩy.
- **Prod đang lệch cấu hình (phát hiện khi rà soát, không sửa trong công việc này)**: `translation-gateway`/`translation-worker` vẫn mang `EMBEDDING_API_URL`/`RERANKER_API_URL` của instance cũ `217.91.60.211` và `QWEN_BASE_URL=http://172.17.0.1:18180/v1` (không có gì lắng nghe), trong khi `.env` và gateway/pool đã trỏ `148.113.245.56`. Nguyên nhân: nhóm dịch đọc biến từ `translation-main.json` do `release.py` sinh, không từ `.env`. Thiết kế `stack/` dùng **một `.env` duy nhất** cho mọi service để loại lỗi này.
