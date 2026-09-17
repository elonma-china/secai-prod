# ═══════════════════════════════════════════════════════════════════════════════
#  secai-onebox/serving/Dockerfile — MỘT image duy nhất cho toàn bộ tầng serving
#  (LLM + embedding + reranker, N-GPU tự phát hiện, nginx LB nội bộ).
#
#  Bake nguyên "on_start_cyber_f1.sh" (đã chạy LIVE, validate trên instance vast
#  48438534, 2×A100) lên trên image chính chủ vllm/vllm-openai — KHÔNG viết lại
#  logic N-GPU. Script đã tự làm đúng những gì cần:
#    - NUM_GPUS="${NUM_GPUS:-$(nvidia-smi -L | wc -l)}"   → tự dò GPU, không fix cứng
#    - vòng lặp launch llm-g/embedding-g/reranker-g cho từng GPU (nohup, TP=1)
#    - sinh động nginx upstream N-thành-viên (least_conn) sau khi tất cả healthy
#
#  Bake sẵn nginx/gettext-base/curl ở BUILD TIME (cần internet 1 lần, ở máy có
#  internet) để lúc RUNTIME container không phải apt-get nữa — bắt buộc cho mục
#  tiêu air-gap: container phải tự khởi động lại được dù khi đó box đã bị cắt
#  mạng ngoài. Model weights (HF) vẫn cần pre-seed riêng vào volume hf-cache
#  (xem AIR-GAP mục B1 trong plan) — Dockerfile này không bake weights.
#
#  Build (từ thư mục secai/, context = secai/ để COPY được cả 2 nhánh):
#    docker build -f secai-onebox/serving/Dockerfile -t secai-serving:local ..
#  (docker-compose build tự set context đúng — xem docker-compose.yml)
# ═══════════════════════════════════════════════════════════════════════════════
ARG VLLM_BASE_IMAGE=vllm/vllm-openai:v0.25.1
FROM ${VLLM_BASE_IMAGE}

# nginx (LB) + gettext-base (envsubst, không dùng ở container này nhưng nhẹ, giữ
# tương thích) + curl (health probe nội bộ của on_start). git KHÔNG cần — template
# jinja đã COPY sẵn đúng vị trí REPO_DIR mong đợi nên script không phải git clone.
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends nginx gettext-base curl \
 && rm -rf /var/lib/apt/lists/*

# Layout đúng như on_start_cyber_f1.sh tự suy ra REPO_DIR = dirname(dirname(script)):
#   /opt/secai-serving/vast/on_start_cyber_f1.sh
#   /opt/secai-serving/templates/qwen3_reranker.jinja
COPY secai-agent-gateway/secai-serving-vast/vast/on_start_cyber_f1.sh /opt/secai-serving/vast/on_start_cyber_f1.sh
COPY secai-onebox/templates/qwen3_reranker.jinja /opt/secai-serving/templates/qwen3_reranker.jinja
COPY secai-onebox/serving/entrypoint.sh /opt/secai-serving/entrypoint.sh
RUN chmod +x /opt/secai-serving/vast/on_start_cyber_f1.sh /opt/secai-serving/entrypoint.sh

# Nội bộ mạng compose — không publish port ra host ở đây (gateway gọi service này
# qua tên service, xem docker-compose.yml).
EXPOSE 8000 8001 8002

# entrypoint.sh chạy on_start_cyber_f1.sh RỒI GIỮ CONTAINER SỐNG (script gốc tự thoát
# sau khi setup xong — nếu ENTRYPOINT thẳng vào nó, PID 1 exit → restart loop vô hạn).
ENTRYPOINT ["/opt/secai-serving/entrypoint.sh"]
