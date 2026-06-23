"""
tests/verify_mic_to_agent.py — Verify the PARTICIPANT-MIC -> AGENT direction (S14b).

The companion verify_livekit_agent.py proves agent -> participant (narration). This
proves the reverse, which was never exercised live: a participant publishes a real
voice clip as their mic, and we confirm the agent's LiveKit transport actually
delivers those frames to the input loop, the real Silero VAD segments an utterance,
and Faster-Whisper transcribes it.

It isolates the transport + VAD + STT integration over real WebRTC (LiveKit Cloud),
printing per-stage counters so a break is obvious:
    frames_received == 0      -> subscription/transport bug (agent never hears mic)
    frames > 0, no utterance  -> VAD threshold / framing bug
    utterance, empty text     -> resampling / STT feeding bug

Run:
  CB_MODELS_DIR=../models ../.venv/Scripts/python tests/verify_mic_to_agent.py
"""

from __future__ import annotations

import asyncio
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_settings  # noqa: E402
from core.transport.livekit_transport import LiveKitRoomTransport  # noqa: E402
from deps import ServiceRegistry  # noqa: E402

CLIP = os.path.join(os.path.dirname(__file__), "..", "..", "test_assets", "clips", "q_revenue.m4a")


def mint(settings, room: str, identity: str) -> str:
    """Mint a publish+subscribe join token (same grants the app uses)."""
    from livekit import api

    secret = settings.livekit_api_secret.get_secret_value() if settings.livekit_api_secret else ""
    return (
        api.AccessToken(settings.livekit_api_key, secret)
        .with_identity(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .to_jwt()
    )


def load_clip(path: str, sr: int = 16000) -> np.ndarray:
    """Decode an .m4a clip to int16 mono at ``sr`` (PyAV / AAC)."""
    import av

    container = av.open(path)
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=sr)
    out: list[np.ndarray] = []
    for frame in container.decode(audio=0):
        for r in resampler.resample(frame):
            out.append(r.to_ndarray().reshape(-1))
    for r in resampler.resample(None):
        out.append(r.to_ndarray().reshape(-1))
    container.close()
    return np.concatenate(out).astype(np.int16) if out else np.zeros(1, np.int16)


async def main() -> int:
    from livekit import rtc

    s = get_settings()
    if not s.livekit_url:
        print("LIVEKIT_* not set in .env — cannot run live verification.")
        return 1

    print("[loading models]")
    reg = ServiceRegistry()
    await reg.load_all(s)
    await reg.warmup()

    room_name = "verify-mic-to-agent"
    pcm16 = load_clip(CLIP, s.sample_rate)
    src_amp = float(np.abs(pcm16.astype(np.float32) / 32768.0).mean())
    print(f"[clip] {os.path.basename(CLIP)}  {len(pcm16) / s.sample_rate:.1f}s  mean|amp|={src_amp:.4f}\n")

    # ---- AGENT side: connect, subscribe, feed the transport into VAD+STT ----
    agent_room = rtc.Room()
    out_source = rtc.AudioSource(reg.tts.native_sr, 1)
    transport = LiveKitRoomTransport(agent_room, out_source, in_sr=s.sample_rate, out_sr=reg.tts.native_sr)

    @agent_room.on("track_subscribed")
    def _on_sub(track, pub, participant):  # noqa: ANN001
        print(f"[agent] track_subscribed kind={track.kind} from={participant.identity}")
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            transport.set_input_track(track)

    await agent_room.connect(s.livekit_url, mint(s, room_name, "chatterbot-agent"))
    print("[agent] connected")

    # ---- USER side: connect and publish the clip as a mic track ----
    user_room = rtc.Room()
    await user_room.connect(s.livekit_url, mint(s, room_name, "user"))
    mic_source = rtc.AudioSource(s.sample_rate, 1)
    mic_track = rtc.LocalAudioTrack.create_audio_track("user-mic", mic_source)
    await user_room.local_participant.publish_track(mic_track, rtc.TrackPublishOptions())
    print("[user] mic track published")

    # Drive VAD+STT off the agent transport for a bounded window.
    from core.agent_worker import SpeechBuffer
    from utils.audio import Reframer

    reg.vad.reset()
    buf = SpeechBuffer(s)
    reframer = Reframer(s.vad_frame_samples)
    stats = {"frames": 0, "peak": 0.0, "utterances": [], "rx_amp": 0.0, "samps": 0, "rx": []}

    async def consume() -> None:
        async for samples in transport:
            stats["frames"] += 1
            stats["samps"] += len(samples)
            stats["rx"].append(np.asarray(samples, dtype=np.float32))
            stats["rx_amp"] = max(stats["rx_amp"], float(np.abs(samples).mean()))
            for chunk in reframer.push(samples):
                prob = reg.vad.probability(chunk)
                stats["peak"] = max(stats["peak"], prob)
                buf.update(chunk, prob)
                if buf.utterance_complete():
                    audio = buf.take()
                    text = await reg.stt.transcribe(audio)
                    stats["utterances"].append(text)
                    print(f"[agent] utterance captured ({audio.size} samp) -> {text!r}")

    consumer = asyncio.create_task(consume())

    # Push the clip in 20 ms chunks at ~real time, then a tail of silence so the
    # VAD sees trailing silence and closes the utterance.
    step = int(s.sample_rate * 0.02)
    for i in range(0, len(pcm16), step):
        await mic_source.capture_frame(
            rtc.AudioFrame(
                data=pcm16[i : i + step].tobytes(),
                sample_rate=s.sample_rate,
                num_channels=1,
                samples_per_channel=len(pcm16[i : i + step]),
            )
        )
        await asyncio.sleep(0.02)
    silence = np.zeros(step, dtype=np.int16)
    for _ in range(40):  # ~0.8 s trailing silence
        await mic_source.capture_frame(
            rtc.AudioFrame(data=silence.tobytes(), sample_rate=s.sample_rate, num_channels=1, samples_per_channel=step)
        )
        await asyncio.sleep(0.02)

    await asyncio.sleep(1.0)
    consumer.cancel()

    # --- isolation: run the SAME VAD directly over (a) the source clip and
    #     (b) the audio actually received over the wire ---
    def vad_peak(audio_f32: np.ndarray) -> float:
        reg.vad.reset()
        rf = Reframer(s.vad_frame_samples)
        peak = 0.0
        for fr in rf.push(audio_f32):
            peak = max(peak, reg.vad.probability(fr))
        return peak

    src_f32 = pcm16.astype(np.float32) / 32768.0
    src_peak = vad_peak(src_f32)
    rx_f32 = np.concatenate(stats["rx"]) if stats["rx"] else np.zeros(1, np.float32)
    rx_peak = vad_peak(rx_f32)

    print("\n" + "=" * 70)
    print(f"frames_received = {stats['frames']}  ({stats['samps']} samples)")
    print(f"rx_mean|amp|    = {stats['rx_amp']:.4f}  (source clip {src_amp:.4f})")
    print(f"rx samples[:6]  = {np.round(rx_f32[8000:8006], 4)}")
    print(f"VAD peak on SOURCE clip   = {src_peak:.3f}")
    print(f"VAD peak on RECEIVED audio = {rx_peak:.3f}")
    print(f"peak_vad_prob (streaming) = {stats['peak']:.3f}  (threshold {s.vad_threshold})")
    print(f"utterances      = {stats['utterances']}")
    ok = stats["frames"] > 0 and any(t.strip() for t in stats["utterances"])
    print("MIC->AGENT " + ("PASS" if ok else "FAIL"))
    print("=" * 70)

    await transport.aclose()
    await user_room.disconnect()
    await agent_room.disconnect()
    await reg.aclose()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
