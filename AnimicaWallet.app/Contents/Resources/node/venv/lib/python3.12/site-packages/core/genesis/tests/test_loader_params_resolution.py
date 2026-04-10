from pathlib import Path

from core.genesis.loader import load_genesis


def test_load_genesis_uses_devnet_chain_params() -> None:
    params, header = load_genesis(Path("core/genesis/devnet.json"))

    assert params.chain_id == 1337
    assert header.chainId == 1337


def test_load_genesis_uses_testnet_chain_params() -> None:
    params, header = load_genesis(Path("core/genesis/testnet.json"))

    assert params.chain_id == 2
    assert header.chainId == 2
