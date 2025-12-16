"""Tests for checkpoint configuration."""

import os
import pytest

from p2p.checkpoints.config import CheckpointsConfig, load_checkpoints_config


def test_default_config():
    """Test default checkpoint configuration."""
    config = CheckpointsConfig()
    
    assert config.mode == "off"
    assert config.rpc_url == "https://rpc.animica.org/rpc"
    assert config.file_path is None
    assert config.max_age_seconds is None
    assert config.strict is False
    
    assert not config.is_enabled()
    assert not config.requires_rpc()
    assert not config.requires_file()


def test_rpc_mode_config():
    """Test RPC mode configuration."""
    config = CheckpointsConfig(mode="rpc")
    
    assert config.is_enabled()
    assert config.requires_rpc()
    assert not config.requires_file()


def test_file_mode_config():
    """Test file mode configuration."""
    config = CheckpointsConfig(mode="file", file_path="/tmp/checkpoints.json")
    
    assert config.is_enabled()
    assert not config.requires_rpc()
    assert config.requires_file()


def test_load_config_from_env_default(monkeypatch):
    """Test loading default config from environment."""
    # Clear any existing checkpoint env vars
    for key in list(os.environ.keys()):
        if key.startswith("ANIMICA_CHECKPOINTS_"):
            monkeypatch.delenv(key, raising=False)
    
    config = load_checkpoints_config()
    
    assert config.mode == "off"
    assert not config.is_enabled()


def test_load_config_from_env_rpc_mode(monkeypatch):
    """Test loading RPC mode from environment."""
    monkeypatch.setenv("ANIMICA_CHECKPOINTS_MODE", "rpc")
    monkeypatch.setenv("ANIMICA_CHECKPOINTS_RPC_URL", "https://custom.rpc/endpoint")
    
    config = load_checkpoints_config()
    
    assert config.mode == "rpc"
    assert config.rpc_url == "https://custom.rpc/endpoint"
    assert config.is_enabled()


def test_load_config_from_env_file_mode(monkeypatch):
    """Test loading file mode from environment."""
    monkeypatch.setenv("ANIMICA_CHECKPOINTS_MODE", "file")
    monkeypatch.setenv("ANIMICA_CHECKPOINTS_FILE", "/tmp/test_checkpoints.json")
    
    config = load_checkpoints_config()
    
    assert config.mode == "file"
    assert config.file_path == "/tmp/test_checkpoints.json"
    assert config.is_enabled()


def test_load_config_strict_mode(monkeypatch):
    """Test loading strict mode from environment."""
    monkeypatch.setenv("ANIMICA_CHECKPOINTS_MODE", "rpc")
    monkeypatch.setenv("ANIMICA_CHECKPOINTS_STRICT", "true")
    
    config = load_checkpoints_config()
    
    assert config.strict is True


def test_load_config_max_age(monkeypatch):
    """Test loading max age from environment."""
    monkeypatch.setenv("ANIMICA_CHECKPOINTS_MODE", "rpc")
    monkeypatch.setenv("ANIMICA_CHECKPOINTS_MAX_AGE", "3600")
    
    config = load_checkpoints_config()
    
    assert config.max_age_seconds == 3600


def test_load_config_invalid_mode(monkeypatch):
    """Test that invalid mode falls back to 'off'."""
    monkeypatch.setenv("ANIMICA_CHECKPOINTS_MODE", "invalid")
    
    config = load_checkpoints_config()
    
    assert config.mode == "off"
    assert not config.is_enabled()


def test_load_config_expanduser(monkeypatch):
    """Test that file paths are expanded with expanduser."""
    monkeypatch.setenv("ANIMICA_CHECKPOINTS_MODE", "file")
    monkeypatch.setenv("ANIMICA_CHECKPOINTS_FILE", "~/checkpoints.json")
    
    config = load_checkpoints_config()
    
    # Should expand ~ to home directory
    assert config.file_path is not None
    assert not config.file_path.startswith("~")
    assert "checkpoints.json" in config.file_path
