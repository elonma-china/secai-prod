# Build context: src/, pyproject.toml, devspace/ (dist + serve_static.py), report_runtime.py.
# The base is the pinned gateway image with the project's runtime dependencies.
ARG GATEWAY_IMAGE
FROM ${GATEWAY_IMAGE}
ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision=${SOURCE_REVISION}
COPY src /app/src
COPY pyproject.toml /app/pyproject.toml
COPY devspace /app/devspace
COPY report_runtime.py /app/report_runtime.py
