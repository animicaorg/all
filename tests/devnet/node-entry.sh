#!/usr/bin/env bash
# Entrypoint for devnet nodes (node1/node2).
#
# Responsibilities:
#   - Validate required configuration (genesis path, DB URI, ports).
#   - Initialize the chain DB from genesis on first run.
#   - Start the RPC/WS server as the animica user and keep the container alive.

set -euo pipefail

run_as_animica() {
  # If we’re already the animica user, just run the command
  if [ "$(id -un 2>/dev/null)" = "animica" ] || [ "$(id -u)" -eq 10001 ]; then
    "$@"
    return
  fi

  # Otherwise, drop to the animica user using su
  # (gosu is not installed in this image, so don’t rely on it)
  su -s /bin/sh animica -c "$*"
}

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
  echo "[$(timestamp)] [node-entry] $*"
}

fatal() {
  log "ERROR: $*" >&2
  exit 1
}

ROLE="${1:-node}"
ANIMICA_CHAIN_ID="${ANIMICA_CHAIN_ID:-1337}"
ANIMICA_RPC_HOST="${ANIMICA_RPC_HOST:-0.0.0.0}"
ANIMICA_RPC_PORT="${ANIMICA_RPC_PORT:-8545}"
ANIMICA_RPC_DB_URI="${ANIMICA_RPC_DB_URI:-sqlite:////data/animica.db}"
ANIMICA_RPC_CORS_ORIGINS="${ANIMICA_RPC_CORS_ORIGINS:-[*]}"
ANIMICA_LOG_LEVEL="${ANIMICA_LOG_LEVEL:-INFO}"
GENESIS_PATH_RAW="${GENESIS_PATH:-core/genesis/genesis.json}"

# Resolve genesis path relative to the repo root inside the container (/app)
if [[ "${GENESIS_PATH_RAW}" == /* ]]; then
  GENESIS_PATH_RESOLVED="${GENESIS_PATH_RAW}"
else
  GENESIS_PATH_RESOLVED="/app/${GENESIS_PATH_RAW#./}"
fi

[[ -f "${GENESIS_PATH_RESOLVED}" ]] || fatal "GENESIS_PATH not found: ${GENESIS_PATH_RESOLVED} (set GENESIS_PATH env var)"

log "Starting ${ROLE} with chainId=${ANIMICA_CHAIN_ID}, host=${ANIMICA_RPC_HOST}, port=${ANIMICA_RPC_PORT}, db=${ANIMICA_RPC_DB_URI}" \
  "genesis=${GENESIS_PATH_RESOLVED} cors=${ANIMICA_RPC_CORS_ORIGINS} log_level=${ANIMICA_LOG_LEVEL}"

# Ensure data dir ownership and existence
mkdir -p /data
chown -R animica:animica /data

# Initialize the DB once if it doesn't exist
if [[ ! -f /data/animica.db ]]; then
  log "Initializing genesis DB at /data/animica.db"
  run_as_animica "python -m core.boot --genesis '${GENESIS_PATH_RESOLVED}' --db '${ANIMICA_RPC_DB_URI}'"
else
  log "Existing DB detected at /data/animica.db; skipping genesis init"
fi

# Start the RPC/WS server (never return)
log "Launching rpc.server"
exec run_as_animica python -m animica.rpc.server \
  --db "${ANIMICA_RPC_DB_URI}" \
  --genesis "${GENESIS_PATH_RESOLVED}" \
  --chain-id "${ANIMICA_CHAIN_ID}" \
  --host "${ANIMICA_RPC_HOST}" \
  --port "${ANIMICA_RPC_PORT}" \
  --cors "${ANIMICA_RPC_CORS_ORIGINS}" \
  --log-level "${ANIMICA_LOG_LEVEL}"
