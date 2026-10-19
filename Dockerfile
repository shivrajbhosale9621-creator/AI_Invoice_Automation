# Multi-Agent Invoice Automation App Dockerfile
FROM python:3.10-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=5000

# Install minimal OS dependencies for PyMuPDF, OpenCV headless, and ONNX Runtime OpenMP
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (.dockerignore excludes chroma_db, invoices.db, venvs, tests, etc.)
COPY . .

# Ensure runtime directories exist
RUN mkdir -p inboxes reports chroma_db

# Pre-download and bake ML models into image layers (RapidOCR ONNX & ChromaDB DefaultEmbeddingFunction)
RUN python -c "from ocr_engine import get_ocr_model_paths; get_ocr_model_paths(); from chromadb.utils import embedding_functions; embedding_functions.DefaultEmbeddingFunction()(['warmup'])"

# Expose default application port
EXPOSE 5000

# Healthcheck to ensure FastAPI service is live on active $PORT
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# Launch FastAPI app with Uvicorn respecting $PORT injected by Cloud Run
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
