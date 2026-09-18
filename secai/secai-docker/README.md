# secai-docker — bộ Docker thống nhất cho project SecAI/AIEngines

Gộp toàn bộ **core** của dự án vào **một** docker-compose project (`secai-docker`) chạy trên máy
local, đấu nối qua network nội bộ `secai-net`. Phần **model (LLM / embedding / reranker) vẫn gọi ra
vast** (`23.127.144.217`) qua HTTP — không serve local, không đụng GPU.

## Thành phần (4 container)

| Service | Image | Vai trò | Port host |
|---|---|---|---|
| `secai-docker-gateway` | `secai-agent-gateway:0.3.0-merged` (build từ `../secai-agent-gateway`) | BE FastAPI (IAS gateway) | **8140** → 8131 |
| `secai-docker-qdrant` | `qdrant/qdrant:v1.10.1` | Vector store (collection `cve_kb_hybrid`) | 6333 / 6334 |
| `secai-docker-neo4j` | `neo4j:5.20-community` | Knowledge graph | 7474 / 7687 |
| `secai-docker-redis` | `redis:7-alpine` | Lịch sử hội thoại (ephemeral 24h) | 6379 |

Data dùng **bind-mount** thẳng vào `/home/ict/AIEngines/secai/secai-data/` (không nhân đôi):
`_state/qdrant_storage`, `_state/neo4j_data`, `qdrant/` (snapshots ro), `exploitdb` (ro),
`poc` (rw, có `catalog.db`), `poc/store` (rw), `poc-sources` (ro, 12GB).

## Cách dùng

```bash
cd /home/ict/AIEngines/secai/secai-docker

# 1) (một lần) giải phóng port do stack cũ chiếm — data giữ nguyên qua bind-mount:
docker compose -f ../secai-data/docker-compose.yml down     # qdrant + neo4j cũ
docker rm -f redis-ai secai-gateway-evidence                # redis chat + evidence gw

# 2) build + up + in danh sách container
./up.sh
```

Gateway container ra **:8140** để **coexist** với native systemd `secai-gateway.service` (:8131,
đang phục vụ ias.id.vn — cần sudo để dừng, script không đụng vào).

Muốn container chiếm luôn :8131 (thay native):
```bash
sudo systemctl stop secai-gateway        # tự chạy — cần sudo
GATEWAY_HOST_PORT=8131 ./up.sh
```

## Kiểm tra

```bash
curl -s localhost:8140/health | jq                                  # 200, không degraded
curl -s localhost:6333/collections/cve_kb_hybrid | jq .result.status # green
docker compose ps                                                    # 4 container Up (healthy)
```

## Gỡ / dựng lại

```bash
docker compose down            # dừng stack (data ở bind-mount + named volume vẫn còn)
./up.sh                        # dựng lại
```

## Ghi chú

- **Model vẫn ở vast**: `QWEN_BASE_URL` / `EMBEDDING_API_URL` / `RERANKER_API_URL` trong `.env` trỏ
  `23.127.144.217`. Stack này KHÔNG tự serve model.
- **Core only**: không kèm SearXNG (`WEB_SEARCH_ENABLED=false`), không kèm kali-executor
  (`VERIFY_ENABLED=false`, `EXEC_ENABLED=false`), không kèm FE.
- **Relocate sang host khác**: vì dùng bind-mount, phải copy kèm cả `secai-data/` (đổi
  `SECAI_DATA_ROOT` trong `.env`).
- `.env` chứa secret → đã đưa vào `.gitignore`.


## Tối ưu chatbot (chat-opt) — image `0.2.4-chatopt-repro` (2026-09-04)

Image gateway đã qua **vòng lặp tối ưu chất lượng trả lời** (điểm 80.9 → **94.3/100**, grader Opus web-verify):
- **Guard `chat_final_text`** (`runner.complete`): chống câu rỗng/deflect, trả lời meta canonical, aggregate honest — **CHỈ đổi text, GIỮ nguyên API shape** (`{response,answer,message,metadata{run_id,profile,tool_registry}}`).
- **Prompt** (`chat.md`/`base_system.md`): meta direct-answer, output-hygiene, rule PoC.
- **Data** (Qdrant/Neo4j, không nằm trong image): hiệu đính CVSS 123k CVE vs NVD, alias enrichment, fix q036 (CVE-2006-1547 DoS không phải Beanutils RCE).
- **input-classifier** giữ nguyên (BẬT, `INPUT_CLASSIFIER_BLOCK_OFFTOPIC=false`).

### ✅ Build source (two-scopes ĐÃ GIẢI QUYẾT)
Nhánh `feat/chat-harness-optimize` (@3c05a04) đã **merge classifier ⋈ exploit-advance ⋈ chat-opt** thành 1 tree.
`docker compose build gateway` từ `../secai-agent-gateway` **tái tạo image đầy đủ** (classifier + guard + /ai/exploit + /ai/translate). Không còn cần workaround gateway-image-src.

### Rebuild / deploy
    cd secai-docker
    docker compose build gateway          # tái tạo image 0.2.4-chatopt-repro từ gateway-image-src
    docker compose up -d --no-build gateway   # hoặc dùng image có sẵn

Còn treo (cycle sau): retrieval PoC fallback (q022/q025/q029); dọn synthetic CVE-2026-*.
