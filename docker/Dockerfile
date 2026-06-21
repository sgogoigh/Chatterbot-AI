# Chatterbot-AI backend image (IMPLEMENTATION.md §22.1).
# Corrected vs. the source doc: adds libreoffice-core + poppler-utils for
# full-slide rasterization (recheck R9), which python-pptx cannot do alone.
FROM python:3.11-slim

# System dependencies:
#   ffmpeg/libsndfile1  — audio decode/encode
#   tesseract-ocr       — OCR over slide images
#   libreoffice-core    — pptx -> pdf rendering (R9)
#   poppler-utils       — pdf -> png rendering (R9)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libsndfile1 \
        tesseract-ocr \
        libreoffice-core \
        poppler-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (backend/ is copied flat so `uvicorn main:app` resolves).
COPY backend/ /app/

# Models cache to a mounted volume on first run (Whisper/MiniLM/Piper auto-download).
ENV CB_MODELS_DIR=/models
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
