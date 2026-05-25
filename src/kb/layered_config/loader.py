"""Layer 5 + 6 loader — YAML on disk via OmegaConf.

Caches per-path. Cache invalidates only on process restart (config files
are read-only at runtime; mutation lives in `config_overrides` DB).

Path discovery: `KB_CONFIG_ROOT` env var or the repo's `config/` directory.
Production deployments set the env to a mounted volume.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from omegaconf import DictConfig, ListConfig, OmegaConf


def _config_root() -> Path:
    env_value = os.environ.get("KB_CONFIG_ROOT")
    if env_value:
        return Path(env_value).resolve()
    # Repo-relative default — three parents up from this file:
    # src/kb/layered_config/loader.py → src/kb/layered_config → src/kb
    # → src → repo root → config/
    here = Path(__file__).resolve()
    return here.parents[3] / "config"


CONFIG_ROOT: Path = _config_root()
DEFAULTS_PATH: Path = CONFIG_ROOT / "defaults.yaml"
DOMAINS_DIR: Path = CONFIG_ROOT / "domains"
DOC_TYPES_DIR: Path = CONFIG_ROOT / "doc_types"
PROMPTS_DIR: Path = CONFIG_ROOT / "prompts"


class ConfigLoadError(Exception):
    """Raised when a referenced YAML file can't be loaded or parsed."""


def _load_yaml(path: Path) -> DictConfig:
    if not path.exists():
        raise ConfigLoadError(f"missing config file: {path}")
    try:
        loaded = OmegaConf.load(path)
    except Exception as exc:
        raise ConfigLoadError(f"failed to parse {path}: {exc}") from exc
    if not isinstance(loaded, DictConfig):
        raise ConfigLoadError(
            f"{path}: expected a YAML mapping at the root, got {type(loaded).__name__}"
        )
    return loaded


@lru_cache(maxsize=1)
def load_defaults_tree() -> DictConfig:
    """Layer 6 — global defaults. Required to exist; loader fails loud if not."""
    return _load_yaml(DEFAULTS_PATH)


@lru_cache(maxsize=32)
def load_domain_tree(domain: str) -> DictConfig | None:
    """Layer 5 — `config/domains/<domain>.yaml`. Returns None if absent."""
    if not domain:
        return None
    path = DOMAINS_DIR / f"{domain}.yaml"
    if not path.exists():
        return None
    return _load_yaml(path)


@lru_cache(maxsize=128)
def load_doc_type_tree(doc_type: str) -> DictConfig | None:
    """Per-doc-type tree — used by the source-authority lookup (Design 2)
    + L3 plugin selection. Returns None if absent.
    """
    if not doc_type:
        return None
    path = DOC_TYPES_DIR / f"{doc_type}.yaml"
    if not path.exists():
        return None
    return _load_yaml(path)


def _to_plain(value: Any) -> Any:
    """OmegaConf-aware leaf coercion. Primitives pass through; nested
    DictConfig / ListConfig get converted to plain dict / list."""
    if isinstance(value, (DictConfig, ListConfig)):
        return OmegaConf.to_object(value)  # type: ignore[arg-type]
    return value


def _walk(tree: DictConfig, prefix: tuple[str, ...] = ()) -> list[tuple[str, Any]]:
    """Flatten a DictConfig into [(dot.key, leaf_value), ...]. Recurses into
    nested dicts only — lists count as leaves (UI renders them as arrays)."""
    out: list[tuple[str, Any]] = []
    for raw_key in tree.keys():
        key = str(raw_key)
        value = tree[key]
        if isinstance(value, DictConfig):
            out.extend(_walk(value, prefix + (key,)))
        else:
            out.append((".".join(prefix + (key,)), _to_plain(value)))
    return out


def flatten_tree(tree: DictConfig | None) -> dict[str, Any]:
    """Return a {dot.key: value} mapping for every leaf in `tree`. Used by
    the Effective Config UI to enumerate all keys with their layer."""
    if tree is None:
        return {}
    return dict(_walk(tree))


def get_dotted(tree: DictConfig | None, dotted_key: str) -> tuple[bool, Any]:
    """Read a dot-keyed path from `tree`.

    Returns `(found, value)`. `found=False` means the key path doesn't
    exist or any segment is None. `found=True` may carry a None value if
    that's what was set.
    """
    if tree is None:
        return False, None
    cur: Any = tree
    for seg in dotted_key.split("."):
        if isinstance(cur, DictConfig) and seg in cur:
            cur = cur[seg]
        else:
            return False, None
    return True, _to_plain(cur)


def reset_caches() -> None:
    """Test helper — drop the LRU caches so a per-test config edit is visible."""
    load_defaults_tree.cache_clear()
    load_domain_tree.cache_clear()
    load_doc_type_tree.cache_clear()
