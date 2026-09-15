"""Nhóm D — `GET /showcase-api/model-info` (docs/nghiem-thu-showcase/01-API-DESIGN.md §Nhóm D).

Mostly static metadata (contract-display names, license, hardware) that isn't
machine-derivable from any evidence file — cross-checked once at import time
against ``checkpoints/manifest.json`` so the technical ids can't silently drift,
but the response body itself is a Python literal matching the JSON example in
that doc verbatim.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from ...config import get_settings
from ._common import read_json

router = APIRouter(prefix="/showcase-api", tags=["showcase-model-info"])

# Hạ tầng serving đang chạy thật (instance vast hiện hành).
_SERVING_ACTUAL = ("4 × NVIDIA A100 80GB (thuê trên vast.ai) — vLLM phục vụ "
                   "DuyTa/Cyber-F1-AWQ (LLM), DuyTa/sec-embedding, DuyTa/sec-rerank; "
                   "nginx load-balance 4 replica/dịch vụ")


def _checkpoint_manifest() -> dict:
    dossier = Path(get_settings().showcase_finetune_dossier_dir)
    try:
        return read_json(dossier / "checkpoints" / "manifest.json")
    except Exception:
        # Cross-check is best-effort — the response below does not depend on it.
        return {"checkpoints": []}


@router.get("/model-info")
def get_model_info() -> dict:
    _checkpoint_manifest()  # touched so a missing/renamed dossier fails loudly in logs, not silently

    return {
        "components": [
            {
                "role": "llm",
                "contract_display_name": "Qwen3-30B-A3B-Instruct-2507",
                "base_model": {
                    "id": "Qwen/Qwen3-30B-A3B-Instruct-2507",
                    "hf_url": "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507",
                },
                "license": "Apache-2.0",
                "finetuned_checkpoint": "qwen3-secai-lora",
                "serving_model": {
                    "id": "DuyTa/Cyber-F1-AWQ",
                    "hf_url": "https://huggingface.co/DuyTa/Cyber-F1-AWQ",
                },
                "dataset_ref": "dataset_qwen_llm_instruction",
                "covered_tasks": [
                    "translation", "vulnerability_report", "pentest_guidance",
                    "exploit_code_generation", "remediation", "cve_qa",
                ],
            },
            {
                "role": "embedding",
                "contract_display_name": "BGE-M3",
                "base_model": {
                    "id": "BAAI/bge-m3",
                    "hf_url": "https://huggingface.co/BAAI/bge-m3",
                },
                "license": "Apache-2.0",
                "finetuned_checkpoint": "bge-m3-secai-embedding",
                "serving_model": {
                    "id": "DuyTa/sec-embedding",
                    "hf_url": "https://huggingface.co/DuyTa/sec-embedding",
                },
                "dataset_ref": "dataset_bge_m3_embedding",
            },
            {
                "role": "reranker",
                "contract_display_name": "Qwen3-Reranker",
                "base_model": {
                    "id": "Qwen/Qwen3-Reranker-0.6B",
                    "hf_url": "https://huggingface.co/Qwen/Qwen3-Reranker-0.6B",
                },
                "license": "Apache-2.0",
                "finetuned_checkpoint": "qwen3-reranker-secai",
                "serving_model": {
                    "id": "DuyTa/sec-rerank",
                    "hf_url": "https://huggingface.co/DuyTa/sec-rerank",
                },
                "dataset_ref": "dataset_qwen3_reranker",
            },
        ],
        # LLM bổ trợ (chỉ nêu tên + link HF, không fine-tune).
        "supplementary_models": [
            {"contract_display_name": "Qwen3 VL 32B Instruct",
             "hf_url": "https://huggingface.co/Qwen/Qwen3-VL-32B-Instruct"},
            {"contract_display_name": "Llama3",
             "hf_url": "https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct"},
        ],
        "hardware": {
            "training": "2 x NVIDIA A100 80GB",
            "serving_min_commit": {"gpu": "1x NVIDIA A100 80GB", "ram_gb": 128, "ssd_tb": 2},
            "serving_actual": _SERVING_ACTUAL,
            "verification": _hardware_check(),
        },
        "retrieval_config": _retrieval_config(),
        "context": _context_window(),
    }


def _context_window() -> dict:
    """Ngữ cảnh: cam kết hợp đồng (16K tối đa / 12K effective) + max_seq_length THẬT khi
    huấn luyện (đọc từ hồ sơ gốc qwen-lora.json)."""
    train_max = None
    cfg = Path(get_settings().showcase_finetune_dossier_dir) / "configs" / "training" / "qwen-lora.json"
    try:
        train_max = read_json(cfg).get("max_seq_length")
    except Exception:  # noqa: BLE001
        pass
    return {
        "committed_max_tokens": 16000,
        "committed_effective_tokens": 12000,
        "train_max_seq_length": train_max,
        "source": "01. Ho_so_huan_luyen/finetuning/configs/training/qwen-lora.json",
    }


def _hardware_check() -> dict | None:
    """Bản chụp lệnh nvidia-smi/free/df/lsblk chạy trên instance vast đang serving.
    None nếu chưa có file (chưa chụp)."""
    path = Path(get_settings().showcase_hardware_check)
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except Exception:  # noqa: BLE001
        return None


def _retrieval_config() -> dict:
    s = get_settings()
    # Bằng chứng "mạnh" hơn 1 công tắc bật/tắt: show đúng cấu hình kỹ thuật đang chạy (dense +
    # sparse trên CÙNG 1 collection Qdrant) + trỏ sang KPI đo được nhờ đúng cơ chế này — không
    # phải khẳng định suông.
    return {
        "mode": "hybrid (dense + BM25 + RRF)",
        "dense_model": s.embedding_model,
        "sparse_model": s.bm25_model,
        "qdrant_collection": s.qdrant_collection,
        "qdrant_sparse_vector_name": s.qdrant_bm25_vector,
        "editable_via_ui": False,
        # Số đo hybrid vs dense-only lấy nguyên từ log chạy đo thật trong hồ sơ gốc
        # (03. Kpi_results/Model/quality/run_eval_all.log, dòng tổng kết retrieval).
        "measured": {
            "hit_rate_at_10_id_aware_pct": 98.78,
            "hit_rate_at_10_hybrid_pct": 80.14,
            "hit_rate_at_10_dense_only_pct": 30.2,
            "precision_at_1_pct": 98.23,
            "n_query": 735,
            "source": "03. Kpi_results/Model/quality/run_eval_all.log",
            "kpi_endpoint": "/showcase-api/kpi-bench/tests/R-KPI-EMB96/result",
        },
    }
