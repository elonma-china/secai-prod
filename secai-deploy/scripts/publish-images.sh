#!/usr/bin/env bash
# Build + push the gateway image to Docker Hub. Needs `docker login` beforehand.
set -euo pipefail
NAMESPACE="${NAMESPACE:-secai1999}"
TAG="${TAG:-1.0.0}"
GATEWAY_DIR="${1:-/home/ict/AIEngines/secai-agent-gateway}"

# --network host: this host's docker bridge/NAT blocks container egress (confirmed via
# DOCKER-USER/FORWARD chain — no internet from a default-bridge container even with explicit
# DNS), so `pip install .` during the build needs the host's network namespace directly.
docker build --network host -t "${NAMESPACE}/secai-gateway:${TAG}" "$GATEWAY_DIR"
docker tag "${NAMESPACE}/secai-gateway:${TAG}" "${NAMESPACE}/secai-gateway:latest"
docker push "${NAMESPACE}/secai-gateway:${TAG}"
docker push "${NAMESPACE}/secai-gateway:latest"
echo "DONE. Pushed ${NAMESPACE}/secai-gateway:${TAG} and :latest"
