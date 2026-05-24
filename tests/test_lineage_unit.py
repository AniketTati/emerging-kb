"""Phase 6 — lineage path computation unit tests."""

from __future__ import annotations

import pytest

from kb.extraction.lineage import (
    compute_lineage_path,
    label_to_uuid,
    uuid_to_label,
)


def test_uuid_to_label_replaces_hyphens():
    uuid = "12345678-90ab-cdef-1234-567890abcdef"
    assert uuid_to_label(uuid) == "12345678_90ab_cdef_1234_567890abcdef"


def test_label_to_uuid_inverse():
    uuid = "12345678-90ab-cdef-1234-567890abcdef"
    label = uuid_to_label(uuid)
    assert label_to_uuid(label) == uuid


def test_compute_lineage_path_root_entity():
    """No parent → lineage_path = just the entity's label."""
    entity_id = "11111111-2222-3333-4444-555555555555"
    path = compute_lineage_path(entity_id=entity_id, parent_lineage_path=None)
    assert path == "11111111_2222_3333_4444_555555555555"


def test_compute_lineage_path_with_parent():
    """Parent has a lineage path → child's path appends."""
    entity_id = "22222222-2222-2222-2222-222222222222"
    parent = "11111111_1111_1111_1111_111111111111"
    path = compute_lineage_path(entity_id=entity_id, parent_lineage_path=parent)
    assert path == "11111111_1111_1111_1111_111111111111.22222222_2222_2222_2222_222222222222"


def test_compute_lineage_path_with_grandparent_chain():
    """Path with multiple ancestors composes correctly (parent.lineage_path
    already encodes its own ancestors)."""
    entity_id = "33333333-3333-3333-3333-333333333333"
    grandparent_chain = "aaa.bbb.111_222"
    path = compute_lineage_path(entity_id=entity_id, parent_lineage_path=grandparent_chain)
    assert path == "aaa.bbb.111_222.33333333_3333_3333_3333_333333333333"


def test_compute_lineage_path_handles_empty_parent_string():
    """An empty string for parent (not None) is treated like None — single label."""
    entity_id = "12345678-90ab-cdef-1234-567890abcdef"
    path = compute_lineage_path(entity_id=entity_id, parent_lineage_path="")
    assert path == "12345678_90ab_cdef_1234_567890abcdef"
