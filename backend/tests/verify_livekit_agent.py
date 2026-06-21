"""
tests/verify_livekit_agent.py — Live check that the agent joins a LiveKit room and speaks.

Manual script (NOT a pytest test — it hits LiveKit Cloud + edge-TTS). Proves the
"Go live" dispatch end-to-end for the agent->participant direction:

  1. dispatch the agent via run_agent_session() into a test room
  2. connect a second participant (a stand-in for the browser)
  3. confirm that participant SUBSCRIBES to the agent's published track and
     RECEIVES audio frames (the narration the agent is speaking)

Run:
  CB_MODELS_DIR=../models ../.venv/Scripts/python tests/verify_livekit_agent.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routes_session import _mint_join_token  # noqa: E402
from config import get_settings  # noqa: E402
from core.agent_session import run_agent_session  # noqa: E402
from core.playback_tracker import PlaybackTracker  # noqa: E402
from core.session_state import SessionState  # noqa: E402
from deps import ServiceRegistry  # noqa: E402
from models import Track  # noqa: E402
from run_local import _demo_plan  # noqa: E402


async def main() -> int:
    """Dispatch the agent + a listener participant; verify narration audio arrives."""
    from livekit import rtc

    s = get_settings()
    if not s.livekit_url:
        print("LIVEKIT not configured in .env — skipping")
        return 0

    print("[1] loading models...")
    reg = ServiceRegistry()
    await reg.load_all(s)
    await reg.warmup()

    plan = _demo_plan()
    first = plan.scripts[Track.STANDARD][0].sentences
    tracker = PlaybackTracker(first, out_sr=reg.tts.native_sr, wpm=150)
    state = SessionState(session_id="verify", job_id="demo", plan=plan, tracker=tracker)

    room_name = "chatterbot-verify"
    agent_token = _mint_join_token(s, room_name, "chatterbot-agent")
    user_token = _mint_join_token(s, room_name, "listener")

    print("[2] dispatching agent into room...")
    agent_task = asyncio.create_task(run_agent_session(s, reg, state, room_name, agent_token))

    # Stand-in listener (what the browser does): receive the agent's voice track.
    got = {"track": False, "frames": 0, "agent_seen": False}
    user_room = rtc.Room()

    @user_room.on("participant_connected")
    def _on_join(p):  # noqa: ANN001
        if "agent" in p.identity:
            got["agent_seen"] = True

    @user_room.on("track_subscribed")
    def _on_sub(track, pub, participant):  # noqa: ANN001
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            got["track"] = True

            async def drain():
                stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
                async for _ev in stream:
                    got["frames"] += 1

            asyncio.create_task(drain())

    print("[3] listener joining room...")
    await user_room.connect(s.livekit_url, user_token)

    print("[4] waiting for narration (edge-TTS)...")
    for i in range(20):
        await asyncio.sleep(1)
        if got["frames"] > 0:
            break
        print(f"    t={i + 1}s  agent_seen={got['agent_seen']}  track={got['track']}  frames={got['frames']}")

    print(f"\n    agent_seen={got['agent_seen']}  track_subscribed={got['track']}  audio_frames={got['frames']}")

    agent_task.cancel()
    try:
        await agent_task
    except asyncio.CancelledError:
        pass
    await user_room.disconnect()
    await reg.aclose()

    ok = got["agent_seen"] and got["track"] and got["frames"] > 0
    print(f"\nLIVEKIT AGENT DISPATCH {'PASS' if ok else 'FAIL'} — "
          "agent joined the room and streamed narration to the participant.\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
