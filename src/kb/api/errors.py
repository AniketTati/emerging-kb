"""RFC 9457 `application/problem+json` error helpers.

Every 4xx/5xx response that this service generates uses the problem+json
shape from api_contracts §0.3. The `type` field is a URL-style slug under
`https://kb.example.com/errors/<slug>` — phase G2 tables list the slugs.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

_ERROR_NAMESPACE = "https://kb.example.com/errors/"


def problem_response(
    request: Request,
    *,
    status_code: int,
    type_slug: str,
    title: str,
    detail: str = "",
) -> JSONResponse:
    """Build a RFC 9457 application/problem+json response."""
    return JSONResponse(
        status_code=status_code,
        content={
            "type": f"{_ERROR_NAMESPACE}{type_slug}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": request.url.path,
        },
        media_type="application/problem+json",
    )


# ---------------------------------------------------------------------------
# Custom exceptions — raised by deps / endpoints, handled globally in main.
# Pairing exceptions with named handlers keeps the slug-table in api_contracts
# the single source of truth.
# ---------------------------------------------------------------------------


class MissingIdempotencyKeyError(Exception):
    """POST without `Idempotency-Key` header (api_contracts §2.2 / §0.5)."""


class BadRequestError(Exception):
    """Bad query/body that isn't a pydantic validation failure (e.g. limit > 200)."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class InvalidParserOverrideError(Exception):
    """Phase 2c §5.6.1 #11: POST /files?parser=<value> with a value not in
    {auto, docling, gemini}. Maps to 400 invalid-parser-override."""

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(
            f"?parser={value!r} is invalid; expected one of auto, docling, gemini"
        )


class CorpusRebuildNoInputError(Exception):
    """Phase 3e §6.3: POST /corpus/raptor/rebuild on a workspace with zero
    files at lifecycle_state='ready'. Nothing to cluster. Maps to 400
    corpus-rebuild-no-input."""

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        super().__init__(
            f"workspace {workspace_id!r} has no files at lifecycle_state='ready'; "
            f"nothing to cluster"
        )


class CorpusRebuildInFlightError(Exception):
    """Phase 3e §6.3: POST /corpus/raptor/rebuild while a job is already
    queued (procrastinate_jobs.status IN ('todo','doing')) for this
    workspace. Maps to 503 corpus-rebuild-in-flight."""

    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id
        super().__init__(
            f"a corpus rebuild for workspace {workspace_id!r} is already in flight"
        )
