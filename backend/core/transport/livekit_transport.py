"""
core/transport/livekit_transport.py — LiveKit WebRTC transport (W3 live voice).

The ONLY module that imports `livekit.rtc`. Bridges a connected LiveKit room to
the agent's audio contract:
  * INPUT  — the remote participant's mic track -> float32 mono 16 kHz frames
  * OUTPUT — synthesized chunks -> an rtc.AudioFrame on the agent's published track
  * stop() — barge-in: clears the AudioSource queue (drops buffered TTS at once)

The room is created/connected/published by core/agent_session.py; this class is
fed the room + output AudioSource and is told which remote track to read once it
is subscribed (event-driven, since the user may join before or after the agent).
"""

from __future__ import annotations

import asyncio

import numpy as np

from utils.audio import float32_to_int16, int16_to_float32
from utils.logging import get_logger

log = get_logger(component="livekit-transport")


class LiveKitRoomTransport:
    """Adapts a live rtc.Room (+ output AudioSource) to the agent's I/O contract."""

    def __init__(self, room, source, in_sr: int, out_sr: int):
        """Bind to a connected room and the agent's published AudioSource.

        ``in_sr`` is the canonical VAD/STT rate (16 kHz); ``out_sr`` is the TTS
        native rate. The remote mic track is supplied later via
        :meth:`set_input_track` when the room fires ``track_subscribed``.
        """
        self.room = room
        self.source = source
        self.in_sr = in_sr
        self.out_sr = out_sr
        self._track = None
        self._track_ready = asyncio.Event()

    def set_input_track(self, track) -> None:
        """Register the remote audio track to listen on (called from the room event)."""
        if self._track is None:
            self._track = track
            self._track_ready.set()
            log.info("input track attached")

    def __aiter__(self):
        """Return the async generator of inbound float32 16 kHz frames."""
        return self._frames()

    async def _frames(self):
        """Yield float32 mono frames from the remote mic, once a track is available.

        Waits for the first subscribed audio track, then streams it via
        rtc.AudioStream resampled to ``in_sr``. The agent can narrate (output)
        while this is still waiting for the user to speak.
        """
        await self._track_ready.wait()
        from livekit import rtc

        stream = rtc.AudioStream(self._track, sample_rate=self.in_sr, num_channels=1)
        async for event in stream:
            frame = getattr(event, "frame", event)
            pcm = np.frombuffer(frame.data, dtype=np.int16)
            yield int16_to_float32(pcm)

    async def play(self, samples: np.ndarray, sample_rate: int) -> None:
        """Push one float32 chunk to the agent's published track as an rtc.AudioFrame."""
        from livekit import rtc

        frame = rtc.AudioFrame(
            data=float32_to_int16(samples).tobytes(),
            sample_rate=sample_rate,
            num_channels=1,
            samples_per_channel=len(samples),
        )
        await self.source.capture_frame(frame)

    async def stop(self) -> None:
        """Barge-in: drop queued outbound audio so playback halts immediately."""
        self.source.clear_queue()

    async def aclose(self) -> None:
        """Release the output source (room disconnect is handled by the session)."""
        try:
            await self.source.aclose()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
