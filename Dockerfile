FROM python:3.11-slim AS base

# Keep Python from writing .pyc files or buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install system packages required by scientific/vision dependencies and OCR
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        tesseract-ocr \
        poppler-utils \
        ffmpeg \
        libsm6 \
        libxext6 \
        libgl1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies separately to leverage Docker layer caching
COPY requirements-cpu.txt .
RUN pip install --upgrade pip && pip install -r requirements-cpu.txt

# Copy the application source
COPY . .

# Ensure folders expected at runtime exist even before any bind mounts are added
RUN mkdir -p logs uploads flask_session

# Pre-download embedding models to avoid cold-start delays
# Downloads jina-embeddings-v3 (primary) and all-MiniLM-L6-v2 (fallback)
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('jinaai/jina-embeddings-v3', trust_remote_code=True); \
    SentenceTransformer('all-MiniLM-L6-v2'); \
    print('Models pre-downloaded')"

EXPOSE 5001

# Gunicorn with tuned worker/thread settings
# --timeout 300: Allow time for first-request model loading (~40s for jina)
# --worker-tmp-dir /dev/shm: Use RAM for heartbeat file (avoids disk I/O stalls)
# --max-requests 500: Recycle workers more frequently to prevent memory bloat
CMD ["gunicorn", "run:app", "--bind", "0.0.0.0:5001", "--workers", "2", "--threads", "4", "--timeout", "300", "--graceful-timeout", "30", "--keep-alive", "5", "--max-requests", "500", "--max-requests-jitter", "50", "--worker-tmp-dir", "/dev/shm"]
