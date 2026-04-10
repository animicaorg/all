from pathlib import Path

from core.genesis.loader import compute_genesis_identity


def test_compute_genesis_identity_mainnet_is_stable() -> None:
    genesis_path = Path("core/genesis/mainnet.json")
    identity_first = compute_genesis_identity(genesis_path)
    identity_second = compute_genesis_identity(genesis_path)

    assert identity_first.genesis_block_hash == identity_second.genesis_block_hash
    assert identity_first.genesis_file_hash == identity_second.genesis_file_hash
    assert isinstance(identity_first.genesis_block_hash, (bytes, bytearray))
    assert len(identity_first.genesis_block_hash) == 32
    assert identity_first.chain_id == identity_second.chain_id
