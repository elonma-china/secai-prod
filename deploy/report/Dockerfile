# Build the gateway image from this checkout first; report dependencies come from it.
ARG GATEWAY_IMAGE=secai-agent-gateway:reports-core
FROM ${GATEWAY_IMAGE}
COPY deploy/report/runtime.py /app/report_runtime.py
CMD ["uvicorn", "report_runtime:app", "--host", "0.0.0.0", "--port", "8131"]
