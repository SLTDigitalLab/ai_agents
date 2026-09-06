FROM python:3.11-slim AS python-runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxcb1 libxcb-cursor0 libgl1 libglib2.0-0 libmagic-dev \
    poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 300 -r /tmp/requirements.txt

FROM python-runtime AS backend
WORKDIR /app
COPY backend/ /app/
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import socket; s=socket.create_connection(('127.0.0.1',8000),3); s.close()" || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]

FROM python-runtime AS mcp-lifestore
WORKDIR /app
COPY backend/ /app/backend/
COPY mcp_lifestore/ /app/mcp_lifestore/
ENV PYTHONPATH=/app/backend:/app
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import socket; s=socket.create_connection(('127.0.0.1',8001),3); s.close()" || exit 1
CMD ["python", "/app/mcp_lifestore/server.py"]
