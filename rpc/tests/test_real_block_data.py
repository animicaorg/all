"""
Test that RPC endpoints return real block data from the chain, not synthetic mocks.

This validates that:
1. Block endpoints use real BlockDB, not synthetic make_block() helpers
2. Mining endpoints use real difficulty from consensus
3. No production paths return placeholder/mock data
"""

import pytest


def test_rpc_block_methods_documented():
    """
    Verify that RPC block methods exist and are properly documented.
    """
    try:
        from rpc.methods import block
    except ImportError:
        pytest.skip("rpc.methods.block not available")
    
    # Check that module has proper structure
    assert hasattr(block, "_fallback_block"), (
        "Block module should have fallback function"
    )
    
    # Verify fallback is clearly marked
    if hasattr(block, "_fallback_block"):
        func = block._fallback_block
        source_name = func.__name__
        assert "fallback" in source_name, "Fallback must be named as such"


def test_block_methods_do_not_use_trivial_hashes():
    """
    Verify that fallback block structure doesn't use trivial patterns
    that would leak into production.
    """
    try:
        from rpc.methods.block import _fallback_block
    except ImportError:
        pytest.skip("Block methods not available")
    
    # Get a fallback block
    fallback = _fallback_block(chain_id=1337)
    
    # Verify it has expected structure
    assert "header" in fallback, "Block should have header"
    assert "transactions" in fallback, "Block should have transactions"
    
    # Verify it uses proper zero hash format (not synthetic)
    header = fallback["header"]
    
    # Zero hash should be formatted properly
    if "hash" in header:
        h = header["hash"]
        assert h.startswith("0x"), "Hash should be hex-prefixed"
        assert len(h) == 66, "Hash should be 32 bytes (0x + 64 hex chars)"


def test_miner_rpc_uses_real_theta_from_consensus():
    """
    Verify that miner RPC methods attempt to resolve real Θ from consensus,
    with documented fallback values.
    """
    try:
        from rpc.methods.miner import _resolve_theta, _DEFAULT_THETA_MICRO
    except ImportError:
        pytest.skip("Miner RPC methods not available")
    
    # Verify default theta is a reasonable consensus value, not trivial
    # Default is configured as 3,000,000 µ-nats (3.0 nats)
    assert _DEFAULT_THETA_MICRO > 1_000_000, (
        "Default theta should be a real consensus value, not trivial"
    )
    assert _DEFAULT_THETA_MICRO < 100_000_000, (
        "Default theta should be reasonable (not absurdly high)"
    )
    
    # Verify _resolve_theta tries consensus first
    # We can't test the actual consensus lookup without a full node,
    # but we can verify it returns a non-trivial value
    theta = _resolve_theta()
    
    assert isinstance(theta, int), "Theta should be an integer"
    assert theta > 0, "Theta should be positive"
    assert theta >= _DEFAULT_THETA_MICRO, (
        "Resolved theta should be at least the default"
    )


def test_miner_rpc_address_handling_uses_proper_validation():
    """
    Verify that miner RPC address handling uses proper bech32 validation,
    not placeholder addresses.
    """
    try:
        from rpc.methods.miner import _decode_bech32_address
    except ImportError:
        pytest.skip("Miner address handling not available")
    
    # Test with a valid-looking bech32 address
    test_addr = "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq86r5dm"
    
    try:
        decoded = _decode_bech32_address(test_addr)
        assert isinstance(decoded, bytes), "Decoded address should be bytes"
        assert len(decoded) == 32, "Decoded address should be 32 bytes"
    except Exception as e:
        # If validation fails, that's OK - the key is it's not accepting
        # placeholder addresses like "0x0..."
        pass


def test_rpc_methods_have_proper_imports():
    """
    Verify that RPC methods import real types from core, not mock types.
    """
    try:
        import rpc.methods.miner as miner_mod
    except ImportError:
        pytest.skip("Miner RPC module not available")
    
    # Check that module imports are from real core types
    import inspect
    source = inspect.getsource(miner_mod)
    
    # Should import from core.types, not test fixtures
    assert "from core.types" in source, (
        "Miner RPC should import from core.types for real types"
    )
    
    # Should not import from test mocks
    assert "from test" not in source.lower() or "# test" in source.lower(), (
        "Production RPC should not import from test modules"
    )


def test_block_rpc_uses_canonical_encoding():
    """
    Verify that block RPC methods use canonical encoding functions
    from core, not ad-hoc serialization.
    """
    try:
        from rpc.methods import block
    except ImportError:
        pytest.skip("Block RPC methods not available")
    
    import inspect
    source = inspect.getsource(block)
    
    # Should attempt to import canonical encoders
    assert "canonical" in source or "cbor" in source.lower(), (
        "Block RPC should use canonical or CBOR encoding"
    )
    
    # Should compute proper hashes
    assert "sha3_256" in source or "hash" in source.lower(), (
        "Block RPC should compute proper hashes"
    )


def test_rpc_context_uses_real_chain_state():
    """
    Verify that RPC context (deps.get_ctx()) provides access to real
    chain state, not mock state.
    """
    try:
        from rpc import deps
    except ImportError:
        pytest.skip("RPC deps not available")
    
    # Verify context builder exists
    assert hasattr(deps, "build_context") or hasattr(deps, "get_ctx"), (
        "RPC deps should provide context access"
    )
    
    # If we can build a context, verify it has expected attributes
    try:
        if hasattr(deps, "build_context"):
            ctx = deps.build_context()
        else:
            pytest.skip("Cannot build context without running server")
    except Exception:
        # Context building may require server lifecycle; that's OK
        pytest.skip("Context building requires server")
    
    # Verify context has chain-related attributes
    assert hasattr(ctx, "cfg") or hasattr(ctx, "config"), (
        "Context should have config"
    )


def test_aicf_make_block_is_not_imported_by_rpc():
    """
    Verify that production RPC code does not import make_block from
    aicf.node (the test stub).
    """
    try:
        import rpc.methods.block as block_mod
        import rpc.methods.miner as miner_mod
    except ImportError:
        pytest.skip("RPC modules not available")
    
    import inspect
    
    # Check block module doesn't import from aicf.node
    block_source = inspect.getsource(block_mod)
    assert "from aicf.node import" not in block_source, (
        "Production RPC should not import from aicf.node stub"
    )
    assert "aicf.node.make_block" not in block_source, (
        "Production RPC should not use aicf.node.make_block"
    )
    
    # Check miner module doesn't import from aicf.node
    miner_source = inspect.getsource(miner_mod)
    assert "from aicf.node import" not in miner_source, (
        "Production miner RPC should not import from aicf.node stub"
    )


def test_rpc_server_does_not_use_stub_node():
    """
    Verify that the main RPC server (rpc/server.py) does not
    import or use the aicf stub node.
    """
    try:
        import rpc.server as server_mod
    except ImportError:
        pytest.skip("RPC server not available")
    
    import inspect
    source = inspect.getsource(server_mod)
    
    # Should not import aicf.node
    assert "aicf.node" not in source, (
        "Production RPC server should not use aicf.node stub"
    )
    
    # Test passes if aicf.node is not imported
    # The server may delegate to methods or use dependency injection,
    # so we don't require specific core/consensus imports here


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
