"""Settings — env-driven via pydantic-settings.

Hydra + OmegaConf (architecture §8) lands at Phase 5 when stack/model choices
need layered config. Phase 0's surface is env-var-only.
"""

from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Postgres (superuser; used by migration runner) ----
    postgres_user: str = Field(default="kb", alias="KB_POSTGRES_USER")
    postgres_password: str = Field(default="kb-dev-password", alias="KB_POSTGRES_PASSWORD")
    postgres_db: str = Field(default="kb", alias="KB_POSTGRES_DB")
    postgres_host: str = Field(default="db", alias="KB_POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="KB_POSTGRES_PORT")

    # ---- Application role (kb_app non-superuser; RLS applies) ----
    app_role: str = Field(default="kb_app", alias="KB_APP_ROLE")
    app_password: str = Field(default="kb-app-dev-password", alias="KB_APP_PASSWORD")

    # ---- MinIO ----
    minio_endpoint: str = Field(default="minio:9000", alias="KB_MINIO_ENDPOINT")
    minio_access_key: str = Field(default="kb-minio-dev", alias="KB_MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="kb-minio-dev-secret", alias="KB_MINIO_SECRET_KEY")
    minio_bucket: str = Field(default="kb", alias="KB_MINIO_BUCKET")
    minio_secure: bool = Field(default=False, alias="KB_MINIO_SECURE")

    # ---- App ----
    log_level: str = Field(default="INFO", alias="KB_LOG_LEVEL")
    log_format: str = Field(default="json", alias="KB_LOG_FORMAT")
    default_workspace_id: UUID = Field(
        default=UUID("00000000-0000-0000-0000-000000000000"),
        alias="KB_DEFAULT_WORKSPACE_ID",
    )

    # ---- Phase 2a — upload limits ----
    max_upload_bytes: int = Field(
        default=100 * 1024 * 1024,  # 100 MB per build_tracker §5.5 decision #13
        alias="KB_MAX_UPLOAD_BYTES",
    )

    # ---- Phase 3a — chunker tuning ----
    # Per build_tracker §5.7 decision #1: 2500 default (mid of architecture's
    # "~2–4K tokens" guidance). Tests override to ~200 for fast deterministic
    # multi-chunk fixtures.
    chunk_tokens: int = Field(default=2500, alias="KB_CHUNK_TOKENS")
    chunk_overlap_tokens: int = Field(default=250, alias="KB_CHUNK_OVERLAP_TOKENS")

    # ---- Runtime overrides (used by tests + the in-process FastAPI app) ----
    # KB_DB_URL overrides the kb_app (RLS-applicable) URL — used by API + most
    # tests. KB_DATABASE_URL overrides the superuser URL — used by migrations,
    # Procrastinate, and the worker (worker needs to bypass RLS to read the
    # initial file row, then SET LOCAL app.workspace_id for downstream queries).
    app_db_url_override: str | None = Field(default=None, alias="KB_DB_URL")
    superuser_db_url_override: str | None = Field(default=None, alias="KB_DATABASE_URL")

    @computed_field  # type: ignore[misc]
    @property
    def database_url(self) -> str:
        """Superuser connection string (migrations, Procrastinate, worker)."""
        if self.superuser_db_url_override:
            return self.superuser_db_url_override
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def app_database_url(self) -> str:
        """kb_app role connection string (API; RLS applies)."""
        if self.app_db_url_override:
            return self.app_db_url_override
        return (
            f"postgresql://{self.app_role}:{self.app_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor; reads env once per process."""
    return Settings()
