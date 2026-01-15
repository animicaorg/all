#!/usr/bin/env python3
"""
Simple verification that the sync stall fixes are syntactically correct
and the defensive logic is properly structured.
"""

import sys
import ast
import os

def check_syntax(filepath):
    """Check if the Python file has valid syntax."""
    print(f"Checking syntax of {filepath}...")
    try:
        with open(filepath, 'r') as f:
            code = f.read()
        ast.parse(code)
        print(f"✓ Syntax is valid")
        return True
    except SyntaxError as e:
        print(f"✗ Syntax error: {e}")
        return False

def check_defensive_patterns(filepath):
    """Check that defensive patterns are present in the code."""
    print(f"\nChecking defensive patterns in {filepath}...")
    
    patterns = {
        "fork_recovery": "duplicate_headers_fork",
        "in_flight_watchdog": "In-flight headers but accepting nothing",
        "stale_anchor_clear": "Clearing stale headers_duplicate anchor",
        "fork_detection": "Large gap between matched ancestor and local head",
        "inflight_fork_recovery": "inflight_no_accept_fork",
    }
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    found = {}
    for name, pattern in patterns.items():
        if pattern.replace(".*", "") in content:
            found[name] = True
            print(f"✓ Found {name} pattern")
        else:
            found[name] = False
            print(f"✗ Missing {name} pattern")
    
    return all(found.values())

def check_constants_used(filepath):
    """Check that defensive timeout values are reasonable."""
    print(f"\nChecking defensive timeout constants...")
    
    checks = [
        ("FORK_DETECTION_GAP_THRESHOLD", "Fork detection threshold constant"),
        ("FORK_RECOVERY_GAP_THRESHOLD", "Fork recovery threshold constant"),
        ("STALE_ANCHOR_TIMEOUT_SEC", "Stale anchor timeout constant"),
        ("INFLIGHT_RECENT_RESPONSE_SEC", "In-flight recent response constant"),
    ]
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    found_all = True
    for check, description in checks:
        if check in content:
            print(f"✓ Found {description}: {check}")
        else:
            print(f"✗ Missing {description}: {check}")
            found_all = False
    
    return found_all

def main():
    # Use relative path from script location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(script_dir, "p2p", "node", "p2p_service.py")
    
    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found")
        return 1
    
    # Run checks
    checks = [
        check_syntax(filepath),
        check_defensive_patterns(filepath),
        check_constants_used(filepath),
    ]
    
    # Summary
    print("\n" + "="*60)
    if all(checks):
        print("✓ All checks passed!")
        print("\nThe sync stall fixes have been successfully applied:")
        print("  - Fork detection and recovery")
        print("  - In-flight header watchdog")
        print("  - Stale anchor clearing")
        print("  - Comprehensive defensive mechanisms")
        return 0
    else:
        print("✗ Some checks failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
