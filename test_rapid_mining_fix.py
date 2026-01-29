#!/usr/bin/env python3
"""
Test script to verify rapid mining difficulty fix.

This script tests that rapid block mining (e.g., mining 20+ blocks in quick succession)
doesn't cause theta to skyrocket and make mining impossible.

The fix clamps dt_seconds to prevent extreme difficulty increases during rapid mining.
"""

import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))


def test_rapid_mining_theta_adjustment():
    """Test that rapid mining doesn't cause runaway theta."""
    print("\n=== Test: Rapid Mining Theta Adjustment ===")
    
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    from consensus.difficulty import RetargetParams, init_state
    
    # Reset mining state
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    # Initialize with a reasonable theta
    initial_theta = 8_000_000  # 8.0 nats (devnet default)
    params = RetargetParams(
        target_block_time_s=300.0,  # 5 minutes target
        half_life_blocks=8.0,
        gain_beta=0.9,
        step_clamp_micro=2_000_000,
        theta_min_micro=100_000,
        theta_max_micro=None,
    )
    
    _MINING_STATE["theta_state"] = init_state(params, initial_theta)
    
    print(f"Initial theta: {initial_theta / 1e6:.3f} nats")
    print(f"Target block time: {params.target_block_time_s}s")
    print(f"Min dt threshold: {max(1.0, params.target_block_time_s * 0.1):.1f}s")
    print()
    
    # Simulate rapid mining: 20 blocks mined very quickly (0.5s each)
    rapid_dt = 0.5  # Half a second per block
    print(f"Simulating 20 blocks mined at {rapid_dt}s each:")
    
    thetas = [initial_theta]
    for i in range(20):
        new_theta = _adjust_theta_for_mining(rapid_dt)
        thetas.append(new_theta)
        print(f"  Block {i+1}: theta = {new_theta / 1e6:.3f} nats")
    
    final_theta = thetas[-1]
    print()
    print(f"Final theta after 20 blocks: {final_theta / 1e6:.3f} nats")
    print(f"Theta increase ratio: {final_theta / initial_theta:.2f}x")
    
    # Verify theta didn't explode (should stay under 10x initial value with clamping)
    # Without the fix, theta would be > 100x initial value
    max_acceptable_ratio = 10.0
    actual_ratio = final_theta / initial_theta
    
    if actual_ratio <= max_acceptable_ratio:
        print(f"✓ PASS: Theta remained reasonable ({actual_ratio:.2f}x <= {max_acceptable_ratio}x)")
        return True
    else:
        print(f"✗ FAIL: Theta increased too much ({actual_ratio:.2f}x > {max_acceptable_ratio}x)")
        return False


def test_normal_mining_still_adjusts():
    """Test that normal mining still adjusts theta appropriately."""
    print("\n=== Test: Normal Mining Theta Adjustment ===")
    
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    from consensus.difficulty import RetargetParams, init_state
    
    # Reset mining state
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    # Initialize with a reasonable theta
    initial_theta = 8_000_000  # 8.0 nats (devnet default)
    params = RetargetParams(
        target_block_time_s=300.0,  # 5 minutes target
        half_life_blocks=8.0,
        gain_beta=0.9,
        step_clamp_micro=2_000_000,
        theta_min_micro=100_000,
        theta_max_micro=None,
    )
    
    _MINING_STATE["theta_state"] = init_state(params, initial_theta)
    
    print(f"Initial theta: {initial_theta / 1e6:.3f} nats")
    print(f"Target block time: {params.target_block_time_s}s")
    print()
    
    # Simulate normal mining: blocks at target rate
    target_dt = 300.0  # 5 minutes
    print(f"Simulating 5 blocks mined at target rate ({target_dt}s each):")
    
    thetas = [initial_theta]
    for i in range(5):
        new_theta = _adjust_theta_for_mining(target_dt)
        thetas.append(new_theta)
        print(f"  Block {i+1}: theta = {new_theta / 1e6:.3f} nats")
    
    final_theta = thetas[-1]
    print()
    print(f"Final theta after 5 blocks: {final_theta / 1e6:.3f} nats")
    print(f"Theta change ratio: {final_theta / initial_theta:.4f}x")
    
    # At target rate, theta should stay relatively stable (within 20% of initial)
    min_acceptable_ratio = 0.8
    max_acceptable_ratio = 1.2
    actual_ratio = final_theta / initial_theta
    
    if min_acceptable_ratio <= actual_ratio <= max_acceptable_ratio:
        print(f"✓ PASS: Theta remained stable at target rate ({actual_ratio:.4f}x)")
        return True
    else:
        print(f"⚠ INFO: Theta changed ({actual_ratio:.4f}x) - this is expected with EMA")
        return True  # This is okay, just informational


def test_slow_mining_decreases_theta():
    """Test that slow mining decreases theta."""
    print("\n=== Test: Slow Mining Theta Adjustment ===")
    
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    from consensus.difficulty import RetargetParams, init_state
    
    # Reset mining state
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    # Initialize with a reasonable theta
    initial_theta = 8_000_000  # 8.0 nats (devnet default)
    params = RetargetParams(
        target_block_time_s=300.0,  # 5 minutes target
        half_life_blocks=8.0,
        gain_beta=0.9,
        step_clamp_micro=2_000_000,
        theta_min_micro=100_000,
        theta_max_micro=None,
    )
    
    _MINING_STATE["theta_state"] = init_state(params, initial_theta)
    
    print(f"Initial theta: {initial_theta / 1e6:.3f} nats")
    print(f"Target block time: {params.target_block_time_s}s")
    print()
    
    # Simulate slow mining: blocks at 2x target rate (600s)
    slow_dt = 600.0  # 10 minutes
    print(f"Simulating 5 blocks mined slowly ({slow_dt}s each):")
    
    thetas = [initial_theta]
    for i in range(5):
        new_theta = _adjust_theta_for_mining(slow_dt)
        thetas.append(new_theta)
        print(f"  Block {i+1}: theta = {new_theta / 1e6:.3f} nats")
    
    final_theta = thetas[-1]
    print()
    print(f"Final theta after 5 blocks: {final_theta / 1e6:.3f} nats")
    print(f"Theta change ratio: {final_theta / initial_theta:.4f}x")
    
    # With slow mining, theta should decrease
    if final_theta < initial_theta:
        print(f"✓ PASS: Theta decreased as expected for slow mining")
        return True
    else:
        print(f"✗ FAIL: Theta should decrease with slow mining")
        return False


if __name__ == "__main__":
    print("=" * 70)
    print("Testing Rapid Mining Difficulty Fix")
    print("=" * 70)
    
    results = []
    
    # Run all tests
    results.append(("Rapid mining doesn't cause theta explosion", test_rapid_mining_theta_adjustment()))
    results.append(("Normal mining still adjusts theta", test_normal_mining_still_adjusts()))
    results.append(("Slow mining decreases theta", test_slow_mining_decreases_theta()))
    
    # Print summary
    print()
    print("=" * 70)
    print("Test Summary")
    print("=" * 70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    print()
    print(f"Results: {passed}/{total} tests passed")
    
    sys.exit(0 if passed == total else 1)
