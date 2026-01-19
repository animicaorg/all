#!/usr/bin/env bash
# dev/mine-cpu.sh - Mine with CPU using useful work
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration
RPC_URL="${RPC_URL:-http://127.0.0.1:8545}"
MINER_ADDRESS="${MINER_ADDRESS:-}"
WORK_TYPE="${WORK_TYPE:-hash_work}"

echo "=== CPU Mining (Useful Work) ==="
echo "RPC: $RPC_URL"
echo "Work Type: $WORK_TYPE"
echo ""

# Activate venv if present
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate"
fi

# Get initial balance if address provided
if [ -n "$MINER_ADDRESS" ]; then
    echo "Querying initial balance..."
    INITIAL_BALANCE=$(python -c "
import sys
sys.path.insert(0, '$ROOT_DIR')
from rpc.client import RpcClient
client = RpcClient('$RPC_URL')
balance = client.get_balance('$MINER_ADDRESS')
print(balance)
" 2>/dev/null || echo "0")
    echo "Initial balance: $INITIAL_BALANCE"
    echo ""
fi

# Start CPU miner
cd "$ROOT_DIR"
echo "Starting CPU miner..."
ANIMICA_MINER_WORK_TYPE="$WORK_TYPE" python -m mining.cli.mine \
    --rpc "$RPC_URL" \
    --threads 1 \
    --device cpu \
    ${MINER_ADDRESS:+--address "$MINER_ADDRESS"} \
    "$@"
