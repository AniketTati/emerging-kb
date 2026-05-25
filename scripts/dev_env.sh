# Source this from your shell to set up env for native dev.
#
#   source scripts/dev_env.sh
#
# Loads .env (which has docker-internal hostnames "db" / "minio")
# then overrides the host-targeting vars to localhost so the natively-
# running api + worker can reach the Docker-published db + minio.
#
# Companions:
#   scripts/dev_api.sh    — launch api locally
#   scripts/dev_worker.sh — launch procrastinate worker locally
#   ui/                   — `cd ui && npm run dev` for the Next.js app

# Load .env (export every key/value).
set -a
# shellcheck disable=SC1091
source "$(git rev-parse --show-toplevel)/.env"
set +a

# Override to localhost — db + minio are docker-published to host (see
# docker-compose.override.yml). The api/worker run on the host now.
export KB_POSTGRES_HOST=localhost
export KB_MINIO_ENDPOINT=localhost:9000

# Procrastinate + repo's DB URL helpers want explicit URLs.
export KB_DATABASE_URL="postgresql://${KB_POSTGRES_USER}:${KB_POSTGRES_PASSWORD}@localhost:5432/${KB_POSTGRES_DB}"
export KB_DB_URL="postgresql://kb_app:${KB_APP_PASSWORD}@localhost:5432/${KB_POSTGRES_DB}"

# Convenience: when uv-run scripts depend on the project's venv.
export PATH="$(git rev-parse --show-toplevel)/.venv/bin:$PATH"

echo "[dev_env] loaded — api will hit localhost:5432 + localhost:9000"
