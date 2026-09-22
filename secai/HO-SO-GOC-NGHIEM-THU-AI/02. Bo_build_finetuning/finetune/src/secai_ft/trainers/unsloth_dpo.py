"""Unsloth LoRA DPO for Cyber-F1 MoE, reusing the exact model-loading path of ``unsloth_sft``.

This is the "DPO" half of the MLOps pipeline's "Tự động Finetune/DPO" stage
(``mlops/scripts/05_dpo_llm.py``). It does NOT reimplement model loading, MoE-backend
selection or the Blackwell grouped-mm fallback — all of that is imported from
``unsloth_sft.load_base_model`` so the two trainers cannot silently drift apart.

Preference data ``jsonl`` records must have ``prompt`` (list of ``messages`` up to and
excluding the final assistant turn), ``chosen`` and ``rejected`` (assistant message content
strings). ``mlops/scripts/05_dpo_llm.py`` builds this file straight from a fresh
``llm-qa.json`` drift-check report: chosen = the harness gold answer, rejected = the
serving model's own candidate answer where the judge scored it below the pass threshold or
flagged ``hallucination: true`` — no extra judge call needed.
"""
from __future__ import annotations

from typing import Any


def _patch_trl_vision_mapping_rename() -> None:
    """Work around trl==0.22.2 importing a constant transformers has since renamed.

    Verified live (2026-09-18): `trl.trainer.dpo_trainer` does
    ``from transformers.models.auto.modeling_auto import MODEL_FOR_VISION_2_SEQ_MAPPING_NAMES``
    at module import time; transformers 5.5.0 (pulled in alongside trl==0.22.2 here — see
    `finetune/requirements-train.txt` for why that exact trl pin is needed for SFT) renamed it to
    ``MODEL_FOR_IMAGE_TEXT_TO_TEXT_MAPPING_NAMES``, so the bare import crashes without this.
    Only fixes the import; see the `DPOTrainer.process_row` aliasing in `run_unsloth_dpo` for why
    an empty-vs-aliased dict here turned out not to matter for `is_vision_model` at runtime.
    """
    import transformers.models.auto.modeling_auto as modeling_auto

    if not hasattr(modeling_auto, "MODEL_FOR_VISION_2_SEQ_MAPPING_NAMES"):
        modeling_auto.MODEL_FOR_VISION_2_SEQ_MAPPING_NAMES = {}


def _stub_optional_trl_judge_deps() -> None:
    """Let `trl.trainer.dpo_trainer` import without the optional judge/merge extras it pulls in
    unconditionally at module load, even though we never use them for plain offline DPO.

    Verified live (2026-09-18): importing `DPOTrainer` drags in
    `trl.trainer.callbacks` -> `trl.trainer.judges` -> `import llm_blender`, whose own
    `blender_utils.py` does ``from transformers.utils.hub import TRANSFORMERS_CACHE`` — a
    constant our (newer, Unsloth-required) transformers version no longer has, so the real
    package fails to import outright. `llm_blender.Blender()` is only ever instantiated inside
    `PairRMJudge.__init__` (checked in trl's `judges.py`), which we never construct, so a stub
    module satisfies the import without needing the real dependency to actually work.
    """
    import importlib.machinery
    import sys
    import types

    if "llm_blender" not in sys.modules:
        stub = types.ModuleType("llm_blender")
        stub.Blender = type("Blender", (), {})
        # trl's own availability probe (`importlib.util.find_spec`) raises `ValueError:
        # llm_blender.__spec__ is None` for a spec-less module already in sys.modules — give it
        # a real (if empty) spec so that check treats the stub as "available".
        stub.__spec__ = importlib.machinery.ModuleSpec("llm_blender", loader=None)
        sys.modules["llm_blender"] = stub


def run_unsloth_dpo(config: dict[str, Any]):
    from datasets import disable_caching, load_dataset

    from secai_ft.trainers.unsloth_sft import load_base_model

    # See unsloth_sft.run_unsloth_sft for why: a datasets/dill/typing_extensions version
    # combination can make DPOTrainer's internal `.map()` fingerprinting crash with
    # `PicklingError: Can't pickle sentinel`. Cheap to keep set here too even though pinning
    # trl==0.22.2 (see finetune/requirements-train.txt) already avoided it in practice.
    disable_caching()

    model, tok = load_base_model(config)  # already applies _patch_unsloth_token_placeholders

    _patch_trl_vision_mapping_rename()
    _stub_optional_trl_judge_deps()
    from trl import DPOConfig, DPOTrainer

    # Verified live (2026-09-18): even with `MODEL_FOR_VISION_2_SEQ_MAPPING_NAMES = {}` confirmed
    # empty in both `transformers.models.auto.modeling_auto` and `trl.trainer.dpo_trainer`'s own
    # namespace right up until `DPOTrainer(...)` runs, `DPOTrainer.__init__` still ends up with
    # `self.is_vision_model = True` for this checkpoint (root cause not pinned down — something
    # in Unsloth's Qwen3.5-MoE/vision-tower patching sets it some other way we did not find in
    # the generated `unsloth_compiled_cache` sources either). `is_vision_model=True` makes
    # `_prepare_dataset` call `self.process_row`, which requires `processing_class` to be a full
    # multimodal processor — but `load_base_model` always hands back the bare, already-unwrapped
    # tokenizer (correct for `SFTTrainer`, and the only kind of `processing_class` this pipeline
    # ever has, since none of its training data includes images). Rather than keep chasing where
    # the flag gets flipped, make the wrong branch behave like the right one: `process_row` and
    # `tokenize_row` are both plain `@staticmethod`s with an identical signature (checked in
    # trl's source), so aliasing one to the other is exact, not an approximation. Both are
    # declared `@staticmethod`; re-wrapping in `staticmethod(...)` is required — plain
    # `ClassName.process_row = ClassName.tokenize_row` unwraps the descriptor to a bare
    # function, which then auto-binds `self` as its first positional arg on the next
    # `self.process_row(...)` call (`TypeError: got multiple values for 'processing_class'`,
    # hit live 2026-09-18) instead of staying a plain, unbound static function.
    DPOTrainer.process_row = staticmethod(DPOTrainer.tokenize_row)

    ds = load_dataset("json", data_files={
        "train": config["dataset_path"], "validation": config["validation_path"]})

    def fmt(ex):
        return {
            "prompt": tok.apply_chat_template(
                ex["prompt"], tokenize=False, add_generation_prompt=True, enable_thinking=False),
            "chosen": ex["chosen"],
            "rejected": ex["rejected"],
        }

    train_ds = ds["train"].map(fmt)
    validation_ds = ds["validation"].map(fmt)
    if not len(validation_ds):
        raise ValueError("validation dataset is empty; refusing to train without model-selection data")

    args = DPOConfig(
        output_dir=config["output_dir"],
        per_device_train_batch_size=config["batch_size"],
        gradient_accumulation_steps=config["gradient_accumulation_steps"],
        num_train_epochs=config["epochs"], max_steps=config.get("max_steps", -1),
        learning_rate=float(config.get("learning_rate", 5e-6)),
        beta=config.get("dpo_beta", 0.1),
        warmup_ratio=config.get("warmup_ratio", 0.03), lr_scheduler_type=config.get("scheduler", "cosine"),
        optim="adamw_8bit", bf16=True, logging_steps=10,
        eval_strategy=config.get("eval_strategy", "steps"),
        eval_steps=config.get("eval_steps", 50),
        save_strategy=config.get("save_strategy", "steps"),
        save_steps=config.get("save_steps", 50),
        save_total_limit=config.get("save_total_limit", 2),
        max_length=config["max_seq_length"], max_prompt_length=config["max_seq_length"] // 2,
        report_to=["tensorboard"], logging_dir=config["logging"]["tensorboard_dir"],
    )
    # Verified live (2026-09-18): `DPOTrainer.__init__` unconditionally does
    # `model.warnings_issued["estimate_tokens"] = True` — a plain `transformers.PreTrainedModel`
    # always has that dict from its own `__init__`, but this PEFT-wrapped Qwen3.5-MoE model
    # (loaded via Unsloth) doesn't carry it through, so the attribute lookup falls all the way
    # to `AttributeError` instead of just missing a key. A real (empty) dict satisfies it.
    if not hasattr(model, "warnings_issued"):
        model.warnings_issued = {}

    # DPOTrainer builds its own frozen reference model from `model` when `ref_model=None` —
    # correct here because `model` is LoRA-wrapped: the reference forward pass runs with the
    # adapters disabled, so it is exactly the frozen base model, at no extra VRAM cost.
    trainer = DPOTrainer(model=model, ref_model=None, processing_class=tok,
                         train_dataset=train_ds, eval_dataset=validation_ds, args=args)

    stats = trainer.train()
    model.save_pretrained(config["output_dir"])
    tok.save_pretrained(config["output_dir"])
    print(f"[unsloth_dpo] done -> {config['output_dir']}  final_loss={getattr(stats, 'training_loss', None)}")
    return config["output_dir"]
