"""
api/routes_presentation.py — Upload, build, slides, build-status (IMPLEMENTATION.md §7).

Control-plane endpoints for preparing a presentation:
  * POST /api/presentations            — upload .pptx, extract SlideContent[].
  * POST /api/presentations/{id}/build — async: pacing + 3-track scripts + RAG index.
  * GET  .../build/status              — poll build progress (§7.1).
  * GET  .../slides                    — slide metadata for the viewer.
  * GET  .../slides/{i}/image          — slide raster bytes.

The build runs as a background asyncio task and returns 202 immediately, because
parsing + N×LLM calls + indexing can exceed an HTTP timeout for 50 slides (R2).
"""

from __future__ import annotations

import asyncio
import os
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app_state import JobRecord, builds, jobs
from config import get_settings
from core.pacing import PERSONA_PROFILES, PacingService
from models import (
    BuildRequest,
    BuildResponse,
    BuildStatus,
    PacingPlan,
    SlideContent,
    Track,
    UploadResponse,
)

router = APIRouter(prefix="/api/presentations", tags=["presentation"])


@router.post("", response_model=UploadResponse)
async def upload_presentation(request: Request, file: UploadFile = File(...)) -> UploadResponse:
    """Accept a .pptx upload, extract its slides, and register a new job.

    Validates the extension + size, saves the file under the job's data dir, runs
    the (blocking) SlideProcessor extraction off the event loop, and stores the
    resulting SlideContent[]. Script generation is deferred to ``build`` (§7).
    """
    settings = get_settings()
    if not file.filename or not file.filename.lower().endswith(".pptx"):
        raise HTTPException(status_code=422, detail="only .pptx files are supported")

    job_id = uuid.uuid4().hex[:12]
    job_dir = os.path.join(settings.data_dir, "jobs", job_id)
    os.makedirs(job_dir, exist_ok=True)
    pptx_path = os.path.join(job_dir, "source.pptx")

    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large")
    with open(pptx_path, "wb") as f:
        f.write(data)

    registry = request.app.state.registry
    try:
        slides: list[SlideContent] = await asyncio.to_thread(
            registry.slides.extract, pptx_path, job_dir
        )
    except ValueError as e:                       # e.g. slide-count out of range
        raise HTTPException(status_code=422, detail=str(e)) from e

    jobs.put(JobRecord(job_id=job_id, slides=slides))
    return UploadResponse(job_id=job_id, slide_count=len(slides))


@router.post("/{job_id}/build", response_model=BuildResponse, status_code=202)
async def build_presentation(job_id: str, body: BuildRequest, request: Request) -> BuildResponse:
    """Kick off the (async) build: pacing plan + 3-track scripts + RAG index.

    Returns 202 immediately with a build_id; progress is polled via
    ``/build/status`` (R2). The heavy work runs in a background task so a long
    50-slide build never blocks the HTTP request.
    """
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")

    build_id = builds.start(job_id, total=len(job.slides))
    asyncio.create_task(_run_build(job_id, body, request.app.state.registry))
    return BuildResponse(job_id=job_id, build_id=build_id, state="running")


async def _run_build(job_id: str, body: BuildRequest, registry) -> None:
    """Background task: allocate time, generate all-track scripts, index for RAG.

    Cancel-safe and progress-reporting (§7.1): updates BuildStore as each slide's
    scripts complete, persists the PacingPlan onto the JobRecord, and records
    per-track duration estimates. Any failure is captured into the build status.
    """
    settings = get_settings()
    job = jobs.get(job_id)
    try:
        target_seconds = body.target_minutes * 60.0
        per_slide = PacingService.allocate_seconds(job.slides, target_seconds, body.persona)
        densities = [PacingService.density(s) for s in job.slides]
        priorities = PacingService.priorities(densities)

        scripts: dict[Track, list] = {}
        estimates: dict[Track, float] = {}
        done_counter = {"n": 0}

        def _bump() -> None:
            """Progress callback: advance the slides-done counter for status polling."""
            done_counter["n"] += 1
            builds.update(job_id, slides_done=done_counter["n"])

        for track in (Track.STANDARD, Track.SUMMARY, Track.TURBO):
            budgets = PacingService.word_budgets(per_slide, body.persona, track)
            track_scripts = await registry.slides.build_scripts(
                job.slides, budgets, body.persona, track, on_slide_done=_bump
            )
            scripts[track] = track_scripts
            wpm = PERSONA_PROFILES[body.persona].wpm
            total_words = sum(s.word_count for sc in track_scripts for s in sc.sentences)
            estimates[track] = total_words / wpm * 60.0

        plan = PacingPlan(
            job_id=job_id,
            persona=body.persona,
            target_seconds=target_seconds,
            per_slide_seconds=per_slide,
            priorities=priorities,
            scripts=scripts,
        )
        job.plan = plan

        # Index content for grounded Q&A.
        await asyncio.to_thread(registry.kb.index, job_id, job.slides)

        builds.update(
            job_id, state="done", slides_done=len(job.slides),
            tracks_built=list(scripts.keys()), estimated_seconds=estimates,
        )
    except Exception as e:  # noqa: BLE001 - surface failure into status, don't crash app
        builds.update(job_id, state="error", error=str(e))


@router.get("/{job_id}/build/status", response_model=BuildStatus)
async def build_status(job_id: str) -> BuildStatus:
    """Return the current progress/state of a job's build task (§7.1)."""
    status = builds.get(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="no build for this job")
    return status


@router.get("/{job_id}/slides", response_model=list[SlideContent])
async def list_slides(job_id: str) -> list[SlideContent]:
    """Return slide metadata (text/notes/render path) for the frontend viewer."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return job.slides


@router.get("/{job_id}/slides/{index}/image")
async def slide_image(job_id: str, index: int):
    """Return the rendered raster (or first embedded image) for a slide.

    Prefers the full-slide render produced at extraction (R9); falls back to the
    first embedded image. 404 if neither exists.
    """
    job = jobs.get(job_id)
    if job is None or not (0 <= index < len(job.slides)):
        raise HTTPException(status_code=404, detail="unknown slide")
    slide = job.slides[index]
    path = slide.render_path or (slide.image_paths[0] if slide.image_paths else None)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="no image for this slide")
    return FileResponse(path)
