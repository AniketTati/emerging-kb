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

- Entity IDs are UUIDs in canonical lowercase form. **UUIDv4** is the default for primary keys (PG's `gen_random_uuid()` is fine when time-sortability isn't a query pattern — e.g. `schemas.id`, `audit_log.id`). **UUIDv7** is required where monotonic-by-creation ordering is queried or compared — e.g. `X-Request-Id` (trace correlation), and the Phase 8+ `query_id` used to thread results through the audit log. Each phase's G1 picks the flavor per table and records it as a decision.
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

## 2. Phase 1a — Schemas CRUD foundation

Per [build_tracker §5.2](build_tracker.md). Five endpoints under `/schemas`. All workspace-scoped (RLS day-1), Idempotency-Key honored per the rules in [§0.5](#05-idempotency).

### 2.1 Schema resource shape

The canonical schema object returned by every endpoint:

```json
{
  "id": "0193b1f0-d27e-7c2a-9c11-9a3f8c1c9c11",
  "name": "ContractV1",
  "description": "Vendor agreements with delivery + indemnity clauses.",
  "lifecycle_state": "active",
  "created_at": "2026-05-23T12:00:00Z",
  "updated_at": "2026-05-23T12:00:00Z"
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string (uuid) | UUIDv4 from `gen_random_uuid()` per §0.2 (time-sort not a query pattern for schemas). Stable across renames. |
| `name` | string | 1–200 chars. Unique within workspace among `active` schemas. |
| `description` | string | 0–10000 chars. Empty string default; never `null`. |
| `lifecycle_state` | string | `"active"` always on responses (deleted rows return 404). |
| `created_at` | string (ISO-8601 UTC) | Set at insert; never changes. |
| `updated_at` | string (ISO-8601 UTC) | Bumped on every mutation. |

`workspace_id` is **not** in the response — clients know their own workspace, and surfacing it invites the misread "this object belongs to a different workspace than I think it does."

### 2.2 `POST /schemas` — create a schema

**Auth:** none in Phase 1a (workspace resolved by middleware from request context).
**Idempotency:** `Idempotency-Key` header **required**. POST creates a new resource; without idempotency, a network retry could create duplicates.

**Request:**

```http
POST /schemas
Content-Type: application/json
Idempotency-Key: <client-generated-uuid>

{
  "name": "ContractV1",
  "description": "Vendor agreements with delivery + indemnity clauses."
}
```

| Body field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | 1–200 chars |
| `description` | string | no | 0–10000 chars; defaults to `""` |

**Success — `201 Created`:** schema object (§2.1) in body. `Location: /schemas/<id>` header.

**Errors:**

| Status | When | `problem+json` `type` slug |
|---|---|---|
| `400` | malformed JSON, missing `Idempotency-Key` | `bad-request`, `missing-idempotency-key` |
| `409` | another active schema with the same name in this workspace | `schema-name-conflict` |
| `422` | name too long / too short / wrong type, description too long | `validation-error` |

**Idempotency replay:** if a row exists in `idempotency_keys` for `(workspace_id, Idempotency-Key)`, return the cached `response` with `status_code` (verbatim), don't re-execute. TTL handled by Phase 9 cleanup; cached entries are valid indefinitely until then.

### 2.3 `GET /schemas` — list active schemas

**Auth:** none.
**Idempotency:** N/A (read).

**Request:**

```http
GET /schemas?limit=50&offset=0
```

| Query param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `limit` | int | no | 50 | 1–200 |
| `offset` | int | no | 0 | ≥ 0 |

Only `lifecycle_state='active'` rows in the caller's workspace. Sort: `created_at DESC` (stable; ties broken by `id DESC`).

**Success — `200 OK`:**

```json
{
  "items": [ <schema>, <schema>, ... ],
  "total": 42,
  "limit": 50,
  "offset": 0
}
```

| Field | Type | Notes |
|---|---|---|
| `items` | array | Schema objects (§2.1). |
| `total` | int | Total active schemas in workspace (across all pages). |
| `limit` | int | Echoed back. |
| `offset` | int | Echoed back. |

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `400` | `limit > 200`, `offset < 0`, non-int values | `bad-request` |

### 2.4 `GET /schemas/:id` — read one schema

**Auth:** none.
**Idempotency:** N/A (read).

**Path param:** `id` — schema UUID.

**Success — `200 OK`:** schema object (§2.1).

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `404` | schema does not exist, OR is `lifecycle_state='deleted'`, OR belongs to a different workspace (RLS filter, but expressed as 404 not 403 — clients can't tell the workspace exists at all) | `not-found` |

### 2.5 `PUT /schemas/:id` — full-replace name + description

**Auth:** none.
**Idempotency:** `Idempotency-Key` header **optional**. Resource-level PUT is naturally idempotent; the header gates response replay.

**Phase 1b will wrap this endpoint** with the "always create a new version" trigger. The contract here stays stable; the side effect changes.

**Request:**

```http
PUT /schemas/0193b1f0-d27e-7c2a-9c11-9a3f8c1c9c11
Content-Type: application/json
Idempotency-Key: <optional-client-uuid>

{
  "name": "ContractV2",
  "description": "Updated."
}
```

Body shape identical to POST §2.2.

**Success — `200 OK`:** updated schema object (§2.1) with bumped `updated_at`.

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `404` | not found / deleted / wrong workspace | `not-found` |
| `409` | proposed `name` collides with another active schema in this workspace | `schema-name-conflict` |
| `422` | validation | `validation-error` |

### 2.6 `DELETE /schemas/:id` — soft delete

**Auth:** none.
**Idempotency:** `Idempotency-Key` header **optional**.

Soft-deletes by setting `lifecycle_state='deleted'` and bumping `updated_at`. The row remains in the table for Phase 9 audit-log integration; no hard delete until at least Phase 9.

**Request:**

```http
DELETE /schemas/0193b1f0-d27e-7c2a-9c11-9a3f8c1c9c11
```

**Success — `204 No Content`.** Empty body. `X-Request-Id` header set as always.

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `404` | not found / already deleted / wrong workspace | `not-found` |

Note on idempotency: a second DELETE on the same id returns `404`, not `204`. RFC 7231 says DELETE is "idempotent" in the sense that multiple calls leave the system in the same final state — it does NOT mandate that the response codes match. The 404-on-second-delete pattern is standard (the resource is no longer findable). Clients that want guaranteed-success-on-retry should use `Idempotency-Key`.

### 2.7 Common headers + cross-cutting behavior

Every endpoint in §2 inherits:
- `X-Request-Id` on every response (incl. errors) — generated UUIDv7 or echoed from request header (§0.8).
- Probe-style access-log skipping does **not** apply — `/schemas/*` are real endpoints and get full access logs.
- Workspace context resolved by middleware. Phase 1a default = the zero-UUID sentinel; explicit `X-Test-Workspace` header overrides (test-only).
- All errors follow RFC 9457 `application/problem+json` (§0.3). `type` field uses the slugs in the tables above.

### 2.8 Out of scope for Phase 1a

Listed here so reviewers (and future me) can verify nothing leaks:
- `current_version_id` field on the schema object — **Phase 1b**.
- `GET /schemas/:id/versions`, `POST /schemas/:id/versions/:v/rollback` — **Phase 1b**.
- Nested `entities`, `fields`, `relationships` arrays on the schema object — **Phase 1c**.
- `POST /schemas/:id/entities`, `POST /schemas/:id/entities/:eid/fields`, `POST /schemas/:id/relationships` — **Phase 1c**.
- NL field descriptions (`nl_description` column on `schema_fields`) — **Phase 1c**.
- domain_vocabulary endpoints — **Phase 5**.
- `audit_log` writes on create/update/delete — **Phase 9** (decides backfill vs forward-only).
- Cursor pagination — Phase 8+ when first endpoint needs it.

---

## 3. Future phases — placeholders

Each phase appends its endpoint contracts here at its G2 gate. Index:

| Phase | Endpoint group | Status |
|---|---|---|
| 0 | `/health`, `/ready` | ✅ signed off 2026-05-23 |
| **1a** | `/schemas` CRUD (POST/GET-list/GET/PUT/DELETE) | 🟡 drafted in §2 — awaiting sign-off |
| 1b | `/schemas/:id/versions*` (versioning + rollback) | ⬜ |
| 1c | `/schemas/:id/{entities,fields,relationships}` (hierarchy) | ⬜ |
| 2–7 | Internal worker triggers + admin endpoints (TBD at each phase's G1) | ⬜ |
| 8 | `/query`, `/chat`, `/chat/:id/stream` | ⬜ |
| 9 | `/upload/:id/status` (SSE), `/audit` | ⬜ |
| 10a–g | UI-driven endpoints follow from `prototype/wiring_inventory.md` | ⬜ |

---

## 4. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-22 | File created at Phase 0 G2. §0 conventions + §1 Phase 0 contracts (`/health`, `/ready`) drafted. Awaiting sign-off. | Aniket |
| 2026-05-23 | **Re-validated against re-opened Phase 0 G1.** No contract changes required: `/ready`'s `migrations` check still reads `schema_migrations`; `Idempotency-Key` header is still backed by the `idempotency_keys` table (now workspace-scoped via primary key `(workspace_id, key)` — server-side detail, invisible to clients). `X-Request-Id` header promise in §0.8 is now backed by middleware in G1 plan. | Aniket |
| 2026-05-23 | **Phase 1a G2 — schemas CRUD contracts drafted.** §2 added with 5 endpoints (POST/GET-list/GET/PUT/DELETE) under `/schemas`. Schema response shape (no `workspace_id` field — clients know their own). Body validation rules. RFC 9457 error slugs per endpoint (`schema-name-conflict`, `not-found`, `validation-error`, `bad-request`, `missing-idempotency-key`). Idempotency: required on POST, optional on PUT/DELETE. §3 placeholder index renumbered + split: Phase 1 row → 1a/1b/1c. §0 placeholder section renumbered to §3; this changelog renumbered to §4. | Aniket |
| 2026-05-23 | **§0.2 UUID convention broadened (post-G3 consistency sweep).** Old text said "All entity IDs are UUIDv7"; reality is Phase 0 ships `audit_log.id` as v4 and Phase 1a chose v4 for `schemas.id`. Honest replacement: v4 by default for PKs where time-sortability isn't a query pattern; v7 required where monotonic-by-creation ordering is queried (X-Request-Id, future `query_id`). Each phase's G1 picks the flavor per table. §2.1 `id` field annotated to cite this. | Aniket |
