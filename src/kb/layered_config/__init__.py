"""Layered configuration — Design 9.

Six-layer resolution (most-specific → most-general):

    1. user          DB: config_overrides scope_kind='user'
    2. doc           DB: config_overrides scope_kind='doc'
    3. doc_type      DB: config_overrides scope_kind='doc_type'
    4. workspace     DB: config_overrides scope_kind='workspace'
    5. domain        YAML: config/domains/<domain>.yaml
    6. defaults      YAML: config/defaults.yaml

Layers 5-6 load at boot via OmegaConf. Layers 1-4 read at runtime via
the `config_overrides` table (Phase WA-1 migration 0020).

Public API:

    from kb.layered_config import resolve_config, ResolvedConfig

    val = await resolve_config(
        "extraction.l3.rarity_threshold",
        workspace_id=ws,
        doc_type="executed_contract",
        conn=conn,
    )
    # → 0.95 (from defaults.yaml — no overrides applied)

    rc = await resolve_config(
        "models.extraction_llm",
        workspace_id=ws,
        conn=conn,
    )
    # → "gemini-2.5-flash"

The companion `effective_config(scope...)` helper returns every key in the
tree with the resolved value + which layer produced it — that's what the
Settings → Effective Config UI consumes.
"""

from kb.layered_config.loader import (
    DEFAULTS_PATH,
    DOMAINS_DIR,
    DOC_TYPES_DIR,
    PROMPTS_DIR,
    CONFIG_ROOT,
    load_defaults_tree,
    load_domain_tree,
    load_doc_type_tree,
)
from kb.layered_config.repo import (
    OverrideRecord,
    insert_override,
    revoke_override,
    read_override,
    read_workspace_overrides,
)
from kb.layered_config.resolver import (
    ConfigKeyNotFoundError,
    ResolvedConfig,
    ResolvedEntry,
    effective_config,
    resolve_config,
)

__all__ = [
    # loader
    "DEFAULTS_PATH",
    "DOMAINS_DIR",
    "DOC_TYPES_DIR",
    "PROMPTS_DIR",
    "CONFIG_ROOT",
    "load_defaults_tree",
    "load_domain_tree",
    "load_doc_type_tree",
    # repo
    "OverrideRecord",
    "insert_override",
    "revoke_override",
    "read_override",
    "read_workspace_overrides",
    # resolver
    "ConfigKeyNotFoundError",
    "ResolvedConfig",
    "ResolvedEntry",
    "effective_config",
    "resolve_config",
]
