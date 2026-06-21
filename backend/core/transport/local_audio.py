"""
core/transport/local_audio.py — Mic + speakers transport via sounddevice (§3.2, W2).

The Windows "talk to your laptop" transport: captures the microphone as float32
mono frames at 16 kHz (for VAD/STT) and plays synthesized speech to the speakers,
with an instant flush on barge-in (``stop``). No LiveKit, no browser, no Docker.

Design:
  * INPUT  — a sounddevice InputStream callback pushes mic blocks onto a thread-safe
             queue; the async iterator drains it and resamples to 16 kHz if the
             device can't open at 16 kHz natively.
  * OUTPUT — a sounddevice OutputStream callback pulls from an output queue; ``play``
             appends a chunk, ``stop`` clears the queue (drops buffered TTS at once).
"""

from __future__ import annotations

import asyncio
import queue
import threading

import numpy as np

from utils.audio import resample
from utils.logging import get_logger

log = get_logger(component="local-audio")


class LocalAudioTransport:
    """Microphone-in / speaker-out audio transport for local Windows testing."""

    def __init__(self, in_sr: int = 16000, out_sr: int = 24000, block_ms: int = 32):
        """Configure the canonical input rate, output rate, and mic block size.

        ``out_sr`` should match the TTS backend's ``native_sr`` (edge-tts = 24 kHz)
        so no output resampling is needed. ``block_ms`` is the mic callback block.
        """
        self.in_sr = in_sr
        self.out_sr = out_sr
        self.block_ms = block_ms
        self._in_q: queue.Queue[np.ndarray] = queue.Queue()
        self._out_q: queue.Queue[np.ndarray] = queue.Queue()
        self._out_buf = np.empty(0, dtype=np.float32)
        self._out_lock = threading.Lock()
        self._in_stream = None
        self._out_stream = None
        self._device_in_sr = in_sr
        self._closed = False

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Open the mic input and speaker output streams.

        Tries to open the mic at 16 kHz; if the device rejects it, opens at the
        device default rate and resamples per block. Output opens at ``out_sr``.
        """
        import sounddevice as sd

        # --- input (mic) ---
        try:
            self._device_in_sr = self.in_sr
            self._in_stream = sd.InputStream(
                samplerate=self.in_sr, channels=1, dtype="float32",
                blocksize=int(self.in_sr * self.block_ms / 1000),
                callback=self._on_mic,
            )
            self._in_stream.start()
        except Exception as e:  # noqa: BLE001 - fall back to device default rate
            log.warning(f"mic at {self.in_sr}Hz failed ({e}); using device default + resample")
            self._device_in_sr = int(sd.query_devices(kind="input")["default_samplerate"])
            self._in_stream = sd.InputStream(
                samplerate=self._device_in_sr, channels=1, dtype="float32",
                callback=self._on_mic,
            )
            self._in_stream.start()

        # --- output (speakers) ---
        self._out_stream = sd.OutputStream(
            samplerate=self.out_sr, channels=1, dtype="float32", callback=self._on_speaker
        )
        self._out_stream.start()
        log.info(f"local audio started (mic {self._device_in_sr}Hz -> {self.in_sr}Hz, out {self.out_sr}Hz)")

    async def aclose(self) -> None:
        """Stop and close both audio streams."""
        self._closed = True
        for st in (self._in_stream, self._out_stream):
            try:
                if st is not None:
                    st.stop()
                    st.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- callbacks
    def _on_mic(self, indata, frames, time_info, status) -> None:
        """sounddevice input callback: push a mono float32 block (resampled to 16 kHz)."""
        block = indata[:, 0].copy()
        if self._device_in_sr != self.in_sr:
            block = resample(block, self._device_in_sr, self.in_sr)
        self._in_q.put(block)

    def _on_speaker(self, outdata, frames, time_info, status) -> None:
        """sounddevice output callback: fill the device buffer from queued TTS audio.

        Pulls from the output queue into a rolling buffer and writes exactly
        ``frames`` samples, zero-padding when there's nothing to play. ``stop``
        clears both the queue and the rolling buffer for an instant barge-in.
        """
        with self._out_lock:
            while len(self._out_buf) < frames and not self._out_q.empty():
                self._out_buf = np.concatenate([self._out_buf, self._out_q.get()])
            if len(self._out_buf) >= frames:
                outdata[:, 0] = self._out_buf[:frames]
                self._out_buf = self._out_buf[frames:]
            else:
                n = len(self._out_buf)
                outdata[:n, 0] = self._out_buf
                outdata[n:, 0] = 0.0
                self._out_buf = np.empty(0, dtype=np.float32)

    # ------------------------------------------------------------- input side
    def __aiter__(self):
        """Return self as the async iterator of inbound 16 kHz float32 frames."""
        return self

    async def __anext__(self) -> np.ndarray:
        """Yield the next mic block, awaiting (without blocking the loop) for audio."""
        while not self._closed:
            try:
                return self._in_q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.005)
        raise StopAsyncIteration

    # ------------------------------------------------------------ output side
    async def play(self, samples: np.ndarray, sample_rate: int) -> None:
        """Queue a float32 chunk for playback (resampling to the output rate if needed)."""
        if sample_rate != self.out_sr:
            samples = resample(samples, sample_rate, self.out_sr)
        self._out_q.put(samples.astype(np.float32))

    async def stop(self) -> None:
        """Barge-in: drop all queued + buffered output audio immediately."""
        with self._out_lock:
            self._out_buf = np.empty(0, dtype=np.float32)
        while not self._out_q.empty():
            try:
                self._out_q.get_nowait()
            except queue.Empty:
                break
