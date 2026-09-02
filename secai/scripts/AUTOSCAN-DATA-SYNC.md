# Cập nhật feat: Đồng bộ CVE→KB từ Intelligent-AutoScan

Cutover 2026-09-02. Nguồn CVE của KB chuyển từ **NVD** sang **AutoScan data API**. PoC giữ nguyên nguồn git.

## 1. Tóm tắt thay đổi
- **Trước:** cron kéo NVD → `cve_intel.db` → embed vào Qdrant/Neo4j.
- **Nay:** cron kéo **CVE từ AutoScan API** → embed vào Qdrant `cve_kb_hybrid` (dense+bm25) + Neo4j. Incremental, refresh-if-newer.
- **PoC:** không đổi — vẫn từ 7 repo git (`build_catalog`). AutoScan-PoC tắt (chỉ exploitdb = tập con).
- **NVD giữ làm dự phòng nóng**, chưa xóa.

## 2. Luồng dữ liệu
```
AutoScan API ──(CVE, incremental)──> sync_autoscan_to_kb.py ──> Qdrant cve_kb_hybrid + Neo4j
7 repo git ───(git pull + build_catalog)───────────────────> catalog.db (PoC)
```

## 3. File & đường dẫn (tuyệt đối)

### Script sync (đã merge vào main)
- `/home/ict/AIEngines/secai/scripts/sync_autoscan_to_kb.py` — CVE → Qdrant + Neo4j.
- `/home/ict/AIEngines/secai/scripts/sync_autoscan_poc.py` — exploitdb AutoScan → catalog (mặc định TẮT trong luồng).
- `/home/ict/AIEngines/secai/scripts/autoscan_daily_sync.sh` — wrapper: preflight API+embed, chạy bước KB.
- `/home/ict/AIEngines/secai/scripts/tests/` — unit test + fixtures.

### Cron (điểm vào)
- `/home/ict/Downloads/CVE-DB/run_sync.sh` — lịch `0 3 * * *`; đã thay bước NVD bằng gọi `autoscan_daily_sync.sh`.
- `/home/ict/Downloads/CVE-DB/run_sync.sh.bak-pre-autoscan-2026-09-02` — bản NVD cũ (rollback).

### State / log
- `/home/ict/AIEngines/secai/secai-data/_state/autoscan_to_kb.json` — watermark CVE→KB.
- `/home/ict/AIEngines/secai/secai-data/_state/autoscan_poc.json` — watermark PoC (khi bật).
- `/home/ict/AIEngines/secai/secai-data/_state/autoscan_daily.log` — log wrapper.

### Kho dữ liệu
- Qdrant `http://localhost:6333` collection `cve_kb_hybrid` (dense 1024 + sparse bm25/idf).
- Neo4j `http://localhost:7474` (CVE/CWE/Product + HAS_WEAKNESS/AFFECTS).
- PoC catalog: `/home/ict/AIEngines/secai/secai-data/poc/catalog.db`.
- PoC git sources: `/home/ict/AIEngines/secai/secai-data/poc-sources/`.

### Cấu hình / nguồn
- Gateway `.env`: `/home/ict/AIEngines/secai/secai-agent-gateway/.env` — chứa `AUTOSCAN_API`, `AUTOSCAN_USER`, `AUTOSCAN_PASS`, `EMBEDDING_*`, `NEO4J_*`.
- AutoScan API: `http://34.50.65.146:8789` (Basic Auth; IP GCP hay đổi — sửa 1 dòng `AUTOSCAN_API` khi đổi).

## 4. Hành vi chính
- **refresh-if-newer** (mặc định): CVE mới → nạp; nguồn `updated_at` mới hơn bản đã lưu → refresh tại chỗ (uuid5 id, không trùng); không đổi → bỏ qua. Cờ `--no-refresh` / `--update-existing`.
- **Watermark** `updated_at`; biên `>=`. Watermark **chỉ tiến khi 0 lỗi** (fail giữa chừng không mất delta).
- **L1**: fetch detail `/api/cve/{id}` + parse `vendors` → products/CWE/exploit-flag đầy đủ.
- **L2**: mất watermark → **từ chối** quét toàn bộ (bắt buộc `--full`).
- **L3**: refresh **giữ** products/capec/mitre của point cũ khi bản mới rỗng.
- **Preflight (D1)**: API hoặc embed chết → bỏ bước KB, exit≠0 (cron thấy lỗi), KB không ghi dở.

## 5. Vận hành

Chạy tay (máy build, `AIRGAP=false`; cần `AUTOSCAN_PASS` trong `.env`):
```bash
python3 /home/ict/AIEngines/secai/scripts/sync_autoscan_to_kb.py --dry-run --max-pages 1   # thử, không ghi
python3 /home/ict/AIEngines/secai/scripts/sync_autoscan_to_kb.py                            # incremental thật
bash    /home/ict/AIEngines/secai/scripts/autoscan_daily_sync.sh                            # đúng việc cron làm
```
Backfill toàn bộ (nặng): thêm `--full`. Bật lại PoC-from-AutoScan: `AUTOSCAN_SYNC_POC=1`.

Rollback về NVD:
```bash
cp /home/ict/Downloads/CVE-DB/run_sync.sh.bak-pre-autoscan-2026-09-02 /home/ict/Downloads/CVE-DB/run_sync.sh
```

## 6. Kiểm chứng đã thực hiện
- Chạy thật: 172 CVE nạp, 0 lỗi, watermark tiến đúng; point có dense+bm25, Neo4j có node+CWE.
- CTO review: 5 lỗi logic (L1–L5) đã fix + D1/D5; unit test xanh.
- Đo retrieval: tra theo mã CVE exact 100%; semantic R@1 ~87.5%.

## 7. Git
- Branch `feat/autoscan-data-sync` đã **merge vào main** (monorepo scope, local). Chưa push.
- Lưu ý scope: `origin/main` là layout standalone khác — script sync thuộc monorepo, không port sang origin/main.
