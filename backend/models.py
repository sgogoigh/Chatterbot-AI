"""
models.py — All pydantic domain + API schemas (IMPLEMENTATION.md §5).

These are the *contracts* between every layer of the backend: ingestion
(SlideProcessor) → pacing (PacingService) → playback (PlaybackTracker) →
retrieval (KnowledgeBase) → orchestration (AgentWorker) → REST API.

Keeping every schema in one module means a single source of truth for the
shapes that cross module boundaries. Enums mirror the vocabulary used
throughout the source document (personas, tracks, intents, session states).
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# =====================================================================
# Enums — the controlled vocabulary of the system
# =====================================================================
class Persona(str, Enum):
    """Delivery persona; sets base speaking rate + buffer ratio (see PacingService §15.1)."""

    GENERAL = "GENERAL"
    TEACHER = "TEACHER"
    MEETING = "MEETING"
    TEDX = "TEDX"


class Track(str, Enum):
    """Verbosity variant of the narration script (IMPLEMENTATION.md §15.1).

    All three target the same total duration; they differ in word count so the
    pacing drift handler can recover time by switching tracks.
    """

    STANDARD = "STANDARD"
    SUMMARY = "SUMMARY"
    TURBO = "TURBO"


class SlidePriority(str, Enum):
    """Per-slide importance, used to decide which slides to compress first."""

    ANCHOR = "ANCHOR"
    SUPPORTING = "SUPPORTING"
    CONTEXTUAL = "CONTEXTUAL"


class Intent(str, Enum):
    """The 6-class semantic intent space (FastIntentClassifier §13)."""

    NEXT = "NEXT"
    PREV = "PREV"
    GOTO = "GOTO"
    QUESTION = "QUESTION"
    STOP = "STOP"
    IGNORE = "IGNORE"


class SessionPhase(str, Enum):
    """Session lifecycle states; mirrors PLAN §11 / IMPLEMENTATION.md state machine."""

    IDLE = "IDLE"
    LOADING = "LOADING"
    READY = "READY"
    SPEAKING = "SPEAKING"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    ANSWERING = "ANSWERING"
    PAUSED = "PAUSED"
    TRANSITIONING = "TRANSITIONING"
    ENDED = "ENDED"


# =====================================================================
# Ingestion schemas (produced by SlideProcessor §14)
# =====================================================================
class SlideContent(BaseModel):
    """Everything extracted from a single PowerPoint slide."""

    index: int                      # 0-based slide position
    title: str | None = None
    body_text: str = ""             # concatenated text-frame content
    notes: str = ""                 # speaker notes
    ocr_text: str = ""              # text recovered from images via tesseract
    image_paths: list[str] = Field(default_factory=list)
    render_path: str | None = None  # full-slide raster (R9), if produced


class ScriptSentence(BaseModel):
    """One spoken sentence of narration plus its (optional) audio sample range.

    ``sample_start``/``sample_end`` are filled lazily by the PlaybackTracker once
    audio exists (or estimated proportionally before synthesis) — see §16.
    """

    text: str
    word_count: int
    sample_start: int | None = None
    sample_end: int | None = None


class SlideScript(BaseModel):
    """The narration for one slide on one track, segmented into sentences."""

    slide_index: int
    track: Track
    sentences: list[ScriptSentence] = Field(default_factory=list)
    target_seconds: float = 0.0     # budget allocated by PacingService
    priority: SlidePriority = SlidePriority.SUPPORTING


class PacingPlan(BaseModel):
    """Complete time-locked delivery plan for a job (output of PacingService §15)."""

    job_id: str
    persona: Persona
    target_seconds: float
    per_slide_seconds: dict[int, float] = Field(default_factory=dict)
    priorities: dict[int, SlidePriority] = Field(default_factory=dict)
    # One full script (list of per-slide scripts) per track:
    scripts: dict[Track, list[SlideScript]] = Field(default_factory=dict)


# =====================================================================
# RAG schemas (KnowledgeBase §17)
# =====================================================================
class Chunk(BaseModel):
    """A retrievable text fragment tagged with its source slide."""

    chunk_id: str
    slide_index: int
    text: str
    score: float = 0.0              # populated during retrieval/fusion


class RetrievedContext(BaseModel):
    """Top-k grounding context returned for a question, with cited sources."""

    chunks: list[Chunk] = Field(default_factory=list)
    sources: list[int] = Field(default_factory=list)   # cited slide indices


# =====================================================================
# API request / response schemas (FastAPI §7)
# =====================================================================
class UploadResponse(BaseModel):
    """Result of ``POST /api/presentations``."""

    job_id: str
    slide_count: int


class BuildRequest(BaseModel):
    """Body of ``POST /api/presentations/{job_id}/build``."""

    target_minutes: float = Field(gt=0, le=120)
    persona: Persona = Persona.GENERAL


class BuildResponse(BaseModel):
    """Acknowledgement for the (async) build job (202 Accepted)."""

    job_id: str
    build_id: str
    state: str = "queued"


class BuildStatus(BaseModel):
    """Progress of an in-flight build (``GET .../build/status``)."""

    job_id: str
    state: str                      # queued | running | done | error
    slides_done: int = 0
    total: int = 0
    tracks_built: list[Track] = Field(default_factory=list)
    estimated_seconds: dict[Track, float] = Field(default_factory=dict)
    error: str | None = None


class StartSessionRequest(BaseModel):
    """Body of ``POST /api/sessions``."""

    job_id: str
    room_name: str | None = None


class StartSessionResponse(BaseModel):
    """LiveKit join details handed back to the frontend."""

    session_id: str
    room_name: str
    livekit_url: str
    token: str                      # participant join token (JWT)


class NavigateRequest(BaseModel):
    """Manual navigation from UI buttons; routes through the same handler as voice."""

    session_id: str
    intent: Literal["NEXT", "PREV", "GOTO"]
    slide_index: int | None = None  # required when intent == GOTO


class ControlRequest(BaseModel):
    """Session control plane (pause/resume/stop)."""

    action: Literal["pause", "resume", "stop"]


class StatusResponse(BaseModel):
    """Live session status (``GET /api/sessions/{id}/status``)."""

    session_id: str
    phase: SessionPhase
    current_slide: int
    current_track: Track
    elapsed_seconds: float
    budget_seconds: float
    drift_pct: float
