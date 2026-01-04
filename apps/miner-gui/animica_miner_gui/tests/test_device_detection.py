"""Tests for device detection (with mocking)."""

import pytest
from unittest.mock import Mock, patch, mock_open

from animica_miner_gui.backend.device_detection import (
    detect_cpu,
    detect_gpus,
    detect_all,
    get_safe_mode_config,
)


def test_detect_cpu_basic():
    """Test basic CPU detection."""
    cpu_info = detect_cpu()
    
    assert cpu_info.cores >= 1
    assert cpu_info.threads >= 1
    assert cpu_info.model_name is not None
    assert cpu_info.recommended_threads >= 1


@patch('os.path.exists')
@patch('builtins.open', new_callable=mock_open, read_data="1")
def test_detect_cpu_in_container(mock_file, mock_exists):
    """Test CPU detection in container."""
    def exists_side_effect(path):
        return path == "/.dockerenv"
    
    mock_exists.side_effect = exists_side_effect
    
    cpu_info = detect_cpu()
    
    # Should detect container
    assert cpu_info.in_container is True


def test_detect_gpus_no_opencl():
    """Test GPU detection when OpenCL is not available."""
    with patch('animica_miner_gui.backend.device_detection.pyopencl', None):
        gpus, has_opencl = detect_gpus()
        
        assert has_opencl is False
        assert len(gpus) == 0


@patch('animica_miner_gui.backend.device_detection.detect_cpu')
@patch('animica_miner_gui.backend.device_detection.detect_gpus')
def test_detect_all_mock(mock_detect_gpus, mock_detect_cpu):
    """Test complete detection with mocked components."""
    from animica_miner_gui.backend.device_detection import CPUInfo, GPUInfo
    
    # Mock CPU detection
    mock_cpu = CPUInfo(
        cores=8,
        threads=16,
        model_name="Test CPU",
        vendor="TestVendor",
        hugepages_available=False,
        recommended_threads=15,
        in_container=False,
        container_cpu_limit=None
    )
    mock_detect_cpu.return_value = mock_cpu
    
    # Mock GPU detection
    mock_gpu = GPUInfo(
        device_id=0,
        name="Test GPU",
        vendor="TestVendor",
        compute_units=32,
        memory_mb=8192,
        driver_version="1.0",
        opencl_version="2.0",
        recommended=True
    )
    mock_detect_gpus.return_value = ([mock_gpu], True)
    
    # Run detection
    result = detect_all()
    
    assert result.cpu.cores == 8
    assert len(result.gpus) == 1
    assert result.has_opencl is True
    assert len(result.recommendations) > 0


def test_get_safe_mode_config():
    """Test safe mode configuration generation."""
    from animica_miner_gui.backend.device_detection import DetectionResult, CPUInfo
    
    # Normal configuration
    cpu = CPUInfo(
        cores=8,
        threads=16,
        model_name="Test",
        vendor="Test",
        hugepages_available=False,
        recommended_threads=15,
        in_container=False,
        container_cpu_limit=None
    )
    
    detection = DetectionResult(
        cpu=cpu,
        gpus=[],
        has_opencl=False,
        warnings=[],
        recommendations=[]
    )
    
    config = get_safe_mode_config(detection)
    
    assert config['enable_safe_mode'] is False
    assert config['cpu_threads'] >= 1


def test_get_safe_mode_config_low_resources():
    """Test safe mode config with low resources."""
    from animica_miner_gui.backend.device_detection import DetectionResult, CPUInfo
    
    # Container with limited CPUs
    cpu = CPUInfo(
        cores=1,
        threads=1,
        model_name="Test",
        vendor="Test",
        hugepages_available=False,
        recommended_threads=1,
        in_container=True,
        container_cpu_limit=1.5
    )
    
    detection = DetectionResult(
        cpu=cpu,
        gpus=[],
        has_opencl=False,
        warnings=["Low available memory: 2.0 GB"],
        recommendations=[]
    )
    
    config = get_safe_mode_config(detection)
    
    assert config['enable_safe_mode'] is True
