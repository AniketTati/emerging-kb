"""Phase 7 — resolve_identities_file_impl integration tests."""

from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest


pytestmark = pytest.mark.asyncio


@contextmanager
def _env(**kwargs):
    prior = {k: os.environ.get(k) for k in kwargs}
    for k, v in kwargs.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def _seed_file_with_mentions(
    db_url: str,
    workspace_id: str,
    *,
    label: str,
    mentions: list[tuple[str, str]],  # [(mention_text, mention_type)]
    state: str = "identity_resolving",
) -> tuple[str, list[str]]:
    """Seed a file + extracted_mentions. Returns (file_id, mention_ids)."""
    file_id = str(uuid.uuid4())
    sha = hashlib.sha256(f"id-{workspace_id}-{label}".encode()).hexdigest()
    mention_ids: list[str] = []

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
        await conn.execute(
            "INSERT INTO files (id, workspace_id, name, content_sha, object_key, "
            "mime_type, size_bytes, lifecycle_state) "
            "VALUES (%s, %s, %s, %s, %s, 'application/pdf', 100, %s)",
            (file_id, workspace_id, f"id-{label}.pdf", sha, f"raw_files/{sha}", state),
        )
        await conn.execute(
            "INSERT INTO raw_pages (id, file_id, workspace_id, page_number, text, "
            "layout_json, content_sha) "
            "VALUES (%s, %s, %s, 1, 'page', '{}'::jsonb, %s)",
            (str(uuid.uuid4()), file_id, workspace_id, sha),
        )
        chunk_id = str(uuid.uuid4())
        chunk_sha = hashlib.sha256(f"ch-{workspace_id}-{label}".encode()).hexdigest()
        await conn.execute(
            "INSERT INTO chunks (id, file_id, workspace_id, chunk_index, text, "
            "source_page_numbers, token_count, content_sha) "
            "VALUES (%s, %s, %s, 0, 'c', %s, 5, %s)",
            (chunk_id, file_id, workspace_id, [1], chunk_sha),
        )
        cc_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO contextual_chunks (id, chunk_id, file_id, workspace_id, "
            "contextual_prefix, contextual_text, model_id, prefix_token_count, "
            "cache_creation_input_tokens, cache_read_input_tokens) "
            "VALUES (%s, %s, %s, %s, '', 'c', 'identity', 0, 0, 0)",
            (cc_id, chunk_id, file_id, workspace_id),
        )
        for text, mtype in mentions:
            mid = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO extracted_mentions "
                "(id, contextual_chunk_id, file_id, workspace_id, mention_text, mention_type, model_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'identity')",
                (mid, cc_id, file_id, workspace_id, text, mtype),
            )
            mention_ids.append(mid)
        await conn.commit()
    return file_id, mention_ids


async def test_resolve_identities_skips_non_identity_resolving(client, db_url_superuser):
    """State guard: skip if file is not in identity_resolving."""
    from kb.workers.tasks import resolve_identities_file_impl

    workspace = str(uuid.uuid4())
    file_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="r", mentions=[("X", "PERSON")],
        state="ready",
    )

    with _env(KB_DATABASE_URL=db_url_superuser, KB_IDENTITY_JUDGE="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await resolve_identities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM mention_to_entity "
            "WHERE mention_id IN (SELECT id FROM extracted_mentions WHERE file_id = %s)",
            (file_id,),
        )
        assert (await cur.fetchone())[0] == 0


async def test_resolve_identities_creates_new_entities_for_unique_mentions(
    client, db_url_superuser,
):
    """3 unique mentions → 3 new entities + 3 mention_to_entity links."""
    from kb.workers.tasks import resolve_identities_file_impl

    workspace = str(uuid.uuid4())
    file_id, mention_ids = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="u",
        mentions=[
            ("Acme Corp", "ORG"),
            ("John Smith", "PERSON"),
            ("2024-01-15", "DATE"),
        ],
    )

    with _env(KB_DATABASE_URL=db_url_superuser, KB_IDENTITY_JUDGE="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await resolve_identities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))

        cur = await conn.execute(
            "SELECT lifecycle_state FROM files WHERE id = %s", (file_id,),
        )
        assert (await cur.fetchone())[0] == "ready"

        cur = await conn.execute(
            "SELECT count(*) FROM entities WHERE workspace_id = %s", (workspace,),
        )
        assert (await cur.fetchone())[0] == 3

        cur = await conn.execute(
            "SELECT count(*) FROM mention_to_entity WHERE workspace_id = %s", (workspace,),
        )
        assert (await cur.fetchone())[0] == 3


async def test_resolve_identities_deterministic_match_reuses_entity(
    client, db_url_superuser,
):
    """Same exact name+type in second file → reuses existing entity (no new row)."""
    from kb.workers.tasks import resolve_identities_file_impl

    workspace = str(uuid.uuid4())
    # First file with "Acme Corp"
    f1_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="a",
        mentions=[("Acme Corp", "ORG")],
    )
    # Second file with same "Acme Corp" (different case to test lowercased match)
    f2_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="b",
        mentions=[("ACME CORP", "ORG")],
    )

    with _env(KB_DATABASE_URL=db_url_superuser, KB_IDENTITY_JUDGE="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await resolve_identities_file_impl(f1_id)
        await resolve_identities_file_impl(f2_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))

        # Only 1 entity (deterministic match collapsed both)
        cur = await conn.execute(
            "SELECT count(*) FROM entities WHERE workspace_id = %s", (workspace,),
        )
        assert (await cur.fetchone())[0] == 1

        # Both mention_to_entity rows point to the same entity
        cur = await conn.execute(
            "SELECT count(DISTINCT entity_id) FROM mention_to_entity "
            "WHERE workspace_id = %s",
            (workspace,),
        )
        assert (await cur.fetchone())[0] == 1

        # Lifecycle event with method counts
        cur = await conn.execute(
            "SELECT count(*) FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'identities_resolved'",
            (f2_id,),
        )
        assert (await cur.fetchone())[0] == 1


async def test_resolve_identities_re_run_is_idempotent(client, db_url_superuser):
    """Re-running deletes prior mention_to_entity rows and reinserts."""
    from kb.workers.tasks import resolve_identities_file_impl

    workspace = str(uuid.uuid4())
    file_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="r",
        mentions=[("X", "PERSON")],
    )

    with _env(KB_DATABASE_URL=db_url_superuser, KB_IDENTITY_JUDGE="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await resolve_identities_file_impl(file_id)

        # Reset state + re-run
        async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
            await conn.execute(
                "UPDATE files SET lifecycle_state = 'identity_resolving' WHERE id = %s",
                (file_id,),
            )
            await conn.commit()
        await resolve_identities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM mention_to_entity "
            "WHERE mention_id IN (SELECT id FROM extracted_mentions WHERE file_id = %s)",
            (file_id,),
        )
        # Still 1 (stable)
        assert (await cur.fetchone())[0] == 1


# ===========================================================================
# Edge cases + lifecycle payload verification (§5.14 #13)
# ===========================================================================


async def test_resolve_identities_with_no_mentions_advances_to_ready(client, db_url_superuser):
    """File with 0 mentions still advances lifecycle (don't block ingestion)."""
    from kb.workers.tasks import resolve_identities_file_impl

    workspace = str(uuid.uuid4())
    file_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="empty", mentions=[],
    )

    with _env(KB_DATABASE_URL=db_url_superuser, KB_IDENTITY_JUDGE="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await resolve_identities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute("SELECT lifecycle_state FROM files WHERE id = %s", (file_id,))
        assert (await cur.fetchone())[0] == "ready"
        cur = await conn.execute("SELECT count(*) FROM entities WHERE workspace_id = %s", (workspace,))
        assert (await cur.fetchone())[0] == 0


async def test_resolve_identities_lifecycle_payload_contains_method_counts(
    client, db_url_superuser,
):
    """Decision #13: payload {mention_count, deterministic, embedding, llm_judge, new}."""
    import json
    from kb.workers.tasks import resolve_identities_file_impl

    workspace = str(uuid.uuid4())
    file_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="counts",
        mentions=[("Alpha", "ORG"), ("Beta", "ORG")],
    )

    with _env(KB_DATABASE_URL=db_url_superuser, KB_IDENTITY_JUDGE="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await resolve_identities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT payload FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'identities_resolved'",
            (file_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        payload = row[0]
        # psycopg returns jsonb as dict already.
        assert payload["mention_count"] == 2
        assert "deterministic" in payload
        assert "embedding" in payload
        assert "llm_judge" in payload
        assert "new" in payload
        # All 2 mentions were brand new → method_counts['new'] should be 2
        assert payload["new"] == 2
        assert payload["deterministic"] + payload["embedding"] + payload["llm_judge"] + payload["new"] == payload["mention_count"]


async def test_resolve_identities_entities_persist_across_files(client, db_url_superuser):
    """Decision #4 + #11: entities table accumulates across files in a workspace.
    Two separate files mentioning 'ACME Corp' → 1 entity with mention_count=2."""
    from kb.workers.tasks import resolve_identities_file_impl

    workspace = str(uuid.uuid4())
    f1_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="p1",
        mentions=[("ACME Corp", "ORG")],
    )
    f2_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="p2",
        mentions=[("ACME Corp", "ORG")],
    )

    with _env(KB_DATABASE_URL=db_url_superuser, KB_IDENTITY_JUDGE="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await resolve_identities_file_impl(f1_id)
        await resolve_identities_file_impl(f2_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        # Only 1 entity exists (deterministic collapse) — and mention_count tracks both files.
        cur = await conn.execute(
            "SELECT count(*), max(mention_count) FROM entities WHERE workspace_id = %s",
            (workspace,),
        )
        n_entities, max_count = await cur.fetchone()
        assert n_entities == 1
        assert max_count >= 2, f"expected mention_count ≥ 2 after 2-file reuse; got {max_count}"


async def test_resolve_identities_records_resolved_method_deterministic(
    client, db_url_superuser,
):
    """Decision #5: resolved_method should be 'deterministic' when a second file
    matches an existing entity by exact name."""
    from kb.workers.tasks import resolve_identities_file_impl

    workspace = str(uuid.uuid4())
    f1_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="m1",
        mentions=[("Zenith", "ORG")],
    )
    f2_id, m2_ids = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="m2",
        mentions=[("Zenith", "ORG")],
    )

    with _env(KB_DATABASE_URL=db_url_superuser, KB_IDENTITY_JUDGE="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await resolve_identities_file_impl(f1_id)
        await resolve_identities_file_impl(f2_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        cur = await conn.execute(
            "SELECT resolved_method FROM mention_to_entity WHERE mention_id = %s",
            (m2_ids[0],),
        )
        method = (await cur.fetchone())[0]
        assert method == "deterministic", (
            f"Second file's Zenith mention should match via deterministic; got {method}"
        )


async def test_resolve_identities_inserts_mention_to_entity_with_workspace_id(
    client, db_url_superuser,
):
    """Decision #5: mention_to_entity rows carry workspace_id for RLS isolation."""
    from kb.workers.tasks import resolve_identities_file_impl

    workspace = str(uuid.uuid4())
    file_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="ws", mentions=[("Foo", "PERSON")],
    )

    with _env(KB_DATABASE_URL=db_url_superuser, KB_IDENTITY_JUDGE="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await resolve_identities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT workspace_id::text FROM mention_to_entity "
            "WHERE mention_id IN (SELECT id FROM extracted_mentions WHERE file_id = %s)",
            (file_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == workspace


async def test_resolve_identities_no_crash_when_state_already_ready(
    client, db_url_superuser,
):
    """State guard: if called twice and 2nd call sees 'ready', no crash + no work."""
    from kb.workers.tasks import resolve_identities_file_impl

    workspace = str(uuid.uuid4())
    file_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="r2", mentions=[("X", "PERSON")],
    )

    with _env(KB_DATABASE_URL=db_url_superuser, KB_IDENTITY_JUDGE="identity"):
        from kb.config import get_settings
        get_settings.cache_clear()
        await resolve_identities_file_impl(file_id)
        # State is now 'ready'. Call again — should be no-op.
        await resolve_identities_file_impl(file_id)

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM file_lifecycle "
            "WHERE file_id = %s AND event = 'identities_resolved'",
            (file_id,),
        )
        # Exactly 1 identities_resolved event (not duplicated by 2nd call)
        assert (await cur.fetchone())[0] == 1


async def test_resolve_identities_embedding_blocking_matches_existing_entity(
    client, db_url_superuser,
):
    """Decision #3 stage (b): embedding nearest-neighbor with cosine ≥ 0.92
    auto-matches existing entity even when the canonical_name differs.

    Setup: pre-seed an entity with a known one-hot embedding [1.0, 0, 0, ...].
    Then seed a mention with a DIFFERENT name (so deterministic match fails)
    but force its mock embedding to produce the same one-hot via the
    DeterministicMockEmbedder's deterministic-per-text behavior.

    The DeterministicMockEmbedder maps every text to a unique vector (hash-
    based), so we can't trivially produce identical vectors from different
    texts. So we mock the embedder at the test-injection point: monkeypatch
    make_embedder to return a stub that always returns one-hot vectors.
    """
    from kb.workers.tasks import resolve_identities_file_impl
    from kb.embeddings import EmbeddingResult

    workspace = str(uuid.uuid4())
    # Pre-seed an entity with one-hot [1.0, 0, ...]
    one_hot = [0.0] * 3072
    one_hot[0] = 1.0
    vec_literal = "[" + ",".join(repr(float(v)) for v in one_hot) + "]"
    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))
        await conn.execute(
            "INSERT INTO entities "
            "(workspace_id, canonical_name, entity_type, embedding) "
            "VALUES (%s, 'OldEntityName', 'ORG', %s::halfvec)",
            (workspace, vec_literal),
        )
        await conn.commit()

    # Seed a file with a mention whose canonical name is DIFFERENT
    # (deterministic match must fail) — but we'll force its embedding to
    # match the pre-seeded entity via monkeypatching make_embedder.
    file_id, _ = await _seed_file_with_mentions(
        db_url_superuser, workspace, label="emb",
        mentions=[("NewAlias", "ORG")],
    )

    # Stub embedder: always returns the one-hot vector.
    class StubEmbedder:
        async def embed_batch(self, texts):
            return [
                EmbeddingResult(vector=list(one_hot), model_id="stub", dim=3072)
                for _ in texts
            ]

    import kb.workers.tasks as tasks_mod
    # Inject the stub into the worker's embedder lookup path.
    orig_make_embedder = None
    import kb.embeddings as emb_mod
    orig_make_embedder = emb_mod.make_embedder
    emb_mod.make_embedder = lambda: StubEmbedder()
    try:
        with _env(KB_DATABASE_URL=db_url_superuser, KB_IDENTITY_JUDGE="identity"):
            from kb.config import get_settings
            get_settings.cache_clear()
            await resolve_identities_file_impl(file_id)
    finally:
        emb_mod.make_embedder = orig_make_embedder

    async with await psycopg.AsyncConnection.connect(db_url_superuser) as conn:
        await conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace,))

        # Only 1 entity should exist — the pre-seeded one (NewAlias didn't
        # create a 2nd entity because embedding-match collapsed it).
        cur = await conn.execute(
            "SELECT count(*) FROM entities WHERE workspace_id = %s", (workspace,),
        )
        n = (await cur.fetchone())[0]
        assert n == 1, f"expected 1 entity (embedding-match collapsed alias); got {n}"

        # The mention_to_entity row should use resolved_method='embedding'.
        cur = await conn.execute(
            "SELECT resolved_method FROM mention_to_entity "
            "WHERE mention_id IN (SELECT id FROM extracted_mentions WHERE file_id = %s)",
            (file_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "embedding", (
            f"expected resolved_method='embedding'; got '{row[0]}'"
        )
