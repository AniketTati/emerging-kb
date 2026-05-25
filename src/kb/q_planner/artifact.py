"""B4b — Q-mode CSV artifact storage (Design 1 layer 10 support).

When a Q-mode execution succeeds, the result is serialized to CSV and
written to MinIO under `q_mode_artifacts/<workspace>/<audit_query_id>.csv`.
The object key is returned and persisted in `audit_queries.csv_artifact_key`
so the dashboard can offer a download link.

CSV is the right artifact format for aggregate results — small, portable,
inspectable in a spreadsheet, and matches what auditors expect. JSON is
preserved in `audit_queries.plan` + `params` for full reconstruction.

Storage layer falls back to a no-op (returns `None` key) when MinIO is
not configured / unreachable. The audit_queries row still lands; only
the downloadable artifact is missing. That's intentional: an inability
to write an artifact must not block answer delivery.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any


_LOG = logging.getLogger(__name__)


_BUCKET = "kb-q-mode-artifacts"
_KEY_PREFIX = "q_mode_artifacts"


def rows_to_csv_bytes(
    column_names: list[str] | tuple[str, ...],
    rows: list[tuple] | tuple[tuple, ...],
) -> bytes:
    """Serialize rows + headers into CSV bytes. Pure-function, used by
    `persist_csv_artifact` AND by callers that want to return CSV
    directly (e.g. a future download endpoint)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(list(column_names))
    for r in rows:
        writer.writerow([_stringify_cell(v) for v in r])
    return buf.getvalue().encode("utf-8")


def _stringify_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    # Avoid scientific notation for numerics.
    if isinstance(v, float):
        return repr(v)
    return str(v)


def build_artifact_key(workspace_id: str, audit_query_id: str) -> str:
    """Deterministic object key. Mirrored by the dashboard download URL."""
    return f"{_KEY_PREFIX}/{workspace_id}/{audit_query_id}.csv"


async def persist_csv_artifact(
    *,
    workspace_id: str,
    audit_query_id: str,
    column_names: list[str] | tuple[str, ...],
    rows: list[tuple] | tuple[tuple, ...],
) -> str | None:
    """Write the CSV bytes to MinIO and return the object key. Returns
    None on any storage failure — caller must tolerate this and proceed
    without the artifact. Storage failures are logged at WARNING."""
    if not rows:
        # No rows → no artifact. Audit row still lands with row_count=0.
        return None

    payload = rows_to_csv_bytes(column_names, rows)
    key = build_artifact_key(workspace_id, audit_query_id)

    try:
        from kb.storage import get_minio_client
        client = get_minio_client()
        # Best-effort bucket creation.
        try:
            if not client.bucket_exists(_BUCKET):
                client.make_bucket(_BUCKET)
        except Exception:  # noqa: BLE001
            # If bucket creation fails (e.g. permissions), still try put.
            pass

        client.put_object(
            _BUCKET, key,
            io.BytesIO(payload),
            length=len(payload),
            content_type="text/csv",
        )
        return key
    except Exception as exc:  # noqa: BLE001
        _LOG.warning(
            "Q-mode CSV artifact persistence failed (workspace=%s, audit_query_id=%s): %s",
            workspace_id, audit_query_id, exc,
        )
        return None


async def fetch_csv_artifact(
    workspace_id: str, audit_query_id: str,
) -> bytes | None:
    """Read back a previously persisted CSV. Returns None when missing
    or unreachable (caller renders a 404)."""
    key = build_artifact_key(workspace_id, audit_query_id)
    try:
        from kb.storage import get_minio_client
        client = get_minio_client()
        response = client.get_object(_BUCKET, key)
        try:
            return response.read()
        finally:
            try:
                response.close()
                response.release_conn()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        _LOG.warning(
            "Q-mode CSV artifact fetch failed (workspace=%s, audit_query_id=%s): %s",
            workspace_id, audit_query_id, exc,
        )
        return None
