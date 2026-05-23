# syntax=docker/dockerfile:1.7

# ----------------------------------------------------------------------------
# Builder stage — installs deps via uv into a venv we copy into the runtime.
# ----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Pin uv via the official image; copies its single static binary.
COPY --from=ghcr.io/astral-sh/uv:0.5.0 /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON=python3.12

# Install dependencies first (cached layer) before copying source.
# README + LICENSE are referenced by pyproject.toml metadata; hatchling needs them.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ----------------------------------------------------------------------------
# Runtime stage — minimal image with the venv + source.
# ----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user.
RUN groupadd -r kb && useradd -r -g kb -u 1000 -d /app -s /usr/sbin/nologin kb

WORKDIR /app

COPY --from=builder --chown=kb:kb /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER kb

# Default entrypoint = API. docker-compose overrides for worker + migrate.
EXPOSE 8000
CMD ["uvicorn", "kb.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
