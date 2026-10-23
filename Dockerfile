FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install . && useradd --create-home --uid 10001 lantern

# Don't run as root inside the container.
USER lantern

# Secrets are passed at runtime, never baked in:
#   docker run --env-file .env -p 8000:8000 lantern
# Cloud Run sets $PORT (8080); locally it defaults to 8000. --proxy-headers so the app sees https behind a proxy.
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
