#!/usr/bin/env bash
# dev/mine-gpu.sh - Mine with GPU using useful work
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration
RPC_URL="${RPC_URL:-http://127.0.0.1:8545}"
POOL_URL="${POOL_URL:-stratum+tcp://127.0.0.1:3333}"
MINER_ADDRESS="${MINER_ADDRESS:-}"
WORK_TYPE="${WORK_TYPE:-hash_work}"

echo "=== GPU Mining (Useful Work) ==="
echo "RPC: $RPC_URL"
echo "Pool: $POOL_URL"
echo "Work Type: $WORK_TYPE"
echo ""

# Activate venv if present
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate"
fi

# Start GPU miner (stratum client mode)
cd "$ROOT_DIR"
echo "Starting GPU miner (stratum client)..."
ANIMICA_MINER_WORK_TYPE="$WORK_TYPE" python -m mining.cli.stratum_client \
    --pool "$POOL_URL" \
    --device gpu \
    ${MINER_ADDRESS:+--address "$MINER_ADDRESS"} \
    "$@"
