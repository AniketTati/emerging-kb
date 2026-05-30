"""P1 — write-path thresholds read through layered config.

The ingestion pipeline used to bake thresholds in as hardcoded constants, so
a domain/workspace override saved in the Settings UI had no effect. P1 routes
identity (0.92/0.85), auto-promotion (0.80/0.90/0.90), field-convergence
similarity (0.85), and doc-chain title-similarity (0.7/0.8) through
`_resolve_threshold` → `layered_config.resolve_config`.

These tests exercise the shared `_resolve_threshold` helper end-to-end against
the real config_overrides table + config/defaults.yaml — proving the plumbing
all four sites share:
  - a workspace-scope DB override changes the resolved value (done-when);
  - the new defaults.yaml key resolves to its YAML value (config is read, not
    just the passed default);
  - a missing key OR a malformed override falls back to the default — a config
    miss never breaks ingest (pre-ingest checklist item H).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


async def test_workspace_override_changes_resolved_value(db_url_superuser):
    from kb.layered_config import repo
    from kb.workers.tasks import _resolve_threshold

    ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        await repo.insert_override(
            conn,
            workspace_id=ws,
            scope_kind="workspace",
            scope_id=ws,
            config_key="extraction.identity.embedding_high_threshold",
            config_value=0.55,
        )
        await conn.commit()
        val = await _resolve_threshold(
            conn,
            key="extraction.identity.embedding_high_threshold",
            workspace_id=ws,
            default=0.92,
        )
    assert val == 0.55, "workspace override must beat the hardcoded default"


async def test_defaults_yaml_value_wins_over_passed_default(db_url_superuser):
    """With no DB override, a key present in config/defaults.yaml resolves to
    its YAML value — proving the pipeline genuinely reads config rather than
    silently using the constant. Uses the new extraction-side vocabulary key."""
    from kb.workers.tasks import _resolve_threshold

    ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        val = await _resolve_threshold(
            conn,
            key="extraction.l2b.vocabulary.similarity_threshold",
            workspace_id=ws,
            default=0.999,  # deliberately wrong — YAML (0.85) must win
        )
    assert val == 0.85, "defaults.yaml value should win over the passed default"


async def test_missing_key_falls_back_to_default(db_url_superuser):
    from kb.workers.tasks import _resolve_threshold

    ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        val = await _resolve_threshold(
            conn,
            key="extraction.does.not.exist.anywhere",
            workspace_id=ws,
            default=0.77,
        )
    assert val == 0.77, "a key absent from every layer must return the default"


async def test_malformed_override_is_safe(db_url_superuser):
    """A non-numeric override value must NOT break ingest — it falls back to
    the default (pre-ingest checklist item H: a bad config can't crash the
    pipeline)."""
    from kb.layered_config import repo
    from kb.workers.tasks import _resolve_threshold

    ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        await repo.insert_override(
            conn,
            workspace_id=ws,
            scope_kind="workspace",
            scope_id=ws,
            config_key="extraction.identity.embedding_low_threshold",
            config_value="not-a-number",
        )
        await conn.commit()
        val = await _resolve_threshold(
            conn,
            key="extraction.identity.embedding_low_threshold",
            workspace_id=ws,
            default=0.85,
        )
    assert val == 0.85, "a malformed override must fall back to the default"
