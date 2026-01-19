#!/usr/bin/env bash
# dev/mine-quantum.sh - Mine with quantum useful work
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration
RPC_URL="${RPC_URL:-http://127.0.0.1:8545}"
MINER_ADDRESS="${MINER_ADDRESS:-}"
WORK_TYPE="${WORK_TYPE:-quantum}"

echo "=== Quantum Mining (Useful Work) ==="
echo "RPC: $RPC_URL"
echo "Work Type: $WORK_TYPE"
echo ""

# Activate venv if present
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate"
fi

# Start quantum miner
cd "$ROOT_DIR"
echo "Starting quantum miner..."
ANIMICA_MINER_WORK_TYPE="$WORK_TYPE" python -m mining.cli.mine \
    --rpc "$RPC_URL" \
    --threads 1 \
    --device quantum \
    ${MINER_ADDRESS:+--address "$MINER_ADDRESS"} \
    "$@"
