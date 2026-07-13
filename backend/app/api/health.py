"""
Aegis — GET /api/health

Electron's main process polls this endpoint on startup.
The window is only shown once this returns HTTP 200.
"""

from fastapi import APIRouter
from pydantic import BaseModel
import time

router = APIRouter(tags=["Health"])

# Track startup time for uptime reporting
_START_TIME = time.time()


class HealthResponse(BaseModel):
    status: str
    uptime_seconds: float
    version: str


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Sidecar Health Check",
    description="Used by Electron to verify the sidecar is ready before loading the UI.",
)
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        uptime_seconds=round(time.time() - _START_TIME, 2),
        version="0.1.0",
    )
