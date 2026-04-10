"""
execution.scheduler.lockset — read/write lockset capture from access-tracker.

This module centralizes:
  • A canonical LockSet type (reads/writes of (address, storage_key?)).
  • Helpers to normalize keys and to detect conflicts/merge locksets.
  • A safe wrapper to execute a callable under the state's access tracker and
    return both the result and a captured LockSet.
  • Fallback helpers to infer a coarse lockset from a receipt access list.

It is intentionally dependency-light and tolerant of missing features:
if the access-tracker is unavailable, capture helpers return (result, None, None)
so callers can conservatively fall back to serial execution.

Key model
---------
Key := (address: bytes, storage_key: Optional[bytes])
  - storage_key None means an account-level read/write (balance/nonce/code).
  - storage_key b"...32 bytes..." means a storage slot-level access.

Typical usage
-------------
    from execution.scheduler.lockset import run_with_lockset

    result, lockset, err = run_with_lockset(
        state_view,
        apply_tx,
        tx=tx, state=state_view, block_env=block_env, gas_table=gas_table, params=params
    )
    if err is not None:
        ... handle failure ...
    if lockset is None:
        ... fall back to serial or infer from receipt ...

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence, Set, Tuple, Union

Key = Tuple[bytes, Optional[bytes]]


@dataclass(frozen=True)
class LockSet:
    """Read/write lockset over (address, slot?) keys."""

    reads: Set[Key]
    writes: Set[Key]

    @staticmethod
    def empty() -> "LockSet":
        return LockSet(reads=set(), writes=set())

    def conflicts_with(self, other: "LockSet") -> bool:
        """
        True iff there is any R/W, W/R, or W/W overlap between self and other.
        (R/R overlap is fine.)
        """
        if self.writes & other.writes:
            return True
        if self.writes & other.reads:
            return True
        if self.reads & other.writes:
            return True
        return False

    def union(self, other: "LockSet") -> "LockSet":
        return LockSet(
            reads=self.reads | other.reads, writes=self.writes | other.writes
        )

    @staticmethod
    def from_pairs(reads: Iterable[Key] = (), writes: Iterable[Key] = ()) -> "LockSet":
        return LockSet(reads=set(reads), writes=set(writes))


# ------------------------------ Normalization -------------------------------

BytesLike = Union[bytes, bytearray, memoryview, str, int]


def _to_bytes(x: BytesLike, *, hex_ok: bool = True, min_len: int = 0) -> bytes:
    """
    Convert common types to bytes:
      - bytes/bytearray/memoryview: returned/copied
      - str: if hex_ok and startswith "0x" → hex decode; else UTF-8
      - int: big-endian minimal length, optionally left-padded to min_len
    """
    if isinstance(x, (bytes, bytearray, memoryview)):
        b = bytes(x)
    elif isinstance(x, str):
        if hex_ok and x.startswith("0x"):
            x = x[2:]
            if len(x) % 2:
                x = "0" + x
            b = bytes.fromhex(x)
        else:
            b = x.encode("utf-8")
    elif isinstance(x, int):
        if x < 0:
            raise ValueError("negative integers not supported for normalization")
        # Minimal big-endian; pad if requested
        nbytes = max(1, (x.bit_length() + 7) // 8)
        b = x.to_bytes(nbytes, "big")
    else:
        raise TypeError(f"unsupported key/address type: {type(x)!r}")

    if min_len and len(b) < min_len:
        b = b.rjust(min_len, b"\x00")
    return b


def normalize_key(addr: BytesLike, slot: Optional[BytesLike]) -> Key:
    """
    Normalize (address, slot?) to bytes. We DO NOT enforce fixed widths here;
    callers may choose to pad to 20/32 bytes upstream if desired.
    """
    a = _to_bytes(addr)
    s = None if slot is None else _to_bytes(slot)
    return (a, s)


# --------------------------- Capture via tracker -----------------------------


def run_with_lockset(
    state_view: Any,
    fn: Any,
    /,
    **kwargs: Any,
) -> Tuple[Optional[Any], Optional[LockSet], Optional[BaseException]]:
    """
    Execute `fn(**kwargs)` while capturing read/write accesses using the optional
    execution.state.access_tracker facility. Returns a triple:
        (result_or_None, lockset_or_None, error_or_None)

    If the tracker is unavailable, this returns (result, None, None) without error.
    If the callable raises, returns (None, None, exc).

    The expected access-tracker protocol is:

        from execution.state.access_tracker import track_accesses
        with track_accesses(state_view) as tr:
            ... do reads/writes via state_view ...
        reads, writes = tr.reads_writes()   # iterables of (addr, slot?) pairs

    This module only *consumes* that protocol and does not enforce its shape beyond
    calling `reads_writes()`.
    """
    try:
        from ..state.access_tracker import track_accesses  # type: ignore
    except Exception:
        # No tracker: execute plainly, no lockset.
        try:
            res = fn(**kwargs)
            return res, None, None
        except BaseException as e:  # pragma: no cover
            return None, None, e

    try:
        with track_accesses(state_view) as tr:  # type: ignore[misc]
            res = fn(**kwargs)
        reads_raw, writes_raw = tr.reads_writes()  # type: ignore[attr-defined]
        reads: Set[Key] = set()
        writes: Set[Key] = set()
        for a, s in reads_raw:
            reads.add(normalize_key(a, s))
        for a, s in writes_raw:
            writes.add(normalize_key(a, s))
        return res, LockSet(reads=reads, writes=writes), None
    except BaseException as e:
        return None, None, e


# ----------------------- Fallback: infer from receipt -----------------------


def lockset_from_access_list(access_list: Sequence[Any]) -> LockSet:
    """
    Build a conservative lockset from an EIP-2930-like access list:

        access_list := [
           {"address": "0x..", "storageKeys": ["0x..", ...]},
           ...
        ]  OR
        [
            (address_bytes_or_hex, [slot_bytes_or_hex, ...]),
            ...
        ]

    We conservatively treat listed entries as *writes*. If a tuple has an empty
    slot list, we treat it as an account-level write.
    """
    reads: Set[Key] = set()
    writes: Set[Key] = set()
    for ent in access_list:
        if isinstance(ent, dict):
            addr = ent.get("address")
            slots = ent.get("storageKeys") or []
        else:
            addr, slots = ent
        if not slots:
            writes.add(normalize_key(addr, None))
        else:
            for s in slots:
                writes.add(normalize_key(addr, s))
    return LockSet(reads=reads, writes=writes)


def lockset_from_result_receipt(result: Any) -> Optional[LockSet]:
    """
    Try to derive a lockset from ApplyResult.receipt.accessList if present.
    Returns None if no access list is available.
    """
    try:
        rc = getattr(result, "receipt", None) or {}
        acc = rc.get("accessList")
        if not acc:
            return None
        return lockset_from_access_list(acc)
    except Exception:
        return None


# ------------------------------ Utilities -----------------------------------


def merge_locksets(items: Iterable[LockSet]) -> LockSet:
    """Union-merge a series of locksets."""
    r: Set[Key] = set()
    w: Set[Key] = set()
    for ls in items:
        r |= ls.reads
        w |= ls.writes
    return LockSet(reads=r, writes=w)


def any_conflict(locksets: Sequence[LockSet]) -> bool:
    """True if any pair of locksets conflicts."""
    for i in range(len(locksets)):
        for j in range(i + 1, len(locksets)):
            if locksets[i].conflicts_with(locksets[j]):
                return True
    return False


__all__ = [
    "Key",
    "LockSet",
    "normalize_key",
    "run_with_lockset",
    "lockset_from_access_list",
    "lockset_from_result_receipt",
    "merge_locksets",
    "any_conflict",
]
