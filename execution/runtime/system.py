"""
execution.runtime.system — system account ids (treasury/coinbase/reserved)

Provides:
- A canonical address size for the execution layer.
- Deterministic, chain-id–scoped default treasury address (overrideable by config).
- A burn sink constant (never credited in normal fee flows; burns are implicit).
- Helpers to parse/validate addresses and to construct a SystemAccounts bundle.

Notes
-----
* Addresses here are raw bytes (not bech32m). Higher layers may present bech32m
  for UX; the execution/state machinery should operate on bytes of fixed length.
* The default treasury address is derived from a domain-tagged SHA3-256 of the
  chain-id so different networks do not collide. Operators can override it via
  config (e.g., core/types/params.py or execution/adapters/params.py).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Union

# --------------------------------------------------------------------------------------
# Address model
# --------------------------------------------------------------------------------------

# Animica uses sha3_256(pubkey) in addresses → 32 bytes is the canonical size.
ADDRESS_SIZE: int = 32

ZERO_ADDRESS: bytes = b"\x00" * ADDRESS_SIZE


class SystemId(str, Enum):
    TREASURY = "treasury"
    BURN_SINK = "burn_sink"
    ZERO = "zero"


# Burn is *accountless* in fee settlement (nothing is credited), but some subsystems
# may want a stable, obviously-unspendable sink for testing or special flows.
BURN_SINK: bytes = hashlib.sha3_256(b"animica/burn_sink/v1").digest()


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def _derive_chain_scoped(tag: str, chain_id: int) -> bytes:
    """
    Deterministically derive a chain-scoped system address from a tag and chain id.
    """
    payload = f"animica/{tag}|chain_id:{int(chain_id)}".encode("utf-8")
    return _sha3_256(payload)  # 32 bytes


def ensure_address(addr: bytes, *, name: str = "address") -> bytes:
    """
    Validate that `addr` is the canonical ADDRESS_SIZE in length.
    """
    if not isinstance(addr, (bytes, bytearray)):
        raise TypeError(f"{name} must be bytes, got {type(addr).__name__}")
    b = bytes(addr)
    if len(b) != ADDRESS_SIZE:
        raise ValueError(f"{name} must be {ADDRESS_SIZE} bytes, got {len(b)}")
    return b


def parse_hex_address(
    value: Union[str, bytes, bytearray], *, name: str = "address"
) -> bytes:
    """
    Parse an address from hex (with or without '0x' prefix) or pass-through bytes.
    """
    if isinstance(value, (bytes, bytearray)):
        return ensure_address(value, name=name)
    if not isinstance(value, str):
        raise TypeError(f"{name} must be hex str or bytes, got {type(value).__name__}")
    s = value.lower().strip()
    if s.startswith("0x"):
        s = s[2:]
    try:
        b = bytes.fromhex(s)
    except ValueError as e:
        raise ValueError(f"{name}: invalid hex string") from e
    return ensure_address(b, name=name)


def to_hex(addr: bytes) -> str:
    """
    Hex-encode a raw address for logging/debug (0x-prefixed).
    """
    return "0x" + ensure_address(addr).hex()


# --------------------------------------------------------------------------------------
# System accounts bundle
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemAccounts:
    """
    Collected system addresses used during execution and settlement.
    """

    chain_id: int
    treasury: bytes
    burn_sink: bytes = BURN_SINK
    zero: bytes = ZERO_ADDRESS

    def is_system(self, addr: bytes) -> bool:
        a = ensure_address(addr)
        return a in (self.treasury, self.burn_sink, self.zero)


def default_treasury_address(chain_id: int) -> bytes:
    """
    Deterministic, chain-scoped default treasury address (overrideable by config).
    """
    return _derive_chain_scoped("treasury", chain_id)


def load_system_accounts(
    params: Optional[Mapping[str, Any]] = None,
    *,
    chain_id: Optional[int] = None,
) -> SystemAccounts:
    """
    Construct SystemAccounts from optional params.

    Expected (optional) keys in `params`:
      - "chain_id": int
      - "treasury_address": hex string ("0x…") or raw 32-byte bytes

    If not provided, sensible defaults are derived deterministically from chain_id.
    """
    p = params or {}
    cid = int(chain_id if chain_id is not None else p.get("chain_id", 1))

    tr_raw = p.get("treasury_address")
    if tr_raw is None:
        treasury = default_treasury_address(cid)
    else:
        treasury = parse_hex_address(tr_raw, name="treasury_address")

    return SystemAccounts(chain_id=cid, treasury=treasury)


# --------------------------------------------------------------------------------------
# Coinbase access
# --------------------------------------------------------------------------------------


def coinbase_from_block_env(block_env: Any) -> bytes:
    """
    Extract the coinbase/miner address from a BlockEnv-like object.

    The execution/types/context.BlockContext is expected to expose a `coinbase: bytes`
    field of length ADDRESS_SIZE. This helper validates and returns it.
    """
    if not hasattr(block_env, "coinbase"):
        raise AttributeError("block_env must expose a 'coinbase' attribute (bytes).")
    return ensure_address(getattr(block_env, "coinbase"), name="coinbase")


__all__ = [
    "ADDRESS_SIZE",
    "ZERO_ADDRESS",
    "SystemId",
    "BURN_SINK",
    "ensure_address",
    "parse_hex_address",
    "to_hex",
    "SystemAccounts",
    "default_treasury_address",
    "load_system_accounts",
    "coinbase_from_block_env",
]
