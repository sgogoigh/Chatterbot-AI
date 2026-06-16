"""
app_state.py — Lightweight in-memory stores for jobs, builds, and sessions.

Referenced by IMPLEMENTATION.md §7.1 ("single-process asyncio.Task is sufficient
... given the one-presentation constraint"). Because only one presentation plays
at a time, we don't need an external queue or DB; prepared artifacts are persisted
to ``data/jobs/<job_id>/`` and this module tracks live, in-flight state.

  * JobStore     — extracted slides + the built PacingPlan per job_id.
  * BuildStore   — progress of async build tasks (drives build/status, §7.1).
  * SessionManager — live SessionState + agent worker per session_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models import BuildStatus, PacingPlan, SlideContent


@dataclass
class JobRecord:
    """Everything known about an uploaded/built presentation."""

    job_id: str
    slides: list[SlideContent] = field(default_factory=list)
    plan: PacingPlan | None = None       # populated once build completes


class JobStore:
    """In-memory registry of uploaded presentations (keyed by job_id)."""

    def __init__(self) -> None:
        """Create an empty job registry."""
        self._jobs: dict[str, JobRecord] = {}

    def put(self, record: JobRecord) -> None:
        """Insert or replace a job record."""
        self._jobs[record.job_id] = record

    def get(self, job_id: str) -> JobRecord | None:
        """Return the job record for ``job_id`` or None if unknown."""
        return self._jobs.get(job_id)


class BuildStore:
    """Tracks progress of asynchronous build tasks (§7.1)."""

    def __init__(self) -> None:
        """Create an empty build-status registry."""
        self._builds: dict[str, BuildStatus] = {}

    def start(self, job_id: str, total: int) -> str:
        """Register a new build for ``job_id`` and return its build_id.

        The build_id is derived from the job_id (one active build per job under the
        single-presentation constraint).
        """
        build_id = f"build-{job_id}"
        self._builds[job_id] = BuildStatus(job_id=job_id, state="running", total=total)
        return build_id

    def get(self, job_id: str) -> BuildStatus | None:
        """Return the current build status for ``job_id`` or None."""
        return self._builds.get(job_id)

    def update(self, job_id: str, **fields) -> None:
        """Patch fields on an existing build status (progress callbacks, completion)."""
        status = self._builds.get(job_id)
        if status:
            for k, v in fields.items():
                setattr(status, k, v)


class SessionManager:
    """Holds live sessions; enforces the single-concurrent-presentation rule (doc)."""

    def __init__(self) -> None:
        """Create an empty session registry."""
        self._sessions: dict[str, object] = {}      # session_id -> SessionState

    def put(self, session_id: str, state) -> None:
        """Register a live session's state object."""
        self._sessions[session_id] = state

    def get(self, session_id: str):
        """Return the SessionState for ``session_id`` or None."""
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        """Drop a session (graceful termination)."""
        self._sessions.pop(session_id, None)

    def active_count(self) -> int:
        """Number of live sessions (used to enforce the single-session constraint)."""
        return len(self._sessions)


# Process-wide singletons (single-instance deployment, §7.1).
jobs = JobStore()
builds = BuildStore()
sessions = SessionManager()
