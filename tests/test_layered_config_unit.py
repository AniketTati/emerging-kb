"""WA-1 / Design 9 — layered config unit tests.

Loader tests are pure-function (no DB). Resolver tests use a fake conn
double for the DB-layer cases — full DB integration runs in the API
test file under testcontainers (test_settings_api.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from omegaconf import OmegaConf

from kb.layered_config import (
    ConfigKeyNotFoundError,
    effective_config,
    load_defaults_tree,
    load_doc_type_tree,
    load_domain_tree,
    resolve_config,
)
from kb.layered_config import loader as loader_mod
from kb.layered_config import repo as repo_mod
from kb.layered_config.loader import (
    flatten_tree,
    get_dotted,
    reset_caches,
)


@pytest.fixture(autouse=True)
def _clear_loader_caches():
    """Each test gets a fresh load of the YAML files."""
    reset_caches()
    yield
    reset_caches()


# ===========================================================================
# Loader: YAML tree + dotted lookup
# ===========================================================================


def test_defaults_loads_and_has_expected_keys():
    tree = load_defaults_tree()
    assert "extraction" in tree
    assert "retrieval" in tree
    assert "models" in tree


def test_mixed_demo_domain_loads_with_overrides():
    tree = load_domain_tree("mixed_demo")
    assert tree is not None
    # Demo sets min_doc_count=5 (defaults=20).
    assert tree.extraction.l2b.auto_promotion.min_doc_count == 5


def test_unknown_domain_returns_none():
    assert load_domain_tree("not_a_real_domain") is None


def test_doc_type_loads_executed_contract():
    tree = load_doc_type_tree("executed_contract")
    assert tree is not None
    assert float(tree.authority) == 0.90


def test_doc_type_unknown_returns_none():
    assert load_doc_type_tree("not_a_real_type") is None


def test_get_dotted_returns_found_value():
    tree = load_defaults_tree()
    found, value = get_dotted(tree, "extraction.l3.rarity_threshold")
    assert found
    assert value == 0.95


def test_get_dotted_returns_not_found_on_missing_key():
    tree = load_defaults_tree()
    found, value = get_dotted(tree, "nonexistent.path.deep")
    assert not found
    assert value is None


def test_get_dotted_on_none_tree_returns_not_found():
    found, value = get_dotted(None, "anything")
    assert not found
    assert value is None


def test_flatten_tree_produces_dot_keyed_map():
    tree = load_defaults_tree()
    flat = flatten_tree(tree)
    assert "extraction.l3.rarity_threshold" in flat
    assert flat["extraction.l3.rarity_threshold"] == 0.95
    assert "models.extraction_llm" in flat


def test_flatten_tree_on_none_returns_empty():
    assert flatten_tree(None) == {}


# ===========================================================================
# Resolver (no-DB paths — layers 5 and 6)
# ===========================================================================


@pytest.mark.asyncio
async def test_resolve_falls_through_to_defaults():
    v = await resolve_config(
        "extraction.l3.rarity_threshold", workspace_id="ws"
    )
    assert v == 0.95


@pytest.mark.asyncio
async def test_resolve_domain_overrides_defaults():
    # mixed_demo sets min_doc_count=5; defaults=20.
    v = await resolve_config(
        "extraction.l2b.auto_promotion.min_doc_count",
        workspace_id="ws",
        domain="mixed_demo",
    )
    assert v == 5


@pytest.mark.asyncio
async def test_resolve_unknown_domain_falls_through_to_defaults():
    v = await resolve_config(
        "extraction.l2b.auto_promotion.min_doc_count",
        workspace_id="ws",
        domain="not_a_real_domain",
    )
    assert v == 20


@pytest.mark.asyncio
async def test_resolve_missing_key_raises_by_default():
    with pytest.raises(ConfigKeyNotFoundError):
        await resolve_config("nonexistent.deep.key", workspace_id="ws")


@pytest.mark.asyncio
async def test_resolve_missing_key_with_default_returns_default():
    v = await resolve_config(
        "nonexistent.deep.key", workspace_id="ws", default=42,
    )
    assert v == 42


# ===========================================================================
# Resolver — DB-layer paths via stub conn
# ===========================================================================


class _StubRecord:
    def __init__(self, value: Any) -> None:
        self.config_value = value


class _StubConn:
    """Just enough of the Connection contract for repo.read_override /
    read_workspace_overrides to be called by the resolver."""

    def __init__(self, overrides: dict[tuple[str, str, str, str], Any]) -> None:
        # Map (workspace_id, scope_kind, scope_id, config_key) -> value
        self.overrides = overrides

    async def execute(self, sql: str, params: tuple = ()):
        return _StubCursor(sql, params, self.overrides)


class _StubCursor:
    def __init__(
        self,
        sql: str,
        params: tuple,
        overrides: dict[tuple[str, str, str, str], Any],
    ) -> None:
        self.sql = sql
        self.params = params
        self.overrides = overrides

    async def fetchone(self):
        if "WHERE workspace_id" in self.sql and "LIMIT 1" in self.sql:
            # repo.read_override: params = (workspace, kind, scope_id, key)
            ws, kind, sid, key = self.params[:4]
            value = self.overrides.get((ws, kind, sid, key))
            if value is None:
                return None
            return (
                "fake-id", ws, kind, sid, key, value,
                "test reason", "test user", _FakeDt(), True,
            )
        return None

    async def fetchall(self):
        # read_workspace_overrides: returns all (workspace=params[0])
        ws = self.params[0]
        rows = []
        for (w, k, s, key), v in self.overrides.items():
            if w == ws:
                rows.append((
                    "fake-id", w, k, s, key, v,
                    "test reason", "test user", _FakeDt(), True,
                ))
        return rows


class _FakeDt:
    def isoformat(self) -> str:
        return "2026-05-25T00:00:00Z"


@pytest.mark.asyncio
async def test_resolve_workspace_override_beats_defaults():
    conn = _StubConn({
        ("ws-1", "workspace", "ws-1", "extraction.l3.rarity_threshold"): 0.5,
    })
    v = await resolve_config(
        "extraction.l3.rarity_threshold",
        workspace_id="ws-1",
        conn=conn,  # type: ignore[arg-type]
    )
    assert v == 0.5


@pytest.mark.asyncio
async def test_resolve_doc_type_beats_workspace_beats_domain_beats_defaults():
    conn = _StubConn({
        ("ws-1", "workspace", "ws-1", "extraction.l3.rarity_threshold"): 0.7,
        ("ws-1", "doc_type", "executed_contract", "extraction.l3.rarity_threshold"): 0.6,
    })
    v = await resolve_config(
        "extraction.l3.rarity_threshold",
        workspace_id="ws-1",
        conn=conn,  # type: ignore[arg-type]
        doc_type="executed_contract",
        domain="mixed_demo",
    )
    # doc_type is more specific than workspace, beats it.
    assert v == 0.6


@pytest.mark.asyncio
async def test_resolve_user_layer_is_most_specific():
    conn = _StubConn({
        ("ws-1", "user", "user-123", "models.extraction_llm"): "claude-opus-4-7",
        ("ws-1", "workspace", "ws-1", "models.extraction_llm"): "gemini-2.5-pro",
    })
    v = await resolve_config(
        "models.extraction_llm",
        workspace_id="ws-1",
        conn=conn,  # type: ignore[arg-type]
        user_id="user-123",
    )
    assert v == "claude-opus-4-7"


# ===========================================================================
# effective_config — Settings UI consumer
# ===========================================================================


@pytest.mark.asyncio
async def test_effective_config_returns_all_keys_with_layer_provenance():
    conn = _StubConn({
        ("ws-1", "workspace", "ws-1", "retrieval.rerank.top_k"): 100,
    })
    rc = await effective_config(
        workspace_id="ws-1",
        conn=conn,  # type: ignore[arg-type]
    )
    # Every defaults-tree leaf appears.
    keys = {e.key for e in rc.entries}
    assert "extraction.l3.rarity_threshold" in keys
    assert "models.extraction_llm" in keys

    overridden = rc.get("retrieval.rerank.top_k")
    assert overridden is not None
    assert overridden.layer == "workspace"
    assert overridden.value == 100
    assert overridden.scope_id == "ws-1"

    untouched = rc.get("extraction.l3.rarity_threshold")
    assert untouched is not None
    assert untouched.layer == "defaults"
    assert untouched.value == 0.95


@pytest.mark.asyncio
async def test_effective_config_domain_layer_attributed_correctly():
    rc = await effective_config(
        workspace_id="ws-1",
        domain="mixed_demo",
        # No conn → DB layers skipped.
    )
    entry = rc.get("extraction.l2b.auto_promotion.min_doc_count")
    assert entry is not None
    # Demo overrides defaults from 20 → 5; should attribute to layer 'domain'.
    assert entry.layer == "domain"
    assert entry.value == 5
    assert entry.scope_id == "mixed_demo"


# ===========================================================================
# Repo unit guard — scope_kind allowlist
# ===========================================================================


def test_allowed_scope_kinds_matches_migration_check():
    assert repo_mod.ALLOWED_SCOPE_KINDS == ("user", "doc", "doc_type", "workspace")
