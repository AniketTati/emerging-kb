"""T3 (§6.11) — Q-mode dynamic catalog derived from the live emerged schema.

The hand-curated `catalog.py` whitelist gates aggregation at the (table, column)
level only: it knows `extracted_entities.fields` is jsonb but has no idea which
jsonb KEYS exist in a given workspace or what they hold. That lets the planner
LLM guess keys (and silently NULL-aggregate a non-existent one), and lets a
numeric SUM run over a genuinely-textual field (e.g. `entity_detail.value`
holding company names / GST numbers) and ship a confident wrong/empty number.

This module derives a *per-workspace* catalog from
`domain.structured_schema.live_schema` (no duplication) + a bounded value-
castability probe, so:

  * the planner prompt carries the REAL canonical keys, each tagged with its
    GRAIN (doc_root scalar vs unit_type row) and whether it is numeric-
    aggregatable, and with spelling-variant unit_types grouped (so an aggregate
    doesn't miss half its rows to `transactionlisting` vs `transaction_listing`);
  * the validator can refuse a numeric aggregation over a field whose live
    values are non-numeric (§6.11 value_type gate) while still ALLOWING string-
    stored numerics ("INR 18,400") that `value_normalize` can parse — the
    distinction the raw `jsonb_typeof` probe cannot make.

**The gate is deliberately conservative** (refuse only a field that is clearly
text): the live cast can still NULL-skip a numeric-*intended* value the bare
`::numeric` cast can't parse ("INR 18,400/year"), so the load-bearing guard is
the §6.6 audit-envelope sanity check on *contributing rows*, not this gate.

Trust boundary (§6.11): this widens the aggregatable surface to whatever
extraction produced — wider than the hand-fixed whitelist. Safety still rests on
the OUTER fence (`catalog.ALLOWED_TABLES` — no new *tables* become queryable;
only jsonb keys inside the already-allowed `extracted_entities.fields` widen),
parameterized compile, read-only role, timeout, row cap, and the group-by
cardinality cap — never on the key whitelist alone.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from kb.domain.schema_epoch import read_schema_epoch
from kb.domain.structured_schema import LiveSchema, live_schema


# A numeric aggregation is refused ONLY when a known key holds NO jsonb numbers
# AND at most this fraction of its sampled values parse as numbers. Tight on
# purpose — see the module docstring (sanity check is the real guard).
NUMERIC_NONCASTABLE_MAX: float = 0.25
# Values sampled per (owner, key) for the castability probe.
SAMPLE_PER_KEY: int = 40


def _collapse(s: str) -> str:
    """Spelling-fold a unit_type / key: lowercase, drop every non-alphanumeric
    run. `transaction_listing`, `transactionlisting`, `Transaction Listing` all
    collapse to `transactionlisting`."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _is_castable(value: str | None) -> bool:
    """Whether a stored string parses to a number via the same currency/
    magnitude-aware parser that populates `value_numeric` at ingest. Reused so
    the gate's notion of 'numeric' matches the write-path's."""
    if value is None:
        return False
    try:
        from kb.extraction.value_normalize import normalize_value
        return normalize_value(str(value)) is not None
    except Exception:  # noqa: BLE001
        return False


@dataclass(frozen=True)
class KeyInfo:
    """One aggregatable jsonb key (or doc-root scalar) in the live schema."""
    key: str
    grain: str                 # 'doc_root' | 'unit:<unit_type>'
    value_type: str
    n_number: int              # rows where jsonb_typeof == 'number'
    n_total: int
    castable_fraction: float   # fraction of sampled string values that parse

    @property
    def numeric_aggregatable(self) -> bool:
        """True when SUM/AVG/numeric-MIN/MAX is meaningful: any real json number,
        or enough string values parse as numbers. Lenient by design — the gate
        refuses only the clear-text complement (`is_text_only`)."""
        return self.n_number > 0 or self.castable_fraction > NUMERIC_NONCASTABLE_MAX

    @property
    def is_text_only(self) -> bool:
        """True when the field is clearly non-numeric (no json numbers and
        almost nothing parses) — a numeric aggregation over it is refused."""
        return self.n_number == 0 and self.castable_fraction <= NUMERIC_NONCASTABLE_MAX


@dataclass(frozen=True)
class DynamicCatalog:
    """Per-workspace, epoch-pinned view of what is actually aggregatable."""
    workspace_id: str
    epoch: int
    # canonical_unit (collapsed) -> {key -> KeyInfo}
    unit_keys: dict[str, dict[str, KeyInfo]] = field(default_factory=dict)
    # canonical_unit -> sorted list of the real unit_type spellings it groups
    unit_variants: dict[str, list[str]] = field(default_factory=dict)
    # doc_type -> {canonical_key -> KeyInfo}  (doc_root scalars)
    doc_root_keys: dict[str, dict[str, KeyInfo]] = field(default_factory=dict)

    # -- lookups -------------------------------------------------------------

    def has_key(self, key: str, *, unit_types: list[str] | None = None) -> bool:
        """Whether `key` is a real jsonb key on the queried rows — used by the
        row-filter compiler to skip a mis-named column rather than NULL-exclude
        every row."""
        if unit_types:
            return any(
                key in self.unit_keys.get(_collapse(ut), {}) for ut in unit_types
            )
        if any(key in km for km in self.unit_keys.values()):
            return True
        return any(key in km for km in self.doc_root_keys.values())

    def variants_for(self, unit_type: str) -> list[str]:
        """All real unit_type spellings sharing `unit_type`'s collapsed form —
        so a plan can filter `unit_type IN (...)` without dropping half its
        rows. Returns [unit_type] when nothing groups with it."""
        c = _collapse(unit_type)
        return self.unit_variants.get(c) or [unit_type]

    def _unit_keyinfos(self, unit_types: list[str] | None) -> list[KeyInfo]:
        out: list[KeyInfo] = []
        if unit_types:
            for ut in unit_types:
                out.extend(self.unit_keys.get(_collapse(ut), {}).values())
        else:  # search every unit_type
            for keymap in self.unit_keys.values():
                out.extend(keymap.values())
        return out

    def numeric_aggregatable_verdict(
        self,
        key: str,
        *,
        unit_types: list[str] | None = None,
        doc_types: list[str] | None = None,
    ) -> bool | None:
        """Is `fields.<key>` numerically aggregatable?

        Returns:
          - True  — the key is known and numeric-aggregatable somewhere in scope;
          - False — the key is known in scope and EVERY match is text-only
                    (→ caller refuses cleanly);
          - None  — the key is unknown in scope (→ caller falls through to the
                    static catalog's behavior; never a NEW refusal: back-compat).
        """
        matches: list[KeyInfo] = [
            ki for ki in self._unit_keyinfos(unit_types) if ki.key == key
        ]
        if doc_types is not None:
            for dt in doc_types:
                ki = self.doc_root_keys.get(dt, {}).get(key)
                if ki is not None:
                    matches.append(ki)
        elif unit_types is None:
            # No scope hint at all — also consider doc-root scalars.
            for keymap in self.doc_root_keys.values():
                ki = keymap.get(key)
                if ki is not None:
                    matches.append(ki)
        if not matches:
            return None
        if any(ki.numeric_aggregatable for ki in matches):
            return True
        return False

    # -- planner prompt ------------------------------------------------------

    def prompt_block(self, *, max_units: int = 60, max_keys: int = 24) -> str:
        """Authoritative aggregation surface for the Q-mode planner prompt:
        canonical keys + grain + numeric flag, spelling-variants unioned."""
        if not self.unit_keys and not self.doc_root_keys:
            return ""
        lines = [
            "## WORKSPACE AGGREGATION CATALOG (authoritative — the ONLY keys "
            "that exist; do NOT invent others)",
            "",
            "Numeric-aggregatable keys are tagged [num] (valid for SUM/AVG/"
            "MIN/MAX). Untagged keys are text — use only for COUNT / "
            "COUNT_DISTINCT / group_by / filters.",
            "",
            "### unit_type rows  (grain: one row per structural item — "
            "from extracted_entities, filter `unit_type` IN the FULL spelling "
            "list shown, then aggregate `fields.<key>::<cast>`)",
        ]
        for canon in sorted(self.unit_keys.keys())[:max_units]:
            variants = self.unit_variants.get(canon, [canon])
            keymap = self.unit_keys[canon]
            keys_render = ", ".join(
                f"{k}{' [num]' if ki.numeric_aggregatable else ''}"
                for k, ki in sorted(keymap.items())[:max_keys]
            )
            label = " | ".join(variants)
            lines.append(f"  {label}:\n      {keys_render}")
        if self.doc_root_keys:
            lines.append("")
            lines.append(
                "### doc-root scalars  (grain: one per document — from "
                "extracted_entities with `parent_entity_id IS NULL`, or "
                "proposed_fields by inferred_doc_type)"
            )
            for dt in sorted(self.doc_root_keys.keys())[:max_units]:
                keymap = self.doc_root_keys[dt]
                keys_render = ", ".join(
                    f"{k}{' [num]' if ki.numeric_aggregatable else ''}"
                    for k, ki in sorted(keymap.items())[:max_keys]
                )
                lines.append(f"  {dt}: {keys_render}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build (reuses live_schema for structure; one extra SQL for castability)
# ---------------------------------------------------------------------------


async def _safe_fetch(conn: Any, sp: str, sql: str, params: Any) -> list[tuple] | None:
    """SELECT inside a SAVEPOINT (mirrors structured_schema._safe_fetch) so a
    probe failure leaves the shared request txn usable."""
    try:
        await conn.execute(f"SAVEPOINT {sp}")
    except Exception:  # noqa: BLE001
        return None
    try:
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
        try:
            await conn.execute(f"RELEASE SAVEPOINT {sp}")
        except Exception:  # noqa: BLE001
            pass
        return rows
    except Exception:  # noqa: BLE001
        try:
            await conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            await conn.execute(f"RELEASE SAVEPOINT {sp}")
        except Exception:  # noqa: BLE001
            pass
        return None


async def _probe_castability(
    conn: Any, workspace_id: str,
) -> dict[tuple[str, str], tuple[int, int, float]]:
    """For each (unit_type, key) in extracted_entities, return
    (n_number, n_total, castable_fraction) using a bounded value sample.

    One SQL gathers a capped sample of string values per (unit_type, key);
    `value_normalize` runs in Python so the gate's notion of 'numeric' matches
    the ingest write-path exactly."""
    rows = await _safe_fetch(
        conn, "dc_castprobe",
        "SELECT unit_type, key, "
        "       count(*) FILTER (WHERE jtype = 'number')::int AS n_number, "
        "       count(*)::int AS n_total, "
        "       (array_remove(array_agg(sval) "
        "          FILTER (WHERE jtype = 'string'), NULL))[1:%s] AS samples "
        "FROM ( "
        "  SELECT ee.unit_type, k.key, "
        "         jsonb_typeof(ee.fields -> k.key) AS jtype, "
        "         ee.fields ->> k.key AS sval "
        "  FROM extracted_entities ee "
        "  CROSS JOIN LATERAL jsonb_object_keys(ee.fields) AS k(key) "
        "  WHERE ee.workspace_id = %s AND ee.unit_type IS NOT NULL "
        ") s "
        "GROUP BY unit_type, key",
        (SAMPLE_PER_KEY, workspace_id),
    )
    out: dict[tuple[str, str], tuple[int, int, float]] = {}
    for unit_type, key, n_number, n_total, samples in (rows or []):
        sample_list = list(samples or [])
        if sample_list:
            n_ok = sum(1 for v in sample_list if _is_castable(v))
            frac = n_ok / len(sample_list)
        else:
            frac = 0.0
        out[(str(unit_type), str(key))] = (
            int(n_number or 0), int(n_total or 0), float(frac),
        )
    return out


def _from_live_schema(
    schema: LiveSchema,
    castability: dict[tuple[str, str], tuple[int, int, float]],
) -> DynamicCatalog:
    """Assemble the catalog from a LiveSchema + the castability probe (pure)."""
    # unit_type rows, grouped by collapsed spelling.
    unit_keys: dict[str, dict[str, KeyInfo]] = {}
    unit_variant_set: dict[str, set[str]] = {}
    for col in schema.unit_columns:
        canon = _collapse(col.unit_type)
        unit_variant_set.setdefault(canon, set()).add(col.unit_type)
        n_number, n_total, frac = castability.get(
            (col.unit_type, col.canonical_key), (0, 0, 0.0),
        )
        # live_schema already knows json-number presence; merge with the probe.
        if col.is_numeric and n_number == 0:
            n_number = max(n_number, col.n_rows)
        ki = KeyInfo(
            key=col.canonical_key, grain=col.grain or f"unit:{col.unit_type}",
            value_type=col.value_type, n_number=n_number,
            n_total=n_total or col.n_rows, castable_fraction=frac,
        )
        bucket = unit_keys.setdefault(canon, {})
        # If two spellings carry the same key, keep the more-numeric verdict.
        prev = bucket.get(col.canonical_key)
        if prev is None or (ki.numeric_aggregatable and not prev.numeric_aggregatable):
            bucket[col.canonical_key] = ki

    unit_variants = {
        c: sorted(v) for c, v in unit_variant_set.items()
    }

    # doc-root scalars, by doc_type.
    doc_root_keys: dict[str, dict[str, KeyInfo]] = {}
    for f in schema.fields:
        ki = KeyInfo(
            key=f.canonical_key, grain="doc_root", value_type=f.value_type,
            n_number=f.n_docs_with_field if f.is_numeric else 0,
            n_total=f.n_docs_with_field,
            # doc-root numeric verdict leans on live_schema's is_numeric (the
            # presence probe); string-scalar castability isn't sampled here.
            castable_fraction=1.0 if f.is_numeric else 0.0,
        )
        doc_root_keys.setdefault(f.doc_type, {})[f.canonical_key] = ki

    return DynamicCatalog(
        workspace_id=schema.workspace_id,
        epoch=schema.epoch,
        unit_keys=unit_keys,
        unit_variants=unit_variants,
        doc_root_keys=doc_root_keys,
    )


# ---------------------------------------------------------------------------
# Epoch-keyed cache (mirrors structured_schema)
# ---------------------------------------------------------------------------

_CACHE_MAX = 64
_cache: "OrderedDict[tuple[str, int], DynamicCatalog]" = OrderedDict()


def clear_cache() -> None:
    _cache.clear()


async def build_dynamic_catalog(
    conn: Any,
    *,
    workspace_id: str,
    schema: LiveSchema | None = None,
    force_refresh: bool = False,
) -> DynamicCatalog:
    """Build (and cache per schema epoch) the workspace's `DynamicCatalog`.

    `schema` may be passed when the caller already built a `LiveSchema` this
    turn (avoids a second introspection); otherwise it's fetched. Any probe
    failure yields a catalog with whatever structure resolved — never raises."""
    epoch = await read_schema_epoch(conn, workspace_id=workspace_id)
    key = (str(workspace_id), int(epoch))
    if not force_refresh and key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    if schema is None:
        schema = await live_schema(conn, workspace_id=workspace_id)
    castability = await _probe_castability(conn, str(workspace_id))
    catalog = _from_live_schema(schema, castability or {})
    _cache[key] = catalog
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return catalog
