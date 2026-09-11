FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libjpeg62-turbo zlib1g fonts-ipafont-gothic \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# gcp は live 用（Gemini / Cloud Storage）。mock では import されないが、
# 本番イメージに無いと GEMINI_MODE=live / STORAGE_DRIVER=gcs が起動時に落ちる。
RUN pip install --upgrade pip && pip install ".[dev,gcp]"

COPY app ./app
COPY web ./web
COPY tests ./tests

RUN mkdir -p /data/storage

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
