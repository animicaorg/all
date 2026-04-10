"""
Secondary indexes for the randomness store.

This module provides lightweight, backend-agnostic secondary indexes over the
byte-oriented KeyValue store defined in `randomness.store.kv`. We maintain two
families of indexes for both Commit and Reveal records:

  1) By round:
        - idx:round:{c|r}:{round_be8}:{primary_key} -> {primary_key}
     Allows fast enumeration of all commits/reveals that belong to a given
     round (e.g., to finalize or audit the round).

  2) By address:
        - idx:addr:{c|r}:{addr_len1}{addr}:{round_be8}:{primary_key} -> {primary_key}
     Allows listing a participant’s history, optionally filtering by a round
     range. Keys are ordered primarily by address, then round (big-endian),
     then primary key (to disambiguate duplicates).

Notes
-----
- Values store the primary key bytes (so callers don’t need to parse keys).
- Functions here do not open transactions themselves; call them inside the same
  KeyValue.transaction() that writes the primary record to avoid index drift.
- `primary_key` must be the exact key under which the corresponding record is
  stored in the primary buckets (e.g., "commits:" / "reveals:" in kv.py).

Key encoding
------------
- round_be8: round id encoded as 8-byte big-endian unsigned.
- addr_len1: single byte length of address (0–255). This prevents prefix
  clashes between addresses and keeps prefix scans precise.

API
---
- index_commit / index_reveal
- deindex_commit / deindex_reveal
- iter_commits_by_round / iter_reveals_by_round
- iter_commits_by_address / iter_reveals_by_address
"""

from __future__ import annotations

from typing import Generator, Iterable, Iterator, Literal, Optional, Tuple

try:
    # Prefer the shared protocol/type from the store package.
    from .kv import KeyValue  # type: ignore
except Exception:  # pragma: no cover
    from typing import Protocol

    class KeyValue(Protocol):  # type: ignore
        def put(self, key: bytes, value: bytes) -> None: ...
        def get(self, key: bytes) -> Optional[bytes]: ...
        def delete(self, key: bytes) -> None: ...
        def iter_prefix(self, prefix: bytes) -> Iterable[Tuple[bytes, bytes]]: ...
        def transaction(self): ...  # context manager


# ---- Internal helpers -------------------------------------------------------

Kind = Literal["commit", "reveal"]
_KIND_TAG = {"commit": b"c", "reveal": b"r"}

_IDX_ROUND = b"idx:round:"
_IDX_ADDR = b"idx:addr:"


def _round_be8(round_id: int) -> bytes:
    if round_id < 0:
        raise ValueError("round_id must be non-negative")
    return round_id.to_bytes(8, "big", signed=False)


def _addr_len_byte(addr: bytes) -> bytes:
    if not isinstance(addr, (bytes, bytearray)):
        raise TypeError("addr must be bytes")
    if len(addr) > 255:
        raise ValueError("addr length must fit in one byte (<=255)")
    return bytes((len(addr),))


def _round_prefix(kind: Kind, round_id: int) -> bytes:
    return _IDX_ROUND + _KIND_TAG[kind] + b":" + _round_be8(round_id)


def _round_key(kind: Kind, round_id: int, primary_key: bytes) -> bytes:
    return _round_prefix(kind, round_id) + b":" + primary_key


def _addr_prefix(kind: Kind, addr: bytes) -> bytes:
    # addr prefix encodes 1-byte length followed by the raw addr
    return _IDX_ADDR + _KIND_TAG[kind] + b":" + _addr_len_byte(addr) + addr


def _addr_key(kind: Kind, addr: bytes, round_id: int, pk: bytes) -> bytes:
    return _addr_prefix(kind, addr) + b":" + _round_be8(round_id) + b":" + pk


def _parse_addr_key_round(kind: Kind, addr: bytes, key: bytes) -> Tuple[int, bytes]:
    """
    Given a full address-index key, return (round_id, primary_key).

    Expected layout:
      idx:addr:{t}:{len}{addr}:{round_be8}:{primary_key}
    """
    prefix = _addr_prefix(kind, addr) + b":"
    if not key.startswith(prefix):
        raise ValueError("Key does not match address index prefix")
    rest = key[len(prefix) :]
    if len(rest) < 8 + 1:  # round_be8 + ":" + at least 1 byte of pk
        raise ValueError("Malformed address index key")
    round_id = int.from_bytes(rest[:8], "big", signed=False)
    if rest[8:9] != b":":
        raise ValueError("Malformed address index key (missing separator)")
    primary_key = rest[9:]
    return round_id, primary_key


# ---- Public API: index maintenance -----------------------------------------


def index_commit(
    kv: KeyValue, *, round_id: int, addr: bytes, primary_key: bytes
) -> None:
    """
    Add commit record to both round and address indexes.
    Call from within the same transaction that writes the primary record.
    """
    kv.put(_round_key("commit", round_id, primary_key), primary_key)
    kv.put(_addr_key("commit", addr, round_id, primary_key), primary_key)


def index_reveal(
    kv: KeyValue, *, round_id: int, addr: bytes, primary_key: bytes
) -> None:
    """
    Add reveal record to both round and address indexes.
    Call from within the same transaction that writes the primary record.
    """
    kv.put(_round_key("reveal", round_id, primary_key), primary_key)
    kv.put(_addr_key("reveal", addr, round_id, primary_key), primary_key)


def deindex_commit(
    kv: KeyValue, *, round_id: int, addr: bytes, primary_key: bytes
) -> None:
    """Remove commit entry from both indexes (idempotent)."""
    kv.delete(_round_key("commit", round_id, primary_key))
    kv.delete(_addr_key("commit", addr, round_id, primary_key))


def deindex_reveal(
    kv: KeyValue, *, round_id: int, addr: bytes, primary_key: bytes
) -> None:
    """Remove reveal entry from both indexes (idempotent)."""
    kv.delete(_round_key("reveal", round_id, primary_key))
    kv.delete(_addr_key("reveal", addr, round_id, primary_key))


# ---- Public API: iteration --------------------------------------------------


def iter_commits_by_round(kv: KeyValue, round_id: int) -> Iterator[bytes]:
    """
    Iterate primary keys of commit records for a given round.
    """
    prefix = _round_prefix("commit", round_id)
    for _k, v in kv.iter_prefix(prefix):
        # value holds primary key
        yield v


def iter_reveals_by_round(kv: KeyValue, round_id: int) -> Iterator[bytes]:
    """
    Iterate primary keys of reveal records for a given round.
    """
    prefix = _round_prefix("reveal", round_id)
    for _k, v in kv.iter_prefix(prefix):
        yield v


def iter_commits_by_address(
    kv: KeyValue,
    addr: bytes,
    *,
    start_round: Optional[int] = None,
    end_round: Optional[int] = None,
) -> Iterator[Tuple[int, bytes]]:
    """
    Iterate (round_id, primary_key) for all commit records by address,
    ordered by round (ascending). Optional inclusive round bounds.
    """
    prefix = _addr_prefix("commit", addr)
    for k, v in kv.iter_prefix(prefix):
        r, _pk_from_key = _parse_addr_key_round("commit", addr, k)
        if (start_round is not None and r < start_round) or (
            end_round is not None and r > end_round
        ):
            continue
        # v is the primary key; prefer that (saves parsing)
        yield (r, v)


def iter_reveals_by_address(
    kv: KeyValue,
    addr: bytes,
    *,
    start_round: Optional[int] = None,
    end_round: Optional[int] = None,
) -> Iterator[Tuple[int, bytes]]:
    """
    Iterate (round_id, primary_key) for all reveal records by address,
    ordered by round (ascending). Optional inclusive round bounds.
    """
    prefix = _addr_prefix("reveal", addr)
    for k, v in kv.iter_prefix(prefix):
        r, _pk_from_key = _parse_addr_key_round("reveal", addr, k)
        if (start_round is not None and r < start_round) or (
            end_round is not None and r > end_round
        ):
            continue
        yield (r, v)


__all__ = [
    "index_commit",
    "index_reveal",
    "deindex_commit",
    "deindex_reveal",
    "iter_commits_by_round",
    "iter_reveals_by_round",
    "iter_commits_by_address",
    "iter_reveals_by_address",
]
