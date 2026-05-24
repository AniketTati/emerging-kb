# syntax=docker/dockerfile:1.7

# ----------------------------------------------------------------------------
# Builder stage — installs deps via uv into a venv we copy into the runtime.
# ----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Pin uv via the official image; copies its single static binary.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

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
        # Phase 2a — Docling pulls opencv-python + pillow which need these
        # system libs for image decoding + GL rendering paths.
        libxcb1 \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user.
RUN groupadd -r kb && useradd -r -g kb -u 1000 -d /app -s /usr/sbin/nologin kb

WORKDIR /app

COPY --from=builder --chown=kb:kb /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/tmp/huggingface \
    XDG_CACHE_HOME=/tmp/cache

# Pre-create writable cache directories for the kb user (uid 1000).
# Docling downloads layout models from HuggingFace at first parse.
RUN mkdir -p /tmp/huggingface /tmp/cache && chown -R kb:kb /tmp/huggingface /tmp/cache

USER kb

# Pre-warm Docling's layout + table models so the FIRST parse doesn't pay a
# ~2-3 min download. Cached in the image layer; container starts hit a warm
# cache. Saves the sweep's longest tail per cold worker start. Build-time
# cost: one-time ~500 MB pull + ~30s build delay.
#
# Explicit -o /tmp/huggingface — without it docling-tools writes to
# `${CWD}/.cache/docling/models` (relative to WORKDIR=/app, which `kb` user
# can't write to). Setting DOCLING_ARTIFACTS_PATH so runtime DocumentConverter
# instances find the pre-warmed models.
ENV DOCLING_ARTIFACTS_PATH=/tmp/huggingface/docling
RUN docling-tools models download -o /tmp/huggingface/docling \
    && echo "[build] Docling models pre-warmed in /tmp/huggingface/docling"

# Default entrypoint = API. docker-compose overrides for worker + migrate.
EXPOSE 8000
CMD ["uvicorn", "kb.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
