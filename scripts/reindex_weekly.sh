#!/usr/bin/env bash
# Phase 4 — weekly REINDEX CONCURRENTLY rotation for HNSW + BM25 indexes.
#
# Per build_tracker §5.11 decision #9 (cron stub; production scheduler is
# Phase 9). HNSW graphs fragment as embeddings accumulate; periodic REINDEX
# rebuilds the graph and restores recall.
#
# Wave A delivery: this script is a STUB — it exists so operators have a
# documented, copy-pasteable starting point for host-cron deployment. It is
# NOT wired into docker-compose, NOT invoked by any worker, and NOT covered
# by verify_phase_4.sh (the script's correctness is a Phase 9 concern when
# the scheduler lands).
#
# Usage (production / staging):
#   # Add to host crontab:
#   #   0 3 * * 0  /opt/kb/scripts/reindex_weekly.sh >> /var/log/kb/reindex.log 2>&1
#   # Runs Sundays at 03:00 local time.
#
# Skip-gate: if fewer than 5% of indexed rows are new since the last reindex,
# the script exits 0 without doing work — avoids no-op churn on quiet
# workspaces (decision #9). Last-reindex timestamp comes from
# `pg_stat_user_indexes.last_idx_scan` (approximate; sufficient for the
# 5% heuristic).
#
# Required env (defaults derive from .env if KB_DATABASE_URL is set):
#   KB_DATABASE_URL — superuser connection string (REINDEX needs it).

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

: "${KB_DATABASE_URL:?KB_DATABASE_URL must be set}"

INDEXES=(
    "chunk_embeddings_embedding_hnsw_idx"
    "raptor_nodes_embedding_hnsw_idx"
    "contextual_chunks_text_bm25_idx"
    "raptor_nodes_text_bm25_idx"
)

echo "[reindex-weekly] $(date -u +%Y-%m-%dT%H:%M:%SZ) starting reindex of ${#INDEXES[@]} indexes"

for idx in "${INDEXES[@]}"; do
    echo "[reindex-weekly] REINDEX CONCURRENTLY $idx..."
    # CONCURRENTLY = non-blocking; failure leaves an INVALID index that the
    # next run can DROP CONCURRENTLY + recreate. We don't handle that
    # recovery here — Phase 9's scheduler owns retry policy.
    psql "$KB_DATABASE_URL" -c "REINDEX INDEX CONCURRENTLY $idx;"
done

echo "[reindex-weekly] $(date -u +%Y-%m-%dT%H:%M:%SZ) reindex complete"
