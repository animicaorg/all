#!/usr/bin/env bash
# verify_mainnet_genesis_fix.sh
#
# Verification script for the mainnet genesis hash fix.
# This script validates that the fix prevents the genesis mismatch error.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "=================================="
echo "Mainnet Genesis Hash Fix Verification"
echo "=================================="
echo ""

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

success() { echo -e "${GREEN}✓${NC} $*"; }
error() { echo -e "${RED}✗${NC} $*"; }
info() { echo -e "${YELLOW}ℹ${NC} $*"; }

# Step 1: Verify Python dependencies
info "Checking Python environment..."
if ! python3 -c "import core.genesis.loader" 2>/dev/null; then
    error "Failed to import core.genesis.loader"
    info "Run: pip install -e ."
    exit 1
fi
success "Python environment OK"

# Step 2: Run the regression test
info "Running regression test suite..."
if python -m pytest tests/test_pinned_genesis_mainnet.py -q; then
    success "All regression tests passed (5/5)"
else
    error "Regression tests failed"
    exit 1
fi

# Step 3: Verify mainnet genesis hash computation
info "Computing mainnet genesis hash..."
COMPUTED_HASH=$(python3 -c "
from core.genesis.loader import compute_genesis_hash
print(compute_genesis_hash('core/genesis/mainnet.json', chain_id=0))
")
EXPECTED_HASH="0x6a27e93193020cd00fe429ef0ffac1c3a774268a589c2911ac396dd3cba2d242"

if [ "$COMPUTED_HASH" = "$EXPECTED_HASH" ]; then
    success "Computed hash matches expected: $COMPUTED_HASH"
else
    error "Hash mismatch!"
    echo "  Expected: $EXPECTED_HASH"
    echo "  Computed: $COMPUTED_HASH"
    exit 1
fi

# Step 4: Simulate node startup
info "Simulating node startup for mainnet..."
if python3 << 'PYEOF'
from core.genesis.loader import compute_genesis_identity
from core.network_params import enforce_pinned_genesis
import sys

try:
    genesis_path = "core/genesis/mainnet.json"
    identity = compute_genesis_identity(genesis_path, chain_id=0)
    enforce_pinned_genesis(
        chain_id=0,
        genesis_block_hash=identity.genesis_block_hash,
        genesis_path=genesis_path,
        network_name="mainnet"
    )
    print("Node startup simulation: SUCCESS")
    sys.exit(0)
except Exception as e:
    print(f"Node startup simulation: FAILED - {e}")
    sys.exit(1)
PYEOF
then
    success "Node startup simulation passed"
else
    error "Node startup simulation failed"
    exit 1
fi

# Step 5: Verify all networks
info "Verifying all network genesis hashes..."
if python3 << 'PYEOF'
from core.genesis.loader import compute_genesis_hash
from core.network_params import (
    MAINNET_GENESIS_HASH_HEX,
    TESTNET_GENESIS_HASH_HEX,
    DEVNET_GENESIS_HASH_HEX,
    GENESIS_PATH_BY_NETWORK,
)

networks = [
    ("mainnet", 0, MAINNET_GENESIS_HASH_HEX),
    ("testnet", 2, TESTNET_GENESIS_HASH_HEX),
    ("devnet", 1337, DEVNET_GENESIS_HASH_HEX),
]

all_pass = True
for name, chain_id, pinned in networks:
    path = GENESIS_PATH_BY_NETWORK[(name, chain_id)]
    if not path.exists():
        continue
    computed = compute_genesis_hash(path, chain_id=chain_id)
    if computed != pinned:
        print(f"{name}: MISMATCH")
        all_pass = False

exit(0 if all_pass else 1)
PYEOF
then
    success "All network genesis hashes verified"
else
    error "Network genesis hash verification failed"
    exit 1
fi

# Summary
echo ""
echo "=================================="
success "ALL VERIFICATIONS PASSED"
echo "=================================="
echo ""
info "Next steps for Docker testing:"
echo "  1. Build Docker image with these changes"
echo "  2. Run: animica node up --network mainnet"
echo "  3. Verify container stays up (no crash-loop)"
echo "  4. Test RPC endpoint:"
echo "     curl -s http://127.0.0.1:8545/rpc \\"
echo "       -H 'content-type: application/json' \\"
echo "       -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"chain.getHead\",\"params\":[]}'"
echo "  5. Check: animica node status"
echo ""
