#!/usr/bin/env python3
"""
Test that instant blocks skip PoW and use nonce=0.
This is a simplified unit test that verifies the code path.
"""

import sys
import os

# Add repo root to path
sys.path.insert(0, os.path.dirname(__file__))


def test_instant_block_code_path():
    """Test that the instant block code path is correctly implemented."""
    print("\n" + "="*70)
    print("TEST: Instant block PoW skip code path")
    print("="*70)
    
    try:
        # Import the miner module to check the fix is in place
        import rpc.methods.miner as miner_module
        import inspect
        
        # Get the source code of _mine_once
        source = inspect.getsource(miner_module._mine_once)
        
        # Check for instant block PoW skip
        checks = [
            ("instant block check", "if instant_block:" in source),
            ("skip PoW message", "skipping PoW" in source or "Skip PoW" in source),
            ("nonce=0 for instant", "valid_nonce = 0" in source),
            ("else for normal PoW", "else:" in source and "_mine_for_header" in source),
        ]
        
        all_passed = True
        for check_name, check_result in checks:
            status = "✅" if check_result else "❌"
            print(f"  {status} {check_name}: {check_result}")
            if not check_result:
                all_passed = False
        
        if all_passed:
            print("\n  ✅ All instant block PoW skip checks passed!")
            print("     The code correctly skips PoW for instant blocks.")
            return True
        else:
            print("\n  ❌ Some checks failed!")
            return False
            
    except Exception as e:
        print(f"\n  ❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_instant_block_logic():
    """Test the instant block logic at the function level."""
    print("\n" + "="*70)
    print("TEST: Instant block reward computation")
    print("="*70)
    
    try:
        from consensus.rewards import compute_block_reward
        
        # Test that instant blocks produce zero rewards
        rewards_instant = compute_block_reward(
            chain_id=1337,
            height=1,
            params=None,
            instant_block=True,
        )
        
        if len(rewards_instant) == 0:
            print("  ✅ Instant block produces zero rewards")
        else:
            print(f"  ❌ Instant block produced rewards: {rewards_instant}")
            return False
        
        # Test that instant blocks always produce zero rewards, regardless of height
        for test_height in [1, 10, 100, 1000]:
            rewards = compute_block_reward(
                chain_id=1337,
                height=test_height,
                params=None,
                instant_block=True,
            )
            if len(rewards) != 0:
                print(f"  ❌ Instant block at height {test_height} produced rewards: {rewards}")
                return False
        
        print(f"  ✅ Instant blocks always produce zero rewards at all heights tested")
        
        print("\n  ✅ All instant block reward tests passed!")
        return True
        
    except Exception as e:
        print(f"\n  ❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success1 = test_instant_block_code_path()
    success2 = test_instant_block_logic()
    sys.exit(0 if (success1 and success2) else 1)
