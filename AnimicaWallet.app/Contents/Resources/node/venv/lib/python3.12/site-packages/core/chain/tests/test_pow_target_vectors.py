from __future__ import annotations

from pathlib import Path

from core.genesis.loader import load_genesis
from core.network_params import MAINNET_GENESIS_HASH_HEX
from core.types.header import Header
from core.utils.pow import micro_threshold_to_target256


def _load_mainnet_genesis_header() -> Header:
    repo_root = Path(__file__).resolve().parents[3]
    genesis_path = repo_root / "core" / "genesis" / "mainnet.json"
    _params, header = load_genesis(genesis_path)
    return header


def test_mainnet_genesis_pow_vector() -> None:
    header = _load_mainnet_genesis_header()
    key_hash = header.hash().hex()

    assert key_hash == MAINNET_GENESIS_HASH_HEX[2:]
    assert int(header.height) == 0
    assert int(header.thetaMicro) == 1_000_000

    target = micro_threshold_to_target256(int(header.thetaMicro))
    assert (
        hex(target)
        == "0x5e2d58d8b3bcdf1abadec7829054f90dda9805aab56c77333024b9d0a507daed"
    )

    header_hash_int = int.from_bytes(bytes(header.hash()), "big")
    assert header_hash_int <= target
