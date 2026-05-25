"""Unit tests for tolerant LLM-output JSON recovery."""

from __future__ import annotations

from kb.extraction.json_recovery import parse_tolerant_array_in_object


def test_strict_parse_passes_through():
    raw = '{"mentions": [{"text": "a"}, {"text": "b"}]}'
    items, truncated = parse_tolerant_array_in_object(raw, "mentions")
    assert items == [{"text": "a"}, {"text": "b"}]
    assert truncated is False


def test_strips_code_fence():
    raw = '```json\n{"mentions": [{"text": "x"}]}\n```'
    items, truncated = parse_tolerant_array_in_object(raw, "mentions")
    assert items == [{"text": "x"}]
    assert truncated is False


def test_recovers_truncated_response():
    # Three complete elements + a partial fourth.
    raw = (
        '{"mentions": ['
        '{"text": "alpha", "type": "ORG"},'
        '{"text": "bravo", "type": "ORG"},'
        '{"text": "charlie", "type": "ORG"},'
        '{"text": "del'  # truncated mid-string
    )
    items, truncated = parse_tolerant_array_in_object(raw, "mentions")
    assert truncated is True
    assert len(items) == 3
    assert items[0]["text"] == "alpha"
    assert items[2]["text"] == "charlie"


def test_recovers_from_truncated_nested_object():
    # Element 4 has a nested object that's mid-write.
    raw = (
        '{"fields": ['
        '{"name": "n1", "value": "v1"},'
        '{"name": "n2", "value": "v2", "meta": {"nested": "x"}},'
        '{"name": "n3", "value": "v3"},'
        '{"name": "n4", "value": "v4", "meta": {"nested": "y'
    )
    items, truncated = parse_tolerant_array_in_object(raw, "fields")
    assert truncated is True
    assert len(items) == 3
    assert items[-1]["name"] == "n3"


def test_handles_string_escapes_inside_truncation():
    # Two earlier elements contain `\"` escapes (`\\"` in Python source =
    # `\"` in the actual JSON string). The walker must NOT exit string
    # state on the escaped quote, else it would mistake the following
    # `},` punctuation for object-close inside the string.
    raw = (
        '{"mentions": ['
        '{"text": "has a \\"quoted\\" word"},'
        '{"text": "next"},'
        '{"text": "tru'
    )
    items, truncated = parse_tolerant_array_in_object(raw, "mentions")
    assert truncated is True
    assert len(items) == 2
    assert items[0]["text"] == 'has a "quoted" word'
    assert items[1]["text"] == "next"


def test_empty_array_returns_empty():
    raw = '{"mentions": []}'
    items, truncated = parse_tolerant_array_in_object(raw, "mentions")
    assert items == []
    assert truncated is False


def test_missing_key_returns_empty():
    raw = '{"other": [{"x":1}]}'
    items, truncated = parse_tolerant_array_in_object(raw, "mentions")
    assert items == []
    assert truncated is False


def test_truncated_before_any_complete_element_returns_empty():
    raw = '{"mentions": [{"text": "no_close'
    items, truncated = parse_tolerant_array_in_object(raw, "mentions")
    assert items == []
    assert truncated is True


def test_skips_malformed_middle_element_keeps_rest():
    # Element 2 has a malformed escape (`\x` is not valid JSON).
    # Element-by-element recovery should drop it but keep 1, 3, 4.
    # This was the regression behind 5 docs returning 0 mentions on
    # Gemini's multi-line formatted output.
    raw = (
        '{"mentions": ['
        '{"text": "alpha", "type": "ORG"},'
        '{"text": "br\\xkenJSON", "type": "ORG"},'
        '{"text": "charlie", "type": "ORG"},'
        '{"text": "delta", "type": "ORG"}'
        ']}'
    )
    items, truncated = parse_tolerant_array_in_object(raw, "mentions")
    assert truncated is True  # strict parse failed → recovery path
    assert len(items) == 3
    assert items[0]["text"] == "alpha"
    assert items[1]["text"] == "charlie"
    assert items[2]["text"] == "delta"


def test_multiline_formatted_array():
    # Gemini sometimes returns multi-line indented JSON; strict parse
    # succeeds but make sure recovery walker also handles it cleanly.
    raw = """{
  "mentions": [
    {
      "text": "Foo",
      "type": "ORG"
    },
    {
      "text": "Bar",
      "type": "PERSON"
    }
  ]
}"""
    items, truncated = parse_tolerant_array_in_object(raw, "mentions")
    assert truncated is False
    assert len(items) == 2
