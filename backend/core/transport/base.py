"""
core/transport/base.py — Audio transport interface + shared helpers (§3.2).

Defines the duck-typed contract every transport must satisfy. The agent worker
holds ``audio_in`` (an async iterable of float32 frames) and ``audio_out`` (a sink);
a transport object typically serves as both.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class AudioSink(Protocol):
    """Outbound audio target (what the worker writes synthesized speech to)."""

    async def play(self, samples: np.ndarray, sample_rate: int) -> None:
        """Enqueue/play one float32 mono chunk at ``sample_rate``."""

    async def stop(self) -> None:
        """Flush any buffered/queued audio immediately (barge-in)."""


@runtime_checkable
class AudioTransport(Protocol):
    """Full transport: async-iterable input frames + an output sink + lifecycle."""

    async def start(self) -> None:
        """Open devices/streams; called before iteration/playback begins."""

    def __aiter__(self) -> AsyncIterator[np.ndarray]:
        """Iterate inbound float32 mono frames at the canonical 16 kHz."""

    async def play(self, samples: np.ndarray, sample_rate: int) -> None:
        """Play one outbound float32 chunk (sink half)."""

    async def stop(self) -> None:
        """Barge-in: drop queued outbound audio."""

    async def aclose(self) -> None:
        """Release devices/streams on shutdown."""
