"""GET /health — liveness probe.

api_contracts §1.1. Always returns 200 with the documented shape.
Must not depend on any external service. p99 < 100ms.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from kb import __version__

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    ts: str


@router.get("/health", response_model=HealthResponse, tags=["lifecycle"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="kb-api",
        version=__version__,
        ts=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
