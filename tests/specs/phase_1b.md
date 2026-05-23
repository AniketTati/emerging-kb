# Phase 1b — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-23 · awaiting sign-off.
> **Inputs:** Phase 1b G1 plan ([build_tracker §5.3](../../docs/build_tracker.md)) · Phase 1b G2 contracts ([api_contracts.md §3](../../docs/api_contracts.md)).
> **Outputs at G3:** this spec + one new red skeleton file (`tests/test_schema_versions.py`) + two RED *additions* to existing Phase 1a files (`tests/test_schemas_crud.py`, `tests/test_idempotency.py`). New imports point at `kb.api.schema_versions` and the extended `kb.domain.schemas` that land at G4 — collection fails at G3 (expected "red" state).

---

## 1. Scope

Every endpoint in api_contracts §3 gets coverage. Every G1 decision in [build_tracker §5.3](../../docs/build_tracker.md) (full snapshot, monotonic int per schema, POST creates v1 atomically, rollback = clone-forward, declarative diff format, Idempotency-Key required on rollback, per-schema FOR UPDATE serialization, 409 rollback-noop) has at least one test asserting it.

Three files cover the surface (**28 new tests**, on top of the 78 from Phase 0+1a; pytest `--collect-only` is the authoritative count):

| File | New tests | Covers |
|---|---|---|
| [`tests/test_schema_versions.py`](../test_schema_versions.py) | 25 | GET list (§3.7 — 8) · GET one + diff (§3.8 — 9) · rollback (§3.9 — 7) · concurrent-PUT serialization (§3.4 — 1) · workspace isolation across all three new endpoints |
| [`tests/test_schemas_crud.py`](../test_schemas_crud.py) — additive | 2 | POST returns `current_version=1` (§3.3) · PUT bumps `current_version` (§3.4) |
| [`tests/test_idempotency.py`](../test_idempotency.py) — additive | 1 | Rollback `Idempotency-Key` replay returns cached body without writing a new version row (§3.9) |

**Out of scope (Phase 1c / 6 / 9 / 10d):**
- Entity / field / relationship snapshots inside `body` (1c).
- Re-extraction worker dispatch on rollback (Phase 6).
- `audit_log` writes on mutation (Phase 9).
- `If-Match` optimistic locking (Phase 10d).
- `created_by` on versions (auth phase).

---

## 2. Fixture strategy

Reuses Phase 0's testcontainers + Phase 1a's per-test workspace fixture pattern unchanged. Two additions for Phase 1b:

- **Version factory helpers.** A `put_schema(client, ws, schema_id, name=, description=, idempotency_key=)` async helper and a `rollback(client, ws, schema_id, version, *, idempotency_key=)` helper save repetition. Both go into `tests/test_schema_versions.py`.
- **No new fixture for concurrent PUTs.** The single concurrency test uses `asyncio.gather` over the existing `client` fixture; httpx + ASGITransport handles in-process concurrency fine.

Migrations now include `0006_schema_versions.sql` (lands at G4). The session-scoped `db_migrated` fixture picks it up automatically.

---

## 3. Conventions

Same as Phase 0 + 1a (pytest + pytest-asyncio + httpx + structlog; testcontainers session; UUIDv4 for `Idempotency-Key` test values via `uuid.uuid4()`). Plus:

- **Idempotency-Key reuse for rollback.** Same `str(uuid.uuid4())` pattern as 1a's POST tests.
- **Workspace-isolation tests on the new endpoints** must run as `kb_app` (the default) so RLS applies. Mirror the §2.4 contract — leak via 404, not 403.
- **Diff assertions** compare against a fully-spelled-out expected dict (no partial matchers). The diff format is locked at G2; tests should fail loudly if the shape drifts.

---

## 4. Test inventory

### 4.1 `tests/test_schema_versions.py` — NEW file

Maps to [api_contracts §3.7 + §3.8 + §3.9](../../docs/api_contracts.md) and the §3.1 invariants.

#### Version list — `GET /schemas/:id/versions` (§3.7)

| Test | Intent |
|---|---|
| `test_list_returns_only_v1_after_post` | POST → GET versions → `total=1`, `items=[{version:1, kind:"post", parent_version:null, …}]`. Invariant: POST creates v1 atomically (§3.1 #3, decision #3). |
| `test_list_returns_newest_first` | POST + 2× PUT → list items are `[v3, v2, v1]` (DESC by version_number). |
| `test_list_items_have_summary_shape` | Item dicts contain `version`, `kind`, `parent_version`, `created_at` — and explicitly NOT `body` or `diff_from_prior` (§3.7 lightweight summary). |
| `test_list_pagination_offset_and_limit` | POST + 4× PUT (5 versions); `?limit=2&offset=1` → `total=5`, `items=[v4, v3]`. |
| `test_list_rejects_limit_over_200` | `?limit=201` → 400 slug `bad-request`. |
| `test_list_404_for_unknown_schema` | GET versions on a random UUID → 404 slug `not-found`. |
| `test_list_404_for_soft_deleted_schema` | POST + DELETE + GET versions → 404 (parent gone hides the log). |
| `test_list_isolated_across_workspaces` | POST in workspace A; GET versions as workspace B → 404 (NOT 403 — same existence-leak avoidance as §2.4). |

#### Version read — `GET /schemas/:id/versions/:v` (§3.8)

| Test | Intent |
|---|---|
| `test_read_v1_has_null_diff_from_prior` | GET v1 → `diff_from_prior` is `null` (§3.5 invariant: only v1 has null). |
| `test_read_v1_body_matches_post_body` | GET v1 → `body == {"name":"X","description":""}` exactly (snapshot is faithful). |
| `test_read_v2_diff_reflects_changed_description` | POST + PUT(description=...) → GET v2 → `diff_from_prior == {added:[], removed:[], changed:[{path:"description", old:"", new:"..."}]}`. Asserts decision #7 format. |
| `test_read_v2_diff_reflects_changed_name` | POST + PUT(name=NEW) → GET v2 → `changed:[{path:"name", old:"X", new:"NEW"}]`. |
| `test_read_v2_kind_is_put` | After a PUT, v2's `kind == "put"` (not "post"). |
| `test_read_404_for_unknown_schema` | GET /versions/1 on a random schema UUID → 404. |
| `test_read_404_for_unknown_version` | POST → GET version 999 → 404. |
| `test_read_422_for_non_positive_int_version` | GET version 0 or -1 → 422 slug `validation-error`. (FastAPI path validation; v must be ≥1 per §3.8.) |
| `test_read_isolated_across_workspaces` | POST in A; GET /versions/1 as B → 404. |

#### Rollback — `POST /schemas/:id/versions/:v/rollback` (§3.9)

| Test | Intent |
|---|---|
| `test_rollback_creates_new_version_with_target_body` | POST + PUT(description="changed") + rollback to v1 → v3 exists with `body == v1.body`. Asserts decision #5 (clone-forward). |
| `test_rollback_response_bumps_current_version` | Same scenario → response shows `current_version=3` and `description=="" ` (v1's value). |
| `test_rollback_kind_is_rollback` | GET v3 after rollback → `kind == "rollback"`, `parent_version == 2`. |
| `test_rollback_409_when_target_is_current` | POST + rollback to v1 (which IS current) → 409 slug `rollback-noop`. Asserts decision #13. |
| `test_rollback_404_for_unknown_target_version` | POST + rollback to v=999 → 404. |
| `test_rollback_requires_idempotency_key` | POST + PUT + rollback to v1 with no `Idempotency-Key` → 400 slug `missing-idempotency-key`. |
| `test_rollback_isolated_across_workspaces` | POST in A; rollback as B → 404. |

#### Concurrency / serialization invariant (§3.4)

| Test | Intent |
|---|---|
| `test_concurrent_puts_allocate_contiguous_version_numbers` | POST + 5× PUT fired via `asyncio.gather` (different bodies, different idempotency keys) → all 6 versions exist (v1..v6), contiguous, no duplicates, no UNIQUE-violation 500s. Asserts decision #12 (`SELECT ... FOR UPDATE` serialization). |

### 4.2 `tests/test_schemas_crud.py` — additive

Mutations on Phase 1a's tests for the new `current_version` field. Pure additions; existing 20 tests stay green.

| Test | Intent |
|---|---|
| `test_post_response_includes_current_version_1` | POST → body has `current_version: 1`. Asserts §3.2 + §3.3. |
| `test_put_response_bumps_current_version_to_2` | POST then PUT same id → PUT response has `current_version: 2`. Asserts §3.4. |

### 4.3 `tests/test_idempotency.py` — additive

One new test for rollback's replay semantics (mirrors the existing POST/PUT/DELETE replay tests).

| Test | Intent |
|---|---|
| `test_rollback_with_same_idempotency_key_replays_cached_response` | POST + PUT(change description) + rollback to v1 with key K → 200 with v3 body. Rollback again with key K + same target → 200 with identical body, AND `SELECT count(*) FROM schema_versions WHERE schema_id=...` still 3 (no v4). Asserts decision #8 + invariant §3.1 #6. |

---

## 5. What "green" means at G4

When all of the following pass, G3 is satisfied and G4 closes:
1. `uv run pytest tests/` exits 0.
2. Phase 0 suite remains green (49 tests) — no regressions.
3. Phase 1a suite remains green (29 tests) — no regressions (the 2 additions in 4.2 push the file from 20 → 22).
4. The 25 new tests in `test_schema_versions.py` are green.
5. The 1 new test in `test_idempotency.py` is green (file: 4 → 5).
6. Coverage of `src/kb/api/schema_versions.py` and `src/kb/domain/schema_versions.py` is ≥ 90%.
7. Total: 49 + 29 + 25 + 2 + 1 = **106 tests**. (`pytest --collect-only` is authoritative — count may shift ±1 during build.)

---

## 6. Sign-off

When Aniket approves this spec + the skeleton files, the Phase 1b G3 cell in [build_tracker §5](../../docs/build_tracker.md) flips 🟡 → ✅ and G4 (build) opens. Sign-off recorded in `build_tracker.md` §9.

---

## 7. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-23 | Spec drafted at Phase 1b G3 open. Buckets: new file `test_schema_versions.py` (25 = list 8 · read 9 · rollback 7 · concurrency 1) + 2 additive `test_schemas_crud.py` tests + 1 additive `test_idempotency.py` test = **28 new tests**. Total after Phase 1b: 49 (Phase 0) + 29 (Phase 1a) + 28 = 106. Awaiting sign-off. | Aniket |
