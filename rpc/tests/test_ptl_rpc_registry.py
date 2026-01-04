"""Tests for PTL RPC method registration."""

from __future__ import annotations

import pytest

from rpc.methods import ensure_loaded, get_methods


def test_ptl_replication_status_registered():
    """Test that ptl.replicationStatus is registered in RPC methods."""
    ensure_loaded()
    methods = get_methods()
    
    assert "ptl.replicationStatus" in methods, \
        "Canonical ptl.replicationStatus method must be registered"
    
    method = methods["ptl.replicationStatus"]
    assert method.name == "ptl.replicationStatus"
    assert callable(method.func)


def test_tx_replication_status_backward_compat():
    """Test that tx.replicationStatus is registered as backward-compatible alias."""
    ensure_loaded()
    methods = get_methods()
    
    assert "tx.replicationStatus" in methods, \
        "Backward-compatible tx.replicationStatus alias must be registered"
    
    method = methods["tx.replicationStatus"]
    assert method.name == "tx.replicationStatus"
    assert callable(method.func)


def test_ptl_methods_registered():
    """Test that all PTL methods are properly registered."""
    ensure_loaded()
    methods = get_methods()
    
    required_ptl_methods = [
        "ptl.replicationStatus",
        "tx.replicationStatus",  # backward compat
        "tx.submitRawTransaction",
        "tx.get",
        "tx.pending",
        "debug.ptlStats",
        "debug.ptlPeers",
    ]
    
    for method_name in required_ptl_methods:
        assert method_name in methods, \
            f"PTL method {method_name} must be registered"


def test_deps_get_state_db_adapter():
    """Test that rpc.deps.get_state_db_adapter is available (regression test)."""
    from rpc import deps
    
    assert hasattr(deps, "get_state_db_adapter"), \
        "rpc.deps.get_state_db_adapter must exist to fix mining AttributeError"
    
    assert callable(deps.get_state_db_adapter), \
        "get_state_db_adapter must be callable"


def test_deps_get_block_db():
    """Test that rpc.deps.get_block_db is available."""
    from rpc import deps
    
    assert hasattr(deps, "get_block_db"), \
        "rpc.deps.get_block_db must exist"
    
    assert callable(deps.get_block_db), \
        "get_block_db must be callable"


def test_deps_registry_helpers():
    """Test that rpc.deps registry helpers are available."""
    from rpc import deps
    
    assert hasattr(deps, "register"), \
        "rpc.deps.register must exist for PTL service registration"
    
    assert hasattr(deps, "get"), \
        "rpc.deps.get must exist for PTL service access"
    
    assert callable(deps.register), "register must be callable"
    assert callable(deps.get), "get must be callable"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
