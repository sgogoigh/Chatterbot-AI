"""
core/transport/livekit_transport.py — LiveKit WebRTC transport (§3.2, W3).

The ONLY module that imports `livekit.rtc`. Adapts a connected LiveKit room to the
agent's audio contract: inbound participant mic → float32 16 kHz frames; outbound
synthesized chunks → rtc.AudioFrame on a published track; barge-in → clear_queue.

⚠️ This is the version-sensitive seam (R5). It is exercised/finalized in W3
(real LiveKit Cloud + a browser/Playground client); the local/file transports do
not touch it. Kept importable so the FastAPI agent bootstrap works when LiveKit is
configured.
"""

from __future__ import annotations

import asyncio

import numpy as np

from utils.audio import float32_to_int16, int16_to_float32
from utils.logging import get_logger

log = get_logger(component="livekit-transport")


class LiveKitTransport:
    """Bridges a LiveKit room to the agent's input-iterator / output-sink contract."""

    def __init__(self, ctx, in_sr: int = 16000, out_sr: int = 24000):
        """Bind to a connected LiveKit JobContext; record in/out sample rates."""
        self.ctx = ctx
        self.in_sr = in_sr
        self.out_sr = out_sr
        self._source = None        # rtc.AudioSource (outbound)
        self._stream = None        # rtc.AudioStream (inbound)

    async def start(self) -> None:
        """Publish the agent's output track and subscribe to the participant's mic.

        Waits briefly for a remote audio track to appear (single-user assumption).
        The exact subscription/event API is version-sensitive — validate in W3.
        """
        from livekit import rtc

        self._source = rtc.AudioSource(self.out_sr, 1)
        track = rtc.LocalAudioTrack.create_audio_track("chatterbot", self._source)
        await self.ctx.room.local_participant.publish_track(track)

        remote = await self._await_remote_audio_track()
        self._stream = rtc.AudioStream(remote, sample_rate=self.in_sr, num_channels=1)
        log.info("livekit transport started")

    async def _await_remote_audio_track(self, timeout: float = 30.0):
        """Return the first remote audio track, waiting up to ``timeout`` for one."""
        deadline = timeout
        while deadline > 0:
            for p in self.ctx.room.remote_participants.values():
                for pub in p.track_publications.values():
                    if pub.track is not None:
                        return pub.track
            await asyncio.sleep(0.25)
            deadline -= 0.25
        raise TimeoutError("no remote audio track appeared")

    def __aiter__(self):
        """Return an async iterator of inbound float32 16 kHz frames."""
        return self._iter_frames()

    async def _iter_frames(self):
        """Yield float32 mono frames decoded from the inbound rtc AudioStream."""
        async for event in self._stream:
            frame = getattr(event, "frame", event)        # AudioFrameEvent.frame or frame
            pcm = np.frombuffer(frame.data, dtype=np.int16)
            yield int16_to_float32(pcm)

    async def play(self, samples: np.ndarray, sample_rate: int) -> None:
        """Push one float32 chunk to the published track as an rtc.AudioFrame."""
        from livekit import rtc

        frame = rtc.AudioFrame(
            data=float32_to_int16(samples).tobytes(),
            sample_rate=sample_rate, num_channels=1, samples_per_channel=len(samples),
        )
        await self._source.capture_frame(frame)

    async def stop(self) -> None:
        """Barge-in: drop queued outbound audio on the source."""
        if self._source is not None:
            await self._source.clear_queue()

    async def aclose(self) -> None:
        """No-op; the room lifecycle is owned by the LiveKit job context."""
