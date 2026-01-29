from __future__ import annotations

from core.genesis.loader import compute_genesis_identity
from core.network_params import get_network_genesis_path, get_pinned_genesis_hash


def test_pinned_genesis_hashes_match_files() -> None:
    networks = [
        ("mainnet", 1),
        ("testnet", 2),
        ("devnet", 1337),
    ]
    for name, chain_id in networks:
        genesis_path = get_network_genesis_path(network_name=name, chain_id=chain_id)
        assert genesis_path is not None
        identity = compute_genesis_identity(genesis_path)
        pinned = get_pinned_genesis_hash(network_name=name, chain_id=chain_id)
        assert pinned is not None
        assert identity.genesis_block_hash == pinned
