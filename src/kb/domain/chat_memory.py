"""B6a / WA-12 — Conversation memory repo + ChatContext builder.

Design 8's three-tier memory:

  Tier 1 — Hot turns        : last K=6 verbatim turns; retrieval-side rewriter
  Tier 2 — Rolling summary  : Mem0-style compressed digest of older turns
  Tier 3 — Carry-forward    : structured (entities[], filters{}, prior_result_set_id)

This module provides:
  - chat_sessions / chat_turns CRUD
  - ChatContext dataclass
  - build_chat_context(conn, session_id) → ChatContext for the next turn
  - persist_turn(conn, session_id, ...) → writes the new chat_turns row
                                          and rolls the Tier-3 state on
                                          chat_sessions.

The Tier-2 summary regeneration is delegated to `kb.query.context_resolver`
(which owns the LLM call). This module is pure SQL + Python — no LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from kb.db.pool import Connection


# Per Design 8: K=6 verbatim turns for the retrieval-side rewriter.
DEFAULT_HOT_TURNS: int = 6
# Older-turn threshold beyond which Tier-2 summarization fires.
SUMMARY_THRESHOLD_TURNS: int = 10


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatTurn:
    id: str
    session_id: str
    turn_index: int
    user_query: str
    resolved_query: str | None
    answer: str | None
    citations: list
    created_at: str


@dataclass(frozen=True)
class ChatSession:
    id: str
    workspace_id: str
    user_id: str | None
    created_at: str
    last_active_at: str
    carry_forward_entities: tuple[str, ...]
    carry_forward_filters: dict
    prior_result_set_id: str | None
    older_turn_summary: str
    title: str | None


@dataclass(frozen=True)
class ChatContext:
    """The 3-tier context object handed to the orchestrator each turn."""
    session_id: str
    last_turn_id: str | None
    # Tier 3 — structured carry-forward
    carry_forward_entities: tuple[str, ...]
    carry_forward_filters: dict
    prior_result_set_id: str | None
    # Tier 2 — rolling Mem0-style summary
    older_turn_summary: str
    # Tier 1 — last K verbatim turns
    last_k_verbatim_turns: tuple[dict, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "last_turn_id": self.last_turn_id,
            "carry_forward_entities": list(self.carry_forward_entities),
            "carry_forward_filters": self.carry_forward_filters,
            "prior_result_set_id": self.prior_result_set_id,
            "older_turn_summary": self.older_turn_summary,
            "last_k_verbatim_turns": list(self.last_k_verbatim_turns),
        }


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


_SESSION_COLS = (
    "id::text, workspace_id::text, user_id::text, created_at, last_active_at, "
    "carry_forward_entities, carry_forward_filters, prior_result_set_id::text, "
    "older_turn_summary, title"
)


def _row_to_session(row: tuple) -> ChatSession:
    return ChatSession(
        id=str(row[0]),
        workspace_id=str(row[1]),
        user_id=str(row[2]) if row[2] else None,
        created_at=row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
        last_active_at=row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]),
        carry_forward_entities=tuple(str(e) for e in (row[5] or [])),
        carry_forward_filters=(
            row[6] if isinstance(row[6], dict)
            else (json.loads(row[6]) if row[6] else {})
        ),
        prior_result_set_id=str(row[7]) if row[7] else None,
        older_turn_summary=str(row[8] or ""),
        title=row[9],
    )


async def create_session(
    conn: Connection,
    *,
    workspace_id: str,
    user_id: str | None = None,
    title: str | None = None,
) -> str:
    cur = await conn.execute(
        "INSERT INTO chat_sessions (workspace_id, user_id, title) "
        "VALUES (%s, %s, %s) RETURNING id::text",
        (workspace_id, user_id, title),
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def read_session(
    conn: Connection, *, session_id: str,
) -> ChatSession | None:
    cur = await conn.execute(
        f"SELECT {_SESSION_COLS} FROM chat_sessions WHERE id = %s",
        (session_id,),
    )
    row = await cur.fetchone()
    return _row_to_session(row) if row else None


async def update_session_carry_forward(
    conn: Connection,
    *,
    session_id: str,
    carry_forward_entities: list[str] | None = None,
    carry_forward_filters: dict | None = None,
    prior_result_set_id: str | None = None,
    older_turn_summary: str | None = None,
) -> bool:
    """Merge-update the carry-forward fields. Any None argument is left
    untouched. Returns True on hit."""
    # COALESCE pattern: SET col = COALESCE(%s, col). For arrays/jsonb
    # the explicit NULL → no-change is cleaner than building dynamic SQL.
    cur = await conn.execute(
        """
        UPDATE chat_sessions SET
            carry_forward_entities = COALESCE(%s::uuid[], carry_forward_entities),
            carry_forward_filters  = COALESCE(%s::jsonb,  carry_forward_filters),
            prior_result_set_id    = COALESCE(%s::uuid,   prior_result_set_id),
            older_turn_summary     = COALESCE(%s,         older_turn_summary),
            last_active_at         = NOW()
         WHERE id = %s
        """,
        (
            carry_forward_entities,
            (json.dumps(carry_forward_filters)
             if carry_forward_filters is not None else None),
            prior_result_set_id,
            older_turn_summary,
            session_id,
        ),
    )
    return getattr(cur, "rowcount", 0) > 0


async def list_recent_sessions(
    conn: Connection, *, workspace_id: str, limit: int = 50,
) -> list[ChatSession]:
    cur = await conn.execute(
        f"SELECT {_SESSION_COLS} FROM chat_sessions "
        f"WHERE workspace_id = %s "
        f"ORDER BY last_active_at DESC LIMIT %s",
        (workspace_id, limit),
    )
    return [_row_to_session(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------


_TURN_COLS = (
    "id::text, session_id::text, turn_index, user_query, resolved_query, "
    "answer, citations, created_at"
)


def _row_to_turn(row: tuple) -> ChatTurn:
    return ChatTurn(
        id=str(row[0]),
        session_id=str(row[1]),
        turn_index=int(row[2]),
        user_query=str(row[3]),
        resolved_query=row[4],
        answer=row[5],
        citations=(
            row[6] if isinstance(row[6], list)
            else (json.loads(row[6]) if row[6] else [])
        ),
        created_at=(
            row[7].isoformat() if hasattr(row[7], "isoformat") else str(row[7])
        ),
    )


async def insert_turn(
    conn: Connection,
    *,
    workspace_id: str,
    session_id: str,
    user_query: str,
    resolved_query: str | None,
    answer: str | None,
    citations: list,
    context_used: dict,
    query_log_id: str | None = None,
    result_set_id: str | None = None,
) -> tuple[str, int]:
    """Insert a new chat_turns row, auto-assigning the next turn_index
    for the session. Returns (id, turn_index)."""
    cur = await conn.execute(
        """
        WITH next AS (
            SELECT COALESCE(MAX(turn_index) + 1, 0) AS idx
              FROM chat_turns WHERE session_id = %s
        )
        INSERT INTO chat_turns (
            workspace_id, session_id, turn_index,
            user_query, resolved_query, context_used, answer, citations,
            query_log_id, result_set_id
        )
        SELECT %s, %s, next.idx,
               %s, %s, %s::jsonb, %s, %s::jsonb,
               %s, %s
          FROM next
        RETURNING id::text, turn_index
        """,
        (
            session_id,
            workspace_id, session_id,
            user_query, resolved_query,
            json.dumps(context_used or {}),
            answer,
            json.dumps(citations or []),
            query_log_id, result_set_id,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return (str(row[0]), int(row[1]))


async def read_last_k_turns(
    conn: Connection, *, session_id: str, k: int = DEFAULT_HOT_TURNS,
) -> list[ChatTurn]:
    cur = await conn.execute(
        f"SELECT {_TURN_COLS} FROM chat_turns "
        f"WHERE session_id = %s "
        f"ORDER BY turn_index DESC LIMIT %s",
        (session_id, k),
    )
    rows = await cur.fetchall()
    # Return chronological order (oldest first) for prompt assembly.
    return list(reversed([_row_to_turn(r) for r in rows]))


async def count_turns_in_session(
    conn: Connection, *, session_id: str,
) -> int:
    cur = await conn.execute(
        "SELECT COUNT(*)::int FROM chat_turns WHERE session_id = %s",
        (session_id,),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# ChatContext builder
# ---------------------------------------------------------------------------


async def build_chat_context(
    conn: Connection,
    *,
    session_id: str,
    k_hot_turns: int = DEFAULT_HOT_TURNS,
) -> ChatContext | None:
    """Assemble the 3-tier ChatContext for the next turn. Returns None
    when the session doesn't exist (caller treats it as "fresh
    standalone query")."""
    session = await read_session(conn, session_id=session_id)
    if session is None:
        return None

    hot = await read_last_k_turns(conn, session_id=session_id, k=k_hot_turns)
    last_turn_id = hot[-1].id if hot else None

    hot_payload = tuple({
        "turn_index": t.turn_index,
        "user_query": t.user_query,
        "resolved_query": t.resolved_query,
        "answer_summary": _summarize_answer(t.answer),
    } for t in hot)

    return ChatContext(
        session_id=session.id,
        last_turn_id=last_turn_id,
        carry_forward_entities=session.carry_forward_entities,
        carry_forward_filters=session.carry_forward_filters,
        prior_result_set_id=session.prior_result_set_id,
        older_turn_summary=session.older_turn_summary,
        last_k_verbatim_turns=hot_payload,
    )


def _summarize_answer(answer: str | None, *, max_chars: int = 240) -> str | None:
    """Cheap one-liner for hot-turn previews. The LLM resolver does the
    real anaphora work; this just keeps the prompt size bounded."""
    if not answer:
        return None
    a = answer.strip()
    if len(a) <= max_chars:
        return a
    return a[:max_chars].rstrip() + "..."
