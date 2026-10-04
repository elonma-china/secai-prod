# Release context contains only reviewed overlay files and built frontend assets.
ARG BASE_IMAGE=secai-agent-gateway:rollback-before-review-20261004
FROM ${BASE_IMAGE}
COPY backend/ /app/src/secai_gateway/
COPY frontend/ /app/review-ui/
# Keep the original command, user, healthcheck and dependencies for gateway.
