# Dịch vụ chuyển .doc -> .docx (LibreOffice headless). Nền ghim digest như deploy/translate-v2/Dockerfile.converter.
FROM ubuntu:24.04@sha256:a853f94d226358a79c740cfc7bce0c289748f3fe3488d921d038ccd752c61b60

ENV DEBIAN_FRONTEND=noninteractive HOME=/tmp
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv libreoffice-writer \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/v && /opt/v/bin/pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY server.py /opt/server.py

ENV CONVERTER_HOST=0.0.0.0 CONVERTER_PORT=18091
EXPOSE 18091
USER 65534:65534
ENTRYPOINT ["/opt/v/bin/python", "/opt/server.py"]
