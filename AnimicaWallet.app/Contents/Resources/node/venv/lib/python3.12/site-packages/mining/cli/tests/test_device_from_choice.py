"""Tests for _device_from_choice function in miner CLI.

This test ensures that the device selection logic correctly uses
the auto_detect_device() function from the device module.
"""

from mining.cli import miner
from mining.device import DeviceType


def test_device_from_choice_auto():
    """Test that 'auto' choice calls auto_detect_device and returns a valid device type."""
    device = miner._device_from_choice('auto')
    
    # Should return a valid device type string
    assert isinstance(device, str)
    assert device in [
        DeviceType.CPU,
        DeviceType.CUDA,
        DeviceType.ROCM,
        DeviceType.OPENCL,
        DeviceType.METAL,
    ]


def test_device_from_choice_explicit_cpu():
    """Test that explicit 'cpu' choice returns 'cpu'."""
    device = miner._device_from_choice('cpu')
    assert device == 'cpu'


def test_device_from_choice_explicit_cuda():
    """Test that explicit 'cuda' choice returns 'cuda'."""
    device = miner._device_from_choice('cuda')
    assert device == 'cuda'


def test_device_from_choice_explicit_metal():
    """Test that explicit 'metal' choice returns 'metal'."""
    device = miner._device_from_choice('metal')
    assert device == 'metal'


def test_device_from_choice_explicit_opencl():
    """Test that explicit 'opencl' choice returns 'opencl'."""
    device = miner._device_from_choice('opencl')
    assert device == 'opencl'


def test_device_from_choice_explicit_rocm():
    """Test that explicit 'rocm' choice returns 'rocm'."""
    device = miner._device_from_choice('rocm')
    assert device == 'rocm'
