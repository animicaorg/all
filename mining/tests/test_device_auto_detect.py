"""
Tests for device auto-detection functionality.

Validates that auto_detect_device() correctly prioritizes available devices
and falls back to CPU when no GPU backends are available.
"""

from __future__ import annotations

from typing import List
from unittest.mock import patch

import pytest

from mining.device import (
    DeviceInfo,
    DeviceType,
    auto_detect_device,
    list_available,
)


def test_auto_detect_device_cpu_only() -> None:
    """Test auto-detection when only CPU is available."""
    # Mock list_available to return only CPU
    cpu_device = DeviceInfo(
        type=DeviceType.CPU,
        name="CPU",
        index=0,
        vendor="generic",
        driver="python",
    )
    
    with patch("mining.device.list_available", return_value=[cpu_device]):
        detected = auto_detect_device()
        assert detected == DeviceType.CPU


def test_auto_detect_device_cuda_available() -> None:
    """Test auto-detection when CUDA GPU is available."""
    devices = [
        DeviceInfo(
            type=DeviceType.CUDA,
            name="NVIDIA GeForce RTX 3080",
            index=0,
            vendor="NVIDIA",
        ),
        DeviceInfo(
            type=DeviceType.CPU,
            name="CPU",
            index=0,
        ),
    ]
    
    with patch("mining.device.list_available", return_value=devices):
        detected = auto_detect_device()
        # Should prefer CUDA over CPU
        assert detected == DeviceType.CUDA


def test_auto_detect_device_rocm_available() -> None:
    """Test auto-detection when ROCm GPU is available."""
    devices = [
        DeviceInfo(
            type=DeviceType.ROCM,
            name="AMD Radeon RX 6800",
            index=0,
            vendor="AMD",
        ),
        DeviceInfo(
            type=DeviceType.CPU,
            name="CPU",
            index=0,
        ),
    ]
    
    with patch("mining.device.list_available", return_value=devices):
        detected = auto_detect_device()
        # Should prefer ROCm over CPU
        assert detected == DeviceType.ROCM


def test_auto_detect_device_opencl_available() -> None:
    """Test auto-detection when OpenCL device is available."""
    devices = [
        DeviceInfo(
            type=DeviceType.OPENCL,
            name="Generic OpenCL Device",
            index=0,
        ),
        DeviceInfo(
            type=DeviceType.CPU,
            name="CPU",
            index=0,
        ),
    ]
    
    with patch("mining.device.list_available", return_value=devices):
        detected = auto_detect_device()
        # Should prefer OpenCL over CPU
        assert detected == DeviceType.OPENCL


def test_auto_detect_device_metal_available() -> None:
    """Test auto-detection when Metal device is available (Apple Silicon)."""
    devices = [
        DeviceInfo(
            type=DeviceType.METAL,
            name="Apple M1",
            index=0,
            vendor="Apple",
        ),
        DeviceInfo(
            type=DeviceType.CPU,
            name="CPU",
            index=0,
        ),
    ]
    
    with patch("mining.device.list_available", return_value=devices):
        detected = auto_detect_device()
        # Should prefer Metal over CPU
        assert detected == DeviceType.METAL


def test_auto_detect_device_priority_cuda_over_opencl() -> None:
    """Test that CUDA is preferred over OpenCL when both are available."""
    devices = [
        DeviceInfo(
            type=DeviceType.CUDA,
            name="NVIDIA GPU",
            index=0,
            vendor="NVIDIA",
        ),
        DeviceInfo(
            type=DeviceType.OPENCL,
            name="OpenCL Device",
            index=0,
        ),
        DeviceInfo(
            type=DeviceType.CPU,
            name="CPU",
            index=0,
        ),
    ]
    
    with patch("mining.device.list_available", return_value=devices):
        detected = auto_detect_device()
        # CUDA should be selected over OpenCL and CPU
        assert detected == DeviceType.CUDA


def test_auto_detect_device_priority_cuda_over_rocm() -> None:
    """Test that CUDA is preferred over ROCm when both are available."""
    devices = [
        DeviceInfo(
            type=DeviceType.CUDA,
            name="NVIDIA GPU",
            index=0,
        ),
        DeviceInfo(
            type=DeviceType.ROCM,
            name="AMD GPU",
            index=0,
        ),
        DeviceInfo(
            type=DeviceType.CPU,
            name="CPU",
            index=0,
        ),
    ]
    
    with patch("mining.device.list_available", return_value=devices):
        detected = auto_detect_device()
        # CUDA should be selected over ROCm
        assert detected == DeviceType.CUDA


def test_auto_detect_device_priority_rocm_over_opencl() -> None:
    """Test that ROCm is preferred over OpenCL when both are available."""
    devices = [
        DeviceInfo(
            type=DeviceType.ROCM,
            name="AMD GPU",
            index=0,
        ),
        DeviceInfo(
            type=DeviceType.OPENCL,
            name="OpenCL Device",
            index=0,
        ),
        DeviceInfo(
            type=DeviceType.CPU,
            name="CPU",
            index=0,
        ),
    ]
    
    with patch("mining.device.list_available", return_value=devices):
        detected = auto_detect_device()
        # ROCm should be selected over OpenCL
        assert detected == DeviceType.ROCM


def test_auto_detect_device_priority_opencl_over_metal() -> None:
    """Test that OpenCL is preferred over Metal when both are available."""
    devices = [
        DeviceInfo(
            type=DeviceType.OPENCL,
            name="OpenCL Device",
            index=0,
        ),
        DeviceInfo(
            type=DeviceType.METAL,
            name="Metal Device",
            index=0,
        ),
        DeviceInfo(
            type=DeviceType.CPU,
            name="CPU",
            index=0,
        ),
    ]
    
    with patch("mining.device.list_available", return_value=devices):
        detected = auto_detect_device()
        # OpenCL should be selected over Metal
        assert detected == DeviceType.OPENCL


def test_auto_detect_device_priority_metal_over_cpu() -> None:
    """Test that Metal is preferred over CPU when both are available."""
    devices = [
        DeviceInfo(
            type=DeviceType.METAL,
            name="Metal Device",
            index=0,
        ),
        DeviceInfo(
            type=DeviceType.CPU,
            name="CPU",
            index=0,
        ),
    ]
    
    with patch("mining.device.list_available", return_value=devices):
        detected = auto_detect_device()
        # Metal should be selected over CPU
        assert detected == DeviceType.METAL


def test_auto_detect_device_empty_list_fallback() -> None:
    """Test fallback to CPU when no devices are returned (edge case)."""
    with patch("mining.device.list_available", return_value=[]):
        detected = auto_detect_device()
        # Should fallback to CPU
        assert detected == DeviceType.CPU


def test_auto_detect_device_multiple_same_type() -> None:
    """Test that auto-detection works when multiple devices of same type exist."""
    devices = [
        DeviceInfo(
            type=DeviceType.CUDA,
            name="NVIDIA GPU 0",
            index=0,
        ),
        DeviceInfo(
            type=DeviceType.CUDA,
            name="NVIDIA GPU 1",
            index=1,
        ),
        DeviceInfo(
            type=DeviceType.CPU,
            name="CPU",
            index=0,
        ),
    ]
    
    with patch("mining.device.list_available", return_value=devices):
        detected = auto_detect_device()
        # Should still return the device type (CUDA)
        assert detected == DeviceType.CUDA


def test_auto_detect_device_returns_string() -> None:
    """Test that auto_detect_device returns a string (device type identifier)."""
    detected = auto_detect_device()
    assert isinstance(detected, str)
    # Should be one of the known device types
    assert detected in [
        DeviceType.CPU,
        DeviceType.CUDA,
        DeviceType.ROCM,
        DeviceType.OPENCL,
        DeviceType.METAL,
    ]


def test_list_available_integration() -> None:
    """Integration test: verify list_available works in current environment."""
    # This test runs against the actual system
    devices = list_available()
    
    # Should always have at least CPU
    assert len(devices) > 0
    device_types = [d.type for d in devices]
    assert DeviceType.CPU in device_types
    
    # All devices should have required attributes
    for device in devices:
        assert isinstance(device.type, str)
        assert isinstance(device.name, str)
        assert isinstance(device.index, int)


def test_auto_detect_device_integration() -> None:
    """Integration test: verify auto_detect_device works in current environment."""
    # This test runs against the actual system
    detected = auto_detect_device()
    
    # Should return a valid device type
    assert detected in [
        DeviceType.CPU,
        DeviceType.CUDA,
        DeviceType.ROCM,
        DeviceType.OPENCL,
        DeviceType.METAL,
    ]
    
    # In CI/test environments without GPU, should be CPU
    # In systems with GPU, should be a GPU type
    available = list_available()
    available_types = {d.type for d in available}
    assert detected in available_types
