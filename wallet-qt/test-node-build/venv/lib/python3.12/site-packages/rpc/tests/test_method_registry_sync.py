"""
Regression test for JSON-RPC dispatcher method registry synchronization.

This test ensures that the JSON-RPC dispatcher correctly syncs with the
rpc.methods registry, so that methods like tx.sendRawTransaction are available
when using new_test_client() from a REPL or test environment.

The issue was that rpc/jsonrpc.py's _sync_with_methods_registry was calling
method_registry.ensure_loaded() and method_registry.get_registry(), but
rpc/methods/__init__.py only exposed load_builtins() and get_methods().
This caused an AttributeError that left the dispatcher empty, leading to
-32601 Method not found for valid methods.
"""

from __future__ import annotations

import pytest

from rpc.tests import new_test_client, rpc_call


def test_tx_sendRawTransaction_is_registered():
    """
    Test that tx.sendRawTransaction is registered in the dispatcher after
    new_test_client() is called.
    
    This is a regression test for the issue where methods were not being
    synced from rpc.methods to the jsonrpc dispatcher.
    """
    client, cfg, _ = new_test_client()
    
    # Call tx.sendRawTransaction with invalid params (to trigger parameter validation)
    # We expect a structured error about invalid parameters or bad signature,
    # NOT -32601 Method not found
    result = rpc_call(
        client,
        "tx.sendRawTransaction",
        params=["0x1234"],  # Incomplete/malformed tx data
        expect_error=True,
    )
    
    # Verify we got an error response
    assert "error" in result
    error = result["error"]
    
    # The error should NOT be -32601 (Method not found)
    # It should be something else (likely -32602 Invalid params or a custom error)
    assert error["code"] != -32601, (
        f"Got 'Method not found' error, which means tx.sendRawTransaction "
        f"was not registered in the dispatcher. Error: {error}"
    )
    
    # The method should fail with an appropriate error (e.g., invalid CBOR,
    # bad signature, etc.) but NOT with "Method not found"
    assert error["message"] != "Method not found"


def test_multiple_methods_are_registered():
    """
    Test that multiple methods from different namespaces are registered.
    """
    client, cfg, _ = new_test_client()
    
    # Test methods from different namespaces
    methods_to_test = [
        "chain.getChainId",
        "chain.getHead",
        "state.getBalance",
        "tx.sendRawTransaction",
    ]
    
    for method in methods_to_test:
        # We just check that the method exists by attempting to call it
        # (some may need params, but we're only checking for -32601)
        payload = {"jsonrpc": "2.0", "method": method, "id": 1}
        if method == "state.getBalance":
            # This method requires params
            payload["params"] = ["0x0000000000000000000000000000000000000000"]
        elif method == "tx.sendRawTransaction":
            # This method requires params
            payload["params"] = ["0x1234"]
        
        resp = client.post("/rpc", json=payload)
        assert resp.status_code == 200, f"HTTP {resp.status_code} for {method}"
        
        data = resp.json()
        
        # If there's an error, it should NOT be -32601 (Method not found)
        if "error" in data:
            assert data["error"]["code"] != -32601, (
                f"Method {method} not found in dispatcher! "
                f"Error: {data['error']}"
            )


def test_method_aliases_are_registered():
    """
    Test that method aliases (e.g., tx_sendRawTransaction) are also registered.
    
    The method tx.sendRawTransaction has an underscore alias tx_sendRawTransaction
    for compatibility with different naming conventions.
    """
    client, cfg, _ = new_test_client()
    
    # Test the underscore alias of tx.sendRawTransaction
    result = rpc_call(
        client,
        "tx_sendRawTransaction",  # underscore version (alias)
        params=["0x1234"],
        expect_error=True,
    )
    
    # Should NOT be -32601 Method not found
    assert "error" in result
    assert result["error"]["code"] != -32601, (
        "Alias tx_sendRawTransaction not found in dispatcher"
    )


def test_idempotent_ensure_loaded():
    """
    Test that ensure_loaded() is idempotent and can be called multiple times.
    """
    from rpc import methods as method_registry
    
    # Call ensure_loaded multiple times
    method_registry.ensure_loaded()
    methods_count_1 = len(method_registry.get_methods())
    
    method_registry.ensure_loaded()
    methods_count_2 = len(method_registry.get_methods())
    
    method_registry.ensure_loaded()
    methods_count_3 = len(method_registry.get_methods())
    
    # The count should remain the same (idempotent)
    assert methods_count_1 == methods_count_2 == methods_count_3
    assert methods_count_1 > 0, "No methods were loaded"


def test_get_registry_returns_same_as_get_methods():
    """
    Test that get_registry() is a proper alias for get_methods().
    """
    from rpc import methods as method_registry
    
    method_registry.ensure_loaded()
    
    methods = method_registry.get_methods()
    registry = method_registry.get_registry()
    
    # Both should return the same data
    assert methods == registry
    assert len(methods) > 0
    assert "tx.sendRawTransaction" in methods
    assert "tx.sendRawTransaction" in registry
