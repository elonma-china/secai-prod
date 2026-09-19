"""Cấu hình KPI eval — model-agnostic, đọc từ env. Stdlib-only (chạy cả trên VPS không pip).

Đổi model nghiệm thu (vd sang Qwen) chỉ cần set:
  LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
Các endpoint truy hồi mặc định trỏ loopback VPS (embedding :8010, reranker :8011, qdrant :6333).
"""
import os

# ---- LLM agent đang đánh giá (model under test). Mặc định DeepSeek (OpenAI-compatible).
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

# ---- LLM-judge cho RAGAS faithfulness — ĐỘC LẬP với model under test (tránh self-eval bias).
# Mặc định gpt-4o-mini (rẻ, ổn định, đa ngữ). Đổi judge: set RAGAS_JUDGE_*.
RAGAS_JUDGE_MODEL = os.environ.get("RAGAS_JUDGE_MODEL", "gpt-4o-mini")
RAGAS_JUDGE_BASE_URL = os.environ.get("RAGAS_JUDGE_BASE_URL", "https://api.openai.com/v1")
RAGAS_JUDGE_API_KEY = os.environ.get("RAGAS_JUDGE_API_KEY") or os.environ.get("OPENAI_API_KEY", "")

# ---- Embedding (bge-m3, 1024-dim) — PHẢI khớp model đã index Qdrant.
EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:8010").rstrip("/")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")

# ---- Reranker (Qwen3-Reranker-0.6B).
RERANK_URL = os.environ.get("RERANK_URL", "http://localhost:8011").rstrip("/")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "Qwen/Qwen3-Reranker-0.6B")

# ---- API key cho embed/rerank endpoint (vLLM --api-key). Mặc định dùng chung key với LLM.
# Endpoint bge-m3/reranker yêu cầu Bearer → thiếu key = 401 (retrieval KPI về 0).
EMBED_KEY = os.environ.get("EMBED_KEY") or os.environ.get("EMBEDDING_API_KEY") or LLM_API_KEY
RERANK_KEY = os.environ.get("RERANK_KEY") or os.environ.get("RERANKER_API_KEY") or LLM_API_KEY

# ---- Qdrant.
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "cve_kb")

# ---- Ngưỡng KPI hợp đồng (task-AI-requirements.md) — dùng để chấm pass/fail trong report.
THRESHOLDS = {
    "R-KPI-EMB96": (">", 96.0),       # Embedding Hit Rate@10 (%)
    "R-KPI-RERANK90": (">", 90.0),    # Reranking Precision@1 (%)
    "R-KPI-FAITH98": (">", 98.0),     # RAGAS Faithfulness (%)
    "R-KPI-RELEV90": (">", 90.0),     # Answer Relevancy (%)
    "R-KPI-ENTITY100": (">=", 100.0), # Entity Precision CVE/CWE (%)
    "R-KPI-HALLU2": ("<", 2.0),       # Hallucination Rate (%)
    "R-LLM-SAST85": (">=", 85.0),     # SAST accuracy (%)
    "R-LLM-EXPLOIT85": (">=", 85.0),  # Exploit explanation accuracy (%)
    "R-LLM-REMED60": (">=", 60.0),    # Auto-remediation accuracy (%)
    "R-KPI-RADAR90": (">", 90.0),     # Radar coverage area (%)
    "R-SEC-PII": (">", 90.0),         # PII/Secret F1 (%)
    "R-PERF-TTFT": ("range", (900.0, 1500.0)),  # TTFT (ms)
    "R-PERF-TPS": ("range", (20.0, 35.0)),      # Throughput (tokens/s)
    "R-CTRL-RESP15": ("<=", 15000.0), # Response time (ms)
}


def check_threshold(code, value):
    """True nếu value đạt ngưỡng KPI code; None nếu không có rule."""
    rule = THRESHOLDS.get(code)
    if not rule:
        return None
    op, target = rule
    if op == ">":
        return value > target
    if op == ">=":
        return value >= target
    if op == "<":
        return value < target
    if op == "<=":
        return value <= target
    if op == "range":
        return target[0] <= value <= target[1]
    return None
