"""P1 (query-side) — query thresholds read through layered config.

The query pipeline baked the CRAG refusal threshold (and others) in as
hardcoded constants, so a domain/workspace override saved in the Settings UI
had no effect on the refuse gate. P1 query-side routes it through
`resolve_query_threshold` → `layered_config.resolve_config`.

Mirrors tests/test_p1_config_thresholds.py (the write-path equivalent) against
the real config_overrides table + config/defaults.yaml:
  - a workspace-scope DB override changes the resolved CRAG threshold (done-when);
  - the `retrieval.crag.threshold` defaults.yaml key resolves to its YAML value
    (config is read, not just the passed default);
  - a missing key OR a malformed override falls back to the default — a config
    miss never breaks a query.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


async def test_workspace_override_changes_crag_threshold(db_url_superuser):
    from kb.layered_config import repo
    from kb.query.config_thresholds import resolve_query_threshold

    ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        await repo.insert_override(
            conn,
            workspace_id=ws,
            scope_kind="workspace",
            scope_id=ws,
            config_key="retrieval.crag.threshold",
            config_value=0.7,
        )
        await conn.commit()
        val = await resolve_query_threshold(
            conn,
            key="retrieval.crag.threshold",
            workspace_id=ws,
            default=0.5,
        )
    assert val == 0.7, "workspace override must beat the hardcoded refuse gate"


async def test_defaults_yaml_crag_value_wins_over_passed_default(db_url_superuser):
    """With no DB override, `retrieval.crag.threshold` resolves to its YAML
    value (0.5) — proving the orchestrator genuinely reads config rather than
    silently using the constant."""
    from kb.query.config_thresholds import resolve_query_threshold

    ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        val = await resolve_query_threshold(
            conn,
            key="retrieval.crag.threshold",
            workspace_id=ws,
            default=0.999,  # deliberately wrong — YAML (0.5) must win
        )
    assert val == 0.5, "defaults.yaml value should win over the passed default"


async def test_new_query_keys_resolve_from_defaults_yaml():
    """The two keys added/relied on this session resolve to their YAML values,
    proving the key path exists (no DB needed)."""
    from kb.query.config_thresholds import resolve_query_threshold

    ws = str(uuid.uuid4())
    am = await resolve_query_threshold(
        None, key="retrieval.auto_merge.threshold", workspace_id=ws, default=0.999,
    )
    assert am == 0.5, "retrieval.auto_merge.threshold should read 0.5 from defaults.yaml"
    gap = await resolve_query_threshold(
        None, key="conflicts.authority_dominance_gap", workspace_id=ws, default=0.999,
    )
    assert gap == 0.30, "conflicts.authority_dominance_gap should read 0.30 from defaults.yaml"


async def test_missing_key_falls_back_to_default(db_url_superuser):
    from kb.query.config_thresholds import resolve_query_threshold

    ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        val = await resolve_query_threshold(
            conn,
            key="retrieval.does.not.exist.anywhere",
            workspace_id=ws,
            default=0.42,
        )
    assert val == 0.42, "a key absent from every layer must return the default"


async def test_malformed_override_is_safe(db_url_superuser):
    """A non-numeric override must NOT break the query — it falls back to the
    default (a bad config can't crash the pipeline)."""
    from kb.layered_config import repo
    from kb.query.config_thresholds import resolve_query_threshold

    ws = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws,))
        await repo.insert_override(
            conn,
            workspace_id=ws,
            scope_kind="workspace",
            scope_id=ws,
            config_key="retrieval.crag.threshold",
            config_value="not-a-number",
        )
        await conn.commit()
        val = await resolve_query_threshold(
            conn,
            key="retrieval.crag.threshold",
            workspace_id=ws,
            default=0.5,
        )
    assert val == 0.5, "a malformed override must fall back to the default"


async def test_none_conn_resolves_from_defaults_yaml():
    """A search call may pass conn=None (no persistence). The resolver then
    skips DB layers and still reads defaults.yaml — no crash, no DB needed."""
    from kb.query.config_thresholds import resolve_query_threshold

    val = await resolve_query_threshold(
        None,
        key="retrieval.crag.threshold",
        workspace_id=str(uuid.uuid4()),
        default=0.999,
    )
    assert val == 0.5, "conn=None must still resolve the defaults.yaml value"
