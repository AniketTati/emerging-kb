"""Tolerant JSON parsing for LLM extraction output.

LLMs occasionally produce truncated JSON when the requested list grows
past the model's `max_output_tokens` cap. Strict `json.loads` then
raises on the half-written final element, throwing away every prior
element we DID receive successfully.

`parse_tolerant_array_in_object` recovers as much as possible:

  - First tries strict `json.loads`. If that works, returns the parsed
    value as-is.
  - On failure, locates the named array inside the top-level object,
    walks forward tracking brace depth, and remembers every position
    where the depth returns to 1 (= a complete inner object closed).
    Truncates to the last such position, appends `]}` to close the
    array + object, and re-parses.

Returns `(parsed_value, was_truncated)` so callers can log + alarm on
truncated responses without losing the data.

Used by mentions, proposed_fields, triples, and clause extractors —
all of which return `{"<key>": [ ... ]}` shapes that exhibit the same
truncation failure mode.
"""

from __future__ import annotations

import json
import re


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines)
    return text


def parse_tolerant_array_in_object(
    raw: str,
    array_key: str,
) -> tuple[list, bool]:
    """Parse `{"<array_key>": [ ... ]}` from `raw`. Returns (items, truncated).

    Strategy:
      1. Strip ```json code fence if present.
      2. Try strict parse. If success, return (items, False).
      3. On failure, walk the source string starting from the `[` after
         `"<array_key>":`. Extract each top-level `{...}` element span
         and attempt to parse it individually — malformed elements are
         skipped, so one bad row doesn't nuke the whole list.
      4. Return (items, True) — `True` because we needed recovery.
    """
    text = _strip_code_fence(raw)

    # Fast path — strict parse.
    try:
        parsed = json.loads(text)
        items = parsed.get(array_key) if isinstance(parsed, dict) else None
        if isinstance(items, list):
            return items, False
        return [], False
    except json.JSONDecodeError:
        pass

    # Slow path — element-by-element recovery.
    array_start = _find_array_open(text, array_key)
    if array_start < 0:
        return [], True

    items: list = []
    for span in _iter_element_spans(text, array_start):
        try:
            items.append(json.loads(text[span[0]:span[1] + 1]))
        except json.JSONDecodeError:
            # Skip malformed element; keep going.
            continue
    return items, True


def _find_array_open(text: str, key: str) -> int:
    """Return the index of the `[` that opens the array assigned to `key`
    in the top-level object, or -1 if not found."""
    # Allow whitespace + quoting variations around the colon.
    pattern = re.compile(r'"' + re.escape(key) + r'"\s*:\s*\[')
    m = pattern.search(text)
    if m is None:
        return -1
    return m.end() - 1  # position of the `[`


def _iter_element_spans(text: str, array_open: int):
    """Yield `(start, end)` tuples for each top-level `{...}` element in
    the array starting at `text[array_open]` (which is `[`). `end` is the
    inclusive index of the closing `}`. Uses brace + string-state tracking
    so escapes/nested objects/commas inside strings don't fool the scan.
    Stops at the matching `]` (or end of input for truncated input)."""
    depth = 0
    in_string = False
    escape = False
    elem_start = -1
    i = array_open + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                if depth == 0:
                    elem_start = i
                depth += 1
            elif ch == "[":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and elem_start >= 0:
                    yield (elem_start, i)
                    elem_start = -1
                elif depth < 0:
                    return
            elif ch == "]":
                # `]` at depth 0 = the outer array closing → stop.
                if depth == 0:
                    return
                depth -= 1
        i += 1
