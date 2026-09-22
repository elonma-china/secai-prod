# Trigger API — kích hoạt pipeline MLOps theo yêu cầu

API nhỏ, riêng, để gọi `run_pipeline.sh` bất kỳ lúc nào ngoài lịch cố định
(`setup/install_schedule.sh`) — ví dụ khi vừa có một đợt CVE lớn cần kiểm tra ngay.

**Vì sao tách khỏi gateway public (`secai-agent-gateway`):** endpoint `/trigger` có thể châm
ngòi thuê GPU trên vast.ai và push model lên Hugging Face — hành động tốn tiền thật và ảnh
hưởng tới hệ thống production. Đặt chung tiến trình với API khách hàng dùng hàng ngày (dùng
`ADMIN_API_KEY` chung) sẽ mở rộng bề mặt tấn công của một endpoint đắt đỏ. `trigger_api/` chạy
tiến trình riêng, cổng riêng, khoá riêng (`MLOPS_TRIGGER_API_KEY`).

Service này chạy TRÊN MÁY ĐIỀU PHỐI (host), KHÔNG trong container gateway — pipeline cần
venv/GPU/vast CLI/HF token (`~/.config`) của host; gateway trong Docker không chạy pipeline được.

## Chạy — dựng thành service (khuyến nghị)

Khoá + secret để ở `~/.config/secai-mlops/mlops.env` (NGOÀI cây dossier — xem sự cố lộ secret
2026-09-18 ở `mlops/README.md`), cần có `MLOPS_TRIGGER_API_KEY` (sinh:
`python3 -c "import secrets;print('mlt_'+secrets.token_urlsafe(24))"`). Rồi:

```bash
bash setup/install_trigger_service.sh --dry-run   # xem trước unit
bash setup/install_trigger_service.sh             # systemd --user, tự chạy lại khi reboot/crash
# kiểm tra: systemctl --user status secai-mlops-trigger.service ; curl -s localhost:8877/health
```

Chạy tay (không service): `bash trigger_api/run.sh` (dùng venv `mlops/.venv-eval`, đã có
fastapi+uvicorn; tự nạp `~/.config/secai-mlops/mlops.env`).

Mặc định bind `127.0.0.1` (không mở ra ngoài). Trang nghiệm thu gọi tới đây qua reverse-proxy
same-origin của gateway (`/showcase-api/retraining/*`, xem dưới) — KHÔNG cần mở cổng 8877 ra
ngoài. Nếu cần gọi trực tiếp từ máy khác, đặt sau reverse proxy có TLS + IP allowlist.

## Expose ra internet — BẮT BUỘC bật HTTP Basic Auth trước

Endpoint này có thể **Execute/Deploy thật** (thuê GPU + ghi đè production). Nếu expose ra internet
(vd Cloudflare Quick Tunnel — vốn KHÔNG có access-control riêng), **PHẢI** bật Basic Auth (lớp
ngoài) trước, cạnh X-API-Key (lớp trong, cho mutation) — defense-in-depth:

```bash
# thêm vào ~/.config/secai-mlops/mlops.env rồi restart service:
MLOPS_BASIC_AUTH_USER=<user>
MLOPS_BASIC_AUTH_PASS=<mật khẩu mạnh>
# systemctl --user restart secai-mlops-trigger.service
```

Để trống 2 biến này = TẮT (chỉ localhost). Khi bật, MỌI request cần Basic Auth (401 +
`WWW-Authenticate` nếu thiếu; OPTIONS/CORS-preflight được cho qua). Muốn tab trang nghiệm thu
(qua gateway proxy same-origin) vẫn chạy khi Basic Auth bật: khai cùng `user:pass` ở gateway
setting `showcase_retraining_basic_auth` (env `SHOWCASE_RETRAINING_BASIC_AUTH` trong
`secai-docker/.env`) + rebuild gateway — proxy sẽ tự thêm `Authorization: Basic`.

## Gọi

```bash
# Kích hoạt — chỉ dry-run (đo model đang serving, không train/deploy):
curl -s -X POST http://127.0.0.1:8877/trigger \
  -H "X-API-Key: $MLOPS_TRIGGER_API_KEY" -H 'Content-Type: application/json' -d '{}'

# Kích hoạt đầy đủ — cho phép train + push HF candidate + requalify (chưa đụng production):
curl -s -X POST http://127.0.0.1:8877/trigger \
  -H "X-API-Key: $MLOPS_TRIGGER_API_KEY" -H 'Content-Type: application/json' \
  -d '{"execute": true}'

# Kích hoạt trọn vẹn — cho phép cả promote lên production nếu candidate đạt:
curl -s -X POST http://127.0.0.1:8877/trigger \
  -H "X-API-Key: $MLOPS_TRIGGER_API_KEY" -H 'Content-Type: application/json' \
  -d '{"execute": true, "allow_deploy": true}'

# Smoke = dry-run (giai đoạn 1-3), tương đương {"mode":"dry"}:
curl -s -X POST http://127.0.0.1:8877/smoke -H "X-API-Key: $MLOPS_TRIGGER_API_KEY"

# Dạng mới bằng mode (dry|execute|deploy) — tương đương các lệnh trên:
curl -s -X POST http://127.0.0.1:8877/trigger \
  -H "X-API-Key: $MLOPS_TRIGGER_API_KEY" -H 'Content-Type: application/json' -d '{"mode":"deploy"}'
```

Các endpoint ĐỌC (KHÔNG cần key — chỉ đọc, KHÔNG trả giá trị secret):

```bash
curl -s http://127.0.0.1:8877/schedule                       # lịch đối soát + trạng thái systemd timer
curl -s http://127.0.0.1:8877/config                         # tham số retraining (secret chỉ báo ✓/✗, KHÔNG lộ giá trị) + vast key/model
curl -s http://127.0.0.1:8877/vast/status                    # số dư tài khoản vast.ai (credit/balance) — best-effort
curl -s http://127.0.0.1:8877/runs                           # liệt kê các lần chạy (đọc status.json)
curl -s http://127.0.0.1:8877/runs/<run_id>                  # status.json đầy đủ (per-stage)
```

Endpoint CẦN key (log có thể chứa secret nội bộ / config write ghi secret xuống host):

```bash
# đọc log 1 giai đoạn (log có thể chứa API key serving/IP):
curl -s http://127.0.0.1:8877/runs/<run_id>/stages/03/log -H "X-API-Key: $MLOPS_TRIGGER_API_KEY"
# SSE realtime (EventSource không gửi header được → dùng ?api_key=):
curl -sN "http://127.0.0.1:8877/runs/<run_id>/stream?api_key=$MLOPS_TRIGGER_API_KEY"
# ghi tham số retraining vào ~/.config/secai-mlops/mlops.env (+ schedule vào pipeline.yml):
curl -s -X POST http://127.0.0.1:8877/config -H "X-API-Key: $MLOPS_TRIGGER_API_KEY" \
  -H 'Content-Type: application/json' -d '{"HF_TOKEN":"...","JUDGE_MODEL":"...","schedule_day":15}'
```

`/config` là nguồn cho mục **"Cấu hình tham số retraining"** trên tab trang nghiệm thu (HF token,
judge, **vast.ai API key + số dư**, model serving, nhịp đối soát, ngưỡng CVE) — sửa qua UI, áp
dụng cho lần chạy KẾ TIẾP (không cần restart: `_launch` đọc lại mlops.env mỗi lần phóng).
`VAST_API_KEY` ghi vào `~/.config/vastai/vast_api_key` (nơi vastai CLI đọc), KHÔNG vào mlops.env.
MLOPS_TRIGGER_API_KEY / MLOPS_BASIC_AUTH_* KHÔNG sửa qua UI (set 1 lần lúc cài, tránh tự khoá +
cần restart).

`/trigger` (và `/smoke`) chạy `run_pipeline.sh` NỀN (`subprocess.Popen`, không block) và trả
`run_id` ngay. `run_pipeline.sh` ghi `runs/<run_id>/status.json` (tiến độ + exit từng giai đoạn,
qua `_lib/pipeline_status.py`) và `runs/<run_id>/logs/<NN>.log` (log riêng mỗi giai đoạn) — nguồn
dữ liệu cho `/runs*` + `/stream`. `/status/{run_id}` (cũ, CẦN key) vẫn giữ để tương thích.

## Hiển thị trong trang nghiệm thu (:8131/showcase-demo) — tab "Retraining tự động"

Tab `demo/showcase/tabs/retraining-auto.js` của trang nghiệm thu gọi service này để: xem lịch
đối soát, bấm **Chạy smoke** / **Kích hoạt cả vòng đời** (có nhập key + xác nhận), và xem **log
từng giai đoạn realtime** (SSE). Gateway reverse-proxy `/showcase-api/retraining/*` →
`host.docker.internal:8877` (`showcase/retraining_proxy.py`, setting
`showcase_retraining_upstream`) để trang gọi same-origin.

**Cần rebuild gateway để proxy có hiệu lực** (`docker compose build gateway && docker compose up
-d gateway` — gián đoạn ias.id.vn vài giây; router nằm trong image build-time). Trước khi rebuild,
mở tab và nhập trực tiếp `http://localhost:8877` vào ô "Base URL" (CORS đã bật) để dùng ngay.

## Giao diện web — `/ui/`

`app.py` mount 1 trang tĩnh (HTML+JS thuần, không build step) tại `static/index.html`, phục vụ ở
đường dẫn `/ui/`. Sau khi chạy `uvicorn app:app --host 127.0.0.1 --port 8877` (xem trên), mở:

```
http://127.0.0.1:8877/ui/
```

Trang này:

- Hiển thị 10 bước pipeline (01→10) dạng flow, đánh dấu riêng các bước "gate/inference thật"
  (03, 09, 10) — nơi pipeline gọi thật vào model đang serving hoặc ghi đè production.
- Có ô nhập `X-API-Key`, lưu tạm trong `localStorage` của trình duyệt (không gửi đi đâu khác
  ngoài header `X-API-Key` của chính API này).
- Nút **DRY RUN (không thuê GPU)** — POST `/trigger` với `{"execute": false}`. Mô tả rõ ràng
  ngay cạnh nút: đây KHÔNG phải no-op tuyệt đối — vẫn chạy bước 01–03 thật, tức vẫn gọi
  inference thật vào model production đang serving để đo drift; chỉ là không train/không thuê
  GPU mới/không push HF/không đụng production.
- 1 khu vực "Vùng nguy hiểm" tách biệt (viền/màu đỏ): phải gõ đúng chữ `EXECUTE` vào 1 ô mới mở
  khoá được nút **Execute (THUÊ GPU + có thể push HF)** (`{"execute": true}`); tương tự phải gõ
  đúng `DEPLOY` mới mở khoá được tuỳ chọn `allow_deploy: true` (chỉ áp dụng cùng lúc bấm
  Execute). Cả hai nút đều có hộp thoại xác nhận lần cuối trước khi thực sự gọi API.
- Sau khi trigger (hoặc dán 1 `run_id` có sẵn vào ô "Theo dõi run"), trang poll
  `GET /status/{run_id}` mỗi 3 giây và hiển thị `last_log_lines` dạng terminal log-tail, cùng
  bảng chi tiết các `checks` trong `finetune-decision.json` (production & candidate) nếu có.

Trang không lưu trạng thái gì ở phía server ngoài những gì `/trigger` và `/status` vốn đã trả
về — không có DB/session riêng cho UI.

## Đồng bộ nhật ký run lên ClearML — `tools/sync_runs_to_clearml.py`

Công cụ độc lập, CHỈ ĐỌC `mlops/runs/`, không chạy pipeline, không gọi bất kỳ endpoint nào của
`trigger_api` (an toàn tuyệt đối về mặt tiền bạc — không đụng vast.ai/HF/production). Dùng để
đưa lịch sử các lần chạy (log + quyết định finetune-decision.json) lên ClearML self-host làm nơi
quan sát/tra cứu tập trung, thay vì phải mò từng thư mục `runs/<run_id>/`.

```bash
export CLEARML_CONFIG_FILE=/home/ict/.config/secai-mlops/clearml.conf   # client config đã verify
cd "mlops/tools"

# Xem trước sẽ đồng bộ gì, KHÔNG cần cài package clearml, KHÔNG gọi ClearML:
python3 sync_runs_to_clearml.py --dry-run

# Đồng bộ thật (cần `pip install clearml`) — quét toàn bộ mlops/runs/*:
python3 sync_runs_to_clearml.py

# Hoặc chỉ 1 run cụ thể:
python3 sync_runs_to_clearml.py --run-id <run_id>
```

Mỗi run_id map sang 1 ClearML Task (`project_name="secai-mlops-pipeline"`,
`task_name=<run_id>`) — chạy lại nhiều lần trên cùng run_id sẽ tìm và dùng lại task đã có
(idempotent), không tạo trùng. Task được gắn `trigger_api.log` làm artifact, và nếu có
`report-production/finetune-decision.json` / `report-candidate/finetune-decision.json` thì
upload nguyên văn JSON + báo cáo bảng các `checks` (metric/current/status/lý do) + 1 scalar tóm
tắt `<key>_finetune_required` (1 nếu `FINETUNE_REQUIRED`, ngược lại 0). Xem web UI của ClearML
tại `http://localhost:8080`, project `secai-mlops-pipeline`.
