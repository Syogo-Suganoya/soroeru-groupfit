# アーキテクチャ図の生成専用イメージ（アプリ本体には graphviz を持ち込まない）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends graphviz fonts-ipafont-gothic \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "diagrams>=0.23"

WORKDIR /work
CMD ["python", "docs/architecture.py"]
