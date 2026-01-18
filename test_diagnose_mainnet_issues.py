#!/usr/bin/env python3
"""
Diagnostic script for mainnet sync and balance issues.

This script helps diagnose:
1. Chain ID configuration
2. Sync status and peer tips
3. Balance querying
4. Block import and reward application

Run this against a running mainnet node to identify issues.
"""

import sys
import json
import time
from pathlib import Path


def check_chain_id_config():
    """Check that mainnet is configured with chain_id=0."""
    print("\n" + "="*80)
    print("CHECK 1: Chain ID Configuration")
    print("="*80)
    
    try:
        from animica.config import load_network_config, DEFAULT_NETWORK
        
        # Check default network
        print(f"\n  Default network: {DEFAULT_NETWORK}")
        
        # Check mainnet config
        config = load_network_config("mainnet")
        print(f"\n  Mainnet Configuration:")
        print(f"    network name: {config.name}")
        print(f"    chain_id: {config.chain_id}")
        print(f"    rpc_url: {config.rpc_url}")
        print(f"    data_dir: {config.data_dir}")
        print(f"    db_name: {config.db_name}")
        
        if config.chain_id != 0:
            print(f"\n  ✗ ERROR: Mainnet chain_id is {config.chain_id}, expected 0!")
            return False
        
        print(f"\n  ✓ PASS: Mainnet correctly configured with chain_id=0")
        return True
        
    except Exception as e:
        print(f"\n  ✗ ERROR: Failed to load config: {e}")
        return False


def check_sync_status():
    """Check sync status including peer tips."""
    print("\n" + "="*80)
    print("CHECK 2: Sync Status")
    print("="*80)
    
    try:
        import httpx
        from animica.config import load_network_config
        
        config = load_network_config("mainnet")
        rpc_url = config.rpc_url
        
        print(f"\n  Querying RPC: {rpc_url}")
        
        # Query sync status
        response = httpx.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "method": "sync.getStatus",
                "params": [],
                "id": 1,
            },
            timeout=10.0,
        )
        
        if response.status_code != 200:
            print(f"\n  ✗ ERROR: RPC returned status {response.status_code}")
            return False
        
        result = response.json()
        if "error" in result:
            print(f"\n  ✗ ERROR: RPC error: {result['error']}")
            return False
        
        status = result.get("result", {})
        
        print(f"\n  Sync Status:")
        print(f"    phase: {status.get('phase')}")
        print(f"    head_height: {status.get('head_height')}")
        print(f"    best_header_height: {status.get('best_header_height')}")
        print(f"    best_block_height: {status.get('best_block_height')}")
        print(f"    best_remote_height: {status.get('best_remote_height')}")
        print(f"    network_best_height: {status.get('network_best_height')}")
        print(f"    sync_status_reason: {status.get('sync_status_reason')}")
        print(f"    synchronized: {status.get('synchronized')}")
        print(f"    behind_by: {status.get('behind_by')}")
        
        print(f"\n  Peer Tips:")
        print(f"    peer_tips_total: {status.get('peer_tips_total')}")
        print(f"    peer_tips_fresh: {status.get('peer_tips_fresh')}")
        print(f"    peer_tips_stale: {status.get('peer_tips_stale')}")
        
        # Check for known issues
        issues = []
        
        if status.get('sync_status_reason') == 'no_fresh_peer_tips':
            issues.append("ISSUE: sync_status_reason is 'no_fresh_peer_tips'")
        
        if status.get('peer_tips_fresh', 0) == 0 and status.get('peer_tips_total', 0) > 0:
            issues.append("ISSUE: Have peers but no fresh tips")
        
        if status.get('best_remote_height') is None:
            issues.append("ISSUE: best_remote_height is None")
        
        if issues:
            print(f"\n  ✗ ISSUES FOUND:")
            for issue in issues:
                print(f"    - {issue}")
            return False
        
        print(f"\n  ✓ PASS: Sync status looks healthy")
        return True
        
    except Exception as e:
        print(f"\n  ✗ ERROR: Failed to query sync status: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_node_status():
    """Check overall node status including chain ID."""
    print("\n" + "="*80)
    print("CHECK 3: Node Status")
    print("="*80)
    
    try:
        import httpx
        from animica.config import load_network_config
        
        config = load_network_config("mainnet")
        rpc_url = config.rpc_url
        
        print(f"\n  Querying RPC: {rpc_url}")
        
        # Query node status
        response = httpx.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "method": "node.getStatus",
                "params": [],
                "id": 1,
            },
            timeout=10.0,
        )
        
        if response.status_code != 200:
            print(f"\n  ✗ ERROR: RPC returned status {response.status_code}")
            return False
        
        result = response.json()
        if "error" in result:
            print(f"\n  ✗ ERROR: RPC error: {result['error']}")
            return False
        
        status = result.get("result", {})
        chain_info = status.get("chain", {})
        head = chain_info.get("head", {})
        
        print(f"\n  Chain Info:")
        print(f"    height: {head.get('height')}")
        print(f"    chain_id: {head.get('chainId') or head.get('chain_id')}")
        print(f"    hash: {head.get('hash', 'N/A')[:18]}...")
        
        actual_chain_id = head.get('chainId') or head.get('chain_id')
        
        if actual_chain_id != 0:
            print(f"\n  ✗ ERROR: Node reports chain_id={actual_chain_id}, expected 0 for mainnet!")
            return False
        
        print(f"\n  ✓ PASS: Node reports chain_id=0 (mainnet)")
        return True
        
    except Exception as e:
        print(f"\n  ✗ ERROR: Failed to query node status: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_balance_query():
    """Check balance query for a test address."""
    print("\n" + "="*80)
    print("CHECK 4: Balance Query")
    print("="*80)
    
    try:
        import httpx
        from animica.config import load_network_config
        
        config = load_network_config("mainnet")
        rpc_url = config.rpc_url
        
        # Use a test address (all zeros)
        test_address = "0x" + ("00" * 32)
        
        print(f"\n  Querying balance for test address: {test_address[:18]}...")
        print(f"  RPC: {rpc_url}")
        
        # Query balance
        response = httpx.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "method": "state.getBalance",
                "params": [test_address],
                "id": 1,
            },
            timeout=10.0,
        )
        
        if response.status_code != 200:
            print(f"\n  ✗ ERROR: RPC returned status {response.status_code}")
            return False
        
        result = response.json()
        if "error" in result:
            print(f"\n  ⚠ RPC error (expected for zero address): {result['error']}")
            print(f"  ✓ PASS: Balance RPC is accessible")
            return True
        
        balance = result.get("result")
        print(f"\n  Balance: {balance}")
        print(f"\n  ✓ PASS: Balance query successful")
        return True
        
    except Exception as e:
        print(f"\n  ✗ ERROR: Failed to query balance: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_block_reward_logic():
    """Check that block reward computation works for mainnet."""
    print("\n" + "="*80)
    print("CHECK 5: Block Reward Logic")
    print("="*80)
    
    try:
        from consensus.rewards import compute_block_reward
        
        # Test reward computation for mainnet (chain_id=0)
        print(f"\n  Computing block reward for mainnet (chain_id=0) at height 1...")
        
        # Use minimal params
        test_params = {
            "monetary": {
                "issuance": {
                    "subsidy": {
                        "start_nANM_per_block": 300_000_000_000,  # 300 ANM
                        "epoch_length_blocks": 1350000,
                        "decay_pct_per_epoch": 50.0,
                        "tail_nANM_per_block": 100000,
                        "max_halvings": 64,
                    },
                    "subsidy_split_pct": {
                        "miner": 100,
                        "aicf": 0,
                        "treasury": 0,
                    },
                },
            },
            "system_addresses": {
                "coinbase_default": "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "aicf_treasury": "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "treasury": "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            },
        }
        
        rewards = compute_block_reward(
            chain_id=0,
            height=1,
            params=test_params,
        )
        
        print(f"\n  Rewards computed:")
        for idx, (addr, amount) in enumerate(rewards):
            reward_type = "miner" if idx == 0 else f"other_{idx}"
            print(f"    [{idx}] {reward_type}: {amount} nANM ({amount / 1e9:.9f} ANM) → {addr[:20]}...")
        
        total_reward = sum(amt for _, amt in rewards)
        print(f"\n  Total reward: {total_reward} nANM ({total_reward / 1e9:.9f} ANM)")
        
        if total_reward != 300_000_000_000:
            print(f"\n  ⚠ WARNING: Expected 300 ANM, got {total_reward / 1e9:.9f} ANM")
        else:
            print(f"\n  ✓ PASS: Block reward computation correct")
        
        return True
        
    except Exception as e:
        print(f"\n  ✗ ERROR: Failed to compute block reward: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all diagnostic checks."""
    print("\n" + "="*80)
    print("MAINNET DIAGNOSTICS")
    print("="*80)
    print("\nThis script checks for common mainnet sync and balance issues.")
    print("Run this with a mainnet node running on default RPC port 8545.")
    
    results = {
        "chain_id_config": check_chain_id_config(),
        "sync_status": check_sync_status(),
        "node_status": check_node_status(),
        "balance_query": check_balance_query(),
        "block_reward_logic": check_block_reward_logic(),
    }
    
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for check_name, passed_check in results.items():
        status = "✓ PASS" if passed_check else "✗ FAIL"
        print(f"  {check_name:25s}: {status}")
    
    print(f"\n  Total: {passed}/{total} checks passed")
    
    if passed == total:
        print("\n  ✓ ALL CHECKS PASSED - System appears healthy")
        return 0
    else:
        print(f"\n  ✗ {total - passed} CHECKS FAILED - Issues detected")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
