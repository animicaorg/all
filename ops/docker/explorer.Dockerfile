# ------------------------------------------------------------------------------
# Animica Explorer API Image (lightweight aggregations for explorer-web)
# - Python 3.11 slim
# - FastAPI + uvicorn, msgspec, httpx, websockets, prometheus-client
# - Non-root runtime, healthcheck, tiny footprint
#
# Environment (override at docker run / compose):
#   EXPLORER_PORT=8085
#   RPC_HTTP_URL=http://rpc:8545/rpc
#   RPC_WS_URL=ws://rpc:8546/ws
#   CHAIN_ID=1
#   CORS_ALLOW_ORIGINS=https://explorer.local,https://studio.local
# ------------------------------------------------------------------------------

ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

ARG DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EXPLORER_PORT=8085 \
    RPC_HTTP_URL=http://rpc:8545/rpc \
    RPC_WS_URL=ws://rpc:8546/ws \
    CHAIN_ID=1 \
    CORS_ALLOW_ORIGINS=*

# System bits: certs, curl for healthcheck, tini for signal handling
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tini \
  && rm -rf /var/lib/apt/lists/*

# Python deps
RUN python -m pip install --upgrade pip setuptools wheel \
 && python -m pip install \
      fastapi \
      "uvicorn[standard]" \
      "pydantic>=2" \
      msgspec \
      httpx \
      websockets \
      blake3 \
      pycryptodomex \
      prometheus-client \
      cachetools

# App user & dirs
ARG USER=animica
ARG UID=10003
ARG GID=10003
RUN groupadd -g "${GID}" "${USER}" \
 && useradd -m -u "${UID}" -g "${GID}" -s /usr/sbin/nologin "${USER}" \
 && mkdir -p /app /var/log/animica \
 && chown -R "${USER}:${USER}" /app /var/log/animica

WORKDIR /app

# Copy repo (expect explorer API module under explorer_api/ in the repo)
# If your code lives elsewhere, adjust this COPY or multi-stage build as needed.
COPY . /app

USER ${USER}

EXPOSE 8085

# Simple bootstrap to pass env → app config and launch uvicorn
RUN printf '%s\n' '#!/usr/bin/env sh' \
  'set -eu' \
  ': "${EXPLORER_PORT:=8085}"' \
  ': "${RPC_HTTP_URL:=http://rpc:8545/rpc}"' \
  ': "${RPC_WS_URL:=ws://rpc:8546/ws}"' \
  ': "${CHAIN_ID:=1}"' \
  ': "${CORS_ALLOW_ORIGINS:=*}"' \
  'export EXPLORER_PORT RPC_HTTP_URL RPC_WS_URL CHAIN_ID CORS_ALLOW_ORIGINS' \
  'exec uvicorn explorer_api.app:app --host 0.0.0.0 --port "${EXPLORER_PORT}"' \
  > /usr/local/bin/start-explorer && chmod +x /usr/local/bin/start-explorer

# Healthcheck hits /healthz (the app should expose it)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS "http://127.0.0.1:${EXPLORER_PORT}/healthz" || exit 1

# OCI labels
ARG VERSION=0.0.0+local
ARG VCS_REF=unknown
ARG BUILD_DATE
LABEL org.opencontainers.image.title="animica-explorer-api" \
      org.opencontainers.image.description="Lightweight Explorer API for Animica (FastAPI)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0"

ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["/usr/local/bin/start-explorer"]
