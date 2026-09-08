FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    libmagic1 \
    libmagic-dev \
    libgomp1 \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-sin \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 300 -r requirements.txt

COPY backend ./backend
COPY mcp_lifestore ./mcp_lifestore

ENV PYTHONPATH=/app/backend:/app

EXPOSE 8001

CMD ["python", "mcp_lifestore/server.py"]
