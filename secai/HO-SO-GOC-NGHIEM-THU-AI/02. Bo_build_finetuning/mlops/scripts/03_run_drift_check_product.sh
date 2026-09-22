#!/usr/bin/env bash
# Giai đoạn 3 (PRODUCT-KPI) — Phát hiện suy giảm đo model ĐANG SERVING qua FULL PIPELINE sản phẩm
# (retrieval hybrid + LLM-RAG + task SAST/exploit/remediation), neo TRẦN KPI HỢP ĐỒNG ×0.95
# (finetune_thresholds_product.yml). THAY 03_run_drift_check.sh cũ (đo standalone dense-only/bare —
# sai thang đo). Đo trên BỘ CVE MỚI (raw genset acceptance_*.jsonl từ stage 2).
#
# Usage: 03_run_drift_check_product.sh <genset_dir> <report_out_dir> [gateway_base]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GENSET="${1:?cần <genset_dir> (acceptance_*.jsonl)}"; REPORT_DIR="${2:?cần <report_out_dir>}"
GW_BASE="${3:-http://localhost:8131}"
mkdir -p "$REPORT_DIR"

bash "$ROOT/_lib/product_kpi/run_product_kpi.sh" "$GENSET" "$REPORT_DIR" "$GW_BASE"

# run_pipeline.sh đọc finetune-decision.json — đồng bộ tên (schema đã giống hệt: decision + finetune_targets)
cp -f "$REPORT_DIR/product-decision.json" "$REPORT_DIR/finetune-decision.json"
echo "[03_product] decision -> $REPORT_DIR/finetune-decision.json"
