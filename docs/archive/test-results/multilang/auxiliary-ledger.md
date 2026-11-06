# Shared auxiliary model trial ledger

Read-only snapshot from the other task's docs/loop/STATE.md on 2026-10-04. Limit is **three total OCR/LID/QE model/checkpoint trials** for the coordinated work, one loaded/inferred at a time; DuyTa translator is outside this count. Ownership and detailed trial artifacts remain with the file/OCR worker.

| Slot | Model/checkpoint | Owner and observed state | Assessment action |
|---|---|---|---|
| 1/3 | Paddle OCR checkpoint reported by file/OCR task | OCR worker loaded/inferred; Arabic reading order failed bounded calibration | Reuse owner evidence only; no duplicate worker/download |
| 2/3 | Qwen3.5-2B @ 15852e8c16360a2fea060d615a32b45270f8a8fc | OCR worker loaded/inferred Arabic smokes; full accuracy not yet established | Count used; do not rerun or alter process |
| 3/3 | Qwen3-VL-2B-Instruct, frozen OCR policy `18b11ec` | File/OCR worker completed its own synthetic benchmark and stopped owned PID 20474/tunnels; final task state is STOPPED/BLOCKED | Count used; do not rerun or alter process |

No assessment LID/QE model has been downloaded, loaded or inferred. Rules and sacrebleu metrics are weightless and do not consume a model slot. The shared auxiliary allowance is now **3/3 exhausted**. OCR's owned helper process/tunnels have stopped, but assessment still has no live reservation, corpus, loaded-revision attestation or independent review evidence; live assessment remains blocked.
