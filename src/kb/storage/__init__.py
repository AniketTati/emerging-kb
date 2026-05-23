"""MinIO client wiring.

Phase 0 needs a client only for the `/ready` minio health check. Object PUT/GET
patterns land at Phase 2 (parse layer) when raw files actually need to land.
"""

from __future__ import annotations

from functools import lru_cache

from minio import Minio

from kb.config import get_settings


@lru_cache(maxsize=1)
def get_minio_client() -> Minio:
    """Singleton MinIO client constructed from settings."""
    settings = get_settings()
    return Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


__all__ = ["get_minio_client"]
