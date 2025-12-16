"""Tests for unbounded theta micro behavior under extreme conditions."""

from __future__ import annotations

import consensus.difficulty as diff


def test_theta_can_grow_beyond_old_limits():
    """Test that theta can now grow beyond the previous 60M limit but respects 300M hard cap."""
    # Use default params (theta_max_micro=None uses hard cap)
    params = diff.RetargetParams(
        target_block_time_s=12.0,
        half_life_blocks=8.0,
        gain_beta=0.9,
        step_clamp_micro=5_000_000,  # Large step for faster growth in test
        theta_min_micro=300_000,
        theta_max_micro=None,  # None = use hard cap (300M)
    )
    
    # Start at a high value (old max was 60M)
    theta_init = 60_000_000  # 60 nats
    state = diff.init_state(params, theta_init_micro=theta_init)
    
    # Simulate sustained fast blocks (1s when target is 12s)
    for _ in range(50):
        state = diff.update_theta(state, dt_seconds=1.0)
    
    # Theta should have grown beyond the old 60M limit
    assert state.theta_micro > 60_000_000, (
        f"Theta should grow beyond old 60M limit, got {state.theta_micro}"
    )
    
    # But should respect the hard cap (300M µ-nats)
    assert state.theta_micro <= diff.THETA_HARD_CAP_MICRO, (
        f"Theta {state.theta_micro} exceeded hard cap {diff.THETA_HARD_CAP_MICRO}"
    )


def test_theta_respects_hard_cap():
    """Test that hard cap prevents theta from exceeding 300M µ-nats."""
    params = diff.RetargetParams(
        target_block_time_s=12.0,
        half_life_blocks=4.0,  # Very fast adaptation
        gain_beta=1.5,  # Very aggressive
        step_clamp_micro=50_000_000,  # Large step (50M micro-nats)
        theta_min_micro=1_000_000,
        theta_max_micro=None,  # None = use hard cap
    )
    
    # Start near hard cap
    theta_init = 280_000_000  # 280M µ-nats (close to 300M cap)
    state = diff.init_state(params, theta_init_micro=theta_init)
    
    # Try to push it beyond cap with sustained fast blocks
    for _ in range(20):
        state = diff.update_theta(state, dt_seconds=0.1)
    
    # Should not exceed hard cap
    assert state.theta_micro <= diff.THETA_HARD_CAP_MICRO, (
        f"Theta {state.theta_micro} exceeded hard cap {diff.THETA_HARD_CAP_MICRO}"
    )
    
    # Should be exactly at cap
    assert state.theta_micro == diff.THETA_HARD_CAP_MICRO, (
        f"Theta should stabilize at cap, got {state.theta_micro}"
    )


def test_theta_with_max_specified_still_works():
    """Test that specifying theta_max_micro still works (backward compatibility)."""
    max_value = 40_000_000  # 40 nats
    params = diff.RetargetParams(
        target_block_time_s=12.0,
        half_life_blocks=16.0,
        gain_beta=0.75,
        step_clamp_micro=1_000_000,
        theta_min_micro=500_000,
        theta_max_micro=max_value,  # Explicitly set max
    )
    
    theta_init = 30_000_000  # 30 nats
    state = diff.init_state(params, theta_init_micro=theta_init)
    
    # Simulate very fast blocks
    for _ in range(100):
        state = diff.update_theta(state, dt_seconds=1.0)
    
    # Should not exceed the specified maximum
    assert state.theta_micro <= max_value, (
        f"Theta {state.theta_micro} exceeded specified maximum {max_value}"
    )


def test_step_clamp_prevents_wild_fluctuations():
    """Test that step_clamp_micro prevents wild single-block swings even when unbounded."""
    step_clamp = 2_000_000  # 2.0 nats max change per block
    params = diff.RetargetParams(
        target_block_time_s=12.0,
        half_life_blocks=16.0,
        gain_beta=0.8,
        step_clamp_micro=step_clamp,
        theta_min_micro=1_000_000,
        theta_max_micro=None,  # Unbounded
    )
    
    theta_init = 10_000_000  # 10 nats
    state = diff.init_state(params, theta_init_micro=theta_init)
    
    # Single extremely fast block
    prev_theta = state.theta_micro
    state = diff.update_theta(state, dt_seconds=0.01)  # 100x faster than target
    
    # Change should be limited by step clamp
    delta = abs(state.theta_micro - prev_theta)
    assert delta <= step_clamp * 1.1, (  # Allow 10% margin for rounding
        f"Single-block change {delta} exceeded step clamp {step_clamp}"
    )


def test_theta_with_hard_cap_converges_under_normal_load():
    """Test that theta with hard cap still converges to stable values under normal conditions."""
    params = diff.RetargetParams(
        target_block_time_s=12.0,
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=500_000,
        theta_min_micro=500_000,
        theta_max_micro=None,  # None = use hard cap
    )
    
    theta_init = 5_000_000  # 5 nats
    state = diff.init_state(params, theta_init_micro=theta_init)
    
    # Simulate blocks arriving exactly at target interval
    thetas = [state.theta_micro]
    for _ in range(100):
        state = diff.update_theta(state, dt_seconds=12.0)
        thetas.append(state.theta_micro)
    
    # Theta should stabilize (not drift indefinitely)
    early_thetas = thetas[10:30]
    late_thetas = thetas[-20:]
    
    early_avg = sum(early_thetas) / len(early_thetas)
    late_avg = sum(late_thetas) / len(late_thetas)
    
    # Should not drift more than 20% over 70 blocks at target
    drift_ratio = abs(late_avg - early_avg) / early_avg
    assert drift_ratio < 0.2, (
        f"Theta drifted {drift_ratio:.1%} over time, should be stable at target interval"
    )


def test_theta_with_hard_cap_handles_extreme_variance():
    """Test that theta with hard cap handles extreme interval variance without instability."""
    params = diff.RetargetParams(
        target_block_time_s=12.0,
        half_life_blocks=16.0,
        gain_beta=0.8,
        step_clamp_micro=1_000_000,
        theta_min_micro=300_000,
        theta_max_micro=None,  # None = use hard cap
    )
    
    theta_init = 8_000_000  # 8 nats
    state = diff.init_state(params, theta_init_micro=theta_init)
    
    # Alternate between very fast and very slow blocks
    thetas = [state.theta_micro]
    for i in range(50):
        if i % 2 == 0:
            dt = 1.0  # Very fast
        else:
            dt = 30.0  # Very slow
        state = diff.update_theta(state, dt_seconds=dt)
        thetas.append(state.theta_micro)
    
    # Should produce stable finite values (no NaN, no negative)
    for theta in thetas:
        assert isinstance(theta, int), f"Theta should be int, got {type(theta)}"
        assert theta > 0, f"Theta should be positive, got {theta}"
        assert theta < diff.MAX_SAFE_THETA_MICRO, f"Theta {theta} exceeded safe limit"
    
    # Variation should be bounded despite extreme variance
    theta_min, theta_max = min(thetas), max(thetas)
    ratio = theta_max / theta_min
    assert ratio < 10.0, (
        f"Theta variation {ratio:.1f}x too large under extreme variance"
    )


if __name__ == "__main__":
    test_theta_can_grow_beyond_old_limits()
    print("✓ Theta can grow beyond old limits (up to 300M cap)")
    
    test_theta_respects_hard_cap()
    print("✓ Theta respects 300M hard cap")
    
    test_theta_with_max_specified_still_works()
    print("✓ Backward compatibility with specified max")
    
    test_step_clamp_prevents_wild_fluctuations()
    print("✓ Step clamp prevents wild fluctuations")
    
    test_theta_with_hard_cap_converges_under_normal_load()
    print("✓ Theta with hard cap converges under normal load")
    
    test_theta_with_hard_cap_handles_extreme_variance()
    print("✓ Theta with hard cap handles extreme variance")
    
    print("\n✓ All theta tests passed!")
