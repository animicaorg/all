from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class NetworkParams:
    name: str
    chain_id: int
    expected_genesis_block_hash: Optional[bytes] = None


MAINNET_GENESIS_HASH_HEX = (
    "0x1d964197f0def34f190cdfea52a6bed997b9e0f14d8173d0a5e4e4ae2ae3b474"
)

MAINNET_PARAMS = NetworkParams(
    name="mainnet",
    chain_id=1,
    expected_genesis_block_hash=bytes.fromhex(MAINNET_GENESIS_HASH_HEX[2:]),
)

TESTNET_PARAMS = NetworkParams(name="testnet", chain_id=2)
DEVNET_PARAMS = NetworkParams(name="devnet", chain_id=1337)

_BY_CHAIN_ID = {
    MAINNET_PARAMS.chain_id: MAINNET_PARAMS,
    TESTNET_PARAMS.chain_id: TESTNET_PARAMS,
    DEVNET_PARAMS.chain_id: DEVNET_PARAMS,
}

_BY_NAME = {
    MAINNET_PARAMS.name: MAINNET_PARAMS,
    TESTNET_PARAMS.name: TESTNET_PARAMS,
    DEVNET_PARAMS.name: DEVNET_PARAMS,
}


def get_network_params(
    *, chain_id: Optional[int] = None, network_name: Optional[str] = None
) -> Optional[NetworkParams]:
    if chain_id is not None:
        return _BY_CHAIN_ID.get(int(chain_id))
    if network_name:
        return _BY_NAME.get(network_name.strip().lower())
    return None


def get_expected_genesis_hash(chain_id: int) -> Optional[bytes]:
    params = get_network_params(chain_id=chain_id)
    if params is None:
        return None
    return params.expected_genesis_block_hash


def is_mainnet_name(network_name: Optional[str]) -> bool:
    if not network_name:
        return False
    return network_name.strip().lower() in {"mainnet", "main"}


def enforce_pinned_genesis(
    *,
    chain_id: int,
    genesis_block_hash: bytes,
    genesis_path: Optional[str] = None,
    network_name: Optional[str] = None,
) -> None:
    from core.errors import GenesisError

    params = get_network_params(chain_id=chain_id)
    if params is None or params.expected_genesis_block_hash is None:
        return
    if network_name is not None and not is_mainnet_name(network_name):
        return
    expected = params.expected_genesis_block_hash
    if genesis_block_hash != expected:
        expected_hex = "0x" + expected.hex()
        found_hex = "0x" + genesis_block_hash.hex()
        path_hint = genesis_path or "<unknown>"
        raise GenesisError(
            "genesis does not match pinned mainnet genesis",
            expected=expected_hex,
            found=found_hex,
            genesis_path=path_hint,
            chain_id=chain_id,
            network=network_name or params.name,
        )
