"""
execution.access_list.build — construct a canonical access-list from an execution trace.

The access list captures which *accounts* and *storage keys* were touched during
transaction execution so that:
  • schedulers can plan parallelism and detect conflicts, and
  • block builders can pre-warm state reads/writes deterministically.

Input flexibility
-----------------
This builder accepts either:
  1) An "access tracker" object (duck-typed) with some of these attributes:
       - accounts_read:    Iterable[bytes-like]
       - accounts_written: Iterable[bytes-like]
       - storage_reads:    Mapping[bytes-like, Iterable[bytes-like]] or Iterable[tuple[addr,key]]
       - storage_writes:   Mapping[bytes-like, Iterable[bytes-like]] or Iterable[tuple[addr,key]]
     (Names with singular/plural variants are also detected, e.g. `storage_read`.)

  2) An iterable of *events*, each as a dict or tuple:
       dict example:
         {"type": "storage_read",  "address": b"\x01"*20, "key": b"\x00"*32}
         {"type": "account_write", "address": "0xabc123..."}
       tuple example: ("storage_write", address_bytes, key_bytes)

Only bytes-like (bytes/bytearray/memoryview) or 0x-hex strings are accepted for
addresses and keys; anything else raises TypeError.

Output
------
A canonical, de-duplicated, sorted list of `AccessListEntry` objects:
    AccessListEntry(address: bytes, storage_keys: tuple[bytes, ...])

Sorting rules:
  • Entries are sorted by address (lexicographically).
  • storage_keys within an entry are sorted lexicographically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (Any, Dict, Iterable, Iterator, List, Mapping,
                    MutableMapping, Optional, Sequence, Tuple, Union)

# Prefer the project's canonical types, but provide a lightweight fallback to
# keep early bring-up smooth.
try:
    from execution.types.access_list import AccessListEntry  # type: ignore
except Exception:  # pragma: no cover

    @dataclass(frozen=True)
    class AccessListEntry:  # type: ignore
        address: bytes
        storage_keys: Tuple[bytes, ...]


# ------------------------------- byte helpers --------------------------------


def _is_bytes_like(x: Any) -> bool:
    return isinstance(x, (bytes, bytearray, memoryview))


def _to_bytes(x: Any) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, bytearray):
        return bytes(x)
    if isinstance(x, memoryview):
        return bytes(x)
    if isinstance(x, str) and x.startswith("0x"):
        h = x[2:]
        if len(h) % 2:
            h = "0" + h
        return bytes.fromhex(h)
    raise TypeError(f"expected bytes-like or 0x-hex string, got {type(x)!r}")


# ------------------------------- core builder --------------------------------

_EVENT_ACCOUNT_READ = {"account_read", "acct_read", "account-get", "account-load"}
_EVENT_ACCOUNT_WRITE = {"account_write", "acct_write", "account-put", "account-store"}
_EVENT_STORAGE_READ = {"storage_read", "sread", "storage-get", "slot-get"}
_EVENT_STORAGE_WRITE = {"storage_write", "swrite", "storage-put", "slot-put"}
_EVENT_CALL = {"call", "delegate_call", "static_call"}
_EVENT_CREATE = {"create", "contract_create"}


def _normalize_tracker_storage(
    obj: Any,
    attr_names: Sequence[str],
) -> Iterator[Tuple[bytes, bytes]]:
    """
    Read a storage access collection from `obj` using the first present attribute
    from `attr_names`. Supports:
      - Mapping[address -> Iterable[key]]
      - Iterable[(address, key)]
    """
    for name in attr_names:
        if hasattr(obj, name):
            val = getattr(obj, name)
            if isinstance(val, Mapping):
                for a, keys in val.items():
                    a_b = _to_bytes(a)
                    for k in keys:
                        yield a_b, _to_bytes(k)
            else:
                # Assume iterable of tuples
                for item in val:
                    if not isinstance(item, (tuple, list)) or len(item) != 2:
                        raise TypeError(
                            f"{name} must be Mapping[addr->keys] or Iterable[(addr,key)], got {type(val)!r}"
                        )
                    a, k = item
                    yield _to_bytes(a), _to_bytes(k)
            return
    # If none present, nothing to yield.


def _normalize_tracker_accounts(
    obj: Any,
    attr_names: Sequence[str],
) -> Iterator[bytes]:
    for name in attr_names:
        if hasattr(obj, name):
            val = getattr(obj, name)
            for a in val:
                yield _to_bytes(a)
            return


def _ingest_event(
    ev: Any,
    addrs: set[bytes],
    slots: MutableMapping[bytes, set[bytes]],
    include_reads: bool,
    include_writes: bool,
    include_calls: bool,
) -> None:
    if isinstance(ev, dict):
        etype = str(ev.get("type", "")).lower()
        if not etype:
            raise ValueError("event dict missing 'type'")
        addr = ev.get("address")
        key = ev.get("key", None)
    elif isinstance(ev, (tuple, list)) and ev:
        etype = str(ev[0]).lower()
        addr = ev[1] if len(ev) > 1 else None
        key = ev[2] if len(ev) > 2 else None
    else:
        raise TypeError(f"unsupported event shape: {type(ev)!r}")

    if etype in _EVENT_ACCOUNT_READ:
        if include_reads:
            addrs.add(_to_bytes(addr))
    elif etype in _EVENT_ACCOUNT_WRITE:
        if include_writes:
            addrs.add(_to_bytes(addr))
    elif etype in _EVENT_STORAGE_READ:
        if include_reads:
            a_b = _to_bytes(addr)
            k_b = _to_bytes(key)
            slots.setdefault(a_b, set()).add(k_b)
            addrs.add(a_b)
    elif etype in _EVENT_STORAGE_WRITE:
        if include_writes:
            a_b = _to_bytes(addr)
            k_b = _to_bytes(key)
            slots.setdefault(a_b, set()).add(k_b)
            addrs.add(a_b)
    elif etype in _EVENT_CALL or etype in _EVENT_CREATE:
        # Calls/creates touch the callee/created address at the account level.
        if include_calls and addr is not None:
            addrs.add(_to_bytes(addr))
    else:
        # Unknown types are ignored to make the builder forward-compatible.
        return


def build_access_list(
    trace_or_tracker: Any,
    *,
    include_reads: bool = True,
    include_writes: bool = True,
    include_calls: bool = True,
) -> List[AccessListEntry]:
    """
    Build a canonical, de-duplicated access list from a trace or tracker.

    Args:
        trace_or_tracker: access tracker (duck-typed) OR iterable of event dicts/tuples.
        include_reads:  whether to include read touches (default True)
        include_writes: whether to include write touches (default True)
        include_calls:  whether to include addresses seen as call/create targets (default True)

    Returns:
        List[AccessListEntry] sorted by address; each entry has sorted storage_keys.
    """
    # Accumulators
    addrs: set[bytes] = set()
    slots: Dict[bytes, set[bytes]] = {}

    # Path A: tracker object with attributes
    if not isinstance(trace_or_tracker, (list, tuple)) and not hasattr(
        trace_or_tracker, "__iter__"
    ):
        # Non-iterable → treat as tracker object
        tracker = trace_or_tracker

        # Accounts
        if include_reads:
            for a in _normalize_tracker_accounts(
                tracker,
                ("accounts_read", "account_reads", "acct_reads", "accounts_get"),
            ):
                addrs.add(a)
        if include_writes:
            for a in _normalize_tracker_accounts(
                tracker,
                ("accounts_written", "account_writes", "acct_writes", "accounts_put"),
            ):
                addrs.add(a)

        # Storage (reads/writes)
        if include_reads:
            for a, k in _normalize_tracker_storage(
                tracker, ("storage_reads", "storage_read", "slots_read", "slot_reads")
            ):
                addrs.add(a)
                slots.setdefault(a, set()).add(k)
        if include_writes:
            for a, k in _normalize_tracker_storage(
                tracker,
                ("storage_writes", "storage_write", "slots_write", "slot_writes"),
            ):
                addrs.add(a)
                slots.setdefault(a, set()).add(k)

        # Optional call/creation targets
        if include_calls:
            for name in (
                "call_targets",
                "called_addresses",
                "create_addresses",
                "created_addresses",
            ):
                if hasattr(tracker, name):
                    for a in getattr(tracker, name):
                        addrs.add(_to_bytes(a))

    else:
        # Path B: iterable of events
        for ev in trace_or_tracker:  # type: ignore[assignment]
            _ingest_event(
                ev, addrs, slots, include_reads, include_writes, include_calls
            )

    # Canonicalize: ensure every address with storage appears in addrs
    for a in list(slots.keys()):
        addrs.add(a)

    # Build entries sorted by address, with sorted storage keys
    entries: List[AccessListEntry] = []
    for addr in sorted(addrs):
        keys = tuple(sorted(slots.get(addr, ())))
        entries.append(AccessListEntry(address=addr, storage_keys=keys))

    return entries


__all__ = ["build_access_list"]
