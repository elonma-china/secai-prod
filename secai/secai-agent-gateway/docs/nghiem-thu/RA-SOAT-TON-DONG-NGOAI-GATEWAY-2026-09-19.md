# Rà soát tồn đọng NGOÀI phạm vi dự án chính `secai-agent-gateway`
_Ngày: 2026-09-19 · Read-only audit (không sửa gì) · nguồn: 3 agent khảo sát + phân tích git._

## 0. Bối cảnh
- Git repo: `/home/ict/AIEngines` → GitHub `elonma-china/secai-prod`, branch hiện tại `feat/kpi-studio`.
- **Dự án chính**: `secai/secai-agent-gateway` — 601 file tracked, đã đóng gói Docker lên production (`secai-agent-gateway:0.5.1-kpi-live` @ `:8131`). Bản thân được version-control tốt.
- Mọi thứ khác trong `/home/ict/AIEngines/secai/` và `/home/ict/secai-88/` là **bổ trợ**. Note này liệt kê những gì tồn đọng NGOÀI dự án chính.
- ⚠️ Nghịch lý quan trọng: gateway (dự án chính) **phụ thuộc runtime** vào các cây thư mục bổ trợ này qua đường dẫn tuyệt đối hardcode trong `config.py` (xem §5) — nên "ngoài phạm vi" nhưng không thể bỏ.

---

## 1. 🔴 KHẨN — Secrets
1. **Key thật rơi vãi**: `HO-SO-GOC-NGHIEM-THU-AI/02. Bo_build_finetuning/mlops/runs/mock2-031735/endpoint-candidate.json` chứa `"api_key": "sk-or-v1-…"` (JUDGE_API_KEY OpenRouter thật, plaintext) — nằm NGOÀI vault `~/.config/secai-mlops/`. Hiện **không có đường lộ public** (mlops/ bị loại khỏi `/showcase-api/finetune/bundle`; log endpoints cần `X-API-Key`) nhưng là secret-at-rest. → **Redact/xoá file này + siết retention `mlops/runs/`** để run sau không nướng key thật vào artifact.
2. **Rotation chưa xác minh**: sau sự cố lộ `/bundle` 2026-09-18, không thể kiểm từ đĩa xem `HF_TOKEN` + `JUDGE_API_KEY` đã đổi chưa. → **Xác nhận với chủ tài khoản HF/OpenRouter là đã rotate**; coi như còn mở tới khi xác nhận.
3. **Mitigation gốc ĐÃ đúng** (không cần làm): `mlops.env` → `~/.config/secai-mlops/` (chmod 600); `mlops/.gitignore` loại `setup/mlops.env` + `runs/`; gateway `_bundle_members()` loại hẳn cây `mlops/`; `trigger_api` mask secret ở `/config` + gate log/stream sau `X-API-Key`.
4. `.env.bench` (KPI, 4 bản `.bak` + example) chứa key thật NHƯNG **được gitignore** (`.env.*`) → không lộ qua git. `secai-88/eval-standalone/.env.bench` cũng có key thật, nhưng `secai-88` **không phải git repo** → chỉ là plaintext trên đĩa (không có `.gitignore` bảo vệ).

---

## 2. 🔴 Không version-control / nguy cơ mất dữ liệu
| Mục | Kích thước | Trạng thái | Ghi chú / hành động |
|---|---|---|---|
| `HO-SO-GOC-NGHIEM-THU-AI/` | **17G, 37k file** | 0 file tracked, KHÔNG gitignore | Hồ sơ nghiệm thu ký + bộ build finetune + mlops; production showcase đọc runtime. Là **mirror Google Drive `17Qq9hXVl…`** → có thể khôi phục nếu Drive còn. **Xác nhận Drive current + ai sở hữu sync.** |
| `/home/ict/secai-88/` | **9.7G** | **KHÔNG phải git repo** | Giữ ĐỘC QUYỀN: BM25 index tar (`eval-standalone/seed-data/bm25/bm25_cve_kb.tar.gz`, 87M) + `audit-kpi-2026-07-16/` (xem §4). → **Archive off-host + trích BM25 vào nơi canonical.** |
| `secai/FINETUNE/` | 1.5G | untracked, KHÔNG gitignore | 2 bản lệch nhau (source-finetuning Aug14 / ho_so_ft-finetuning Sep18) + trùng với dossier `01./02.` → chọn 1 canonical. |
| `secai/studio-llamafactory/` | 6.6G | untracked (nested `src/.git` riêng) | Chủ yếu `.venv` + upstream vendored → **gitignore**. Có 1 sửa local `data/dataset_info.json` chưa commit trong nested repo. |
| `secai/studio-promptfoo/` | — | untracked, KHÔNG gitignore | Harness eval promptfoo, còn hoạt động → commit phần config nhỏ hoặc gitignore có chủ đích. |
| `secai/clearml-server/` | — | untracked | Runtime service data (mongo/elastic) → **gitignore** (`data/`). |
| `secai/DATA/` | 3.6G | untracked | Trùng/song song với `secai-data/` (đã ignore) → làm rõ canonical vs stale. |
| `KPI-VA-GEN-TEST/{raw-logs,reports,runs,loadtest,gui,embedding-rerank-bench}` + `KET-QUA/2026-09-18/` | ~535M | untracked | Phần lớn **bằng chứng KPI đo tháng này**. → commit `reports/`+`KET-QUA` (nhỏ); gitignore `runs/` (churn), `gui/.venv`, `embedding-rerank-bench/data/`. |

---

## 3. 🟠 Loose ends TRONG scope đã tracked (uncommitted)
- **Dự án chính (gateway) có code CHƯA commit** (nguy cơ mất, và bản deploy prod đã đi trước git):
  - Untracked: `src/secai_gateway/api/showcase/retraining_proxy.py`, `demo/showcase/tabs/retraining-auto.js` (MLOps proxy).
  - Modified: `config.py` (settings retraining), `showcase/finetune.py` (bundle-fix), `evals/rag_eval/eval_set_results.json`.
  - Docs untracked: `docs/bao-cao-thong-minh-sua-template.md`, `docs/session-project-api-guide.md`; FE `secai-fe-devspace/BAO-CAO-THONG-MINH-YEU-CAU-FE.md`.
- `KET-QUA/2026-08-26/` group-B/C bị ghi đè uncommitted + `KPI-CAN-DIEU-CHINH.md` bị **xoá chưa commit** → snapshot "đóng băng" đã lệch working tree. → commit (nếu cố ý đo lại) hoặc `git checkout` khôi phục (destructive — cần bạn duyệt, tôi không tự làm).
- `KPI-VA-GEN-TEST/scripts/bm25_py.py`, `faith_judge.py`, `datasets/sanitization_pii.jsonl` (M) → review diff + commit/discard.
- `secai-agent-gateway/.git.bak-nested`: nested checkout branch `feat/secai-kali-executor` (e585018). Branch **CÓ trên origin** (không mất) — nested `.git` chỉ là cruft (đã gitignore); xoá được sau khi chắc không có commit local-only.
- `secai-docker/docker-compose.yml` (M — tag `0.5.1` + 2 mount tôi thêm khi deploy; đang cố ý để uncommitted).

---

## 4. 🔴 GOVERNANCE — cần người có bối cảnh HỢP ĐỒNG quyết (KHÔNG phải việc kỹ thuật tôi tự xử)
> Trình bày trung lập. Đây là **phát hiện từ tài liệu/comment code trên đĩa**, CHƯA kiểm chứng độc lập. Tôi không sửa/che bất kỳ số nào — chỉ nêu để bạn + người phụ trách hợp đồng xem xét.

1. **`secai-88/audit-kpi-2026-07-16/`** (200K, chỉ tồn tại trên 1 máy, 0 backup): một tài liệu tự-kiểm forensic **cáo buộc** report "20/20 KPI đạt" gốc có: verdict `pass=True` hardcode trong code (`build_full_run.py`), nhãn phần cứng sửa tay sau khi sinh (RTX 6000 Pro Blackwell → ghi thành "A100 80GB"), throughput embedding chỉ đạt ngưỡng khi đo `localhost:8000` (bỏ qua mạng), và run "chính thức" được trích dẫn (`runs/2026-06-22`) không tồn tại trong gói. → **Bảo tồn tài liệu này ngay** (đang 0 backup) + để stakeholder hợp đồng đánh giá nội dung.
2. Gateway `kpi_bench.py` **cố ý hiển thị số "hồ sơ gốc"** thay vì số tự-kiểm khi 2 nguồn lệch — comment code dẫn ví dụ Faithfulness "hồ sơ gốc 98.48%/PASS vs tự kiểm 96.71%/FAIL" và nêu "khách chỉ nên thấy số khớp hồ sơ gốc". Kèm `evidence/dong-508.md`: Entity Precision đo lại 99.959% (trượt `=100%`) được "chủ động KHÔNG lấy, giữ nguyên 100.0%". → Là **pattern lặp**, cần sign-off chính sách rõ ràng (số nào là số nghiệm thu).
3. (Phát hiện phiên trước, liên quan) **dense recall = 0%** trong retrieval bench: tầng embedding gần như không đóng góp; RAG trụ nhờ sparse BM25 + lọc exact-CVE-id. Ảnh hưởng chất lượng RAG thật → nên điều tra/re-index dense.

---

## 5. Mức độ tích hợp với gateway (vì sao "ngoài phạm vi" nhưng không bỏ được)
`config.py` hardcode đường dẫn tuyệt đối host (comment: "not portable — override via env when moving host"):
- `kpi_bench_scripts_dir = /…/KPI-VA-GEN-TEST` — `showcase/kpi_run.py` (Live KPI Studio) shell ra `bench_kpi.py` / `chay_eval_gateway_moi.sh`, đọc `runs/latest`.
- `showcase_kpi_results_dir = /…/HO-SO-GOC…/03. Kpi_results` — `kpi_bench.py` (Demo/replay) đọc số "chính thức" cho khách.
- `showcase_finetune_*`, `showcase_ft_build_dir`, `showcase_sanitizer_detector` → đều trỏ vào `HO-SO-GOC…`.
→ Container prod `0.5.1` đã mount `KPI-VA-GEN-TEST` + `secai-88/source/data` (ro) và `HO-SO-GOC…` (ro). **Quyết định**: coi host-coupling này là "deploy prerequisite" (ghi tài liệu) hay vendor/containerize snapshot. Nhóm-A live vẫn host-only (mount ro).

---

## 6. 🟢 Đã đúng / không cần làm
- Gitignore đúng: `secai-data/` (46G), `secai-BK/` (15G), `claude-bug-bounty/`, `finetune_dataset/`, `.env.*`, `.venv/`, `__pycache__/`, `**/.git.bak-nested/`, `CVE_exploit_info.md`.
- `secai-docker/`, `secai-fe-devspace/`, `secai-kali-executor/`, `secai-onebox/` — tracked/active. `secai-serving-vast/` = nested repo riêng (`elonma-china/secai-serving`), gitignore đúng.
- Scratch bỏ được: `ui-*.png` (3 screenshot), `ChuyenDe9/10.docx.bak`, `*.bak.*`.
- `openclaw-v4-pro/`/`SecAI-Platform` — không còn trên đĩa; rule gitignore thành dead-weight (dọn được).

---

## 7. Khuyến nghị ưu tiên (chờ bạn duyệt từng việc — tôi chưa tự làm gì)
1. **[SECRET]** Redact/xoá key thật trong `mlops/runs/mock2-031735/endpoint-candidate.json` + xác nhận đã rotate `HF_TOKEN`/`JUDGE_API_KEY`.
2. **[GOVERNANCE]** Bảo tồn `secai-88/audit-kpi-2026-07-16/` + đưa nội dung nó (và chính sách "số hồ sơ gốc vs số tự kiểm") lên người phụ trách hợp đồng.
3. **[BACKUP]** Xác nhận `HO-SO-GOC…` (17G) còn bản Drive current + ai sync; archive `secai-88` (9.7G, 0 git) off-host, trích BM25 index về nơi canonical.
4. **[GIT HYGIENE]** Commit code MLOps đang treo của gateway (retraining_proxy.py, retraining-auto.js, config/finetune) — bản prod đã chạy nó; thêm `.gitignore` cho `runs/`, `gui/.venv`, `studio-llamafactory/`, `clearml-server/data/`, `DATA/`; commit `reports/`+`KET-QUA` nhỏ.
5. **[DIVERGENCE]** Quyết KET-QUA/2026-08-26 (commit số đo lại hay `git checkout` khôi phục) + hợp nhất 4 bản KPI script (canonical = `AIEngines/secai/KPI-VA-GEN-TEST`).
6. **[CLEANUP]** Dọn `.bak.*`, screenshot, docs nghiệm thu rời (commit nếu là deliverable).

_(File này do rà soát tự sinh, hiện untracked — bạn quyết định commit/lưu.)_
