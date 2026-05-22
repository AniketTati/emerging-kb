"""ASGI middleware: workspace context + X-Request-Id + access log.

Order (outermost first):
  1. RequestIdMiddleware    — generates or echoes X-Request-Id; binds to contextvar
  2. WorkspaceMiddleware    — resolves workspace_id; binds to contextvar
  3. AccessLogMiddleware    — emits one structured log per request; skips probes
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Awaitable, Callable

import uuid_utils
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from kb.config import get_settings
from kb.logging import request_id_var, workspace_id_var

# Endpoints that must not emit access logs (api_contracts §0.8).
_PROBE_PATHS = frozenset({"/health", "/ready"})

# Test-only header that lets clients override workspace_id in Phase 0
# (no auth yet). Phase 1 replaces this with auth-resolved workspaces.
_TEST_WORKSPACE_HEADER = "X-Test-Workspace"
_REQUEST_ID_HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generate or echo X-Request-Id; bind into the request-scoped contextvar."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(_REQUEST_ID_HEADER)
        rid = incoming or str(uuid_utils.uuid7())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[_REQUEST_ID_HEADER] = rid
        return response


class WorkspaceMiddleware(BaseHTTPMiddleware):
    """Resolve workspace_id and bind it into the contextvar.

    Phase 0 has no auth. Resolution order:
      1. `X-Test-Workspace` header (test-only escape hatch).
      2. Settings.default_workspace_id (the zero UUID sentinel).

    The `current_workspace()` dependency reads this contextvar. Future
    middleware will also set `app.workspace_id` on the connection pool
    so RLS applies (wired in the lifespan handler).
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = get_settings()
        override = request.headers.get(_TEST_WORKSPACE_HEADER)
        ws_id = override if override else str(settings.default_workspace_id)
        token = workspace_id_var.set(ws_id)
        try:
            return await call_next(request)
        finally:
            workspace_id_var.reset(token)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit one access-log entry per request. Skips probe endpoints."""

    def __init__(self, app, logger_name: str = "kb.access") -> None:
        super().__init__(app)
        self._logger = logging.getLogger(logger_name)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in _PROBE_PATHS:
            return await call_next(request)

        t0 = time.perf_counter()
        response = await call_next(request)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        self._logger.info(
            "request",
            extra={
                "event_context": "request",
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": latency_ms,
                "request_id": request_id_var.get(),
                "workspace_id": workspace_id_var.get(),
            },
        )
        return response
