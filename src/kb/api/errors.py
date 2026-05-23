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
