"""
utils/audio.py — Audio format + framing helpers (IMPLEMENTATION.md §8.3, §10, §12).

Pure numpy/scipy utilities shared across the media pipeline:
  * int16 <-> float32 PCM conversion (LiveKit frames are int16; models want f32)
  * resampling (only needed if a strict output rate is enforced — R4)
  * fixed-size re-chunking (Silero VAD needs exactly 512 samples per call — §10)
"""

from __future__ import annotations

import numpy as np

try:
    from scipy.signal import resample_poly
except Exception:  # scipy optional at import time; resample is rarely on the hot path
    resample_poly = None


def int16_to_float32(pcm: np.ndarray) -> np.ndarray:
    """Convert int16 PCM samples to float32 in the range [-1, 1].

    LiveKit ``AudioFrame`` data arrives as int16; Silero/Whisper/Piper all work
    in float32, so this is the standard inbound conversion.
    """
    return (pcm.astype(np.float32)) / 32768.0


def float32_to_int16(audio: np.ndarray) -> np.ndarray:
    """Convert float32 [-1, 1] audio to clipped int16 PCM for the outbound track."""
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16)


def resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Resample float32 ``audio`` from ``src_sr`` to ``dst_sr``.

    Only used if a strict output sample rate is enforced (see R4 — by default we
    avoid this by running the output track at the Piper voice's native rate).
    Falls back to a linear interpolation if scipy is unavailable.
    """
    if src_sr == dst_sr:
        return audio
    if resample_poly is not None:
        g = np.gcd(src_sr, dst_sr)
        return resample_poly(audio, dst_sr // g, src_sr // g).astype(np.float32)
    # Fallback: linear interpolation (lower quality, dependency-free).
    n_dst = int(round(len(audio) * dst_sr / src_sr))
    x_old = np.linspace(0, 1, len(audio), endpoint=False)
    x_new = np.linspace(0, 1, n_dst, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


class Reframer:
    """Re-chunks an arbitrary stream of samples into fixed-size frames.

    Silero VAD requires exactly ``frame_samples`` (512 @ 16 kHz) per inference;
    LiveKit frames are not guaranteed to be that size, so the input loop feeds
    raw samples here and pulls back uniform frames.
    """

    def __init__(self, frame_samples: int):
        """Create a reframer emitting frames of ``frame_samples`` samples each."""
        self.frame_samples = frame_samples
        self._buf = np.empty(0, dtype=np.float32)

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        """Append ``samples`` and return any complete fixed-size frames now available.

        Leftover samples that don't fill a frame are retained for the next call,
        so no audio is dropped across frame boundaries.
        """
        self._buf = np.concatenate([self._buf, samples.astype(np.float32)])
        frames: list[np.ndarray] = []
        while len(self._buf) >= self.frame_samples:
            frames.append(self._buf[: self.frame_samples])
            self._buf = self._buf[self.frame_samples :]
        return frames

    def reset(self) -> None:
        """Discard any buffered partial frame (call on session/stream reset)."""
        self._buf = np.empty(0, dtype=np.float32)
