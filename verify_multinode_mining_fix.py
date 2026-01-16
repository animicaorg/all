#!/usr/bin/env python3
"""
Verification Script: Multi-Node Mining to Same Wallet Fix

This script helps verify that the fix for multi-node mining crashes is working.

Usage:
    python3 verify_multinode_mining_fix.py

What it checks:
1. The strict parent hash check has been removed
2. Warning logging is in place
3. Mining address tracking exists
4. User warnings are present
"""

import re
import sys
from pathlib import Path

def check_file_content(filepath: Path, checks: list[tuple[str, str, bool]]) -> tuple[int, int]:
    """
    Check file for expected content.
    
    Args:
        filepath: Path to file to check
        checks: List of (description, pattern, should_exist) tuples
        
    Returns:
        (passed, total) counts
    """
    try:
        content = filepath.read_text()
    except FileNotFoundError:
        print(f"❌ File not found: {filepath}")
        return 0, len(checks)
    
    passed = 0
    total = len(checks)
    
    for description, pattern, should_exist in checks:
        found = bool(re.search(pattern, content, re.MULTILINE | re.DOTALL))
        
        if found == should_exist:
            print(f"✅ {description}")
            passed += 1
        else:
            status = "found" if found else "not found"
            expected = "expected" if should_exist else "should not exist"
            print(f"❌ {description} - {status} but {expected}")
    
    return passed, total


def main():
    print("="*80)
    print("Verifying Multi-Node Mining to Same Wallet Fix")
    print("="*80)
    print()
    
    repo_root = Path(__file__).parent
    miner_py = repo_root / "rpc" / "methods" / "miner.py"
    
    if not miner_py.exists():
        print(f"❌ Cannot find {miner_py}")
        print("   Make sure you're running this from the repo root")
        return 1
    
    print("Checking rpc/methods/miner.py...")
    print()
    
    checks = [
        # Check 1: Strict parent-head check should be removed (specific case we fixed)
        (
            "1. Strict parent-head hash check removed (line ~4987)",
            r'if parent_hash_hex and head_hash and parent_hash_hex != head_hash:\s*raise\s+rpc_errors\.RpcError',
            False  # Should NOT exist
        ),
        
        # Check 2: Warning should be logged instead
        (
            "2. Warning logged for parent mismatch",
            r'log\.warning\(\s*["\']Block parent mismatch - possible multi-node mining',
            True  # Should exist
        ),
        
        # Check 3: Mining address tracking exists
        (
            "3. Mining address tracking function exists",
            r'def _track_mining_address\(address: str\)',
            True
        ),
        
        # Check 4: Active mining addresses dict exists
        (
            "4. Active mining addresses tracking dict exists",
            r'_ACTIVE_MINING_ADDRESSES:\s*dict\[str,\s*dict\[str,\s*Any\]\]',
            True
        ),
        
        # Check 5: Multi-node mining warning in getBlockTemplate
        (
            "5. MULTI_NODE_MINING_DETECTED warning exists",
            r'MULTI_NODE_MINING_DETECTED',
            True
        ),
        
        # Check 6: Enhanced diagnostics for parent mismatch rejection
        (
            "6. Enhanced logging for parent mismatch rejection",
            r'Block rejected due to parent mismatch - possible multi-node mining conflict',
            True
        ),
        
        # Check 7: User guidance in logs
        (
            "7. User guidance about unique wallets",
            r'Use a different wallet address for each mining node',
            True
        ),
    ]
    
    passed, total = check_file_content(miner_py, checks)
    
    print()
    print("="*80)
    print(f"Results: {passed}/{total} checks passed")
    
    if passed == total:
        print()
        print("✅ All checks passed! The fix is properly implemented.")
        print()
        print("The following improvements are in place:")
        print("  • Strict parent check removed (no more crashes)")
        print("  • Warning logging for diagnostics")
        print("  • Mining address tracking for detection")
        print("  • User warnings and guidance")
        print()
        print("Next steps:")
        print("  1. Test with two nodes mining to same wallet")
        print("  2. Verify logs show warnings but no crashes")
        print("  3. Confirm both nodes continue syncing")
        return 0
    else:
        print()
        print(f"⚠️  {total - passed} check(s) failed.")
        print("   The fix may not be fully implemented.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
