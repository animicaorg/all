#!/usr/bin/env python3
"""
Verification script for sync status accuracy fix.

This script tests the sync status RPC endpoint to verify that:
1. best_remote_* fields are present and populated
2. behind_by is correctly calculated
3. synchronized is false when behind
4. sync_status_reason is set when not synchronized

Usage:
    python verify_sync_status_fix.py [--rpc-url http://localhost:8545]
"""

import argparse
import json
import sys
from typing import Any, Dict, Optional

try:
    import requests
except ImportError:
    print("Error: requests library not found. Install with: pip install requests")
    sys.exit(1)


def rpc_call(url: str, method: str, params: list = None) -> Dict[str, Any]:
    """Make a JSON-RPC call."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or []
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if "error" in data:
            raise Exception(f"RPC error: {data['error']}")
        
        return data.get("result", {})
    except requests.exceptions.RequestException as e:
        raise Exception(f"HTTP error: {e}")


def verify_sync_status(rpc_url: str) -> bool:
    """Verify sync status fix is working correctly."""
    print(f"Testing sync status at {rpc_url}...")
    print("=" * 60)
    
    success = True
    
    # Get sync status
    try:
        sync_status = rpc_call(rpc_url, "sync.getStatus", [])
    except Exception as e:
        print(f"❌ FAIL: Cannot get sync status: {e}")
        return False
    
    print("\n✓ Successfully called sync.getStatus")
    
    # Check for new fields
    required_fields = [
        "best_remote_height",
        "best_remote_hash", 
        "best_remote_peer",
        "best_remote_age_sec",
        "behind_by",
        "sync_status_reason"
    ]
    
    print("\nChecking for new fields:")
    for field in required_fields:
        if field in sync_status:
            value = sync_status[field]
            print(f"  ✓ {field}: {value}")
        else:
            print(f"  ❌ {field}: MISSING")
            success = False
    
    # Extract key values
    local_height = sync_status.get("head_height") or sync_status.get("best_block_height")
    best_remote_height = sync_status.get("best_remote_height")
    behind_by = sync_status.get("behind_by")
    synchronized = sync_status.get("synchronized")
    sync_status_reason = sync_status.get("sync_status_reason")
    
    print("\nKey Metrics:")
    print(f"  Local height:        {local_height}")
    print(f"  Best remote height:  {best_remote_height}")
    print(f"  Behind by:           {behind_by}")
    print(f"  Synchronized:        {synchronized}")
    print(f"  Reason:              {sync_status_reason}")
    
    # Validate logic
    print("\nValidating sync status logic:")
    
    # Test 1: If best_remote_height is None, synchronized should be False
    if best_remote_height is None:
        if synchronized:
            print(f"  ❌ FAIL: synchronized is True but best_remote_height is None")
            success = False
        else:
            print(f"  ✓ PASS: synchronized is False when best_remote_height is None")
        
        if sync_status_reason:
            print(f"  ✓ PASS: sync_status_reason is set: '{sync_status_reason}'")
        else:
            print(f"  ⚠ WARNING: sync_status_reason not set when best_remote unknown")
    
    # Test 2: If behind_by is calculated, check accuracy
    elif best_remote_height is not None and local_height is not None:
        expected_behind_by = max(0, best_remote_height - local_height)
        
        if behind_by == expected_behind_by:
            print(f"  ✓ PASS: behind_by correctly calculated ({behind_by})")
        else:
            print(f"  ❌ FAIL: behind_by mismatch. Expected {expected_behind_by}, got {behind_by}")
            success = False
        
        # Test 3: If significantly behind, should not be synchronized
        ALLOWED_LAG = 2
        if behind_by > ALLOWED_LAG:
            if synchronized:
                print(f"  ❌ FAIL: synchronized is True but {behind_by} blocks behind (> ALLOWED_LAG={ALLOWED_LAG})")
                success = False
            else:
                print(f"  ✓ PASS: synchronized is False when {behind_by} blocks behind")
        
        # Test 4: If within ALLOWED_LAG, can be synchronized
        elif behind_by <= ALLOWED_LAG:
            if synchronized:
                print(f"  ✓ PASS: synchronized is True when {behind_by} blocks behind (<= ALLOWED_LAG={ALLOWED_LAG})")
            else:
                print(f"  ℹ INFO: synchronized is False even though within ALLOWED_LAG (may be catching up)")
    
    # Test 5: Check best_remote_age_sec freshness
    best_remote_age = sync_status.get("best_remote_age_sec")
    if best_remote_age is not None:
        TIP_FRESHNESS_SEC = 60.0
        if best_remote_age <= TIP_FRESHNESS_SEC:
            print(f"  ✓ PASS: best_remote_age_sec ({best_remote_age:.1f}s) is fresh (<= {TIP_FRESHNESS_SEC}s)")
        else:
            print(f"  ⚠ WARNING: best_remote_age_sec ({best_remote_age:.1f}s) is stale (> {TIP_FRESHNESS_SEC}s)")
    
    # Display peer info if available
    best_remote_peer = sync_status.get("best_remote_peer")
    if best_remote_peer:
        print(f"\nBest Remote Peer:")
        print(f"  Peer:   {best_remote_peer}")
        print(f"  Height: {best_remote_height}")
        print(f"  Age:    {best_remote_age:.1f}s" if best_remote_age else "  Age:    N/A")
    
    print("\n" + "=" * 60)
    
    if success:
        print("✅ ALL CHECKS PASSED")
        print("\nThe sync status fix is working correctly!")
        return True
    else:
        print("❌ SOME CHECKS FAILED")
        print("\nThe sync status may not be working as expected.")
        return False


def main():
    parser = argparse.ArgumentParser(description="Verify sync status accuracy fix")
    parser.add_argument(
        "--rpc-url",
        default="http://localhost:8545",
        help="RPC URL to test (default: http://localhost:8545)"
    )
    
    args = parser.parse_args()
    
    print("Sync Status Accuracy Fix Verification")
    print("=" * 60)
    print()
    
    try:
        success = verify_sync_status(args.rpc_url)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
