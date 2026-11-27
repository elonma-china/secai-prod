# Smoke gói `stack/` trên máy này (2026-11-23)

Nhánh `feat/stack-package`, image dựng từ HEAD `8a3fadb8` (trước các bản sửa bên dưới; compose/bootstrap đã sửa được áp lại trên cùng image). Project compose `secai-stack-smoke`, gateway `192.168.1.222:21131`, report `:21183`, review UI `:21174`. Không đụng container prod; đã dỡ sạch ở cuối.

## Kết quả

| Mục | Kết quả | Bằng chứng / ghi chú |
|---|---|---|
| Build gateway (`pack.sh image`) | đạt | 47,6 s (layer có cache), 1,42 GB; tag local, không push |
| Build VM (`images/pentestcode-vm/build.sh`) | đạt | 396,9 s, 3,85 GB; vẫn có `gyp ERR` tree-sitter-powershell (đã biết, không chặn) |
| Build converter / review-ui | đạt | 0,24 s / 0,31 s (toàn cache), 814 MB / 178 MB |
| `bootstrap.sh init` + `preflight` | đạt sau xử lý | quyền `poc`, `poc/store`: chown 10001:10100 + g+w (chạy bằng container root dùng một lần thay `sudo`, chỉ trên bản copy); `vm.max_map_count` = 1048576 sẵn |
| `docker compose up` đủ 4 profile (2 VM × 12 slot) | đạt | 12 service running/healthy; RAM stack ≈ 14 GB lúc nghỉ (VM ~4,5–4,8 GB mỗi cái, ES 3 GB) |
| `check.sh all` | đạt | env/config/models/health xanh, gồm truy vấn RAG thật CVE-2021-44228. Một lần trượt `models` do rerank Vast quá 15 s (tạm thời, chạy lại 3 lần đều đạt) |
| UI-CHAT-1 / UI-CHAT-2 | đạt / đạt | 12 s / 7 s |
| UI-TR-1 | đạt | 1 s |
| UI-TR-2 | trượt (đã biết) | job `failed` `TRANSLATION_UNAVAILABLE`: dịch vụ chuyên dụng Hy-MT2 đang chết, prod cũng trượt. Xem phát hiện 1 |
| UI-TR-3 | trượt (đã biết) | job `failed` `OCR_UNAVAILABLE`: OCR đang chết, prod cũng trượt |
| UI-RPT-1 | đạt | 6 s |
| UI-POC-3 | đạt (sau sửa) | lần đầu HTTP 403 (thiếu allowlist, đã sửa), sau đó đạt 4 s |
| UI-POC-1 (PoC thật, CVE-2022-22963 → 192.168.96.7:8080) | đạt | 845 s, `final ok=true`, stage 3.3, có code + hướng dẫn chạy. Bằng chứng: listener trong vm-a nhận `GET /rce-purejava` và `POST /id` **từ 192.168.96.7** (callback RCE từ lab qua mạng `poc-labs-internal`) |
| UI-POC-2 | chưa kiểm | quy tắc mỗi ca PoC một lần; baseline prod chưa hợp lệ, chờ N=24 xen kẽ |
| RAG core eval 100 câu | chưa kiểm | Qdrant smoke chỉ là tập con 360 điểm; so sánh chỉ có nghĩa trên Qdrant đầy đủ |
| N=24 exploit-v2 | chưa kiểm | ngoài phạm vi 8b, chờ chủ dự án chọn khung giờ |

## Kiểm sống

- **a. HOME slot VM:** `drwxrwx--- 200NN 10100`, tức **0770, setgid bị bỏ** (không có `CAP_FSETID`; CapEff `0xeb`). Sweeper vẫn bù: cây `.ias/engagements` 0700/0600 do user slot tạo được chuyển sang nhóm 10100, 0770/0660 trong khoảng **0,5 s**. Tệp do engine thật tạo trong phiên PoC là 0660/0770 (umask 007 có hiệu lực).
- **b. Gateway xoá được thư mục của engine:** gateway `uid=10001 gid=999 groups=999,10100`; `rm -rf` cây 0700 của user slot12 (vm-b) → `WIPE_OK`; gọi đúng hàm `_reset_engagement_store("/ias-pool-home-b/slot12")` → `engagements` rỗng, kể cả khi gọi ngay sau lúc tạo.
- **c. Ghi dữ liệu:** report-backend `uid=10001` `touch /srv/reports/x` → OK (`/srv/reports` 10001:999 755). Gateway ghi được `/app/review-data`, `/srv/poc-store`, `/srv/poc`, `/app/data`.
- **d. APOC offline:** log neo4j `Installing Plugin 'apoc' from /var/lib/neo4j/labs/apoc-*-core.jar`; `RETURN apoc.version()` → `"5.20.0"`.
- **e. Image thật:** `.env` có tag `…:8a3fadb8-smoke-20261123` cho gateway/VM/converter/review-ui; preflight OK, `docker compose ps` chạy đúng các image đó.

## Lỗi của `stack/` đã sửa

| Commit | Lỗi |
|---|---|
| `b364d4a2` | `.env` chung có `REVIEW_ENABLED=true` (init đặt) nên translation-gateway, worker và report-backend restart liên tục (`load_credentials` ValueError vì không mount `review-credentials.json`). Giờ đặt `REVIEW_ENABLED: "false"` cho các service đó |
| `42aca8e5` | translation-worker kế thừa HEALTHCHECK của image (`127.0.0.1:8131`) nên luôn unhealthy trên bridge (prod xanh giả vì host network trúng gateway prod). Tắt healthcheck cho worker |
| `2a931900` | Preflight nhận URL specialist `http://<IP không phải loopback>`, nhưng gateway chỉ nhận http loopback hoặc https IP private và chết lúc khởi động. Preflight giờ kiểm dạng URL |
| `a37e3087` | `.env.example` không có `VERIFY_ALLOWED_TARGETS`, nên mọi request exploit-v2 bị 403. Đã thêm biến; preflight bắt buộc khi bật exploit (trừ khi `VERIFY_ALLOW_ALL=true`) |

Mỗi commit có test (`tests/test_stack_compose.py`, `tests/test_stack_scripts.py`), chạy qua `scripts/dev.sh test`.

## Phát hiện cần quyết định (chưa sửa)

1. **Dịch tệp/ảnh trên bridge:** `translation/specialist.py` và `multilang/provider.py` chỉ nhận dịch vụ chuyên dụng qua `http://127.0.0.1:<cổng>` hoặc `https://<IP private>`. Trong stack bridge, 127.0.0.1 là chính container, nên dù Hy-MT2 có sống thì stack cũng không gọi được nếu không thêm tunnel/sidecar dùng chung network namespace với translation-gateway/worker, hoặc dùng https. Đây là điều kiện để UI-TR-2 đạt (điều kiện hoàn tất gói). OCR (`ocr_client.py`) nhận http private nên không bị chặn. Smoke đặt `http://127.0.0.1:18868` giống prod để service khởi động. **Đã xử lý (Task 8d, cùng ngày):** sidecar `specialist-tunnel` chung netns với translation-gateway/worker; UI-TR-2/TR-3 đạt qua sidecar với stub (`TUNNEL_MODE=direct` và `ssh`), với model thật vẫn chưa kiểm. Xem `stack/ACCEPTANCE.md`.
2. **Hết dải địa chỉ Docker trên máy này:** `all predefined address pools have been fully subnetted`. Smoke dùng tệp override riêng, đặt `secai-net` = `10.231.0.0/24`, không nằm trong repo. Máy đích sạch sẽ không gặp; nếu cần, README nên ghi cách đặt subnet.
3. `test_config_validates_without_dotenv` cố ý trượt khi có `stack/.env`. Khi smoke đang chạy phải chuyển `.env` ra chỗ khác mới chạy được bộ test.

## Dữ liệu và dọn dẹp

- `DATA_ROOT=~/secai-stack-smoke-data`: `exploitdb`, `poc-sources`, `exploitdb-mcp-server` là symlink (mount `:ro`); `poc` là bản copy 1,7 GB, `catalog.db` lấy qua sqlite backup API. Qdrant là tập con 360 điểm scroll chỉ-đọc từ Qdrant prod: CVE của core eval, CVE-2021-44228, CVE-2022-22963, CVE-2026-27944, cộng 292 điểm theo products log4j/spring_cloud_function/nginx_ui/struts/tomcat. Schema, vector size và payload index giống prod.
- Secret lấy từ env của container prod (chỉ đọc), ghi vào `stack/.env` 0600 (gitignored), không in ra. Đã xoá sau smoke.
- Teardown: `down -v`, gỡ 4 tag smoke, xoá `DATA_ROOT`, `stack/.env`, `stack/secrets`. `docker ps -a | grep smoke-20261123` rỗng. Prod trước/sau: 95 container, 66 healthy, 0 unhealthy, cùng tên.
- Kết quả thô của ca: `local/stack-acceptance/20261123-stack-api/` (không commit).
