# Phase 0 — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-23 · awaiting sign-off.
> **Inputs:** Phase 0 G1 plan ([build_tracker §5.1](../../docs/build_tracker.md)) · Phase 0 G2 contracts ([api_contracts.md §1](../../docs/api_contracts.md)).
> **Outputs at G3:** this spec + five red skeleton test files in `tests/` + `tests/conftest.py`. Skeletons fail at collection time (modules don't exist yet) or at runtime (`NotImplementedError`) — that is the **expected** state at G3. G4 lands the code that turns them green.

---

## 1. Scope

Every contract from G2 gets test coverage. Every architectural invariant Phase 0 commits to (RLS day-1, X-Request-Id middleware, workspace context) gets test coverage. Five test files cover the surface:

| File | Covers | Contracts / decisions tested |
|---|---|---|
| [`tests/test_health.py`](../test_health.py) | `GET /health` contract | api_contracts §1.1 |
| [`tests/test_ready.py`](../test_ready.py) | `GET /ready` contract (all variants) | api_contracts §1.2 |
| [`tests/test_migrations.py`](../test_migrations.py) | Migration runner behaviour | build_tracker §5.1 "Migration runner behaviour" |
| [`tests/test_rls.py`](../test_rls.py) | RLS isolation on workspace-scoped tables | build_tracker §5.1 decision #6 |
| [`tests/test_middleware.py`](../test_middleware.py) | Workspace-context + X-Request-Id middleware | build_tracker §5.1 decision #6 + api_contracts §0.8 |

Plus [`tests/conftest.py`](../conftest.py) — shared fixtures.

**Out of scope (later phases own this):**
- Schema CRUD (Phase 1 G3).
- Parse / chunk / index / extract / identity / query tests (phases 2–8 G3 each).
- UI tests (Phase 10 G3 each).
- Audit-log hash chain + integrity job tests (Phase 9 G3).
- File lifecycle tests (Phase 2 G3).

---

## 2. Fixture strategy — decision

**Pick: `testcontainers-python`.** Each test session spins up its own Postgres (ParadeDB image) + MinIO containers, runs the migration suite against the fresh DB, tears down at session end. Tests get hermetic isolation; CI runs without needing a pre-existing docker-compose stack; local dev runs without polluting the long-lived dev DB.

**Alternative considered:** point tests at the running `docker-compose` stack via env vars. Cheaper to start (no per-session container overhead) but creates session-vs-session interference and requires `docker compose up` before pytest can run. Rejected.

**Implication for G4:** add `testcontainers[postgres,minio] >= 4.7` and `pytest-asyncio >= 0.24` to `pyproject.toml` dev-dependencies. (These are dev-only; not in the production image.)

**Per-test isolation within a session:** transaction-rollback per test for read-only checks; truncate-and-reseed for tests that exercise the migration runner or schema_migrations row writes. The `db_session` fixture defaults to transaction-rollback; specific tests opt into the `db_truncate` fixture when they need a real commit.

**Async loop:** `pytest-asyncio` in `auto` mode. Every test marked `async def` is awaited.

---

## 3. Conventions

- **HTTP client:** `httpx.AsyncClient` over `ASGITransport(app=fastapi_app)`. No live network calls.
- **Database role:** tests connect as the non-superuser `kb_app` role. Migrations are applied as superuser in the session fixture; tests then drop to `kb_app` so RLS actually applies. Tests that need superuser explicitly request the `db_superuser` fixture.
- **Workspace context:** every test that touches a workspace-scoped table calls `set_workspace(<uuid>)` first (helper in conftest that issues `SET LOCAL app.workspace_id`). RLS tests explicitly toggle this to verify isolation.
- **Naming:** `test_<unit>_<expectation>` — e.g. `test_health_returns_200_with_documented_shape`, `test_ready_returns_503_when_db_down`. Behaviour, not implementation.
- **Failure messages:** every assertion has a message that names the contract or decision it's checking (e.g. `assert resp.status_code == 503, "api_contracts §1.2: /ready must return 503 when any dep is down"`).
- **No mocks at boundaries we own.** Real Postgres, real MinIO. We mock only the third-party HTTP surface we can't reach in CI (none in Phase 0).
- **Time:** tests freeze time via `freezegun` when assertions involve timestamps. Locked time is `2026-05-23T12:00:00Z`.

---

## 4. Test inventory

### 4.1 `test_health.py` — `GET /health`

Maps to [api_contracts §1.1](../../docs/api_contracts.md).

| Test | Intent |
|---|---|
| `test_health_returns_200_with_documented_shape` | GET `/health` → 200; body is exactly the documented keys `{status, service, version, ts}` with the documented types. |
| `test_health_status_field_is_ok` | `status == "ok"` literally. |
| `test_health_service_field_is_kb_api` | `service == "kb-api"`. |
| `test_health_version_matches_pyproject` | `version` equals the value read from `pyproject.toml`. |
| `test_health_ts_is_iso8601_utc_recent` | `ts` parses as ISO-8601 UTC, within 5 seconds of `now()`. |
| `test_health_does_not_depend_on_db` | With the database container paused, `/health` still returns 200. (Sanity check: liveness ≠ readiness.) |
| `test_health_does_not_write_access_log` | Hit `/health` 10 times; structlog capture sink shows zero access-log entries. |
| `test_health_responds_under_100ms_p99` | Hit `/health` 100 times; p99 latency < 100ms. (Loose budget; tightens later.) |

### 4.2 `test_ready.py` — `GET /ready`

Maps to [api_contracts §1.2](../../docs/api_contracts.md).

| Test | Intent |
|---|---|
| `test_ready_returns_200_when_all_deps_ok` | Fresh stack: all checks pass; status code 200; body `status=="ready"`; all check entries have `status=="ok"` and `latency_ms` is an int. |
| `test_ready_check_set_matches_phase_0_contract` | Body `checks` keys are exactly `{db, minio, migrations}` — no surprises, no missing. |
| `test_ready_returns_503_when_db_down` | Pause the postgres container; `/ready` → 503; `checks.db.status == "fail"`; `checks.db.error` is human-readable; other checks still report status. |
| `test_ready_returns_503_when_minio_down` | Pause MinIO container; `/ready` → 503; `checks.minio.status == "fail"`. |
| `test_ready_returns_503_when_migration_pending` | Add a fake `migrations/sql/9999_pending.sql` on disk that isn't recorded in `schema_migrations`; `/ready` → 503; `checks.migrations.error` mentions the pending filename. |
| `test_ready_response_uses_application_problem_json_on_failure` | Failure responses set `Content-Type: application/json` (not `application/problem+json` — `/ready` is intentionally a typed health response, not a generic error). |
| `test_ready_checks_run_in_parallel` | Slow down each check by 1s (via fixture-injected delay); total response time is ≈ 1s, not 3s — proves `asyncio.gather`. |
| `test_ready_overall_budget_is_5s` | Slow down one check beyond 5s; that check reports `timeout` error; `/ready` returns within 5.5s. |
| `test_ready_does_not_write_access_log` | Same as `/health`. |
| `test_ready_no_auth_required` | No Authorization header; still works. |

### 4.3 `test_migrations.py` — migration runner

Maps to [build_tracker §5.1 "Migration runner behaviour"](../../docs/build_tracker.md).

| Test | Intent |
|---|---|
| `test_runner_bootstraps_schema_migrations_on_empty_db` | Run against a DB with no `schema_migrations` table; runner creates it and proceeds. |
| `test_runner_applies_all_files_in_lexical_order` | After fresh apply, `schema_migrations` contains exactly the four files in `migrations/sql/`, ordered by filename. |
| `test_runner_is_idempotent_when_rerun` | Apply, then apply again; second run inserts zero new rows, completes successfully. |
| `test_runner_rolls_back_on_failed_migration` | Inject a SQL syntax error in a temp migration; runner aborts that file, does NOT record it in `schema_migrations`, exits non-zero. Previously-applied files remain applied. |
| `test_runner_applies_extensions_first` | After fresh apply: `SELECT * FROM pg_extension WHERE extname IN ('vector', 'pg_search')` returns both rows. |
| `test_runner_creates_kb_app_role` | After fresh apply, `SELECT 1 FROM pg_roles WHERE rolname='kb_app'` returns a row. |
| `test_runner_creates_initial_audit_log_partitions` | `audit_log_2026_05` and `audit_log_2026_06` exist as partitions of `audit_log`. |
| `test_runner_records_filename_and_applied_at` | Each `schema_migrations` row has `id` = filename and `applied_at` ≈ now(). |
| `test_runner_runs_as_superuser` | Migrations succeed despite RLS being enabled on `audit_log` and `idempotency_keys`. |

### 4.4 `test_rls.py` — RLS isolation

Maps to [build_tracker §5.1 decision #6](../../docs/build_tracker.md).

| Test | Intent |
|---|---|
| `test_audit_log_has_rls_enabled` | `SELECT relrowsecurity FROM pg_class WHERE relname='audit_log'` → `True`. |
| `test_idempotency_keys_has_rls_enabled` | Same for `idempotency_keys`. |
| `test_schema_migrations_has_no_rls` | Same query returns `False` — infrastructure, no workspace scope. |
| `test_audit_log_isolated_across_workspaces` | Insert one audit_log row under workspace_id `A`, one under workspace_id `B` (each in its own SET LOCAL session). Querying as workspace `A` returns only A's row; as `B` returns only B's row. |
| `test_idempotency_keys_isolated_across_workspaces` | Same scenario with idempotency_keys. |
| `test_no_workspace_context_means_no_rows` | Connect as kb_app without setting `app.workspace_id`; SELECT on audit_log fails (or returns zero rows — define and assert). |
| `test_superuser_bypasses_rls` | Same SELECT as superuser returns both rows. (Sanity check: migration role can see everything.) |
| `test_dropping_workspace_filter_does_not_leak` | Issue a query against audit_log without an explicit `WHERE workspace_id = ...`. Returns only the current session's workspace rows. |

### 4.5 `test_middleware.py` — workspace context + X-Request-Id

Maps to [api_contracts §0.8](../../docs/api_contracts.md) + [build_tracker §5.1 decision #6](../../docs/build_tracker.md).

| Test | Intent |
|---|---|
| `test_response_has_x_request_id_header` | Every response (including 404, 500) has `X-Request-Id` set. |
| `test_x_request_id_is_uuidv7` | Header value parses as UUIDv7. |
| `test_x_request_id_is_unique_per_request` | Two consecutive requests have different `X-Request-Id` values. |
| `test_x_request_id_propagates_from_client_when_provided` | Client sends `X-Request-Id: <uuid>` header → response echoes the same value. (Distributed tracing pattern.) |
| `test_workspace_context_set_per_request` | A request triggers an endpoint that reads `current_setting('app.workspace_id')`; value matches the resolved workspace for that request. |
| `test_workspace_context_isolated_between_concurrent_requests` | Launch two concurrent requests with different workspace IDs; each endpoint sees its own ID, never the other's. (Tests `SET LOCAL` per-transaction scoping, not session.) |
| `test_workspace_defaults_to_default_when_unauthenticated` | Phase 0 has no auth; middleware sets `app.workspace_id = 'default'`. |
| `test_structlog_binds_request_id_and_workspace_id` | A log emitted inside an endpoint includes `request_id` and `workspace_id` in its structured fields. |

---

## 5. What "green" means at G4

When all of the following pass, G3 is satisfied and G4 closes:

1. `uv run pytest tests/` exits 0.
2. No test is `pytest.skip()`-ed (skips hide unfinished work).
3. Each test in this spec has a corresponding `def test_...` in the matching file.
4. Coverage of `src/kb/api/`, `src/kb/db/`, `migrations/runner.py` is ≥ 90% (line + branch).
5. The same test suite passes both locally (testcontainers-spun stack) and in CI.

`scripts/verify_phase_0.sh` (lands at G5) wraps this and adds the end-to-end docker-compose smoke.

---

## 6. Sign-off

When Aniket signs off this spec + the skeleton files in `tests/`, the Phase 0 G3 cell in [build_tracker §5](../../docs/build_tracker.md) flips 🟡 → ✅ and G4 (build) opens. Sign-off recorded in `build_tracker.md` §9.

---

## 7. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-23 | Spec drafted at Phase 0 G3 open. Five test buckets defined: health, ready, migrations, RLS, middleware. Testcontainers picked as fixture strategy. Awaiting sign-off. | Aniket |
