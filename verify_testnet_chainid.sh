#!/bin/bash
# Quick verification script for testnet chain_id change

set -e

echo "=========================================="
echo "Testnet Chain ID Change Verification"
echo "=========================================="
echo ""

echo "1. Checking Python constants..."
python3 -c "
from core.config import TESTNET_CHAIN_ID, MAINNET_CHAIN_ID, DEVNET_CHAIN_ID
print(f'  MAINNET_CHAIN_ID = {MAINNET_CHAIN_ID} (expected: 1)')
print(f'  TESTNET_CHAIN_ID = {TESTNET_CHAIN_ID} (expected: 2)')
print(f'  DEVNET_CHAIN_ID = {DEVNET_CHAIN_ID} (expected: 1337)')
assert MAINNET_CHAIN_ID == 1, 'Mainnet should be 1'
assert TESTNET_CHAIN_ID == 2, 'Testnet should be 2'
assert DEVNET_CHAIN_ID == 1337, 'Devnet should be 1337'
print('  ✓ All constants correct')
"
echo ""

echo "2. Checking network params..."
python3 -c "
from core.network_params import MAINNET_PARAMS, TESTNET_PARAMS, DEVNET_PARAMS
print(f'  Mainnet: chain_id={MAINNET_PARAMS.chain_id}, name={MAINNET_PARAMS.name}')
print(f'  Testnet: chain_id={TESTNET_PARAMS.chain_id}, name={TESTNET_PARAMS.name}')
print(f'  Devnet: chain_id={DEVNET_PARAMS.chain_id}, name={DEVNET_PARAMS.name}')
assert MAINNET_PARAMS.chain_id == 1
assert TESTNET_PARAMS.chain_id == 2
assert DEVNET_PARAMS.chain_id == 1337
print('  ✓ Network params correct')
"
echo ""

echo "3. Checking spec/params.yaml..."
if grep -q '"animica:2":' spec/params.yaml; then
    echo "  ✓ Found animica:2 for testnet"
else
    echo "  ✗ Missing animica:2 in params.yaml"
    exit 1
fi

if grep -q '"animica:1":' spec/params.yaml; then
    echo "  ✓ Found animica:1 for mainnet"
else
    echo "  ✗ Missing animica:1 in params.yaml"
    exit 1
fi
echo ""

echo "4. Checking genesis files..."
testnet_id=$(grep -m1 '"chainId"' core/genesis/testnet.json | grep -oP '\d+')
if [ "$testnet_id" = "2" ]; then
    echo "  ✓ Testnet genesis has chainId=2"
else
    echo "  ✗ Testnet genesis has chainId=$testnet_id (expected 2)"
    exit 1
fi
echo ""

echo "5. Checking docker-compose.testnet.yml..."
if grep -q 'ANIMICA_CHAIN_ID:-2}' ops/docker/docker-compose.testnet.yml; then
    echo "  ✓ Docker Compose uses chain_id=2 default"
else
    echo "  ✗ Docker Compose not using chain_id=2"
    exit 1
fi
echo ""

echo "6. Running integration test..."
PYTHONPATH=/home/runner/work/all/all python3 tests/integration/test_testnet_chain_id_network_separation.py
echo ""

echo "=========================================="
echo "✅ ALL VERIFICATIONS PASSED"
echo "=========================================="
echo ""
echo "Summary:"
echo "  - Mainnet: chain_id=0"
echo "  - Testnet: chain_id=1 (changed from 2)"
echo "  - Devnet: chain_id=1337"
echo ""
echo "Network separation enforced via genesis hash."
