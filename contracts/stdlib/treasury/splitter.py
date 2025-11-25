# -*- coding: utf-8 -*-
"""
contracts.stdlib.treasury.splitter
==================================

Deterministic N-way payment splitter for contracts that hold funds in their own
treasury balance (via VM stdlib `treasury`). This module tracks immutable
payees and integer share weights, supports explicit deposits (accounting only),
and enables *pull-style* releases: each payee can claim what they're owed based
on the formula:

    owed(payee) = floor(totalReceived * shares(payee) / totalShares) - released(payee)

Key properties
--------------
- **Deterministic & simple math**: integer arithmetic with floor division.
- **Immutable membership**: payee list & shares are fixed at creation.
- **Pull payments**: each payee calls `release` to claim; `release_all` can
  distribute (optionally capped by `max_payees`) in deterministic index order.
- **No hidden time**: no clocks; deposits and releases are explicit calls.
- **No automatic reservation**: deposits mark *accounting intent*. At `release`
  time we enforce the treasury has enough unspent balance. If you want stronger
  safety, pair with an escrow or pre-reserve strategy at the contract level.

Usage sketch (inside your contract)
-----------------------------------
    from stdlib import abi, events
    from contracts.stdlib.treasury import splitter as ps
    # optional: from contracts.stdlib.treasury import escrow as esc  (for reservations)

    def create_splitter(payees: list[bytes], shares: list[int], nonce: bytes, meta: bytes=b"") -> bytes:
        return ps.create(payees, shares, nonce, meta)

    def attribute_revenue(id: bytes, amount: int) -> None:
        # Optionally ensure funds exist before accounting the deposit:
        # require_min_balance(amount)  # or stricter reservation logic you build.
        ps.deposit(id, amount)

    def claim(id: bytes, payee: bytes) -> int:
        return ps.release(id, payee)

    def claim_all(id: bytes, max_payees: int=0) -> int:
        # Distribute to all payees (or first max_payees) in index order
        return ps.release_all(id, max_payees)

Storage layout
--------------
- Splitter ID: keccak256( concat(payees) | concat(u256(shares)) | nonce )
- Keys (bytes prefixes shown; `id` is the splitter id; `i` is index):
    "ps:ex:" + id              -> b"\x01" (exists)
    "ps:n:"  + id              -> u256 count N
    "ps:ts:" + id              -> u256 totalShares
    "ps:tr:" + id              -> u256 totalReceived (accounting)
    "ps:td:" + id              -> u256 totalReleased (sum of all releases)
    "ps:meta:"+ id             -> bytes opaque metadata
    "ps:p:"  + id + uvar(i)    -> payee[i] (bytes address)
    "ps:s:"  + id + uvar(i)    -> u256 shares[i]
    "ps:rl:" + id + uvar(i)    -> u256 released[i] (cumulative)
    "ps:ix:" + id + payee      -> u256 (i+1) reverse index map (0 = not found)

Events
------
- b"SplitterCreated" {id, n, totalShares, meta}
- b"SplitterDeposit" {id, amount, totalReceived}
- b"SplitterReleased" {id, payee, amount, releasedToDate}
- b"SplitterReleaseAll" {id, distributed, count}

Revert messages
---------------
- b"SPLIT:NOT_FOUND"
- b"SPLIT:EXISTS"
- b"SPLIT:BAD_INPUT"
- b"SPLIT:BAD_INDEX"
- b"SPLIT:ZERO_ADDR"
- b"SPLIT:ZERO_SHARES"
- b"SPLIT:DUP_PAYEE"
- b"SPLIT:NO_FUNDS"

Notes
-----
- All integers are treated as u256 at the storage boundary; intermediate math
  uses Python ints but results are range-checked before persistence/transfer.
- `deposit` does not move funds; it *attributes* treasury balance to this
  splitter's accounting. `release` actually transfers from the contract
  treasury to the payee and will revert if insufficient funds remain.
"""
from __future__ import annotations

from typing import Final, List, Tuple

from stdlib import abi, events  # type: ignore
from stdlib import hash as _hash  # type: ignore
from stdlib import storage  # type: ignore

# Reuse treasury helpers from sibling
from . import balance as _trez_balance, transfer as _trez_transfer, require_min_balance as _trez_require

# ---------- Constants ----------

_U256_MAX: Final[int] = (1 << 256) - 1

# Storage prefixes
_P_EX: Final[bytes] = b"ps:ex:"
_P_N: Final[bytes] = b"ps:n:"
_P_TS: Final[bytes] = b"ps:ts:"
_P_TRCV: Final[bytes] = b"ps:tr:"
_P_TREL: Final[bytes] = b"ps:td:"
_P_META: Final[bytes] = b"ps:meta:"
_P_PAY: Final[bytes] = b"ps:p:"
_P_SHR: Final[bytes] = b"ps:s:"
_P_REL: Final[bytes] = b"ps:rl:"
_P_IX: Final[bytes] = b"ps:ix:"

# ---------- Basic encoders ----------

def _u256_to_bytes(x: int) -> bytes:
    if x < 0 or x > _U256_MAX:
        abi.revert(b"SPLIT:BAD_INPUT")
    return int(x).to_bytes(32, "big")

def _bytes_to_u256(b: bytes) -> int:
    if len(b) == 0:
        return 0
    if len(b) != 32:
        abi.revert(b"SPLIT:NOT_FOUND")
    return int.from_bytes(b, "big")

def _set_u256(k: bytes, x: int) -> None:
    storage.set(k, _u256_to_bytes(x))

def _get_u256(k: bytes) -> int:
    return _bytes_to_u256(storage.get(k))

def _setb(k: bytes, v: bytes) -> None:
    storage.set(k, bytes(v))

def _getb(k: bytes) -> bytes:
    return storage.get(k)

def _k(prefix: bytes, id_: bytes) -> bytes:
    return prefix + id_

def _ki(prefix: bytes, id_: bytes, i: int) -> bytes:
    # store index as minimal big-endian (uvarint) to keep keys compact & ordered
    if i < 0:
        abi.revert(b"SPLIT:BAD_INDEX")
    # Minimal big-endian encoding (no leading zeros)
    if i == 0:
        idx = b"\x00"
    else:
        sz = (i.bit_length() + 7) // 8
        idx = i.to_bytes(sz, "big")
    return prefix + id_ + idx

def _exists(id_: bytes) -> bool:
    return storage.get(_k(_P_EX, id_)) == b"\x01"

def _ensure_addr(a: bytes) -> None:
    if not isinstance(a, (bytes, bytearray)) or len(a) == 0:
        abi.revert(b"SPLIT:ZERO_ADDR")

def _ensure_share(s: int) -> None:
    if s <= 0:
        abi.revert(b"SPLIT:ZERO_SHARES")
    if s > _U256_MAX:
        abi.revert(b"SPLIT:BAD_INPUT")

# ---------- Public views ----------

def compute_id(payees: List[bytes], shares: List[int], nonce: bytes) -> bytes:
    """
    Deterministic splitter id = keccak256( concat(payees) | concat(u256(shares)) | nonce )
    """
    if len(payees) == 0 or len(payees) != len(shares):
        abi.revert(b"SPLIT:BAD_INPUT")
    buf = bytearray()
    seen = set()
    for p in payees:
        _ensure_addr(p)
        if p in seen:
            abi.revert(b"SPLIT:DUP_PAYEE")
        seen.add(p)
        buf += p
    for s in shares:
        _ensure_share(int(s))
        buf += _u256_to_bytes(int(s))
    buf += bytes(nonce)
    return _hash.keccak256(bytes(buf))

def count(id_: bytes) -> int:
    """Number of payees N."""
    if not _exists(id_):
        abi.revert(b"SPLIT:NOT_FOUND")
    return _get_u256(_k(_P_N, id_))

def total_shares(id_: bytes) -> int:
    if not _exists(id_):
        abi.revert(b"SPLIT:NOT_FOUND")
    return _get_u256(_k(_P_TS, id_))

def total_received(id_: bytes) -> int:
    if not _exists(id_):
        abi.revert(b"SPLIT:NOT_FOUND")
    return _get_u256(_k(_P_TRCV, id_))

def total_released(id_: bytes) -> int:
    if not _exists(id_):
        abi.revert(b"SPLIT:NOT_FOUND")
    return _get_u256(_k(_P_TREL, id_))

def payee_at(id_: bytes, i: int) -> Tuple[bytes, int, int]:
    """
    Get (payee, shares, released) by index.
    """
    if not _exists(id_):
        abi.revert(b"SPLIT:NOT_FOUND")
    n = count(id_)
    if i < 0 or i >= n:
        abi.revert(b"SPLIT:BAD_INDEX")
    p = _getb(_ki(_P_PAY, id_, i))
    s = _get_u256(_ki(_P_SHR, id_, i))
    r = _get_u256(_ki(_P_REL, id_, i))
    return p, s, r

def shares_of(id_: bytes, payee: bytes) -> int:
    if not _exists(id_):
        abi.revert(b"SPLIT:NOT_FOUND")
    idx1 = _get_u256(_k(_P_IX, id_) + payee)  # (i+1) or 0
    if idx1 == 0:
        return 0
    return _get_u256(_ki(_P_SHR, id_, idx1 - 1))

def released_of(id_: bytes, payee: bytes) -> int:
    if not _exists(id_):
        abi.revert(b"SPLIT:NOT_FOUND")
    idx1 = _get_u256(_k(_P_IX, id_) + payee)
    if idx1 == 0:
        return 0
    return _get_u256(_ki(_P_REL, id_, idx1 - 1))

def pending_of(id_: bytes, payee: bytes) -> int:
    """
    Compute owed amount for a payee, using floor(totalReceived * share / totalShares) - released.
    """
    _ensure_addr(payee)
    if not _exists(id_):
        abi.revert(b"SPLIT:NOT_FOUND")
    idx1 = _get_u256(_k(_P_IX, id_) + payee)
    if idx1 == 0:
        return 0
    i = idx1 - 1
    tr = total_received(id_)
    ts = total_shares(id_)
    s = _get_u256(_ki(_P_SHR, id_, i))
    already = _get_u256(_ki(_P_REL, id_, i))
    # floor division calculation
    due = (tr * s) // ts
    return due - already if due >= already else 0

# ---------- Mutations ----------

def create(payees: List[bytes], shares: List[int], nonce: bytes, meta: bytes=b"") -> bytes:
    """
    Create a new splitter with immutable payees & shares. Returns `id`.
    """
    id_ = compute_id(payees, shares, nonce)
    if _exists(id_):
        abi.revert(b"SPLIT:EXISTS")

    n = len(payees)
    ts = 0
    # Persist roster
    seen = set()
    for i in range(n):
        p = payees[i]
        s = int(shares[i])
        _ensure_addr(p)
        _ensure_share(s)
        if p in seen:
            abi.revert(b"SPLIT:DUP_PAYEE")
        seen.add(p)
        _setb(_ki(_P_PAY, id_, i), p)
        _set_u256(_ki(_P_SHR, id_, i), s)
        _set_u256(_ki(_P_REL, id_, i), 0)
        # reverse index (i+1)
        _set_u256(_k(_P_IX, id_) + p, i + 1)
        ts += s
        if ts > _U256_MAX:
            abi.revert(b"SPLIT:BAD_INPUT")

    _set_u256(_k(_P_N, id_), n)
    _set_u256(_k(_P_TS, id_), ts)
    _set_u256(_k(_P_TRCV, id_), 0)
    _set_u256(_k(_P_TREL, id_), 0)
    _setb(_k(_P_META, id_), bytes(meta))
    _setb(_k(_P_EX, id_), b"\x01")

    events.emit(b"SplitterCreated", {
        b"id": id_,
        b"n": _u256_to_bytes(n),
        b"totalShares": _u256_to_bytes(ts),
        b"meta": bytes(meta),
    })
    return id_

def deposit(id_: bytes, amount: int) -> None:
    """
    Attribute `amount` units of the contract treasury to this splitter's accounting.
    This does not move funds, but you may optionally call `require_min_balance(amount)`
    beforehand to assert funds are available at deposit time.
    """
    if not _exists(id_):
        abi.revert(b"SPLIT:NOT_FOUND")
    if amount < 0:
        abi.revert(b"SPLIT:BAD_INPUT")

    tr = total_received(id_)
    new_tr = tr + int(amount)
    if new_tr > _U256_MAX:
        abi.revert(b"SPLIT:BAD_INPUT")
    _set_u256(_k(_P_TRCV, id_), new_tr)

    events.emit(b"SplitterDeposit", {
        b"id": id_,
        b"amount": _u256_to_bytes(int(amount)),
        b"totalReceived": _u256_to_bytes(new_tr),
    })

def release(id_: bytes, payee: bytes) -> int:
    """
    Release the currently due amount to `payee`. Returns the amount transferred.
    Reverts if there are not enough funds in the contract treasury at the moment
    of release.
    """
    _ensure_addr(payee)
    if not _exists(id_):
        abi.revert(b"SPLIT:NOT_FOUND")

    idx1 = _get_u256(_k(_P_IX, id_) + payee)
    if idx1 == 0:
        abi.revert(b"SPLIT:BAD_INPUT")
    i = idx1 - 1

    owed = pending_of(id_, payee)
    if owed == 0:
        return 0

    # Ensure treasury can cover this transfer *now*.
    _trez_require(owed)

    # Update released counters
    prev_rel = _get_u256(_ki(_P_REL, id_, i))
    new_rel = prev_rel + owed
    if new_rel > _U256_MAX:
        abi.revert(b"SPLIT:BAD_INPUT")
    _set_u256(_ki(_P_REL, id_, i), new_rel)

    td = total_released(id_)
    new_td = td + owed
    if new_td > _U256_MAX:
        abi.revert(b"SPLIT:BAD_INPUT")
    _set_u256(_k(_P_TREL, id_), new_td)

    # Transfer
    _trez_transfer(payee, owed)

    events.emit(b"SplitterReleased", {
        b"id": id_,
        b"payee": payee,
        b"amount": _u256_to_bytes(owed),
        b"releasedToDate": _u256_to_bytes(new_rel),
    })
    return owed

def release_all(id_: bytes, max_payees: int = 0) -> int:
    """
    Distribute to all payees (or the first `max_payees` if >0) in index order.
    Returns the total amount transferred.

    NOTE: This loops O(N). For large N, prefer individual `release` calls or
    chunked calls with `max_payees`.
    """
    if not _exists(id_):
        abi.revert(b"SPLIT:NOT_FOUND")
    n = count(id_)
    lim = n if max_payees <= 0 or max_payees > n else max_payees

    distributed = 0
    # We compute all owed amounts first to avoid dependence on transfer side effects
    # Then enforce treasury coverage per payee before each transfer (strongest safety).
    for i in range(lim):
        p = _getb(_ki(_P_PAY, id_, i))
        owed = pending_of(id_, p)
        if owed > 0:
            # will revert if insufficient funds
            distributed += release(id_, p)

    events.emit(b"SplitterReleaseAll", {
        b"id": id_,
        b"distributed": _u256_to_bytes(distributed),
        b"count": _u256_to_bytes(lim),
    })
    return distributed

__all__ = [
    # views
    "compute_id", "count", "total_shares", "total_received", "total_released",
    "payee_at", "shares_of", "released_of", "pending_of",
    # mutations
    "create", "deposit", "release", "release_all",
]
