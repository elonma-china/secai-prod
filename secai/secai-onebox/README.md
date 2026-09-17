# secai-onebox — gom cả SecAI vào 1 khối Docker trên 1 GPU box (N-GPU tự phát hiện, air-gap)

Gộp **model serving (LLM + embedding + reranker + nginx LB)** + **DB (qdrant/neo4j/redis)** +
**BE gateway** vào **một `docker-compose` duy nhất**, chạy trên **một GPU box** (vast.ai VM
hoặc bất kỳ máy Linux có Docker + NVIDIA Container Toolkit). Không có SSH tunnel ra ngoài cho
model — mọi service gọi nhau qua tên service nội bộ. Chỉ **gateway** expose ra ngoài.

> Data DB đặt trên **Local Volume** của box (`$DATA_ROOT`, sống qua destroy instance).

```
secai-serving (N-GPU tự phát hiện, xem serving/Dockerfile)
    └─ nvidia-smi -L → N × (llm-g + embedding-g + reranker-g) + nginx LB nội bộ (8001/8000/8002)
qdrant:6333  neo4j:7687  redis:6379   ──► gateway:8131 ─► EXPOSE ra ngoài (DUY NHẤT, qua SSH tunnel)
Local Volume ($DATA_ROOT) ──► qdrant_storage + neo4j_data + redis + poc/* + hf-cache + bm25_local_model
```

**N-GPU không fix cứng**: `secai-serving` build từ `serving/Dockerfile`, bake nguyên
`../secai-agent-gateway/secai-serving-vast/vast/on_start_cyber_f1.sh` — script này tự dò
`NUM_GPUS=$(nvidia-smi -L | wc -l)` rồi launch N replica (TP=1, zero NCCL) + tự sinh nginx
upstream N-thành-viên. Compose reservation dùng `count: all` (không `device_ids`) → chạy đúng
với 2, 4, hay N GPU bất kỳ. Đã verify thực tế: **A100 SXM4** (sm_80, instance tham chiếu
`48438534`) và **RTX PRO 6000 WS Blackwell** (sm_120, instance `48436187` — torch cu130 +
CompressedTensors WNA16 Marlin MoE load + inference thành công).

## Model đang serve (đều PUBLIC trên HuggingFace — không cần HF_TOKEN)

| Service | Model id (client gửi = server serve) | vLLM |
|---|---|---|
| LLM | `DuyTa/Cyber-F1-AWQ` (Qwen3.5-MoE AWQ int4) | len 65536, reasoning qwen3, tool qwen3_coder, thinking off |
| Embedding | `DuyTa/sec-embedding` | runner pooling, fp16, len 512 |
| Reranker | `DuyTa/sec-rerank` | pooling + hf-overrides Qwen3ForSequenceClassification + jinja |

> gateway gửi field `model` đúng bằng các id trên — compose serve **đúng các id này**, không
> phải bge-m3/Qwen3-Reranker gốc (đó chỉ là default của on_start script khi không set env).

## AIR-GAP

Mục tiêu: sau khi deploy xong, stack chạy **không cần internet**, kể cả restart:
- Mọi tool gateway có thể gọi ra ngoài (`web_fetch`, `fetch_reference`, `web_search`,
  `github_advisory`, `poc_github_ondemand`, `bughunt_recon`) đã khoá `=false` — 2 lớp: `.env`
  VÀ `environment:` của service `gateway` trong compose (phòng khi sửa 1 chỗ quên chỗ kia).
- `secai-serving/Dockerfile` bake sẵn nginx/curl ở BUILD TIME — container không `apt-get` lúc
  chạy. Model weights (`hf-cache`) + fastembed `Qdrant/bm25` fallback (`bm25_local_model`) phải
  **pre-seed** trước (xem `seed-data.sh`); sau khi seed đủ, set `HF_HUB_OFFLINE=1` trong `.env`.
- `seed-data.sh`: **KHÔNG dùng `--skip-poc-sources`** cho deploy air-gap — flag đó re-clone từ
  GitHub trên VM lúc chạy.
- `poc-sources` (13G) luôn upload đủ, không re-clone.

## Files

| File | Vai trò |
|---|---|
| `docker-compose.yml` | 5 service: `secai-serving` (N-GPU, build) + qdrant + neo4j + redis + gateway (build) |
| `serving/Dockerfile` | image serving: `vllm/vllm-openai` + nginx bake sẵn + `on_start_cyber_f1.sh` |
| `.env` | config gateway + biến interpolation cho compose (serving/infra). **Chứa secret — .gitignore** |
| `.env.example` | bản mask để tham chiếu |
| `templates/qwen3_reranker.jinja` | chat-template cho reranker (COPY vào image serving lúc build) |
| `up-onebox.sh` | thuê máy vast VM mới + provision + up (chạy TỪ local) — dùng khi CHƯA có box |
| `onstart-onebox.sh` | cài docker+NVIDIA toolkit (nếu thiếu) + build+up (chạy TRONG box) |
| `seed-data.sh` | rsync data local → Local Volume của box (qdrant/neo4j/poc/exploitdb/hf-cache/bm25) |

## Cách chạy — deploy lên box ĐÃ CÓ SẴN (instance đã thuê, không rent mới)

```bash
# 0) gắn SSH key nếu chưa vào được (VM-mode: attach ssh KHÔNG tự áp dụng cho instance đang
#    chạy — cần reboot 1 lần để cloud-init nạp lại key):
vastai --url https://cloud.vast.ai attach ssh <IID> "$(cat ~/.ssh/id_ed25519.pub)"
vastai --url https://cloud.vast.ai reboot instance <IID>

# 1) rsync code lên box
rsync -ah --exclude .git --exclude __pycache__ --exclude .venv \
  ./ root@<IP>:/opt/secai/secai-onebox/
rsync -ah --exclude .git --exclude __pycache__ --exclude .venv --exclude data --exclude '*.log' \
  ../secai-agent-gateway/ root@<IP>:/opt/secai/secai-agent-gateway/

# 2) trong box: cài docker+NVIDIA toolkit (idempotent, skip nếu đã có) + build + up
ssh root@<IP> "sudo bash /opt/secai/secai-onebox/onstart-onebox.sh"

# 3) seed data (chạy TỪ máy local, sau khi mkdir DATA_ROOT trên box đã có — onstart tự tạo)
./seed-data.sh --host <IP> --port <SSH_PORT> --key ~/.ssh/id_ed25519
ssh root@<IP> "cd /opt/secai/secai-onebox && docker compose restart qdrant neo4j gateway"
```

Nếu chưa có box (muốn rent VM mới): dùng `./up-onebox.sh` (xem header file — cần
`--template <VM_TEMPLATE_HASH>`, lấy tại https://cloud.vast.ai/templates/).

## Expose gateway :8131 ra ngoài cho FE (không port trực tiếp)

VM-mode KVM instance thường **không map port tuỳ ý** ra public IP (chỉ port SSH đã khai lúc
tạo). Dùng **SSH local-forward** qua đúng port SSH đó — không cần vast cấp thêm port, không
phụ thuộc dịch vụ tunnel bên thứ 3 (giữ đúng tinh thần air-gap).

**Chạy trên MÁY FE** (đã có sẵn `fe-tunnel.sh` + `fe-tunnel.service` bền, auto-reconnect):
```bash
# một lần thử:
REMOTE_HOST=38.65.239.54 REMOTE_PORT=20700 SSH_KEY=~/.ssh/id_ed25519 ./fe-tunnel.sh
# chạy nền bền:  nohup ./fe-tunnel.sh >/tmp/fe-tunnel.log 2>&1 &
# hoặc systemd (tự bật sau reboot máy FE): xem hướng dẫn trong fe-tunnel.service
# FE trỏ GATEWAY_URL=http://127.0.0.1:8131   (kiểm tra: curl http://127.0.0.1:8131/health)
```
`fe-tunnel.sh` tự dùng `autossh` nếu có, không thì vòng lặp ssh + `ServerAliveInterval` +
backoff để tự bật lại khi rớt. Đã test end-to-end: `/health`=ok và `/ai/chat` chạy qua tunnel.

## Kiểm chứng (trong box)

```bash
cd /opt/secai/secai-onebox && docker compose ps                                # 5 service healthy
curl -H "Authorization: Bearer sk-secai2026" http://localhost:8001/v1/models   # + :8000 :8002 → 200
curl http://localhost:6333/collections/cve_kb_hybrid                           # collection tồn tại
curl http://localhost:8131/health                                             # gateway 200
docker compose logs -f secai-serving                                          # theo dõi N-GPU cold-load
```
Test toàn bộ API (không chỉ health): `/ai/chat`, `/ai/chat/stream`, `/ai/translate(+/stream)`,
`/ai/generate-report(+/stream)`, `/ai/generate-statistical-report/stream`, `/ai/exploit`,
`/admin/refresh-models` — đối chiếu `/openapi.json` để không bỏ sót route.

## Lưu ý quan trọng

- **Disk không resize** sau khi tạo VM → cần đủ chỗ cho weights (~27G, 3 model) + data seed
  (~18G) + images (~10G) + OS. **RAM** là nút cổ chai (neo4j+qdrant+redis+N×vLLM).
- **Local Volume khóa 1 máy vật lý**; destroy instance vẫn giữ data, nhưng không di chuyển host được.
- **Bảo mật**: gateway *unauthenticated by design*. Đã giữ `VERIFY_ENABLED=false` +
  `EXEC_ENABLED=false` (khuyến nghị mạnh trên box công khai — spawn container/chạy lệnh root
  không qua auth). Đổi `API_KEY`/`ADMIN_API_KEY`/`NEO4J_PASSWORD` trong `.env` trước khi chạy thật.
  vLLM/DB **không** expose ra ngoài — chỉ gateway, và chỉ qua SSH tunnel (không public port).
- **Neo4j data lấy từ production đang chạy (`secai-docker`)**: dùng `neo4j-admin database dump`
  với downtime cực ngắn (~15s, stop → dump → start ngay) thay vì copy trực tiếp store đang live
  (Neo4j Community không có hot-backup thật). Qdrant dùng snapshot API (live, 0 downtime).
