"""
vm_py.runtime.context — BlockEnv/TxEnv passed to contracts (deterministic)

These lightweight environments are injected into the VM runtime so contracts
can read chain/transaction metadata in a *deterministic* way. They contain
only pure data (ints/bytes) and perform strict validation.

Design notes
------------
- Addresses are raw bytes (typically 32 bytes for Animica; we don't enforce a
  fixed length here to keep this module decoupled from higher layers).
- Hex strings (with or without "0x") are accepted by helpers and normalized to
  bytes.
- All numeric fields are validated to be non-negative.
- `chain_id` is included in BlockEnv to allow domain separation inside the VM.
- Tx hash is carried to seed deterministic PRNGs and for event transcripts.

This module intentionally does not expose wall-clock time or non-deterministic
sources; `timestamp` is the consensus timestamp provided by the execution layer.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, Union


# ----------------------------- helpers ----------------------------- #

class ContextError(Exception):
    """Validation or coercion failure for BlockEnv/TxEnv."""


def _strip_0x(s: str) -> str:
    return s[2:] if s.startswith(("0x", "0X")) else s


def to_bytes(value: Union[bytes, bytearray, memoryview, str]) -> bytes:
    """
    Coerce `value` to bytes.
    - If str, interpret as hex (with or without '0x'); odd-length hex is rejected.
    - If a bytes-like object, copy to immutable bytes.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        h = _strip_0x(value.strip())
        if len(h) % 2 != 0:
            raise ContextError(f"hex string must have even length, got {len(h)}")
        try:
            return bytes.fromhex(h)
        except ValueError as e:  # pragma: no cover - defensive
            raise ContextError(f"invalid hex string: {value!r}") from e
    raise ContextError(f"cannot convert type {type(value).__name__} to bytes")


def to_hex(b: Union[bytes, bytearray, memoryview]) -> str:
    """Encode bytes as 0x-prefixed lowercase hex."""
    return "0x" + bytes(b).hex()


def _require_non_negative_int(name: str, v: Any) -> int:
    if not isinstance(v, int):
        raise ContextError(f"{name} must be int, got {type(v).__name__}")
    if v < 0:
        raise ContextError(f"{name} must be non-negative, got {v}")
    return v


# ----------------------------- models ------------------------------ #

@dataclass(frozen=True)
class BlockEnv:
    """
    Deterministic per-block environment passed to contracts.

    Fields
    ------
    height:     Block height (0-based).
    timestamp:  Consensus timestamp (seconds since epoch or chain-defined unit).
    coinbase:   Miner/producer address as raw bytes.
    chain_id:   Integer chain identifier (matches core/types/params.py).
    """
    height: int
    timestamp: int
    coinbase: bytes
    chain_id: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "height", _require_non_negative_int("height", self.height))
        object.__setattr__(self, "timestamp", _require_non_negative_int("timestamp", self.timestamp))
        object.__setattr__(self, "chain_id", _require_non_negative_int("chain_id", self.chain_id))
        object.__setattr__(self, "coinbase", to_bytes(self.coinbase))

    # ---- constructors ---- #

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BlockEnv":
        return cls(
            height=_require_non_negative_int("height", d.get("height")),
            timestamp=_require_non_negative_int("timestamp", d.get("timestamp")),
            coinbase=to_bytes(d.get("coinbase", b"")),
            chain_id=_require_non_negative_int("chain_id", d.get("chain_id")),
        )

    @classmethod
    def from_execution_context(cls, ctx: Any) -> "BlockEnv":
        """
        Best-effort adapter from execution/runtime BlockContext-like objects:
        expects attributes: height, timestamp, coinbase, chain_id.
        """
        return cls(
            height=_require_non_negative_int("height", getattr(ctx, "height")),
            timestamp=_require_non_negative_int("timestamp", getattr(ctx, "timestamp")),
            coinbase=to_bytes(getattr(ctx, "coinbase")),
            chain_id=_require_non_negative_int("chain_id", getattr(ctx, "chain_id")),
        )

    # ---- views ---- #

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["coinbase"] = to_hex(self.coinbase)
        return d


@dataclass(frozen=True)
class TxEnv:
    """
    Deterministic per-transaction environment passed to contracts.

    Fields
    ------
    tx_hash:   Canonical transaction hash bytes (domain-separated upstream).
    sender:    Originator address (bytes).
    to:        Call target address (bytes) or None for deploy.
    value:     Transfer value in the native unit (int).
    gas_limit: Gas limit available to the VM (int).
    nonce:     Sender nonce (int).
    """
    tx_hash: bytes
    sender: bytes
    to: Optional[bytes]
    value: int
    gas_limit: int
    nonce: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "tx_hash", to_bytes(self.tx_hash))
        object.__setattr__(self, "sender", to_bytes(self.sender))
        if self.to is not None:
            object.__setattr__(self, "to", to_bytes(self.to))
        object.__setattr__(self, "value", _require_non_negative_int("value", self.value))
        object.__setattr__(self, "gas_limit", _require_non_negative_int("gas_limit", self.gas_limit))
        object.__setattr__(self, "nonce", _require_non_negative_int("nonce", self.nonce))

    # ---- constructors ---- #

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TxEnv":
        to_val = d.get("to", None)
        return cls(
            tx_hash=to_bytes(d.get("tx_hash", b"")),
            sender=to_bytes(d.get("sender", b"")),
            to=(to_bytes(to_val) if to_val is not None else None),
            value=_require_non_negative_int("value", d.get("value", 0)),
            gas_limit=_require_non_negative_int("gas_limit", d.get("gas_limit", 0)),
            nonce=_require_non_negative_int("nonce", d.get("nonce", 0)),
        )

    @classmethod
    def from_execution_context(cls, ctx: Any) -> "TxEnv":
        """
        Best-effort adapter from execution/runtime TxContext-like objects:
        expects attributes: tx_hash, sender, to, value, gas_limit, nonce.
        """
        to_attr = getattr(ctx, "to", None)
        return cls(
            tx_hash=to_bytes(getattr(ctx, "tx_hash")),
            sender=to_bytes(getattr(ctx, "sender")),
            to=(to_bytes(to_attr) if to_attr is not None else None),
            value=_require_non_negative_int("value", getattr(ctx, "value")),
            gas_limit=_require_non_negative_int("gas_limit", getattr(ctx, "gas_limit")),
            nonce=_require_non_negative_int("nonce", getattr(ctx, "nonce")),
        )

    # ---- views ---- #

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tx_hash"] = to_hex(self.tx_hash)
        d["sender"] = to_hex(self.sender)
        d["to"] = (to_hex(self.to) if self.to is not None else None)
        return d


__all__ = [
    "ContextError",
    "to_bytes",
    "to_hex",
    "BlockEnv",
    "TxEnv",
]
