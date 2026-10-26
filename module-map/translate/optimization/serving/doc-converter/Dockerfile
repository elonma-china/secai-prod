FROM secai-doc-converter:1
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv /opt/v && /opt/v/bin/pip install --no-cache-dir fastapi uvicorn
COPY server.py /opt/server.py
USER 65534:65534
ENV HOME=/tmp
ENTRYPOINT ["/opt/v/bin/python", "/opt/server.py"]
