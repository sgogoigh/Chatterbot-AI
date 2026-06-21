"""
api/routes_health.py — Liveness + readiness probes (IMPLEMENTATION.md §6, §7).

  * /healthz — cheap liveness ping (process is up).
  * /readyz  — returns 200 only after all models are loaded + warmed, so an
               orchestrator won't route traffic before the pipeline can serve it.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    """Liveness probe: always 200 while the process is running."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict:
    """Readiness probe: 200 only when the ServiceRegistry reports all models warm.

    Reads the registry stored on app state by the lifespan (§6). Returns 503 until
    warmup completes so the <5 ms VAD path etc. are never hit cold.
    """
    registry = getattr(request.app.state, "registry", None)
    if registry is not None and registry.is_ready():
        return {"status": "ready"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "loading"}
