"""Tests for PTL service initialization in RPC deps."""

from __future__ import annotations

import tempfile
import os
from pathlib import Path

import pytest


def test_ptl_service_initialized_on_startup():
    """Test that PTL service is initialized during RPC startup."""
    from rpc import deps
    from rpc.config import Config
    
    # Create temporary database for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        genesis_path = Path(__file__).parents[2] / "genesis" / "devnet.json"
        
        # Enable PTL explicitly
        original_ptl_enable = os.environ.get("ANIMICA_PTL_ENABLE")
        original_tx_system = os.environ.get("ANIMICA_TX_SYSTEM")
        ctx = None
        
        try:
            os.environ["ANIMICA_PTL_ENABLE"] = "1"
            os.environ["ANIMICA_TX_SYSTEM"] = "ptl"
            
            cfg = Config(
                chain_id=1337,
                db_uri=f"sqlite:///{db_path}",
                genesis_path=str(genesis_path),
                host="127.0.0.1",
                port=8545,
            )
            
            # Build context which should initialize PTL
            ctx = deps.build_context(cfg)
            
            # Check that PTL service was registered
            ptl_service = deps.get("ptl_service")
            assert ptl_service is not None, "PTL service should be registered"
            
            # Verify it has the expected interface
            assert hasattr(ptl_service, "submit"), "PTL service should have submit method"
            assert hasattr(ptl_service, "get"), "PTL service should have get method"
            assert hasattr(ptl_service, "store"), "PTL service should have store attribute"
            
        finally:
            # Restore environment
            if original_ptl_enable is not None:
                os.environ["ANIMICA_PTL_ENABLE"] = original_ptl_enable
            else:
                os.environ.pop("ANIMICA_PTL_ENABLE", None)
            
            if original_tx_system is not None:
                os.environ["ANIMICA_TX_SYSTEM"] = original_tx_system
            else:
                os.environ.pop("ANIMICA_TX_SYSTEM", None)
            
            # Clean up context
            if ctx:
                ctx.close()


def test_ptl_disabled_when_not_enabled():
    """Test that PTL service is not initialized when disabled."""
    from rpc import deps
    from rpc.config import Config
    
    # Create temporary database for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        genesis_path = Path(__file__).parents[2] / "genesis" / "devnet.json"
        
        # Disable PTL explicitly
        original_ptl_enable = os.environ.get("ANIMICA_PTL_ENABLE")
        original_tx_system = os.environ.get("ANIMICA_TX_SYSTEM")
        ctx = None
        
        try:
            os.environ["ANIMICA_PTL_ENABLE"] = "0"
            os.environ["ANIMICA_TX_SYSTEM"] = "mempool"
            
            cfg = Config(
                chain_id=1337,
                db_uri=f"sqlite:///{db_path}",
                genesis_path=str(genesis_path),
                host="127.0.0.1",
                port=8545,
            )
            
            # Build context
            ctx = deps.build_context(cfg)
            
            # Check that PTL service was NOT registered
            ptl_service = deps.get("ptl_service")
            assert ptl_service is None, "PTL service should not be registered when disabled"
            
        finally:
            # Restore environment
            if original_ptl_enable is not None:
                os.environ["ANIMICA_PTL_ENABLE"] = original_ptl_enable
            else:
                os.environ.pop("ANIMICA_PTL_ENABLE", None)
            
            if original_tx_system is not None:
                os.environ["ANIMICA_TX_SYSTEM"] = original_tx_system
            else:
                os.environ.pop("ANIMICA_TX_SYSTEM", None)
            
            # Clean up context
            if ctx:
                ctx.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
