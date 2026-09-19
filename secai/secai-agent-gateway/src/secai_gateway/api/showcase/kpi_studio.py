"""KPI Studio — `/showcase-api/kpi-studio/*`.

Mở rộng của nhóm K (`kpi_bench.py`, vốn chỉ phát lại log của MỘT bản ghi hồ sơ gốc đã đóng
băng) thành một "studio" cho người vận hành: catalog đủ 19 KPI kèm mô tả/công thức/ngưỡng,
duyệt phân trang các bộ dữ liệu THẬT dùng để bench, và kiểm tra nhanh 4 dịch vụ backend
(LLM/embedding/reranker/Qdrant) có sẵn sàng để bench hay không.

Catalog KPI (formula/threshold/dataset — nội dung THAM KHẢO/PHƯƠNG PHÁP LUẬN, không phải số
đo) được copy tĩnh từ `KPI-VA-GEN-TEST/01-KPI/kpi-registry.json` (nguồn sự thật duy nhất cho
định nghĩa 19 KPI, ngày lập 2026-08-26) vào ngay trong module này — để tab Studio không phụ
thuộc filesystem path của thư mục làm việc KPI-VA-GEN-TEST/ (không nằm trong repo này).

Dataset browse + service-status đọc THẬT (không mô phỏng):
  - Dataset: đọc thẳng file .jsonl trong hồ sơ gốc (settings.showcase_kpi_results_dir), giống
    quy ước "mọi số liệu đọc thẳng từ hồ sơ gốc" đã áp dụng cho toàn bộ nhóm K.
  - Service-status: gọi thật LLM `/models`, embedding `/v1/embeddings`, reranker `/v1/rerank`,
    Qdrant `/collections/{name}` — đúng lệnh verify tay đã ghi trong CLAUDE.md (không phải chỉ
    ping health, mà xác nhận đúng TÊN MODEL đang serve khớp cấu hình, bắt được đúng lớp lỗi
    "model lệch tên -> 404" đã từng xảy ra thật 2026-08-23).

Việc CHẠY bench (run-stream, live log) không lặp lại ở đây — Studio tái dùng nguyên
`/showcase-api/kpi-bench/tests/{id}/run-stream` đã có sẵn trong kpi_bench.py.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Query

from ...config import get_settings
from ._common import not_found

router = APIRouter(prefix="/showcase-api/kpi-studio", tags=["showcase-kpi-studio"])


# ─────────────────────────────────────────────────────────────────────────────
# 1) Catalog — 19 KPI + 2 mục thông tin thêm (KPI thứ 12 không ngưỡng, mã thứ 20 mồ côi)
# ─────────────────────────────────────────────────────────────────────────────

GROUPS = [
    {"id": "A", "name": "Chất lượng AI", "count": 11, "run_cmd": "bash run_eval_all.sh",
     "note": "1 lệnh ra cả 11 KPI → runs/<ngày-giờ>-evalall/raw-logs/kpi-summary.json"},
    {"id": "B", "name": "Hiệu năng chatflow", "count": 5, "run_cmd": "./bench-perf.sh",
     "note": "Chỉ R-CTRL-RESP15 là gate thật so ngưỡng; 4 mã còn lại không ép ngưỡng trong code."},
    {"id": "C", "name": "Throughput mô hình", "count": 3, "run_cmd": "./bench-models.sh",
     "note": "Ngưỡng tính trên 1× A100 — phải đo tại chỗ (SSH vào máy serving), đo qua WAN sai lệch ~5x."},
]

_KPI: list[dict[str, Any]] = [
    # ── Nhóm A — Chất lượng AI (11) ──────────────────────────────────────────
    {"code": "R-KPI-EMB96", "name": "Embedding Hit Rate@10", "group": "A", "unit": "%",
     "threshold": 96, "op": ">", "enforced": True, "higher_is_better": True,
     "formula": "100 × hit_idaware / n. Hit khi gold metadata.cve_id nằm trong top-10 đường "
                "id-aware: exact-CVE filter chèn trước, rồi RRF(dense top-20, BM25 top-20) K=60.",
     "dataset": "acceptance_dinh_danh + acceptance_khac_phuc + acceptance_tinh_huong (bỏ câu bẫy "
                "và câu nhiều CVE → còn ~735/1000 câu)",
     "dataset_keys": ["acceptance_dinh_danh", "acceptance_khac_phuc", "acceptance_tinh_huong"],
     "depends_on": ["EMBED_URL (bắt buộc đúng dòng bge-m3 đã index)", "QDRANT_URL", "BM25 artifacts"],
     "script": "retrieval_kpi.py", "note": "EMBED_MODEL sai (không phải bge-m3) → lệch index → recall sập ~0."},
    {"code": "R-KPI-RERANK90", "name": "Reranking Precision@1", "group": "A", "unit": "%",
     "threshold": 90, "op": ">", "enforced": True, "higher_is_better": True,
     "formula": "100 × p1 / n. Doc top-1 sau rerank có cve_id == gold. Rerank trên id-aware top-20, "
                "mỗi doc cắt 1200 ký tự.",
     "dataset": "như R-KPI-EMB96", "dataset_keys": ["acceptance_dinh_danh", "acceptance_khac_phuc", "acceptance_tinh_huong"],
     "depends_on": ["RERANK_URL /v1/rerank"],
     "script": "retrieval_kpi.py", "note": "P@1 bị chặn trần bởi recall của tập ứng viên."},
    {"code": "R-KPI-FAITH98", "name": "Faithfulness (RAGAS)", "group": "A", "unit": "%",
     "threshold": 98, "op": ">", "enforced": True, "higher_is_better": True,
     "formula": "Judge kiểu RAGAS-NLI: tách đáp án thành các mệnh đề sự kiện về CVE, đếm số mệnh "
                "đề được CONTEXT chứng minh → supported/total; KPI = trung bình các mẫu.",
     "dataset": "answers.jsonl (đáp án đã sinh) + gold-contexts scroll từ Qdrant theo cve_id",
     "dataset_keys": [],
     "depends_on": ["RAGAS_JUDGE_BASE_URL", "RAGAS_JUDGE_MODEL", "OPENAI_API_KEY (giá trị = key DeepSeek)"],
     "script": "faith_judge.py",
     "note": "98% được tài liệu nội bộ tự nhận là KHÔNG khả thi với RAG (đo thật 88.8%/822 mẫu). "
             "Không có judge độc lập thì giám khảo trùng chính model đang đo → lỗ hổng phương pháp luận."},
    {"code": "R-KPI-RELEV90", "name": "Answer Relevance", "group": "A", "unit": "%",
     "threshold": 90, "op": ">", "enforced": True, "higher_is_better": True,
     "formula": "100 × mean(score). LLM-judge trả {\"score\": 0..1}, temperature=0: 1.0 đúng trọng "
                "tâm / 0.5 liên quan nhưng lan man / 0.0 lạc đề.",
     "dataset": "answers.jsonl (mẫu đầu --ragas-sample, mặc định 250)", "dataset_keys": [],
     "depends_on": ["LLM_BASE_URL"], "script": "llm_kpi.py",
     "note": "Hợp đồng ghi 'Cosine Similarity' nhưng cài đặt hiện tại là LLM-judge, và giám khảo = "
             "chính model đang đo → đổi model là đổi luôn thước đo."},
    {"code": "R-KPI-ENTITY100", "name": "Entity Precision CVE/CWE", "group": "A", "unit": "%",
     "threshold": 100, "op": "=", "enforced": True, "higher_is_better": True,
     "formula": "prec = (số_id_nêu_ra − số_id_bịa) / số_id_nêu_ra mỗi đáp án; KPI = 100 × mean(prec). "
                "Đáp án không nêu id nào bị loại khỏi mẫu số.",
     "dataset": "answers.jsonl + contexts.jsonl", "dataset_keys": [],
     "depends_on": ["QDRANT_URL"], "script": "llm_kpi.py",
     "note": "1 id bị tính là 'bịa' khi hội đủ 4 điều kiện (không thuộc gold/context/câu hỏi, không "
             "có trong KB Qdrant, CWE thì không nằm trong whitelist MITRE ~180 id, và được khẳng định)."},
    {"code": "R-KPI-HALLU2", "name": "Hallucination Rate", "group": "A", "unit": "%",
     "threshold": 2, "op": "<", "enforced": True, "higher_is_better": False,
     "formula": "100 × số_câu_ảo_giác / TỔNG số đáp án (mẫu số là tất cả đáp án, không loại câu nào).",
     "dataset": "answers.jsonl — cần đủ 30 câu bẫy trong acceptance_tinh_huong",
     "dataset_keys": ["acceptance_tinh_huong"], "depends_on": [], "script": "llm_kpi.py",
     "note": "2 lý do bị tính ảo giác: (a) câu bẫy có fake_cve mà đáp án không từ chối; (b) có entity "
             "bịa bất kỳ từ R-KPI-ENTITY100."},
    {"code": "R-LLM-SAST85", "name": "SAST accuracy", "group": "A", "unit": "%",
     "threshold": 85, "op": ">=", "enforced": True, "higher_is_better": True,
     "formula": "base_grade = số_check_đạt/tổng_check; câu ĐẠT khi score ≥ 0.75, cộng check 'nêu "
                "đúng CWE gold'. KPI = 100 × npass/ntot, chấm theo ĐA SỐ 2/3 lượt.",
     "dataset": "60 câu capability=sast + file mã lỗi mô phỏng", "dataset_keys": [],
     "depends_on": ["backend chat"], "script": "task_kpi.py",
     "note": "CẠM BẪY LỚN: repo tồn tại 3 con số mâu thuẫn (85%/100%/21.43%) do lẫn giữa chấm "
             "majority-2/3 (nghiệm thu) và chấm 1 lượt (pipeline KPI) — không được trộn."},
    {"code": "R-LLM-EXPLOIT85", "name": "Exploit Explanation", "group": "A", "unit": "%",
     "threshold": 85, "op": ">=", "enforced": True, "higher_is_better": True,
     "formula": "base_grade + check 'có nêu cơ chế' (khớp bất kỳ từ khoá trong danh sách EXPLOIT_KW); "
                "ĐẠT khi score ≥ 0.75.",
     "dataset": "60 câu capability=exploit", "dataset_keys": [],
     "depends_on": ["backend chat"], "script": "task_kpi.py", "note": "Chấm 1 lượt, không majority."},
    {"code": "R-LLM-REMED60", "name": "Auto-remediation", "group": "A", "unit": "%",
     "threshold": 60, "op": ">=", "enforced": True, "higher_is_better": True,
     "formula": "base_grade thuần (gồm must_have_sections của chính câu đó); ĐẠT khi score ≥ 0.75.",
     "dataset": "60 câu capability=remediation", "dataset_keys": [],
     "depends_on": ["backend chat"], "script": "task_kpi.py", "note": None},
    {"code": "R-SEC-PII", "name": "Sanitization PII/Secret F1", "group": "A", "unit": "%",
     "threshold": 90, "op": ">", "enforced": True, "higher_is_better": True,
     "formula": "Micro P/R/F1 gộp 5 loại (email/ip/phone/api_key/password): recall=TP/(TP+FN), "
                "precision=TP/(TP+FP), F1=2PR/(P+R).",
     "dataset": "500 mẫu SINH RUNTIME bởi gen_pii_dataset.py --n 500 --seed 42 (72% positive / "
                "28% hard-negative)", "dataset_keys": ["sanitization_pii"],
     "depends_on": [], "script": "sanitization_kpi.py",
     "note": "Bộ dò là detector.py (regex + phân loại entropy Shannon > 3.5). Dataset sinh lại mỗi "
             "lần chạy — cùng seed thì cùng bộ."},
    {"code": "R-KPI-RADAR90", "name": "Radar coverage area", "group": "A", "unit": "%",
     "threshold": 90, "op": ">", "enforced": True, "higher_is_better": True,
     "formula": "coverage = 100 × diện_tích_ngũ_giác(đạt được) / diện_tích(cam kết), 5 trục: "
                "Faithfulness/Relevance/Entity Precision/Logic-Reasoning/Safety.",
     "dataset": "tổng hợp từ 5 trục (không đo độc lập)", "dataset_keys": [],
     "depends_on": [], "script": "build_reports.py",
     "note": "Là KPI PHẢI SINH, không đo độc lập. Metric thiếu → tính là 0 nên một bước lỗi kéo tụt "
             "cả radar. Giá trị có thể vượt 100."},

    # ── Nhóm B — Hiệu năng chatflow (5) ──────────────────────────────────────
    {"code": "R-PERF-TPS", "name": "TPS chatflow (token/s mỗi luồng)", "group": "B", "unit": "tok/s",
     "threshold": [20, 35], "op": "range", "enforced": False, "higher_is_better": True,
     "formula": "tokens_out / (total_ms − ttft_ms), lấy p50 qua các luồng đang chạy đồng thời.",
     "dataset": "perf_bench_100 (100 prompt)", "dataset_keys": ["perf_bench_100"],
     "depends_on": ["backend chat"], "script": "perf_load.py",
     "note": "KHÔNG PHẢI GATE THẬT — code luôn trả pass=True miễn có số đo, không so ngưỡng."},
    {"code": "R-PERF-TTFT", "name": "TTFT p50", "group": "B", "unit": "ms",
     "threshold": [900, 1500], "op": "range", "enforced": False, "higher_is_better": False,
     "formula": "min(part.start) − info.time.created (dấu thời gian phía SERVER), lấy p50.",
     "dataset": "perf_bench_100", "dataset_keys": ["perf_bench_100"],
     "depends_on": ["backend chat"], "script": "perf_load.py",
     "note": "KHÔNG PHẢI GATE THẬT — có ví dụ thật ghi 2755ms vẫn hiện 'ĐẠT' dù ngoài dải 900-1500ms."},
    {"code": "R-PERF-RPS", "name": "RPS ổn định", "group": "B", "unit": "req/s",
     "threshold": 0.6, "op": "~", "enforced": False, "higher_is_better": True,
     "formula": "số_request_thành_công / thời_gian_tường (wall clock) của đúng 1 lần chạy.",
     "dataset": "perf_bench_100", "dataset_keys": ["perf_bench_100"],
     "depends_on": ["backend chat"], "script": "perf_load.py",
     "note": "KHÔNG PHẢI GATE THẬT. Từ 2026-08-27 gateway nâng rate-limit /ai/chat lên 90 req/60s "
             "(trước đó 15/60s xung đột cấu trúc với mục tiêu CCU~30 + RPS~0.6)."},
    {"code": "R-PERF-CCU", "name": "CCU đồng thời", "group": "B", "unit": "luồng",
     "threshold": 30, "op": "~", "enforced": "partial", "higher_is_better": True,
     "formula": "Chính là tham số --concurrency truyền vào lệnh bench — KHÔNG phải đại lượng đo được.",
     "dataset": "perf_bench_100", "dataset_keys": ["perf_bench_100"],
     "depends_on": ["backend chat"], "script": "perf_load.py",
     "note": "Ràng buộc thật duy nhất: không request nào lỗi ở mức concurrency đã chọn."},
    {"code": "R-CTRL-RESP15", "name": "Response p50", "group": "B", "unit": "ms",
     "threshold": 15000, "op": "<=", "enforced": True, "higher_is_better": False,
     "formula": "p50 của (completed − created) trên toàn bộ request trong 1 lần chạy.",
     "dataset": "perf_bench_100 (hoặc results.jsonl của lần chạy chất lượng)",
     "dataset_keys": ["perf_bench_100"],
     "depends_on": ["backend chat"], "script": "perf_kpi.py",
     "note": "GATE THẬT — 1 trong 4 KPI nhóm B/C có kiểm ngưỡng thật sự trong code."},

    # ── Nhóm C — Throughput mô hình (3) ──────────────────────────────────────
    {"code": "R-MODEL-EMB1200", "name": "Embedding throughput", "group": "C", "unit": "emb/s",
     "threshold": 1200, "op": ">=", "enforced": True, "higher_is_better": True,
     "formula": "Đỉnh của (số embedding hoàn thành / thời gian) qua thang concurrency 8/16/32 (có warmup).",
     "dataset": "văn bản cố định trong tp_bench.py", "dataset_keys": [],
     "depends_on": ["EMBED_URL"], "script": "tp_bench.py --mode embed",
     "note": "GATE THẬT. Ngưỡng tính trên 1× A100; đo từ máy bench xa (WAN) gồm cả độ trễ mạng → "
             "phải SSH vào máy serving rồi đo tại localhost."},
    {"code": "R-MODEL-RERANK180", "name": "Reranker throughput", "group": "C", "unit": "docs/s",
     "threshold": 180, "op": ">=", "enforced": True, "higher_is_better": True,
     "formula": "Đỉnh của (số doc rerank / thời gian) qua thang concurrency (batch 50).",
     "dataset": "doc cố định trong tp_bench.py", "dataset_keys": [],
     "depends_on": ["RERANK_URL"], "script": "tp_bench.py --mode rerank",
     "note": "GATE THẬT. Cùng lưu ý vị trí đo như R-MODEL-EMB1200."},
    {"code": "R-LLM-TPS28", "name": "LLM throughput (lớp suy luận)", "group": "C", "unit": "tok/s",
     "threshold": 28, "op": ">=", "enforced": True, "higher_is_better": True,
     "formula": "tokens / (e2e − ttft), p50, lọc các luồng có > 20 token. Stream SSE /v1/chat/"
                "completions, max_tokens=512, temperature=0.",
     "dataset": "1 prompt Log4Shell cố định", "dataset_keys": [],
     "depends_on": ["LLM_BASE_URL"], "script": "llm_load.py",
     "note": "GATE THẬT. Đơn vị KHÁC R-PERF-TPS: đếm số chunk SSE, không phải token thật từ server."},
]

_META = {
    "total_kpi": 19,
    "source": "KPI-VA-GEN-TEST/01-KPI/kpi-registry.json (lập 2026-08-26) — nội dung phương pháp "
              "luận (công thức/ngưỡng/dataset), KHÔNG phải số đo. Số đo thật lấy ở nhóm K "
              "(/showcase-api/kpi-bench/*), đọc từ hồ sơ gốc.",
    "kpi_thu_12_khong_nguong": {
        "code": "R-KPI-REASONING", "note": "Có marker suy luận và ≥ 40 từ. Không có ngưỡng riêng — "
                "chỉ là 1 trong 5 trục radar (mục tiêu ngầm 80) nên ảnh hưởng gián tiếp tới "
                "R-KPI-RADAR90. Không tính vào 19 KPI chính thức."},
    "ma_thu_20_mo_coi": {
        "code": "R-CHATFLOW-TPS25", "note": "Có trong một số kpi-summary.json cũ nhưng không script "
                "sống nào tái lập được — chỉ tồn tại trong code mồ côi in cứng '✅ ĐẠT'. Không tính "
                "vào 19 KPI chính thức."},
}


def _threshold_display(kpi: dict) -> str:
    thr, op, unit = kpi["threshold"], kpi["op"], kpi["unit"]
    if op == "range" and isinstance(thr, list):
        return f"{thr[0]}–{thr[1]} {unit}"
    sym = {">": ">", ">=": "≥", "<": "<", "<=": "≤", "=": "=", "~": "~"}.get(op, op)
    return f"{sym} {thr} {unit}"


def _pass_rule(kpi: dict) -> str:
    enforced = kpi["enforced"]
    disp = _threshold_display(kpi)
    if enforced is True:
        return f"Đạt khi giá trị đo {disp} (gate thật — code có so ngưỡng)."
    if enforced == "partial":
        return f"Không có gate cứng trên số {disp} — ràng buộc thật duy nhất là 0 request lỗi."
    return f"Không phải gate thật — {disp} chỉ để hiển thị/đối chiếu, code không ép so ngưỡng này."


@router.get("/catalog")
def get_catalog() -> dict:
    kpis = []
    for k in _KPI:
        kpis.append({**k, "threshold_display": _threshold_display(k), "pass_rule": _pass_rule(k)})
    return {"meta": _META, "groups": GROUPS, "kpis": kpis}


# ─────────────────────────────────────────────────────────────────────────────
# 2) Dataset browser — đọc THẬT từ hồ sơ gốc, phân trang
# ─────────────────────────────────────────────────────────────────────────────

def _root() -> Path:
    return Path(get_settings().showcase_kpi_results_dir)


_DATASETS: dict[str, dict] = {
    "acceptance_dinh_danh": {
        "rel": "Model/Datasets/acceptance_dinh_danh.jsonl", "label": "Định danh (nhóm A)",
        "desc": "400 câu hỏi tra cứu CVE/CWE trực tiếp — bucket dinh_danh của bộ 1000 câu.",
        "used_by": ["R-KPI-EMB96", "R-KPI-RERANK90"],
    },
    "acceptance_khac_phuc": {
        "rel": "Model/Datasets/acceptance_khac_phuc.jsonl", "label": "Khắc phục (nhóm A)",
        "desc": "400 câu yêu cầu hướng khắc phục/remediation — bucket khac_phuc của bộ 1000 câu.",
        "used_by": ["R-KPI-EMB96", "R-KPI-RERANK90"],
    },
    "acceptance_tinh_huong": {
        "rel": "Model/Datasets/acceptance_tinh_huong.jsonl", "label": "Tình huống + câu bẫy (nhóm A)",
        "desc": "200 câu tình huống, gồm các câu bẫy fake_cve dùng để đo Hallucination Rate.",
        "used_by": ["R-KPI-EMB96", "R-KPI-RERANK90", "R-KPI-HALLU2"],
    },
    "sanitization_pii": {
        "rel": "Santization/Dataset/sanitization_pii.jsonl", "label": "PII/Secret sanitization",
        "desc": "500 mẫu sinh runtime (gen_pii_dataset.py --seed 42): 72% positive (có PII/secret "
                "thật) / 28% hard-negative (trông giống nhưng không phải).",
        "used_by": ["R-SEC-PII"],
    },
    "perf_bench_100": {
        "rel": "Perf/perf/perf_bench_100.jsonl", "label": "Perf bench 100 prompt",
        "desc": "100 câu CVE/khắc phục dùng cho toàn bộ nhóm B (TPS/TTFT/RPS/CCU/Response p50), "
                "output tham chiếu ≥ 512 token/câu.",
        "used_by": ["R-PERF-TPS", "R-PERF-TTFT", "R-PERF-RPS", "R-PERF-CCU", "R-CTRL-RESP15"],
    },
}


def _read_jsonl_all(path: Path) -> list[Any]:
    import json as _json
    rows: list[Any] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(_json.loads(line))
    return rows


@router.get("/datasets")
def list_datasets() -> dict:
    out = []
    for key, d in _DATASETS.items():
        path = _root() / d["rel"]
        n = None
        if path.is_file():
            with path.open("r", encoding="utf-8") as f:
                n = sum(1 for line in f if line.strip())
        out.append({"key": key, "label": d["label"], "description": d["desc"], "used_by": d["used_by"],
                    "row_count": n, "source": d["rel"], "available": path.is_file()})
    return {"datasets": out}


@router.get("/datasets/{key}")
def get_dataset_page(key: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200)) -> dict:
    d = _DATASETS.get(key)
    if d is None:
        raise not_found(f"Không có dataset '{key}'. Xem /showcase-api/kpi-studio/datasets để lấy danh sách hợp lệ.")
    path = _root() / d["rel"]
    if not path.is_file():
        raise not_found(f"Không tìm thấy file dataset trong hồ sơ gốc: {path}")
    rows = _read_jsonl_all(path)
    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]
    return {
        "key": key, "label": d["label"], "source": d["rel"], "total_rows": total,
        "page": page, "page_size": page_size, "total_pages": max(1, -(-total // page_size)),
        "rows": page_rows,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3) Service status — kiểm tra THẬT LLM/embedding/reranker/Qdrant có sẵn sàng để bench
# ─────────────────────────────────────────────────────────────────────────────

def _exc_detail(e: Exception) -> str:
    """str(e) is empty for several httpx timeout/connect exceptions (no message attached) —
    fall back to the exception class name so the UI never shows a blank reason."""
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}"[:180] if msg else f"{type(e).__name__} (không có thông điệp — thường là timeout/refused)"


async def _check_llm(s) -> dict:
    provider_kind = s.llm_provider.lower()
    if provider_kind == "deepseek":
        base, key = s.deepseek_base_url.rstrip("/"), s.deepseek_api_key
    else:
        base, key = s.qwen_base_url.rstrip("/"), s.qwen_api_key
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=3.5) as c:
            r = await c.get(base + "/models", headers=headers)
            return {"role": "llm", "url": base, "ok": r.is_success, "http_status": r.status_code,
                    "latency_ms": round((time.monotonic() - t0) * 1000), "detail": None}
    except httpx.HTTPError as e:
        return {"role": "llm", "url": base, "ok": False, "http_status": None,
                "latency_ms": round((time.monotonic() - t0) * 1000), "detail": _exc_detail(e)}


async def _check_embedding(s) -> dict:
    url = s.embedding_api_url.rstrip("/") if s.embedding_api_url else ""
    if not url:
        return {"role": "embedding", "url": None, "ok": False, "detail": "chưa cấu hình EMBEDDING_API_URL"}
    headers = {"Content-Type": "application/json"}
    if s.embedding_api_key:
        headers["Authorization"] = f"Bearer {s.embedding_api_key}"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.post(f"{url}/v1/embeddings", headers=headers,
                              json={"model": s.embedding_model, "input": "kpi-studio-healthcheck"})
            ok = r.is_success
            detail = None if ok else f"HTTP {r.status_code}: model '{s.embedding_model}' có thể chưa " \
                                      f"được serve đúng tên (xem CLAUDE.md — deploy vast phải dùng biến thể fine-tuned)"
            return {"role": "embedding", "url": url, "model": s.embedding_model, "ok": ok,
                    "http_status": r.status_code, "latency_ms": round((time.monotonic() - t0) * 1000),
                    "detail": detail}
    except httpx.HTTPError as e:
        return {"role": "embedding", "url": url, "model": s.embedding_model, "ok": False,
                "latency_ms": round((time.monotonic() - t0) * 1000), "detail": _exc_detail(e)}


async def _check_reranker(s) -> dict:
    url = s.reranker_api_url.rstrip("/") if s.reranker_api_url else ""
    if not url:
        return {"role": "reranker", "url": None, "ok": False, "detail": "chưa cấu hình RERANKER_API_URL"}
    headers = {"Content-Type": "application/json"}
    if s.reranker_api_key:
        headers["Authorization"] = f"Bearer {s.reranker_api_key}"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.post(f"{url}/v1/rerank", headers=headers,
                              json={"model": s.reranker_model, "query": "healthcheck",
                                    "documents": ["kpi-studio healthcheck doc"], "top_n": 1})
            ok = r.is_success
            detail = None if ok else f"HTTP {r.status_code}: model '{s.reranker_model}' có thể chưa " \
                                      f"được serve đúng tên"
            return {"role": "reranker", "url": url, "model": s.reranker_model, "ok": ok,
                    "http_status": r.status_code, "latency_ms": round((time.monotonic() - t0) * 1000),
                    "detail": detail}
    except httpx.HTTPError as e:
        return {"role": "reranker", "url": url, "model": s.reranker_model, "ok": False,
                "latency_ms": round((time.monotonic() - t0) * 1000), "detail": _exc_detail(e)}


async def _check_qdrant(s) -> dict:
    url = s.qdrant_url.rstrip("/")
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=3.5) as c:
            r = await c.get(f"{url}/collections/{s.qdrant_collection}")
            ok = r.is_success
            points = None
            if ok:
                try:
                    points = r.json().get("result", {}).get("points_count")
                except Exception:  # noqa: BLE001
                    pass
            detail = None if ok else f"HTTP {r.status_code}: collection '{s.qdrant_collection}' có thể chưa tồn tại"
            return {"role": "qdrant", "url": url, "collection": s.qdrant_collection, "ok": ok,
                    "http_status": r.status_code, "points_count": points,
                    "latency_ms": round((time.monotonic() - t0) * 1000), "detail": detail}
    except httpx.HTTPError as e:
        return {"role": "qdrant", "url": url, "collection": s.qdrant_collection, "ok": False,
                "latency_ms": round((time.monotonic() - t0) * 1000), "detail": _exc_detail(e)}


@router.get("/service-status")
async def service_status() -> dict:
    """Kiểm tra thật 4 dịch vụ backend cần để bench: LLM, embedding, reranker, Qdrant.

    Embedding/reranker gọi ĐÚNG request thật (POST /v1/embeddings, /v1/rerank với đúng tên
    model cấu hình) — không chỉ ping health — để bắt được lớp lỗi "serving load model mặc
    định thay vì biến thể fine-tuned → tên model lệch → 404" đã ghi trong CLAUDE.md.
    """
    s = get_settings()
    llm, emb, rer, qd = await asyncio.gather(
        _check_llm(s), _check_embedding(s), _check_reranker(s), _check_qdrant(s),
    )
    checks = [llm, emb, rer, qd]
    ready = all(c["ok"] for c in checks)
    return {"ready_to_bench": ready, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "checks": checks}
