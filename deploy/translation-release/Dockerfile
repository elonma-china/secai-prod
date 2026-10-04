ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN /opt/venv/bin/pip install --no-cache-dir python-docx==1.2.0 python-multipart==0.0.20 tokenizers==0.23.2 Pillow==12.3.0 olefile==0.47 lingua-language-detector==2.0.2 regex==2026.9.29
COPY --chown=app:app overlay/ /app/src/secai_gateway/
COPY --chown=app:app devspace/ /app/devspace/
USER app
CMD ["uvicorn", "secai_gateway.multilang_app:app", "--host", "0.0.0.0", "--port", "8131"]
