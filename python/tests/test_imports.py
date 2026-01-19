def test_genesis_loader_imports() -> None:
    import core.genesis.loader as loader

    assert loader is not None


def test_compute_genesis_identity_imports() -> None:
    from core.genesis.loader import compute_genesis_identity

    assert compute_genesis_identity is not None


def test_network_manifest_imports() -> None:
    """Test that core.network_manifest is importable (required by animica.config)."""
    from core.network_manifest import get_manifest

    assert get_manifest is not None


def test_network_manifest_mainnet() -> None:
    """Test that mainnet manifest can be retrieved and has correct chain_id."""
    from core.network_manifest import get_manifest

    manifest = get_manifest(network="mainnet")
    assert manifest is not None
    assert manifest.chain_id == 0
    assert manifest.network_name == "mainnet"
