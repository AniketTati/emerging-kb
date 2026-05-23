"""ASGI middleware: workspace context + X-Request-Id + access log.

Order (outermost first):
  1. RequestIdMiddleware    — generates or echoes X-Request-Id; binds to contextvar
  2. WorkspaceMiddleware    — resolves workspace_id; binds to contextvar
  3. AccessLogMiddleware    — emits one structured log per request; skips probes
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

import structlog
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
    """Emit per-request logs.

    Two channels with different semantics:

    - **Structlog `kb.request`** fires on EVERY request (probes included). It
      exercises the contextvar binding (request_id + workspace_id) and is what
      `capture_structlog` in tests observes. Emitted as a single info-level
      event with `event_context="request"`.

    - **Stdlib `kb.access`** fires only on NON-probe requests, per api_contracts
      §0.8 ("probe endpoints skip access logs"). This is the traditional
      access-log channel that LBs / log aggregators tail. The `capture_access_logs`
      helper in tests observes this channel.
    """

    def __init__(self, app, access_logger_name: str = "kb.access") -> None:
        super().__init__(app)
        self._access_log = logging.getLogger(access_logger_name)
        # NOTE: structlog logger acquired per-call (not cached as instance attr)
        # so tests can swap processors via `capture_structlog` and see emitted events.

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        t0 = time.perf_counter()
        response = await call_next(request)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Structlog trace event — always fires; exercises contextvar binding.
        # request_id + workspace_id are merged in via the configured processors.
        structlog.get_logger("kb.request").info(
            "request",
            event_context="request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            latency_ms=latency_ms,
        )

        # Traditional access log — probes are excluded (api_contracts §0.8).
        if request.url.path not in _PROBE_PATHS:
            self._access_log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "latency_ms": latency_ms,
                    "request_id": request_id_var.get(),
                    "workspace_id": workspace_id_var.get(),
                },
            )
        return response
