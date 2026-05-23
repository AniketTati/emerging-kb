"""Emerging KB — multi-resolution knowledge base.

Single Python package; api, workers, and migrate entrypoints share this code.
See docs/build_tracker.md §5.1 for the locked Phase 0 layout.
"""

from importlib import metadata

__version__: str = metadata.version("emerging-kb")

__all__ = ["__version__"]
