"""
services/tts_factory.py — TTS backend selection (WINDOWS_TESTING.md §3.1).

Returns a TTS object conforming to the shared contract (``native_sr`` +
``synthesize_stream(text)``), chosen by ``settings.tts_backend``:

    auto       -> edge on Windows, piper elsewhere
    edge       -> EdgeTTS (free, cross-platform)            [implemented]
    piper      -> Piper pip package (Linux/container)        [implemented]
    groq       -> Groq Orpheus /audio/speech                 [planned]
    piper_exe  -> bundled piper.exe via subprocess           [planned]
    pyttsx3    -> Windows SAPI5 offline                       [planned]

Keeping selection in one place lets the rest of the app stay backend-agnostic.
"""

from __future__ import annotations

import sys

from config import Settings


def make_tts(settings: Settings):
    """Construct the configured TTS backend instance.

    Resolves ``auto`` by platform, then dispatches to the concrete backend. Raises
    a clear error for backends that aren't implemented yet so misconfiguration
    fails loudly rather than silently producing no audio.
    """
    backend = settings.tts_backend
    if backend == "auto":
        backend = "edge" if sys.platform == "win32" else "piper"

    if backend == "edge":
        from services.tts_edge import EdgeTTS

        return EdgeTTS(settings)
    if backend == "piper":
        from services.tts_service import TTSService

        return TTSService(settings)

    raise ValueError(
        f"TTS backend '{backend}' is not implemented yet. "
        "Use 'edge' (Windows) or 'piper' (Linux/container)."
    )
