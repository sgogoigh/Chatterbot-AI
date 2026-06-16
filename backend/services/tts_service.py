"""
services/tts_service.py — Piper text-to-speech (IMPLEMENTATION.md §12).

Synthesizes narration + answer audio. Key points:
  * Piper VITS voice (default en_US-lessac-medium). Runs on CPU.
  * Blocking synthesis is pushed off the event loop with ``asyncio.to_thread``.
  * Streaming: ``synthesize_stream`` yields chunks so the agent can push audio to
    LiveKit as it is produced (low time-to-first-audio, §12.2).

⚠️ R4 (sample-rate): lessac-medium is 22 050 Hz, NOT 16 kHz. We expose the voice's
native rate via ``native_sr`` and the agent creates its outbound AudioSource at
THAT rate. The inbound VAD/STT path stays at 16 kHz; the two streams are
independent. Only resample if a strict output rate is ever mandated.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import numpy as np

from config import Settings
from utils.audio import int16_to_float32


class TTSService:
    """Piper TTS wrapper producing float32 mono audio at the voice's native rate."""

    def __init__(self, settings: Settings):
        """Load the Piper voice and record its native sample rate.

        The voice's sample rate (``native_sr``) is authoritative for the outbound
        stream (R4). Missing voice assets are expected to be pre-downloaded into
        ``models_dir``; production deployments cache them on the models volume.
        The exact PiperVoice API is version sensitive (recheck R1); adjust
        ``_synth_sync`` if the pinned piper-tts version changes its interface.
        """
        from piper import PiperVoice

        self.settings = settings
        self.voice = PiperVoice.load(self._voice_path(settings))
        # PiperVoice exposes its config sample rate; fall back to 22050 if absent.
        self.native_sr = getattr(getattr(self.voice, "config", None), "sample_rate", 22050)

    def _voice_path(self, settings: Settings) -> str:
        """Resolve the on-disk path to the configured Piper ``.onnx`` voice model."""
        return f"{settings.models_dir}/piper/{settings.piper_voice}.onnx"

    async def synthesize(self, text: str) -> np.ndarray:
        """Synthesize ``text`` to a single float32 mono array (off the event loop).

        Convenience for non-streaming callers (e.g. tests). The agent's hot path
        prefers :meth:`synthesize_stream` for lower latency.
        """
        return await asyncio.to_thread(self._synth_sync, text)

    def _synth_sync(self, text: str) -> np.ndarray:
        """Blocking full synthesis; concatenates Piper's int16 chunks into float32 PCM.

        Piper yields audio in chunks; here we collect them all. ``synthesize_stream``
        exposes the same chunks incrementally for streaming playback.
        """
        return np.concatenate(list(self._iter_f32(text))) if text.strip() else np.empty(0, np.float32)

    def synthesize_stream(self, text: str) -> Iterator[np.ndarray]:
        """Yield float32 audio chunks as Piper produces them (for low-latency push).

        The agent worker pushes each chunk to the LiveKit AudioSource immediately,
        minimising time-to-first-audio. This is a *blocking generator*; the agent
        drives it from a worker thread / executor as needed.
        """
        yield from self._iter_f32(text)

    def _iter_f32(self, text: str) -> Iterator[np.ndarray]:
        """Adapt Piper's chunk output to float32 numpy arrays.

        Piper versions differ in chunk type (raw int16 bytes vs objects carrying
        ``audio_int16_bytes``); this normalises both to float32 in [-1, 1].
        """
        for chunk in self.voice.synthesize_stream_raw(text):
            raw = getattr(chunk, "audio_int16_bytes", chunk)  # bytes either way
            pcm = np.frombuffer(raw, dtype=np.int16)
            yield int16_to_float32(pcm)

    def warmup(self) -> None:
        """Synthesize a tiny phrase so the first real synthesis isn't cold (§6)."""
        try:
            self._synth_sync("hello")
        except Exception:
            # Warmup is best-effort; a missing voice asset surfaces at first real use.
            pass
