"""Unit tests for PTL configuration."""

import os
from unittest.mock import patch

from core.ptl.config import PtlConfig


def test_from_env_defaults_when_not_set():
    """Test that tx_system defaults to 'ptl' when ANIMICA_TX_SYSTEM is not set."""
    with patch.dict(os.environ, {}, clear=False):
        # Remove ANIMICA_TX_SYSTEM if it exists
        os.environ.pop("ANIMICA_TX_SYSTEM", None)
        config = PtlConfig.from_env()
        assert config.tx_system == "ptl"


def test_from_env_defaults_when_empty_string():
    """Test that tx_system defaults to 'ptl' when ANIMICA_TX_SYSTEM is set to empty string."""
    with patch.dict(os.environ, {"ANIMICA_TX_SYSTEM": ""}, clear=False):
        config = PtlConfig.from_env()
        assert config.tx_system == "ptl"


def test_from_env_respects_explicit_value():
    """Test that tx_system respects an explicit value when set."""
    with patch.dict(os.environ, {"ANIMICA_TX_SYSTEM": "mempool"}, clear=False):
        config = PtlConfig.from_env()
        assert config.tx_system == "mempool"


def test_from_env_preserves_other_defaults():
    """Test that other default values remain unchanged."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ANIMICA_TX_SYSTEM", None)
        os.environ.pop("ANIMICA_PTL_MIN_PEER_ACKS", None)
        os.environ.pop("ANIMICA_PTL_TTL_SECONDS", None)
        
        config = PtlConfig.from_env()
        
        assert config.tx_system == "ptl"
        assert config.min_peer_acks == 2
        assert config.ttl_seconds == 3600


def test_use_ptl_lowercasing():
    """Test that use_ptl() properly lowercases the tx_system value."""
    # Test with uppercase
    config = PtlConfig(tx_system="PTL")
    assert config.use_ptl() is True
    
    # Test with mixed case
    config = PtlConfig(tx_system="Ptl")
    assert config.use_ptl() is True
    
    # Test with lowercase
    config = PtlConfig(tx_system="ptl")
    assert config.use_ptl() is True
    
    # Test with mempool
    config = PtlConfig(tx_system="mempool")
    assert config.use_ptl() is False


def test_use_mempool_lowercasing():
    """Test that use_mempool() properly lowercases the tx_system value."""
    # Test with uppercase
    config = PtlConfig(tx_system="MEMPOOL")
    assert config.use_mempool() is True
    
    # Test with mixed case
    config = PtlConfig(tx_system="MemPool")
    assert config.use_mempool() is True
    
    # Test with lowercase
    config = PtlConfig(tx_system="mempool")
    assert config.use_mempool() is True
    
    # Test with ptl
    config = PtlConfig(tx_system="ptl")
    assert config.use_mempool() is False


def test_from_env_with_custom_values():
    """Test that from_env() respects custom environment variable values."""
    env_vars = {
        "ANIMICA_TX_SYSTEM": "mempool",
        "ANIMICA_PTL_MIN_PEER_ACKS": "5",
        "ANIMICA_PTL_TTL_SECONDS": "7200",
        "ANIMICA_PTL_DB_PATH": "/custom/path/to/db",
        "ANIMICA_PTL_RECONCILE_INTERVAL_S": "20.0",
        "ANIMICA_PTL_ANNOUNCE_BATCH_SIZE": "200",
        "ANIMICA_PTL_ANNOUNCE_INTERVAL_S": "2.0",
        "ANIMICA_PTL_MAX_PUSH_BATCH": "100",
        "ANIMICA_PTL_MAX_BLOCK_SIZE": "2000000",
        "ANIMICA_PTL_MAX_BLOCK_GAS": "20000000",
    }
    
    with patch.dict(os.environ, env_vars, clear=False):
        config = PtlConfig.from_env()
        
        assert config.tx_system == "mempool"
        assert config.min_peer_acks == 5
        assert config.ttl_seconds == 7200
        assert config.db_path == "/custom/path/to/db"
        assert config.reconcile_interval_s == 20.0
        assert config.announce_batch_size == 200
        assert config.announce_interval_s == 2.0
        assert config.max_push_batch == 100
        assert config.max_block_size == 2000000
        assert config.max_block_gas == 20000000
