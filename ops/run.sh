#!/usr/bin/env bash
set -euo pipefail

print_usage() {
  cat <<'USAGE'
Usage: ops/run.sh [--profile PROFILE] <node|pool|dashboard|all>

Subcommands:
  node        Start the Animica node RPC server for the selected profile.
  pool        Run the Stratum mining pool backend.
  dashboard   Launch the miner dashboard (Vite dev server).
  all         Start node + pool in the background, then launch the dashboard.

Options:
  --profile PROFILE   Choose devnet, testnet, or mainnet (default: mainnet).
USAGE
}

# ------------------------------
# Profile / command parsing
# ------------------------------

PROFILE="mainnet"
COMMAND=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      COMMAND="$1"
      shift
      break
      ;;
  esac
done

if [[ -z "${COMMAND}" ]]; then
  print_usage
  exit 1
fi

case "$PROFILE" in
  devnet|testnet|mainnet) ;;
  *)
    echo "Unknown profile: ${PROFILE}" >&2
    exit 1
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROFILE_PATH="${REPO_ROOT}/ops/profiles/${PROFILE}.env"

# Make in-repo Python packages importable
export PYTHONPATH="${REPO_ROOT}/python${PYTHONPATH:+:${PYTHONPATH}}"

if [[ ! -f "${PROFILE_PATH}" ]]; then
  echo "Missing profile file: ${PROFILE_PATH}" >&2
  exit 1
fi

# ------------------------------
# Load profile env
# ------------------------------

set -a
# shellcheck disable=SC1090
source "${PROFILE_PATH}"
set +a

# ------------------------------
# Normalize network + chain id + DBs
# ------------------------------
#
#  - ANIMICA_NETWORK follows PROFILE unless explicitly set.
#  - ANIMICA_CHAIN_ID default depends on PROFILE:
#        devnet  -> 1337
#        testnet -> 2
#        mainnet -> 1
#  - DBs are profile-specific by default:
#        ~/.animica/<profile>/chain.db
#        ~/.animica/<profile>/pool.db
#

if [[ -z "${ANIMICA_NETWORK:-}" ]]; then
  export ANIMICA_NETWORK="${PROFILE}"
fi

# Chain id defaults by profile, but allow explicit override.
if [[ -z "${ANIMICA_CHAIN_ID:-}" ]]; then
  case "${PROFILE}" in
    devnet)
      export ANIMICA_CHAIN_ID=1337
      ;;
    testnet)
      export ANIMICA_CHAIN_ID=2
      ;;
    mainnet)
      export ANIMICA_CHAIN_ID=1
      ;;
  esac
fi

# Default RPC URL if not set in profile
export ANIMICA_RPC_URL="${ANIMICA_RPC_URL:-http://127.0.0.1:8545/rpc}"

# Use XDG-ish home by default
ANIMICA_DATA_ROOT="${ANIMICA_DATA_ROOT:-$HOME/.animica}"

# Per-network DB roots (chain-<id>) with migration from legacy profile-based layout
CHAIN_DB_PATH="${ANIMICA_DATA_ROOT}/chain-${ANIMICA_CHAIN_ID}/animica.db"
POOL_DB_PATH="${ANIMICA_DATA_ROOT}/chain-${ANIMICA_CHAIN_ID}/pool.db"
LEGACY_PROFILE_DIR="${ANIMICA_DATA_ROOT}/${PROFILE}"
LEGACY_CHAIN_DB="${LEGACY_PROFILE_DIR}/chain.db"
LEGACY_POOL_DB="${LEGACY_PROFILE_DIR}/pool.db"

migrate_legacy_db() {
  local source="$1" target="$2"
  if [[ -f "${target}" || ! -f "${source}" ]]; then
    return
  fi
  mkdir -p "$(dirname "${target}")"
  cp -p "${source}" "${target}"
  echo "[animica] Migrated legacy DB ${source} -> ${target}"
}

migrate_legacy_db "${LEGACY_CHAIN_DB}" "${CHAIN_DB_PATH}"
migrate_legacy_db "${LEGACY_POOL_DB}" "${POOL_DB_PATH}"

export ANIMICA_RPC_DB_URI="${ANIMICA_RPC_DB_URI:-sqlite:///${CHAIN_DB_PATH}}"
export ANIMICA_MINING_POOL_DB_URL="${ANIMICA_MINING_POOL_DB_URL:-sqlite:///${POOL_DB_PATH}}"

# Stratum + pool API binds
export ANIMICA_STRATUM_BIND="${ANIMICA_STRATUM_BIND:-0.0.0.0:3333}"
export ANIMICA_POOL_API_BIND="${ANIMICA_POOL_API_BIND:-0.0.0.0:8550}"

split_bind() {
  local bind host port
  bind="$1"
  host="${bind%%:*}"
  port="${bind##*:}"
  echo "${host}" "${port}"
}

set_bind_env() {
  read -r stratum_host stratum_port < <(split_bind "${ANIMICA_STRATUM_BIND}")
  export ANIMICA_STRATUM_HOST="${ANIMICA_STRATUM_HOST:-${stratum_host}}"
  export ANIMICA_STRATUM_PORT="${ANIMICA_STRATUM_PORT:-${stratum_port}}"

  read -r api_host api_port < <(split_bind "${ANIMICA_POOL_API_BIND}")
  export ANIMICA_POOL_API_HOST="${ANIMICA_POOL_API_HOST:-${api_host}}"
  export ANIMICA_POOL_API_PORT="${ANIMICA_POOL_API_PORT:-${api_port}}"
}

set_bind_env

# ------------------------------
# P2P seeds for the selected profile
# ------------------------------

load_p2p_seeds() {
  if [[ -n "${ANIMICA_P2P_SEEDS:-}" ]]; then
    echo "[animica] Using P2P seeds from environment"
    return
  fi

  local seed_csv
  seed_csv=$(cd "${REPO_ROOT}" && python -m ops.seeds.profile_loader --profile "${PROFILE}" --write-peerstore 2>/dev/null || true)
  if [[ -n "${seed_csv}" ]]; then
    export ANIMICA_P2P_SEEDS="${seed_csv}"
    echo "[animica] Loaded ${PROFILE} seeds (${seed_csv//,/, })"
  else
    echo "[animica] No profile seeds found for ${PROFILE}; falling back to defaults"
  fi
}

# ------------------------------
# RPC helpers
# ------------------------------

parse_rpc_from_url() {
  python - <<'PY'
import os
from urllib.parse import urlparse

url = os.getenv("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc")
parsed = urlparse(url)
print(parsed.hostname or "127.0.0.1")
print(parsed.port or 8545)
print(parsed.path or "/rpc")
PY
}

rpc_health_check() {
  local rpc_url="$1"
  python - "${rpc_url}" <<'PY'
import json
import sys
import urllib.error
import urllib.request

url = sys.argv[1]
payload = json.dumps(
    {"jsonrpc": "2.0", "id": 1, "method": "net_version", "params": []}
).encode()
req = urllib.request.Request(
    url, data=payload, headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req, timeout=2) as resp:
        json.load(resp)
    sys.exit(0)
except (urllib.error.URLError, urllib.error.HTTPError, OSError):
    sys.exit(1)
PY
}

wait_for_rpc() {
  local rpc_url="$1"
  local timeout_s="${2:-10}"
  local start_ts
  start_ts="$(date +%s)"
  while true; do
    if rpc_health_check "${rpc_url}"; then
      return 0
    fi
    if (( $(date +%s) - start_ts >= timeout_s )); then
      return 1
    fi
    sleep 1
  done
}

is_local_rpc_host() {
  read -r rpc_host _ _ < <(parse_rpc_from_url)
  case "${rpc_host}" in
    ""|localhost|127.*|0.0.0.0)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

# Start a local node for the pool if the configured RPC is local and unreachable.
AUTO_NODE_PID=""
cleanup_auto_node() {
  if [[ -n "${AUTO_NODE_PID}" ]]; then
    echo "[animica] Stopping auto-started node (pid=${AUTO_NODE_PID})"
    kill "${AUTO_NODE_PID}" 2>/dev/null || true
  fi
}

ensure_pool_rpc() {
  local rpc_url="${ANIMICA_RPC_URL:-http://127.0.0.1:8545/rpc}"
  local wait_seconds="${ANIMICA_POOL_RPC_WAIT_SECONDS:-8}"
  local local_grace="${ANIMICA_POOL_RPC_LOCAL_GRACE:-15}"

  if wait_for_rpc "${rpc_url}" "${wait_seconds}"; then
    return 0
  fi

  if is_local_rpc_host; then
    # When the node is starting in another process (e.g., `run.sh all`), give it
    # a bit more time to come up before auto-starting a new one.
    if wait_for_rpc "${rpc_url}" "${local_grace}"; then
      return 0
    fi

    echo "[animica] RPC ${rpc_url} unreachable; auto-starting local node for the pool"
    start_node &
    AUTO_NODE_PID=$!
    trap cleanup_auto_node EXIT INT TERM

    # Give the node a moment to come up before starting the pool.
    if ! wait_for_rpc "${rpc_url}" "${ANIMICA_POOL_RPC_WAIT_SECONDS:-20}"; then
      echo "[animica] RPC still unreachable after auto-start; check node logs" >&2
    fi
  else
    echo "[animica] RPC ${rpc_url} is not reachable. Set ANIMICA_RPC_URL to a running node or use 'ops/run.sh all'." >&2
  fi
}

# ------------------------------
# Pool profile detection
# ------------------------------

detect_pool_profile() {
  if [[ -n "${ANIMICA_POOL_PROFILE:-}" ]]; then
    echo "[animica] Using pool profile from environment: ${ANIMICA_POOL_PROFILE}"
    return
  fi

  local rpc_url
  rpc_url="${ANIMICA_RPC_URL:-http://127.0.0.1:8545/rpc}"
  local inferred
  inferred=$(python - "${rpc_url}" <<'PY' || true
import json
import sys
import urllib.request

rpc_url = sys.argv[1]
payload = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "miner.get_sha256_job",
    "params": [{"address": ""}],
}).encode()

try:
    req = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        data = json.load(resp)
    if isinstance(data, dict) and "result" in data:
        print("asic_sha256")
        sys.exit(0)
except Exception:
    pass

print("hashshare")
PY
)

  if [[ -z "${inferred}" ]]; then
    inferred="hashshare"
  fi

  export ANIMICA_POOL_PROFILE="${inferred}"
  echo "[animica] Auto-selected pool profile: ${ANIMICA_POOL_PROFILE}"
}

# ------------------------------
# Start commands
# ------------------------------

start_node() {
  echo "[animica] Starting node (profile=${PROFILE}, network=${ANIMICA_NETWORK}, chain_id=${ANIMICA_CHAIN_ID})"
  echo "[animica] RPC DB: ${ANIMICA_RPC_DB_URI}"
  load_p2p_seeds
  read -r rpc_host rpc_port rpc_path < <(parse_rpc_from_url)
  export ANIMICA_RPC_HOST="${ANIMICA_RPC_HOST:-${rpc_host}}"
  export ANIMICA_RPC_PORT="${ANIMICA_RPC_PORT:-${rpc_port}}"
  export ANIMICA_RPC_WS_PATH="${ANIMICA_RPC_WS_PATH:-${rpc_path}}"
  cd "${REPO_ROOT}" || exit 1
  if [[ -d .venv ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  fi
  python -m rpc.server
}

start_pool() {
  echo "[animica] Starting Stratum pool (profile=${PROFILE}, network=${ANIMICA_NETWORK}, chain_id=${ANIMICA_CHAIN_ID})"
  echo "[animica] Pool DB: ${ANIMICA_MINING_POOL_DB_URL}"
  cd "${REPO_ROOT}" || exit 1
  if [[ -d .venv ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
  else
    echo "Warning: .venv not found; ensure dependencies are installed" >&2
  fi
  detect_pool_profile
  export ANIMICA_MINING_POOL_LOG_LEVEL="${ANIMICA_MINING_POOL_LOG_LEVEL:-info}"
  read -r stratum_host stratum_port < <(split_bind "${ANIMICA_STRATUM_BIND}")
  export ANIMICA_STRATUM_HOST="${ANIMICA_STRATUM_HOST:-${stratum_host}}"
  export ANIMICA_STRATUM_PORT="${ANIMICA_STRATUM_PORT:-${stratum_port}}"
  read -r api_host api_port < <(split_bind "${ANIMICA_POOL_API_BIND}")
  export ANIMICA_POOL_API_HOST="${ANIMICA_POOL_API_HOST:-${api_host}}"
  export ANIMICA_POOL_API_PORT="${ANIMICA_POOL_API_PORT:-${api_port}}"
  ensure_pool_rpc
  python -m animica.stratum_pool
}

start_dashboard() {
  echo "[animica] Starting miner dashboard (profile=${PROFILE}, network=${ANIMICA_NETWORK}, chain_id=${ANIMICA_CHAIN_ID})"
  cd "${REPO_ROOT}" || exit 1
  if [[ ! -d node_modules ]]; then
    pnpm install
  fi
  cd apps/miner-dashboard || exit 1
  if [[ ! -d node_modules ]]; then
    pnpm install
  fi
  api_host="${ANIMICA_POOL_API_HOST:-127.0.0.1}"
  api_port="${ANIMICA_POOL_API_PORT:-8550}"
  if [[ "${api_host}" == "0.0.0.0" ]]; then
    api_host="127.0.0.1"
  fi
  export VITE_STRATUM_API_URL="${VITE_STRATUM_API_URL:-http://${api_host}:${api_port}}"
  pnpm dev -- --host
}

start_all() {
  echo "[animica] Launching node + pool in background for profile=${PROFILE}, network=${ANIMICA_NETWORK}, chain_id=${ANIMICA_CHAIN_ID}"
  start_node &
  NODE_PID=$!
  start_pool &
  POOL_PID=$!

  cleanup() {
    echo "[animica] Shutting down background services"
    kill "${NODE_PID}" "${POOL_PID}" 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM

  start_dashboard

  wait "${NODE_PID}" "${POOL_PID}" || true
}

# ------------------------------
# Dispatch
# ------------------------------

case "$COMMAND" in
  node)
    start_node
    ;;
  pool)
    start_pool
    ;;
  dashboard)
    start_dashboard
    ;;
  all)
    start_all
    ;;
  *)
    print_usage
    exit 1
    ;;
esac
