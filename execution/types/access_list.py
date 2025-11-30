"""
execution.types.access_list — canonical access-list element types.

An *access list* hints which accounts and storage keys a transaction expects to
touch. This improves determinism and enables optimistic-parallel scheduling.

Types
-----
* `AccessListEntry`: (address, storage_keys[])
    - `address` is raw bytes (20 or 32 typical; we allow >= 8 to be pragmatic).
    - `storage_keys` is a tuple of 32-byte keys (slot identifiers).

* `AccessList`: an immutable tuple-like container of `AccessListEntry` with:
    - `to_dict()` / `from_dict()` for JSON-friendly hex encoding.
    - `canonical()` to produce a deterministically ordered, deduplicated view.

Notes
-----
We *enforce* 32 bytes for storage keys to avoid ambiguity. Address length is not
strictly enforced (beyond a sanity lower bound) because Animica supports
variable-length addresses depending on the configured identity scheme.

This module is dependency-light and safe to import from execution runtime code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (Any, Dict, Iterable, Iterator, List, Mapping, Optional,
                    Sequence, Tuple, Union)

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


# ------------------------------ main types ----------------------------------


@dataclass(frozen=True)
class AccessListEntry:
    """
    A single access-list element: an address and storage keys.

    Attributes:
        address: bytes                — account/contract address
        storage_keys: tuple[bytes, …] — zero or more 32-byte storage slots
    """

    address: bytes
    storage_keys: Tuple[bytes, ...] = ()

    def __init__(self, address: HexLike, storage_keys: Sequence[HexLike] = ()):
        addr_b = _hex_to_bytes(address)
        if len(addr_b) < 8:
            raise ValueError(f"address too short: {len(addr_b)} bytes")
        keys_b: List[bytes] = []
        for i, k in enumerate(storage_keys):
            kb = _hex_to_bytes(k)
            if len(kb) != 32:
                raise ValueError(f"storage_keys[{i}] must be 32 bytes (got {len(kb)})")
            keys_b.append(kb)
        object.__setattr__(self, "address", addr_b)
        object.__setattr__(self, "storage_keys", tuple(keys_b))

    # --------- (de)serialization ---------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "address": _bytes_to_hex(self.address),
            "storageKeys": [_bytes_to_hex(k) for k in self.storage_keys],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "AccessListEntry":
        addr = d.get("address")
        keys = d.get("storageKeys") or d.get("storage_keys") or []
        if not isinstance(keys, (list, tuple)):
            raise TypeError("storageKeys must be list/tuple")
        return cls(addr, keys)


class AccessList(Tuple[AccessListEntry, ...]):
    """
    Immutable container of `AccessListEntry` with helpers.

    Behaves like a tuple; provides `canonical()` for deterministic ordering and
    deduplication of storage keys per address.
    """

    # Allow tuple construction via `AccessList(entries)` (inherited)

    # --------- helpers ---------

    def canonical(self) -> "AccessList":
        """
        Return a new AccessList with:
            * entries sorted by address bytes (lexicographic)
            * storage_keys de-duplicated and sorted within each entry
            * duplicate addresses merged
        """
        # Group by address
        grouped: Dict[bytes, List[bytes]] = {}
        for e in self:
            grouped.setdefault(e.address, [])
            grouped[e.address].extend(e.storage_keys)

        # Deduplicate & sort keys for each address
        entries: List[AccessListEntry] = []
        for addr in sorted(grouped.keys()):
            # unique keys preserving order using set+list comprehension would reorder; use seen set.
            seen: set[bytes] = set()
            unique_sorted = sorted(
                k for k in grouped[addr] if (k not in seen and not seen.add(k))
            )
            entries.append(AccessListEntry(addr, unique_sorted))
        return AccessList(entries)

    def to_dict(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self]

    @classmethod
    def from_dict(
        cls, arr: Sequence[Mapping[str, Any] | AccessListEntry]
    ) -> "AccessList":
        entries: List[AccessListEntry] = []
        for i, item in enumerate(arr):
            if isinstance(item, AccessListEntry):
                entries.append(item)
            elif isinstance(item, Mapping):
                entries.append(AccessListEntry.from_dict(item))
            else:
                raise TypeError(
                    f"AccessList item {i} must be AccessListEntry or mapping"
                )
        return AccessList(entries)

    # nice repr
    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"AccessList(len={len(self)})"


__all__ = ["AccessListEntry", "AccessList"]
