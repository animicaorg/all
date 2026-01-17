"""
Unit tests for VarDiff implementation.
"""

from __future__ import annotations

import time

import pytest

from animica.pool.vardiff import VarDiffConfig, VarDiffManager, VarDiffState


@pytest.fixture
def vardiff_config():
    """Create default VarDiff config for testing."""
    return VarDiffConfig(
        enabled=True,
        target_shares_per_min=10.0,
        retarget_sec=30.0,
        min_difficulty=0.01,
        max_difficulty=1.0,
        variance_percent=15.0,
        smoothing_alpha=0.2,
        window_sec=60.0,
    )


@pytest.fixture
def vardiff_manager(vardiff_config):
    """Create VarDiff manager for testing."""
    return VarDiffManager(vardiff_config)


def test_create_state(vardiff_manager):
    """Test creating VarDiff state for a connection."""
    connection_id = "conn_1"
    state = vardiff_manager.create_state(connection_id, initial_difficulty=0.5)

    assert state.connection_id == connection_id
    assert state.current_difficulty == 0.5
    assert len(state.share_timestamps) == 0
    assert state.retarget_count == 0

    # Verify state is stored
    retrieved = vardiff_manager.get_state(connection_id)
    assert retrieved is not None
    assert retrieved.connection_id == connection_id


def test_create_state_clamps_difficulty(vardiff_manager):
    """Test that initial difficulty is clamped to bounds."""
    # Test below minimum
    state1 = vardiff_manager.create_state("conn_1", initial_difficulty=0.001)
    assert state1.current_difficulty == 0.01  # Clamped to min

    # Test above maximum
    state2 = vardiff_manager.create_state("conn_2", initial_difficulty=10.0)
    assert state2.current_difficulty == 1.0  # Clamped to max


def test_remove_state(vardiff_manager):
    """Test removing VarDiff state."""
    connection_id = "conn_1"
    vardiff_manager.create_state(connection_id)

    assert vardiff_manager.get_state(connection_id) is not None

    vardiff_manager.remove_state(connection_id)

    assert vardiff_manager.get_state(connection_id) is None


def test_record_share(vardiff_manager):
    """Test recording shares for rate tracking."""
    connection_id = "conn_1"
    vardiff_manager.create_state(connection_id)

    # Record some shares
    for _ in range(5):
        vardiff_manager.record_share(connection_id)
        time.sleep(0.01)

    state = vardiff_manager.get_state(connection_id)
    assert len(state.share_timestamps) == 5


def test_share_window_pruning(vardiff_manager):
    """Test that old shares are pruned from window."""
    connection_id = "conn_1"
    state = vardiff_manager.create_state(connection_id)

    # Manually add old timestamps
    now = time.time()
    state.share_timestamps.append(now - 120)  # 2 minutes ago (outside window)
    state.share_timestamps.append(now - 90)  # 1.5 minutes ago (outside window)
    state.share_timestamps.append(now - 30)  # 30 seconds ago (inside window)
    state.share_timestamps.append(now - 10)  # 10 seconds ago (inside window)

    # Record a new share (triggers pruning)
    vardiff_manager.record_share(connection_id)

    # Should only have shares within window (60s) plus the new one
    state = vardiff_manager.get_state(connection_id)
    # The two old shares should be pruned
    assert len(state.share_timestamps) <= 3


def test_should_retarget(vardiff_manager):
    """Test retarget timing logic."""
    connection_id = "conn_1"
    state = vardiff_manager.create_state(connection_id)

    # Should not retarget immediately
    assert not vardiff_manager.should_retarget(connection_id)

    # Manually set last retarget time in the past
    state.last_retarget_time = time.time() - 35  # 35 seconds ago

    # Should now retarget (config is 30s)
    assert vardiff_manager.should_retarget(connection_id)


def test_calculate_new_difficulty_not_enough_shares(vardiff_manager):
    """Test that calculation requires minimum shares."""
    connection_id = "conn_1"
    vardiff_manager.create_state(connection_id, initial_difficulty=0.5)

    # Record only 1-2 shares (below minimum)
    vardiff_manager.record_share(connection_id)
    vardiff_manager.record_share(connection_id)

    # Should return None (not enough data)
    new_diff = vardiff_manager.calculate_new_difficulty(connection_id)
    assert new_diff is None


def test_calculate_new_difficulty_increase(vardiff_manager):
    """Test difficulty increases when share rate is too high."""
    connection_id = "conn_1"
    state = vardiff_manager.create_state(connection_id, initial_difficulty=0.5)

    # Simulate high share rate (20 shares/min, target is 10)
    # Add 20 shares within 60 seconds
    now = time.time()
    for i in range(20):
        state.share_timestamps.append(now - (60 - i * 3))

    # Force observed rate EMA to match
    state.observed_rate_ema = 20.0

    new_diff = vardiff_manager.calculate_new_difficulty(connection_id)

    # Should increase difficulty (roughly double)
    assert new_diff is not None
    assert new_diff > 0.5
    assert new_diff <= 1.0  # But clamped to max


def test_calculate_new_difficulty_decrease(vardiff_manager):
    """Test difficulty decreases when share rate is too low."""
    connection_id = "conn_1"
    state = vardiff_manager.create_state(connection_id, initial_difficulty=0.5)

    # Simulate low share rate (4 shares/min, target is 10)
    # This should cause ~60% decrease which exceeds 15% threshold
    now = time.time()
    # Add 6 shares within window to ensure we have enough (min is 5)
    for i in range(6):
        state.share_timestamps.append(now - (59 - i * 10))  # Spread over 59s

    # Don't force observed_rate_ema, let it be calculated
    # With 6 shares in 60s = 6/min, which is 60% of target
    # This should cause difficulty to decrease to 0.5 * 0.6 = 0.3

    new_diff = vardiff_manager.calculate_new_difficulty(connection_id)

    # Should decrease difficulty
    assert new_diff is not None
    assert new_diff < 0.5
    assert new_diff >= 0.01  # But clamped to min


def test_calculate_new_difficulty_hysteresis(vardiff_manager):
    """Test hysteresis prevents small changes."""
    connection_id = "conn_1"
    state = vardiff_manager.create_state(connection_id, initial_difficulty=0.5)

    # Simulate share rate just slightly off target (10.5 shares/min vs 10)
    now = time.time()
    for i in range(11):  # 11 shares in 60s ≈ 11/min
        state.share_timestamps.append(now - (60 - i * 5.5))

    state.observed_rate_ema = 10.5

    new_diff = vardiff_manager.calculate_new_difficulty(connection_id)

    # Should return None due to hysteresis (change < 15%)
    assert new_diff is None


def test_calculate_new_difficulty_clamps_to_min(vardiff_manager):
    """Test that new difficulty is clamped to minimum."""
    connection_id = "conn_1"
    state = vardiff_manager.create_state(connection_id, initial_difficulty=0.02)

    # Simulate very low share rate (1 share/min vs target 10)
    # This is 10% of target, so new_diff = 0.02 * 0.1 = 0.002, which should clamp to 0.01
    now = time.time()
    # Add 6 shares spread across window to ensure minimum count
    for i in range(6):
        state.share_timestamps.append(now - (59 - i * 10))

    # With 6 shares/min, ratio = 0.6, new_diff = 0.02 * 0.6 = 0.012
    # But we want to test clamping, so let's use smoothing_alpha=1.0 in a modified test
    # Actually, let's just set the observed_rate_ema after first calculation
    vardiff_manager.calculate_new_difficulty(connection_id)  # Initialize EMA
    state.observed_rate_ema = 1.0  # Override to force very low rate

    new_diff = vardiff_manager.calculate_new_difficulty(connection_id)

    # Should be clamped to minimum
    assert new_diff is not None
    assert new_diff == 0.01


def test_calculate_new_difficulty_clamps_to_max(vardiff_manager):
    """Test that new difficulty is clamped to maximum."""
    connection_id = "conn_1"
    state = vardiff_manager.create_state(connection_id, initial_difficulty=0.8)

    # Simulate very high share rate
    now = time.time()
    for i in range(50):
        state.share_timestamps.append(now - (60 - i * 1.2))

    state.observed_rate_ema = 50.0  # Very high

    new_diff = vardiff_manager.calculate_new_difficulty(connection_id)

    # Should be clamped to maximum
    assert new_diff is not None
    assert new_diff == 1.0


def test_apply_new_difficulty(vardiff_manager):
    """Test applying new difficulty updates state."""
    connection_id = "conn_1"
    state = vardiff_manager.create_state(connection_id, initial_difficulty=0.5)

    old_retarget_time = state.last_retarget_time
    old_retarget_count = state.retarget_count

    time.sleep(0.01)

    vardiff_manager.apply_new_difficulty(connection_id, 0.75)

    assert state.current_difficulty == 0.75
    assert state.last_retarget_time > old_retarget_time
    assert state.retarget_count == old_retarget_count + 1


def test_retarget_convenience_method(vardiff_manager):
    """Test the convenience retarget method."""
    connection_id = "conn_1"
    state = vardiff_manager.create_state(connection_id, initial_difficulty=0.5)

    # Not ready to retarget yet
    result = vardiff_manager.retarget(connection_id)
    assert result is None

    # Simulate time passing and high share rate
    state.last_retarget_time = time.time() - 35
    now = time.time()
    for i in range(20):
        state.share_timestamps.append(now - (60 - i * 3))
    state.observed_rate_ema = 20.0

    # Should retarget now
    result = vardiff_manager.retarget(connection_id)
    assert result is not None
    assert result > 0.5


def test_get_stats(vardiff_manager):
    """Test getting VarDiff statistics."""
    connection_id = "conn_1"
    vardiff_manager.create_state(connection_id, initial_difficulty=0.5)

    # Record some shares
    for _ in range(10):
        vardiff_manager.record_share(connection_id)

    stats = vardiff_manager.get_stats(connection_id)

    assert stats is not None
    assert stats["connection_id"] == connection_id
    assert stats["current_difficulty"] == 0.5
    assert stats["target_rate"] == 10.0
    assert stats["shares_in_window"] == 10
    assert stats["retarget_count"] == 0
    assert "observed_rate" in stats
    assert "time_since_retarget" in stats


def test_vardiff_disabled(vardiff_manager):
    """Test that VarDiff can be disabled."""
    vardiff_manager._config.enabled = False

    connection_id = "conn_1"
    vardiff_manager.create_state(connection_id, initial_difficulty=0.5)

    # Simulate conditions for retarget
    state = vardiff_manager.get_state(connection_id)
    state.last_retarget_time = time.time() - 35
    for _ in range(20):
        vardiff_manager.record_share(connection_id)
    state.observed_rate_ema = 20.0

    # Should not retarget when disabled
    new_diff = vardiff_manager.calculate_new_difficulty(connection_id)
    assert new_diff is None


def test_ema_smoothing(vardiff_manager):
    """Test EMA smoothing of observed rate."""
    connection_id = "conn_1"
    state = vardiff_manager.create_state(connection_id, initial_difficulty=0.5)

    # First observation
    now = time.time()
    for i in range(10):
        state.share_timestamps.append(now - (60 - i * 6))

    vardiff_manager.calculate_new_difficulty(connection_id)
    first_ema = state.observed_rate_ema
    assert first_ema > 0

    # Second observation (different rate)
    state.share_timestamps.clear()
    for i in range(20):
        state.share_timestamps.append(now - (60 - i * 3))

    vardiff_manager.calculate_new_difficulty(connection_id)
    second_ema = state.observed_rate_ema

    # EMA should change but be smoothed
    assert second_ema != first_ema
    # With alpha=0.2, new EMA = 0.2 * new_rate + 0.8 * old_ema
    # So it shouldn't jump directly to new rate
    assert abs(second_ema - 20.0) > 1.0  # Not exactly the new rate
