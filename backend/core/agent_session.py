"""
core/agent_session.py — Join a LiveKit room as the agent and run the voice loop (W3).

This is the dispatch glue that makes the frontend's "Go live" complete end-to-end.
Instead of the heavyweight LiveKit Agents worker/job-dispatch system, the backend
directly joins the session's room as a second participant ("chatterbot-agent")
using the rtc SDK — fully in-process and controllable.

Flow per session:
  1. create rtc.Room, publish an AudioSource track (the agent's voice)
  2. on `track_subscribed`, hand the user's mic track to the transport
  3. connect to the room with the agent token (auto-subscribe on)
  4. run ChatterbotAgentWorker over a LiveKitRoomTransport until done/cancelled
  5. disconnect + clean up

The agent narrates immediately on connect; it begins listening as soon as the
user's mic track is subscribed.
"""

from __future__ import annotations

import asyncio

from config import Settings
from core.agent_worker import ChatterbotAgentWorker
from core.session_state import SessionState
from core.transport.livekit_transport import LiveKitRoomTransport
from utils.logging import get_logger


async def run_agent_session(
    settings: Settings,
    registry,
    state: SessionState,
    room_name: str,
    agent_token: str,
) -> None:
    """Connect the agent to ``room_name`` and run the full-duplex loop to completion.

    Designed to run as a background task (one per live session). Cancellation
    (session stop / shutdown) disconnects the room cleanly. All exceptions are
    logged rather than propagated so a failed voice session never crashes the API.
    """
    from livekit import rtc

    log = get_logger(session_id=state.session_id, component="agent-session")
    room = rtc.Room()
    source = rtc.AudioSource(registry.tts.native_sr, 1)
    transport = LiveKitRoomTransport(
        room, source, in_sr=settings.sample_rate, out_sr=registry.tts.native_sr
    )

    # When the user's mic track is subscribed, feed it to the transport's input.
    @room.on("track_subscribed")
    def _on_track_subscribed(track, publication, participant):  # noqa: ANN001
        """Attach the first remote audio track as the agent's listening input."""
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            transport.set_input_track(track)

    try:
        await room.connect(settings.livekit_url, agent_token)
        log.info(f"agent connected to room '{room_name}'")

        # Publish the agent's voice track (carries TTS narration + answers).
        agent_track = rtc.LocalAudioTrack.create_audio_track("chatterbot-voice", source)
        await room.local_participant.publish_track(agent_track, rtc.TrackPublishOptions())

        # If the user already joined before us, attach their existing audio track.
        for participant in room.remote_participants.values():
            for pub in participant.track_publications.values():
                if pub.track is not None and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    transport.set_input_track(pub.track)

        worker = ChatterbotAgentWorker(ctx=None, registry=registry, settings=settings, state=state)
        worker.audio_in = transport
        worker.audio_out = transport
        await worker.run()
        log.info("agent session finished (presentation complete)")
    except asyncio.CancelledError:
        log.info("agent session cancelled")
        raise
    except Exception as e:  # noqa: BLE001 - never crash the API over a voice session
        log.error(f"agent session error: {e}")
    finally:
        await transport.aclose()
        try:
            await room.disconnect()
        except Exception:  # noqa: BLE001
            pass
