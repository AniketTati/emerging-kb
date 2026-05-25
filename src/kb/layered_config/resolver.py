"""Resolver — combines layers 1-6 per Design 9.

Public API:
- `resolve_config(key, *, workspace_id, conn=None, domain=None,
                  doc_type=None, doc_id=None, user_id=None) -> Any`
- `ResolvedConfig` / `ResolvedEntry` — return type of `effective_config()`.

The resolver short-circuits at the first matching layer. When `conn` is
None, layers 1-4 are skipped (useful for early-boot resolution of values
the loader itself needs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kb.db.pool import Connection
from kb.layered_config import loader, repo


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedEntry:
    """One key/value with provenance."""
    key: str
    value: Any
    # Layer label (one of 'user', 'doc', 'doc_type', 'workspace', 'domain',
    # 'defaults'); the higher-up label that produced the value.
    layer: str
    # Scope_id that produced it ('default' for layers 5/6 when not pegged
    # to a single scope_id).
    scope_id: str | None = None


@dataclass(frozen=True)
class ResolvedConfig:
    """Tree-wide resolved view — what the Settings UI shows."""
    entries: list[ResolvedEntry] = field(default_factory=list)

    def get(self, key: str) -> ResolvedEntry | None:
        for entry in self.entries:
            if entry.key == key:
                return entry
        return None


class ConfigKeyNotFoundError(KeyError):
    """Key resolved to nothing across all six layers."""


# ---------------------------------------------------------------------------
# Single-key resolution (hot path)
# ---------------------------------------------------------------------------


# Ordered list of (scope_kind, scope_id_arg_name) tuples — most-specific first.
_DB_LAYER_ORDER: tuple[tuple[str, str], ...] = (
    ("user", "user_id"),
    ("doc", "doc_id"),
    ("doc_type", "doc_type"),
    ("workspace", "workspace_id"),
)


async def resolve_config(
    key: str,
    *,
    workspace_id: str,
    conn: Connection | None = None,
    domain: str | None = None,
    doc_type: str | None = None,
    doc_id: str | None = None,
    user_id: str | None = None,
    default: Any = ...,
) -> Any:
    """Return the resolved value for `key` (dotted path).

    If `default` is supplied, it's returned when no layer matches. Without
    `default`, `ConfigKeyNotFoundError` is raised.

    When `conn` is None, DB layers (1-4) are skipped. Useful for early boot
    paths that need to read defaults before the DB pool is up.
    """
    # --- Layers 1-4: DB overrides ---
    if conn is not None:
        scope_args = {
            "user_id": user_id,
            "doc_id": doc_id,
            "doc_type": doc_type,
            "workspace_id": workspace_id,
        }
        for scope_kind, scope_arg_name in _DB_LAYER_ORDER:
            scope_id_value = scope_args.get(scope_arg_name)
            if scope_id_value is None:
                continue
            override = await repo.read_override(
                conn,
                workspace_id=workspace_id,
                scope_kind=scope_kind,
                scope_id=str(scope_id_value),
                config_key=key,
            )
            if override is not None:
                return override.config_value

    # --- Layer 5: domain YAML ---
    if domain:
        domain_tree = loader.load_domain_tree(domain)
        found, value = loader.get_dotted(domain_tree, key)
        if found:
            return value

    # --- Layer 6: defaults YAML ---
    defaults_tree = loader.load_defaults_tree()
    found, value = loader.get_dotted(defaults_tree, key)
    if found:
        return value

    if default is not ...:
        return default
    raise ConfigKeyNotFoundError(
        f"key {key!r} not found in any layer (workspace={workspace_id}, "
        f"domain={domain}, doc_type={doc_type}, doc_id={doc_id}, user_id={user_id})"
    )


# ---------------------------------------------------------------------------
# Tree-wide resolved view (Effective Config UI)
# ---------------------------------------------------------------------------


async def effective_config(
    *,
    workspace_id: str,
    conn: Connection | None = None,
    domain: str | None = None,
    doc_type: str | None = None,
    doc_id: str | None = None,
    user_id: str | None = None,
) -> ResolvedConfig:
    """Walk every key from layers 5+6 and overlay layer 1-4 overrides.

    Returns a `ResolvedConfig` whose `entries` cover every leaf in the
    composed tree, each annotated with which layer produced it.
    """
    # Start from defaults — flat key set is the union of defaults + domain.
    defaults_tree = loader.load_defaults_tree()
    defaults_flat = loader.flatten_tree(defaults_tree)

    domain_tree = loader.load_domain_tree(domain) if domain else None
    domain_flat = loader.flatten_tree(domain_tree)

    # Union of keys across YAML; deterministic order matters for the UI.
    keys: list[str] = sorted({*defaults_flat.keys(), *domain_flat.keys()})

    # Pre-fetch all active overrides for this workspace in one round-trip;
    # we then in-memory match per key for each scope. Cheap; typical
    # workspaces have well under 100 overrides.
    db_overrides_by_key: dict[tuple[str, str, str], Any] = {}
    if conn is not None:
        records = await repo.read_workspace_overrides(
            conn, workspace_id=workspace_id
        )
        for r in records:
            db_overrides_by_key[(r.scope_kind, r.scope_id, r.config_key)] = r.config_value

    scope_args: dict[str, str | None] = {
        "user_id": user_id,
        "doc_id": doc_id,
        "doc_type": doc_type,
        "workspace_id": workspace_id,
    }

    entries: list[ResolvedEntry] = []
    for key in keys:
        # Walk layers 1-4 → 5 → 6.
        layer: str | None = None
        value: Any = None
        scope_id: str | None = None

        for scope_kind, scope_arg_name in _DB_LAYER_ORDER:
            sid = scope_args.get(scope_arg_name)
            if sid is None:
                continue
            if (scope_kind, str(sid), key) in db_overrides_by_key:
                layer = scope_kind
                value = db_overrides_by_key[(scope_kind, str(sid), key)]
                scope_id = str(sid)
                break

        if layer is None and key in domain_flat:
            layer = "domain"
            value = domain_flat[key]
            scope_id = domain

        if layer is None:
            layer = "defaults"
            value = defaults_flat.get(key)

        entries.append(
            ResolvedEntry(key=key, value=value, layer=layer, scope_id=scope_id)
        )

    return ResolvedConfig(entries=entries)
