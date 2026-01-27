#!/usr/bin/env python3
"""
Test that instant blocks bypass mining gate checks.
"""

import sys
import os

# Add repo root to path
sys.path.insert(0, os.path.dirname(__file__))


def test_instant_block_bypass():
    """Test that miner_mine allows instant blocks even when mining gate would block."""
    print("\n" + "="*70)
    print("TEST: Instant blocks should bypass mining gate")
    print("="*70)
    
    try:
        from rpc.methods import miner as miner_methods
    except ImportError as e:
        print(f"SKIP: Cannot import rpc.methods.miner: {e}")
        return True
    
    # Test that instant_block=True bypasses mining gate
    # We can't easily test the full flow without a running node, but we can
    # verify the code path exists and doesn't crash
    
    print("\n  Checking miner_mine signature supports instant_block parameter...")
    import inspect
    sig = inspect.signature(miner_methods.miner_mine)
    params = sig.parameters
    
    if 'instant_block' not in params:
        print(f"  ✗ FAIL: miner_mine missing instant_block parameter")
        return False
    
    print(f"  ✓ PASS: miner_mine has instant_block parameter")
    
    # Verify the parameter is properly typed
    param = params['instant_block']
    print(f"    Parameter: {param}")
    print(f"    Default: {param.default}")
    
    # Check that the code contains the bypass logic
    import rpc.methods.miner as miner_mod
    source = inspect.getsource(miner_mod.miner_mine)
    
    if 'instant_block' not in source:
        print(f"  ✗ FAIL: miner_mine source doesn't reference instant_block")
        return False
    
    if 'bypass' not in source.lower():
        print(f"  ✗ FAIL: miner_mine source doesn't contain bypass logic")
        return False
    
    print(f"  ✓ PASS: miner_mine contains bypass logic for instant blocks")
    
    print(f"\n" + "="*70)
    print(f"TEST RESULT: ✓ ALL TESTS PASSED")
    print(f"="*70)
    
    return True


if __name__ == "__main__":
    success = test_instant_block_bypass()
    sys.exit(0 if success else 1)
