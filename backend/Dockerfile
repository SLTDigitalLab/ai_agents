FROM python:3.11-slim

WORKDIR /app

# Install system dependencies required for unstructured and OCR
RUN apt-get update && apt-get install -y \
    libxcb1 \
    poppler-utils \
    tesseract-ocr \
    libmagic-dev \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 300 -r requirements.txt

# Add additional required libraries after pip to preserve cache
RUN apt-get update && apt-get install -y libxcb-cursor0 && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
