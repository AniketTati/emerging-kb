"""B9 / WA-16 — Directory loader (programmatic ingest via POST /files).

Walks a directory tree, POSTs each supported file via the existing
multipart endpoint. The worker pipeline fires the usual parse → chunk →
contextualize → embed → extract chain — same path as a real upload.

Used by:
  - scripts/run_eval.sh   (load CUAD/Enron/SEC corpora)
  - tests/test_b9_api.py  (load tests/fixtures/ for the harness E2E)
  - operators bootstrapping a workspace from a folder

Returns an `IngestionReport` aggregating per-file status so callers can
surface failures (parse error, oversize, mime-rejected) without
silently dropping rows.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


_LOG = logging.getLogger(__name__)


# Mime guesses keyed by file suffix. The server re-validates via magic
# bytes, so this is best-effort and we don't need to be exhaustive.
_MIME_BY_SUFFIX: dict[str, str] = {
    ".pdf": "application/pdf",
    ".eml": "message/rfc822",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


_SUPPORTED_SUFFIXES: frozenset[str] = frozenset(_MIME_BY_SUFFIX.keys())


@dataclass(frozen=True)
class IngestionResult:
    """One file's outcome."""
    path: str
    file_id: str | None
    status: str        # 'ok' | 'duplicate' | 'rejected' | 'error'
    http_status: int
    detail: str | None = None
    name: str = ""


@dataclass(frozen=True)
class IngestionReport:
    """Per-run aggregate."""
    items: tuple[IngestionResult, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def ok(self) -> int:
        return sum(1 for i in self.items if i.status == "ok")

    @property
    def duplicates(self) -> int:
        return sum(1 for i in self.items if i.status == "duplicate")

    @property
    def errors(self) -> int:
        return sum(
            1 for i in self.items if i.status in ("rejected", "error")
        )

    def summary(self) -> str:
        return (
            f"ingest summary — total={self.total} ok={self.ok} "
            f"duplicates={self.duplicates} errors={self.errors}"
        )


def _guess_mime(path: Path) -> str | None:
    return _MIME_BY_SUFFIX.get(path.suffix.lower())


async def ingest_file(
    client: httpx.AsyncClient,
    path: Path,
    *,
    workspace_id: str,
    name_override: str | None = None,
) -> IngestionResult:
    """POST one file via multipart. Returns IngestionResult — never raises."""
    if not path.is_file():
        return IngestionResult(
            path=str(path), file_id=None, status="error", http_status=0,
            detail="not a regular file", name=path.name,
        )

    mime = _guess_mime(path)
    if mime is None:
        return IngestionResult(
            path=str(path), file_id=None, status="rejected", http_status=0,
            detail=f"unsupported suffix {path.suffix!r}",
            name=path.name,
        )

    try:
        body = path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        return IngestionResult(
            path=str(path), file_id=None, status="error", http_status=0,
            detail=f"read failed: {exc}", name=path.name,
        )

    name = name_override or path.name
    files = {"file": (name, body, mime)}
    data = {"name": name}
    idem_key = str(uuid.uuid4())

    try:
        resp = await client.post(
            "/files",
            files=files,
            data=data,
            headers={
                "X-Test-Workspace": workspace_id,
                "Idempotency-Key": idem_key,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return IngestionResult(
            path=str(path), file_id=None, status="error", http_status=0,
            detail=f"HTTP error: {exc}", name=name,
        )

    payload: dict[str, Any] = {}
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        pass

    file_id = (
        payload.get("file_id") or payload.get("id") or payload.get("fileId")
    )
    if resp.status_code in (200, 201) and file_id:
        # 200 = dedup hit; 201 = new file. Both are "successful ingest".
        status = "duplicate" if resp.status_code == 200 else "ok"
        return IngestionResult(
            path=str(path), file_id=str(file_id),
            status=status, http_status=resp.status_code, name=name,
        )

    return IngestionResult(
        path=str(path), file_id=None, status="error",
        http_status=resp.status_code,
        detail=payload.get("detail") or payload.get("title") or str(payload)[:200],
        name=name,
    )


async def ingest_directory(
    client: httpx.AsyncClient,
    directory: Path | str,
    *,
    workspace_id: str,
    recursive: bool = True,
    limit: int | None = None,
    concurrency: int = 4,
) -> IngestionReport:
    """Walk `directory` and POST every supported file. Returns one
    IngestionResult per file attempted.

    Concurrency limits parallel uploads; the worker queue handles the
    parse/extract pipeline downstream so we don't need to throttle that."""
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"{root} is not a directory")

    candidates = _collect_candidates(root, recursive=recursive)
    if limit is not None:
        candidates = candidates[:max(0, int(limit))]

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _bound_ingest(p: Path) -> IngestionResult:
        async with sem:
            return await ingest_file(
                client, p, workspace_id=workspace_id,
            )

    results = await asyncio.gather(
        *[_bound_ingest(p) for p in candidates]
    )
    report = IngestionReport(items=tuple(results))
    _LOG.info(report.summary())
    return report


def _collect_candidates(root: Path, *, recursive: bool) -> list[Path]:
    iterator = root.rglob("*") if recursive else root.iterdir()
    out: list[Path] = []
    for p in iterator:
        if not p.is_file():
            continue
        if p.suffix.lower() not in _SUPPORTED_SUFFIXES:
            continue
        out.append(p)
    out.sort()
    return out
