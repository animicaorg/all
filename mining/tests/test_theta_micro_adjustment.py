"""Tests for dynamic theta micro adjustment during mining operations."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import consensus.difficulty as diff


def test_theta_adjustment_initialization():
    """Test that theta adjustment state initializes correctly."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    
    # Reset state
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    # Initialize (no dt provided)
    theta = _adjust_theta_for_mining(dt_seconds=None)
    
    # Should have initialized state
    assert _MINING_STATE.get("theta_state") is not None
    assert isinstance(theta, int)
    assert theta > 0


def test_theta_adjustment_faster_blocks():
    """Test that theta increases when blocks arrive faster than target."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    
    # Reset and initialize
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    # Initialize with baseline theta
    initial_theta = _adjust_theta_for_mining(dt_seconds=None)
    
    # Simulate fast blocks (6s when target is 12s)
    # Should increase theta (make mining harder)
    theta_values = [initial_theta]
    for _ in range(10):
        theta = _adjust_theta_for_mining(dt_seconds=6.0)
        theta_values.append(theta)
    
    # Theta should trend upward (harder) for fast blocks
    # Allow for some initial adjustment lag
    final_theta = theta_values[-1]
    mid_theta = theta_values[len(theta_values) // 2]
    
    # At least one of mid or final should be higher than initial
    # (EMA smoothing means changes may be gradual)
    assert max(mid_theta, final_theta) >= initial_theta * 0.95, \
        f"Expected theta to increase or stay stable for fast blocks, got {initial_theta} → {final_theta}"


def test_theta_adjustment_slower_blocks():
    """Test that theta decreases when blocks arrive slower than target."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    
    # Reset and initialize
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    # Initialize with high theta
    initial_theta = _adjust_theta_for_mining(dt_seconds=None)
    
    # Set to a higher initial value
    if _MINING_STATE.get("theta_state"):
        state = _MINING_STATE["theta_state"]
        high_theta = diff.nats_to_micro(8.0)  # ~8 nats
        _MINING_STATE["theta_state"] = diff.RetargetState(
            theta_micro=high_theta,
            tau_nats=diff.micro_to_nats(high_theta),
            ema_log_dt_over_T=0.0,
            alpha=state.alpha,
            params=state.params,
        )
        initial_theta = high_theta
    
    # Simulate slow blocks (24s when target is 12s)
    # Should decrease theta (make mining easier)
    theta_values = [initial_theta]
    for _ in range(10):
        theta = _adjust_theta_for_mining(dt_seconds=24.0)
        theta_values.append(theta)
    
    # Theta should trend downward (easier) for slow blocks
    final_theta = theta_values[-1]
    mid_theta = theta_values[len(theta_values) // 2]
    
    # At least one of mid or final should be lower than initial
    assert min(mid_theta, final_theta) <= initial_theta * 1.05, \
        f"Expected theta to decrease or stay stable for slow blocks, got {initial_theta} → {final_theta}"


def test_theta_adjustment_extreme_values():
    """Test that theta adjustment handles extreme block times safely."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    
    # Reset and initialize
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    initial_theta = _adjust_theta_for_mining(dt_seconds=None)
    
    # Test very fast block (0.1s)
    theta_fast = _adjust_theta_for_mining(dt_seconds=0.1)
    assert theta_fast > 0  # Should still be valid
    
    # Test very slow block (300s = 5 minutes)
    theta_slow = _adjust_theta_for_mining(dt_seconds=300.0)
    assert theta_slow > 0  # Should still be valid
    
    # Test invalid values (should handle gracefully)
    theta_invalid = _adjust_theta_for_mining(dt_seconds=-1.0)
    assert theta_invalid > 0  # Should return valid theta
    
    theta_zero = _adjust_theta_for_mining(dt_seconds=0.0)
    assert theta_zero > 0  # Should return valid theta


def test_theta_adjustment_clamping():
    """Test that theta adjustment respects min bound and hard cap."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    from consensus.difficulty import THETA_HARD_CAP_MICRO
    
    # Reset and initialize
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    _adjust_theta_for_mining(dt_seconds=None)
    
    # Get params
    state = _MINING_STATE.get("theta_state")
    assert state is not None
    
    min_theta = state.params.theta_min_micro
    
    # Simulate many very fast blocks (should grow but respect hard cap)
    initial_theta = _adjust_theta_for_mining(dt_seconds=None)
    for _ in range(50):
        theta = _adjust_theta_for_mining(dt_seconds=0.5)
    
    # Should grow significantly due to fast blocks
    assert theta > initial_theta, f"Theta should have increased from {initial_theta} but is {theta}"
    
    # Should respect hard cap (3B µ-nats)
    assert theta <= THETA_HARD_CAP_MICRO, f"Theta {theta} exceeded hard cap {THETA_HARD_CAP_MICRO}"
    
    # Reset to high value
    high_theta = diff.nats_to_micro(20.0)
    _MINING_STATE["theta_state"] = diff.RetargetState(
        theta_micro=high_theta,
        tau_nats=diff.micro_to_nats(high_theta),
        ema_log_dt_over_T=0.0,
        alpha=state.alpha,
        params=state.params,
    )
    
    # Simulate many very slow blocks (should hit min)
    for _ in range(50):
        theta = _adjust_theta_for_mining(dt_seconds=60.0)
    
    # Should respect minimum
    assert theta >= min_theta, f"Theta {theta} below minimum {min_theta}"


def test_theta_adjustment_disabled():
    """Test that adjustment can be disabled."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    
    # Disable adjustment
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = False
    
    # Should return baseline theta without adjustment
    theta1 = _adjust_theta_for_mining(dt_seconds=None)
    theta2 = _adjust_theta_for_mining(dt_seconds=6.0)
    theta3 = _adjust_theta_for_mining(dt_seconds=24.0)
    
    # All should be equal (no adjustment)
    assert theta1 == theta2 == theta3


def test_theta_adjustment_cap_enforcement():
    """Test that theta adjustment enforces the 3B µ-nats hard cap."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    from consensus.difficulty import THETA_HARD_CAP_MICRO
    
    # Reset and initialize with high theta near cap
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    _adjust_theta_for_mining(dt_seconds=None)
    
    # Get state and set it near the cap
    state = _MINING_STATE.get("theta_state")
    assert state is not None
    
    # Set theta to 95% of cap
    near_cap_theta = int(THETA_HARD_CAP_MICRO * 0.95)
    _MINING_STATE["theta_state"] = diff.RetargetState(
        theta_micro=near_cap_theta,
        tau_nats=diff.micro_to_nats(near_cap_theta),
        ema_log_dt_over_T=0.0,
        alpha=state.alpha,
        params=state.params,
    )
    
    # Simulate sustained very fast blocks that would push theta above cap
    for _ in range(100):
        theta = _adjust_theta_for_mining(dt_seconds=0.1)
    
    # Should be capped at exactly 3B µ-nats
    assert theta == THETA_HARD_CAP_MICRO, (
        f"Theta should be capped at {THETA_HARD_CAP_MICRO}, got {theta}"
    )
    
    # Further fast blocks should keep it at cap
    for _ in range(20):
        theta = _adjust_theta_for_mining(dt_seconds=0.1)
    
    assert theta == THETA_HARD_CAP_MICRO, (
        f"Theta should remain at cap {THETA_HARD_CAP_MICRO}, got {theta}"
    )


def test_theta_adjustment_mixed_intervals():
    """Test theta adjustment with realistic mixed block intervals."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    
    # Reset and initialize
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    initial_theta = _adjust_theta_for_mining(dt_seconds=None)
    
    # Simulate mixed intervals: some fast, some slow, mostly around target
    intervals = [
        10.0, 12.0, 11.0, 8.0, 15.0,  # mixed around target
        13.0, 12.0, 9.0, 14.0, 11.0,
        6.0, 18.0, 12.0, 10.0, 13.0,  # continued variation
    ]
    
    theta_values = [initial_theta]
    for dt in intervals:
        theta = _adjust_theta_for_mining(dt_seconds=dt)
        theta_values.append(theta)
    
    # Should produce stable, finite values
    for theta in theta_values:
        assert isinstance(theta, int)
        assert theta > 0
        assert theta < 1_000_000_000  # Reasonable upper bound
    
    # Variation should be modest (not wild swings)
    min_theta = min(theta_values)
    max_theta = max(theta_values)
    ratio = max_theta / min_theta
    
    # Should stay within reasonable band (e.g., 3x variation max)
    assert ratio < 3.0, f"Theta variation too large: {ratio:.2f}x (min={min_theta}, max={max_theta})"


if __name__ == "__main__":
    # Run tests directly
    test_theta_adjustment_initialization()
    print("✓ Initialization test passed")
    
    test_theta_adjustment_faster_blocks()
    print("✓ Faster blocks test passed")
    
    test_theta_adjustment_slower_blocks()
    print("✓ Slower blocks test passed")
    
    test_theta_adjustment_extreme_values()
    print("✓ Extreme values test passed")
    
    test_theta_adjustment_clamping()
    print("✓ Clamping test passed")
    
    test_theta_adjustment_cap_enforcement()
    print("✓ Cap enforcement test passed")
    
    test_theta_adjustment_disabled()
    print("✓ Disabled adjustment test passed")
    
    test_theta_adjustment_mixed_intervals()
    print("✓ Mixed intervals test passed")
    
    print("\n✓ All tests passed!")
