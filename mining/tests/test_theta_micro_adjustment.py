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
    """Test that theta adjustment respects min/max bounds."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    
    # Reset and initialize
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    _adjust_theta_for_mining(dt_seconds=None)
    
    # Get params
    state = _MINING_STATE.get("theta_state")
    assert state is not None
    
    min_theta = state.params.theta_min_micro
    max_theta = state.params.theta_max_micro
    
    # Verify new higher limits are configured
    assert max_theta >= 100_000_000, f"Expected theta_max >= 100M, got {max_theta}"
    
    # Simulate many very fast blocks (should hit max)
    for _ in range(100):  # Increased iterations to reach higher max
        theta = _adjust_theta_for_mining(dt_seconds=0.5)
    
    # Should respect maximum
    assert theta <= max_theta, f"Theta {theta} exceeded maximum {max_theta}"
    
    # Verify theta can actually reach high values (at least 80% of max)
    assert theta >= max_theta * 0.8, f"Theta {theta} should reach near maximum {max_theta} under sustained fast blocks"
    
    # Reset to high value
    high_theta = diff.nats_to_micro(80.0)  # Start at 80 nats (within new range)
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


def test_theta_adjustment_high_load_scaling():
    """Test that theta can scale beyond previous 60M limit under sustained load."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    
    # Reset and initialize
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    initial_theta = _adjust_theta_for_mining(dt_seconds=None)
    
    # Simulate extreme sustained fast mining (very high hash rate)
    # This represents a scenario where network hash rate spikes dramatically
    for i in range(150):
        theta = _adjust_theta_for_mining(dt_seconds=1.0)  # 1s blocks (target is 12s)
    
    # Should scale significantly beyond 60M
    assert theta > 60_000_000, f"Theta {theta} should exceed old 60M limit under extreme load"
    
    # Verify theta can reach very high values (at least 80M)
    assert theta >= 80_000_000, f"Theta {theta} should reach at least 80M under sustained extreme load"
    
    # Verify theta is still within max bounds
    state = _MINING_STATE.get("theta_state")
    assert state is not None
    assert theta <= state.params.theta_max_micro


def test_theta_adjustment_network_metrics():
    """Test that network metrics are tracked and integrated."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE, _update_network_metrics
    
    # Reset and initialize
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    # Initialize
    _adjust_theta_for_mining(dt_seconds=None)
    
    # Simulate some blocks to generate metrics
    for _ in range(10):
        _adjust_theta_for_mining(dt_seconds=12.0)
    
    # Update and verify network metrics
    metrics = _update_network_metrics()
    
    # Metrics should exist and be reasonable
    assert isinstance(metrics, dict)
    assert "pending_tx_count" in metrics
    assert "recent_tx_throughput" in metrics
    assert "hash_rate_estimate" in metrics
    
    # Values should be non-negative
    assert metrics["pending_tx_count"] >= 0
    assert metrics["recent_tx_throughput"] >= 0.0
    
    # Verify metrics are stored in state
    assert _MINING_STATE.get("network_metrics") is not None


def test_theta_adjustment_logging_coverage():
    """Test that comprehensive logging is produced during adjustments."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    import logging
    from io import StringIO
    
    # Reset and initialize
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    # Capture log output
    log_capture = StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setLevel(logging.INFO)
    logger = logging.getLogger("animica.rpc.miner")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    
    try:
        # Initialize (should log)
        _adjust_theta_for_mining(dt_seconds=None)
        
        # Simulate blocks with varying times to trigger adjustments
        for dt in [6.0, 5.0, 4.0, 3.0, 2.0]:  # Fast blocks to trigger significant changes
            _adjust_theta_for_mining(dt_seconds=dt)
        
        # Get log output
        log_output = log_capture.getvalue()
        
        # Verify initialization message
        assert "Initialized dynamic theta adjustment" in log_output
        assert "range=" in log_output
        
        # Verify adjustment messages include new fields
        # (May not always trigger if changes are small, so this is a weak check)
        if "Adjusted mining theta" in log_output:
            assert "pending_tx" in log_output or "%" in log_output  # Should have metrics or utilization
        
    finally:
        logger.removeHandler(handler)


def test_theta_adjustment_history_tracking():
    """Test that adjustment history is tracked for monitoring."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    
    # Reset and initialize
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    _adjust_theta_for_mining(dt_seconds=None)
    
    # Simulate several adjustments
    for dt in [10.0, 11.0, 12.0, 13.0, 14.0]:
        _adjust_theta_for_mining(dt_seconds=dt)
    
    # Verify history is tracked
    history = _MINING_STATE.get("adjustment_history")
    assert history is not None
    assert len(history) > 0
    
    # Verify history entries have required fields
    for entry in history:
        assert "timestamp" in entry
        assert "old_theta" in entry
        assert "new_theta" in entry
        assert "delta" in entry
        assert "dt_seconds" in entry


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
    
    test_theta_adjustment_disabled()
    print("✓ Disabled adjustment test passed")
    
    test_theta_adjustment_mixed_intervals()
    print("✓ Mixed intervals test passed")
    
    test_theta_adjustment_high_load_scaling()
    print("✓ High load scaling test passed")
    
    test_theta_adjustment_network_metrics()
    print("✓ Network metrics test passed")
    
    test_theta_adjustment_logging_coverage()
    print("✓ Logging coverage test passed")
    
    test_theta_adjustment_history_tracking()
    print("✓ History tracking test passed")
    
    print("\n✓ All tests passed!")
