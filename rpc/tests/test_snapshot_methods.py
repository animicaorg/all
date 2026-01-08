"""
Test that snapshot RPC methods are properly registered and callable.
"""

from rpc import methods


def test_snapshot_methods_are_registered():
    """Test that snapshot.* RPC methods are registered in the method registry."""
    
    # Ensure methods are loaded
    methods.ensure_loaded()
    
    registry = methods.get_registry()
    
    # Check that key snapshot methods are registered
    snapshot_methods = [
        "snapshot.create",
        "snapshot.list",
        "snapshot.get",
        "snapshot.verify",
        "snapshot.import",
        "snapshot.delete",
        "snapshot.downloadChunk",
        "snapshot.discoverFromPeers",
    ]
    
    for method_name in snapshot_methods:
        assert method_name in registry, f"Method {method_name} not registered"
        assert callable(registry[method_name].func), f"Method {method_name} is not callable"


def test_snapshot_methods_have_descriptions():
    """Test that snapshot methods have proper descriptions."""
    
    methods.ensure_loaded()
    registry = methods.get_registry()
    
    # Verify descriptions are set
    assert "snapshot.create" in registry
    assert registry["snapshot.create"].desc is not None
    assert "snapshot" in registry["snapshot.create"].desc.lower()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
