"""Tests for device parameter handling in mining CLI."""

from __future__ import annotations

from animica.cli import mining
from typer.testing import CliRunner

runner = CliRunner()


def test_device_validation_rejects_invalid() -> None:
    """Test that invalid device types are rejected early."""
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


def test_device_validation_accepts_cpu() -> None:
    """Test that 'cpu' device is accepted."""
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "anim1test",
            "--count", "1",
            "--device", "cpu",
            "--rpc-url", "http://invalid-will-fail",  # Will fail at RPC connection
        ],
    )
    
    # Should not fail validation (will fail at RPC connection which is expected)
    # Exit code 2 = validation error (bad), 5 = connection error (good for this test)
    assert result.exit_code != 2 or "unsupported device" not in result.output.lower()


def test_device_validation_accepts_auto() -> None:
    """Test that 'auto' device is accepted."""
    result = runner.invoke(
        mining.app,
        [
            "mine-blocks",
            "anim1test",
            "--count", "1",
            "--device", "auto",
            "--rpc-url", "http://invalid-will-fail",  # Will fail at RPC connection
        ],
    )
    
    # Should not fail validation (will fail at RPC connection which is expected)
    # Exit code 2 = validation error (bad), 5 = connection error (good for this test)
    assert result.exit_code != 2 or "unsupported device" not in result.output.lower()


def test_device_validation_accepts_all_supported() -> None:
    """Test that all supported device types are accepted."""
    supported_devices = ["cpu", "cuda", "rocm", "opencl", "metal", "auto"]
    
    for device in supported_devices:
        result = runner.invoke(
            mining.app,
            [
                "mine-blocks",
                "anim1test",
                "--count", "1",
                "--device", device,
                "--rpc-url", "http://invalid-will-fail",
            ],
        )
        
        # Should not fail device validation
        assert "unsupported device" not in result.output.lower(), f"Device '{device}' should be supported but was rejected"
