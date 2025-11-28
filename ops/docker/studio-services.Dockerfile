# ------------------------------------------------------------------------------
# Animica Studio Services Image
# FastAPI proxy for deploy/verify/faucet/artifacts (no server-side signing).
#
# - Python 3.11 slim
# - FastAPI + uvicorn
# - msgspec, pydantic v2, httpx/requests
# - SQLite for metadata/queues, filesystem/S3 (optional) for artifacts
# - Non-root, healthcheck, tini as PID1
#
# Environment (override at runtime):
#   SERVICES_PORT=8090
#   RPC_URL=http://node:8545/rpc
#   CHAIN_ID=1
#   CORS_ALLOW_ORIGINS=*
#   RATE_LIMITS=default
#   STORAGE_DIR=/data/artifacts
#   FAUCET_KEY=        # optional; empty disables faucet routes that require hot key
# ------------------------------------------------------------------------------

ARG PYTHON_VERSION=3.11
ARG DEBIAN_FRONTEND=noninteractive

# ----- builder: compile/download wheels (uvicorn[standard] pulls httptools/uvloop)
FROM python:${PYTHON_VERSION}-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl build-essential \
  && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip setuptools wheel

RUN set -eux; \
    mkdir -p /wheels; \
    python -m pip wheel --wheel-dir=/wheels \
      fastapi \
      "uvicorn[standard]" \
      "pydantic>=2" \
      msgspec \
      httpx \
      requests \
      python-multipart \
      itsdangerous \
      prometheus-client \
      cachetools \
      orjson; \
    ls -l /wheels

# ----- runtime: slim image consuming pre-built wheels only
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SERVICES_PORT=8090 \
    RPC_URL=http://node:8545/rpc \
    CHAIN_ID=1 \
    CORS_ALLOW_ORIGINS="*" \
    RATE_LIMITS=default \
    STORAGE_DIR=/data/artifacts

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tini \
  && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-index --find-links=/wheels \
      fastapi \
      "uvicorn[standard]" \
      "pydantic>=2" \
      msgspec \
      httpx \
      requests \
      python-multipart \
      itsdangerous \
      prometheus-client \
      cachetools \
      orjson \
  && rm -rf /wheels

# Create non-root user and dirs
ARG USER=animica
ARG UID=10004
ARG GID=10004
RUN groupadd -g "${GID}" "${USER}" \
 && useradd -m -u "${UID}" -g "${GID}" -s /usr/sbin/nologin "${USER}" \
 && mkdir -p /app /data /var/log/animica \
 && chown -R "${USER}:${USER}" /app /data /var/log/animica

WORKDIR /app

# Copy the repo (expects studio_services/ package present at this path)
COPY . /app

# Ensure runtime can write storage dir by default
RUN chown -R "${USER}:${USER}" /data

USER ${USER}

EXPOSE 8090

# Launch wrapper: exports env and starts uvicorn
RUN printf '%s\n' '#!/usr/bin/env sh' \
  'set -eu' \
  ': "${SERVICES_PORT:=8090}"' \
  ': "${RPC_URL:=http://node:8545/rpc}"' \
  ': "${CHAIN_ID:=1}"' \
  ': "${CORS_ALLOW_ORIGINS:=*}"' \
  ': "${RATE_LIMITS:=default}"' \
  ': "${STORAGE_DIR:=/data/artifacts}"' \
  'export SERVICES_PORT RPC_URL CHAIN_ID CORS_ALLOW_ORIGINS RATE_LIMITS STORAGE_DIR FAUCET_KEY' \
  'exec uvicorn studio_services.main:app --host 0.0.0.0 --port "${SERVICES_PORT}"' \
  > /usr/local/bin/start-services && chmod +x /usr/local/bin/start-services

# Healthcheck calls /healthz (provided by studio_services)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS "http://127.0.0.1:${SERVICES_PORT}/healthz" || exit 1

# OCI labels
ARG VERSION=0.0.0+local
ARG VCS_REF=unknown
ARG BUILD_DATE
LABEL org.opencontainers.image.title="animica-studio-services" \
      org.opencontainers.image.description="FastAPI proxy for deploy/verify/faucet/artifacts (no server-side signing)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0"

ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["/usr/local/bin/start-services"]
