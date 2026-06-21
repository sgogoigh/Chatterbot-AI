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

from pydantic import AliasChoices, Field, SecretStr
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
    # Default to a project-local cache so it works on Windows out of the box
    # (the container overrides this to /models via env).
    models_dir: str = "models"

    # --------------------------------------------------------------- TTS / STT
    # Pluggable backends so the pipeline runs on Windows where Piper (pip) won't
    # install (R12). "auto" => edge on Windows, piper elsewhere. See WINDOWS_TESTING.md.
    tts_backend: str = "auto"           # auto | edge | groq | piper | piper_exe | pyttsx3
    edge_tts_voice: str = "en-US-AriaNeural"
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "auto"
    piper_exe_path: str = ""            # path to piper.exe when tts_backend=piper_exe
    stt_backend: str = "local"          # local (faster-whisper) | groq (whisper-large-v3-turbo)
    groq_stt_model: str = "whisper-large-v3-turbo"

    # -------------------------------------------------------------------- LLM
    # Accept both the CB_-prefixed name AND the conventional GROQ_API_KEY so an
    # existing .env (e.g. from the Groq/LiveKit quickstarts) works unchanged.
    groq_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("CB_GROQ_API_KEY", "GROQ_API_KEY")
    )
    groq_model: str = Field(                       # R7: model ids drift over time
        default="llama-3.3-70b-versatile",
        validation_alias=AliasChoices("CB_GROQ_MODEL", "GROQ_MODEL"),
    )
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_timeout_s: float = 20.0
    llm_max_answer_tokens: int = 300
    ollama_base_url: str = "http://localhost:11434"   # graceful fallback target
    ollama_model: str = "llama3.1:8b"

    # --------------------------------------------------------------- LiveKit
    livekit_url: str = Field(
        default="", validation_alias=AliasChoices("CB_LIVEKIT_URL", "LIVEKIT_URL")
    )
    livekit_api_key: str = Field(
        default="", validation_alias=AliasChoices("CB_LIVEKIT_API_KEY", "LIVEKIT_API_KEY")
    )
    livekit_api_secret: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("CB_LIVEKIT_API_SECRET", "LIVEKIT_API_SECRET")
    )

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
        env_prefix="CB_",
        extra="ignore",
        case_sensitive=False,
    )


def _find_env_files() -> list[str]:
    """Return candidate .env paths, lowest- to highest-priority (later wins).

    Looks for the repo-root ``.env`` (next to this package's parent) and a
    ``backend/.env`` override, plus the CWD ``.env``. This makes config load the
    same regardless of whether the process starts from the repo root or backend/
    (the .env the user created lives at the repo root).
    """
    import os

    here = os.path.dirname(os.path.abspath(__file__))      # .../backend
    repo_root = os.path.dirname(here)                       # .../Chatterbot-AI
    candidates = [
        os.path.join(repo_root, ".env"),                   # repo-root .env (user's)
        os.path.join(here, ".env"),                        # backend/.env override
        ".env",                                            # CWD .env
    ]
    return [p for p in candidates if os.path.exists(p)]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance.

    Cached with ``lru_cache`` so configuration is parsed from the environment
    exactly once and shared everywhere (FastAPI ``Depends`` and the agent
    worker both call this). The .env file is located robustly via
    :func:`_find_env_files` so it loads from the repo root regardless of CWD.
    Tests can clear the cache via ``get_settings.cache_clear()``.
    """
    return Settings(_env_file=_find_env_files() or None)
