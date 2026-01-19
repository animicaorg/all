#!/usr/bin/env bash
# dev/run-node.sh - Start an Animica node for development/testing
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration
CHAIN_ID="${CHAIN_ID:-1337}"
NETWORK="${NETWORK:-devnet}"
DATA_DIR="${DATA_DIR:-$ROOT_DIR/chain-$CHAIN_ID}"
RPC_PORT="${RPC_PORT:-8545}"
P2P_PORT="${P2P_PORT:-30303}"

echo "=== Starting Animica Node ==="
echo "Network: $NETWORK"
echo "Chain ID: $CHAIN_ID"
echo "Data Dir: $DATA_DIR"
echo "RPC Port: $RPC_PORT"
echo "P2P Port: $P2P_PORT"
echo ""

# Ensure data directory exists
mkdir -p "$DATA_DIR"

# Activate venv if present
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate"
fi

# Start node via CLI
cd "$ROOT_DIR"
exec python -m python.animica.cli.node \
    --chain-id "$CHAIN_ID" \
    --data-dir "$DATA_DIR" \
    --rpc-port "$RPC_PORT" \
    --p2p-port "$P2P_PORT" \
    --network "$NETWORK" \
    "$@"
