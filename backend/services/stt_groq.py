"""
services/stt_groq.py — Cloud STT via Groq Whisper (WINDOWS_TESTING.md §3, R-stt).

A drop-in alternative to the local Faster-Whisper backend: posts the utterance to
Groq's OpenAI-compatible /audio/transcriptions endpoint (whisper-large-v3-turbo).
Much more accurate than the local small.en model (it resolves clips small.en
mis-hears), at the cost of a network round-trip + per-minute pricing.

Same async ``transcribe(audio_f32_16k) -> str`` contract as STTService, so it
swaps in via the factory with no caller changes.
"""

from __future__ import annotations

import io

import numpy as np

from config import Settings
from utils.logging import get_logger

log = get_logger(component="stt-groq")


class GroqSTTService:
    """Speech-to-text via Groq's hosted Whisper (OpenAI-compatible endpoint)."""

    def __init__(self, settings: Settings):
        """Hold config + an async HTTP client; require a Groq API key."""
        import httpx

        self.s = settings
        self._client = httpx.AsyncClient(timeout=settings.llm_timeout_s)
        self._key = settings.groq_api_key.get_secret_value() if settings.groq_api_key else ""

    async def transcribe(self, audio_f32_16k: np.ndarray) -> str:
        """Transcribe a float32 mono 16 kHz utterance via Groq Whisper.

        Encodes the audio to an in-memory WAV and POSTs it as multipart form data.
        Returns the transcript text (empty string on any failure, so a transient
        cloud error degrades gracefully rather than breaking the turn).
        """
        import soundfile as sf

        if audio_f32_16k.size == 0:
            return ""
        buf = io.BytesIO()
        sf.write(buf, audio_f32_16k, self.s.sample_rate, format="WAV", subtype="PCM_16")
        buf.seek(0)
        try:
            resp = await self._client.post(
                f"{self.s.groq_base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._key}"},
                files={"file": ("utterance.wav", buf, "audio/wav")},
                data={"model": self.s.groq_stt_model, "language": "en",
                      "response_format": "json"},
            )
            resp.raise_for_status()
            return resp.json().get("text", "").strip()
        except Exception as e:  # noqa: BLE001 - never break a turn on a cloud hiccup
            log.warning(f"Groq STT failed: {e}")
            return ""

    def warmup(self) -> None:
        """No-op (cloud call; nothing to warm locally)."""

    async def aclose(self) -> None:
        """Close the HTTP client on shutdown."""
        await self._client.aclose()
