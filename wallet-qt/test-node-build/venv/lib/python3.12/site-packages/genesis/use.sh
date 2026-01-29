#!/usr/bin/env bash
# Copy the appropriate sample genesis file into core/genesis/genesis.json.
# Usage:
#   bash genesis/use.sh devnet|testnet|mainnet
#   DEST_GENESIS_PATH=/custom/path bash genesis/use.sh devnet
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: genesis/use.sh <devnet|testnet|mainnet>

Copies the matching genesis.sample.<network>.json file into core/genesis/genesis.json
so that local runs start from the right chain defaults. Set DEST_GENESIS_PATH to
override the destination path (defaults to core/genesis/genesis.json).
USAGE
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

NETWORK="$1"
case "$NETWORK" in
  devnet|testnet|mainnet) ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown network: ${NETWORK}" >&2
    usage
    exit 1
    ;;
 esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC="${SCRIPT_DIR}/genesis.sample.${NETWORK}.json"
DEST="${DEST_GENESIS_PATH:-${REPO_ROOT}/core/genesis/genesis.json}"

if [[ ! -f "${SRC}" ]]; then
  echo "Sample genesis not found for ${NETWORK}: ${SRC}" >&2
  exit 1
fi

mkdir -p "$(dirname "${DEST}")"
cp -f "${SRC}" "${DEST}"

echo "[animica] Installed ${NETWORK} genesis → ${DEST}"
