"""
Canonical transaction hashing utilities for mempool + RPC.
"""

from __future__ import annotations

from typing import Any

from core.utils.hash import sha3_256
from core.utils.tx import normalize_tx_bytes


def normalized_tx_bytes(tx_like: Any) -> bytes:
    """Normalize tx-like input into canonical CBOR bytes."""
    return normalize_tx_bytes(tx_like)


def tx_hash_bytes(tx_like: Any) -> bytes:
    """Compute canonical tx hash from normalized bytes (sha3_256)."""
    return sha3_256(normalized_tx_bytes(tx_like))


def tx_hash_hex(tx_like: Any) -> str:
    """Compute canonical tx hash as 0x-prefixed hex string."""
    return "0x" + tx_hash_bytes(tx_like).hex()


__all__ = ["normalized_tx_bytes", "tx_hash_bytes", "tx_hash_hex"]
