# syntax=docker/dockerfile:1

# Build stage
# The memory explorer UI is built here so `docker compose up --build` needs no Node on the host.
FROM node:22-alpine AS ui
WORKDIR /ui
COPY ui-app/package.json ui-app/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui-app/ ./
RUN npm run build

FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
# A slow registry must not fail the build (uv's default is 30 s per download).
ENV UV_HTTP_TIMEOUT=180

# Copy project files (no uv.lock committed; editable install resolves from pyproject)
COPY pyproject.toml README.md ./

# Install with the extras both containers need: pg (pgvector/psycopg2) + mlpal (auth/usage) for
# the api, mcp (fastmcp/mlpal-mcp) for the sidecar. mlpal-* resolve from the private mlpal-pypi
# index (see [[tool.uv.index]] + [tool.uv.sources]); it's reachable at build like the sibling
# services (storage/skills). Quote the extras so the shell doesn't glob the brackets.
RUN uv pip install --system --no-cache -e ".[mcp,pg,local-embeddings]"

# Runtime stage
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install runtime system deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash appuser

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY src/ ./src/
COPY alembic/ ./alembic/
# The Vite app from the ui stage.
COPY --from=ui /ui/dist/ ./ui-app/dist/
COPY alembic.ini .
COPY pyproject.toml README.md ./

# Set ownership (+ the fastembed model cache mount point, so the named volume
# inherits appuser ownership on first creation)
RUN chown -R appuser:appgroup /app && \
    mkdir -p /var/cache/fastembed && chown appuser:appgroup /var/cache/fastembed

# Switch to non-root user
USER appuser

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "mlpal_memory_graph.main:app", "--host", "0.0.0.0", "--port", "8000"]
