"""
services/stt_factory.py — STT backend selection (WINDOWS_TESTING.md §3).

Selects the STT implementation by ``settings.stt_backend``:
    local -> Faster-Whisper small.en on CPU (default; offline, fast)
    groq  -> Groq whisper-large-v3-turbo (cloud; higher accuracy, needs key+net)

Both expose the same async ``transcribe(audio_f32_16k) -> str`` contract.
"""

from __future__ import annotations

from config import Settings


def make_stt(settings: Settings):
    """Construct the configured STT backend instance."""
    if settings.stt_backend == "groq":
        from services.stt_groq import GroqSTTService

        return GroqSTTService(settings)
    from services.stt_service import STTService

    return STTService(settings)
