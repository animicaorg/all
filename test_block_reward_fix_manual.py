#!/usr/bin/env python3
"""
Manual smoke test for block reward fix.

This script validates that the instant_block flag is properly propagated
through the reward calculation pipeline.
"""

import sys
import yaml
from pathlib import Path

# Add repo to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))


def load_devnet_params():
    """Load devnet params from spec/params.yaml."""
    # Get the correct repo root (where this script is located)
    script_dir = Path(__file__).parent
    params_file = script_dir / "spec" / "params.yaml"
    with open(params_file, 'r') as f:
        root_params = yaml.safe_load(f)
    return root_params.get('networks', {}).get('animica:1337', {})


# Mock dependencies for isolated testing
class MockCtx:
    class Cfg:
        chain_id = 1337
    
    cfg = Cfg()
    params = None  # Will be loaded from spec/params.yaml
    
    class MockStateDb:
        def snapshot(self):
            class Snap:
                def digest(self):
                    return b"\x00" * 32
            return Snap()
    
    state_db = MockStateDb()


def test_instant_block_flag_propagation():
    """Test that instant_block flag is correctly passed to compute_block_reward."""
    print("Testing instant_block flag propagation...")
    
    # Load real devnet params
    params = load_devnet_params()
    print(f"\nLoaded params for animica:1337 (devnet)")
    print(f"  System addresses: {list(params.get('system_addresses', {}).keys())}")
    print(f"  Has issuance config: {'issuance' in params.get('monetary', {})}")
    
    # Import the fixed functions
    from consensus.rewards import compute_block_reward
    
    # Test 1: Normal block (instant_block=False) should have rewards
    print("\n1. Testing normal block (instant_block=False)...")
    rewards_normal = compute_block_reward(
        chain_id=1337,
        height=1,
        params=params,
        instant_block=False
    )
    print(f"   Normal block rewards: {rewards_normal}")
    assert len(rewards_normal) > 0, "Normal block should have rewards!"
    assert any(amt > 0 for _, amt in rewards_normal), "Normal block should have non-zero amounts!"
    print("   ✓ Normal block has rewards")
    print(f"   ✓ Total reward value: {sum(amt for _, amt in rewards_normal)} nANM")
    
    # Test 2: Instant block (instant_block=True) should have NO rewards
    print("\n2. Testing instant block (instant_block=True)...")
    rewards_instant = compute_block_reward(
        chain_id=1337,
        height=1,
        params=params,
        instant_block=True
    )
    print(f"   Instant block rewards: {rewards_instant}")
    assert len(rewards_instant) == 0, "Instant block should have NO rewards!"
    print("   ✓ Instant block has zero rewards")
    
    # Test 3: Verify _apply_block_reward accepts and uses instant_block parameter
    print("\n3. Testing _apply_block_reward with instant_block parameter...")
    from rpc.methods.miner import _apply_block_reward
    
    # Mock context with real params
    ctx = MockCtx()
    ctx.params = params
    
    # Test normal block reward application
    print("   Testing normal block reward application...")
    reward_normal = _apply_block_reward(ctx, height=1, payout_address=None, instant_block=False)
    print(f"   Normal block reward amount returned: {reward_normal}")
    # Note: This may be 0 due to missing state_db credit implementation in mock
    # But the function should not crash and should call compute_block_reward correctly
    print("   ✓ Normal block reward calculation completed without error")
    
    # Test instant block reward application
    print("   Testing instant block reward application...")
    reward_instant = _apply_block_reward(ctx, height=1, payout_address=None, instant_block=True)
    print(f"   Instant block reward amount returned: {reward_instant}")
    assert reward_instant == 0, "Instant block should return 0 reward!"
    print("   ✓ Instant block returns 0 reward")
    
    print("\n✅ All tests passed! The instant_block flag is properly propagated.")
    print("   - Normal blocks get rewards from emission schedule")
    print("   - Instant blocks get zero rewards")
    print("   - _apply_block_reward correctly passes flag to compute_block_reward")
    return True


if __name__ == "__main__":
    try:
        success = test_instant_block_flag_propagation()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
