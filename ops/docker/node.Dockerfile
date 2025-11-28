# ------------------------------------------------------------------------------
# Animica Python Node Image
# - Python 3.11 slim
# - uvloop, msgspec
# - optional RocksDB (python-rocksdb; librocksdb runtime)
# - FastAPI/uvicorn ready
#
# Multi-stage: build wheels (incl. python-rocksdb) then install into a slim
# runtime that only includes the RocksDB shared library and minimal tools.
# ------------------------------------------------------------------------------

ARG PYTHON_VERSION=3.11
ARG DEBIAN_FRONTEND=noninteractive

# ----- builder: compile wheels (rocksdb needs headers) -------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

# Base OS deps for building native wheels (rocksdb, uvloop, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git build-essential cmake pkg-config \
    librocksdb-dev libsnappy-dev zlib1g-dev libbz2-dev liblz4-dev libzstd-dev \
 && rm -rf /var/lib/apt/lists/*

# Upgrade pip tooling and pre-build wheels into /wheels.
RUN python -m pip install --upgrade pip setuptools wheel

# Build wheels we want to vendor into the runtime. We include an extended set of
# deps commonly used by the node (FastAPI/uvicorn, pydantic, websockets, etc.).
# If python-rocksdb fails to build on some architectures, we let it fail open;
# the node will gracefully disable the RocksDB backend at runtime.
RUN set -eux; \
    mkdir -p /wheels; \
    python -m pip wheel --wheel-dir=/wheels \
      uvloop \
      msgspec \
      fastapi \
      "uvicorn[standard]" \
      "pydantic>=2" \
      websockets \
      anyio \
      blake3 \
      cbor2 \
      msgpack \
      pycryptodomex \
      httpx \
      requests \
      rich \
      typer \
      prometheus-client \
      python-rocksdb || echo "python-rocksdb build failed (optional)"; \
    ls -l /wheels

# ----- runtime: minimal system libs + Python deps ------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install only runtime libraries (no build tools). We include librocksdb for the
# python-rocksdb wheel we built in the previous stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tini \
    librocksdb-dev libsnappy1v5 zlib1g libbz2-1.0 liblz4-1 libzstd1 \
 && rm -rf /var/lib/apt/lists/*

# Install prebuilt wheels
COPY --from=builder /wheels /wheels
RUN set -eux; \
    python -m pip install --no-index --find-links=/wheels \
      uvloop \
      msgspec \
      fastapi \
      "uvicorn[standard]" \
      "pydantic>=2" \
      websockets \
      anyio \
      blake3 \
      cbor2 \
      msgpack \
      pycryptodomex \
      httpx \
      requests \
      rich \
      typer \
      prometheus-client \
      python-rocksdb || echo "python-rocksdb not installed (optional)"; \
    rm -rf /wheels

# Create non-root user & runtime dirs
ARG USER=animica
ARG UID=10001
ARG GID=10001
RUN groupadd -g "${GID}" "${USER}" \
 && useradd -m -u "${UID}" -g "${GID}" -s /usr/sbin/nologin "${USER}" \
 && mkdir -p /app /data /var/log/animica \
 && chown -R "${USER}:${USER}" /app /data /var/log/animica

WORKDIR /app

# Copy repository sources (expecting repo root as build context).
# If you build only subpackages, adjust to COPY the relevant dirs.
COPY . /app

# Run as non-root from here on.
USER ${USER}

# Export common ports:
# - 8545: JSON-RPC HTTP
# - 8546: WebSocket
# - 8080/8081: auxiliary services (studio-services / DA)
EXPOSE 8545 8546 8080 8081

# Healthcheck (best-effort). The RPC server should expose /healthz when mounted.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8545/healthz || exit 1

# OCI labels (optional)
ARG VERSION=0.0.0+local
ARG VCS_REF=unknown
ARG BUILD_DATE
LABEL org.opencontainers.image.title="animica-node" \
      org.opencontainers.image.description="Animica Python node (uvloop/msgspec/rocksdb)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0"

# tini as PID 1 for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]

# By default start the RPC server; override with:
#   docker run ... -- python -m core.boot --genesis core/genesis/genesis.json ...
CMD ["python", "-m", "rpc.server"]
