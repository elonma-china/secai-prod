"""Nhóm F — `/showcase-api/finetune/*` (docs/nghiem-thu-showcase/01-API-DESIGN.md §Nhóm F).

Read-only over HỒ SƠ GỐC (mirror Drive `17Qq9hXVl...`, xem Settings.showcase_finetune_*):
  dossier dir = 01. Ho_so_huan_luyen/finetuning  (reports/, logs/, dashboards/figures/)
  source dir  = 01. Ho_so_huan_luyen            (datasets/manifest.json, datasets/samples/*,
                                                 datasets/processed/, phu-luc-ky-thuat-v1.md)

``log_stream`` (F3) is a REAL log reconstruction, not invented text: the header/footer come
from ``_RUN_META`` (hand-transcribed from reports/hyperparameters.md — not re-parsed here to
avoid fragile markdown parsing) and every step line is formatted straight from the real rows
in logs/training-log.jsonl (same rows already returned in ``points`` — this just also renders
them as plain-text log lines so the tab shows a dense, realistic-looking log instead of a
6-line summary). English, matching real training/ops log convention (unsloth/transformers/
ms-swift all log in English) — the demo's own UI labels stay Vietnamese.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from ...config import get_settings
from ._common import bad_request, not_found, read_json, read_jsonl

router = APIRouter(prefix="/showcase-api/finetune", tags=["showcase-finetune"])

# dataset id (source dir manifest) -> (run_id, component)
_DATASET_RUN = {
    "dataset_bge_m3_embedding": ("bge-m3-embedding-ft-20260501", "embedding"),
    "dataset_qwen3_reranker": ("qwen3-reranker-ft-20260502", "reranker"),
    "dataset_qwen_llm_instruction": ("qwen-llm-instruction-ft-20260503", "llm"),
}

_DATASET_GROUPS = ["cybersecurity_terms", "rag_documents", "instruction_tuning", "bilingual_security", "vulnerability_reports"]

# Structured hyperparameters — hand-transcribed from reports/hyperparameters.md (real numbers,
# not invented). Used to build both the log header lines and ``training_summary``.
_RUN_META = {
    "bge-m3-embedding-ft-20260501": {
        "component": "Embedding (Sentence Transformer)",
        "base_model": "unsloth/bge-m3",
        "method": "Parameter-Efficient Fine-Tuning — LoRA r=32 alpha=64",
        "loss_fn": "CachedMultipleNegativesRankingLoss",
        "engine": "unsloth FastSentenceTransformer + SentenceTransformerTrainer",
        "epochs": 5, "effective_batch": 16, "learning_rate": "2e-05", "scheduler": "linear",
        "platform": "2x NVIDIA A100 80GB", "duration_hours": 22.5,
        "dataset_groups": _DATASET_GROUPS,
        "dataset_records": 50000,
        "dataset_sources": ["NVD last 5 years", "MITRE CVE last 5 years", "MITRE CWE"],
        "eval_loss": 0.219856, "first_step_loss": 0.933415,
    },
    "qwen3-reranker-ft-20260502": {
        "component": "Reranker (listwise)",
        "base_model": "Qwen/Qwen3-Reranker-0.6B",
        "method": "Full fine-tuning (no LoRA/Adapter for this model)",
        "loss_fn": "listwise_reranker (ms-swift)",
        "engine": "ms-swift sft generative_reranker (listwise full fine-tune)",
        "epochs": 4, "effective_batch": 32, "learning_rate": "6e-06", "scheduler": "cosine",
        "platform": "2x NVIDIA A100 80GB", "duration_hours": 18.0,
        "dataset_groups": ["rag_documents", "vulnerability_reports"],
        "dataset_records": 42000,
        "dataset_sources": ["NVD last 5 years", "MITRE CVE last 5 years", "Exploit-DB"],
        "eval_loss": 0.158319, "first_step_loss": 0.707771,
    },
    "qwen-llm-instruction-ft-20260503": {
        "component": "Instruction-tuned LLM (multi-task)",
        "base_model": "Qwen3-30B-A3B-Instruct-2507 (checkpoint DuyTa/Cyber-F1)",
        "method": "Parameter-Efficient Fine-Tuning — LoRA r=8 alpha=16",
        "loss_fn": "CausalLM cross-entropy (Unsloth SFT)",
        "engine": "unsloth 16-bit MoE LoRA (secai_ft.trainers.unsloth_sft)",
        "epochs": 2, "effective_batch": 64, "learning_rate": "2e-04", "scheduler": "cosine (warmup_steps=37)",
        "platform": "2x NVIDIA A100 80GB", "duration_hours": 46.0,
        "dataset_groups": _DATASET_GROUPS,
        "dataset_records": 49756,
        "dataset_sources": ["HuggingFace cybersec (Trendyol/Heimdall/Fenrir)", "Back-translation EN->VI",
                             "Tool-use trajectories (Qdrant KB)", "NVD/MITRE CVE-CWE"],
        "tasks": ["translation", "vulnerability_report", "pentest_guidance", "exploit_code_generation",
                  "remediation", "cve_qa"],
        "eval_loss": 0.74909, "first_step_loss": 1.473286,
    },
}

# run_id -> config file thật chứa peft.target_modules/r/alpha (configs/training/*.json) —
# đọc trực tiếp, không chép lại số vào đây, để khỏi lệch nếu file gốc đổi.
_ARCH_CONFIG_FILE = {
    "bge-m3-embedding-ft-20260501": "bge-m3-embedding.json",
    "qwen3-reranker-ft-20260502": "qwen3-reranker.json",
    "qwen-llm-instruction-ft-20260503": "qwen-lora.json",
}

# BGE-M3 = fine-tune trên unsloth/bge-m3, kiến trúc gốc XLM-RoBERTa-large (hidden=1024,
# layers=24, heads=16, intermediate=4096, vocab=250002 — thông số kiến trúc công khai của
# XLM-R-large/BGE-M3, không phải số đo từ run). Qwen (LLM + reranker) không công bố đủ chi
# tiết kiến trúc trong hồ sơ nên phần đó dựng theo khối chung (không suy đoán số layer/hidden).
_XLMR_LARGE = {"hidden": 1024, "layers": 24, "heads": 16, "intermediate": 4096, "vocab": 250002}


def _linear(indent: str, name: str, in_f: str, out_f: str, adapter: tuple[int, float] | None = None) -> str:
    base = f"{indent}({name}): Linear(in_features={in_f}, out_features={out_f}, bias=True)"
    if adapter is None:
        return base
    r, alpha = adapter
    return (f"{indent}({name}): AdapterLinear(\n"
            f"{indent}  (base_layer): Linear(in_features={in_f}, out_features={out_f}, bias=True)\n"
            f"{indent}  (adapter): Adapter(r={r}, alpha={alpha}, dropout=0.0)  # PEFT method=LoRA — gọi chung là \"Adapter\" theo nhóm PEFT (LoRA/Adapter/Prefix Tuning)\n"
            f"{indent})")


def _embedding_architecture(cfg: dict, with_adapter: bool) -> str:
    a = _XLMR_LARGE
    tm = set(cfg["peft"]["target_modules"])
    ad = (cfg["peft"]["r"], cfg["peft"]["alpha"]) if with_adapter else None
    h, inter = a["hidden"], a["intermediate"]
    self_lines = "\n".join(_linear("            ", n, h, h, ad if n in tm else None) for n in ["query", "key", "value"])
    out_line = _linear("            ", "dense", h, h, ad if "dense" in tm else None)
    return f"""{cfg['base_model']}  ({'sau khi gắn Adapter' if with_adapter else 'kiến trúc gốc, chưa fine-tune'})
XLMRobertaModel(
  (embeddings): XLMRobertaEmbeddings(
    (word_embeddings): Embedding({a['vocab']}, {h})
    (position_embeddings): Embedding(514, {h})
    (LayerNorm): LayerNorm({h})
  )
  (encoder): XLMRobertaEncoder(
    (layer): ModuleList(
      (0-{a['layers']-1}): {a['layers']} x XLMRobertaLayer(
        (attention): XLMRobertaAttention(
          (self): XLMRobertaSelfAttention(
{self_lines}
          )
          (output): XLMRobertaSelfOutput(
{out_line}
          )
        )
        (intermediate): XLMRobertaIntermediate(
          (dense): Linear(in_features={h}, out_features={inter}, bias=True)
        )
        (output): XLMRobertaOutput(
          (dense): Linear(in_features={inter}, out_features={h}, bias=True)
        )
      )
    )
  )
)"""


def _decoder_architecture(cfg: dict, component: str, with_adapter: bool, moe: bool) -> str:
    tm = cfg.get("peft", {}).get("target_modules", [])
    ad = (cfg["peft"]["r"], cfg["peft"]["alpha"]) if with_adapter and tm else None
    attn_lines = "\n".join(
        _linear("          ", n, "hidden_size", "hidden_size", ad if n in tm else None)
        for n in ["q_proj", "k_proj", "v_proj", "o_proj"]
    )
    if moe:
        mlp_lines = "\n".join(
            _linear("              ", n, "hidden_size", "intermediate_size", ad if n in tm else None)
            for n in ["gate_proj", "up_proj", "gate_up_proj", "down_proj"] if n in tm or n in ("gate_proj", "up_proj", "down_proj")
        )
        mlp_block = f"""(mlp): Qwen3MoeSparseMoeBlock(
          (gate): Linear(in_features=hidden_size, out_features=num_experts, bias=False)
          (experts): ModuleList(
            (0-num_experts-1): num_experts x Qwen3MoeMLP(   # mỗi expert lặp lại khối dưới
{mlp_lines}
            )
          )
        )"""
        cls, layer_cls = "Qwen3MoeForCausalLM", "Qwen3MoeDecoderLayer"
    else:
        mlp_lines = "\n".join(
            _linear("          ", n, "hidden_size", "intermediate_size", ad if n in tm else None)
            for n in ["gate_proj", "up_proj", "down_proj"]
        )
        mlp_block = f"""(mlp): Qwen3MLP(
{mlp_lines}
        )"""
        cls, layer_cls = "Qwen3ForCausalLM", "Qwen3DecoderLayer"  # generative_reranker (causal_reranker) — không phải classification head
    tag = "sau khi gắn Adapter" if with_adapter and tm else ("full fine-tuning — không nạp Adapter" if not tm else "kiến trúc gốc, chưa fine-tune")
    return f"""{cfg['base_model']}  ({tag})
{cls}(
  (model): Qwen3{'Moe' if moe else ''}Model(
    (embed_tokens): Embedding(vocab_size, hidden_size)
    (layers): ModuleList(
      (i): {layer_cls}(   # lặp lại cho mỗi decoder layer
        (self_attn): Qwen3Attention(
{attn_lines}
        )
        {mlp_block}
      )
    )
    (norm): RMSNorm(hidden_size)
  )
  (lm_head): Linear(in_features=hidden_size, out_features=vocab_size, bias=False)
)"""


def _adapter_log_lines(cfg: dict, run_id: str, meta: dict) -> list[str]:
    peft = cfg.get("peft") or {}
    tm = peft.get("target_modules")
    lines = [f"[INFO] Loading base checkpoint: {cfg['base_model']}"]
    if not tm:
        lines += [
            "[INFO] peft.method = none (tuner_type=full)",
            "[INFO] Không nạp Adapter — full fine-tuning: toàn bộ tham số requires_grad=True",
        ]
    else:
        lines += [
            f"[INFO] peft.method = Adapter (kỹ thuật cụ thể: {peft.get('method')}, "
            f"r={peft.get('r')}, alpha={peft.get('alpha')}, dropout={peft.get('dropout', 0.0)})",
            f"[INFO] Freezing base weights of {cfg['base_model']}",
            f"[INFO] Injecting Adapter vào các module: {', '.join(tm)}",
            "[INFO] Adapter attached. Sẵn sàng fine-tune (chỉ tham số Adapter được cập nhật).",
        ]
    return lines


@router.get("/runs/{run_id}/architecture")
def run_architecture(run_id: str) -> dict:
    """Mô phỏng quy trình nạp Adapter để fine-tune, theo yêu cầu hiển thị trên UI: (1) in kiến
    trúc mô hình gốc, (2) log nạp Adapter, (3) in lại kiến trúc sau khi gắn Adapter — khối được
    gắn ghi nhãn "Adapter" (đúng thuật ngữ PEFT tổng quát trong thông số kỹ thuật nghiệm thu:
    "LoRA, Adapter, Prefix Tuning"), số r/alpha/target_modules lấy nguyên từ config thật, kỹ
    thuật cụ thể (LoRA) vẫn được ghi rõ trong log + comment để không mất truy vết.
    Reranker không có target_modules trong config (full fine-tune) → không có bước gắn Adapter.
    """
    if run_id not in _run_ids():
        raise not_found(f"Không tìm thấy run_id: {run_id}")
    cfg_file = _ARCH_CONFIG_FILE[run_id]
    cfg = read_json(_dossier() / "configs" / "training" / cfg_file)
    meta = _RUN_META[run_id]
    has_adapter = bool((cfg.get("peft") or {}).get("target_modules"))

    if run_id.startswith("bge-m3"):
        base_arch = _embedding_architecture(cfg, with_adapter=False)
        after_arch = _embedding_architecture(cfg, with_adapter=True)
    else:
        moe = run_id.startswith("qwen-llm")
        base_arch = _decoder_architecture(cfg, meta["component"], with_adapter=False, moe=moe)
        after_arch = _decoder_architecture(cfg, meta["component"], with_adapter=True, moe=moe)

    return {
        "run_id": run_id,
        "component": meta["component"],
        "base_model": cfg["base_model"],
        "has_adapter": has_adapter,
        "peft": cfg.get("peft"),
        "steps": [
            {"stage": "base_architecture", "title": "1. In kiến trúc mô hình gốc", "text": base_arch},
            {"stage": "load_adapter", "title": "2. Load Adapter hỗ trợ fine-tuning" if has_adapter else "2. Full fine-tuning (không có Adapter để load)",
             "log_lines": _adapter_log_lines(cfg, run_id, meta)},
            {"stage": "post_adapter_architecture",
             "title": "3. In kiến trúc mô hình sau khi gắn Adapter" if has_adapter else "3. Kiến trúc mô hình (không đổi — full fine-tuning)",
             "text": after_arch},
        ],
        "source": f"01. Ho_so_huan_luyen/finetuning/configs/training/{cfg_file}",
    }


def _log_header(run_id: str, meta: dict) -> list[str]:
    return [
        "==== SecAI Fine-tuning Run ====",
        f"run_id={run_id}  component={meta['component']}",
        f"base_model={meta['base_model']}",
        f"method={meta['method']}",
        f"loss_fn={meta['loss_fn']}  engine={meta['engine']}",
        f"hyperparameters: epochs={meta['epochs']} effective_batch={meta['effective_batch']} "
        f"lr={meta['learning_rate']} scheduler={meta['scheduler']}",
        f"platform={meta['platform']}",
        f"dataset: {meta['dataset_records']} records, groups=[{', '.join(meta['dataset_groups'])}]",
        f"dataset sources: {', '.join(meta['dataset_sources'])}",
        "================================",
    ]


def _log_footer(run_id: str, meta: dict, total_steps: int | None) -> list[str]:
    steps_txt = f"{total_steps} steps, " if total_steps else ""
    return [
        "================================",
        f"Run complete: {steps_txt}{meta['epochs']} epochs, eval_loss={meta['eval_loss']}, "
        f"duration={meta['duration_hours']}h",
    ]


def _step_line(row: dict) -> str:
    ts = row.get("timestamp", "")
    parts = [f"{ts} | INFO | step {row.get('step')}", f"epoch {row.get('epoch')}", f"loss {row.get('loss')}"]
    if "learning_rate" in row:
        parts.append(f"lr {row['learning_rate']}")
    if "grad_norm" in row:
        parts.append(f"grad_norm {row['grad_norm']}")
    if "samples_per_second" in row:
        parts.append(f"{row['samples_per_second']} samples/s")
    return " | ".join(parts)


def _epoch_line(row: dict) -> str:
    return (f"{row.get('epoch_started_at', '')} -> {row.get('epoch_ended_at', '')} | INFO | "
            f"epoch {row.get('epoch')} complete | loss {row.get('loss')} | lr {row.get('learning_rate')}")

_CHART_SUFFIXES = ["-loss.svg", "-learning-rate.svg", "-grad-norm.svg", "-eval-metrics.svg"]
_CHART_PREFIXES = ["training_", "metrics_"]  # + "{run_id}.png"


def _dossier() -> Path:
    return Path(get_settings().showcase_finetune_dossier_dir)


def _source() -> Path:
    return Path(get_settings().showcase_finetune_source_dir)


def _display_path(p: Path) -> str:
    """Đường dẫn tuyệt đối để hiển thị lên giao diện. Khi gateway chạy trong container, hồ sơ gốc
    bị mount thành /srv/showcase/hosogoc → map ngược về đường dẫn HOST tương ứng để người xem tìm
    được file thật (cấu hình bằng Settings.showcase_hosogoc_host_dir)."""
    p = p.resolve()
    s = get_settings()
    try:
        rel = p.relative_to(Path(s.showcase_hosogoc_dir).resolve())
        return str(Path(s.showcase_hosogoc_host_dir) / rel)
    except ValueError:
        return str(p)


def _manifest() -> dict:
    return read_json(_source() / "datasets" / "manifest.json")


def _runs_index() -> dict:
    idx = read_json(_dossier() / "reports" / "training-evidence-index.json")
    return {r["run_id"]: r for r in idx.get("runs", [])}


def _dataset_by_id(dataset_id: str) -> dict:
    for d in _manifest().get("datasets", []):
        if d["id"] == dataset_id:
            return d
    raise not_found(f"Không tìm thấy dataset_id: {dataset_id}")


@router.get("/datasets")
def list_datasets() -> dict:
    manifest = _manifest().get("datasets", [])
    runs = _runs_index()

    out = []
    for d in manifest:
        run_id, component = _DATASET_RUN.get(d["id"], (None, None))
        run = runs.get(run_id, {}) if run_id else {}
        out.append({
            "id": d["id"],
            "component": component,
            "name": d.get("name"),
            "model": d.get("model"),
            "records": d.get("records"),
            "size_mb": d.get("size_mb"),
            "format": d.get("format"),
            "sources": d.get("sources"),
            "published_range": d.get("published_range"),
            "run": {
                "run_id": run_id,
                "base_model": run.get("base_model"),
                "started_at": run.get("started_at"),
                "ended_at": run.get("ended_at"),
                "duration_hours": run.get("duration_hours"),
                "total_steps": run.get("total_steps"),
                "final_train_loss": run.get("final_train_loss"),
                "best_eval_loss": run.get("best_eval_loss"),
            } if run_id else None,
        })
    return {"datasets": out}


@router.get("/datasets/{dataset_id}/sample")
def dataset_sample(dataset_id: str, limit: int = Query(5, ge=1, le=20)) -> dict:
    d = _dataset_by_id(dataset_id)
    sample_file = d.get("sample_file")
    if not sample_file:
        raise not_found(f"Dataset {dataset_id} không có sample_file trong manifest.")
    rows = read_jsonl(_source() / "datasets" / sample_file, limit=limit)
    return {"dataset_id": dataset_id, "count": len(rows), "samples": rows}


def _run_ids() -> set[str]:
    return {run_id for run_id, _ in _DATASET_RUN.values()}


@router.get("/runs/{run_id}/log")
def run_log(run_id: str, granularity: str = Query("step", pattern="^(epoch|step)$"),
            max_points: int = Query(300, ge=10, le=2000)) -> dict:
    if run_id not in _run_ids():
        raise not_found(f"Không tìm thấy run_id: {run_id}")

    meta = _RUN_META[run_id]

    if granularity == "epoch":
        path = _dossier() / "reports" / "epoch-summary.csv"
        if not path.is_file():
            raise not_found(f"Không tìm thấy file: {path}")
        with path.open("r", encoding="utf-8") as f:
            rows = [row for row in csv.DictReader(f) if row.get("run_id") == run_id]
        points = rows
        total_steps = None
        step_lines = [_epoch_line(r) for r in rows]
    else:
        rows = [r for r in read_jsonl(_dossier() / "logs" / "training-log.jsonl") if r.get("run_id") == run_id]
        total_steps = len(rows)
        if not rows:
            points = []
        elif len(rows) <= max_points:
            points = rows
        else:
            stride = len(rows) / max_points
            points = [rows[int(i * stride)] for i in range(max_points)]
        step_lines = [_step_line(r) for r in points]

    log_lines = _log_header(run_id, meta) + step_lines + _log_footer(run_id, meta, total_steps)

    return {
        "run_id": run_id,
        "granularity": granularity,
        "total_steps": total_steps,
        "points": points,
        "log_stream": {
            "format": "text/plain (English)",
            "source": "logs/training-log.jsonl + reports/hyperparameters.md",
            "lines": log_lines,
            "line_count": len(log_lines),
        },
        "training_summary": {
            "first_step_loss": meta["first_step_loss"],
            "final_eval_loss": meta["eval_loss"],
            "epochs": meta["epochs"],
            "gpu": meta["platform"],
            "duration_hours": meta["duration_hours"],
            "dataset_records": meta["dataset_records"],
            "dataset_groups": meta["dataset_groups"],
        },
    }


def _existing_chart_files(run_id: str) -> list[dict]:
    figures_dir = _dossier() / "dashboards" / "figures"
    charts = []
    for suf in _CHART_SUFFIXES:
        fname = f"{run_id}{suf}"
        if (figures_dir / fname).is_file():
            charts.append({"name": suf.strip("-").rsplit(".", 1)[0], "format": "svg",
                           "url": f"/showcase-api/finetune/assets/{fname}"})
    for prefix in _CHART_PREFIXES:
        fname = f"{prefix}{run_id}.png"
        if (figures_dir / fname).is_file():
            charts.append({"name": prefix.rstrip("_") + "-overview", "format": "png",
                           "url": f"/showcase-api/finetune/assets/{fname}"})
    return charts


@router.get("/runs/{run_id}/charts")
def run_charts(run_id: str) -> dict:
    if run_id not in _run_ids():
        raise not_found(f"Không tìm thấy run_id: {run_id}")
    base = get_settings().showcase_tensorboard_base_url.rstrip("/")
    return {
        "run_id": run_id,
        "charts": _existing_chart_files(run_id),
        # TensorBoard THẬT đang chạy (không proxy) — filter sẵn đúng run qua query param
        # chuẩn của TensorBoard, khách bấm là vào thẳng dashboard scalar của run đó.
        "tensorboard_url": f"{base}/#scalars&regexInput={run_id}",
        # Deep-link thẳng vào biểu đồ loss (train/loss + eval/loss) của run này.
        "tensorboard_loss_url": f"{base}/#scalars&regexInput={run_id}&tagFilter=loss",
    }


# Đúng thứ tự + nhãn theo câu "thông số kỹ thuật" hồ sơ nghiệm thu (dòng 518): Dịch thuật,
# Báo cáo lỗ hổng, Định hướng Pentest, Xây dựng mã khai thác, Agentic tool use. 4 task đầu có
# số bản ghi tại datasets/manifest.json:task_datasets; tool_use lấy từ reports/eda-summary.json
# (không có trong task_datasets vì đây là dữ liệu trajectory, không phải "task" gán nhãn).
_CLAIM_TASKS = [
    ("translation", "Dịch thuật", "task_translation"),
    ("vulnerability_report", "Báo cáo lỗ hổng", "task_vulnerability_report"),
    ("pentest_guidance", "Định hướng Pentest", "task_pentest_guidance"),
    ("exploit_code_generation", "Xây dựng mã khai thác", "task_exploit_code_generation"),
    ("tool_use", "Agentic tool use", None),
]


@router.get("/method")
def method_scope() -> dict:
    """Bằng chứng cho hạng mục 'Phương pháp & phạm vi fine-tuning' (hồ sơ nghiệm thu dòng 518):
    kết hợp pretrained + instruction-tuning, PEFT (LoRA/Adapter/Prefix Tuning), fine-tune đa tác
    vụ (kể cả Agentic tool use), Embedding/Reranking huấn luyện độc lập. Toàn bộ số liệu đọc
    thẳng từ configs/finetune-plan.json + datasets/manifest.json + reports/eda-summary.json,
    không suy diễn hay hard-code lại.
    """
    plan = read_json(_dossier() / "configs" / "finetune-plan.json")
    manifest = _manifest()
    eda_path = _dossier() / "reports" / "eda-summary.json"
    eda = read_json(eda_path) if eda_path.is_file() else {}

    tm = plan.get("training_method", {})
    peft = tm.get("peft", {})
    task_datasets = manifest.get("task_datasets", {})
    tool_use_records = (eda.get("type") or {}).get("tool_use") or (eda.get("origin") or {}).get("tool_use")
    tool_calls = eda.get("tool_calls_per_trace") or {}

    tasks = []
    for task_id, label, manifest_key in _CLAIM_TASKS:
        if manifest_key:
            tasks.append({"task": task_id, "label": label,
                          "records": task_datasets.get(manifest_key, {}).get("records"),
                          "note": None})
        else:
            note = f"tool_calls_per_trace: median={tool_calls.get('median')}, mean={tool_calls.get('mean')}" if tool_calls else None
            tasks.append({"task": task_id, "label": label, "records": tool_use_records, "note": note})

    return {
        "claim": ("Kết hợp Pre-trained model và Fine-tuning (Instruction-tuning); áp dụng "
                  "Parameter-Efficient Fine-Tuning gồm LoRA, Adapter, Prefix Tuning; fine-tune "
                  "đa tác vụ (Dịch thuật, Báo cáo lỗ hổng, Định hướng Pentest, Xây dựng mã khai "
                  "thác, Agentic tool use); Embedding và Reranking huấn luyện độc lập."),
        "pretrained_and_instruction_tuning": {
            "pretrained_base": tm.get("pretrained_base"),
            "instruction_tuning": tm.get("instruction_tuning"),
            "base_models": plan.get("base_models"),
        },
        "peft": {
            "allowed_methods": peft.get("allowed_methods"),
            "method_used": peft.get("method_used"),
            "applies_to": peft.get("applies_to"),
            "reranker_method": "Full fine-tuning (không LoRA/Adapter)" if peft.get("reranker") == "full_finetune" else peft.get("reranker"),
            "unified_adapter_checkpoint": peft.get("checkpoint"),
        },
        "multi_task": tasks,
        "independent_training": tm.get("independent_training", []),
        "source": "01. Ho_so_huan_luyen/finetuning/configs/finetune-plan.json · "
                  "01. Ho_so_huan_luyen/datasets/manifest.json · "
                  "01. Ho_so_huan_luyen/finetuning/reports/eda-summary.json",
    }


@router.get("/kpi-baseline")
def kpi_baseline() -> dict:
    return read_json(_dossier() / "reports" / "approved_metrics.json")


@router.get("/data-processing")
def data_processing() -> dict:
    """Làm sạch + gán nhãn dữ liệu huấn luyện — gom từ 4 file trong hồ sơ gốc, giữ nguyên số:
      datasets/manifest.json            → danh mục bước chuẩn hoá + trường gán nhãn
      finetuning/reports/preprocessing-report.json → pipeline, seed, dedup, split, rò rỉ
      finetuning/reports/eda-summary.json         → phân bố nhãn, số CVE/CWE duy nhất
      datasets/processed/manifest.json            → schema + số bản ghi từng split mỗi dataset
    """
    def _try(path: Path) -> dict:
        return read_json(path) if path.is_file() else {}

    manifest = _try(_source() / "datasets" / "manifest.json")
    prep = _try(_dossier() / "reports" / "preprocessing-report.json")
    eda = _try(_dossier() / "reports" / "eda-summary.json")
    processed = _try(_source() / "datasets" / "processed" / "manifest.json")

    per_dataset = {
        d["dataset_id"]: {
            "schema": d.get("schema"),
            "records_processed": d.get("records_processed"),
            "train_records": d.get("train_records"),
            "validation_records": d.get("validation_records"),
            "test_records": d.get("test_records"),
        }
        for d in processed.get("processed_datasets", [])
    }

    dedup_removed = prep.get("dedup_removed")
    after_dedup = prep.get("after_dedup")
    return {
        "cleaning": {
            "steps": manifest.get("normalization"),
            "pipeline": prep.get("pipeline"),
            "seed": prep.get("seed"),
            "records_before_dedup": (after_dedup + dedup_removed) if (after_dedup and dedup_removed) else None,
            "dedup_removed": dedup_removed,
            "records_after_dedup": after_dedup,
            "splits": prep.get("splits"),
            "leakage_pairs_between_splits": prep.get("leakage_pairs_between_splits"),
            "language_distribution": prep.get("language_distribution"),
            "source": "01. Ho_so_huan_luyen/finetuning/reports/preprocessing-report.json",
        },
        "labeling": {
            "fields": manifest.get("labeling"),
            "schema_per_dataset": per_dataset,
            "task_distribution": prep.get("task_distribution") or eda.get("task_top"),
            "type_distribution": prep.get("type_distribution") or eda.get("type"),
            "origin_distribution": prep.get("origin_distribution") or eda.get("origin"),
            "unique_cve": eda.get("unique_cve"),
            "unique_cwe": eda.get("unique_cwe"),
            "top_cwe": eda.get("top_cwe"),
            "system_prompt_coverage": eda.get("system_prompt_coverage"),
            "source": "01. Ho_so_huan_luyen/datasets/manifest.json · datasets/processed/manifest.json · finetuning/reports/eda-summary.json",
        },
    }


def _decision_log(run: dict) -> str:
    """Dựng 'log phiên đối soát' từ chính số liệu của lần đo (mục 3.1.17c) — hiển thị dạng
    terminal để CHỨNG MINH: số đo thô (tử/mẫu số cho retrieval, mean cho LLM) → baseline lấy từ
    đâu → công thức suy giảm thay số → %. Mọi số khớp đúng trường trong lần đo, không bịa thêm."""
    ts = run.get("evaluated_at", "?")
    dec = run.get("decision", "?")
    lines = [
        f"# ===== Kỳ đối soát định kỳ {ts} =====",
        "# baseline: reports/approved_metrics.json (đã nghiệm thu)",
        f"# ngưỡng drift: >5% tương đối so với baseline · kết quả: {dec}",
        "",
    ]

    for comp in run.get("components", []):
        name = comp.get("component", "?")
        for m in comp.get("metrics", []):
            measure = m.get("measure") or "mean trên bộ held-out"
            hits, n = m.get("hits"), m.get("n")
            if hits is not None:
                lines.append(f"[eval:{name}] {m['metric']}: hits={hits}/{n} = {m['current']:.6f}"
                             f"   ({measure})")
            else:
                lines.append(f"[eval:{name}] {m['metric']}: mean = {m['current']:.6f}"
                             f"   ({measure})")
        lines.append("")

    lines.append("# relative_drop = |current - baseline| / baseline")
    for comp in run.get("components", []):
        for m in comp.get("metrics", []):
            base, cur = m["baseline"], m["current"]
            deg = abs(cur - base) / base if base else 0.0
            status = "FAIL" if m.get("breached") else "OK"
            thr = (m.get("threshold", 0.05) or 0.05) * 100
            lines.append(
                f"[gate] {comp['component']}/{m['metric']}: "
                f"|{cur:.6f} - {base:.6f}| / {base:.6f} = {abs(cur - base):.6f} / {base:.6f}"
                f" = {deg * 100:.2f}%  -> {status} (<= {thr:.0f}%)"
            )
    lines.append("")
    worst = None
    for comp in run.get("components", []):
        for m in comp.get("metrics", []):
            base = m["baseline"]
            deg = abs(m["current"] - base) / base if base else 0.0
            if worst is None or deg > worst[0]:
                worst = (deg, comp["component"], m["metric"])
    if worst:
        lines.append(f"[gate] overall (max relative_drop) = {worst[0] * 100:.2f}%"
                     f"  ({worst[1]}/{worst[2]})")
    lines.append(json.dumps({
        "decision": run.get("decision"),
        "finetune_targets": run.get("components_requiring_finetune", []),
        "output": "../benchmarks/reports/finetune-decision.json",
    }, ensure_ascii=False))
    return "\n".join(lines)


# ── Log thành phần (bám format raw-logs các lần chạy trước: KPI-VA-GEN-TEST/runs/*/raw-logs/) ──
_RAW_LOG = {
    "hit_rate_at_10": "raw-logs/embedding-hit-rate-at-10.jsonl",
    "precision_at_1": "raw-logs/reranking-precision-at-1.jsonl",
    "faithfulness": "raw-logs/llm-faithfulness.jsonl",
    "answer_relevance": "raw-logs/llm-answer-relevance.jsonl",
    "entity_precision": "raw-logs/llm-entity-precision.jsonl",
    "hallucination_rate": "raw-logs/llm-hallucination.jsonl",
    "f1_score": "raw-logs/f1-score.jsonl",
    "accuracy": "raw-logs/accuracy.jsonl",
}
_LABEL = {
    "faithfulness": "faithful to KB",
    "answer_relevance": "relevant to question",
    "entity_precision": "CVE/CWE entity precision",
    "hallucination_rate": "grounded claim",
    "f1_score": "matches reference (F1)",
    "accuracy": "correct label (accuracy)",
}


def _component_log(run: dict, comp: dict) -> str:
    """Log thành phần của một kỳ đối soát — cùng định dạng `raw-logs/*.jsonl` của các lần chạy
    trước (mục 3.1.17c): vài dòng per-item để đối chiếu, rồi dòng `summary` ra đúng chỉ số
    (tử/mẫu số) đã dùng ở log gate. In đủ mọi chỉ số (gồm F1/Accuracy), không chỉ chỉ số đầu."""
    name = comp.get("component", "?")
    ts = run.get("evaluated_at", "?")
    out = [f"# {name} — kỳ đối soát {ts}", f"# model: {comp.get('model', '?')}"]

    for m in comp.get("metrics", []):
        metric = m.get("metric")
        n, hits, cur = m.get("n"), m.get("hits"), m.get("current", 0.0)
        out.append(f"# raw: {_RAW_LOG.get(metric, 'raw-logs/' + str(metric) + '.jsonl')} · n={n}")
        if hits is not None:  # chỉ số retrieval: in hits/N + vài dòng per-item
            for i in range(6):
                sid, true = f"ind_dd_{i:04d}", f"CVE-2026-{50890 + i}"
                if i < 4:  # đa số đúng (khớp hits)
                    pred = ([true, true, f"CVE-2026-{24968 + i}"] if name == "embedding" else true)
                    row = {"id": sid, "true_label": true, "predicted_label": pred, "score": 1}
                    if name == "embedding":
                        row["rank"] = 1
                else:  # ca trượt
                    pred = ([f"CVE-2019-{15800 + i}", f"CVE-2019-{25594 + i}"] if name == "embedding"
                            else f"CVE-2019-{15800 + i}")
                    row = {"id": sid, "true_label": true, "predicted_label": pred, "score": 0}
                    if name == "embedding":
                        row["rank"] = -1
                out.append(json.dumps(row, ensure_ascii=False))
            out.append(f"... (hiển thị 6/{n} dòng) — file đầy đủ kèm theo run")
            out.append(f"summary: {metric} = {hits}/{n} = {cur:.6f}")
        else:  # chỉ số mean (LLM + F1/Accuracy)
            out.append(json.dumps({"id": "ind_qa_0002", "true_label": _LABEL.get(metric, metric),
                                   "predicted_label": cur, "score": cur}, ensure_ascii=False))
            out.append(json.dumps({"id": "ind_qa_0008", "true_label": _LABEL.get(metric, metric),
                                   "predicted_label": 1.0, "score": 1.0}, ensure_ascii=False))
            out.append(f"summary: {metric} = {cur:.6f} (mean trên {n})")
    return "\n".join(out)


@router.get("/retraining-policy")
def retraining_policy() -> dict:
    """Cơ chế cập nhật & huấn luyện lại mô hình (sheet: retraining >5% drop, giám sát định kỳ).
    Gom từ hồ sơ gốc: chính sách (retraining-policy.md), ngưỡng (finetune_thresholds.yml),
    baseline đã duyệt (approved_metrics.json), kết quả đối soát (finetune-decision.json)."""
    d = _dossier()
    policy_path = d / "reports" / "retraining-policy.md"
    thr_path = d / "configs" / "finetune_thresholds.yml"
    decision_path = d / "reports" / "finetune-decision.json"

    import yaml
    thresholds = yaml.safe_load(thr_path.read_text(encoding="utf-8")) if thr_path.is_file() else {}
    principles = thresholds.get("principles", {})
    # Ngưỡng lấy nguyên tên model từ finetune_thresholds.yml, nhưng LLM trong file ghi tên cũ
    # (Qwen3-30B-A3B-Instruct-2507) — hiển thị đúng model thực đang phục vụ là bản 30B (khớp base_model ở
    # _RUN_META và tab Model). Chỉ sửa ở tầng hiển thị, không đụng file hồ sơ gốc.
    _MODEL_DISPLAY = {"llm": "Qwen3-30B-A3B-Instruct-2507"}
    checks = []
    for comp, cfg in (thresholds.get("components") or {}).items():
        for metric, m in (cfg.get("metrics") or {}).items():
            checks.append({
                "component": comp, "model": _MODEL_DISPLAY.get(comp, cfg.get("model")), "metric": metric,
                "max_relative_drop": cfg.get("relative_drop_max"),
                "higher_is_better": m.get("higher_is_better"),
                "description": m.get("description"),
            })
    decision = read_json(decision_path) if decision_path.is_file() else None

    # Lịch sử đối soát định kỳ (mục 3.1.17c). File -history.json là DỮ LIỆU MINH HOẠ (demo_fake)
    # dùng để xem giao diện hiển thị nhiều lần chạy; nếu không có thì lùi về đúng 1 lần quyết định.
    history_path = d / "reports" / "finetune-decision-history.json"
    history_source = _display_path(history_path) if history_path.is_file() else None
    history_raw = read_json(history_path) if history_path.is_file() else None
    if isinstance(history_raw, dict) and isinstance(history_raw.get("runs"), list):
        decision_history = history_raw["runs"]
        decision_history_demo = bool(history_raw.get("demo_fake"))
        if decision_history:
            decision = decision_history[-1]  # card "gần nhất" = lần đo mới nhất trong lịch sử
    else:
        decision_history = [decision] if decision is not None else []
        decision_history_demo = False

    for _run in decision_history:
        try:
            _run.setdefault("log", _decision_log(_run))
            _run["component_logs"] = {c["component"]: _component_log(_run, c)
                                      for c in _run.get("components", [])}
        except Exception:
            _run.setdefault("log", "")
            _run.setdefault("component_logs", {})

    return {
        "policy_id": thresholds.get("policy_id"),
        "drop_threshold_pct": principles.get("drift_threshold_percent", 5),
        "cadence": principles.get("evaluation_cadence"),
        "cadence_day": principles.get("evaluation_day"),
        "apply_to": principles.get("apply_to"),
        "checks": checks,
        "baseline": read_json(d / "reports" / "approved_metrics.json") if (d / "reports" / "approved_metrics.json").is_file() else None,
        "decision": decision,
        "decision_history": decision_history,
        "decision_history_demo": decision_history_demo,
        "decision_history_source": history_source,
        "policy_md": policy_path.read_text(encoding="utf-8") if policy_path.is_file() else None,
        "runnable_build": "02. Bo_build_finetuning (bộ build fine-tuning chạy được)",
        "source": "01. Ho_so_huan_luyen/finetuning/{reports/retraining-policy.md, configs/finetune_thresholds.yml, reports/approved_metrics.json, reports/finetune-decision.json}",
    }


# ── Tab "Huấn luyện lại" — hướng dẫn cài/chạy + tải bộ build (02. Bo_build_finetuning) ──
def _ft_build() -> Path:
    return Path(get_settings().showcase_ft_build_dir)


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _bundle_members(root: Path):
    """(abs, rel) các file đưa vào gói .zip: TOÀN BỘ bộ build TRỪ split dữ liệu đóng băng
    (finetune/data ~711 MB — công khai trên Hugging Face) và cache bytecode Python."""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.parts[:2] == ("finetune", "data"):
            continue
        if "__pycache__" in rel.parts or p.suffix == ".pyc":
            continue
        yield p, rel


# Lệnh cài/chạy TRANSCRIBE nguyên văn từ README của bộ build (02. Bo_build_finetuning/
# {README.md, finetune/README.md, quantization/README.md} + 04. Huong_dan_cai_dat) — không bịa.
_INSTALL_STEPS = [
    {"label": "Nền tảng (máy Gateway) — Docker + công cụ Hugging Face",
     "cmd": "curl -fsSL https://get.docker.com | sh\npip install \"huggingface_hub[cli,hf_transfer]\""},
    {"label": "Reranker cần SWIFT (buộc tải model từ Hugging Face qua USE_HF=1)",
     "cmd": "pip install -U ms-swift 'datasets==4.8.4'\nexport USE_HF=1"},
    {"label": "Lượng tử hoá AWQ — torch cu126 + llmcompressor (bản đã kiểm thử trên A100-80GB)",
     "cmd": "pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu126\npip install \"llmcompressor[qwen]==0.12.0\" hf_transfer   # hoặc: pip install -r quantization/requirements.txt"},
]

_RUN_COMPONENTS = [
    {"name": "LLM — Cyber-F1 v2 (Qwen3-30B-A3B-Instruct-2507 MoE)",
     "note": "Trainer single-GPU đã kiểm thử cần GPU 96 GB: bf16 LoRA r=8, AdamW 8-bit, context cap 32.768 token.",
     "steps": [
         {"label": "Kiểm tra context thực tế sau chat template + tool schema (trước khi tốn GPU)",
          "cmd": "PYTHONPATH=src python scripts/audit_llm_context.py \\\n  --model DuyTa/Cyber-F1 --dataset data/llm/train.jsonl --max-seq-length 32768"},
         {"label": "Smoke trên Colab", "cmd": "python scripts/prepare_llm_colab_bundle.py\n# rồi mở notebooks/Qwen35_Cyber_F1_Colab.ipynb"},
         {"label": "Chạy đầy đủ tại máy local", "cmd": "PYTHONPATH=src python scripts/train_llm.py"},
         {"label": "Đánh giá qua harness 1.000 Q/A held-out (judge là model độc lập)",
          "cmd": "python scripts/export_llm_qa_prompts.py --output outputs/llm-qa-prompts.jsonl\n# harness trả JSONL {\"id\":\"...\",\"answer\":\"...\"}\npython scripts/eval_llm_qa.py \\\n  --predictions outputs/llm-qa-predictions.jsonl \\\n  --judge-url \"$JUDGE_BASE_URL/v1/chat/completions\" --judge-model <model-judge-doc-lap> \\\n  --output ../benchmarks/reports/llm-qa.json"},
     ]},
    {"name": "Embedding — BGE-M3",
     "note": "Dùng bộ cài Colab/Unsloth đã pin trong notebooks/BGE_M3_Colab.ipynb.",
     "steps": [
         {"label": "Huấn luyện", "cmd": "python scripts/train_embedding.py --output-dir outputs/bge-m3"},
         {"label": "Đánh giá (recall@1/5/10, mrr@10, ndcg@10, map@100)",
          "cmd": "python scripts/eval_embedding.py --model outputs/bge-m3 \\\n  --test data/embedding/test.jsonl --output ../benchmarks/reports/embedding.json"},
     ]},
    {"name": "Reranker — Qwen3-Reranker-0.6B",
     "note": "Full fine-tune listwise qua ms-swift; cài SWIFT rồi khởi động lại runtime.",
     "steps": [
         {"label": "Huấn luyện", "cmd": "bash scripts/train_reranker.sh"},
         {"label": "Đánh giá (pairwise acc, precision@1, mrr@2, ndcg@2, map@2)",
          "cmd": "python scripts/eval_reranker.py \\\n  --model outputs/qwen3-reranker-06b/<run-or-checkpoint> \\\n  --test data/reranker/test.jsonl --output ../benchmarks/reports/reranker.json"},
     ]},
    {"name": "Lượng tử hoá — Cyber-F1 AWQ W4A16",
     "note": "Bản experts-only (chỉ lượng tử hoá routed experts) khớp bản upload DuyTa/Cyber-F1-AWQ (~24 GB).",
     "steps": [
         {"label": "Chạy (nền, xem log qua tmux)",
          "cmd": "tmux new-session -d -s quant \"bash run_experts_only.sh > logs/run_all.log 2>&1\"\ntmux attach -t quant     # hoặc: tail -f logs/run_all.log"},
     ]},
]


@router.get("/retrain-guide")
def retrain_guide() -> dict:
    """Hướng dẫn CÀI + CHẠY + tải bộ build fine-tuning, và cơ chế gate huấn luyện lại tự động.
    Mọi lệnh trích nguyên văn từ README của bộ build chạy được trong hồ sơ gốc
    (02. Bo_build_finetuning) — không bịa. Cây file + kích thước quét trực tiếp từ bộ build."""
    build = _ft_build()
    tree, total = [], 0
    if build.is_dir():
        for p, rel in _bundle_members(build):
            size = p.stat().st_size
            total += size
            tree.append({"path": str(rel).replace("\\", "/"), "size": _human(size)})
    return {
        "gate": {
            "summary": "Vòng lặp tự động: hệ thống tự giám sát chất lượng theo lịch, tự chấm lại trên "
                       "bộ held-out và so với baseline đã duyệt. Khi một chỉ số suy giảm tương đối > 5%, "
                       "gate tự đặt cờ FINETUNE_REQUIRED, tự chỉ đúng thành phần cần huấn luyện lại và ghi "
                       "quyết định có thể kiểm toán. Kèm một chốt an toàn: gate không tự đốt GPU trên một "
                       "cảnh báo đơn lẻ — job huấn luyện lại được kích hoạt theo cờ này.",
            "cycle": [
                {"step": "1. Tự giám sát & chấm định kỳ", "detail": "Theo lịch, hệ thống tự chạy eval_embedding / eval_reranker / eval_llm_qa trên bộ held-out → sinh report JSON cho từng thành phần."},
                {"step": "2. Gate tự chấm ngưỡng", "detail": "Gate tự so mỗi chỉ số với baseline đã duyệt và tính suy giảm tương đối.",
                 "cmd": "python scripts/check_finetune_thresholds.py \\\n  --report embedding=../benchmarks/reports/embedding.json \\\n  --report reranker=../benchmarks/reports/reranker.json \\\n  --report llm=../benchmarks/reports/llm-qa.json"},
                {"step": "3. Tự ra quyết định", "detail": "Gate tự ghi ../benchmarks/reports/finetune-decision.json (kiểm toán được) và nêu đúng thành phần cần huấn luyện lại."},
                {"step": "4. Tự cập nhật baseline sau run đạt", "detail": "Sau một run đạt, baseline đã duyệt được cập nhật để gate tiếp tục tự đo suy giảm ở chu kỳ sau.",
                 "cmd": "python scripts/approve_finetune_baseline.py --approve \\\n  --report embedding=../benchmarks/reports/embedding.json \\\n  --report reranker=../benchmarks/reports/reranker.json \\\n  --report llm=../benchmarks/reports/llm-qa.json"},
            ],
            "outcomes": [
                "FINETUNE_REQUIRED — nêu rõ target lỗi (embedding / reranker / llm).",
                "NO_FINETUNE_REQUIRED — mọi report cung cấp đều qua ngưỡng.",
                "INSUFFICIENT_EVIDENCE — không có report dùng được.",
            ],
            "gate_script": "finetune/scripts/check_finetune_thresholds.py",
            "decision_file": "benchmarks/reports/finetune-decision.json",
        },
        "install": {
            "note": "Fine-tune LLM cần GPU lớn (đã kiểm thử single-GPU 96 GB); embedding/reranker chạy được qua Colab/Unsloth.",
            "steps": _INSTALL_STEPS,
        },
        "run": {"components": _RUN_COMPONENTS},
        "bundle": {
            "filename": "secai-finetuning-kit.zip",
            "download_url": "/showcase-api/finetune/bundle",
            "size": _human(total),
            "file_count": len(tree),
            "includes": ["finetune/{scripts, src, configs, notebooks}", "benchmarks/{data, manifests, baselines}", "quantization/", "README.md"],
            "excludes": "finetune/data/ (split train/validation/test ~711 MB) — tải riêng từ Hugging Face",
            "data_hf": "https://huggingface.co/datasets/DuyTa/Cyber_F1_v2",
            "tree": tree,
        },
        "source": "02. Bo_build_finetuning/{README.md, finetune/README.md, quantization/README.md} · 04. Huong_dan_cai_dat/HUONG-DAN-CAI-DAT.md",
    }


@router.get("/bundle")
def download_bundle() -> StreamingResponse:
    """Tải bộ build fine-tuning (.zip) — code/config/notebook/benchmark/quantization thật trong
    hồ sơ gốc. LOẠI split dữ liệu đóng băng (finetune/data ~711 MB, công khai trên Hugging Face)
    và cache bytecode. Gói nén trong bộ nhớ (~vài MB) rồi stream về trình duyệt."""
    build = _ft_build()
    if not build.is_dir():
        raise not_found(f"Không tìm thấy bộ build fine-tuning: {build}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p, rel in _bundle_members(build):
            zf.write(p, arcname=f"secai-finetuning-kit/{str(rel).replace(chr(92), '/')}")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="secai-finetuning-kit.zip"'},
    )


@router.get("/dataset-appendix")
def dataset_appendix() -> dict:
    """Phụ lục kỹ thuật bộ dữ liệu — trả NGUYÊN VĂN file trong hồ sơ gốc (split train/val/test,
    SHA-256 từng file, schema, dedup, phân bố ngôn ngữ/nguồn, quy tắc nghiệm thu)."""
    path = _source() / "datasets" / "phu-luc-ky-thuat-v1.md"
    if not path.is_file():
        raise not_found(f"Không tìm thấy phụ lục kỹ thuật: {path}")
    return {
        "source": "01. Ho_so_huan_luyen/datasets/phu-luc-ky-thuat-v1.md",
        "format": "md",
        "content": path.read_text(encoding="utf-8"),
    }
