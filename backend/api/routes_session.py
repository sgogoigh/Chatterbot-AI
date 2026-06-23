"""
api/routes_session.py — Session lifecycle, LiveKit token, navigation, status (§7, §8).

  * POST /api/sessions             — create a session, build SessionState from the
                                     job's PacingPlan, mint a LiveKit join token.
  * POST /api/sessions/{id}/navigate — manual NEXT/PREV/GOTO (routes through the
                                     SAME handler as voice intents, §7.2).
  * POST /api/sessions/{id}/control  — pause / resume / stop.
  * GET  /api/sessions/{id}/status   — phase / slide / drift.

Note: the live media loop runs in the in-process LiveKit agent worker (§8.2); this
router manages the control-plane state and hands the browser its join token.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from app_state import jobs, sessions
from core.agent_session import run_agent_session
from config import get_settings
from core.pacing import PERSONA_PROFILES
from core.playback_tracker import PlaybackTracker
from core.session_state import SessionState
from models import (
    ControlRequest,
    Intent,
    NavigateRequest,
    SessionPhase,
    StartSessionRequest,
    StartSessionResponse,
    StatusResponse,
    Track,
)

router = APIRouter(prefix="/api/sessions", tags=["session"])


def _mint_join_token(settings, room: str, identity: str) -> str:
    """Mint a LiveKit participant join JWT for ``room`` (IMPLEMENTATION.md §8.1).

    Grants publish + subscribe so the browser can stream mic audio and hear the
    agent. Isolated here so the (version-sensitive) livekit-api call has one home.
    """
    from livekit import api

    secret = settings.livekit_api_secret.get_secret_value() if settings.livekit_api_secret else ""
    return (
        api.AccessToken(settings.livekit_api_key, secret)
        .with_identity(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True))
        .to_jwt()
    )


@router.post("", response_model=StartSessionResponse)
async def start_session(body: StartSessionRequest, request: Request) -> StartSessionResponse:
    """Create a live session, dispatch the voice agent into its room, and return
    the browser's LiveKit join details.

    Enforces the single-concurrent-presentation constraint (doc), builds the
    initial SessionState, mints the user's join token, and launches the agent
    (`run_agent_session`) as a background task so it joins the SAME room and runs
    the full-duplex loop. When the browser then connects with its token and
    publishes the mic, the loop completes end-to-end.
    """
    settings = get_settings()
    job = jobs.get(body.job_id)
    if job is None or job.plan is None:
        raise HTTPException(status_code=409, detail="job not built yet")
    # Single-presentation constraint: a new presentation supersedes any prior one.
    # Sessions are only freed explicitly via control(stop), so a refreshed/closed
    # tab or a crashed agent task would otherwise orphan a session and lock out all
    # future ones. Reclaim them here (cancels their agent tasks) instead of 409-ing.
    for stale_id in list(sessions.active_ids()):
        sessions.remove(stale_id)

    session_id = uuid.uuid4().hex[:12]
    room_name = body.room_name or f"chatterbot-{session_id}"

    # Build the tracker for slide 0 on the STANDARD track.
    std_scripts = job.plan.scripts.get(Track.STANDARD, [])
    first = std_scripts[0].sentences if std_scripts else []
    wpm = PERSONA_PROFILES[job.plan.persona].wpm
    tracker = PlaybackTracker(first, out_sr=settings.sample_rate, wpm=wpm)

    state = SessionState(
        session_id=session_id, job_id=body.job_id, plan=job.plan, tracker=tracker,
        phase=SessionPhase.READY, current_track=Track.STANDARD,
    )
    sessions.put(session_id, state)

    user_token = _mint_join_token(settings, room_name, identity="user")

    # Dispatch the agent into the room (only when LiveKit is configured + models
    # are ready). The browser hears it once it joins with user_token.
    registry = getattr(request.app.state, "registry", None)
    if settings.livekit_url and registry is not None and getattr(registry, "tts", None):
        agent_token = _mint_join_token(settings, room_name, identity="chatterbot-agent")
        task = asyncio.create_task(
            run_agent_session(settings, registry, state, room_name, agent_token)
        )
        sessions.set_task(session_id, task)

    return StartSessionResponse(
        session_id=session_id, room_name=room_name,
        livekit_url=settings.livekit_url, token=user_token,
    )


@router.post("/{session_id}/navigate", response_model=StatusResponse)
async def navigate(session_id: str, body: NavigateRequest) -> StatusResponse:
    """Apply manual navigation (NEXT/PREV/GOTO) from the UI buttons.

    Mutates the session's current slide directly (the same effect the voice path
    produces via the agent's ``_navigate``); for GOTO a ``slide_index`` is
    required. Clears any stale resume so context integrity holds (§7.2/§9.3).
    """
    state = sessions.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="unknown session")

    n = len(state.plan.scripts.get(state.current_track, []))
    if body.intent == "NEXT":
        state.current_slide = min(state.current_slide + 1, n - 1)
    elif body.intent == "PREV":
        state.current_slide = max(state.current_slide - 1, 0)
    elif body.intent == "GOTO":
        if body.slide_index is None:
            raise HTTPException(status_code=422, detail="slide_index required for GOTO")
        state.current_slide = max(0, min(body.slide_index, n - 1))
    state.pending_resume = None
    return _status(state)


def _purge_job_artifacts(request: Request, job_id: str) -> None:
    """Free a finished job's memory + disk: drop its KB store, job record, and the
    persisted ``data/jobs/<job_id>/`` dir (RAG index + slide images).

    Called on stop so an ended/reloaded presentation doesn't leave indexes behind
    (user preference: empty indexes on restart/reload to save disk). The agent's
    in-memory store, if still referenced by a running task, lives until that task
    is cancelled — deleting the files only prevents a future reload.
    """
    settings = get_settings()
    registry = getattr(request.app.state, "registry", None)
    if registry is not None and getattr(registry, "kb", None) is not None:
        registry.kb.evict(job_id)
    jobs.remove(job_id)
    shutil.rmtree(os.path.join(settings.data_dir, "jobs", job_id), ignore_errors=True)


@router.post("/{session_id}/control", response_model=StatusResponse)
async def control(session_id: str, body: ControlRequest, request: Request) -> StatusResponse:
    """Pause / resume / stop a session's narration (control plane).

    ``stop`` is terminal: it ends the session AND purges the presentation's
    persisted index/artifacts (the reload/tab-close beacon and the End button both
    route here), so disk isn't held after a presentation finishes.
    """
    state = sessions.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="unknown session")
    if body.action == "pause":
        state.phase = SessionPhase.PAUSED
    elif body.action == "resume":
        state.phase = SessionPhase.SPEAKING
    elif body.action == "stop":
        state.phase = SessionPhase.ENDED
        job_id = state.job_id
        sessions.remove(session_id)
        _purge_job_artifacts(request, job_id)
    return _status(state)


@router.get("/{session_id}/status", response_model=StatusResponse)
async def status(session_id: str) -> StatusResponse:
    """Return the live phase / slide / track / drift for polling (§7)."""
    state = sessions.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return _status(state)


def _status(state: SessionState) -> StatusResponse:
    """Build a StatusResponse snapshot from the current SessionState.

    Computes elapsed-vs-budget drift the same way the agent does so the UI and the
    worker agree on schedule position.
    """
    done = min(state.current_slide, len(state.plan.per_slide_seconds))
    budget = sum(state.plan.per_slide_seconds.get(i, 0.0) for i in range(done))
    elapsed = state.elapsed(time.monotonic())
    target = state.plan.target_seconds or 1.0
    return StatusResponse(
        session_id=state.session_id, phase=state.phase,
        current_slide=state.current_slide, current_track=state.current_track,
        elapsed_seconds=elapsed, budget_seconds=budget,
        drift_pct=(elapsed - budget) / target,
    )
