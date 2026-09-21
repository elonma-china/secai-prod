"""Unsloth 16-bit LoRA SFT for Qwen3-30B-A3B-Instruct-2507 MoE, messages format, loss on assistant turns.

MoE specifics (Unsloth Faster-MoE, https://unsloth.ai/docs/basics/faster-moe):
  * NO 4-bit/QLoRA — bitsandbytes cannot 4-bit-quantize the fused MoE expert Parameters yet.
    We load in bf16 (`load_in_4bit=False`); 30B-A3B 16-bit LoRA needs ~63GB VRAM (80GB+ GPU).
  * LoRA must explicitly target the expert projections `gate_up_proj` + `down_proj`; they are
    NOT auto-included. The router layer is intentionally left untargeted (Unsloth default).
  * `torch._grouped_mm` in the Torch 2.8 wheel is Hopper-only (SM90).  On Blackwell (SM120),
    force Unsloth's Triton grouped-GEMM instead.  `native_torch` remains a correct, slower
    fallback if Triton is unavailable.

Agent specifics: the model serves behind the linch agent which always declares the three KB
tools, so we inject the same `tools=` schema at train time (parity) and decode tool_call
arguments from JSON-string to dict so Qwen3's template renders them as real objects, not a
double-encoded string. Loss is masked to assistant turns (incl. tool_call turns); Qwen3
renders tool results under `<|im_start|>user`, so `train_on_responses_only` masks them too.
"""
from __future__ import annotations

import os
import sys
from typing import Any


def load_base_model(config: dict[str, Any]):
    """Load + LoRA-wrap the Cyber-F1 MoE base model exactly like the SFT run does.

    Shared by ``run_unsloth_sft`` and ``unsloth_dpo.run_unsloth_dpo`` so the MoE-backend
    selection, Blackwell grouped-mm fallback, meta-tensor guard and LoRA target modules stay
    in one place instead of being copy-pasted per trainer.  Returns ``(model, tok)`` where
    ``tok`` is already unwrapped from the Qwen3 MoE processor.
    """
    # Must be set before importing/loading the Qwen3-30B-A3B-Instruct-2507 patch.  Do not use setdefault here:
    # a stale Colab compiled cache may have left the Hopper-only grouped_mm selection behind.
    backend = config.get("moe_backend", "unsloth_triton")
    if backend not in {"grouped_mm", "unsloth_triton", "native_torch"}:
        raise ValueError(f"Unsupported moe_backend={backend!r}")
    os.environ["UNSLOTH_MOE_BACKEND"] = backend
    # This separate optimization also calls torch._grouped_mm.  The Qwen3-30B-A3B-Instruct-2507 dispatcher above
    # still uses the selected Triton/native backend, but this prevents an older Zoo cache from
    # wrapping ModuleList experts with the Hopper-only fast path.
    if backend != "grouped_mm":
        os.environ["UNSLOTH_MOE_GROUPED"] = "0"
    # Verified live (2026-09-18, single A100 80GB): repeated MoE Triton kernel autotune
    # attempts fail with CUDA OOM near the VRAM ceiling and fall back to non-autotuned (higher
    # memory) kernels; a few steps later a routine grouped-GEMM allocation then fails too even
    # though PyTorch reports several GB "reserved but unallocated" — classic allocator
    # fragmentation, not genuinely out of memory. `expandable_segments` is PyTorch's own
    # documented mitigation (surfaced directly in the OOM error message) and must be set before
    # the CUDA context / any tensor is created, so set it here, before `import torch` fires via
    # the unsloth import below. Does not override an operator-provided value.
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    print(f"[unsloth_sft] MoE backend: {backend}")

    from unsloth import FastLanguageModel

    model, processor = FastLanguageModel.from_pretrained(
        model_name=config["base_model"],
        max_seq_length=config["max_seq_length"],
        load_in_4bit=config.get("load_in_4bit", False),
        fast_inference=config.get("fast_inference", False),
        full_finetuning=False,
        # Cyber-F1 is the conditional-generation checkpoint (it has a small vision tower as
        # well as the Qwen3-30B-A3B-Instruct-2507 text MoE).  `auto` can spill its LM head to meta/CPU even when
        # the 96GB Blackwell has room.  SFT then fails in Unsloth's token check when it calls
        # `.cpu()` on that meta tensor.  Keep every trainable text tensor on the single GPU.
        device_map=config.get("device_map", "cuda:0"),
    )
    meta_parameters = [name for name, parameter in model.named_parameters()
                       if parameter.device.type == "meta"]
    if meta_parameters:
        preview = ", ".join(meta_parameters[:5])
        raise RuntimeError(
            "Cyber-F1 was partially offloaded to meta/CPU; refusing to start an invalid SFT "
            f"run (examples: {preview}). Use a GPU with enough free VRAM or lower max_seq_length."
        )
    # Qwen3-30B-A3B-Instruct-2507 MoE returns a processor (the Unsloth notebook accesses
    # processor.tokenizer). Keep this compatible with text-only models, too.
    tok = getattr(processor, "tokenizer", processor)
    # Verified live (2026-09-18): pointing `base_model` at a PREVIOUS run's saved LoRA
    # checkpoint (e.g. DPO continuing straight from that cycle's fresh SFT output, or from an
    # already-pushed candidate repo) makes `from_pretrained` auto-attach the existing adapter —
    # Unsloth then refuses a second `get_peft_model` call ("You already added LoRA adapters to
    # your model!"). Only attach a fresh adapter when `base_model` was a plain (non-PEFT)
    # checkpoint; a LoRA checkpoint keeps training the SAME adapter it was saved with.
    if not getattr(model, "peft_config", None):
        peft = config["peft"]
        model = FastLanguageModel.get_peft_model(
            model, r=peft["r"], lora_alpha=peft["alpha"], lora_dropout=peft.get("dropout", 0.0),
            target_modules=peft["target_modules"],
            use_gradient_checkpointing=config.get("gradient_checkpointing", True),
            random_state=3407, bias="none",
        )
    else:
        print(f"[unsloth_sft] {config['base_model']} already carries a LoRA adapter — "
              "continuing training on it instead of attaching a new one")
    _install_blackwell_grouped_mm_fallback()
    _patch_unsloth_token_placeholders(tok)
    return model, tok


def _load_messages_only(path: str):
    """Read a Cyber_F1_v2-style JSONL, keeping only the ``messages`` column.

    Real data has inconsistent columns across rows (verified on the live
    ``data/llm/train.jsonl`` smoke run 2026-09-18: 43,692/47,034 rows carry an extra
    ``poc_expected`` field, the rest don't). ``datasets.load_dataset("json", ...)`` infers one
    Arrow schema for the whole file and raises ``DatasetGenerationCastError`` the moment a row's
    column set differs. ``fmt()`` below only ever reads ``messages`` (every other column is
    dropped immediately via ``remove_columns``), so parsing just that field sidesteps the
    mismatch entirely instead of requiring every row to share the exact same optional metadata
    columns.
    """
    import json

    from datasets import Dataset

    with open(path, encoding="utf-8") as handle:
        rows = [{"messages": json.loads(line)["messages"]} for line in handle if line.strip()]
    return Dataset.from_list(rows)


_UNSLOTH_TOKEN_PLACEHOLDERS = {
    "<EOS_TOKEN>": "eos_token_id",
    "<PAD_TOKEN>": "pad_token_id",
    "<BOS_TOKEN>": "bos_token_id",
    "<UNK_TOKEN>": "unk_token_id",
}


def _patch_unsloth_token_placeholders(tok) -> None:
    """Work around an Unsloth 2026.9.6 + TRL 0.24 bug for the Qwen3.5-MoE tokenizer backend.

    Verified live (2026-09-18, DuyTa/Cyber-F1): `tok.eos_token`/`tok.pad_token` read back
    correctly right up until `SFTTrainer(...)` (Unsloth's generated, multi-layer
    ``UnslothSFTTrainer`` wrapper in ``~/unsloth_compiled_cache/``) is constructed — passing the
    real token explicitly to `SFTConfig` makes no difference (confirmed via debug prints:
    `args.eos_token` is still correct immediately after `SFTConfig(...)` returns, but has become
    a literal placeholder string like ``"<EOS_TOKEN>"``/``"<PAD_TOKEN>"`` by the time TRL
    validates it a few lines into `SFTTrainer.__init__`). The exact reconstruction step could not
    be pinned to one line in the generated wrapper (multiple nested `__init__`s re-derive `args`
    via `to_dict()`/`UnslothSFTConfig(**dict_args)`), so patch the one thing every code path
    converges on instead: `tokenizer.convert_tokens_to_ids(...)`, which TRL's SFTTrainer uses to
    validate whichever placeholder string it ends up with. This never touches the tokenizer's
    real vocabulary or the model's embeddings.
    """
    real_convert = tok.convert_tokens_to_ids

    def patched_convert(token):
        attr = _UNSLOTH_TOKEN_PLACEHOLDERS.get(token)
        if attr is not None:
            return getattr(tok, attr)
        return real_convert(token)

    tok.convert_tokens_to_ids = patched_convert


def run_unsloth_sft(config: dict[str, Any]):
    # Unsloth must patch Transformers/TRL before they are imported.
    from datasets import disable_caching
    from trl import SFTConfig, SFTTrainer
    from unsloth.chat_templates import train_on_responses_only

    from secai_ft.tools_schema import TOOLS_SCHEMA, normalize_tool_calls

    # Verified live (2026-09-18): SFTTrainer's internal dataset prep calls `datasets.map()`,
    # which hashes the transform function via `dill` to build a cache fingerprint. With this
    # exact datasets/dill/typing_extensions combination that hash raises
    # `PicklingError: Can't pickle sentinel: it's not the same object as typing_extensions.sentinel`
    # (an ecosystem version-mismatch bug, unrelated to our code). Disabling the on-disk map
    # cache skips fingerprinting entirely; there is no meaningful cache to reuse across runs
    # here anyway since each run gets a fresh checkout.
    disable_caching()

    # transformers 5.x tham chiếu `typing_extensions.sentinel` trong transform_args của .map();
    # datasets.update_fingerprint chỉ bọc try/except quanh hash(transform), KHÔNG quanh
    # hash(transform_args) -> PicklingError "Can't pickle sentinel" lọt ra và làm chết SFTTrainer's
    # internal dataset prep. Caching đã tắt ở trên nên fingerprint chỉ cần duy nhất: vá để mọi lỗi
    # pickle -> fingerprint ngẫu nhiên (an toàn, không dùng lại cache đĩa). Vá cả 2 namespace vì
    # arrow_dataset import update_fingerprint trực tiếp.
    try:
        import uuid as _uuid
        from datasets import fingerprint as _dfp
        from datasets import arrow_dataset as _dad
        _orig_uf = _dfp.update_fingerprint
        if not getattr(_orig_uf, "_secai_safe", False):
            def _safe_update_fingerprint(fingerprint, transform, transform_args):
                try:
                    return _orig_uf(fingerprint, transform, transform_args)
                except Exception:
                    return _uuid.uuid4().hex
            _safe_update_fingerprint._secai_safe = True
            _dfp.update_fingerprint = _safe_update_fingerprint
            _dad.update_fingerprint = _safe_update_fingerprint
    except Exception:
        pass

    # TRL 0.24 `_prepare_dataset` map truyền num_proc qua **map_kwargs (KHÔNG tôn trọng
    # SFTConfig.dataset_num_proc) -> datasets fork worker -> pickle transform (transformers 5.x
    # `typing_extensions.sentinel`) chết. ÉP mọi Dataset.map chạy in-process (num_proc=None) để
    # không fork; fingerprint pickle in-process đã được vá ở trên. Đây là smoke-safe (chậm hơn chút).
    try:
        from datasets import Dataset as _DSforce
        _orig_map_force = _DSforce.map
        if not getattr(_orig_map_force, "_secai_noproc", False):
            def _map_in_process(self, *a, **k):
                k["num_proc"] = None
                return _orig_map_force(self, *a, **k)
            _map_in_process._secai_noproc = True
            _DSforce.map = _map_in_process
    except Exception:
        pass

    model, tok = load_base_model(config)  # already applies _patch_unsloth_token_placeholders

    ds = {"train": _load_messages_only(config["dataset_path"]),
          "validation": _load_messages_only(config["validation_path"])}

    # smoke test: slice to a tiny subset so the full pipeline (load->format->train->save)
    # runs in a few steps before committing GPU hours to the whole corpus.
    n_smoke = config.get("max_train_samples")
    if n_smoke:
        n = min(int(n_smoke), len(ds["train"]))
        ds["train"] = ds["train"].select(range(n))
        print(f"[unsloth_sft] SMOKE: training on first {n} samples only")

    # Verified live (2026-09-18, single A100 80GB): 3 real training steps complete fine, but
    # `Trainer.train()` always runs one more evaluation pass at the very end regardless of
    # `eval_steps`/`max_steps`, over the FULL validation split (5,877 records here) — that OOMs
    # on 80GB because training has already claimed most of it (matches this file's own
    # docstring: the tested single-GPU config needs 96GB). Slicing eval the same way as train
    # keeps a smoke run self-contained on a smaller GPU without touching the full-run config.
    n_smoke_eval = config.get("max_eval_samples")
    if n_smoke_eval:
        n = min(int(n_smoke_eval), len(ds["validation"]))
        ds["validation"] = ds["validation"].select(range(n))
        print(f"[unsloth_sft] SMOKE: evaluating on first {n} samples only")

    def fmt(ex):
        messages = normalize_tool_calls(ex["messages"])  # arguments: JSON-string -> dict
        return {"text": tok.apply_chat_template(
            messages, tools=TOOLS_SCHEMA, tokenize=False, add_generation_prompt=False,
            enable_thinking=False)}

    train_ds = ds["train"].map(fmt, remove_columns=ds["train"].column_names)
    validation_ds = ds["validation"].map(fmt, remove_columns=ds["validation"].column_names)
    if not len(validation_ds):
        raise ValueError("validation dataset is empty; refusing to train without model-selection data")

    # Verified live (2026-09-18, single A100 80GB, expandable_segments already set): 3 smoke
    # steps alone climb to 78.9/79.3GB in use — there is no headroom left for even a 20-example
    # eval pass afterward (Trainer always runs one at the end of `train()`), OOM either way. This
    # matches the 96GB this file's own docstring says the tested full-size config needs. A smoke
    # run only needs to prove the train loop executes, so skip eval entirely instead of shrinking
    # it further; real (non-smoke) runs keep the caller's/default eval behaviour untouched.
    eval_strategy = "no" if n_smoke else config.get("eval_strategy", "steps")

    args = SFTConfig(
        output_dir=config["output_dir"], dataset_text_field="text",
        per_device_train_batch_size=config["batch_size"],
        gradient_accumulation_steps=config["gradient_accumulation_steps"],
        num_train_epochs=config["epochs"], max_steps=config.get("max_steps", -1),
        learning_rate=float(config["learning_rate"]),
        lr_scheduler_type=config.get("scheduler", "cosine"),
        dataset_num_proc=None,  # PHẢI None (KHÔNG 1): datasets>=4.1 vẫn fork 1 worker khi num_proc>=1 -> worker pickle map (transformers 5.x sentinel) chết; None = chạy in-process, không fork. (Fingerprint pickle được vá ở update_fingerprint trên.)
        optim="adamw_8bit", bf16=True, logging_steps=10,
        eval_strategy=eval_strategy,
        eval_steps=config.get("eval_steps", 250),
        save_strategy=config.get("save_strategy", "steps"),
        save_steps=config.get("save_steps", 250),
        save_total_limit=config.get("save_total_limit", 2),
        max_length=config["max_seq_length"], report_to=["tensorboard"],
        # Leave eos_token unset (None -> falls back to processing_class.eos_token per TRL's own
        # docs). Overriding it here would NOT help even if it looked wrong: see
        # `_patch_unsloth_eos_token_placeholder` above for why Unsloth's generated SFTTrainer
        # wrapper silently replaces whatever value ends up here with a broken placeholder for
        # this tokenizer backend, and why the fix lives at the tokenizer level instead.
    )
    # TRL 0.22 (required by the Qwen3-30B-A3B-Instruct-2507 MoE notebook) renamed `tokenizer`
    # to `processing_class`.
    trainer = SFTTrainer(model=model, processing_class=tok, train_dataset=train_ds,
                         eval_dataset=validation_ds, args=args)
    # loss masked to assistant turns (incl. tool_calls turns) via the Qwen3 chat markers
    trainer = train_on_responses_only(
        trainer, instruction_part="<|im_start|>user\n", response_part="<|im_start|>assistant\n")

    _sanity_check_masking(trainer, tok)

    stats = trainer.train()
    model.save_pretrained(config["output_dir"])
    tok.save_pretrained(config["output_dir"])
    print(f"[unsloth_sft] done -> {config['output_dir']}  final_loss={getattr(stats,'training_loss',None)}")
    return config["output_dir"]


def _install_blackwell_grouped_mm_fallback() -> None:
    """Work around Torch 2.8's SM90-only ``torch._grouped_mm`` in expert-LoRA.

    Unsloth's Qwen3-30B-A3B-Instruct-2507 Triton MoE path is compatible with Blackwell.  Its small expert-LoRA
    delta path can nevertheless call ``torch._grouped_mm`` directly.  That Torch 2.8 operation
    rejects the RTX PRO 6000's SM120.  The fallback is differentiable and only runs after that
    exact unsupported-hardware error; supported GPUs retain the fast grouped kernel.
    """
    try:
        import torch
        from unsloth_zoo.temporary_patches import moe_utils

        if not torch.cuda.is_available() or torch.cuda.get_device_capability() < (12, 0):
            return
        modules = [moe_utils]
        load_cached = getattr(moe_utils, "_load_cached_moe_utils_module", None)
        if callable(load_cached) and (cached := load_cached()) is not None:
            modules.append(cached)
        cached_module = sys.modules.get("unsloth_cached_moe_utils")
        if cached_module is not None:
            modules.append(cached_module)

        for module in modules:
            original = getattr(module, "_grouped_mm_with_backward_fix", None)
            manual = getattr(module, "_manual_grouped_mm", None)
            if original is None or manual is None or getattr(original, "_secai_blackwell_safe", False):
                continue

            def safe_grouped_mm(inputs, weight, offsets, _original=original, _manual=manual):
                try:
                    return _original(inputs, weight, offsets)
                except RuntimeError as exc:
                    if "compute capability = 9.0" not in str(exc):
                        raise
                    return _manual(inputs.contiguous(), weight.contiguous(), offsets)

            safe_grouped_mm._secai_blackwell_safe = True
            module._grouped_mm_with_backward_fix = safe_grouped_mm
        print("[unsloth_sft] Blackwell grouped-mm fallback enabled for expert-LoRA")
    except Exception as exc:
        # The fallback is only needed for one Torch/architecture combination.  Keep normal
        # Unsloth environments unchanged, but make a failed installation visible in Colab logs.
        print(f"[unsloth_sft] Blackwell grouped-mm fallback unavailable: {exc}")


def _sanity_check_masking(trainer, tok) -> None:
    """Decode the unmasked (label != -100) tokens of the first example and print them.

    Cheap guard against a template/marker drift silently masking the whole sequence (loss on
    nothing) or unmasking the user/tool turns (loss on inputs). The printed span should be the
    assistant text — tool calls and final answers — and nothing from the user or tool_response.
    """
    try:
        ex = trainer.train_dataset[0]
        ids, labels = ex["input_ids"], ex["labels"]
        kept = [i for i, l in zip(ids, labels) if l != -100]
        total = len(labels)
        print(f"[unsloth_sft] mask check: {len(kept)}/{total} tokens carry loss "
              f"({100 * len(kept) / max(total, 1):.1f}%)")
        print("[unsloth_sft] first unmasked span:\n"
              + tok.decode(kept[:200]).replace("\n", "\\n"))
    except Exception as exc:  # never let a diagnostic abort training
        print(f"[unsloth_sft] mask check skipped: {exc}")
