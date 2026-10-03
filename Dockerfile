FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN pip install .

COPY scripts ./scripts

# Placeholder until the web app exists: verify the Token Factory connection.
# Pass secrets at runtime: docker run --env-file .env personal-ai
CMD ["python", "scripts/smoke_llm.py"]
