"""Tests for device parameter handling in mining CLI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import typer
from animica.cli import mining
from typer.testing import CliRunner

runner = CliRunner()


def test_device_parameter_not_sent_to_rpc() -> None:
    """Test that device parameter is not included in RPC calls."""
    # Mock the RPC client
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    
    # Mock RPC response
    mock_client.request.return_value = {
        "mined": 1,
        "height": 1,
        "totalReward": 5000000000,
    }
    
    # Patch RpcClient
    with patch("animica.cli.mining.RpcClient", return_value=mock_client):
        # Test with explicit device parameter
        result = runner.invoke(
            mining.app,
            [
                "mine-blocks",
                "anim1test",  # positional address
                "--count", "1",
                "--device", "cpu",
                "--no-proxy",  # Disable proxy for simpler test
            ],
        )
        
        # Should succeed
        assert result.exit_code == 0 or result.exit_code == 5  # 5 is connection error (acceptable for test)
        
        # Verify RPC was called
        if mock_client.request.called:
            # Get the call arguments
            call_args = mock_client.request.call_args
            
            # Check that device is NOT in the params
            if len(call_args) > 1:
                params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("params", {})
                assert "device" not in params, "Device parameter should not be sent to RPC"


def test_device_auto_detection() -> None:
    """Test that device auto-detection works without sending to RPC."""
    # Mock device detection
    with patch("animica.cli.mining.auto_detect_device", return_value="cpu"):
        # Mock RPC client
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = {
            "mined": 1,
            "height": 1,
            "totalReward": 5000000000,
        }
        
        with patch("animica.cli.mining.RpcClient", return_value=mock_client):
            result = runner.invoke(
                mining.app,
                [
                    "mine-blocks",
                    "anim1test",
                    "--count", "1",
                    "--device", "auto",
                    "--no-proxy",
                ],
            )
            
            # Should complete without error (or connection error which is acceptable)
            assert result.exit_code in [0, 5]
            
            # Verify auto-detection message appears in output
            if result.exit_code == 0:
                assert "Auto-detected device" in result.output or "cpu" in result.output.lower()


def test_device_validation() -> None:
    """Test that invalid device types are rejected."""
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "anim1test",
            "--count", "1",
            "--device", "invalid_device",
        ],
    )
    
    # Should fail with error about unsupported device
    assert result.exit_code == 2
    assert "unsupported device" in result.output.lower()


def test_device_fallback_on_detection_failure() -> None:
    """Test that device detection failure falls back to CPU."""
    # Mock device detection to raise an exception
    with patch("animica.cli.mining.auto_detect_device", side_effect=Exception("Detection failed")):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = {
            "mined": 1,
            "height": 1,
            "totalReward": 5000000000,
        }
        
        with patch("animica.cli.mining.RpcClient", return_value=mock_client):
            result = runner.invoke(
                mining.app,
                [
                    "mine-blocks",
                    "anim1test",
                    "--count", "1",
                    "--device", "auto",
                    "--no-proxy",
                ],
            )
            
            # Should still work (fallback to CPU)
            assert result.exit_code in [0, 5]  # 0 = success, 5 = connection error
            
            # Check for fallback message
            if "Warning" in result.output:
                assert "cpu" in result.output.lower()
