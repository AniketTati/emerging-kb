#!/usr/bin/env bash
# One-command local bootstrap for the Emerging KB demo.
#
#   cp .env.example .env          # then add KB_GEMINI_API_KEY (+ KB_ANTHROPIC_API_KEY)
#   ./scripts/bootstrap.sh
#   cd ui && cp -n .env.local.example .env.local && npm install && npm run dev
#   open http://localhost:3000
#
# Brings up the Docker stack (db · minio · migrate · api · worker), then
# restores the committed FINANCE demo seed (55 fully-extracted docs — markdown,
# email, digital PDF, scanned/OCR PDF, spreadsheets) into Postgres + MinIO.
# Idempotent: re-running skips the restore when the DB is already populated.
set -euo pipefail
cd "$(dirname "$0")/.."

WS="f0000000-0000-0000-0000-000000000001"
SEED="demo-corpus/seed"

# ---- 1. Preflight ---------------------------------------------------------
if [ ! -f .env ]; then
  echo "✗ .env not found.  Run:  cp .env.example .env   then add your API keys." >&2
  exit 1
fi
if ! grep -qE '^KB_GEMINI_API_KEY=.+' .env; then
  echo "✗ KB_GEMINI_API_KEY is empty in .env — required for query embeddings + OCR." >&2
  exit 1
fi
grep -qE '^KB_ANTHROPIC_API_KEY=.+' .env \
  || echo "⚠ KB_ANTHROPIC_API_KEY empty — answer generation will fall back to Gemini."

if [ ! -f "${SEED}/kb_seed.dump" ]; then
  echo "✗ ${SEED}/kb_seed.dump missing. Re-clone, or regenerate with scripts/seed_export.sh." >&2
  exit 1
fi

# ---- 2. Stack -------------------------------------------------------------
echo "▶ docker compose up (db · minio · migrate · api · worker)…"
# `up -d` builds the image on first run (fresh clone) and reuses it afterwards.
# Pass --build explicitly (KB_BOOTSTRAP_BUILD=1) only when you've changed app code.
docker compose up -d ${KB_BOOTSTRAP_BUILD:+--build}

# ---- 3. Wait for the API --------------------------------------------------
echo "▶ waiting for the API to become healthy on :8000…"
ok=""
for _ in $(seq 1 90); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[ -n "$ok" ] || { echo "✗ API never became healthy. See: docker compose logs api" >&2; exit 1; }

# ---- 4. Seed (idempotent) -------------------------------------------------
FILES=$(docker compose exec -T db psql -U kb -d kb -t -A -c "select count(*) from files" 2>/dev/null | tr -d '[:space:]' || echo 0)
if [ "${FILES:-0}" -gt 0 ]; then
  echo "▶ DB already has ${FILES} files — skipping seed restore (idempotent)."
else
  echo "▶ restoring Postgres seed (data-only) from ${SEED}/kb_seed.dump…"
  # pg_restore exits non-zero on any per-row warning; we verify the data landed
  # below rather than trust its exit code (--disable-triggers handles FK order).
  docker compose exec -T db pg_restore --data-only --disable-triggers --no-owner \
    -U kb -d kb < "${SEED}/kb_seed.dump" || echo "  (pg_restore reported warnings — verifying…)"
  RESTORED=$(docker compose exec -T db psql -U kb -d kb -t -A -c "select count(*) from files" | tr -d '[:space:]')
  [ "${RESTORED:-0}" -gt 0 ] || { echo "✗ seed restore landed 0 docs — aborting." >&2; exit 1; }
  echo "▶ restoring MinIO blobs (source files keyed by content sha)…"
  # Blobs are keyed by sha256(bytes), so they're reconstructed from the committed
  # source corpus. docker cp (tar-based) handles the space in the repo path.
  API_CID=$(docker compose ps -q api)
  docker cp "demo-corpus/domains/finance/." "${API_CID}:/tmp/seedsrc/" >/dev/null
  docker compose exec -T api python scripts/seed_minio.py /tmp/seedsrc
  N=$(docker compose exec -T db psql -U kb -d kb -t -A -c "select count(*) from files" | tr -d '[:space:]')
  echo "▶ seeded ${N} docs into the finance workspace."
fi

# ---- 5. Done --------------------------------------------------------------
cat <<EOF

✅ Backend up + seeded.   API → http://localhost:8000   (health: /health)

Next — start the UI (separate process; not in compose):
   cd ui && cp -n .env.local.example .env.local && npm install && npm run dev
Then open   http://localhost:3000     (default workspace: finance · 55 docs)

Optional — (re)load a schema from committed YAML [deliverable: demo schema]:
   docker compose exec -T api python scripts/load_schema.py --workspace ${WS} - \\
       < demo-corpus/domains/finance/schema.yaml
EOF
