# ------------------------------------------------------------------------------
# Animica Miner Image (CPU backend)
# - Python 3.11 slim
# - numpy + numba (optional JIT acceleration)
# - msgspec, httpx, websockets, uvloop (linux)
# - pure-Python fallbacks remain available if numba not usable at runtime
#
# Default entrypoint runs the built-in miner against an RPC endpoint.
# Configure via env:
#   MINER_RPC_HTTP=http://rpc:8545/rpc
#   MINER_RPC_WS=ws://rpc:8546/ws
#   MINER_CHAIN_ID=1
#   MINER_DEVICE=cpu
#   MINER_THREADS=<auto nproc>
#   MINER_LOG_LEVEL=info
#   MINER_STRATUM_LISTEN= # (optional) e.g. 0.0.0.0:3333 to run stratum server
# ------------------------------------------------------------------------------

ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Reasonable defaults for threaded BLAS/NumPy
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

# Minimal system libs for numpy wheels and sane runtime; tini for PID 1.
# build-essential is required to compile pysha3 when a wheel isn't available.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tini libgomp1 procps build-essential \
  && rm -rf /var/lib/apt/lists/*

# Core Python deps (binary wheels). numba pulls llvmlite wheels.
# If numba wheel is not available for the arch, install will gracefully fail;
# miner will still run on pure-Python or NumPy backend.
RUN python -m pip install --upgrade pip setuptools wheel \
 && python -m pip install \
      numpy \
      "numba>=0.59; platform_machine!='s390x'" \
      uvloop \
      msgspec \
      anyio \
      httpx \
      websockets \
      cbor2 \
      msgpack \
      blake3 \
      pysha3 \
      prometheus-client \
      psutil \
      rich

# App user & dirs
ARG USER=animica
ARG UID=10002
ARG GID=10002
RUN groupadd -g "${GID}" "${USER}" \
 && useradd -m -u "${UID}" -g "${GID}" -s /usr/sbin/nologin "${USER}" \
 && mkdir -p /app /data /var/log/animica \
 && chown -R "${USER}:${USER}" /app /data /var/log/animica

WORKDIR /app
COPY . /app
USER ${USER}

# Optional service ports:
# - Stratum (when enabled via MINER_STRATUM_LISTEN)
EXPOSE 3333

# Small wrapper to translate env → CLI flags so users can `docker run` with envs.
# You can still override CMD with custom args.
RUN printf '%s\n' '#!/usr/bin/env sh' \
  'set -e' \
  ': "${MINER_RPC_HTTP:=http://rpc:8545/rpc}"' \
  ': "${MINER_RPC_WS:=ws://rpc:8546/ws}"' \
  ': "${MINER_CHAIN_ID:=1}"' \
  ': "${MINER_DEVICE:=cpu}"' \
  ': "${MINER_THREADS:=auto}"' \
  ': "${MINER_LOG_LEVEL:=info}"' \
  '' \
  'ARGS="--device ${MINER_DEVICE} --log-level ${MINER_LOG_LEVEL}"' \
  '[ "${MINER_THREADS}" != "auto" ] && ARGS="$ARGS --threads ${MINER_THREADS}"' \
  'if [ -n "${MINER_STRATUM_LISTEN}" ]; then' \
  '  ARGS="$ARGS --stratum-listen ${MINER_STRATUM_LISTEN}"' \
  'fi' \
  'exec python -m mining.cli.miner '"'"'--rpc-http'"'"' "${MINER_RPC_HTTP}" '"'"'--rpc-ws'"'"' "${MINER_RPC_WS}" '"'"'--chain-id'"'"' "${MINER_CHAIN_ID}" ${ARGS}' \
  > /usr/local/bin/start-miner && \
  chmod +x /usr/local/bin/start-miner

# Healthcheck (best-effort): ensure process is alive; if stratum is enabled,
# you can switch this to TCP check on ${MINER_STRATUM_LISTEN}.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD ps -o pid,comm | grep -q "python" || exit 1

# Labels
ARG VERSION=0.0.0+local
ARG VCS_REF=unknown
ARG BUILD_DATE
LABEL org.opencontainers.image.title="animica-miner" \
      org.opencontainers.image.description="Animica built-in CPU miner (NumPy/Numba accelerated)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0"

ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
CMD ["/usr/local/bin/start-miner"]
