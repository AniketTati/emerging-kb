# Phase 1c — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-23 · awaiting sign-off.
> **Inputs:** Phase 1c G1 plan ([build_tracker §5.4](../../docs/build_tracker.md)) · Phase 1c G2 contracts ([api_contracts.md §4](../../docs/api_contracts.md)).
> **Outputs at G3:** this spec + four new red skeleton files. New imports point at `kb.api.schema_hierarchy` and `kb.domain.schema_hierarchy` that land at G4 — collection fails at G3 (expected "red" state).

---

## 1. Scope

Every endpoint in api_contracts §4 gets coverage. Every G1 decision in [build_tracker §5.4](../../docs/build_tracker.md) (new tables, type enum, NL description, kind enum, recorded-not-enforced metadata, soft delete, coarse-grained versioning, hierarchy-in-rollback, nested URLs, Idempotency-Key rules, RLS day-1, snapshot body shape, nested diff paths) has at least one test asserting it.

Four files cover the surface (**36 new tests**, on top of 106 from Phase 0+1a+1b; pytest `--collect-only` is authoritative):

| File | New tests | Covers |
|---|---|---|
| [`tests/test_schema_entities.py`](../test_schema_entities.py) | 10 | Entity CRUD (§4.5–§4.8); cascade-delete to fields + relationships; RLS isolation. |
| [`tests/test_schema_fields.py`](../test_schema_fields.py) | 8 | Field CRUD (§4.10–§4.13); type-enum CHECK fires 422; RLS. |
| [`tests/test_schema_relationships.py`](../test_schema_relationships.py) | 8 | Relationship CRUD (§4.15–§4.17); kind-enum 422; cross-schema FK rejected; RLS. |
| [`tests/test_schema_hierarchy_versions.py`](../test_schema_hierarchy_versions.py) | 10 | Coarse-grained versioning (every nested mutation bumps `current_version`); full-subtree snapshot shape (§4.2); nested-path diffs (§4.3); rollback restores entities + fields + relationships in one tx. |

**Out of scope (Phase 5+ / 6 / 8 / 9):**
- `extracted_entities` table + `lineage_path` ltree (Phase 5/6).
- `/entities/:id/{descendants,ancestors,siblings,breadcrumb}` (Phase 8).
- DB-level enforcement of `single_parent` and `cascade_delete` at extraction time (Phase 6).
- Field-type enum beyond `string/number/boolean/date/datetime` (Phase 6).
- `domain_vocabulary` (Phase 5).
- `audit_log` writes (Phase 9).
- `PUT /relationships/:rid` — soft-delete + re-create per §4.18.

---

## 2. Fixture strategy

Reuses Phase 0's testcontainers + the per-test workspace fixture from Phase 1a/1b. New helpers:

- **Hierarchy factory.** `create_entity(client, ws, schema_id, name=...)`, `create_field(client, ws, schema_id, entity_id, name=..., type=..., nl_description=...)`, `create_relationship(client, ws, schema_id, name=..., from_id=..., to_id=..., kind=...)` async helpers.
- **No new fixture for parent-schema setup.** Each test posts a fresh schema (via Phase 1a's `create_schema` helper) and builds the hierarchy on top.

Migrations now include `0007_schema_hierarchy.sql` (lands at G4). The session-scoped `db_migrated` fixture picks it up automatically.

---

## 3. Conventions

Same as Phase 0+1a+1b. Plus:

- **Subtree assertions** use exact-shape `==` comparison against fully-spelled-out expected dicts. Diff format is locked at G2; tests should fail loudly if shape drifts.
- **Name-resolution invariant** (§4.1 #5) tested explicitly: rollback after deleting + re-creating an entity binds the relationship back to the new UUID via the snapshot's `name` reference.
- **Cascade-on-entity-delete** (§4.8) verified via superuser psql row counts on `schema_fields` and `schema_relationships` — application-level cascade is the contract, but the test confirms the rows are actually soft-deleted in PG.

---

## 4. Test inventory

### 4.1 `tests/test_schema_entities.py` — entity CRUD + cascade + RLS

| Test | Intent |
|---|---|
| `test_post_creates_entity_with_documented_shape` | §4.4: response has exactly `{id, name, description, lifecycle_state, created_at, updated_at}` — no `workspace_id`, no `schema_id`. |
| `test_post_requires_idempotency_key` | §4.5: missing key → 400 slug `missing-idempotency-key`. |
| `test_post_validation_rejects_empty_name` | 422 slug `validation-error`. |
| `test_post_duplicate_name_returns_409` | 409 slug `entity-name-conflict`. |
| `test_post_404_for_unknown_schema` | Random UUID parent → 404 slug `not-found`. |
| `test_get_list_returns_paginated_entities` | Create 3 entities; GET → `{items: [3], total: 3, limit: 50, offset: 0}`. |
| `test_put_updates_name_and_description` | PUT response shows new values; `updated_at` bumped. |
| `test_delete_soft_deletes_entity` | DELETE → 204; GET → 404. Verify row exists in DB with `lifecycle_state='deleted'` via superuser SELECT. |
| `test_delete_cascades_to_fields_and_relationships` | Create entity E with 2 fields + 1 relationship referencing E. DELETE E → all 3 sub-resources soft-deleted (verified via superuser row count). |
| `test_entity_isolated_across_workspaces` | POST in workspace A; GET as workspace B → 404 (NOT 403, §4.1 #1). |

### 4.2 `tests/test_schema_fields.py` — field CRUD + type enum + RLS

| Test | Intent |
|---|---|
| `test_post_creates_field_with_documented_shape` | §4.9: response includes `nl_description`, `type`, `is_required`. |
| `test_post_requires_idempotency_key` | 400 slug `missing-idempotency-key`. |
| `test_post_validation_rejects_invalid_type` | `type="json"` → 422 slug `validation-error`. |
| `test_post_duplicate_name_returns_409` | 409 slug `field-name-conflict`. |
| `test_post_404_for_unknown_entity` | Random entity UUID → 404 slug `not-found`. |
| `test_get_list_returns_paginated_fields` | Create 4 fields; GET → `total=4`. |
| `test_put_updates_type_and_nl_description` | PUT changes type+nl_description; response shows new values. |
| `test_delete_soft_deletes_field` | DELETE → 204; row in DB has `lifecycle_state='deleted'`. |

### 4.3 `tests/test_schema_relationships.py` — relationship CRUD + kind enum + cross-schema + RLS

| Test | Intent |
|---|---|
| `test_post_creates_relationship_with_documented_shape` | §4.14: response includes `kind`, `cardinality`, `cascade_delete`, `single_parent`. |
| `test_post_requires_idempotency_key` | 400 slug `missing-idempotency-key`. |
| `test_post_validation_rejects_invalid_kind` | `kind="ownership"` → 422 slug `validation-error`. |
| `test_post_validation_rejects_cross_schema_entities` | Create schema S1 with entity E1; create schema S2 with entity E2; POST relationship under S1 with `from_entity_id=E1, to_entity_id=E2` → 422. |
| `test_post_duplicate_name_returns_409` | 409 slug `relationship-name-conflict`. |
| `test_get_list_returns_paginated_relationships` | Create 2 entities + 3 relationships between them; GET → `total=3`. |
| `test_delete_soft_deletes_relationship` | DELETE → 204; row in DB has `lifecycle_state='deleted'`. |
| `test_relationship_isolated_across_workspaces` | POST in A; GET as B → 404. |

### 4.4 `tests/test_schema_hierarchy_versions.py` — coarse-grained versioning + snapshot body + nested diff + rollback

| Test | Intent |
|---|---|
| `test_entity_post_bumps_schemas_current_version` | POST schema (v1) → POST entity → GET schema → `current_version=2`. |
| `test_field_post_bumps_schemas_current_version` | After field creation, current_version bumps further. |
| `test_relationship_post_bumps_schemas_current_version` | After relationship creation, current_version bumps further. |
| `test_snapshot_body_includes_entities_with_fields` | After creating entity E with 2 fields, GET `/versions/:current` → body's `entities[0]` has both fields nested + sorted by name. |
| `test_snapshot_body_includes_relationships_with_names` | After creating relationship referencing entities E1, E2 by UUID, snapshot has `relationships[0].from = "E1"` and `to = "E2"` (names, not UUIDs — §4.1 #5). |
| `test_rollback_restores_entities` | POST + add 2 entities → rollback to v1 → entities[] empty (and verified via psql superuser: rows back to soft-deleted state). |
| `test_rollback_restores_fields_under_entities` | Add entity E + 2 fields → snapshot v_n; delete a field → rollback to v_n → both fields restored under E with new UUIDs but same names. |
| `test_rollback_restores_relationships_by_name_resolution` | Add 2 entities + 1 relationship → delete the relationship → rollback to pre-delete version → relationship restored with `from`/`to` resolved back to the (still-active) entity UUIDs. |
| `test_diff_for_added_entity_uses_nested_path` | v2 adds entity "File"; `diff_from_prior.added` contains `{path: "entities.File", value: {...}}`. |
| `test_diff_for_changed_field_type_uses_nested_path` | v2 changes field type; diff has `{path: "entities.X.fields.Y.type", old: "string", new: "datetime"}`. |

---

## 5. What "green" means at G4

When all of the following pass, G3 is satisfied and G4 closes:
1. `uv run pytest tests/` exits 0.
2. Phase 0 (49) + 1a (29) + 1b (28) suites stay green — no regressions.
3. The 36 new tests across 4 new files are green.
4. Coverage of `src/kb/api/schema_hierarchy.py` and `src/kb/domain/schema_hierarchy.py` is ≥ 90%.
5. Total: 49 + 29 + 28 + 36 = **142 tests**. (`pytest --collect-only` is authoritative — count may shift ±1 during build.)

---

## 6. Sign-off

When Aniket approves this spec + the skeleton files, the Phase 1c G3 cell in [build_tracker §5](../../docs/build_tracker.md) flips 🟡 → ✅ and G4 (build) opens. Sign-off recorded in `build_tracker.md` §9.

---

## 7. Change log

| Date | Change | By |
|---|---|---|
| 2026-05-23 | Spec drafted at Phase 1c G3 open. Four new files: entities (10) + fields (8) + relationships (8) + hierarchy_versions (10) = **36 new tests**. Total after Phase 1c: 49 (Phase 0) + 29 (1a) + 28 (1b) + 36 = 142. Awaiting sign-off. | Aniket |
