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

# Install Python deps first for layer caching. requirements-linux.txt adds Piper
# TTS (no Windows wheel, R12) — on Linux tts_backend=auto selects Piper, so the
# image must have it.
COPY requirements.txt requirements-linux.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-linux.txt

# Application code (backend/ is copied flat so `uvicorn main:app` resolves).
COPY backend/ /app/
COPY docker/fetch_piper_voice.py /app/fetch_piper_voice.py

# Run as a non-root user; it owns the job-data dir and the models mount point.
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/data /models \
 && chown -R app:app /app/data /models
USER app

# Models cache to a mounted volume on first run (Whisper/MiniLM auto-download;
# the Piper voice is fetched by fetch_piper_voice.py before the server starts).
ENV CB_MODELS_DIR=/models
EXPOSE 8000

CMD ["sh", "-c", "python fetch_piper_voice.py && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
