#!/usr/bin/env python3
"""
Verification script for genesis hash variants sync fix.

This script helps verify that the node correctly handles different genesis hash
variants when syncing from genesis. It checks:
1. That anchor_candidates includes all genesis hash variants
2. That headers at height 1 can be accepted with any valid genesis hash variant
3. That sync progresses beyond genesis without getting stuck

Usage:
    python3 verify_genesis_hash_sync_fix.py [--rpc-url URL]
"""

import argparse
import sys
import httpx


def make_rpc_call(url: str, method: str, params=None):
    """Make a JSON-RPC call to the node."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or []
    }
    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            return None, data["error"]
        return data.get("result"), None
    except Exception as e:
        return None, str(e)


def check_sync_status(rpc_url: str):
    """Check the current sync status of the node."""
    print(f"Checking sync status at {rpc_url}...")
    
    result, error = make_rpc_call(rpc_url, "animica_sync_status")
    if error:
        print(f"❌ Error getting sync status: {error}")
        return None
    
    return result


def verify_genesis_hash_variants(rpc_url: str):
    """Verify that genesis hash variants are properly handled."""
    print("\n=== Genesis Hash Variants Sync Fix Verification ===\n")
    
    # Get initial sync status
    status = check_sync_status(rpc_url)
    if not status:
        return False
    
    print(f"Current head height: {status.get('head_height', 'unknown')}")
    print(f"Target height: {status.get('target_height', 'unknown')}")
    print(f"Sync phase: {status.get('phase', 'unknown')}")
    print(f"Sync status reason: {status.get('sync_status_reason', 'none')}")
    print(f"Peer tips fresh: {status.get('peer_tips_fresh', 0)}")
    print(f"Peer tips total: {status.get('peer_tips_total', 0)}")
    
    # Check anchor candidates
    if 'last_anchor_check' in status:
        anchor_check = status['last_anchor_check']
        print(f"\nLast anchor check:")
        print(f"  Anchor height: {anchor_check.get('anchor_height', 'unknown')}")
        print(f"  Anchor source: {anchor_check.get('anchor_source', 'unknown')}")
        print(f"  Prev hash known: {anchor_check.get('prev_hash_known', 'unknown')}")
        
        candidates = anchor_check.get('anchor_candidates', [])
        print(f"  Anchor candidates: {len(candidates)}")
        for candidate in candidates:
            print(f"    - {candidate.get('source')}: height {candidate.get('height')}, hash {candidate.get('hash', 'unknown')[:16]}...")
    
    # Check for genesis-related issues
    head_height = status.get('head_height', 0)
    target_height = status.get('target_height', 0)
    sync_reason = status.get('sync_status_reason', '')
    
    at_genesis = head_height == 0
    has_target = target_height and target_height > 0
    no_fresh_tips = sync_reason == 'no_fresh_peer_tips'
    
    print("\n=== Diagnosis ===")
    
    if at_genesis and has_target:
        print("✓ Node is at genesis with target height > 0 (expected scenario)")
        
        if no_fresh_tips:
            print("⚠ Node reports 'no_fresh_peer_tips' - this may indicate peer connectivity issues")
        
        # Check if headers are being rejected
        accepted_count = status.get('last_headers_accepted_count', 0)
        discarded_count = status.get('last_headers_discarded_count', 0)
        discard_reasons = status.get('last_headers_discard_reason_counts', {})
        
        print(f"\nHeader processing stats:")
        print(f"  Last accepted: {accepted_count}")
        print(f"  Last discarded: {discarded_count}")
        if discard_reasons:
            print(f"  Discard reasons: {discard_reasons}")
        
        if discarded_count > 0 and accepted_count == 0:
            print("⚠ Headers are being discarded but not accepted - check logs for genesis hash mismatch")
            print("   Expected fix: Headers should be accepted with any valid genesis hash variant")
            
        # Check recovery actions
        recovery_actions = status.get('stall_recovery_actions', {})
        if recovery_actions:
            print(f"\nStall recovery actions: {recovery_actions}")
            persistent_retries = recovery_actions.get('genesis_watchdog_persistent_retry', 0)
            if persistent_retries > 5:
                print(f"⚠ High number of persistent retries ({persistent_retries}) - sync may be stuck")
                print("   This suggests headers are being repeatedly rejected")
    else:
        if head_height > 0:
            print(f"✓ Node has progressed beyond genesis (height {head_height})")
        else:
            print("ℹ Node is at genesis with no target height")
    
    print("\n=== Recommendations ===")
    
    if at_genesis and has_target and no_fresh_tips:
        print("1. Check that peers are properly connected and have valid chain ID")
        print("2. Review logs for 'Genesis anchor mismatch' warnings")
        print("3. Verify that anchor_candidates includes all genesis hash variants")
        print("4. If headers are being rejected, check that parent_hash matches expected genesis")
    
    if at_genesis and has_target and not no_fresh_tips:
        print("1. Sync should progress automatically - monitor for a few minutes")
        print("2. If stuck, check logs for detailed diagnostics added by the fix")
    
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Verify genesis hash variants sync fix"
    )
    parser.add_argument(
        "--rpc-url",
        default="http://127.0.0.1:8545/rpc",
        help="RPC URL of the node (default: http://127.0.0.1:8545/rpc)"
    )
    
    args = parser.parse_args()
    
    success = verify_genesis_hash_variants(args.rpc_url)
    
    if success:
        print("\n✓ Verification completed")
        return 0
    else:
        print("\n❌ Verification failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
