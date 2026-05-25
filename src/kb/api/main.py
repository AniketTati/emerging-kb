"""FastAPI app factory.

Phase 0 mounts /health, /ready, and a single test-only /_debug/workspace
endpoint (excluded from OpenAPI schema). Routes for real features land
at Phase 1+ G4.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import os

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from kb import __version__
from kb.api.deps import current_workspace_id
from kb.api.errors import (
    BadRequestError,
    MissingIdempotencyKeyError,
    problem_response,
)
from kb.api.health import router as health_router
from kb.api.middleware import (
    AccessLogMiddleware,
    RequestIdMiddleware,
    WorkspaceMiddleware,
)
from kb.api.audit import router as audit_router
from kb.api.corpus import router as corpus_router
from kb.api.doc_chains import router as doc_chains_router
from kb.api.files import router as files_router
from kb.api.query import router as query_router
from kb.api.readiness import router as ready_router
from kb.api.settings import router as settings_router
from kb.api.sse import router as sse_router
from kb.api.vocabulary import router as vocabulary_router
from kb.api.schema_hierarchy import router as schema_hierarchy_router
from kb.api.schema_versions import router as schema_versions_router
from kb.api.schemas import router as schemas_router
from kb.config import get_settings
from kb.domain.files import FileNotFoundError
from kb.domain.schema_hierarchy import (
    EntityNameConflictError,
    EntityNotFoundError,
    FieldNameConflictError,
    FieldNotFoundError,
    InvalidCrossSchemaReferenceError,
    RelationshipNameConflictError,
    RelationshipNotFoundError,
)
from kb.domain.schema_versions import RollbackNoopError, VersionNotFoundError
from kb.domain.schemas import DuplicateNameError, NotFoundError
from kb.logging import configure_logging, get_logger
from kb.parsers import PayloadTooLargeError, UnsupportedMediaTypeError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    logger = get_logger("kb.api")
    logger.info("kb-api starting", version=__version__)

    # Phase 2a: open Procrastinate App so the HTTP layer can defer tasks
    # (POST /files enqueues parse_file). The worker container manages its
    # own lifecycle separately.
    from kb.workers.app import app as procrastinate_app
    from kb.workers.tasks import parse_file  # noqa: F401 — registers the task

    await procrastinate_app.open_async()
    try:
        yield
    finally:
        await procrastinate_app.close_async()
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

    # Phase 10a — CORS for the Next.js dev origin. Production deployments
    # should set KB_CORS_ORIGINS to a comma-separated allowlist.
    _origins = [o.strip() for o in os.environ.get(
        "KB_CORS_ORIGINS", "http://localhost:3000"
    ).split(",") if o.strip()]
    if _origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-Id", "X-Dedup-Reason"],
        )

    app.include_router(health_router)
    app.include_router(ready_router)
    app.include_router(schemas_router)
    app.include_router(schema_versions_router)
    app.include_router(schema_hierarchy_router)
    app.include_router(files_router)
    app.include_router(corpus_router)
    app.include_router(query_router)
    app.include_router(audit_router)
    app.include_router(sse_router)
    app.include_router(settings_router)
    app.include_router(vocabulary_router)
    app.include_router(doc_chains_router)

    # Phase 2a — register default parsers (Docling). Idempotent.
    from kb.parsers import register_default_parsers
    register_default_parsers()

    # ---- Exception handlers — RFC 9457 problem+json for every 4xx ----

    @app.exception_handler(NotFoundError)
    async def _not_found(req: Request, exc: NotFoundError):  # noqa: ARG001
        return problem_response(
            req, status_code=404, type_slug="not-found",
            title="Resource not found", detail=str(exc),
        )

    @app.exception_handler(VersionNotFoundError)
    async def _version_not_found(req: Request, exc: VersionNotFoundError):  # noqa: ARG001
        return problem_response(
            req, status_code=404, type_slug="not-found",
            title="Schema version not found", detail=str(exc),
        )

    @app.exception_handler(RollbackNoopError)
    async def _rollback_noop(req: Request, exc: RollbackNoopError):  # noqa: ARG001
        return problem_response(
            req, status_code=409, type_slug="rollback-noop",
            title="Rollback target is already the current version",
            detail=str(exc),
        )

    # Phase 1c — schema-hierarchy exceptions
    @app.exception_handler(EntityNotFoundError)
    async def _entity_not_found(req: Request, exc: EntityNotFoundError):  # noqa: ARG001
        return problem_response(
            req, status_code=404, type_slug="not-found",
            title="Entity not found", detail=str(exc),
        )

    @app.exception_handler(FieldNotFoundError)
    async def _field_not_found(req: Request, exc: FieldNotFoundError):  # noqa: ARG001
        return problem_response(
            req, status_code=404, type_slug="not-found",
            title="Field not found", detail=str(exc),
        )

    @app.exception_handler(RelationshipNotFoundError)
    async def _rel_not_found(req: Request, exc: RelationshipNotFoundError):  # noqa: ARG001
        return problem_response(
            req, status_code=404, type_slug="not-found",
            title="Relationship not found", detail=str(exc),
        )

    @app.exception_handler(EntityNameConflictError)
    async def _entity_conflict(req: Request, exc: EntityNameConflictError):  # noqa: ARG001
        return problem_response(
            req, status_code=409, type_slug="entity-name-conflict",
            title="Entity name already exists in this schema",
            detail=str(exc),
        )

    @app.exception_handler(FieldNameConflictError)
    async def _field_conflict(req: Request, exc: FieldNameConflictError):  # noqa: ARG001
        return problem_response(
            req, status_code=409, type_slug="field-name-conflict",
            title="Field name already exists on this entity",
            detail=str(exc),
        )

    @app.exception_handler(RelationshipNameConflictError)
    async def _rel_conflict(req: Request, exc: RelationshipNameConflictError):  # noqa: ARG001
        return problem_response(
            req, status_code=409, type_slug="relationship-name-conflict",
            title="Relationship name already exists in this schema",
            detail=str(exc),
        )

    @app.exception_handler(InvalidCrossSchemaReferenceError)
    async def _cross_schema(req: Request, exc: InvalidCrossSchemaReferenceError):  # noqa: ARG001
        return problem_response(
            req, status_code=422, type_slug="validation-error",
            title="Relationship references an entity in a different schema",
            detail=str(exc),
        )

    # Phase 2a — files + parse layer
    @app.exception_handler(FileNotFoundError)
    async def _file_not_found(req: Request, exc: FileNotFoundError):  # noqa: ARG001
        return problem_response(
            req, status_code=404, type_slug="not-found",
            title="File not found", detail=str(exc),
        )

    @app.exception_handler(PayloadTooLargeError)
    async def _payload_too_large(req: Request, exc: PayloadTooLargeError):  # noqa: ARG001
        return problem_response(
            req, status_code=413, type_slug="payload-too-large",
            title="Upload exceeds the configured size limit",
            detail=str(exc),
        )

    @app.exception_handler(UnsupportedMediaTypeError)
    async def _unsupported_mime(req: Request, exc: UnsupportedMediaTypeError):  # noqa: ARG001
        return problem_response(
            req, status_code=415, type_slug="unsupported-media-type",
            title="MIME type not accepted by this phase",
            detail=str(exc),
        )

    @app.exception_handler(DuplicateNameError)
    async def _dup_name(req: Request, exc: DuplicateNameError):  # noqa: ARG001
        return problem_response(
            req, status_code=409, type_slug="schema-name-conflict",
            title="Schema name already exists in this workspace",
            detail=str(exc),
        )

    @app.exception_handler(MissingIdempotencyKeyError)
    async def _missing_idem(req: Request, exc: MissingIdempotencyKeyError):  # noqa: ARG001
        return problem_response(
            req, status_code=400, type_slug="missing-idempotency-key",
            title="Idempotency-Key header is required for this method",
        )

    @app.exception_handler(BadRequestError)
    async def _bad_request(req: Request, exc: BadRequestError):
        return problem_response(
            req, status_code=400, type_slug="bad-request",
            title="Bad request", detail=exc.detail,
        )

    from kb.api.errors import (
        CorpusRebuildInFlightError,
        CorpusRebuildNoInputError,
        InvalidParserOverrideError,
        InvalidQueryError,
        QueryPipelineError,
    )

    @app.exception_handler(InvalidParserOverrideError)
    async def _invalid_parser_override(req: Request, exc: InvalidParserOverrideError):
        return problem_response(
            req, status_code=400, type_slug="invalid-parser-override",
            title="?parser= query value is invalid",
            detail=str(exc),
        )

    @app.exception_handler(CorpusRebuildNoInputError)
    async def _corpus_rebuild_no_input(req: Request, exc: CorpusRebuildNoInputError):
        return problem_response(
            req, status_code=400, type_slug="corpus-rebuild-no-input",
            title="Workspace has no input documents for corpus rebuild",
            detail=str(exc),
        )

    @app.exception_handler(CorpusRebuildInFlightError)
    async def _corpus_rebuild_in_flight(req: Request, exc: CorpusRebuildInFlightError):
        return problem_response(
            req, status_code=503, type_slug="corpus-rebuild-in-flight",
            title="A corpus rebuild is already in flight for this workspace",
            detail=str(exc),
        )

    @app.exception_handler(InvalidQueryError)
    async def _invalid_query(req: Request, exc: InvalidQueryError):
        return problem_response(
            req, status_code=400, type_slug="invalid-query",
            title="Invalid query body",
            detail=str(exc),
        )

    @app.exception_handler(QueryPipelineError)
    async def _query_pipeline_error(req: Request, exc: QueryPipelineError):
        # Don't leak internal exception text to clients (decision #14).
        return problem_response(
            req, status_code=500, type_slug="query-pipeline-error",
            title="Internal query-pipeline error",
            detail="The query pipeline failed after exhausting fail-safes.",
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(req: Request, exc: RequestValidationError):
        return problem_response(
            req, status_code=422, type_slug="validation-error",
            title="Request body or parameters failed validation",
            detail=str(exc.errors()),
        )

    # Test-only debug endpoint — excluded from OpenAPI.
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
