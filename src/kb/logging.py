"""Structured logging via structlog.

Binds `request_id` and `workspace_id` from contextvars so every log emitted
inside a request includes them. Probe endpoints (`/health`, `/ready`) bypass
the access logger per api_contracts §0.8.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

import structlog

# Per-request context vars; populated by middleware.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
workspace_id_var: ContextVar[str | None] = ContextVar("workspace_id", default=None)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure structlog + stdlib logging once per process."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _bind_context_vars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        # cache_logger_on_first_use=False so that `capture_structlog` in tests
        # can swap processors at runtime and see events emitted by code that
        # acquired its logger earlier (e.g. middleware initialized at app
        # startup). Tradeoff is a small per-call lookup; negligible.
        cache_logger_on_first_use=False,
    )


def _bind_context_vars(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Inject request_id and workspace_id from contextvars into every log record."""
    rid = request_id_var.get()
    if rid is not None and "request_id" not in event_dict:
        event_dict["request_id"] = rid
    wid = workspace_id_var.get()
    if wid is not None and "workspace_id" not in event_dict:
        event_dict["workspace_id"] = wid
    return event_dict


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name) if name else structlog.get_logger()


# ----------------------------------------------------------------------------
# Test helpers — used by tests/test_health.py, test_ready.py, test_middleware.py.
# Kept in this module so tests can import them as `from kb.logging import ...`.
# ----------------------------------------------------------------------------


@contextlib.contextmanager
def capture_access_logs() -> Iterator[list[logging.LogRecord]]:
    """Capture entries from the access-log logger (`kb.access`).

    api_contracts §0.8: probe endpoints (`/health`, `/ready`) must NOT log
    access lines. This helper lets tests assert "0 records".
    """
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    logger = logging.getLogger("kb.access")
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


@contextlib.contextmanager
def capture_structlog() -> Iterator[list[dict[str, Any]]]:
    """Capture every structlog event emitted while the block is active.

    Used by test_middleware.py to assert that request-scoped logs include
    `request_id` and `workspace_id` fields.
    """
    captured: list[dict[str, Any]] = []
    original_processors = structlog.get_config().get("processors", [])

    def _sink(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
        captured.append(dict(event_dict))
        return event_dict

    new_processors = [*original_processors[:-1], _sink, original_processors[-1]] if original_processors else [_sink]
    structlog.configure(processors=new_processors)
    try:
        yield captured
    finally:
        structlog.configure(processors=original_processors)
