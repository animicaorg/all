"""Tests for configuration schema and roundtrip."""

import json
import tempfile
from pathlib import Path

import pytest

from animica_miner_gui.backend.config import (
    MiningAppConfig,
    NetworkType,
    MiningMode,
    DeviceType,
    load_config,
    save_config,
)


def test_config_defaults():
    """Test default configuration values."""
    config = MiningAppConfig()
    
    assert config.version == "1.0"
    assert config.network.network_type == NetworkType.DEVNET
    assert config.network.rpc_url == "http://127.0.0.1:8545"
    assert config.miner.mining_mode == MiningMode.SOLO
    assert config.cpu.enabled is True
    assert config.ui.dark_theme is True


def test_config_roundtrip():
    """Test configuration serialization and deserialization."""
    config = MiningAppConfig()
    config.network.network_type = NetworkType.TESTNET
    config.network.rpc_url = "https://test.example.com"
    config.miner.payout_address = "anim1test123456789012345678901234567890abc"
    config.cpu.threads = 4
    
    # Convert to dict and back
    data = config.model_dump()
    restored = MiningAppConfig(**data)
    
    assert restored.network.network_type == NetworkType.TESTNET
    assert restored.network.rpc_url == "https://test.example.com"
    assert restored.miner.payout_address == "anim1test123456789012345678901234567890abc"
    assert restored.cpu.threads == 4


def test_config_json_schema():
    """Test JSON schema generation."""
    schema_json = MiningAppConfig.get_schema_json()
    schema = json.loads(schema_json)
    
    assert "properties" in schema
    assert "network" in schema["properties"]
    assert "miner" in schema["properties"]
    assert "cpu" in schema["properties"]


def test_config_file_io():
    """Test configuration file I/O."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "test_config.json"
        
        # Create and save config
        config = MiningAppConfig()
        config.network.network_type = NetworkType.MAINNET
        config.miner.payout_address = "anim1mainnet123456789012345678901234567890abc"
        config.to_file(config_path)
        
        # Check file exists
        assert config_path.exists()
        
        # Load config
        loaded = MiningAppConfig.from_file(config_path)
        
        assert loaded.network.network_type == NetworkType.MAINNET
        assert loaded.miner.payout_address == "anim1mainnet123456789012345678901234567890abc"


def test_payout_address_validation():
    """Test payout address validation."""
    config = MiningAppConfig()
    
    # Invalid: empty
    config.miner.payout_address = ""
    assert not config.validate_payout_address()
    
    # Invalid: wrong prefix
    config.miner.payout_address = "test123456789012345678901234567890abc"
    assert not config.validate_payout_address()
    
    # Invalid: too short
    config.miner.payout_address = "anim1short"
    assert not config.validate_payout_address()
    
    # Valid
    config.miner.payout_address = "anim1valid123456789012345678901234567890abc"
    assert config.validate_payout_address()


def test_cpu_threads_auto_detect():
    """Test CPU threads auto-detection."""
    from animica_miner_gui.backend.config import CPUConfig
    
    config = CPUConfig(threads=0)
    assert config.threads > 0  # Should be auto-detected


def test_gpu_config():
    """Test GPU configuration."""
    from animica_miner_gui.backend.config import GPUConfig
    
    gpu = GPUConfig(
        device_id=0,
        enabled=True,
        intensity=0.8,
        worksize=256,
        name="Test GPU",
        vendor="Test Vendor"
    )
    
    assert gpu.device_id == 0
    assert gpu.enabled is True
    assert gpu.intensity == 0.8
    assert gpu.worksize == 256
    assert gpu.name == "Test GPU"


def test_config_with_gpus():
    """Test configuration with multiple GPUs."""
    from animica_miner_gui.backend.config import GPUConfig
    
    config = MiningAppConfig()
    config.gpus = [
        GPUConfig(device_id=0, name="GPU 0"),
        GPUConfig(device_id=1, name="GPU 1", intensity=0.5),
    ]
    
    # Roundtrip
    data = config.model_dump()
    restored = MiningAppConfig(**data)
    
    assert len(restored.gpus) == 2
    assert restored.gpus[0].device_id == 0
    assert restored.gpus[1].intensity == 0.5
