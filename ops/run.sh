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
  --profile PROFILE   Choose devnet, testnet, or mainnet (default: devnet).
USAGE
}

PROFILE="devnet"
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

if [[ ! -f "${PROFILE_PATH}" ]]; then
  echo "Missing profile file: ${PROFILE_PATH}" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${PROFILE_PATH}"
set +a

# Normalize well-known environment variables so downstream CLIs see the same
# values whether they came from the profile file or ad-hoc overrides.
export ANIMICA_NETWORK="${ANIMICA_NETWORK:-${PROFILE}}"
export ANIMICA_RPC_URL="${ANIMICA_RPC_URL:-http://127.0.0.1:8545/rpc}"
export ANIMICA_RPC_DB_URI="${ANIMICA_RPC_DB_URI:-sqlite:///~/animica/${PROFILE}/chain.db}"
export ANIMICA_MINING_POOL_DB_URL="${ANIMICA_MINING_POOL_DB_URL:-sqlite:///~/animica/${PROFILE}/pool.db}"
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
payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "miner.get_sha256_job", "params": [{"address": ""}]}).encode()
try:
    req = urllib.request.Request(rpc_url, data=payload, headers={"Content-Type": "application/json"})
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

start_node() {
  echo "[animica] Starting node (profile=${PROFILE})"
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
  echo "[animica] Starting Stratum pool (profile=${PROFILE})"
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
  python -m animica.stratum_pool
}

start_dashboard() {
  echo "[animica] Starting miner dashboard (profile=${PROFILE})"
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
  echo "[animica] Launching node + pool in background for profile=${PROFILE}"
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
