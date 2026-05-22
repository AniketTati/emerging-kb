# API Contracts

> **Single source of truth for HTTP endpoints.** Updated at each phase's G2 gate. No endpoint exists in production until its contract is signed off here. Mistakes here cascade — contracts lock *before* G3 tests start.

**Owner:** Aniket
**Started:** 2026-05-22 (Phase 0 G2)
**Status:** Phase 0 contracts drafted · awaiting sign-off.

---

## 0. Conventions

These apply to every endpoint unless a contract explicitly overrides.

### 0.1 Transport

- All endpoints accept and return `application/json` unless noted (e.g., SSE streams use `text/event-stream`).
- All requests and responses use UTF-8.
- HTTP/1.1 minimum; HTTP/2 supported.

### 0.2 Identifiers + timestamps

- All entity IDs are **UUIDv7** (time-sortable). Returned as canonical lowercase hex strings.
- All timestamps are **ISO-8601 UTC** with `Z` suffix (`2026-05-22T12:34:56Z`).
- Pagination cursors are opaque base64 strings — clients do not parse them.

### 0.3 Errors

All error responses follow **RFC 9457 `application/problem+json`**:

```json
{
  "type": "https://kb.example.com/errors/<slug>",
  "title": "Short human-readable summary",
  "status": 400,
  "detail": "Longer explanation specific to this occurrence",
  "instance": "/the/request/path"
}
```

Additional fields may be added per error class (e.g., `validation_errors` array on 422).

### 0.4 Standard status codes

| Code | When |
|---|---|
| `200 OK` | Successful read / idempotent action |
| `201 Created` | Successful create |
| `202 Accepted` | Async work enqueued; client polls or subscribes to SSE for progress |
| `204 No Content` | Successful delete or empty success |
| `400 Bad Request` | Malformed request (parse error, missing required field) |
| `401 Unauthorized` | Auth required and missing/invalid (auth introduced in a later phase) |
| `403 Forbidden` | Authenticated but not permitted |
| `404 Not Found` | Resource does not exist |
| `409 Conflict` | State conflict (e.g., schema version mismatch) |
| `422 Unprocessable Entity` | Validation error (FastAPI/pydantic default) |
| `429 Too Many Requests` | Rate-limited; `Retry-After` header set |
| `500 Internal Server Error` | Unexpected server failure (logged + alerted) |
| `503 Service Unavailable` | A dependency the endpoint needs is down (see `/ready`) |

### 0.5 Idempotency

Any non-trivial `POST`/`PUT`/`DELETE` accepts the `Idempotency-Key` header (UUIDv7 recommended). The server stores the response in `idempotency_keys` and replays it on retry. Read-only endpoints (`GET`) are inherently idempotent and do not require the header.

### 0.6 Versioning

The API is unversioned in the URL during pre-1.0 development. Breaking changes are tracked in a CHANGELOG once we reach external consumers.

### 0.7 CORS

CORS is permissive in dev (`*`), allow-listed in production via `KB_CORS_ORIGINS` env. Detailed config lands when the UI phase (10a) opens.

### 0.8 Observability hooks (cross-cutting)

Every endpoint:
- Emits a structured access log line with `request_id`, `method`, `path`, `status`, `latency_ms`, `user_id` (when authenticated).
- Sets a `X-Request-Id` response header (UUIDv7) — present even on errors.
- Does **not** log probe endpoints (`/health`, `/ready`) — orchestrators poll them every few seconds.

---

## 1. Phase 0 — Lifecycle endpoints

Two endpoints. Both unauthenticated. Both designed for orchestrator consumption (docker-compose healthcheck, k8s probes, load balancers).

### 1.1 `GET /health` — liveness probe

**Purpose:** Is the FastAPI process alive and serving requests? Used by orchestrators to decide whether to restart the container.

**Auth:** none.

**Request:**
- No headers required.
- No body.

**Response — `200 OK`:**

```json
{
  "status": "ok",
  "service": "kb-api",
  "version": "0.1.0",
  "ts": "2026-05-22T12:34:56Z"
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"ok"` if the endpoint responds. |
| `service` | string | `"kb-api"` for the FastAPI process. Different value if `/health` is also exposed on the worker (not in Phase 0). |
| `version` | string | Semver pulled from `pyproject.toml` at startup. |
| `ts` | string | Server's current UTC timestamp. |

**Errors:** none. If the process is down or unresponsive, the request fails at the TCP/HTTP layer (no JSON response). That **is** the signal.

**Design notes:**
- Liveness only. Does **not** check the database, MinIO, or any other dependency. A degraded dependency must not cause container restart.
- Must respond in < 100ms p99.
- Must not log every hit.
- Implementation budget: trivial. Reads version from cached value at startup.

---

### 1.2 `GET /ready` — readiness probe

**Purpose:** Can this instance serve real traffic? Used by load balancers to decide whether to route requests. Returns `200` only when all critical dependencies are reachable and migrations are current; returns `503` otherwise so the LB drains traffic away.

**Auth:** none.

**Request:**
- No headers required.
- No body.

**Response — `200 OK` (ready):**

```json
{
  "status": "ready",
  "ts": "2026-05-22T12:34:56Z",
  "checks": {
    "db": { "status": "ok", "latency_ms": 3 },
    "minio": { "status": "ok", "latency_ms": 7 },
    "migrations": { "status": "ok", "applied_count": 5 }
  }
}
```

**Response — `503 Service Unavailable` (not ready):**

```json
{
  "status": "not_ready",
  "ts": "2026-05-22T12:34:56Z",
  "checks": {
    "db":         { "status": "fail", "error": "connection refused" },
    "minio":      { "status": "ok",   "latency_ms": 9 },
    "migrations": { "status": "fail", "error": "pending migration: 0006_chunks.sql" }
  }
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | `"ready"` (all checks ok) or `"not_ready"` (any check fail). |
| `ts` | string | Server's current UTC timestamp. |
| `checks` | object | Map of dependency name → check result. |
| `checks.<dep>.status` | string | `"ok"` or `"fail"`. |
| `checks.<dep>.latency_ms` | int | Present only when `status = "ok"`. Time the check took. |
| `checks.<dep>.error` | string | Present only when `status = "fail"`. Human-readable cause. |
| `checks.migrations.applied_count` | int | Present on `migrations.status = "ok"`. Number of migrations recorded in `schema_migrations`. |

**Checks performed (Phase 0 set):**

| # | Dependency | Check | Timeout | Failure means |
|---|---|---|---|---|
| 1 | `db` | `SELECT 1` against the Postgres pool. | 2s | Postgres unreachable or pool exhausted. |
| 2 | `minio` | `HEAD /minio/health/live` against the MinIO endpoint. | 2s | Object store unreachable. |
| 3 | `migrations` | Compare `count(*)` in `schema_migrations` against on-disk `migrations/sql/*.sql` file count. | 1s | Pending migration not yet applied — instance must not serve traffic. |

Future phases append checks (Procrastinate worker queue, embedding API reachability, rerank API reachability, etc.) at their own G2.

**Errors:**

A check that exceeds its timeout is recorded as `{"status": "fail", "error": "timeout after Ns"}` — the overall response stays well-formed; only the HTTP status flips to `503`.

A bug inside the readiness endpoint itself returns `500 Internal Server Error` with `application/problem+json` body. Load balancers treat `500` the same as `503` for traffic routing.

**Design notes:**
- Checks run in parallel (`asyncio.gather`); overall response budget: 5s wall-clock.
- `503` with body (not `200 degraded`) — load balancers and k8s only drain on non-2xx.
- Must not log every hit.
- Implementation lives in `src/kb/api/readiness.py` (G4). Each check is a small async function with its own timeout. New phases append to a registry — they do not edit the endpoint handler.

---

## 2. Future phases — placeholders

Each phase appends its endpoint contracts here at its G2 gate. Index:

| Phase | Endpoint group | Status |
|---|---|---|
| 0 | `/health`, `/ready` | 🟡 drafted (this commit) — awaiting sign-off |
| 1 | Schema CRUD + versioning + hierarchy | ⬜ |
| 2–7 | Internal worker triggers + admin endpoints (TBD at each phase's G1) | ⬜ |
| 8 | `/query`, `/chat`, `/chat/:id/stream` | ⬜ |
| 9 | `/upload/:id/status` (SSE), `/audit` | ⬜ |
| 10a–g | UI-driven endpoints follow from `prototype/wiring_inventory.md` | ⬜ |

---

## 3. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-22 | File created at Phase 0 G2. §0 conventions + §1 Phase 0 contracts (`/health`, `/ready`) drafted. Awaiting sign-off. | Aniket |
