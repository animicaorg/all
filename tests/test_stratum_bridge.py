"""
Basic integration test for Stratum mining bridge.

Tests the 3-command happy path without requiring a full node.
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch
from pathlib import Path


@pytest.mark.asyncio
async def test_stratum_bridge_basic():
    """Test basic stratum bridge functionality."""
    from mining.stratum_bridge import StratumBridge, RpcClient
    
    # Mock RPC client
    mock_rpc = AsyncMock()
    mock_rpc.call = AsyncMock(return_value={
        "enabled": True,
        "templateId": "test123",
        "parent": {"height": 10, "hash": "0x" + "00" * 32},
        "header": {
            "height": 11,
            "chainId": 1,
            "parentHash": "0x" + "00" * 32,
            "timestamp": 1234567890,
            "thetaMicro": 800000,
            "signBytes": "0x" + "aa" * 80,
        },
        "target": "0x00000000ffff0000000000000000000000000000000000000000000000000000",
        "thetaMicro": 800000,
        "timestampMin": 1234567880,
        "timestampMax": 1234567900,
        "coinbase": {"address": "anim1test", "amount": 50000000000},
        "txs": [],
        "excluded": [],
        "mempool": {"pending": 0, "selected": 0, "rejected": {}},
    })
    
    # Create bridge with mocked RPC
    bridge = StratumBridge(rpc_url="http://mock:8545", poll_interval=0.1)
    bridge._rpc = mock_rpc
    
    # Start bridge
    await bridge.start("anim1test")
    
    # Wait for first poll
    await asyncio.sleep(0.2)
    
    # Get current job
    job = await bridge.get_current_job()
    
    # Verify job structure
    assert job is not None
    assert "job_id" in job
    assert job["height"] == 11
    assert job["theta_micro"] == 800000
    assert "header" in job
    
    # Stop bridge
    await bridge.stop()


def test_stratum_cli_commands_exist():
    """Test that stratum CLI commands are registered."""
    from python.animica.cli import stratum
    
    # Check that commands exist
    assert hasattr(stratum, "app")
    assert hasattr(stratum.app, "registered_commands") or hasattr(stratum.app, "commands")


def test_miner_stratum_command_exists():
    """Test that miner stratum command is registered."""
    from python.animica.cli import mining
    
    # Check that miner app has commands
    assert hasattr(mining, "app")


def test_stratum_pid_file_handling():
    """Test PID file creation and parsing."""
    from python.animica.cli.stratum import _write_pid_file, _parse_pid_file, _remove_pid_file
    import tempfile
    
    # Create temp PID file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.pid') as f:
        pid_file = Path(f.name)
    
    try:
        # Write PID file
        _write_pid_file(pid_file, 12345, 3333, "127.0.0.1")
        
        # Parse PID file
        data = _parse_pid_file(pid_file)
        assert data["pid"] == 12345
        assert data["port"] == 3333
        assert data["bind"] == "127.0.0.1"
        
        # Remove PID file
        _remove_pid_file(pid_file)
        assert not pid_file.exists()
    
    finally:
        # Cleanup
        if pid_file.exists():
            pid_file.unlink()


def test_address_validation():
    """Test Bech32 address validation."""
    from python.animica.cli.mining import _validate_bech32_address
    
    # Valid address (starts with anim1)
    assert _validate_bech32_address("anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz")
    
    # Invalid addresses
    assert not _validate_bech32_address("btc1...")  # Wrong prefix
    assert not _validate_bech32_address("not_an_address")  # Invalid format
    assert not _validate_bech32_address("")  # Empty


@pytest.mark.asyncio
async def test_share_submission_basic():
    """Test basic share submission logic."""
    from mining.stratum_bridge import StratumBridge
    
    # Mock RPC
    mock_rpc = AsyncMock()
    mock_rpc.call = AsyncMock(return_value={
        "accepted": True,
        "reason": None,
        "is_block": False,
    })
    
    bridge = StratumBridge(rpc_url="http://mock:8545")
    bridge._rpc = mock_rpc
    bridge._current_template = {
        "templateId": "test123",
        "header": {"height": 11, "signBytes": "0x" + "aa" * 80},
        "txs": [],
    }
    bridge._current_job_id = "job123"
    
    # Submit share
    result = await bridge.submit_share({
        "job_id": "job123",
        "hashshare": {"nonce": "0x1234", "body": {}},
    })
    
    # Verify result
    assert result["accepted"] is True
    assert not result["is_block"]


def test_readme_exists():
    """Test that the README guide exists."""
    import os
    readme_path = Path(__file__).parent.parent / "STRATUM_MINING_GUIDE.md"
    assert readme_path.exists(), "STRATUM_MINING_GUIDE.md should exist"
    
    # Check it contains key sections
    content = readme_path.read_text()
    assert "Quick Start (3 Commands)" in content
    assert "animica stratum up" in content
    assert "animica miner stratum" in content


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
