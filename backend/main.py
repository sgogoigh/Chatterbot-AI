"""
main.py — FastAPI application factory + lifespan + LiveKit agent bootstrap.

See IMPLEMENTATION.md §6 (lifecycle) and §8.2 (in-process agent). This is the
uvicorn entrypoint: ``uvicorn main:app`` (matches the Dockerfile CMD).

Startup sequence (§6):
  1. configure logging,
  2. load every heavy model ONCE into the ServiceRegistry (concurrently),
  3. warm them up (so the <5 ms VAD target etc. are post-warmup),
  4. start the in-process LiveKit agent worker as a background task.

The REST API stays usable for development/testing even when LiveKit isn't
configured — the agent bootstrap degrades gracefully (logs a warning) so you can
exercise upload/build/RAG without a media server.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import routes_health, routes_presentation, routes_session
from config import get_settings
from deps import ServiceRegistry
from utils.logging import get_logger, setup_logging

log = get_logger(component="main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load + warm models once; tear down on shutdown (§6).

    Stored on ``app.state.registry`` so routes and the per-session voice agents
    share the exact same singletons. The agent is NOT started here — it is
    dispatched per session by ``POST /api/sessions`` (core/agent_session.py),
    which joins the session's LiveKit room directly.
    """
    settings = get_settings()
    setup_logging(settings.log_level)
    log.info("Chatterbot-AI backend starting; loading models...")

    registry = ServiceRegistry()
    await registry.load_all(settings)
    await registry.warmup()
    app.state.registry = registry
    log.info("models loaded + warmed; backend ready")

    try:
        yield
    finally:
        log.info("shutting down...")
        await registry.aclose()


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Registers CORS (for the Next.js frontend), the lifespan, and all routers.
    Factored out so tests can build an app without importing side effects.
    """
    app = FastAPI(title="Chatterbot-AI", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],          # tighten to the frontend origin in production
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(routes_health.router)
    app.include_router(routes_presentation.router)
    app.include_router(routes_session.router)
    return app


app = create_app()
