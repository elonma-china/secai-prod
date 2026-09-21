"""Shim `config` cho kpi_bench/scripts/retrieval_kpi.py (nó `import config`).

retrieval_kpi.py của gateway đọc endpoint qua module `config`; ở đây ta cấp bản đọc-từ-env để
GATE mlops chạy đúng bộ đo SẢN PHẨM (hybrid dense+BM25 native Qdrant + id-aware + rerank) trỏ vào
endpoint PRODUCTION (stage 3 detection) hoặc CANDIDATE (stage 9 requalify) — không sửa script gốc.
Đặt thư mục này lên PYTHONPATH khi gọi retrieval_kpi.py.
"""
import os

EMBED_URL = os.environ["EMBED_URL"].rstrip("/")
EMBED_KEY = os.environ.get("EMBED_KEY", "")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "DuyTa/sec-embedding")

RERANK_URL = os.environ["RERANK_URL"].rstrip("/")
RERANK_KEY = os.environ.get("RERANK_KEY", "")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "DuyTa/sec-rerank")

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "cve_kb_hybrid")

# ── LLM-judge ĐỘC LẬP (Phase 2: dùng cho answer-relevance của llm_kpi.py) — KHÔNG phải model
# đang đo. Trỏ vào judge (vd OpenRouter deepseek) để tránh self-eval bias. faith_judge.py đọc
# RAGAS_JUDGE_*/OPENAI_API_KEY riêng qua env.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
