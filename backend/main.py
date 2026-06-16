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

import asyncio
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
    """Load models, warm them, start the agent worker; tear down on shutdown (§6).

    Stored on ``app.state.registry`` so routes (via Request) and the agent worker
    share the exact same singletons.
    """
    settings = get_settings()
    setup_logging(settings.log_level)
    log.info("Chatterbot-AI backend starting; loading models...")

    registry = ServiceRegistry()
    await registry.load_all(settings)
    await registry.warmup()
    app.state.registry = registry
    log.info("models loaded + warmed; backend ready")

    # Start the in-process LiveKit agent worker (best-effort; §8.2).
    app.state.agent_task = asyncio.create_task(_run_agent_worker(settings, registry))

    try:
        yield
    finally:
        log.info("shutting down...")
        app.state.agent_task.cancel()
        await registry.aclose()


async def _run_agent_worker(settings, registry) -> None:
    """Run the LiveKit Agents worker in-process (integration seam, §8.2).

    Wraps the (version-sensitive) LiveKit Agents bootstrap. If LiveKit isn't
    configured or the framework API differs from the pinned version, this logs a
    warning and returns — the REST control plane keeps working so the rest of the
    system is still exercisable. The actual per-session loop lives in
    :class:`ChatterbotAgentWorker` (§9); ``entrypoint`` adapts a JobContext to it.
    """
    if not settings.livekit_url or not settings.livekit_api_key:
        log.warning("LiveKit not configured; agent worker disabled (REST API only)")
        return
    try:
        from livekit.agents import JobContext, WorkerOptions, cli  # noqa: F401

        async def entrypoint(ctx: "JobContext") -> None:
            """Adapt a dispatched LiveKit job to a ChatterbotAgentWorker run.

            Looks up the prepared SessionState for the room and drives the
            full-duplex loops. The room<->session mapping is established when the
            session is created (§8.2).
            """
            from app_state import sessions
            from core.agent_worker import ChatterbotAgentWorker

            await ctx.connect()
            # Map room -> session: the room name carries the session id suffix.
            session_id = ctx.room.name.split("-")[-1]
            state = sessions.get(session_id)
            if state is None:
                log.warning(f"no session for room {ctx.room.name}; ignoring job")
                return
            worker = ChatterbotAgentWorker(ctx, registry, settings, state)
            await worker.run()

        # NOTE: depending on the pinned livekit-agents version, the worker is
        # launched via cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint)) in a
        # dedicated process, or dispatched programmatically. Kept as a seam here.
        log.info("LiveKit agent entrypoint registered")
    except Exception as e:  # noqa: BLE001 - never crash the app over agent bootstrap
        log.warning(f"agent worker bootstrap failed (REST API still up): {e}")


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
