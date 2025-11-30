from __future__ import annotations

"""
context — BlockEnv/TxEnv placeholders for browser simulations

These light-weight dataclasses provide deterministic block/transaction context
to the in-browser VM runtime. They avoid wall-clock and network dependencies,
and are deliberately minimal but extensible.

Fields are intentionally typed with simple primitives (int/bytes) and avoid
external dependencies so they run under Pyodide/WASM.

Conventions
-----------
- All integers are non-negative and fit Python ints.
- All byte fields are raw bytes (not hex strings). Helpers are provided to
  (de)serialize to/from hex for UI integration.
- `dummy()` constructors provide deterministic defaults for local simulations.

Compatibility
-------------
These shapes mirror the subset used by vm_pkg runtime & stdlib:
  BlockEnv: chain_id, height, timestamp, coinbase, base_fee (optional)
  TxEnv   : sender, to (optional), value, gas_price, gas_limit, nonce, tx_hash

Nothing here enforces consensus rules; this is purely for local, deterministic sim.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

# Local hash helper for deterministic defaults
from . import hash_api

__all__ = ["BlockEnv", "TxEnv"]


def _ensure_int(name: str, v: int) -> int:
    if not isinstance(v, int) or v < 0:
        raise ValueError(f"{name} must be a non-negative int, got {v!r}")
    return v


def _ensure_bytes(name: str, v: bytes, *, allow_empty: bool = True) -> bytes:
    if not isinstance(v, (bytes, bytearray)):
        raise ValueError(f"{name} must be bytes, got {type(v)}")
    if not allow_empty and len(v) == 0:
        raise ValueError(f"{name} must be non-empty bytes")
    return bytes(v)


def _hex(b: Optional[bytes]) -> Optional[str]:
    return None if b is None else "0x" + b.hex()


def _unhex(x: Optional[str]) -> Optional[bytes]:
    if x is None:
        return None
    x = x.lower()
    if x.startswith("0x"):
        x = x[2:]
    return bytes.fromhex(x)


@dataclass(slots=True)
class BlockEnv:
    chain_id: int
    height: int
    timestamp: int  # seconds since epoch, deterministic in sims
    coinbase: bytes  # miner/validator address bytes (length flexible)
    base_fee: int = 0  # optional; used by gas accounting if enabled

    # ---- Constructors ----

    @staticmethod
    def dummy(*, chain_id: int = 1, height: int = 1) -> "BlockEnv":
        """
        Deterministic placeholder. Timestamp is derived from (chain_id, height)
        via sha3, so repeated sims are stable across runs and platforms.
        """
        _ensure_int("chain_id", chain_id)
        _ensure_int("height", height)
        seed = f"{chain_id}:{height}".encode("utf-8")
        ts = int.from_bytes(hash_api.sha3_256(seed)[:8], "big") % (2**31)
        coinbase = hash_api.sha3_256(b"coinbase|" + seed)[:20]  # 20-byte preview
        return BlockEnv(
            chain_id=chain_id,
            height=height,
            timestamp=ts,
            coinbase=coinbase,
            base_fee=0,
        )

    @staticmethod
    def from_dict(obj: Dict[str, Any]) -> "BlockEnv":
        return BlockEnv(
            chain_id=_ensure_int("chain_id", int(obj.get("chain_id", 0))),
            height=_ensure_int("height", int(obj.get("height", 0))),
            timestamp=_ensure_int("timestamp", int(obj.get("timestamp", 0))),
            coinbase=_ensure_bytes(
                "coinbase",
                (
                    _unhex(obj.get("coinbase"))
                    if isinstance(obj.get("coinbase"), str)
                    else bytes(obj.get("coinbase", b""))
                ),
            ),
            base_fee=_ensure_int("base_fee", int(obj.get("base_fee", 0))),
        )

    # ---- Serialization ----

    def to_dict(self, *, hex_bytes: bool = True) -> Dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "height": self.height,
            "timestamp": self.timestamp,
            "coinbase": _hex(self.coinbase) if hex_bytes else self.coinbase,
            "base_fee": self.base_fee,
        }


@dataclass(slots=True)
class TxEnv:
    sender: bytes
    to: Optional[bytes]  # None for deploy
    value: int  # native value to transfer
    gas_price: int  # unit price (ignored by default in sims)
    gas_limit: int  # soft limit for simulation
    nonce: int
    tx_hash: bytes  # stable seed for PRNG & tracing

    # ---- Constructors ----

    @staticmethod
    def dummy(
        *,
        sender: Optional[bytes] = None,
        to: Optional[bytes] = None,
        value: int = 0,
        gas_price: int = 0,
        gas_limit: int = 5_000_000,
        nonce: int = 0,
        seed: Optional[bytes] = None,
    ) -> "TxEnv":
        """
        Deterministic placeholder for local calls. If `seed` is not provided,
        it is derived from (sender|to|nonce|value) via sha3 to seed the runtime PRNG.
        """
        if sender is None:
            sender = hash_api.sha3_256(b"sender|demo")[:20]
        sender = _ensure_bytes("sender", sender)
        to_b = None if to is None else _ensure_bytes("to", to)
        _ensure_int("value", value)
        _ensure_int("gas_price", gas_price)
        _ensure_int("gas_limit", gas_limit)
        _ensure_int("nonce", nonce)

        if seed is None:
            m = b"|".join(
                [
                    sender,
                    to_b if to_b is not None else b"",
                    nonce.to_bytes(8, "big"),
                    value.to_bytes(8, "big"),
                ]
            )
            seed = hash_api.sha3_256(b"txseed" + m)
        tx_hash = hash_api.sha3_256(b"txhash" + seed)

        return TxEnv(
            sender=sender,
            to=to_b,
            value=value,
            gas_price=gas_price,
            gas_limit=gas_limit,
            nonce=nonce,
            tx_hash=tx_hash,
        )

    @staticmethod
    def from_dict(obj: Dict[str, Any]) -> "TxEnv":
        sender = obj.get("sender")
        to = obj.get("to")
        return TxEnv(
            sender=_ensure_bytes(
                "sender",
                _unhex(sender) if isinstance(sender, str) else bytes(sender or b""),
            ),
            to=(
                _unhex(to)
                if isinstance(to, str)
                else (bytes(to) if to is not None else None)
            ),
            value=_ensure_int("value", int(obj.get("value", 0))),
            gas_price=_ensure_int("gas_price", int(obj.get("gas_price", 0))),
            gas_limit=_ensure_int("gas_limit", int(obj.get("gas_limit", 0))),
            nonce=_ensure_int("nonce", int(obj.get("nonce", 0))),
            tx_hash=_ensure_bytes(
                "tx_hash",
                (
                    _unhex(obj.get("tx_hash"))
                    if isinstance(obj.get("tx_hash"), str)
                    else bytes(obj.get("tx_hash", b""))
                ),
            ),
        )

    # ---- Serialization ----

    def to_dict(self, *, hex_bytes: bool = True) -> Dict[str, Any]:
        return {
            "sender": _hex(self.sender) if hex_bytes else self.sender,
            "to": _hex(self.to) if hex_bytes else self.to,
            "value": self.value,
            "gas_price": self.gas_price,
            "gas_limit": self.gas_limit,
            "nonce": self.nonce,
            "tx_hash": _hex(self.tx_hash) if hex_bytes else self.tx_hash,
        }
