# ==============================================================================
# Animica AICF Provider (FastAPI) — Dockerfile (multi-arch, multi-stage)
# ------------------------------------------------------------------------------
# - Small, secure, reproducible container for the FastAPI provider template
# - Uses a builder stage to compile wheels, then copies only what’s needed
# - Runs as non-root, wrapped by Tini, with a simple healthcheck
# - Honors env: APP_MODULE, APP_HOST, APP_PORT, LOG_LEVEL (see .env.example)
# ==============================================================================

# -------------------------
# Base & Builder
# -------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# System deps needed to build any wheels (uvloop, cryptography, etc.)
# If you don't need compilation (pure wheels), you can drop build-essential.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy dependency manifests first for better Docker layer caching
# (requirements.txt is the primary; pyproject.toml is optional here for tooling)
COPY requirements.txt ./
# If your template/app also includes a pyproject.toml (PEP 621), uncomment:
# COPY pyproject.toml ./

# Pre-build wheels so the runtime image stays lean
RUN python -m pip install --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


# -------------------------
# Runtime Image
# -------------------------
FROM python:3.11-slim AS runtime

# Minimal runtime packages; curl for healthcheck; tini for proper PID1
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl tini \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

# Create non-root user
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd -g ${APP_GID} app && useradd -u ${APP_UID} -g ${APP_GID} -m -s /usr/sbin/nologin app

WORKDIR /app

# Install wheels from builder
COPY --from=builder /wheels /wheels
RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir /wheels/* \
    && rm -rf /wheels

# Copy application code (template content)
# .dockerignore should exclude venvs, tests, build artifacts, etc.
COPY . /app

# Sensible defaults; can be overridden at runtime via -e or .env
ENV APP_MODULE=app.main:app \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080 \
    LOG_LEVEL=info \
    PYTHONPATH=/app

# Expose HTTP port
EXPOSE 8080

# Healthcheck — expects a /healthz endpoint in your FastAPI app/router
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS "http://localhost:${APP_PORT}/healthz" || exit 1

# Drop to non-root
USER app:app

# Tini as PID1 to forward signals/collect zombies; shell to expand env in command
ENTRYPOINT ["/usr/bin/tini","--"]

# Use env-driven module path so the template remains flexible.
# If your app uses another module, set APP_MODULE accordingly:
#   docker run -e APP_MODULE=provider.app:app ...
CMD ["/bin/sh","-c","uvicorn ${APP_MODULE:-app.main:app} --host ${APP_HOST:-0.0.0.0} --port ${APP_PORT:-8080} --log-level ${LOG_LEVEL:-info} --proxy-headers --forwarded-allow-ips='*'"]
