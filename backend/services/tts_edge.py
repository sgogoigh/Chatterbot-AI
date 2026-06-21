"""
services/tts_edge.py — Edge-TTS backend (WINDOWS_TESTING.md §3.1).

A Windows-friendly TTS that replaces the Linux-only Piper pip package (R12). Uses
Microsoft Edge's free neural voices (`edge-tts`), decodes the returned MP3 to PCM
with `miniaudio`, and exposes the SAME contract every TTS backend must provide:

    native_sr: int
    synthesize_stream(text) -> Iterator[np.float32]   (mono PCM at native_sr)

so it drops straight into ChatterbotAgentWorker / PlaybackTracker with no changes.
edge-tts is async; synthesis here is synchronous because the worker always calls
it inside ``asyncio.to_thread`` (a worker thread with no running loop), where
``asyncio.run`` is safe.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from config import Settings
from utils.audio import int16_to_float32
from utils.logging import get_logger

log = get_logger(component="tts-edge")

# edge-tts streams 24 kHz mono MP3 by default; decode to this rate to avoid resampling.
_EDGE_SR = 24000


class EdgeTTS:
    """Edge neural TTS backend (free, cross-platform; needs internet)."""

    def __init__(self, settings: Settings):
        """Record the configured voice and native sample rate (no network here)."""
        self.voice = settings.edge_tts_voice
        self.native_sr = _EDGE_SR

    def _fetch_mp3(self, text: str) -> bytes:
        """Call edge-tts and return the concatenated MP3 audio bytes for ``text``.

        Runs the async edge-tts client to completion via ``asyncio.run`` — valid
        because the caller is already on a worker thread (no running event loop).
        """
        import asyncio

        import edge_tts

        async def _go() -> bytes:
            buf = bytearray()
            comm = edge_tts.Communicate(text, self.voice)
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            return bytes(buf)

        return asyncio.run(_go())

    def _synth_f32(self, text: str) -> np.ndarray:
        """Synthesize ``text`` to a float32 mono array at ``native_sr``.

        Fetches MP3 from edge-tts and decodes it to 16-bit PCM with miniaudio
        (resampling to ``native_sr`` if needed), then converts to float32 [-1, 1].
        Empty/blank text yields an empty array.
        """
        if not text.strip():
            return np.empty(0, dtype=np.float32)
        import miniaudio

        mp3 = self._fetch_mp3(text)
        if not mp3:
            return np.empty(0, dtype=np.float32)
        decoded = miniaudio.decode(
            mp3,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=self.native_sr,
        )
        pcm = np.array(decoded.samples, dtype=np.int16)
        return int16_to_float32(pcm)

    def synthesize_stream(self, text: str) -> Iterator[np.ndarray]:
        """Yield the synthesized audio in fixed-size float32 chunks.

        edge-tts returns the whole clip at once, so we synthesize then chunk it
        (~100 ms per chunk) to match the streaming-push contract the agent expects
        for low-latency playback + responsive interrupt checks.
        """
        audio = self._synth_f32(text)
        step = self.native_sr // 10  # ~100 ms chunks
        for i in range(0, len(audio), step):
            yield audio[i : i + step]

    async def synthesize(self, text: str) -> np.ndarray:
        """Async convenience: synthesize to a single array off the event loop."""
        import asyncio

        return await asyncio.to_thread(self._synth_f32, text)

    def warmup(self) -> None:
        """Best-effort warm call so the first real synthesis isn't cold (needs net)."""
        try:
            list(self.synthesize_stream("hello"))
        except Exception as e:  # noqa: BLE001 - warmup is best-effort (offline ok)
            log.warning(f"edge-tts warmup skipped: {e}")
