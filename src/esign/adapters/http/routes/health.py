"""Liveness and readiness probes, as used by Kubernetes."""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from esign.adapters.http.dependencies import ContainerDep
from esign.adapters.http.problems import problem_response

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)

READINESS_TIMEOUT_SECONDS = 2.0


@router.get("/live", summary="Liveness")
async def live() -> dict[str, str]:
    """The process is up. Deliberately checks nothing else, so a database outage
    does not make Kubernetes restart healthy pods."""
    return {"status": "ok"}


@router.get("/ready", response_model=None, summary="Readiness")
async def ready(request: Request, container: ContainerDep) -> dict[str, str] | JSONResponse:
    """Ready to serve traffic: MongoDB answers a ping."""
    try:
        await asyncio.wait_for(container.database.ping(), READINESS_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("readiness check failed", extra={"error": type(exc).__name__})
        return problem_response(
            request, status=503, title="Service Unavailable", detail="database is unreachable"
        )
    return {"status": "ok"}
