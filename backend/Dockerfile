# Chatterbot-AI backend image (docs/IMPLEMENTATION.md §22.1).
# Build context is backend/ (Fly.io launches from backend/; compose uses it too):
#   docker build -t chatterbot-backend backend/
# Corrected vs. the source doc: adds libreoffice-core + poppler-utils for
# full-slide rasterization (recheck R9), which python-pptx cannot do alone.
FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

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
# CPU-only torch first: sentence-transformers pulls torch transitively, and the
# default Linux wheel from PyPI bundles several GB of CUDA libraries this CPU
# image never uses.
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt -r requirements-linux.txt

# Application code, copied flat so `uvicorn main:app` resolves (.dockerignore
# keeps .env, local model caches, job data and tests out).
COPY . /app/

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/data /models \
 && chown -R app:app /app/data /models
USER app

ENV CB_MODELS_DIR=/models \
    CB_DATA_DIR=/app/data \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1

# Bake Whisper, MiniLM and the Piper voice into the image (~600 MB) so no
# writable models volume is needed and boot doesn't download anything. Don't
# mount over /models: an empty mount would hide these (Piper won't re-download).
RUN python -m scripts.prefetch_models

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
