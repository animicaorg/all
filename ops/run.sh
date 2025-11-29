#!/usr/bin/env bash
set -euo pipefail

print_usage() {
  cat <<'USAGE'
Usage: ops/run.sh [--profile PROFILE] <node|pool|dashboard|all>

Subcommands:
  node        Start the Animica node RPC server for the selected profile.
  pool        Run the Stratum mining pool backend.
  dashboard   Launch the miner dashboard (Vite dev server).
  all         Print instructions to run all services together.

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

start_node() {
  echo "[animica] Starting node (profile=${PROFILE})"
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
  export ANIMICA_MINING_POOL_LOG_LEVEL="${ANIMICA_MINING_POOL_LOG_LEVEL:-info}"
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
  api_bind="${ANIMICA_POOL_API_BIND:-127.0.0.1:8550}"
  api_host="${api_bind%%:*}"
  api_port="${api_bind##*:}"
  if [[ "${api_host}" == "0.0.0.0" ]]; then
    api_host="127.0.0.1"
  fi
  export VITE_STRATUM_API_URL="${VITE_STRATUM_API_URL:-http://${api_host}:${api_port}}"
  pnpm dev -- --host
}

print_all() {
  cat <<INSTRUCTIONS
[animica] Launch all services for profile=${PROFILE}
1) Terminal A: ops/run.sh --profile ${PROFILE} node
2) Terminal B: ops/run.sh --profile ${PROFILE} pool
3) Terminal C: ops/run.sh --profile ${PROFILE} dashboard
INSTRUCTIONS
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
    print_all
    ;;
  *)
    print_usage
    exit 1
    ;;
esac
