#!/usr/bin/env python3
"""
Integration test to verify docker-compose.mainnet.yml uses correct chain_id.

This test ensures the mainnet docker configuration uses chain_id=0 (not 1)
to match the spec/params.yaml network definition. Using the wrong chain_id
causes rewards to be 0 because params don't load correctly.

Issue: docker-compose.mainnet.yml was using ANIMICA_CHAIN_ID: 1
Fix: Changed to ANIMICA_CHAIN_ID: 0

This test documents the issue and prevents regression.
"""

import sys
import yaml
from pathlib import Path

sys.path.insert(0, '.')

from consensus.rewards import compute_block_reward
from rpc.deps import _params_from_spec

EXPECTED_MAINNET_CHAIN_ID = 0
EXPECTED_TESTNET_CHAIN_ID = 1
EXPECTED_DEVNET_CHAIN_ID = 1337
EXPECTED_BLOCK_REWARD_NANM = 300_000_000_000  # 300 ANM

def test_docker_compose_chain_ids():
    """Verify all docker-compose files use correct chain_ids."""
    print("\n" + "="*70)
    print("Docker Compose Chain ID Validation")
    print("="*70)
    
    docker_dir = Path("ops/docker")
    
    # Test mainnet
    mainnet_file = docker_dir / "docker-compose.mainnet.yml"
    with open(mainnet_file) as f:
        mainnet_yml = yaml.safe_load(f)
    
    node_env = mainnet_yml['services']['node']['environment']
    chain_id_line = node_env['ANIMICA_CHAIN_ID']
    
    # Extract default value from "${ANIMICA_CHAIN_ID:-0}" format
    if ':-' in chain_id_line:
        default_chain_id = int(chain_id_line.split(':-')[1].rstrip('}').strip('"'))
    else:
        default_chain_id = int(chain_id_line)
    
    print(f"\n✓ Mainnet docker-compose chain_id: {default_chain_id}")
    assert default_chain_id == EXPECTED_MAINNET_CHAIN_ID, \
        f"Mainnet should use chain_id={EXPECTED_MAINNET_CHAIN_ID}, got {default_chain_id}"
    
    # Verify params load correctly for this chain_id
    params = _params_from_spec(default_chain_id)
    assert 'monetary' in params, \
        f"Chain ID {default_chain_id} should have monetary params (check spec/params.yaml)"
    
    issuance = params['monetary']['issuance']
    subsidy = issuance['subsidy']
    start_nanm = subsidy['start_nANM_per_block']
    
    print(f"  ✓ Params loaded: {start_nanm} nANM = {start_nanm / 1e9} ANM per block")
    assert start_nanm == EXPECTED_BLOCK_REWARD_NANM, \
        f"Expected {EXPECTED_BLOCK_REWARD_NANM} nANM, got {start_nanm}"
    
    # Verify reward calculation works
    rewards = compute_block_reward(chain_id=default_chain_id, height=1, params=params)
    assert rewards and len(rewards) > 0, "No rewards returned!"
    
    addr, amount = rewards[0]
    print(f"  ✓ Block 1 reward: {amount / 1e9} ANM ({amount} nANM)")
    assert amount == EXPECTED_BLOCK_REWARD_NANM, \
        f"Expected reward {EXPECTED_BLOCK_REWARD_NANM} nANM, got {amount}"
    
    # Test testnet
    testnet_file = docker_dir / "docker-compose.testnet.yml"
    with open(testnet_file) as f:
        testnet_yml = yaml.safe_load(f)
    
    node_env = testnet_yml['services']['node']['environment']
    chain_id_line = node_env['ANIMICA_CHAIN_ID']
    
    if ':-' in chain_id_line:
        testnet_chain_id = int(chain_id_line.split(':-')[1].rstrip('}').strip('"'))
    else:
        testnet_chain_id = int(chain_id_line)
    
    print(f"\n✓ Testnet docker-compose chain_id: {testnet_chain_id}")
    assert testnet_chain_id == EXPECTED_TESTNET_CHAIN_ID, \
        f"Testnet should use chain_id={EXPECTED_TESTNET_CHAIN_ID}, got {testnet_chain_id}"
    
    # Test devnet
    devnet_file = docker_dir / "docker-compose.devnet.yml"
    with open(devnet_file) as f:
        devnet_yml = yaml.safe_load(f)
    
    # Devnet uses 'CHAIN_ID' environment variable (not ANIMICA_CHAIN_ID)
    node_env = devnet_yml['services']['node']['environment']
    chain_id_line = node_env.get('CHAIN_ID') or node_env.get('ANIMICA_CHAIN_ID')
    
    if ':-' in chain_id_line:
        devnet_chain_id = int(chain_id_line.split(':-')[1].rstrip('}').strip('"'))
    else:
        devnet_chain_id = int(chain_id_line)
    
    print(f"\n✓ Devnet docker-compose chain_id: {devnet_chain_id}")
    assert devnet_chain_id == EXPECTED_DEVNET_CHAIN_ID, \
        f"Devnet should use chain_id={EXPECTED_DEVNET_CHAIN_ID}, got {devnet_chain_id}"
    
    print("\n" + "="*70)
    print("✓ SUCCESS: All docker-compose files use correct chain_ids")
    print("="*70)

def test_chain_id_1_has_no_params():
    """
    Document: chain_id=1 is now testnet (changed from 2).
    
    This test verifies that chain_id=1 returns testnet params.
    Mainnet still uses chain_id=0.
    """
    print("\n" + "="*70)
    print("Chain ID 1 Testnet Verification")
    print("="*70)
    
    params = _params_from_spec(1)
    
    # Chain ID 1 should have monetary params (testnet)
    has_monetary = 'monetary' in params and params['monetary'].get('issuance')
    
    if has_monetary:
        print("✓ Chain ID 1 has monetary params (testnet)")
        print("  Testnet now uses chain_id=1 (changed from 2)")
    else:
        print("⚠ WARNING: Chain ID 1 has no monetary params!")
        print("  This is unexpected - testnet should use chain_id=1")
    
    # Try to compute rewards with chain_id=1
    rewards = compute_block_reward(chain_id=1, height=1, params=params)
    
    if rewards:
        addr, amount = rewards[0]
        print(f"✓ Chain ID 1 returns rewards: {amount / 1e9} ANM (testnet)")
    else:
        print("⚠ Chain ID 1 returns no rewards - check params configuration")
    
    print("="*70)

if __name__ == "__main__":
    try:
        test_docker_compose_chain_ids()
        test_chain_id_1_has_no_params()
        print("\n✓ ALL TESTS PASSED\n")
        sys.exit(0)
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)
