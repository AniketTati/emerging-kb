"""Phase 9 — SSE endpoint tests over testcontainers.

Parses text/event-stream output from FastAPI's StreamingResponse via
the helper `parse_event_stream`.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import psycopg
import pytest

from kb.api.sse import parse_event_stream


pytestmark = pytest.mark.asyncio


@pytest.fixture
def test_workspace() -> str:
    return str(uuid.uuid4())


def headers(workspace: str) -> dict[str, str]:
    return {"X-Test-Workspace": workspace}


async def _seed_file_with_lifecycle(
    db_url: str, workspace: str, *, events: list[tuple[str | None, str, str]],
) -> str:
    """Insert one file + N file_lifecycle rows. `events` is a list of
    (from_state, to_state, event_name)."""
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"{workspace}-{file_id}".encode()).hexdigest()
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        # Determine final state for files row from last event
        final_state = events[-1][1] if events else "queued"
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (file_id, workspace, "t.pdf", sha, f"raw/{file_id}",
             "application/pdf", 100, final_state),
        )
        for from_s, to_s, ev in events:
            await conn.execute(
                "INSERT INTO file_lifecycle (file_id, workspace_id, from_state, to_state, event) "
                "VALUES (%s, %s, %s, %s, %s)",
                (file_id, workspace, from_s, to_s, ev),
            )
    return file_id


async def _seed_query_log_with_answer(
    db_url: str, workspace: str, *, answer: str = "answer text.",
    refused: bool = False, citations: list | None = None,
) -> str:
    qid = str(uuid.uuid4())
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(
            """
            INSERT INTO query_log (
                id, workspace_id, query, mode, endpoint, refused, answer, citations, model_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
            )
            """,
            (qid, workspace, "q", "H", "chat", refused, answer,
             json.dumps(citations or []), "test-model"),
        )
    return qid


# ===========================================================================
# Parser helper
# ===========================================================================


def test_parse_event_stream_handles_multiple_events():
    raw = (
        "event: lifecycle\ndata: {\"x\": 1}\n\n"
        "event: heartbeat\ndata: {}\n\n"
        "event: done\ndata: {\"y\": 2}\n\n"
    )
    events = parse_event_stream(raw)
    assert len(events) == 3
    assert events[0] == {"event": "lifecycle", "data": {"x": 1}}
    assert events[2] == {"event": "done", "data": {"y": 2}}


def test_parse_event_stream_skips_empty_blocks():
    raw = "\n\nevent: lifecycle\ndata: {}\n\n\n\n"
    events = parse_event_stream(raw)
    assert len(events) == 1


# ===========================================================================
# SSE upload status
# ===========================================================================


async def test_sse_upload_status_404_when_file_not_in_workspace(
    client, test_workspace,
):
    fake_id = str(uuid.uuid4())
    resp = await client.get(
        f"/upload/{fake_id}/status", headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_sse_upload_status_streams_lifecycle_events(
    client, test_workspace, db_url_superuser, monkeypatch,
):
    """Seed a file already at 'ready' — stream emits all events then closes."""
    # Speed up the poll so the test doesn't wait 1s.
    monkeypatch.setenv("KB_SSE_POLL_INTERVAL_MS", "10")
    # Re-import to pick up the monkeypatched env.
    import importlib

    import kb.api.sse as sse_mod
    importlib.reload(sse_mod)

    file_id = await _seed_file_with_lifecycle(
        db_url_superuser, test_workspace,
        events=[
            (None, "queued", "upload"),
            ("queued", "parsing", "task_started"),
            ("parsing", "parsed", "parse_done"),
            ("parsed", "ready", "ready_event"),
        ],
    )
    resp = await client.get(
        f"/upload/{file_id}/status", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_event_stream(resp.text)
    lifecycle_events = [e for e in events if e["event"] == "lifecycle"]
    assert len(lifecycle_events) == 4
    # Final event closes the stream with "done"
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["reason"] == "terminal_state"


async def test_sse_upload_status_closes_on_failed_state(
    client, test_workspace, db_url_superuser, monkeypatch,
):
    monkeypatch.setenv("KB_SSE_POLL_INTERVAL_MS", "10")
    import importlib

    import kb.api.sse as sse_mod
    importlib.reload(sse_mod)

    file_id = await _seed_file_with_lifecycle(
        db_url_superuser, test_workspace,
        events=[(None, "queued", "upload"), ("queued", "failed", "parse_failed")],
    )
    resp = await client.get(
        f"/upload/{file_id}/status", headers=headers(test_workspace),
    )
    events = parse_event_stream(resp.text)
    assert events[-1]["event"] == "done"


async def test_sse_upload_status_content_type(
    client, test_workspace, db_url_superuser, monkeypatch,
):
    monkeypatch.setenv("KB_SSE_POLL_INTERVAL_MS", "10")
    import importlib

    import kb.api.sse as sse_mod
    importlib.reload(sse_mod)

    file_id = await _seed_file_with_lifecycle(
        db_url_superuser, test_workspace,
        events=[(None, "ready", "upload")],
    )
    resp = await client.get(
        f"/upload/{file_id}/status", headers=headers(test_workspace),
    )
    assert "text/event-stream" in resp.headers["content-type"]
    assert resp.headers.get("cache-control") == "no-cache"


# ===========================================================================
# SSE chat replay
# ===========================================================================


async def test_sse_chat_stream_404_when_query_id_not_found(
    client, test_workspace,
):
    fake_id = str(uuid.uuid4())
    resp = await client.get(
        f"/chat/{fake_id}/stream", headers=headers(test_workspace),
    )
    assert resp.status_code == 404


async def test_sse_chat_stream_404_when_wrong_workspace(
    client, db_url_superuser,
):
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    qid = await _seed_query_log_with_answer(db_url_superuser, ws_a)
    resp = await client.get(f"/chat/{qid}/stream", headers=headers(ws_b))
    assert resp.status_code == 404


async def test_sse_chat_stream_short_answer_emits_one_chunk(
    client, test_workspace, db_url_superuser, monkeypatch,
):
    monkeypatch.setenv("KB_SSE_REPLAY_CHUNK_MS", "1")
    import importlib

    import kb.api.sse as sse_mod
    importlib.reload(sse_mod)

    qid = await _seed_query_log_with_answer(
        db_url_superuser, test_workspace, answer="short.",  # 6 chars
    )
    resp = await client.get(
        f"/chat/{qid}/stream", headers=headers(test_workspace),
    )
    assert resp.status_code == 200
    events = parse_event_stream(resp.text)
    chunks = [e for e in events if e["event"] == "chunk"]
    assert len(chunks) == 1
    assert chunks[0]["data"]["text"] == "short."


async def test_sse_chat_stream_replays_in_50_char_chunks(
    client, test_workspace, db_url_superuser, monkeypatch,
):
    monkeypatch.setenv("KB_SSE_REPLAY_CHUNK_MS", "1")
    import importlib

    import kb.api.sse as sse_mod
    importlib.reload(sse_mod)

    long = "x" * 175  # 50 + 50 + 50 + 25 = 4 chunks
    qid = await _seed_query_log_with_answer(
        db_url_superuser, test_workspace, answer=long,
    )
    resp = await client.get(
        f"/chat/{qid}/stream", headers=headers(test_workspace),
    )
    events = parse_event_stream(resp.text)
    chunks = [e for e in events if e["event"] == "chunk"]
    assert len(chunks) == 4
    # Reassembled chunks equal the original answer
    reassembled = "".join(c["data"]["text"] for c in chunks)
    assert reassembled == long


async def test_sse_chat_stream_done_event_includes_citations(
    client, test_workspace, db_url_superuser, monkeypatch,
):
    monkeypatch.setenv("KB_SSE_REPLAY_CHUNK_MS", "1")
    import importlib

    import kb.api.sse as sse_mod
    importlib.reload(sse_mod)

    citations = [
        {"hit_id": "h1", "kind": "chunk", "file_id": "f1",
         "snippet_preview": "snip", "score": 0.9}
    ]
    qid = await _seed_query_log_with_answer(
        db_url_superuser, test_workspace,
        answer="hello.", citations=citations,
    )
    resp = await client.get(
        f"/chat/{qid}/stream", headers=headers(test_workspace),
    )
    events = parse_event_stream(resp.text)
    done = [e for e in events if e["event"] == "done"]
    assert len(done) == 1
    assert done[0]["data"]["citations"] == citations


async def test_sse_chat_stream_refused_envelope_emits_done_only(
    client, test_workspace, db_url_superuser, monkeypatch,
):
    monkeypatch.setenv("KB_SSE_REPLAY_CHUNK_MS", "1")
    import importlib

    import kb.api.sse as sse_mod
    importlib.reload(sse_mod)

    qid = await _seed_query_log_with_answer(
        db_url_superuser, test_workspace, answer="", refused=True,
    )
    resp = await client.get(
        f"/chat/{qid}/stream", headers=headers(test_workspace),
    )
    events = parse_event_stream(resp.text)
    assert [e["event"] for e in events] == ["done"]


# ===========================================================================
# OpenAPI surface
# ===========================================================================


async def test_openapi_includes_sse_and_audit_routes(client):
    resp = await client.get("/openapi.json")
    paths = set(resp.json()["paths"].keys())
    assert "/audit" in paths
    assert "/upload/{file_id}/status" in paths
    assert "/chat/{query_id}/stream" in paths
