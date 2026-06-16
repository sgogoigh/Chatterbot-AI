"""
config.py — Central configuration for the Chatterbot-AI backend.

See IMPLEMENTATION.md §4 (Configuration & environment).

Everything is driven by environment variables (prefixed ``CB_``) or a local
``.env`` file via ``pydantic-settings``. No secret is ever hard-coded. The
single :func:`get_settings` accessor returns a process-wide cached instance so
every module reads the *same* configuration object.

Structural note: this module sits at the root of the ``backend/`` package tree
(the Docker image sets ``WORKDIR /app`` and copies ``backend/`` there, so
top-level imports like ``from config import get_settings`` resolve correctly).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed, env-driven application settings (IMPLEMENTATION.md §4).

    Each field documents the subsystem it configures. Defaults are chosen so the
    service boots in a sensible "local CPU" posture; secrets and infra URLs
    (LiveKit, Groq) must be supplied via the environment.
    """

    # ----------------------------------------------------------------- server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # --------------------------------------------------------- audio pipeline
    # Canonical *inbound* rate used by VAD + STT. The *outbound* TTS stream may
    # run at the Piper voice's native rate instead — see IMPLEMENTATION.md §12.1
    # (recheck R4): the two streams are independent and need not match.
    sample_rate: int = 16000
    vad_frame_samples: int = 512        # Silero requirement @16k (= 32 ms)
    vad_threshold: float = 0.65         # speech-probability interrupt threshold
    vad_min_speech_ms: int = 160        # consecutive speech needed to confirm onset
    vad_silence_ms: int = 600           # trailing silence that ends an utterance
    stt_max_utterance_ms: int = 8000    # hard cap on a buffered utterance (R3)

    # ----------------------------------------------------------------- models
    whisper_model: str = "small.en"
    whisper_compute_type: str = "int8"
    whisper_device: str = "cpu"
    minilm_model: str = "all-MiniLM-L6-v2"
    piper_voice: str = "en_US-lessac-medium"
    piper_quality: str = "medium"       # x_low | low | medium | high
    models_dir: str = "/models"

    # -------------------------------------------------------------------- LLM
    groq_api_key: SecretStr | None = None
    groq_model: str = "llama-3.3-70b-versatile"   # R7: model ids drift over time
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_timeout_s: float = 20.0
    llm_max_answer_tokens: int = 300
    ollama_base_url: str = "http://localhost:11434"   # graceful fallback target
    ollama_model: str = "llama3.1:8b"

    # --------------------------------------------------------------- LiveKit
    livekit_url: str = ""               # wss://...  (required for live sessions)
    livekit_api_key: str = ""
    livekit_api_secret: SecretStr | None = None

    # -------------------------------------------------------------------- RAG
    rag_top_k: int = 5
    rag_max_context_tokens: int = 1200
    rag_current_slide_boost: float = 0.25     # additive score boost for active slide
    rag_chunk_tokens: int = 180
    rag_chunk_overlap: int = 40
    rag_rrf_k: int = 60                       # Reciprocal Rank Fusion constant

    # --------------------------------------------------------------- pacing
    default_persona: str = "GENERAL"
    pacing_drift_summary_pct: float = 0.08    # switch STANDARD->SUMMARY if >8% over
    pacing_drift_turbo_pct: float = 0.18      # switch ->TURBO if >18% over

    # ------------------------------------------------------------ data paths
    data_dir: str = "data"                    # job artifacts root (gitignored)
    max_slides: int = 50                      # functional requirement upper bound
    max_upload_mb: int = 50

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CB_",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance.

    Cached with ``lru_cache`` so configuration is parsed from the environment
    exactly once and shared everywhere (FastAPI ``Depends`` and the agent
    worker both call this). Tests can clear the cache via
    ``get_settings.cache_clear()``.
    """
    return Settings()
