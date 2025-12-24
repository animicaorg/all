def test_genesis_loader_imports() -> None:
    import core.genesis.loader as loader

    assert loader is not None


def test_compute_genesis_identity_imports() -> None:
    from core.genesis.loader import compute_genesis_identity

    assert compute_genesis_identity is not None
