"""
core/session_state.py — Live session state + resume bookkeeping (IMPLEMENTATION.md §9.1).

Holds everything the AgentWorker mutates during a presentation: the phase
(state machine), current slide/track, the pacing plan, the playback tracker, and
the concurrency primitives that coordinate the listener and speaker loops
(transition lock + interrupt event) — see §19.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from core.playback_tracker import PlaybackTracker
from models import PacingPlan, SessionPhase, Track


@dataclass
class ResumePoint:
    """Deterministic Resume State captured at interrupt time (PLAN §4.5, §9.1).

    Records exactly what must match to safely resume: which slide context, which
    track, and which sentence (already snapped to its start). Context Integrity
    Verification clears/rebuilds this on rapid navigation (§9.3).
    """

    slide_index: int
    track: Track
    sentence_index: int


@dataclass
class SessionState:
    """All mutable state for one live presentation session.

    One instance per session, owned by a single :class:`ChatterbotAgentWorker`.
    The async primitives below are the heart of the full-duplex coordination:
    ``transition_lock`` serialises audio producers (no collision) and
    ``interrupt_event`` signals barge-in from the listener loop to the speaker
    loop (§19).
    """

    session_id: str
    job_id: str
    plan: PacingPlan
    tracker: PlaybackTracker
    phase: SessionPhase = SessionPhase.READY
    current_slide: int = 0
    current_track: Track = Track.STANDARD
    start_monotonic: float = 0.0          # set when narration first begins
    turn_id: int = 0                      # bumped per interrupt/answer (R11 guard)
    pending_resume: ResumePoint | None = None

    # Concurrency primitives (default_factory so each session gets its own):
    interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
    transition_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def elapsed(self, now_monotonic: float) -> float:
        """Return seconds of wall-clock narration elapsed since playback started.

        Used by the pacing drift calculation; returns 0 before narration begins.
        """
        return 0.0 if self.start_monotonic == 0.0 else now_monotonic - self.start_monotonic
