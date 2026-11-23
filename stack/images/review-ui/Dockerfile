# Review UI tĩnh + reverse proxy tới gateway (serve_static.py chỉ dùng stdlib).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 FE_HOST=0.0.0.0 FE_PORT=5174
COPY static/ /app/review-ui/
WORKDIR /app
EXPOSE 5174
USER 65534:65534
CMD ["python", "/app/review-ui/serve_static.py"]
