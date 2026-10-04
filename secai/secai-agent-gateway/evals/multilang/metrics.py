"""Reference overlap diagnostics; never a substitute for the rubric gate."""

from __future__ import annotations

from typing import Sequence


def diagnostic_metrics(
    references: Sequence[str], predictions: Sequence[str | None], *,
    target_lang: str,
) -> dict:
    if len(references) != len(predictions):
        raise ValueError("references and predictions must align with scheduled samples")
    tokenizer = {
        "zh-Hans": "zh",
        "ja": "char",
        "ko": "char",
    }.get(target_lang, "intl")
    kept = [(reference, prediction) for reference, prediction in zip(references, predictions)
            if isinstance(prediction, str) and prediction.strip()]
    result = {
        "coverage": {"scored": len(kept), "scheduled": len(references)},
        "quality_gate": "NOT_ASSESSED",
        "bleu_tokenizer": tokenizer,
        "chrfpp": None,
        "bleu": None,
        "chrfpp_signature": None,
        "bleu_signature": None,
        "metric_status": "MISSING" if not kept else "READY",
    }
    if not kept:
        return result
    try:
        from sacrebleu.metrics import BLEU, CHRF
    except ImportError:
        result["metric_status"] = "MISSING_DEPENDENCY"
        return result
    refs = [reference for reference, _ in kept]
    outputs = [prediction for _, prediction in kept]
    chrf = CHRF(word_order=2)
    bleu = BLEU(tokenize=tokenizer, effective_order=True)
    result["chrfpp"] = round(chrf.corpus_score(outputs, [refs]).score, 4)
    result["bleu"] = round(bleu.corpus_score(outputs, [refs]).score, 4)
    result["chrfpp_signature"] = str(chrf.get_signature())
    result["bleu_signature"] = str(bleu.get_signature())
    return result
