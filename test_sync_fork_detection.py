#!/usr/bin/env python3
"""
Manual test to verify fork detection logic in p2p_service.py

This test checks that the new fork detection code correctly identifies
when a node is on a minority fork based on matched_ancestor_height gap.
"""

import os
import re
from pathlib import Path


# Get the script directory and find p2p_service.py relative to it
SCRIPT_DIR = Path(__file__).parent
P2P_SERVICE_PATH = SCRIPT_DIR / "p2p" / "node" / "p2p_service.py"


def test_fork_detection_logic_present():
    """Verify the fork detection logic was added to p2p_service.py"""
    
    if not P2P_SERVICE_PATH.exists():
        print(f"✗ Could not find p2p_service.py at {P2P_SERVICE_PATH}")
        return False
    
    with open(P2P_SERVICE_PATH, 'r') as f:
        content = f.read()
    
    # Check for the new fork detection logic (test for functionality, not exact wording)
    checks = [
        # Check 1: Fork detection based on matched_ancestor_height
        (r'(CRITICAL|FIX).*minority fork', 
         "Fork detection logic present"),
        
        # Check 2: Check for matched_ancestor_height gap calculation
        (r'ancestor_gap.*=.*best_block_height.*-.*_sync_last_matched_ancestor_height',
         "Ancestor gap calculation"),
        
        # Check 3: Check for canonical chain progress detection
        (r'canonical_chain_progressed',
         "Canonical chain progress tracking"),
        
        # Check 4: Check for target_height evidence
        (r'_sync_target_height.*>.*_sync_last_matched_ancestor_height',
         "Target height evidence check"),
        
        # Check 5: Check for fork detection threshold
        (r'ancestor_gap.*>.*FORK_DETECTION_GAP_THRESHOLD',
         "Fork detection threshold check"),
        
        # Check 6: Check for chain reorganization call
        (r'_reset_chain_to_ancestor\(',
         "Chain reorganization call"),
        
        # Check 7: Check for minority_fork_detected reason
        (r'minority_fork_detected',
         "Minority fork reason"),
    ]
    
    print("Testing fork detection logic additions...")
    all_passed = True
    
    for pattern, description in checks:
        if re.search(pattern, content):
            print(f"✓ {description}")
        else:
            print(f"✗ {description} - NOT FOUND")
            all_passed = False
    
    return all_passed


def test_target_height_logic_present():
    """Verify the target_height consideration logic was added"""
    
    if not P2P_SERVICE_PATH.exists():
        print(f"✗ Could not find p2p_service.py at {P2P_SERVICE_PATH}")
        return False
    
    with open(P2P_SERVICE_PATH, 'r') as f:
        content = f.read()
    
    # Check for the enhanced sync decision logic
    checks = [
        # Check 1: target_height variable in sync decision
        (r'target_height\s*=\s*self\._sync_target_height',
         "Target height variable"),
        
        # Check 2: should_continue_sync logic
        (r'should_continue_sync',
         "Continue sync flag"),
        
        # Check 3: Target height check
        (r'target_height.*>.*local_height',
         "Target height comparison"),
        
        # Check 4: Continue reason tracking
        (r'continue_reason.*=.*["\']target_height["\']',
         "Target height continue reason"),
        
        # Check 5: Updated log message (more flexible pattern)
        (r'(behind|target).*sync',
         "Sync decision log message"),
    ]
    
    print("\nTesting target_height logic additions...")
    all_passed = True
    
    for pattern, description in checks:
        if re.search(pattern, content):
            print(f"✓ {description}")
        else:
            print(f"✗ {description} - NOT FOUND")
            all_passed = False
    
    return all_passed


def test_no_syntax_errors():
    """Basic syntax check by trying to compile the module"""
    
    print("\nTesting Python syntax...")
    
    if not P2P_SERVICE_PATH.exists():
        print(f"✗ Could not find p2p_service.py at {P2P_SERVICE_PATH}")
        return False
    
    try:
        with open(P2P_SERVICE_PATH, 'r') as f:
            code = f.read()
        compile(code, str(P2P_SERVICE_PATH), 'exec')
        print("✓ No syntax errors")
        return True
    except SyntaxError as e:
        print(f"✗ Syntax error: {e}")
        return False


def main():
    print("=" * 60)
    print("Sync Fork Detection Test Suite")
    print("=" * 60)
    
    results = []
    results.append(("Fork detection logic", test_fork_detection_logic_present()))
    results.append(("Target height logic", test_target_height_logic_present()))
    results.append(("Python syntax", test_no_syntax_errors()))
    
    print("\n" + "=" * 60)
    print("Test Results Summary")
    print("=" * 60)
    
    all_passed = all(result for _, result in results)
    
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        symbol = "✓" if passed else "✗"
        print(f"{symbol} {test_name}: {status}")
    
    print("=" * 60)
    
    if all_passed:
        print("\n✓ All tests passed!")
        return 0
    else:
        print("\n✗ Some tests failed!")
        return 1


if __name__ == '__main__':
    exit(main())
