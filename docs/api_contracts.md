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

## 3. Phase 1b — Schemas versioning

Per [build_tracker §5.3](build_tracker.md). Adds **immutable version history** on top of the §2 CRUD surface. Two §2 endpoints (`POST`, `PUT`) mutate; three new endpoints expose the history.

### 3.1 Versioning model (the invariants every endpoint depends on)

1. **Append-only.** A version is never updated or deleted. The version table is `GRANT SELECT, INSERT` only — no UPDATE or DELETE GRANTed to `kb_app`.
2. **Atomic with the mutation that creates it.** `POST /schemas` writes the schema row + `version_number=1` in one transaction. `PUT` writes the row update + a new version in one transaction. A reader can never observe `schemas` and `schema_versions` out of sync.
3. **"Schema exists ⇒ ≥1 version exists."** `schemas.current_version_id` is `NOT NULL` after every successful mutation. (It's defined `NULL`-able only so the DDL can apply to an empty DB.)
4. **Monotonic integer per schema.** `version_number` is allocated as `max(version_number)+1 WHERE schema_id=...` inside the mutation tx. Unique per `(schema_id, version_number)`. Never reused even after rollback.
5. **Rollback = clone-forward, not mutate-back.** Rolling back to v3 from v7 produces v8 with `body = v3.body` and `kind='rollback'`. v3 is unchanged. v7 stays in the log. You can rollback the rollback.
6. **Idempotency-Key replay never duplicates a version.** A replayed `POST`, `PUT`, or rollback returns the cached body without writing a new `schema_versions` row.
7. **Workspace-isolated.** `schema_versions` has its own `workspace_id` column + its own RLS policy. A version is never reachable from another workspace (RLS expresses this as 404, not 403, matching §2.4).

### 3.2 Mutated schema resource shape (additive to §2.1)

The schema object returned by every §2 endpoint now includes one new field:

```json
{
  "id": "0193b1f0-d27e-7c2a-9c11-9a3f8c1c9c11",
  "name": "ContractV1",
  "description": "Vendor agreements with delivery + indemnity clauses.",
  "lifecycle_state": "active",
  "current_version": 1,
  "created_at": "2026-05-23T12:00:00Z",
  "updated_at": "2026-05-23T12:00:00Z"
}
```

| Field added | Type | Notes |
|---|---|---|
| `current_version` | int | The `version_number` (≥1) of the head of this schema's version log. Always present (invariant #3). Bumps by ≥1 on every successful `PUT` and rollback. |

### 3.3 `POST /schemas` — create a schema (+ v1 atomically)

**Contract unchanged from §2.2**, with two behavioural additions:

- Response body now includes `current_version: 1`.
- Server writes one `schema_versions` row in the same transaction (`version_number=1`, `parent_version_number=NULL`, `kind='post'`, `body={"name":..., "description":...}`).
- Idempotency replay: cached body is returned verbatim; no second `schema_versions` row is written.

### 3.4 `PUT /schemas/:id` — full-replace + new version

**Contract unchanged from §2.5**, with these behavioural additions:

- Response body includes the bumped `current_version` — `prior + 1` for any single client. (Concurrent PUTs serialize per-schema server-side via `SELECT ... FOR UPDATE` on the `schemas` row, so version numbers stay contiguous and the UNIQUE `(schema_id, version_number)` constraint is never raced into.)
- Server writes one new `schema_versions` row in the same transaction (`version_number = prior+1`, `parent_version_number = prior`, `kind='put'`, `body={"name":..., "description":...}`).
- Idempotency replay: cached body returned; no second version row.
- Concurrency: last-writer-wins. Two simultaneous PUTs both succeed and both produce their own version rows (contiguous numbers); the schema row's final `name`/`description` reflect whichever transaction committed last. Optimistic-lock `If-Match` header is **not honored in Phase 1b** (Phase 10d Schema Studio surfaces the diff to the user).

### 3.5 Schema version resource shape

The canonical version object returned by §3.7 and §3.8 (and embedded in §3.6's list-item form):

```json
{
  "version": 3,
  "kind": "put",
  "body": {
    "name": "ContractV3",
    "description": "Adds indemnity clauses to ContractV2."
  },
  "parent_version": 2,
  "diff_from_prior": {
    "added": [],
    "removed": [],
    "changed": [
      {"path": "description", "old": "Adds indemnity clauses.", "new": "Adds indemnity clauses to ContractV2."}
    ]
  },
  "created_at": "2026-05-23T12:05:00Z"
}
```

| Field | Type | Notes |
|---|---|---|
| `version` | int | The `version_number`. ≥1. |
| `kind` | string | `"post"` (only v1), `"put"`, or `"rollback"`. |
| `body` | object | Full snapshot. At Phase 1b: `{name, description}`. Phase 1c expands to include nested `entities`, `fields`, `relationships`. |
| `parent_version` | int \| null | The `version_number` this one descended from. `null` only for v1. |
| `diff_from_prior` | object \| null | See §3.6. `null` for v1. Computed at read time, not stored. |
| `created_at` | string (ISO-8601 UTC) | Version row's insert time. |

No `id` (the UUID PK) and no `workspace_id` on the wire — clients reference versions by `(schema_id, version_number)`.

### 3.6 Diff format (`diff_from_prior`)

```json
{
  "added":   [{"path": "<dotted-path>", "value": <new>}],
  "removed": [{"path": "<dotted-path>", "value": <old>}],
  "changed": [{"path": "<dotted-path>", "old": <old>, "new": <new>}]
}
```

Paths are dotted strings (`"description"`, later `"entities.0.fields.2.nl_description"`). At Phase 1b the diff only ever covers `name` and `description`; Phase 1c extends the same shape to nested entities/fields/relationships without changing the format. **Not** RFC 6902 strict JSON Patch (no operation array, no `~0`/`~1` escaping) — the format is declarative for UI rendering, not for replay.

`diff_from_prior` is `null` when `version == 1` (no prior to diff against). For all other versions it is non-null.

### 3.7 `GET /schemas/:id/versions` — list versions, newest-first

**Auth:** none.
**Idempotency:** N/A (read).

**Request:**

```http
GET /schemas/0193b1f0-d27e-7c2a-9c11-9a3f8c1c9c11/versions?limit=50&offset=0
```

| Query param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `limit` | int | no | 50 | 1–200 (matches §2.3) |
| `offset` | int | no | 0 | ≥ 0 |

Sort: `version_number DESC` (i.e., newest first; matches the Schema Studio "Versions" tab UX). Items only show the lightweight summary form — `body` and `diff_from_prior` are not included to keep list responses small.

**Success — `200 OK`:**

```json
{
  "items": [
    {"version": 7, "kind": "rollback", "parent_version": 6, "created_at": "..."},
    {"version": 6, "kind": "put",      "parent_version": 5, "created_at": "..."},
    {"version": 5, "kind": "put",      "parent_version": 4, "created_at": "..."}
  ],
  "total": 7,
  "limit": 50,
  "offset": 0
}
```

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `400` | `limit > 200`, `offset < 0`, non-int values | `bad-request` |
| `404` | parent schema does not exist / is soft-deleted / wrong workspace | `not-found` |

### 3.8 `GET /schemas/:id/versions/:v` — read one version with diff

**Auth:** none.
**Idempotency:** N/A (read).

**Path params:**
- `id` — schema UUID.
- `v` — `version_number` (integer ≥ 1).

**Success — `200 OK`:** version object (§3.5) with `diff_from_prior` computed at read time.

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `404` | parent schema not found / soft-deleted / wrong workspace, OR no `schema_versions` row with that `version_number` for this schema | `not-found` |
| `422` | `v` is not a positive integer | `validation-error` |

### 3.9 `POST /schemas/:id/versions/:v/rollback` — clone v forward as new current version

**Auth:** none.
**Idempotency:** `Idempotency-Key` header **required**. Rollback creates a new `schema_versions` row — same risk profile as POST §2.2.

**Path params:**
- `id` — schema UUID.
- `v` — `version_number` to roll back to (integer ≥ 1).

**Request:**

```http
POST /schemas/0193b1f0-d27e-7c2a-9c11-9a3f8c1c9c11/versions/3/rollback
Content-Type: application/json
Idempotency-Key: <client-generated-uuid>

{}
```

Body is empty (or absent). No fields are accepted — the rollback target is fully specified by the URL.

**Behaviour:**
- Reads v's `body` as the snapshot to restore.
- Inserts a new `schema_versions` row at `version_number = current_version + 1`, `parent_version_number = current_version`, `kind='rollback'`, `body = v.body`.
- Updates `schemas.name`, `schemas.description`, `schemas.current_version_id`, `schemas.updated_at` to reflect the cloned snapshot.
- All in one transaction.

**Success — `200 OK`:** updated schema object (§3.2) with the bumped `current_version`. Same body shape as `PUT`.

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `400` | malformed JSON, missing `Idempotency-Key` | `bad-request`, `missing-idempotency-key` |
| `404` | parent schema not found / soft-deleted / wrong workspace, OR no version with that `version_number` for this schema | `not-found` |
| `409` | `v` IS the current version — no-op rollbacks are rejected to keep the version log meaningful (avoid noise from misclicks) | `rollback-noop` |
| `422` | `v` is not a positive integer | `validation-error` |

**Idempotency replay:** if the same `(workspace_id, Idempotency-Key)` row exists, return the cached response verbatim — no second `schema_versions` row is written even if `current_version` has since advanced.

### 3.10 Out of scope for Phase 1b

Listed so reviewers can verify nothing leaks from later phases:
- `If-Match: <current_version>` optimistic-lock header on `PUT` / rollback — **Phase 10d** UI surfaces the diff conflict.
- `created_by` field on version objects — lands when an auth phase opens.
- `body` field including nested `entities`, `fields`, `relationships` — **Phase 1c** (the `body jsonb` column is forward-compatible; what we write in 1b is the strict subset `{name, description}`).
- Re-extraction trigger on rollback — **Phase 6** ("triggers schema-projection re-extraction on changed fields only" per architecture line 791). Phase 1b only stamps `kind='rollback'` on the row so the worker can find them later.
- `audit_log` writes on any mutation in §3 — **Phase 9**.
- Per-version delete endpoint — never. Versions are an audit trail (decision #9 in build_tracker §5.3).
- Cursor pagination on the version list — Phase 8+ if a schema's history grows past offset+limit's comfort zone.

---

## 4. Phase 1c — Schemas hierarchy

Per [build_tracker §5.4](build_tracker.md). Adds the **entity-type tree** to a schema. 11 endpoints (4 entity + 4 field + 3 relationship) nested under `/schemas/:id/`. Every nested CRUD writes a new `schema_versions` row capturing the full subtree (1b's versioning carries Phase 1c's mutations transparently).

### 4.1 Hierarchy model (the invariants every endpoint depends on)

Three new resources nest under a schema:
- **Entities** — entity types within a schema (e.g., `File`, `Case`, `Note`).
- **Fields** — typed attributes on an entity (e.g., `File.name` is `string`; `Case.opened_at` is `date`). Carry an `nl_description` that Phase 6's Gemini extractor will use as its prompt.
- **Relationships** — typed edges between entity types within the same schema (`kind ∈ {contains, part_of, references, associates, attribute_link}`). Carry `cardinality`, `cascade_delete`, `single_parent` metadata that Phase 6 enforces at extraction time (recorded only here).

Invariants (extend §3.1):
1. **Workspace-isolated.** Each new table has its own `workspace_id` + own RLS policy. Cross-workspace reads/writes return 404, not 403 (same as §2.4 / §3.1 #7).
2. **Parent-scoped soft delete.** A soft-deleted entity hides its fields too (fields filter on the parent's `lifecycle_state='active'`). A soft-deleted relationship is gone from `GET list`. The rows stay in the DB for audit.
3. **Coarse-grained versioning.** Every successful entity / field / relationship mutation writes ONE new row to `schema_versions` whose `body` is the full schema subtree snapshot. The version `kind` becomes `'put'` regardless of which sub-resource changed — it's the **schema** that's versioned, not the sub-resource. This keeps the "Versions" tab in the Schema Studio UI showing a clean linear log per schema.
4. **Atomic mutations.** Each endpoint takes `SELECT ... FOR UPDATE` on the parent `schemas` row (continuing 1b's decision #12) and writes the sub-resource INSERT/UPDATE + the new `schema_versions` row in one transaction.
5. **Cross-resource references use names in snapshots.** The `body.relationships[*]` entries reference entities by their `name`, not their UUID. A rollback that re-creates entities assigns new UUIDs; the relationships re-bind correctly to the new IDs because they're name-resolved at rollback time.
6. **Idempotency-Key replay never duplicates a sub-resource.** Same pattern as §3.1 #6: cached body returned; no second sub-resource row; no second `schema_versions` row.

### 4.2 Extended `schema_versions.body` shape (additive to §3.5)

At Phase 1b: `{name, description}`. At Phase 1c the snapshot grows:

```json
{
  "name": "LegalCorpus",
  "description": "Top-level legal documents.",
  "entities": [
    {
      "name": "File",
      "description": "Case file.",
      "fields": [
        {"name": "title", "type": "string", "nl_description": "The file's display title from the document header.", "is_required": true}
      ]
    },
    {
      "name": "Case",
      "description": "A case within a file.",
      "fields": [
        {"name": "opened_at", "type": "date", "nl_description": "When the case was opened, in ISO date form.", "is_required": false}
      ]
    }
  ],
  "relationships": [
    {"name": "file_contains_case", "kind": "contains", "from": "File", "to": "Case", "cardinality": "one_to_many", "cascade_delete": true, "single_parent": true}
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `entities` | array | Empty `[]` if no entities yet. Items in `name` order. |
| `entities[*].fields` | array | Empty `[]` if no fields yet. Items in `name` order. |
| `relationships` | array | Empty `[]` if no relationships yet. Items in `name` order. `from` / `to` reference entity *names* (per invariant #5). |

The schema response shape itself (§2.1 + §3.2) **does not change** at 1c — the schema object stays `{id, name, description, lifecycle_state, current_version, created_at, updated_at}`. Hierarchy lives at the version-body level. Clients that need the live subtree fetch `GET /schemas/:id/versions/:current`.

### 4.3 Diff format extension (additive to §3.6)

Same `{added, removed, changed}` shape. Paths grow nested-dotted:

- Entity added: `{"path": "entities.File", "value": {...full entity body including fields...}}`.
- Entity removed: same shape under `removed`.
- Field added: `{"path": "entities.File.fields.title", "value": {...field...}}`.
- Field type changed: `{"path": "entities.File.fields.title.type", "old": "string", "new": "datetime"}`.
- Relationship added: `{"path": "relationships.file_contains_case", "value": {...}}`.

`compute_diff` recurses into `entities[*]` and `entities[*].fields[*]` and `relationships[*]`, keyed by `name` (which is unique within its parent scope). No DDL change; same format extends.

### 4.4 Entity resource shape

```json
{
  "id": "0193b1f0-aaaa-7c2a-9c11-9a3f8c1c9c11",
  "name": "File",
  "description": "Top-level case file.",
  "lifecycle_state": "active",
  "created_at": "2026-05-23T12:00:00Z",
  "updated_at": "2026-05-23T12:00:00Z"
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string (uuid) | UUIDv4 (§0.2 broadened). |
| `name` | string | 1–200 chars. Unique within schema among active entities. |
| `description` | string | 0–10000 chars. Empty default; never `null`. |
| `lifecycle_state` | string | Always `"active"` on responses (soft-deleted entities return 404). |
| `created_at` / `updated_at` | string (ISO-8601 UTC) | Same convention as §2.1. |

No `workspace_id`, no `schema_id` in the response (client knows both — `schema_id` is in the URL path).

### 4.5 `POST /schemas/:id/entities` — create entity

**Auth:** none. **Idempotency:** `Idempotency-Key` **required**.

```http
POST /schemas/<id>/entities
Content-Type: application/json
Idempotency-Key: <client-uuid>

{ "name": "File", "description": "Top-level case file." }
```

| Body field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | 1–200 chars |
| `description` | string | no | 0–10000 chars; defaults to `""` |

**Success — `201 Created`:** entity object (§4.4) in body. `Location: /schemas/<id>/entities/<eid>` header. Server writes the entity row + new `schema_versions` row in one tx; `schemas.current_version` bumps.

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `400` | missing `Idempotency-Key` / malformed JSON | `missing-idempotency-key`, `bad-request` |
| `404` | parent schema not found / soft-deleted / wrong workspace | `not-found` |
| `409` | another active entity with the same name in this schema | `entity-name-conflict` |
| `422` | validation | `validation-error` |

### 4.6 `GET /schemas/:id/entities` — list entities

`?limit=50&offset=0` (defaults; 1–200 / ≥0). Sort: `created_at DESC, id DESC`. Filters to `lifecycle_state='active'`.

```json
{ "items": [<entity>, ...], "total": 3, "limit": 50, "offset": 0 }
```

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `400` | `limit > 200`, `offset < 0` | `bad-request` |
| `404` | parent schema not found | `not-found` |

### 4.7 `PUT /schemas/:id/entities/:eid` — full-replace name + description

Body same shape as §4.5 POST. `Idempotency-Key` **optional**.

**Success — `200 OK`:** updated entity object with bumped `updated_at`. Writes a new `schema_versions` row.

**Errors:** `404` (entity not found in schema), `409` (name collides with another active entity), `422` (validation).

### 4.8 `DELETE /schemas/:id/entities/:eid` — soft delete

`Idempotency-Key` **optional**. Returns `204 No Content`. Soft-deletes via `lifecycle_state='deleted'`; writes a new `schema_versions` row. Subsequent reads return 404. **Cascades** (application-level): all `schema_fields` belonging to this entity are also soft-deleted in the same tx, and any `schema_relationships` referencing this entity by `from_entity_id` or `to_entity_id` are also soft-deleted.

Note: a re-created entity with the same name gets a new UUID and starts with zero fields. The partial unique index on `(schema_id, name) WHERE lifecycle_state='active'` permits the re-create.

**Errors:** `404`.

### 4.9 Field resource shape

```json
{
  "id": "0193b1f0-bbbb-7c2a-9c11-9a3f8c1c9c11",
  "name": "opened_at",
  "type": "date",
  "nl_description": "When the case was opened, in ISO date form.",
  "is_required": false,
  "lifecycle_state": "active",
  "created_at": "...",
  "updated_at": "..."
}
```

| Field | Type | Notes |
|---|---|---|
| `name` | string | 1–200; unique within entity among active fields. |
| `type` | string | One of `string`, `number`, `boolean`, `date`, `datetime` (build_tracker decision #2; Phase 6 may extend). |
| `nl_description` | string | 0–10000. Phase 6's Gemini extractor consumes this as the field's extraction prompt. |
| `is_required` | bool | Default `false`. Phase 6 enforces; 1c records. |

### 4.10 `POST /schemas/:id/entities/:eid/fields` — create field

`Idempotency-Key` **required**.

```json
{ "name": "opened_at", "type": "date", "nl_description": "When the case was opened.", "is_required": false }
```

`type` and `nl_description` required in body; `is_required` defaults to `false`.

**Success — `201`:** field object (§4.9). Writes new schema_versions row.

**Errors:** `400` (idempotency-key missing), `404` (schema OR entity not found), `409` (`field-name-conflict`), `422` (validation — incl. `type` outside the enum).

### 4.11 `GET /schemas/:id/entities/:eid/fields` — list fields

Same pagination as §4.6. Filters to active fields on active entity. `404` if parent entity not active.

### 4.12 `PUT /schemas/:id/entities/:eid/fields/:fid`

Body same shape as POST. `Idempotency-Key` **optional**. `200` on success; writes a new `schema_versions` row.

**Errors:** `404`, `409`, `422`.

### 4.13 `DELETE /schemas/:id/entities/:eid/fields/:fid`

`Idempotency-Key` **optional**. `204` on success; writes a new `schema_versions` row. **No cascade** to anything else (fields have no children).

**Errors:** `404`.

### 4.14 Relationship resource shape

```json
{
  "id": "0193b1f0-cccc-7c2a-9c11-9a3f8c1c9c11",
  "name": "file_contains_case",
  "from_entity_id": "0193b1f0-aaaa-...",
  "to_entity_id":   "0193b1f0-eeee-...",
  "kind": "contains",
  "cardinality": "one_to_many",
  "cascade_delete": true,
  "single_parent": true,
  "lifecycle_state": "active",
  "created_at": "...",
  "updated_at": "..."
}
```

| Field | Type | Notes |
|---|---|---|
| `name` | string | 1–200; unique within schema among active relationships. |
| `from_entity_id` / `to_entity_id` | uuid | Both must point at active entities **in the same schema**. Cross-schema FK rejected with `422 validation-error`. NOTE: live objects on the wire use entity UUIDs (so clients can PUT/DELETE specific entities). Snapshot bodies in `schema_versions.body` use entity **names** (so a rollback that re-creates the entity gets a new UUID and re-binds by name per invariant §4.1 #5). |
| `kind` | string | Architecture line 794: `contains`, `part_of`, `references`, `associates`, `attribute_link`. |
| `cardinality` | string | `one_to_one`, `one_to_many`, `many_to_many`. Default `one_to_many`. |
| `cascade_delete` | bool | Default `false`. Phase 6 enforces at extraction-time deletion. |
| `single_parent` | bool | Default `true`. Phase 6 enforces at extraction-time entity assignment for `contains` / `part_of` edges. |

### 4.15 `POST /schemas/:id/relationships` — create typed edge

`Idempotency-Key` **required**.

```json
{
  "name": "file_contains_case",
  "from_entity_id": "0193b1f0-aaaa-...",
  "to_entity_id":   "0193b1f0-eeee-...",
  "kind": "contains",
  "cardinality": "one_to_many",
  "cascade_delete": true,
  "single_parent": true
}
```

`name`, `from_entity_id`, `to_entity_id`, `kind` are required. `cardinality` defaults to `one_to_many`; `cascade_delete` to `false`; `single_parent` to `true`.

**Success — `201`:** relationship object (§4.14). Writes new schema_versions row.

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `400` | missing Idempotency-Key | `missing-idempotency-key` |
| `404` | parent schema not found | `not-found` |
| `409` | another active relationship with same name in this schema | `relationship-name-conflict` |
| `422` | `kind` outside enum, `cardinality` outside enum, `from_entity_id` or `to_entity_id` doesn't reference an active entity in this schema (different schema → 422) | `validation-error` |

### 4.16 `GET /schemas/:id/relationships`

Same pagination. Filters to active relationships on active schema. `404` if schema not active.

### 4.17 `DELETE /schemas/:id/relationships/:rid`

`Idempotency-Key` **optional**. `204` on success; writes a new `schema_versions` row.

**Errors:** `404`.

### 4.18 Out of scope for Phase 1c

- `PUT /relationships/:rid` — soft-delete + re-create is the path for now; type-edge mutation rare enough that the simpler API wins.
- `extracted_entities` table + `lineage_path` ltree — **Phase 5/6**.
- Helper endpoints `/entities/:id/{descendants, ancestors, siblings, breadcrumb}` — **Phase 8**.
- DB-level enforcement of `single_parent` and `cascade_delete` — **Phase 6** (extraction time).
- Field type enum extensions (`currency`, `email`, `list_X`, etc.) — **Phase 6** when extraction needs them.
- `domain_vocabulary` (synonyms, acronyms, definitions) — **Phase 5**.
- `audit_log` writes on nested mutation — **Phase 9**.

---

## 5. Phase 2a — Files + parse pipeline (scaffold + Docling)

Per [build_tracker §5.5](build_tracker.md). Five endpoints under `/files`. First worker phase — the HTTP layer is mostly admin/metadata; the heavy lifting happens in the Procrastinate `parse_file` task.

### 5.1 Pipeline model (the invariants every endpoint depends on)

1. **MinIO holds bytes, Postgres holds metadata.** `files.object_key` references a MinIO object under `raw_files/<sha256>`. Never store file bytes in PG.
2. **Content-hash dedup per workspace.** `(workspace_id, content_sha)` partial unique among `lifecycle_state != 'deleted'` rows. Re-uploading the same content returns the existing `files` row (not a 409).
3. **Lifecycle state machine** (`files.lifecycle_state`): `queued → parsing → parsed → chunked → contextualized → embedded → raptor_building → ready | failed`; soft-delete via `→ deleted` from any non-failed state. Transitions are append-only logged to `file_lifecycle` (immutable audit table). Phase 3a added `chunked` (chained `chunk_file`); Phase 3b added `contextualized` (chained `contextualize_file` — Anthropic-style prefix LLM call with prompt-cached doc context; 3b-bis adds the Gemini adapter); Phase 3c added `embedded` (chained `embed_file` — Gemini Embedding 001 with DeterministicMockEmbedder fallback when `KB_GEMINI_API_KEY` is unset); Phase 3d added the intermediate `raptor_building` + terminal `ready` (chained `raptor_build_file` — per-doc RAPTOR tree). **Each sub-phase appends exactly one or two new states to the enum** — existing readers ignore unknown states (forward-compatible). Phase 3e (corpus-level RAPTOR) does NOT extend this enum — corpus trees are workspace-scoped, not file-scoped.
4. **`raw_pages` immutable.** Per-page content keyed by `(file_id, page_number)`. `GRANT SELECT, INSERT` only. Re-parsing the same content produces byte-identical rows (content-hash keyed).
5. **Per-stage idempotency.** If `parse_file(file_id)` is replayed and `files.lifecycle_state == 'parsed'`, the task returns immediately without re-work.
6. **Workspace-isolated.** All 4 new tables carry own `workspace_id` + own RLS policy. The worker calls `SET LOCAL app.workspace_id` before any per-file query.

### 5.2 File resource shape

```json
{
  "id": "0193b2a0-1111-7c2a-9c11-9a3f8c1c9c11",
  "name": "acme_contract_v3.pdf",
  "content_sha": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "mime_type": "application/pdf",
  "size_bytes": 184321,
  "doc_type": null,
  "lifecycle_state": "parsed",
  "created_at": "2026-05-23T12:00:00Z",
  "updated_at": "2026-05-23T12:00:15Z"
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | UUIDv4. |
| `name` | string | 1–500 chars. Display name from upload. NOT unique. |
| `content_sha` | string | sha256 lower-hex (64 chars). Unique with `workspace_id` among non-deleted. |
| `mime_type` | string | From upload's Content-Type or sniffed from magic bytes. |
| `size_bytes` | int | Raw byte count. |
| `doc_type` | string \| null | Always `null` at Phase 2a (classifier lands in a later phase). |
| `lifecycle_state` | enum | `queued/parsing/parsed/chunked/contextualized/embedded/raptor_building/ready/failed` — `deleted` returns 404 on reads. Terminal success state is `ready` (Phase 3d). Each parsing→…→ready transition appends an entry to `lifecycle` history (§5.3). |

No `workspace_id`, no `object_key` in response — `object_key` is a server-internal detail (clients don't read MinIO directly).

### 5.3 Lifecycle history shape

`GET /files/:id` includes a `lifecycle` array of state-transition events:

```json
{
  "lifecycle": [
    {"from_state": null,      "to_state": "queued",  "event": "upload",      "payload": {},                                "created_at": "..."},
    {"from_state": "queued",  "to_state": "parsing", "event": "task_started","payload": {"task_id": "0193..."},            "created_at": "..."},
    {"from_state": "parsing", "to_state": "parsed",  "event": "parse_done",  "payload": {"parser": "docling", "pages": 12},"created_at": "..."}
    // Phase 2c: `parser` enum widens to `docling | xlsx | email | gemini_ocr | mistral_ocr`.
    // `payload.provenance` may also be set when Phase 2c's strategy/escalation runs (see raw_pages.layout_json.provenance).
    // Phase 3a/3b/3c/3d (subsequent events on a single file's lifecycle history):
    //   parsed → chunked            event=chunking_done           payload={chunk_count}
    //   chunked → contextualized    event=contextualization_done  payload={prefix_count, model_id, cache_creation_input_tokens, cache_read_input_tokens}
    //   contextualized → embedded   event=embedding_done          payload={embedded_count, model_id, dim}
    //   embedded → raptor_building  event=raptor_build_started    payload={leaf_count}
    //   raptor_building → ready     event=raptor_build_done       payload={leaf_count, levels_built, total_summarizer_calls, summarizer_model_id, embedder_model_id}
    // Failures at any stage: <prior_state> → failed, event=<stage>_failed, payload={error_class, message, ...}
  ]
}
```

Items in `created_at ASC` order (oldest first). Append-only — clients can verify integrity by checking transitions follow §5.1 #3.

### 5.4 Raw-page resource shape

`GET /files/:id/pages` returns paginated raw pages:

```json
{
  "page_number": 1,
  "text": "...full page text content...",
  "layout_json": {"blocks": [...]},
  "content_sha": "...",
  "created_at": "..."
}
```

| Field | Type | Notes |
|---|---|---|
| `page_number` | int | 1-indexed. Unique with `file_id`. |
| `text` | string | Full extracted text content. |
| `layout_json` | object | Parser-specific layout metadata. Docling outputs `{blocks: [{type, bbox, text}]}`. Empty `{}` for parsers that don't produce layout (e.g., email body). |
| `content_sha` | string | sha256 of `text` (used for per-page de-dup if needed by later phases). |

### 5.5 `POST /files` — upload + enqueue parse

**Auth:** none in 2a. **Idempotency:** `Idempotency-Key` header **required**.

Two modes — server picks based on `Content-Type`:

**Mode A — multipart/form-data:**

```http
POST /files
Content-Type: multipart/form-data; boundary=...
Idempotency-Key: <client-uuid>

(multipart with field `file` = file content; optional field `name` = display name; if `name` omitted, use the multipart filename)
```

**Mode B — JSON with pre-uploaded `object_key`:**

```http
POST /files
Content-Type: application/json
Idempotency-Key: <client-uuid>

{
  "minio_object_key": "raw_files/e3b0c442...",
  "name": "acme_contract.pdf"
}
```

Mode B is useful for Phase 10a's streaming-upload UI (which streams directly to MinIO before calling the API) and for tests using pre-staged files.

**Query parameters (Phase 2c):**

| Param | Type | Default | Notes |
|---|---|---|---|
| `parser` | enum | `auto` | Forces a parser regardless of the server's `KB_PARSER_STRATEGY`. Values: `auto` (use server strategy + text-layer sniff), `docling` (force Docling — fast, free, may produce empty/garbled text for scanned PDFs), `gemini` (force Gemini OCR — paid, slower, accurate on scanned/multilingual/handwritten/table-heavy inputs). Persisted into `raw_pages.layout_json.provenance.forced_parser`. Useful for testing known-edge-case inputs and benchmarking adapters head-to-head. Invalid values → `400 invalid-parser-override`. |

**Success — `201 Created`:** file object (§5.2) with `lifecycle_state='queued'`. `Location: /files/<id>` header. Server computes `sha256` + writes `files` row + writes initial `file_lifecycle` row (`null → queued`) + enqueues `parse_file` task — all in one tx.

**Content-hash dedup:** if a file with the same `(workspace_id, content_sha)` already exists with `lifecycle_state != 'deleted'`, return `200 OK` (NOT 201) with the existing file object. Header `X-Dedup-Reason: content-hash`.

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `400` | missing `Idempotency-Key`, malformed body, or `?parser=` value not in `{auto, docling, gemini}` | `missing-idempotency-key`, `bad-request`, `invalid-parser-override` |
| `413` | content > 100 MB | `payload-too-large` |
| `415` | mime_type not in supported list. Phase 2a accepted only `application/pdf`. Phase 2b widens to: `application/pdf` · `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (.xlsx) · `application/vnd.ms-excel` (.xls) · `message/rfc822` (.eml). Magic-byte sniff at upload picks the right parser when `Content-Type` is missing or `application/octet-stream`. | `unsupported-media-type` |
| `422` | `name` empty / > 500 chars, or `minio_object_key` doesn't resolve | `validation-error` |

**Idempotency replay:** standard Phase 0 behavior — cached body returned verbatim.

### 5.6 `GET /files` — list active files

`?limit=50&offset=0` (defaults; 1–200 / ≥0). Sort: `created_at DESC, id DESC`. Filters to `lifecycle_state != 'deleted'`.

```json
{ "items": [<file>, ...], "total": 12, "limit": 50, "offset": 0 }
```

**Errors:** `400` (`bad-request`).

### 5.7 `GET /files/:id` — read one file + lifecycle history

Returns the file object (§5.2) **plus** the `lifecycle` array (§5.3).

```json
{
  "id": "...", "name": "...", ...,
  "lifecycle_state": "parsed",
  "lifecycle": [<event>, ...]
}
```

**Errors:** `404` (not found / deleted / wrong workspace).

### 5.8 `GET /files/:id/pages` — list raw pages

`?limit=50&offset=0`. Sort: `page_number ASC`.

```json
{ "items": [<raw_page>, ...], "total": 12, "limit": 50, "offset": 0 }
```

Returns `[]` with `total=0` while `lifecycle_state` is `queued` or `parsing` (no pages yet). Returns `404` if file not found.

**Errors:** `400` (`bad-request`), `404`.

### 5.9 `DELETE /files/:id` — soft delete

**Idempotency:** `Idempotency-Key` header **optional**.

Soft-deletes by setting `lifecycle_state='deleted'`. `raw_pages` rows are NOT cascade-deleted (they remain queryable by `file_id` via the immutable contract — Phase 2a doesn't expose a "list-pages-for-deleted-file" endpoint, but a future audit phase could). MinIO blob is **never** deleted by Phase 2a (manual cleanup if needed).

**Success — `204 No Content`.**

**Errors:** `404`.

### 5.10 Out of scope for Phase 2a

- `POST /files/:id/retry` — manual retry on `failed` lifecycle. **Phase 2b.**
- `PATCH /files/:id` — rename, edit metadata. Out of scope; clients can DELETE + POST.
- Multipart streaming (chunked upload over multiple HTTP requests). The 100 MB limit is enforced at request-body level. **Wave B if needed.**
- xlsx / email / Mistral OCR parser support — **Phase 2b** (same dispatcher, same endpoints; just more `Parser` implementations).
- `doc_type` classifier (architecture step 3.5). Lands when first needed — likely Phase 5.
- Polling endpoint for parse status — clients re-`GET /files/:id` to watch `lifecycle_state`. SSE lifecycle updates are **Phase 9**.
- `audit_log` writes on file mutations — **Phase 9**.

---

## 6. Phase 3e — Corpus RAPTOR

### 6.1 Corpus tree model (the invariants every endpoint depends on)

1. **One corpus tree per workspace.** Cross-workspace trees are out of scope (multi-tenant isolation is enforced by RLS — the same `workspace_id` boundary that scopes files, chunks, contextual_chunks, chunk_embeddings, raptor_nodes, raptor_edges).
2. **Built from per-doc roots, not from raw chunks.** Per-doc raptor trees (Phase 3d) produce a root summary per file; corpus RAPTOR clusters and summarizes ACROSS those doc-roots. For singleton-leaf files (which have no per-doc raptor_nodes — Phase 3d skips tree-build when N≤1 per `build_tracker.md` §5.10 decision #9 "L1 leaves storage" + §5.10.1 decision #6 "Doc-root source"), the doc-root is the single `contextual_chunks` row directly. Cross-kind input is handled via the discriminated `raptor_edges` FK (Phase 3d decision §5.10 #10).
3. **Explicit trigger only.** Corpus rebuild is NOT auto-fired on file upload. Operators call `POST /corpus/raptor/rebuild` on a cadence that fits their cost model. At 100K-doc scale, per-upload rebuilds would melt the worker pool.
4. **Atomic rebuild semantics.** A rebuild DELETEs all `raptor_nodes` + `raptor_edges` rows with `scope='corpus'` for the workspace, then INSERTs the new tree in one transaction. Partial trees are never visible to retrieval.
5. **Deterministic.** UMAP + GMM use a fixed `random_state` so re-running the rebuild with no new docs produces an identical tree (retrieval-time citation stability).
6. **Schema is the same as per-doc.** Corpus nodes live in `raptor_nodes` with `scope='corpus'` and `file_id=NULL` (the forward-compat columns landed at Phase 3d's `0012_raptor.sql`). Corpus edges live in `raptor_edges` and may use either child column (raptor_nodes ID for multi-leaf-file doc-roots; contextual_chunks ID for singleton-leaf-file doc-roots).
7. **Retrieval graceful degradation.** If no corpus tree exists for a workspace, Phase 4 retrieval falls back to per-doc + chunk-level search. The corpus tree is additive; never blocking.

### 6.2 Corpus-node resource shape

Same physical schema as per-doc raptor_nodes (Phase 3d) — distinguished by `scope='corpus'`. Phase 4 will expose a `GET /corpus/raptor` navigation endpoint; in Phase 3e the corpus nodes are read directly via SQL or Phase 4's retrieval queries.

### 6.3 `POST /corpus/raptor/rebuild` — explicit corpus-tree rebuild

**Auth:** none in Wave A (relies on `X-Test-Workspace` header same as other endpoints). Admin RBAC deferred to Phase 9 — operators MUST gate at the network layer in production.

**Idempotency:** the endpoint itself is fire-and-forget; replaying it just queues another rebuild job. The rebuild WORKER is idempotent — re-running with the same input docs produces the same tree (decision §5.10.1 #10). Multiple concurrent rebuild requests for the same workspace serialize via Procrastinate job semantics.

```http
POST /corpus/raptor/rebuild
X-Test-Workspace: <uuid>
Content-Type: application/json

{}
```

(Request body is empty — workspace is implied from the header. Future versions may accept `{"force": bool}` for cache-bust semantics.)

**Success — `202 Accepted`:**

```json
{
  "workspace_id": "...",
  "task_id": "0193b2a0-1111-7c2a-9c11-9a3f8c1c9c11",
  "status": "queued",
  "message": "corpus RAPTOR rebuild queued"
}
```

Worker processes the job asynchronously. Clients poll via SQL on `procrastinate_jobs` (admin polling endpoint lands at Phase 9).

**Errors:**

| Status | When | `type` slug |
|---|---|---|
| `400` | workspace has zero files OR zero docs at lifecycle_state='ready' (nothing to cluster) | `corpus-rebuild-no-input` |
| `503` | a rebuild job for this workspace is already `todo` or `doing` in procrastinate_jobs | `corpus-rebuild-in-flight` |

**Cost note:** at 100K docs with branching=8, a rebuild produces ~115K corpus nodes (≈ N + N/8 + N/64 + ... summary nodes) → ≈ 115K LLM summarization calls + ≈ 115K embedding calls. Operators must own the cost.

### 6.4 Out of scope for Phase 3e

- **`GET /corpus/raptor`** read endpoint — Phase 4 retrieval reads `raptor_nodes`/`raptor_edges` directly via SQL. A REST navigation surface for end-user UIs lands with Phase 8+.
- **Status / progress polling** on the rebuild job — Procrastinate's `procrastinate_jobs` table is queryable via SQL; admin endpoint at Phase 9.
- **Incremental updates** when new files arrive after a rebuild — corpus tree is stale until next manual rebuild. CDC-based incremental rebuilds at Phase 5+.
- **Admin authorization** — Wave A ships open per user direction. Phase 9 adds RBAC.
- **HNSW + BM25 indexes** on the new corpus rows — Phase 4.
- **Cross-workspace corpus trees** — corpus trees are per-workspace, not cross-tenant.

---

## 7. Future phases — placeholders

Each phase appends its endpoint contracts here at its G2 gate. Index:

| Phase | Endpoint group | Status |
|---|---|---|
| 0 | `/health`, `/ready` | ✅ signed off 2026-05-23 |
| 1a | `/schemas` CRUD (POST/GET-list/GET/PUT/DELETE) | ✅ signed off 2026-05-23 (§2) |
| 1b | `/schemas/:id/versions*` (versioning + rollback) | ✅ signed off 2026-05-23 (§3) |
| 1c | `/schemas/:id/{entities,fields,relationships}` (hierarchy — 11 endpoints) | ✅ signed off 2026-05-23 (§4) |
| 2a | `/files` admin upload + read (5 endpoints) + worker pipeline | ✅ signed off 2026-05-23 (§5) |
| 2c | `POST /files?parser=<auto\|docling\|gemini>` caller-override query param + new `400 invalid-parser-override` error type (§5.5 Query parameters subsection) | ✅ signed off 2026-05-24 (§5.5 + §5.3 footnote) |
| 2b | Additional parsers (xlsx + email + Mistral OCR) — no new HTTP endpoints | ✅ signed off 2026-05-23 (§5.5 415 row widened) |
| 3a | Chunking — no new HTTP endpoints; `lifecycle_state` enum widens to add `chunked` (§5.1 #3 + §5.2 row) | ✅ signed off 2026-05-23 (§5.1 #3 + §5.2) |
| 3b | Contextual Retrieval — no new HTTP endpoints; `lifecycle_state` enum widens to add `contextualized` (§5.1 #3 + §5.2 row) | ✅ signed off 2026-05-23 (§5.1 #3 + §5.2) |
| 3c | Embedding — no new HTTP endpoints; `lifecycle_state` enum widens to add `embedded` (§5.1 #3 + §5.2 row) | ✅ signed off 2026-05-23 (§5.1 #3 + §5.2) |
| 3d | Per-doc RAPTOR — no new HTTP endpoints; `lifecycle_state` enum widens with `raptor_building` + `ready` as terminal (§5.2 row) + §5.3 lifecycle example annotated | ✅ signed off 2026-05-24 (§5.2 + §5.3) |
| 3b-bis | Gemini Contextualizer adapter — no contract delta; 4-value `KB_CONTEXTUALIZER` selector added to env vocabulary | ✅ signed off 2026-05-24 (no API surface) |
| 3e | Corpus RAPTOR — `POST /corpus/raptor/rebuild` explicit-trigger endpoint (new top-level §6) + new error types `corpus-rebuild-no-input` (400) and `corpus-rebuild-in-flight` (503) | ✅ signed off 2026-05-24 (§6) |
| 4–7 | Retrieval (HNSW + BM25 + tree-aware query) + extraction + ranking endpoints (TBD at each phase's G1) | ⬜ |
| 8 | `/query`, `/chat`, `/chat/:id/stream` | ⬜ |
| 9 | `/upload/:id/status` (SSE), `/audit`, admin RBAC for `/corpus/raptor/rebuild` | ⬜ |
| 10a–g | UI-driven endpoints follow from `prototype/wiring_inventory.md` | ⬜ |

---

## 8. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-22 | File created at Phase 0 G2. §0 conventions + §1 Phase 0 contracts (`/health`, `/ready`) drafted. Awaiting sign-off. | Aniket |
| 2026-05-23 | **Re-validated against re-opened Phase 0 G1.** No contract changes required: `/ready`'s `migrations` check still reads `schema_migrations`; `Idempotency-Key` header is still backed by the `idempotency_keys` table (now workspace-scoped via primary key `(workspace_id, key)` — server-side detail, invisible to clients). `X-Request-Id` header promise in §0.8 is now backed by middleware in G1 plan. | Aniket |
| 2026-05-23 | **Phase 1a G2 — schemas CRUD contracts drafted.** §2 added with 5 endpoints (POST/GET-list/GET/PUT/DELETE) under `/schemas`. Schema response shape (no `workspace_id` field — clients know their own). Body validation rules. RFC 9457 error slugs per endpoint (`schema-name-conflict`, `not-found`, `validation-error`, `bad-request`, `missing-idempotency-key`). Idempotency: required on POST, optional on PUT/DELETE. §3 placeholder index renumbered + split: Phase 1 row → 1a/1b/1c. §0 placeholder section renumbered to §3; this changelog renumbered to §4. | Aniket |
| 2026-05-23 | **§0.2 UUID convention broadened (post-G3 consistency sweep).** Old text said "All entity IDs are UUIDv7"; reality is Phase 0 ships `audit_log.id` as v4 and Phase 1a chose v4 for `schemas.id`. Honest replacement: v4 by default for PKs where time-sortability isn't a query pattern; v7 required where monotonic-by-creation ordering is queried (X-Request-Id, future `query_id`). Each phase's G1 picks the flavor per table. §2.1 `id` field annotated to cite this. | Aniket |
| 2026-05-23 | **Phase 1b G2 — schemas versioning contracts drafted.** §3 added with 9 sub-sections: versioning model invariants (§3.1), mutated schema object adds `current_version` (§3.2), POST + PUT behavioural deltas (§3.3, §3.4), version resource shape (§3.5), declarative diff format (§3.6), GET list (§3.7), GET one with computed diff (§3.8), POST rollback with `409 rollback-noop` for same-as-current (§3.9), out-of-scope list (§3.10). Old §3 placeholder index → §4; old §4 changelog → §5. | Aniket |
| 2026-05-23 | **Phase 1c G2 — schemas hierarchy contracts drafted.** §4 added with 18 sub-sections: hierarchy invariants (§4.1 — workspace-isolated, parent-scoped soft delete, coarse-grained versioning, atomic mutations, name-resolved cross-refs in snapshots, replay never duplicates), extended `schema_versions.body` shape with entities/fields/relationships (§4.2), diff format extension with nested dotted paths (§4.3), entity resource shape + 4 endpoints (§4.4–§4.8 — POST/GET-list/PUT/DELETE; DELETE cascades to fields + relationships), field resource shape + 4 endpoints (§4.9–§4.13; type enum string/number/boolean/date/datetime), relationship resource shape + 3 endpoints (§4.14–§4.17; no PUT — soft-delete + re-create path; kind enum verbatim from architecture line 794; cardinality/cascade_delete/single_parent recorded only), out-of-scope (§4.18). 3 new error slugs introduced: `entity-name-conflict`, `field-name-conflict`, `relationship-name-conflict` (join 1a/1b's 5). Old §4 placeholder index → §5; old §5 changelog → §6. | Aniket |
| 2026-05-23 | **Phase 2a G2 — files + parse pipeline contracts drafted.** §5 added with 10 sub-sections: pipeline-model invariants (§5.1 — MinIO/PG split, content-hash dedup, lifecycle state machine, raw_pages immutable, per-stage idempotency, workspace-isolated), file resource shape (§5.2), lifecycle history array shape (§5.3), raw-page resource shape (§5.4), POST upload with two modes — multipart OR JSON (§5.5), GET list (§5.6), GET one with lifecycle (§5.7), GET pages (§5.8), DELETE soft (§5.9), out-of-scope §5.10. 2 new error slugs: `payload-too-large` (413, file > 100 MB), `unsupported-media-type` (415, mime not in 2a's whitelist). Idempotency-Key: required POST, optional DELETE (same rule). Content-hash dedup returns `200 OK X-Dedup-Reason: content-hash` (not 409). Old §5 placeholders → §6, old §6 changelog → §7. | Aniket |
| 2026-05-23 | **Phase 2b G2 — mime whitelist widened (single contract delta).** §5.5 `POST /files` 415 row's narrative grows to list the four supported mime types: `application/pdf` + `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (.xlsx) + `application/vnd.ms-excel` (.xls) + `message/rfc822` (.eml). Added: "magic-byte sniff at upload picks the right parser when Content-Type is missing or application/octet-stream." No new endpoints; no new error slugs; no other §5 sub-sections changed. | Aniket |
| 2026-05-23 | **Phase 3a G2 — `lifecycle_state` enum widens by `chunked` (single contract delta).** §5.1 #3 invariant rewritten to make the state machine extension explicit: `queued → parsing → parsed → chunked | failed`; soft-delete via `→ deleted` from any non-failed state. §5.2 file-resource shape's `lifecycle_state` enum row widens accordingly. Phase 3b will append `contextualized`; 3c will append the terminal `ready` — pattern is "each sub-phase appends exactly one new state" so existing wire readers stay forward-compatible. No new endpoints, no new error slugs, no other §5 sub-sections changed. | Aniket |
| 2026-05-23 | **Phase 3b G2 — `lifecycle_state` enum widens by `contextualized` (single contract delta).** §5.1 invariant #3 extended to `queued → parsing → parsed → chunked → contextualized | failed`. §5.2 file-resource shape's `lifecycle_state` enum row widens to match. Phase 3c will append the terminal `ready`. Forward-compat convention continues — each sub-phase appends exactly one new state. No new endpoints, no new error slugs, no other §5 sub-sections changed. | Aniket |
| 2026-05-23 | **Phase 3c G2 — `lifecycle_state` enum widens by `embedded` (single contract delta).** §5.1 invariant #3 extended to `queued → parsing → parsed → chunked → contextualized → embedded | failed`. §5.2 file-shape enum widens to match. Phase 3d will append the terminal `ready`. Forward-compat convention preserved — each sub-phase appends exactly one state. No new endpoints, no new error slugs. | Aniket |
| 2026-05-24 | **Phase 2c G2 — `?parser=` caller override + 400 invalid-parser-override.** §5.5 `POST /files` adds Query parameters subsection documenting `?parser=auto\|docling\|gemini` (default `auto`; persisted into `raw_pages.layout_json.provenance.forced_parser`). 400 error type widened with `invalid-parser-override`. §5.3 lifecycle history example footnoted with the parser enum widening (`docling | xlsx | email | gemini_ocr | mistral_ocr`). | Aniket |
| 2026-05-24 | **Phase 3d G2 — `lifecycle_state` enum widens by `raptor_building` + reframes `ready`.** §5.2 enum row widens to include `raptor_building` (3d's intermediate state between embedded → ready) and reframes `ready` as 3d's terminal (was "Phase 3d will add"). §5.3 lifecycle history example annotated with all post-Phase-2c stage transitions (chunking_done, contextualization_done, embedding_done, raptor_build_started, raptor_build_done) + payload shapes per stage + failure-event convention noted explicitly. | Aniket |
| 2026-05-24 | **Phase 3e G2 — Corpus RAPTOR new §6 added.** New top-level `## 6. Phase 3e — Corpus RAPTOR` introduces the corpus-tree model (§6.1 — 7 invariants covering workspace isolation, doc-root sourcing from per-doc roots, explicit-trigger semantics, atomic rebuild, determinism, schema reuse, retrieval graceful degradation), notes corpus-node resource shape is shared with per-doc (§6.2), and documents `POST /corpus/raptor/rebuild` (§6.3 — 202 Accepted with task_id; errors `400 corpus-rebuild-no-input` and `503 corpus-rebuild-in-flight`). Cost note at the endpoint description warns operators of ~115K LLM+embedding calls at 100K-doc scale. Out-of-scope §6.4 documents the deferrals (GET /corpus/raptor → Phase 8+; status polling → Phase 9; incremental updates → Phase 5+; admin RBAC → Phase 9; HNSW → Phase 4). Old §6 placeholders → §7; old §7 changelog → §8. | Aniket |
