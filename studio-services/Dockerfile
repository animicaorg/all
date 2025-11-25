# ──────────────────────────────────────────────────────────────────────────────
# Animica Studio Services — Production Dockerfile
# Uses Gunicorn with Uvicorn workers. No server-side signing in this service.
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/home/app/.local/bin:${PATH}"

# System deps: gcc for building msgspec/cbor2 wheels; curl for optional healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc curl && \
    rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

# Install Python dependencies first (max layer cache)
COPY studio-services/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    pip install --no-cache-dir gunicorn==22.0.0

# Copy application code
COPY studio-services /app
# If the repo includes local SDKs/VM packages and you want them installed in image,
# uncomment the following lines and ensure relative paths are correct.
# COPY sdk/python /build/sdk_python
# RUN pip install --no-cache-dir -e /build/sdk_python

# Optional: drop build toolchain to slim image
RUN apt-get purge -y gcc || true && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Runtime environment
ENV HOST=0.0.0.0 \
    PORT=8000 \
    LOG_LEVEL=INFO \
    WORKERS=3 \
    UVICORN_LOG_LEVEL=info

# Expose HTTP port
EXPOSE 8000

# Switch to non-root
USER app

# Entrypoint: Gunicorn + Uvicorn worker
# Note: DB migrations are NOT auto-run here; invoke:
#   docker run ... studio-services python -m studio_services.cli migrate
CMD gunicorn "studio_services.main:app" \
    -k uvicorn.workers.UvicornWorker \
    -w ${WORKERS:-3} \
    -b 0.0.0.0:${PORT:-8000} \
    --access-logfile - \
    --error-logfile - \
    --timeout 60

# Example healthcheck (enable if desired)
# HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
#   CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/healthz" || exit 1
