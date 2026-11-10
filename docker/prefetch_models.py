"""
docker/prefetch_models.py — bake model weights into the image at build time.

Runs from /app (the backend code) during ``docker build`` as the non-root app
user, using the backend's own Settings so model names and cache paths match
exactly what the services load at runtime:

  * Faster-Whisper ``whisper_model``  -> WhisperModel(download_root=models_dir)
  * MiniLM ``minilm_model``           -> SentenceTransformer(cache_folder=models_dir)
  * Piper ``piper_voice``             -> models_dir/piper/<voice>.onnx(.json)
  * Silero VAD needs nothing: its .onnx ships inside the silero-vad package.

Baking avoids a writable models volume (Fly.io volumes mount root-owned, and the
image runs as a non-root user) and removes the ~600 MB first-boot download.
"""

from __future__ import annotations

import sys

from config import get_settings


def main() -> int:
    """Download every runtime model into ``settings.models_dir``."""
    s = get_settings()
    print(f"prefetching models into {s.models_dir}", flush=True)

    from faster_whisper import WhisperModel

    WhisperModel(
        s.whisper_model, device="cpu", compute_type=s.whisper_compute_type,
        download_root=s.models_dir,
    )
    print(f"  whisper {s.whisper_model}: ok", flush=True)

    from sentence_transformers import SentenceTransformer

    SentenceTransformer(s.minilm_model, cache_folder=s.models_dir)
    print(f"  minilm {s.minilm_model}: ok", flush=True)

    import fetch_piper_voice

    if fetch_piper_voice.main() != 0:
        return 1
    print(f"  piper {s.piper_voice}: ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
