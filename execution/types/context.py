"""
execution.types.context — execution contexts for blocks and transactions.

These minimal, dependency-light dataclasses carry the *evaluated* context that
the execution engine needs. Higher layers (RPC, mempool) normalize inputs into
these structures before invoking the runtime.

Conventions
-----------
* `timestamp` is Unix time in seconds (int).
* `coinbase`, `sender`, `to`, and `tx_hash` are raw bytes; higher layers may render
  them as bech32m or hex.
* `gas_price` is the *effective* price used for execution/charging (an integer in
  chain-native "wei-like" units). Base/tip splitting is handled elsewhere before
  constructing `TxContext`.

Utilities
---------
* Hex-friendly (de)serialization via `to_dict()` / `from_dict()`.
* Strict but pragmatic validation: non-negative integers; basic length checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Union, Sequence


HexLike = Union[str, bytes, bytearray, memoryview]


# ------------------------------ hex helpers ---------------------------------

def _hex_to_bytes(v: HexLike) -> bytes:
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v)
    if isinstance(v, str):
        s = v.strip()
        if s.startswith(("0x", "0X")):
            s = s[2:]
        if len(s) % 2:
            s = "0" + s  # tolerate odd-length hex
        try:
            return bytes.fromhex(s)
        except ValueError as e:
            raise ValueError(f"invalid hex string: {v!r}") from e
    raise TypeError(f"expected hex-like value, got {type(v).__name__}")


def _bytes_to_hex(b: Optional[bytes]) -> Optional[str]:
    if b is None:
        return None
    return "0x" + b.hex()


# ------------------------------ dataclasses ---------------------------------

@dataclass(frozen=True)
class BlockContext:
    """
    Environment for executing a block of transactions.

    Attributes:
        height:    int >= 0 — block height (genesis = 0)
        timestamp: int >= 0 — Unix seconds
        chain_id:  int >= 1 — CAIP-2 chain id number for this network
        coinbase:  bytes     — miner/validator reward address
        base_fee:  int >= 0  — protocol base fee used in fee accounting (optional; default 0)
    """
    height: int
    timestamp: int
    chain_id: int
    coinbase: bytes
    base_fee: int = 0

    def __init__(
        self,
        *,
        height: int,
        timestamp: int,
        chain_id: int,
        coinbase: HexLike,
        base_fee: int = 0,
    ):
        if height < 0:
            raise ValueError("height must be >= 0")
        if timestamp < 0:
            raise ValueError("timestamp must be >= 0")
        if chain_id <= 0:
            raise ValueError("chain_id must be >= 1")
        if base_fee < 0:
            raise ValueError("base_fee must be >= 0")

        coin_b = _hex_to_bytes(coinbase)
        if len(coin_b) < 8:
            # We do not enforce a fixed address length (20/32), but catch obvious mistakes.
            raise ValueError(f"coinbase address too short: {len(coin_b)} bytes")

        object.__setattr__(self, "height", height)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "chain_id", chain_id)
        object.__setattr__(self, "coinbase", coin_b)
        object.__setattr__(self, "base_fee", int(base_fee))

    # --------- (de)serialization ---------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "height": self.height,
            "timestamp": self.timestamp,
            "chainId": self.chain_id,
            "coinbase": _bytes_to_hex(self.coinbase),
            "baseFee": self.base_fee,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BlockContext":
        return cls(
            height=int(d["height"]),
            timestamp=int(d["timestamp"]),
            chain_id=int(d.get("chainId") or d.get("chain_id")),
            coinbase=d["coinbase"],
            base_fee=int(d.get("baseFee", 0)),
        )


@dataclass(frozen=True)
class TxContext:
    """
    Environment for executing a single transaction.

    Attributes:
        sender:     bytes     — origin/account address
        chain_id:   int >= 1  — must match BlockContext.chain_id
        nonce:      int >= 0  — sender nonce *at admission time*
        gas_price:  int >= 0  — effective price charged per gas unit
        to:         Optional[bytes] — call/deploy target (None for contract creation)
        tx_hash:    Optional[bytes] — canonical tx hash (if known prior to execution)
    """
    sender: bytes
    chain_id: int
    nonce: int
    gas_price: int
    to: Optional[bytes] = None
    tx_hash: Optional[bytes] = None

    def __init__(
        self,
        *,
        sender: HexLike,
        chain_id: int,
        nonce: int,
        gas_price: int,
        to: Optional[HexLike] = None,
        tx_hash: Optional[HexLike] = None,
    ):
        if chain_id <= 0:
            raise ValueError("chain_id must be >= 1")
        if nonce < 0:
            raise ValueError("nonce must be >= 0")
        if gas_price < 0:
            raise ValueError("gas_price must be >= 0")

        sender_b = _hex_to_bytes(sender)
        if len(sender_b) < 8:
            raise ValueError(f"sender address too short: {len(sender_b)} bytes")

        to_b = _hex_to_bytes(to) if isinstance(to, (str, bytes, bytearray, memoryview)) else (bytes(to) if to is not None else None)
        if to_b is not None and len(to_b) < 8:
            raise ValueError(f"'to' address too short: {len(to_b)} bytes")

        txh_b = _hex_to_bytes(tx_hash) if isinstance(tx_hash, (str, bytes, bytearray, memoryview)) else (bytes(tx_hash) if tx_hash is not None else None)

        object.__setattr__(self, "sender", sender_b)
        object.__setattr__(self, "chain_id", chain_id)
        object.__setattr__(self, "nonce", int(nonce))
        object.__setattr__(self, "gas_price", int(gas_price))
        object.__setattr__(self, "to", to_b)
        object.__setattr__(self, "tx_hash", txh_b)

    # --------- helpers ---------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sender": _bytes_to_hex(self.sender),
            "chainId": self.chain_id,
            "nonce": self.nonce,
            "gasPrice": self.gas_price,
            "to": _bytes_to_hex(self.to),
            "txHash": _bytes_to_hex(self.tx_hash),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TxContext":
        return cls(
            sender=d["sender"],
            chain_id=int(d.get("chainId") or d.get("chain_id")),
            nonce=int(d["nonce"]),
            gas_price=int(d.get("gasPrice") or d.get("gas_price")),
            to=d.get("to"),
            tx_hash=d.get("txHash") or d.get("tx_hash"),
        )


__all__ = ["BlockContext", "TxContext"]
