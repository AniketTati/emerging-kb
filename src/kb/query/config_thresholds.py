"""P1 (query-side) — resolve query thresholds through the layered-config
resolver so a domain / workspace override set in the Settings UI actually
changes query behavior, instead of a hardcoded constant.

Mirrors the write-path helper `kb.workers.tasks._resolve_threshold`. SAFE by
construction: the current hardcoded value is passed as `default` to
`resolve_config` AND returned on ANY resolver / coercion error — a config miss
or a malformed override value never breaks a query. The domain is taken from
`KB_DEFAULT_DOMAIN` (same source the write-path + vocabulary paths use); when
unset, layer-5 domain YAML is skipped and the value resolves from
`config/defaults.yaml` (== the hardcoded default).
"""

from __future__ import annotations

import os
from typing import Any


async def resolve_query_threshold(
    conn: Any,
    *,
    key: str,
    workspace_id: str,
    default: float,
    doc_type: str | None = None,
) -> float:
    """Resolve a float query threshold for `key`, falling back to `default`.

    `conn` may be None (e.g. a search call without persistence) — the
    resolver then skips DB override layers and reads domain / defaults YAML.
    """
    from kb.layered_config.resolver import resolve_config

    domain = os.environ.get("KB_DEFAULT_DOMAIN") or None
    try:
        value = await resolve_config(
            key,
            workspace_id=workspace_id,
            conn=conn,
            domain=domain,
            doc_type=doc_type,
            default=default,
        )
        return float(value)
    except Exception:  # noqa: BLE001
        return default
