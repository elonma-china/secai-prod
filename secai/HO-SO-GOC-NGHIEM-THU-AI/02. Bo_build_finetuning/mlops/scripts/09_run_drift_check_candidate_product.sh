#!/usr/bin/env bash
# Giai đoạn 9 (PRODUCT-KPI) — Requalify model CANDIDATE qua FULL PIPELINE sản phẩm, neo TRẦN KPI
# HỢP ĐỒNG ×0.95, trên BỘ CVE MỚI (candidate đã học CVE mới -> phải ĐẠT chỉ tiêu hợp đồng, không chỉ
# "không tệ hơn"). Trỏ endpoint CANDIDATE (đọc endpoint.json từ stage 8) qua env override.
#
# LƯU Ý (candidate stack): run_product_kpi.sh cần MỘT gateway /ai/chat trỏ vào LLM candidate để sinh
# answers full-RAG. endpoint.json chỉ có embed/rerank/LLM URL; cần dựng/ trỏ 1 gateway vào candidate
# (GW_BASE). Ở đây export embed/rerank/LLM cho tầng retrieval + judge; GW_BASE truyền vào là gateway
# đã wire candidate (deploy-time). Qdrant KB dùng CHUNG (kiến thức nằm ở KB, không ở model).
#
# Usage: 09_run_drift_check_candidate_product.sh <genset_dir> <report_out_dir> <endpoint.json> <candidate_gateway_base>
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GENSET="${1:?<genset_dir>}"; REPORT_DIR="${2:?<report_out_dir>}"; ENDPOINT_JSON="${3:?<endpoint.json>}"; GW_BASE="${4:?<candidate_gateway_base>}"
PY="$ROOT/../.venv-eval/bin/python"
mkdir -p "$REPORT_DIR"

# đọc endpoint candidate -> export cho run_product_kpi.sh (retrieval + judge dùng embed/rerank candidate)
eval "$("$PY" - "$ENDPOINT_JSON" <<'PY'
import json, sys, shlex
e = json.load(open(sys.argv[1]))
m = {"EMBED_URL": e.get("embed_url", ""), "EMBED_MODEL": e.get("embed_model", ""),
     "RERANK_URL": e.get("rerank_url", ""), "RERANK_MODEL": e.get("rerank_model", ""),
     "EMBED_KEY": e.get("api_key", ""), "RERANK_KEY": e.get("api_key", "")}
for k, v in m.items():
    if v:
        print(f"export {k}={shlex.quote(v)}")
PY
)"

bash "$ROOT/_lib/product_kpi/run_product_kpi.sh" "$GENSET" "$REPORT_DIR" "$GW_BASE"
# stage 10 (10_promote_or_hold.sh) đọc finetune-decision.json — đồng bộ tên (schema y hệt)
cp -f "$REPORT_DIR/product-decision.json" "$REPORT_DIR/finetune-decision.json"
echo "[09_product] candidate decision -> $REPORT_DIR/finetune-decision.json"
