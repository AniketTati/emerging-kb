# Phase 9 — Test Spec (G3)

> **Status:** G3 open · drafted 2026-05-25.
> **Inputs:** Phase 9 G1 plan ([build_tracker §5.16](../../docs/build_tracker.md), 12 decisions).
> **Outputs at G3:** this spec + 2 new red test files.

---

## 1. Scope

Two test files:

- `tests/test_audit_unit.py` — `/audit` endpoint tests over testcontainers (~10).
- `tests/test_sse_unit.py` — SSE endpoint tests over testcontainers, parsing `text/event-stream` format (~12).

Imports point at `kb.api.audit.{router}` + `kb.api.sse.{router, parse_event_stream}`. Both modules land at G4.

## 2. Decision → test mapping

### `/audit` (decisions #5, #6)

| Test |
|---|
| `test_audit_returns_empty_list_on_empty_workspace` |
| `test_audit_returns_recent_queries_newest_first` |
| `test_audit_response_shape_matches_spec` (entries have id, created_at, endpoint, query, mode, crag_score, refused, refusal_reason, answer truncated to 500, latency_ms, model_id) |
| `test_audit_respects_limit_param` (default 50, custom 5) |
| `test_audit_rejects_oversize_limit` (limit > 200 → 400) |
| `test_audit_cursor_pagination_walks_full_list` (write 7 rows, page 3+3+1) |
| `test_audit_workspace_isolation` (B doesn't see A's rows) |
| `test_audit_answer_truncated_to_500_chars` |

### SSE — upload status (decisions #1, #2, #3, #4, #9)

| Test |
|---|
| `test_sse_upload_status_streams_lifecycle_events_in_order` (seed 3 events; SSE emits 3) |
| `test_sse_upload_status_closes_when_lifecycle_reaches_ready` |
| `test_sse_upload_status_emits_heartbeat_when_idle` (mock time-jump) — or just assert event-name format |
| `test_sse_upload_status_404_when_file_not_in_workspace` |
| `test_sse_upload_status_content_type_is_event_stream` |

### SSE — chat replay (decisions #7, #8, #11)

| Test |
|---|
| `test_sse_chat_stream_replays_cached_answer_in_chunks` (50-char chunks) |
| `test_sse_chat_stream_emits_done_event_when_complete` |
| `test_sse_chat_stream_404_when_query_id_not_found` |
| `test_sse_chat_stream_404_when_wrong_workspace` |
| `test_sse_chat_stream_short_answer_emits_one_chunk` (answer < 50 chars) |
| `test_sse_chat_stream_includes_citations_in_done_payload` |

## 3. G3 exit criteria

- `uv run pytest tests/test_audit_unit.py tests/test_sse_unit.py --collect-only` — RED (ModuleNotFoundError).
- Rest of suite (518) remains green.
