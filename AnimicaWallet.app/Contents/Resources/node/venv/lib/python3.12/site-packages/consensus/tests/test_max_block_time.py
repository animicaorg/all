"""
Test max block time emergency difficulty reduction.

This test verifies that when block times exceed the configured maximum,
difficulty drops to minimum to enable fast recovery.
"""

import pytest
from consensus.difficulty import (
    RetargetParams,
    RetargetState,
    init_state,
    update_theta,
)


def test_normal_block_time_no_emergency():
    """Test that normal block times don't trigger emergency mode."""
    params = RetargetParams(
        target_block_time_s=300.0,  # 5 minute target
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=400_000,
        theta_min_micro=500_000,
        theta_max_micro=None,
        max_block_time_s=3600.0,  # 1 hour max
    )
    
    state = init_state(params, theta_init_micro=3_000_000)
    
    # Normal block time (slightly slower than target)
    state = update_theta(state, dt_seconds=360.0)
    
    # Theta should decrease slightly but stay well above minimum
    assert state.theta_micro > params.theta_min_micro
    assert state.theta_micro < 3_000_000  # Should be lower than initial


def test_emergency_mode_on_max_block_time_exceeded():
    """Test that exceeding max block time triggers emergency difficulty reduction."""
    params = RetargetParams(
        target_block_time_s=300.0,  # 5 minute target
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=400_000,
        theta_min_micro=500_000,
        theta_max_micro=None,
        max_block_time_s=3600.0,  # 1 hour max
    )
    
    state = init_state(params, theta_init_micro=3_000_000)
    
    # Simulate a very long block time (over 1 hour)
    state = update_theta(state, dt_seconds=3700.0)
    
    # Theta should drop to minimum immediately
    assert state.theta_micro == params.theta_min_micro
    
    # EMA should reflect the very slow block
    assert state.ema_log_dt_over_T > 2.0  # ln(3700/300) ≈ 2.51


def test_recovery_after_emergency():
    """Test that difficulty can recover after emergency mode."""
    params = RetargetParams(
        target_block_time_s=300.0,
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=400_000,
        theta_min_micro=500_000,
        theta_max_micro=None,
        max_block_time_s=3600.0,
    )
    
    state = init_state(params, theta_init_micro=3_000_000)
    
    # Emergency: very long block time
    state = update_theta(state, dt_seconds=4000.0)
    assert state.theta_micro == params.theta_min_micro
    
    # Recovery: blocks significantly faster than target to pull EMA negative
    # This simulates miners finding blocks quickly due to low difficulty
    for _ in range(100):
        state = update_theta(state, dt_seconds=100.0)  # Much faster than 300s target
    
    # After many fast blocks, theta should increase above minimum
    assert state.theta_micro > params.theta_min_micro


def test_no_max_block_time_never_triggers_emergency():
    """Test that without max_block_time_s, emergency mode never triggers."""
    params = RetargetParams(
        target_block_time_s=300.0,
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=400_000,
        theta_min_micro=500_000,
        theta_max_micro=None,
        max_block_time_s=None,  # No max block time
    )
    
    state = init_state(params, theta_init_micro=3_000_000)
    
    # Very long block time
    state = update_theta(state, dt_seconds=10000.0)
    
    # Should not drop to minimum immediately (normal retargeting applies)
    # But difficulty will decrease according to normal EMA rules
    assert state.theta_micro < 3_000_000
    # Won't be at minimum due to step clamp
    assert state.theta_micro > params.theta_min_micro


def test_multiple_emergency_triggers():
    """Test multiple consecutive emergency triggers."""
    params = RetargetParams(
        target_block_time_s=300.0,
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=400_000,
        theta_min_micro=500_000,
        theta_max_micro=None,
        max_block_time_s=3600.0,
    )
    
    state = init_state(params, theta_init_micro=3_000_000)
    
    # First emergency
    state = update_theta(state, dt_seconds=3700.0)
    assert state.theta_micro == params.theta_min_micro
    
    # Another block that also exceeds max
    state = update_theta(state, dt_seconds=4000.0)
    assert state.theta_micro == params.theta_min_micro
    
    # Should stay at minimum
    assert state.theta_micro == params.theta_min_micro


def test_edge_case_exactly_at_max():
    """Test behavior when block time is exactly at the maximum."""
    params = RetargetParams(
        target_block_time_s=300.0,
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=400_000,
        theta_min_micro=500_000,
        theta_max_micro=None,
        max_block_time_s=3600.0,
    )
    
    state = init_state(params, theta_init_micro=3_000_000)
    
    # Exactly at max (should NOT trigger emergency)
    state = update_theta(state, dt_seconds=3600.0)
    
    # Should use normal retargeting, not emergency
    assert state.theta_micro > params.theta_min_micro
    
    # Just over max (should trigger emergency)
    state = update_theta(state, dt_seconds=3600.1)
    assert state.theta_micro == params.theta_min_micro


def test_backwards_compatibility():
    """Test that existing code without max_block_time_s still works."""
    # Old-style params without max_block_time_s
    params = RetargetParams(
        target_block_time_s=300.0,
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=400_000,
        theta_min_micro=500_000,
        theta_max_micro=None,
        # max_block_time_s not specified (None by default)
    )
    
    state = init_state(params, theta_init_micro=3_000_000)
    
    # Any block time should work with normal retargeting
    state = update_theta(state, dt_seconds=600.0)
    assert state.theta_micro < 3_000_000
    
    state = update_theta(state, dt_seconds=150.0)
    assert state.theta_micro > params.theta_min_micro


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
