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

WORKDIR /app
COPY python /app/python
COPY pq /app/pq
# Sibling Python packages that python/pyproject.toml force-includes into
# the animica wheel. Without these, `pip wheel /app/python` fails with
# FileNotFoundError because hatchling resolves the force-include paths
# relative to /app/python's pyproject.toml location.
COPY aicf /app/aicf
COPY capabilities /app/capabilities
COPY consensus /app/consensus
COPY core /app/core
COPY coretx /app/coretx
COPY da /app/da
COPY execution /app/execution
COPY mempool /app/mempool
COPY mempool2 /app/mempool2
COPY mining /app/mining
COPY p2p /app/p2p
COPY rpc /app/rpc
COPY stdlib /app/stdlib
COPY vm_py /app/vm_py
# AI stack: only the small subdirs the wheel actually bundles. ai/runs,
# ai/data, ai/models are excluded by .dockerignore so we don't ship
# training artifacts into the image.
COPY ai /app/ai
# Standalone compose files bundled into the wheel (for pip-only users
# whose nodes need `animica node up` without a source checkout).
COPY ops/docker/standalone /app/ops/docker/standalone

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
      pyyaml \
      prometheus-client; \
    python -m pip wheel --wheel-dir=/wheels /app/python /app/pq; \
    python -m pip wheel --wheel-dir=/wheels python-rocksdb \
      || echo "python-rocksdb build failed (optional)"; \
    ls -l /wheels

# ----- runtime: minimal system libs + Python deps ------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install only runtime libraries (no build tools). We include librocksdb for the
# python-rocksdb wheel we built in the previous stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gosu tini \
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
      pyyaml \
      prometheus-client \
      animica \
      animica-pq; \
    python -m pip install --no-index --find-links=/wheels python-rocksdb \
      || echo "python-rocksdb not installed (optional)"; \
    rm -rf /wheels

# EVM execution lane (optional; runtime-gated by ANIMICA_EVM_EXECUTION): a real
# py-evm so Solidity contracts deploy and run on the node-local EVM sequencer
# (chain id 149). Fetched from PyPI (not part of the offline wheel set);
# fail-open — if it's absent the lane simply stays disabled.
RUN python -m pip install "py-evm>=0.10.1b1" \
      || echo "py-evm not installed (EVM execution lane disabled)"

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
COPY --chown=${USER}:${USER} . /app

ENV ANIMICA_USER=${USER} \
    ANIMICA_UID=${UID} \
    ANIMICA_GID=${GID} \
    HOME=/data

RUN python /app/ops/docker/scripts/pq_backend_selftest.py

# Switch to non-root user
USER ${USER}

# Export common ports:
# - 8545: JSON-RPC HTTP
# - 8546: WebSocket
# - 8080/8081: auxiliary services (studio-services / DA)
EXPOSE 8545 8546 8080 8081

# Healthcheck (best-effort). The RPC server should expose /healthz when mounted.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8545/rpc -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":{}}' >/dev/null || exit 1

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
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/app/ops/docker/entrypoints/entrypoint.sh"]

# By default start the RPC server; override with:
#   docker run ... -- python -m core.boot --genesis core/genesis/genesis.json ...
CMD ["python", "-m", "rpc.server"]
