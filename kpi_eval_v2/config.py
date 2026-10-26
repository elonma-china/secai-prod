"""Cấu hình dùng chung cho KPI Eval v2 — BỘ ĐÁNH GIÁ MỚI, độc lập hoàn toàn với kpi_bench cũ.

Nguyên tắc:
- KHÔNG hardcode secret. Đọc endpoint/model/key từ file .env THẬT lúc chạy.
- JUDGE và EMBEDDING dùng để chấm phải ĐỘC LẬP với model đang bị đo (chống tự-chấm).
"""
from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent            # .../secai-agent-gateway/kpi_eval_v2
GATEWAY_ROOT = ROOT.parent                                # .../secai-agent-gateway
REPO_SECAI = GATEWAY_ROOT.parent                          # /home/ict/AIEngines/secai

DOCKER_ENV = REPO_SECAI / "secai-docker" / ".env"
MLOPS_ENV = pathlib.Path.home() / ".config" / "secai-mlops" / "mlops.env"


def _parse_env(path: pathlib.Path) -> dict:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


_docker = _parse_env(DOCKER_ENV)
_mlops = _parse_env(MLOPS_ENV)


def _get(*keys: str, default: str = "") -> str:
    for src in (os.environ, _docker, _mlops):
        for k in keys:
            if src.get(k):
                return src[k]
    return default


# ── HỆ THỐNG BỊ ĐO (system under test) ──────────────────────────────────────
GATEWAY_URL = _get("KPI2_GATEWAY_URL", "GATEWAY_URL", default="http://127.0.0.1:8131")
GATEWAY_KEY = _get("ADMIN_API_KEY")

LLM_BASE_URL = _get("QWEN_BASE_URL", default="http://80.188.223.202:13905/v1")
LLM_MODEL = _get("QWEN_MODEL", default="DuyTa/Cyber-F1-AWQ")
LLM_KEY = _get("QWEN_API_KEY")

EMBED_URL = _get("EMBEDDING_API_URL", default="http://80.188.223.202:13717")
EMBED_MODEL = _get("EMBEDDING_MODEL", default="DuyTa/sec-embedding")
EMBED_KEY = _get("EMBEDDING_API_KEY")

RERANK_URL = _get("RERANKER_API_URL", default="http://80.188.223.202:13542")
RERANK_MODEL = _get("RERANKER_MODEL", default="DuyTa/sec-rerank")
RERANK_KEY = _get("RERANKER_API_KEY")

QDRANT_URL = _get("KPI2_QDRANT_URL", "QDRANT_URL", default="http://127.0.0.1:6333")
# Host serving box (dùng cho đo throughput TẠI-HOST của embed/rerank qua SSH).
SERVING_SSH = _get("KPI2_SERVING_SSH", default="")  # ví dụ "root@80.188.223.202 -p <port>"

# ── JUDGE ĐỘC LẬP (khác model bị đo) — dùng cho RAGAS + rubric chấm SAST/Exploit/Remed ──
JUDGE_BASE_URL = _get("JUDGE_BASE_URL", default="https://openrouter.ai/api/v1")
JUDGE_MODEL = _get("JUDGE_MODEL", default="deepseek/deepseek-chat-v3.1")
JUDGE_KEY = _get("JUDGE_API_KEY")

# Embedding ĐỘC LẬP cho RAGAS answer-relevancy — TUYỆT ĐỐI không dùng sec-embedding của hệ bị đo.
# Mặc định: sentence-transformers đa ngữ (chạy offline, deterministic, hỗ trợ tiếng Việt).
RAGAS_EMBED_MODEL = _get(
    "RAGAS_EMBED_MODEL",
    default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)

# ── Đường dẫn ───────────────────────────────────────────────────────────────
DATASETS = ROOT / "datasets"
RESULTS = ROOT / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
# Gold nhóm chất lượng: các bộ acceptance ĐỘC LẬP đã có sẵn.
GOLD_QUALITY_DIR = GATEWAY_ROOT / "kpi_bench" / "02-GEN-TEST" / "testset-v2"

# ── NGƯỠNG KPI: id -> (op, bound, unit, name, group) ────────────────────────
# op: gt >  | ge >= | lt <  | le <= | eq == | range [lo,hi]
THRESHOLDS: dict[str, tuple] = {
    # nhóm chất lượng (quality)
    "R2-FAITH98":    ("gt", 98,  "%",    "Faithfulness (RAGAS)",                 "quality"),
    "R2-RELEV90":    ("gt", 90,  "%",    "Answer Relevancy (RAGAS)",             "quality"),
    "R2-EMB96":      ("gt", 96,  "%",    "Hit Rate@10 (KHÔNG oracle)",           "quality"),
    "R2-RERANK90":   ("gt", 90,  "%",    "Reranker Precision@1 (KHÔNG oracle)",  "quality"),
    "R2-ENTITY100":  ("ge", 100, "%",    "Entity Precision CVE/CWE (fail-closed)", "quality"),
    "R2-HALLU2":     ("lt", 2,   "%",    "Hallucination Rate (fail-closed)",     "quality"),
    "R2-SAST85":     ("ge", 85,  "%",    "SAST (LLM-judge rubric)",              "quality"),
    "R2-EXPLOIT85":  ("ge", 85,  "%",    "Giải thích exploit (rubric)",          "quality"),
    "R2-REMED60":    ("ge", 60,  "%",    "Auto-remediation (rubric)",            "quality"),
    "R2-RADAR90":    ("ge", 90,  "%",    "Radar coverage (clamped, dẫn xuất)",   "quality"),
    # nhóm hiệu năng (performance)
    "R2-RESP15":     ("le", 15000, "ms", "Chatbot response p50",                "performance"),
    "R2-CTX16K":     ("ge", 12000, "tok","Context hiệu quả (mục tiêu 16k)",     "performance"),
    "R2-TPS":        ("range", (20, 35), "tok/s", "Throughput (token thật)",    "performance"),
    "R2-CHATFLOW":   ("range", (20, 35), "tok/s", "Full chatflow (token thật, ~25)", "performance"),
    "R2-TTFT":       ("range", (900, 1500), "ms", "TTFT",                       "performance"),
    "R2-RPS":        ("ge", 0.6, "req/s", "RPS capacity (3xA100)",              "performance"),
    "R2-CCU":        ("ge", 30,  "user",  "CCU (dò trần thật)",                 "performance"),
    "R2-EMB1200":    ("ge", 1200,"emb/s", "Embedding throughput (TẠI-HOST)",    "performance"),
    "R2-RERANK180":  ("ge", 180, "doc/s", "Rerank throughput (TẠI-HOST)",       "performance"),
    "R2-LLMTPS28":   ("ge", 28,  "tok/s", "LLM throughput (token thật)",        "performance"),
    # nhóm PII (recall/precision RIÊNG 4 loại + F1) — gold ĐỘC LẬP
    "R2-PII-EMAIL-R": ("gt", 98, "%", "Email/IP Recall",     "pii"),
    "R2-PII-EMAIL-P": ("gt", 95, "%", "Email/IP Precision",  "pii"),
    "R2-PII-PHONE-R": ("gt", 95, "%", "SĐT Recall",          "pii"),
    "R2-PII-PHONE-P": ("gt", 90, "%", "SĐT Precision",       "pii"),
    "R2-PII-KEY-R":   ("gt", 85, "%", "API Key Recall",      "pii"),
    "R2-PII-KEY-P":   ("gt", 80, "%", "API Key Precision",   "pii"),
    "R2-PII-PWD-R":   ("gt", 80, "%", "Password Recall",     "pii"),
    "R2-PII-PWD-P":   ("gt", 75, "%", "Password Precision",  "pii"),
    "R2-PII-F1":      ("gt", 90, "%", "PII F1 tổng thể",     "pii"),
}
