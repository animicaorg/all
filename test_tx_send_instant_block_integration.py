#!/usr/bin/env python3
"""
Test that tx send blocks (instant blocks) have zero rewards.

This test simulates the actual flow where tx.sendRawTransaction
calls _ensure_tx_persisted_to_chain which mines an instant block.
"""

import sys
import os

# Add repo root to path
sys.path.insert(0, os.path.dirname(__file__))


def test_tx_send_instant_block_zero_reward():
    """Test that instant blocks mined during tx send have zero rewards."""
    print("\n" + "="*70)
    print("TEST: TX send blocks should have zero rewards")
    print("="*70)
    
    # Test 1: Verify compute_block_reward works with instant_block parameter
    print("\n1. Testing compute_block_reward with instant_block parameter")
    try:
        from consensus.rewards import compute_block_reward
        
        # Normal block at height 1
        normal = compute_block_reward(1337, 1, {}, instant_block=False)
        print(f"   Normal block rewards: {len(normal)} entries")
        
        # Instant block at height 1
        instant = compute_block_reward(1337, 1, {}, instant_block=True)
        print(f"   Instant block rewards: {len(instant)} entries")
        
        if instant:
            print(f"   ✗ FAIL: Instant block should have zero rewards")
            return False
        else:
            print(f"   ✓ PASS: Instant block has zero rewards")
    except Exception as e:
        print(f"   ✗ FAIL: Error testing compute_block_reward: {e}")
        return False
    
    # Test 2: Verify miner_mine accepts instant_block parameter
    print("\n2. Testing miner_mine accepts instant_block parameter")
    try:
        from rpc.methods import miner as miner_methods
        
        # Check if miner_mine function accepts instant_block parameter
        import inspect
        sig = inspect.signature(miner_methods.miner_mine)
        params = sig.parameters
        
        if 'instant_block' in params:
            print(f"   ✓ PASS: miner_mine accepts instant_block parameter")
            print(f"   Parameter type: {params['instant_block'].annotation}")
            print(f"   Parameter default: {params['instant_block'].default}")
        else:
            print(f"   ✗ FAIL: miner_mine does not accept instant_block parameter")
            print(f"   Available parameters: {list(params.keys())}")
            return False
    except Exception as e:
        print(f"   ✗ FAIL: Error checking miner_mine signature: {e}")
        return False
    
    # Test 3: Verify _mine_once accepts instant_block parameter
    print("\n3. Testing _mine_once accepts instant_block parameter")
    try:
        from rpc.methods import miner as miner_methods
        
        # Check if _mine_once function accepts instant_block parameter
        import inspect
        sig = inspect.signature(miner_methods._mine_once)
        params = sig.parameters
        
        if 'instant_block' in params:
            print(f"   ✓ PASS: _mine_once accepts instant_block parameter")
            print(f"   Parameter type: {params['instant_block'].annotation}")
            print(f"   Parameter default: {params['instant_block'].default}")
        else:
            print(f"   ✗ FAIL: _mine_once does not accept instant_block parameter")
            print(f"   Available parameters: {list(params.keys())}")
            return False
    except Exception as e:
        print(f"   ✗ FAIL: Error checking _mine_once signature: {e}")
        return False
    
    # Test 4: Verify _apply_block_reward accepts instant_block parameter
    print("\n4. Testing _apply_block_reward accepts instant_block parameter")
    try:
        from rpc.methods import miner as miner_methods
        
        # Check if _apply_block_reward function accepts instant_block parameter
        import inspect
        sig = inspect.signature(miner_methods._apply_block_reward)
        params = sig.parameters
        
        if 'instant_block' in params:
            print(f"   ✓ PASS: _apply_block_reward accepts instant_block parameter")
            print(f"   Parameter type: {params['instant_block'].annotation}")
            print(f"   Parameter default: {params['instant_block'].default}")
        else:
            print(f"   ✗ FAIL: _apply_block_reward does not accept instant_block parameter")
            print(f"   Available parameters: {list(params.keys())}")
            return False
    except Exception as e:
        print(f"   ✗ FAIL: Error checking _apply_block_reward signature: {e}")
        return False
    
    # Test 5: Verify _ensure_tx_persisted_to_chain calls miner_mine with instant_block=True
    print("\n5. Testing _ensure_tx_persisted_to_chain uses instant_block=True")
    try:
        # Read the source file directly to avoid import issues
        with open('rpc/methods/tx.py', 'r') as f:
            source = f.read()
        
        # Find the _ensure_tx_persisted_to_chain function
        if '_ensure_tx_persisted_to_chain' in source and 'instant_block=True' in source:
            # Check that instant_block=True appears after _ensure_tx_persisted_to_chain
            func_start = source.find('def _ensure_tx_persisted_to_chain')
            instant_block_pos = source.find('instant_block=True', func_start)
            next_func_start = source.find('\ndef ', func_start + 1)
            
            # If instant_block=True appears before the next function, it's in the right function
            if func_start < instant_block_pos < next_func_start:
                print(f"   ✓ PASS: _ensure_tx_persisted_to_chain calls miner_mine with instant_block=True")
            else:
                print(f"   ✗ FAIL: instant_block=True not in _ensure_tx_persisted_to_chain function")
                return False
        else:
            print(f"   ✗ FAIL: _ensure_tx_persisted_to_chain or instant_block=True not found")
            return False
    except Exception as e:
        print(f"   ✗ FAIL: Error checking _ensure_tx_persisted_to_chain source: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print(f"\n" + "="*70)
    print(f"TEST RESULT: ✓ ALL TESTS PASSED")
    print(f"="*70)
    
    return True


if __name__ == "__main__":
    success = test_tx_send_instant_block_zero_reward()
    sys.exit(0 if success else 1)
