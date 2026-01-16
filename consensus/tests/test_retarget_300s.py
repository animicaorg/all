"""
Test difficulty retargeting with 5-minute block time.
"""

import pytest
from consensus.difficulty import (
    RetargetParams,
    init_state,
    update_theta,
    micro_to_nats,
)


def test_retarget_with_300s_target():
    """
    Test that difficulty retargets correctly with 300-second (5-minute) block time.
    """
    # Initialize with 5-minute target
    params = RetargetParams(
        target_block_time_s=300.0,  # 5 minutes
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=400_000,
        theta_min_micro=500_000,
        theta_max_micro=None,  # Unbounded
    )
    
    # Start at 1M µ-nats (1.0 nats)
    state = init_state(params, theta_init_micro=1_000_000)
    
    assert state.theta_micro == 1_000_000
    assert state.params.target_block_time_s == 300.0
    
    # Simulate blocks arriving faster than target (240s instead of 300s = 20% faster)
    # Should increase difficulty (theta)
    for _ in range(10):
        state = update_theta(state, dt_seconds=240.0)
    
    # Theta should have increased (higher difficulty when blocks come faster)
    assert state.theta_micro > 1_000_000, "Theta should increase when blocks arrive faster"
    
    # Simulate blocks arriving slower than target (360s instead of 300s = 20% slower)
    # Should decrease difficulty (theta)
    for _ in range(10):
        state = update_theta(state, dt_seconds=360.0)
    
    # After slow blocks, theta should be back down (but might not be exactly 1M due to EMA)
    # Just verify it decreased from the peak
    assert state.theta_micro < 1_500_000, "Theta should decrease when blocks arrive slower"


def test_retarget_stable_at_target():
    """
    Test that theta remains stable when blocks arrive at target interval.
    """
    params = RetargetParams(
        target_block_time_s=300.0,
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=400_000,
        theta_min_micro=500_000,
    )
    
    state = init_state(params, theta_init_micro=1_000_000)
    initial_theta = state.theta_micro
    
    # Simulate 50 blocks arriving exactly at target
    for _ in range(50):
        state = update_theta(state, dt_seconds=300.0)
    
    # Theta should be close to initial (within 10% due to rounding and EMA)
    assert abs(state.theta_micro - initial_theta) < initial_theta * 0.1, \
        f"Theta should remain stable at target: {state.theta_micro} vs {initial_theta}"


def test_retarget_min_max_bounds():
    """
    Test that theta respects min/max bounds with 5-minute target.
    """
    params = RetargetParams(
        target_block_time_s=300.0,
        half_life_blocks=24.0,
        gain_beta=0.75,
        step_clamp_micro=400_000,
        theta_min_micro=500_000,
        theta_max_micro=5_000_000,  # 5.0 nats max
    )
    
    # Start at minimum
    state = init_state(params, theta_init_micro=500_000)
    
    # Simulate many slow blocks (should try to decrease but hit min)
    for _ in range(100):
        state = update_theta(state, dt_seconds=600.0)  # 2x target
    
    # Should not go below min
    assert state.theta_micro >= params.theta_min_micro
    
    # Start near maximum
    state = init_state(params, theta_init_micro=4_900_000)
    
    # Simulate many fast blocks (should try to increase but hit max)
    for _ in range(100):
        state = update_theta(state, dt_seconds=150.0)  # 0.5x target
    
    # Should not exceed max
    assert state.theta_micro <= params.theta_max_micro


def test_consensus_params_integration():
    """
    Test that consensus.params values work correctly with retargeting.
    """
    from consensus import params as consensus_params
    
    # Verify consensus params are set correctly
    assert consensus_params.TARGET_BLOCK_TIME_SEC == 300.0, \
        "Consensus params should have 5-minute block time"
    
    # Create retarget params using consensus constants
    params = RetargetParams(
        target_block_time_s=consensus_params.TARGET_BLOCK_TIME_SEC,
        half_life_blocks=consensus_params.RETARGET_HALF_LIFE_BLOCKS,
        gain_beta=consensus_params.RETARGET_GAIN_BETA,
        step_clamp_micro=consensus_params.RETARGET_STEP_CLAMP_MICRO,
        theta_min_micro=consensus_params.RETARGET_THETA_MIN_MICRO,
    )
    
    state = init_state(params, theta_init_micro=consensus_params.GENESIS_THETA_MICRO)
    
    # Verify state was initialized correctly
    assert state.params.target_block_time_s == 300.0
    assert state.theta_micro == consensus_params.GENESIS_THETA_MICRO


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
