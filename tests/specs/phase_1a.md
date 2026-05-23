# Phase 1a — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-23 · awaiting sign-off.
> **Inputs:** Phase 1a G1 plan ([build_tracker §5.2](../../docs/build_tracker.md)) · Phase 1a G2 contracts ([api_contracts.md §2](../../docs/api_contracts.md)).
> **Outputs at G3:** this spec + three red skeleton test files in `tests/`. They import from `kb.api.schemas` and `kb.domain.schemas` (which land at G4); collection fails at G3 — that's the expected "red" state.

---

## 1. Scope

Every endpoint in api_contracts §2 gets coverage. Every G1 decision (RLS day-1, soft delete via lifecycle_state, partial-unique-on-active name, idempotency POST-required PUT/DELETE-optional, no workspace_id in response) gets test coverage. Three files cover the surface (**~25 new tests**, on top of the 49 from Phase 0):

| File | Tests | Covers |
|---|---|---|
| [`tests/test_schemas_crud.py`](../test_schemas_crud.py) | ~17 | Happy paths + business errors for POST · GET-list · GET · PUT · DELETE |
| [`tests/test_schemas_rls.py`](../test_schemas_rls.py) | ~4 | Workspace isolation via the `X-Test-Workspace` header through the middleware + RLS policy |
| [`tests/test_idempotency.py`](../test_idempotency.py) | ~4 | Cross-cutting `Idempotency-Key` behavior backed by Phase 0's `idempotency_keys` table |

**Out of scope (Phase 1b / 1c / 9):**
- Version-creation side effect of PUT (1b).
- `/schemas/:id/versions*` endpoints (1b).
- `/schemas/:id/{entities,fields,relationships}` (1c).
- NL field descriptions on response (1c).
- Audit-log writes on mutation (Phase 9).

---

## 2. Fixture strategy

Reuses Phase 0's testcontainers + per-test connection setup unchanged. Two new conventions for Phase 1a:

- **Per-test workspace UUID.** A `test_workspace` fixture mints a fresh UUID per test and yields it as a string. Every HTTP call passes it via `X-Test-Workspace`. State is naturally isolated between tests without needing transaction rollback at the HTTP boundary.
- **Schema factory.** A `create_schema(client, ws, name=...)` async helper posts a schema and returns the parsed body. Saves repetition in tests that need a pre-existing schema.

Migrations now include `0005_schemas.sql` (lands at G4). The session-scoped `db_migrated` fixture from Phase 0 picks it up automatically.

---

## 3. Conventions

Same as Phase 0 (pytest + pytest-asyncio + httpx + structlog; testcontainers session; UUIDv4 for `Idempotency-Key` test values via stdlib `uuid.uuid4()`). Plus:

- **Idempotency-Key generation in tests:** `str(uuid.uuid4())` — clients aren't required to send UUIDv7 (just a unique string).
- **Workspace-isolation tests must run as `kb_app`** (the default in the test client) so RLS actually applies. Superuser would bypass.

---

## 4. Test inventory

### 4.1 `test_schemas_crud.py` — happy paths + business errors

Maps to [api_contracts §2.2–§2.6](../../docs/api_contracts.md).

| Test | Intent |
|---|---|
| `test_post_creates_schema_with_documented_shape` | POST returns 201 with `{id, name, description, lifecycle_state="active", created_at, updated_at}`. No `workspace_id` in response (§2.1 design call). |
| `test_post_id_is_uuid` | Returned `id` parses as UUID. |
| `test_post_without_idempotency_key_returns_400` | Header missing → 400 with `type` slug `missing-idempotency-key`. |
| `test_post_validation_rejects_empty_name` | `""` name → 422 with slug `validation-error`. |
| `test_post_validation_rejects_too_long_name` | 201-char name → 422. |
| `test_post_validation_accepts_max_length_name` | 200-char name → 201 (boundary). |
| `test_post_duplicate_name_returns_409` | Second POST with same name in same workspace → 409 slug `schema-name-conflict`. |
| `test_post_after_delete_allows_name_reuse` | POST → DELETE → POST with same name succeeds (partial unique index excludes deleted rows). |
| `test_get_list_returns_workspace_schemas_paginated` | Create 3; GET → `{items: [3 schemas], total: 3, limit: 50, offset: 0}`. |
| `test_get_list_pagination_offset_and_limit` | Create 5; GET `?limit=2&offset=2` returns items[2:4]. |
| `test_get_list_rejects_limit_over_200` | `?limit=201` → 400 slug `bad-request`. |
| `test_get_list_sorted_by_created_at_desc` | 3 schemas posted sequentially; list order matches reverse-chronological. |
| `test_get_one_returns_schema` | GET /schemas/:id returns the object. |
| `test_get_one_nonexistent_returns_404` | Random UUID → 404 slug `not-found`. |
| `test_get_one_after_delete_returns_404` | POST → DELETE → GET → 404. |
| `test_put_updates_name_and_description` | PUT changes both fields; response shows new values; `updated_at` bumped. |
| `test_put_nonexistent_returns_404` | PUT on random UUID → 404. |
| `test_put_name_collision_returns_409` | Two schemas A, B; PUT B with A's name → 409. |
| `test_delete_soft_deletes_schema` | DELETE → 204. Followed by GET → 404. Verified row exists in DB with `lifecycle_state='deleted'` via superuser SELECT. |
| `test_delete_already_deleted_returns_404` | DELETE → 204; DELETE again → 404 (RFC 7231 state-parity, not code-parity). |

### 4.2 `test_schemas_rls.py` — workspace isolation

Maps to [api_contracts §2.7 + build_tracker §5.2 decision #6](../../docs/build_tracker.md).

| Test | Intent |
|---|---|
| `test_list_isolated_across_workspaces` | POST as workspace A; GET as workspace B → `items=[]`, `total=0`. |
| `test_get_one_returns_404_for_wrong_workspace` | Create in A, GET /schemas/:id as B → 404 (NOT 403, per §2.4 — leaking existence would leak info). |
| `test_put_returns_404_for_wrong_workspace` | Similarly for PUT. |
| `test_delete_returns_404_for_wrong_workspace` | Similarly for DELETE. |
| `test_duplicate_name_across_workspaces_is_allowed` | POST "X" as workspace A → 201; POST "X" as workspace B → 201 (workspaces are independent namespaces). |

### 4.3 `test_idempotency.py` — Idempotency-Key behavior

Maps to [api_contracts §0.5 + §2.2](../../docs/api_contracts.md).

| Test | Intent |
|---|---|
| `test_post_with_same_idempotency_key_replays_cached_response` | POST → 201; POST again with same key + same body → 201 with identical body (no second row). Verify single row in DB. |
| `test_post_idempotency_key_isolated_per_workspace` | Same key in workspace A and B both succeed (primary key is `(workspace_id, key)`). |
| `test_put_with_idempotency_key_replays` | PUT → 200; PUT again with same key → 200 with identical body. |
| `test_delete_with_idempotency_key_replays` | DELETE → 204; DELETE again with same key → 204 (cached), NOT 404. Distinguishes "replay" semantics from "second call sees system in deleted state." |

---

## 5. What "green" means at G4

When all of the following pass, G3 is satisfied and G4 closes:
1. `uv run pytest tests/` exits 0.
2. Phase 0 suite remains green (49 tests) — no regressions.
3. ~25 new Phase 1a tests are green.
4. Coverage of `src/kb/api/schemas.py`, `src/kb/api/idempotency.py`, `src/kb/domain/schemas.py` is ≥ 90%.

---

## 6. Sign-off

When Aniket approves this spec + the skeleton files, the Phase 1a G3 cell in [build_tracker §5](../../docs/build_tracker.md) flips 🟡 → ✅ and G4 (build) opens. Sign-off recorded in `build_tracker.md` §9.

---

## 7. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-23 | Spec drafted at Phase 1a G3 open. Three buckets defined: CRUD (~17) · RLS (~4) · idempotency (~4) = ~25 new tests. Awaiting sign-off. | Aniket |
