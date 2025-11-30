"""
execution.runtime.env — construct BlockEnv/TxEnv from core head + tx

This module adapts whatever your "head" (latest header) and "tx" objects look
like into the canonical execution contexts used by the state machine. It is
intentionally duck-typed: heads and txs can be dataclasses, simple objects, or
dict-like mappings — we just read commonly named attributes/keys.

It returns the dataclasses defined in execution.types.context:
  - BlockContext  (aliased here as BlockEnv)
  - TxContext     (aliased here as TxEnv)

Design goals
------------
- Tolerant field extraction (supports number/height, baseFee/base_price, etc.).
- Hex-or-bytes coercion for addresses and hashes.
- Optional overrides (e.g., force base_price/coinbase/timestamp for tests).
- No import-time heavy deps.

Example
-------
    from execution.runtime.env import make_block_env, make_tx_env

    head = {"number": 100, "timestamp": 1_725_000_000, "baseFee": 3}
    params = ChainParams(chain_id=1)   # from core.types.params
    block_env = make_block_env(head, params, coinbase="0x00"*20)

    tx = {"gasPrice": 5, "from": "0x12..", "nonce": 7}
    tx_env = make_tx_env(tx, block_env, tip_price=2)  # optional override

Notes
-----
- Addresses are returned as raw bytes. Pass hex strings (0x…) or bytes.
- Prices and numeric fields are ints (wei-like smallest unit).
"""

from __future__ import annotations

from dataclasses import fields as dc_fields
from typing import Any, Iterable, Mapping, Optional

from ..types.context import BlockContext, TxContext  # canonical dataclasses

# Public aliases (names used throughout the codebase)
BlockEnv = BlockContext
TxEnv = TxContext


# =============================================================================
# Helpers: tolerant field access & coercions
# =============================================================================


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """
    Return first present attribute/key among `names` from `obj` or `default`.
    Supports objects with attributes, dict-like, and dotdicts.
    """
    for n in names:
        # mapping
        if isinstance(obj, Mapping) and n in obj:
            return obj[n]
        # attribute
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _as_int(x: Any, *, default: int = 0) -> int:
    if x is None:
        return default
    if isinstance(x, int):
        return x
    if isinstance(x, (bool,)):
        return int(x)
    if isinstance(x, (bytes, bytearray)):
        # interpret as big-endian unsigned integer
        return int.from_bytes(x, "big", signed=False)
    if isinstance(x, str):
        x = x.strip()
        if x.startswith(("0x", "0X")):
            try:
                return int(x, 16)
            except ValueError:
                return default
        try:
            return int(x, 10)
        except ValueError:
            return default
    try:
        return int(x)  # last resort
    except Exception:
        return default


def _as_bytes(x: Any, *, expect_len: Optional[int] = None) -> bytes:
    """
    Coerce `x` into bytes. Accepts bytes/bytearray/hex-string/"0x.." strings.
    If expect_len is provided, right-pad with zeros or truncate to that length.
    """
    if x is None:
        out = b""
    elif isinstance(x, (bytes, bytearray)):
        out = bytes(x)
    elif isinstance(x, str):
        s = x.strip()
        if s.startswith(("0x", "0X")):
            s = s[2:]
        if len(s) % 2:
            s = "0" + s  # nibble-align
        try:
            out = bytes.fromhex(s)
        except ValueError:
            out = b""
    else:
        # try int → bytes
        try:
            i = int(x)
            if i < 0:
                i = 0
            # choose minimal length unless expect_len given
            length = expect_len or max(1, (i.bit_length() + 7) // 8)
            out = i.to_bytes(length, "big")
        except Exception:
            out = b""

    if expect_len is not None:
        if len(out) > expect_len:
            out = out[-expect_len:]  # keep rightmost bytes
        elif len(out) < expect_len:
            out = out.rjust(expect_len, b"\x00")
    return out


def _first_present(obj: Any, candidates: Iterable[str]) -> Any:
    return _get(obj, *tuple(candidates))


def _make_dataclass(cls, values: dict) -> Any:
    """
    Construct dataclass `cls` filtering unknown keys for forward-compat.
    """
    valid = {f.name for f in dc_fields(cls)}
    return cls(**{k: v for k, v in values.items() if k in valid})


# =============================================================================
# Block / Tx env builders
# =============================================================================


def make_block_env(
    head: Any,
    chain_params: Any,
    *,
    coinbase: Optional[Any] = None,
    base_price: Optional[int] = None,
    timestamp: Optional[int] = None,
) -> BlockEnv:
    """
    Build a BlockEnv from a chain head/header + chain params.

    Parameters
    ----------
    head : Any
        Object or mapping with common header-ish fields. Recognized alternatives:
        - height, number
        - timestamp, time
        - base_price, baseFee, base_fee
        - coinbase, miner, proposer
        - hash (optional), parent_hash (optional)
    chain_params : Any
        Must expose `chain_id` or `chainId`.
    coinbase : Optional[Any]
        Override coinbase address; bytes or hex string (0x…).
    base_price : Optional[int]
        Override base price (burn component).
    timestamp : Optional[int]
        Override block timestamp (seconds).

    Returns
    -------
    BlockEnv (alias of BlockContext)
    """
    height = _as_int(
        _first_present(head, ("height", "number", "block_height")), default=0
    )
    ts = _as_int(
        (
            timestamp
            if timestamp is not None
            else _first_present(head, ("timestamp", "time"))
        ),
        default=0,
    )
    bp = _as_int(
        (
            base_price
            if base_price is not None
            else _first_present(head, ("base_price", "baseFee", "base_fee"))
        ),
        default=0,
    )

    # Resolve coinbase
    cb_src = (
        coinbase
        if coinbase is not None
        else _first_present(head, ("coinbase", "miner", "proposer"))
    )
    cb = _as_bytes(cb_src, expect_len=20) if cb_src is not None else b"\x00" * 20

    chain_id = _as_int(_get(chain_params, "chain_id", "chainId"), default=0)

    values = {
        "height": height,
        "timestamp": ts,
        "base_price": bp,
        "coinbase": cb,
        "chain_id": chain_id,
        # Optional/bonus info if the dataclass supports them:
        "parent_hash": _as_bytes(
            _first_present(head, ("parent_hash", "parentHash")), expect_len=32
        ),
        "head_hash": _as_bytes(
            _first_present(head, ("hash", "block_hash")), expect_len=32
        ),
    }
    return _make_dataclass(BlockContext, values)


def make_tx_env(
    tx: Any,
    block_env: BlockEnv,
    *,
    gas_price: Optional[int] = None,
    base_price: Optional[int] = None,
    tip_price: Optional[int] = None,
    sender: Optional[Any] = None,
    nonce: Optional[int] = None,
) -> TxEnv:
    """
    Build a TxEnv using a transaction-like object and an existing BlockEnv.

    Parameters
    ----------
    tx : Any
        Object or mapping with common tx fields. Recognized alternatives:
        - gas_price, gasPrice, maxFeePerGas (treated as total price if present)
        - maxPriorityFeePerGas (tip component)
        - from, sender (address)
        - nonce
    block_env : BlockEnv
        The block context produced by make_block_env.
    gas_price : Optional[int]
        Override total gas price directly; if not provided we combine
        base_price + tip_price or fall back to tx.gasPrice/maxFeePerGas.
    base_price : Optional[int]
        Override base component (defaults to block_env.base_price).
    tip_price : Optional[int]
        Override tip component (defaults from tx.maxPriorityFeePerGas or 0).
    sender : Optional[Any]
        Override sender (bytes or hex string).
    nonce : Optional[int]
        Override tx nonce.

    Returns
    -------
    TxEnv (alias of TxContext)
    """
    # Resolve prices
    bp = _as_int(
        base_price if base_price is not None else getattr(block_env, "base_price", 0),
        default=0,
    )

    # EIP-1559-ish candidate names
    tx_tip = _as_int(
        _first_present(tx, ("maxPriorityFeePerGas", "tip_price")), default=0
    )
    tp = _as_int(tip_price if tip_price is not None else tx_tip, default=0)

    # If an explicit gas_price override was given, it wins.
    gp_tx = _as_int(
        _first_present(tx, ("gas_price", "gasPrice", "maxFeePerGas")), default=bp + tp
    )
    gp = _as_int(
        gas_price
        if gas_price is not None
        else (
            bp + tp
            if (
                tip_price is not None
                or "maxPriorityFeePerGas" in getattr(tx, "__dict__", {})
                or (isinstance(tx, Mapping) and "maxPriorityFeePerGas" in tx)
            )
            else gp_tx
        )
    )

    snd = _as_bytes(
        (
            sender
            if sender is not None
            else _first_present(tx, ("from", "sender", "from_address"))
        ),
        expect_len=20,
    )
    nn = _as_int(
        nonce if nonce is not None else _first_present(tx, ("nonce",)), default=0
    )

    values = {
        "chain_id": getattr(block_env, "chain_id", 0),
        "gas_price": gp,
        "base_price": bp,
        "tip_price": max(0, gp - bp),
        "sender": snd if snd else b"\x00" * 20,
        "nonce": nn,
        # Optional/bonus info if supported by TxContext:
        "block_height": getattr(block_env, "height", 0),
        "block_timestamp": getattr(block_env, "timestamp", 0),
        "coinbase": getattr(block_env, "coinbase", b"\x00" * 20),
    }
    return _make_dataclass(TxContext, values)


__all__ = [
    "BlockEnv",
    "TxEnv",
    "make_block_env",
    "make_tx_env",
]
