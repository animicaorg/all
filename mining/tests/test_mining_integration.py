"""Integration tests for mining with theta adjustment and threads flag."""

from __future__ import annotations

import os
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYTHONPATH = os.pathsep.join([str(_REPO_ROOT), str(_REPO_ROOT / "python")])


def test_cli_mine_blocks_help():
    """Test that mine-blocks command shows threads flag in help."""
    import subprocess
    
    result = subprocess.run(
        ["python3", "-m", "mining.cli.miner", "mine-blocks", "--help"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env={**os.environ, "PYTHONPATH": _PYTHONPATH},
    )
    
    assert result.returncode == 0
    assert "--threads" in result.stdout
    assert "CPU threads" in result.stdout or "threads" in result.stdout


def test_cli_start_help():
    """Test that start command shows threads flag in help (baseline)."""
    import subprocess
    
    result = subprocess.run(
        ["python3", "-m", "mining.cli.miner", "start", "--help"],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env={**os.environ, "PYTHONPATH": _PYTHONPATH},
    )
    
    assert result.returncode == 0
    assert "--threads" in result.stdout
    assert "worker threads" in result.stdout or "threads" in result.stdout


def test_theta_adjustment_state_persistence():
    """Test that theta adjustment state persists across multiple adjustments."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    
    # Reset state
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    # Initialize
    theta1 = _adjust_theta_for_mining(dt_seconds=None)
    state1 = _MINING_STATE.get("theta_state")
    
    # Make adjustments
    theta2 = _adjust_theta_for_mining(dt_seconds=10.0)
    state2 = _MINING_STATE.get("theta_state")
    
    theta3 = _adjust_theta_for_mining(dt_seconds=14.0)
    state3 = _MINING_STATE.get("theta_state")
    
    # State should be persistent and evolving
    assert state1 is not None
    assert state2 is not None
    assert state3 is not None
    
    # EMA accumulator should be evolving
    assert hasattr(state2, "ema_log_dt_over_T")
    assert hasattr(state3, "ema_log_dt_over_T")
    
    # Theta values should be reasonable
    assert all(isinstance(t, int) and t > 0 for t in [theta1, theta2, theta3])


def test_theta_adjustment_block_time_tracking():
    """Test that block times are tracked for monitoring."""
    from rpc.methods.miner import _adjust_theta_for_mining, _MINING_STATE
    
    # Reset state
    _MINING_STATE.clear()
    _MINING_STATE["adjustment_enabled"] = True
    
    # Initialize
    _adjust_theta_for_mining(dt_seconds=None)
    
    # Make several adjustments
    test_times = [10.0, 12.0, 11.0, 13.0, 9.0]
    for dt in test_times:
        _adjust_theta_for_mining(dt_seconds=dt)
    
    # Check that block times are tracked
    block_times = _MINING_STATE.get("block_times")
    
    assert block_times is not None
    assert len(block_times) > 0
    assert len(block_times) <= 20  # Should be limited to last 20 (deque maxlen)
    
    # Most recent times should match our test times
    # Convert deque to list for comparison
    recent = list(block_times)[-len(test_times):]
    # Use approximate comparison for floats
    assert len(recent) == len(test_times)
    for r, t in zip(recent, test_times):
        assert abs(r - t) < 1e-9  # Nearly equal


def test_rpc_method_accepts_threads():
    """Test that miner.mine RPC method accepts threads parameter."""
    from rpc.methods.miner import miner_mine
    from unittest.mock import MagicMock, patch
    
    # Mock context
    mock_ctx = MagicMock()
    mock_ctx.get_head = MagicMock(return_value={"height": 5, "hash": "0x123", "header": None})
    mock_ctx.cfg = MagicMock()
    mock_ctx.cfg.chain_id = 1337
    mock_ctx.state_db = None
    
    # Mock _mine_once to avoid actual mining
    with patch("rpc.methods.miner._ctx", return_value=mock_ctx):
        with patch("rpc.methods.miner._mine_once", return_value=(False, 0)):
            # Call with threads parameter
            result = miner_mine(count=1, address="anim1test", threads=4)
            
            # Should return valid result structure
            assert isinstance(result, dict)
            assert "mined" in result
            assert "height" in result
            assert "totalReward" in result


if __name__ == "__main__":
    # Run tests directly
    test_cli_mine_blocks_help()
    print("✓ CLI mine-blocks help test passed")
    
    test_cli_start_help()
    print("✓ CLI start help test passed")
    
    test_theta_adjustment_state_persistence()
    print("✓ Theta adjustment state persistence test passed")
    
    test_theta_adjustment_block_time_tracking()
    print("✓ Theta adjustment block time tracking test passed")
    
    test_rpc_method_accepts_threads()
    print("✓ RPC method accepts threads test passed")
    
    print("\n✓ All integration tests passed!")
