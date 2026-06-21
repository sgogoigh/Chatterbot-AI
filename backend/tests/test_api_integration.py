"""
tests/test_api_integration.py — FastAPI HTTP integration tests (E2E_TESTING.md S13/S10).

Drives the REAL FastAPI routers over HTTP via httpx ASGITransport (in-process, no
network, no uvicorn). The heavy services are replaced with a FakeRegistry injected
into ``app.state.registry`` so the *control plane* (upload → build → session →
navigate → control → status) is exercised deterministically and fast on Windows.

This closes the gap noted earlier: every other suite drives the services directly;
here we verify the FastAPI HTTP layer itself.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from main import create_app
from tests.harness import FakeRegistry


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A FastAPI app with a stub registry and a temp data dir (no lifespan/models).

    ASGITransport doesn't run the lifespan, so we set ``app.state.registry``
    manually (the real lifespan would load models). CB_DATA_DIR points artifacts at
    a temp dir, and the in-memory session store is cleared for isolation.
    """
    monkeypatch.setenv("CB_DATA_DIR", str(tmp_path))
    from config import get_settings

    get_settings.cache_clear()
    from app_state import builds, jobs, sessions

    sessions._sessions.clear()
    jobs._jobs.clear()
    builds._builds.clear()

    application = create_app()
    application.state.registry = FakeRegistry()
    yield application
    get_settings.cache_clear()


async def _client(app) -> AsyncClient:
    """Build an in-process httpx client bound to the ASGI app."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _upload_and_build(ac: AsyncClient) -> str:
    """Helper: upload a (dummy) .pptx, build it, and return the job_id once done."""
    files = {"file": ("deck.pptx", b"dummy-bytes",
                      "application/vnd.openxmlformats-officedocument.presentationml.presentation")}
    r = await ac.post("/api/presentations", files=files)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    r = await ac.post(f"/api/presentations/{job_id}/build",
                      json={"target_minutes": 1, "persona": "GENERAL"})
    assert r.status_code == 202, r.text

    # Poll the async build to completion.
    for _ in range(100):
        s = (await ac.get(f"/api/presentations/{job_id}/build/status")).json()
        if s["state"] in ("done", "error"):
            break
        await asyncio.sleep(0.02)
    assert s["state"] == "done", s
    return job_id


# ----------------------------------------------------------------- health
async def test_health_and_ready(app):
    """/healthz is always 200; /readyz is 200 when the registry reports ready."""
    async with await _client(app) as ac:
        assert (await ac.get("/healthz")).status_code == 200
        r = await ac.get("/readyz")
        assert r.status_code == 200 and r.json()["status"] == "ready"


# ----------------------------------------------------- full control plane (S13)
async def test_full_control_plane_flow(app):
    """S13: upload → build (3 tracks) → slides → start session → navigate → status."""
    async with await _client(app) as ac:
        # upload
        files = {"file": ("deck.pptx", b"dummy",
                          "application/vnd.openxmlformats-officedocument.presentationml.presentation")}
        r = await ac.post("/api/presentations", files=files)
        assert r.status_code == 200
        body = r.json()
        job_id, slide_count = body["job_id"], body["slide_count"]
        assert slide_count == 2

        # build (async → 202) then poll
        r = await ac.post(f"/api/presentations/{job_id}/build",
                          json={"target_minutes": 1, "persona": "TEACHER"})
        assert r.status_code == 202
        for _ in range(100):
            s = (await ac.get(f"/api/presentations/{job_id}/build/status")).json()
            if s["state"] in ("done", "error"):
                break
            await asyncio.sleep(0.02)
        assert s["state"] == "done"
        assert set(s["tracks_built"]) == {"STANDARD", "SUMMARY", "TURBO"}
        assert s["slides_done"] == 2

        # slides metadata
        r = await ac.get(f"/api/presentations/{job_id}/slides")
        assert r.status_code == 200 and len(r.json()) == 2

        # start a session → LiveKit join details + token
        r = await ac.post("/api/sessions", json={"job_id": job_id})
        assert r.status_code == 200, r.text
        sess = r.json()
        session_id = sess["session_id"]
        assert sess["token"] and sess["room_name"]

        # navigate NEXT → slide advances
        r = await ac.post(f"/api/sessions/{session_id}/navigate",
                          json={"session_id": session_id, "intent": "NEXT"})
        assert r.status_code == 200 and r.json()["current_slide"] == 1

        # status reflects the navigation
        r = await ac.get(f"/api/sessions/{session_id}/status")
        assert r.status_code == 200 and r.json()["current_slide"] == 1


# ----------------------------------------------------- navigation edge cases
async def test_goto_requires_slide_index(app):
    """GOTO without slide_index is a 422 (validation in the route)."""
    async with await _client(app) as ac:
        job_id = await _upload_and_build(ac)
        sid = (await ac.post("/api/sessions", json={"job_id": job_id})).json()["session_id"]
        r = await ac.post(f"/api/sessions/{sid}/navigate",
                          json={"session_id": sid, "intent": "GOTO"})
        assert r.status_code == 422


async def test_control_pause_and_stop(app):
    """control pause → PAUSED; stop → ENDED + session removed."""
    async with await _client(app) as ac:
        job_id = await _upload_and_build(ac)
        sid = (await ac.post("/api/sessions", json={"job_id": job_id})).json()["session_id"]

        r = await ac.post(f"/api/sessions/{sid}/control", json={"action": "pause"})
        assert r.status_code == 200 and r.json()["phase"] == "PAUSED"

        r = await ac.post(f"/api/sessions/{sid}/control", json={"action": "stop"})
        assert r.status_code == 200 and r.json()["phase"] == "ENDED"

        # session removed after stop
        assert (await ac.get(f"/api/sessions/{sid}/status")).status_code == 404


# ----------------------------------------------------- single-session (S10)
async def test_second_concurrent_session_rejected(app):
    """S10: a second active session is rejected with 409 (single-presentation constraint)."""
    async with await _client(app) as ac:
        job_id = await _upload_and_build(ac)
        r1 = await ac.post("/api/sessions", json={"job_id": job_id})
        assert r1.status_code == 200
        r2 = await ac.post("/api/sessions", json={"job_id": job_id})
        assert r2.status_code == 409


# ----------------------------------------------------- error paths
async def test_unknown_job_and_session_404(app):
    """Unknown job_id (build) and unknown session_id (status) both 404."""
    async with await _client(app) as ac:
        assert (await ac.post("/api/presentations/nope/build",
                              json={"target_minutes": 1})).status_code == 404
        assert (await ac.get("/api/sessions/nope/status")).status_code == 404


async def test_session_before_build_conflicts(app):
    """Starting a session for an un-built job is a 409 (not yet built)."""
    async with await _client(app) as ac:
        files = {"file": ("deck.pptx", b"dummy",
                          "application/vnd.openxmlformats-officedocument.presentationml.presentation")}
        job_id = (await ac.post("/api/presentations", files=files)).json()["job_id"]
        r = await ac.post("/api/sessions", json={"job_id": job_id})
        assert r.status_code == 409


async def test_reject_non_pptx_upload(app):
    """Uploading a non-.pptx file is a 422."""
    async with await _client(app) as ac:
        files = {"file": ("notes.txt", b"hello", "text/plain")}
        r = await ac.post("/api/presentations", files=files)
        assert r.status_code == 422
