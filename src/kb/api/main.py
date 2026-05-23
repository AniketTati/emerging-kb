"""FastAPI app factory.

Phase 0 mounts /health, /ready, and a single test-only /_debug/workspace
endpoint (excluded from OpenAPI schema). Routes for real features land
at Phase 1+ G4.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from kb import __version__
from kb.api.deps import current_workspace_id
from kb.api.health import router as health_router
from kb.api.middleware import (
    AccessLogMiddleware,
    RequestIdMiddleware,
    WorkspaceMiddleware,
)
from kb.api.readiness import router as ready_router
from kb.config import get_settings
from kb.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    logger = get_logger("kb.api")
    logger.info("kb-api starting", version=__version__)
    yield
    logger.info("kb-api stopping")


def build_app() -> FastAPI:
    """Construct + wire the FastAPI app. Called from `kb.api.main:app` and tests."""
    # Configure logging here (not only in lifespan) so test clients that don't
    # trigger lifespan (httpx + ASGITransport) still get the binding processors
    # registered. configure_logging is idempotent.
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    app = FastAPI(
        title="Emerging KB",
        version=__version__,
        lifespan=lifespan,
        # Phase 0 contracts are stable; Pydantic JSON Schema generation default.
    )

    # Middleware: order is OUTERMOST first when adding to FastAPI;
    # so what we add last runs first on the request and last on the response.
    # We want: RequestId outermost (so error responses still have the header),
    # then Workspace, then AccessLog innermost.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(WorkspaceMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(health_router)
    app.include_router(ready_router)

    # Test-only debug endpoint — excluded from OpenAPI (G1 G5 #5 acceptance:
    # /openapi.json paths contains only /health and /ready).
    @app.get("/_debug/workspace", include_in_schema=False)
    async def _debug_workspace() -> dict[str, str]:
        ws = current_workspace_id()
        # Display the default-workspace sentinel UUID as "default"; an explicit
        # UUID (X-Test-Workspace header, or later Phase 1 auth resolution)
        # echoes back as-is.
        settings = get_settings()
        if ws == str(settings.default_workspace_id):
            return {"workspace_id": "default"}
        return {"workspace_id": ws}

    return app


# Module-level app for uvicorn: `uvicorn kb.api.main:app ...`
app = build_app()
