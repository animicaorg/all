"""
execution.access_list.merge — union / intersection over access lists.

An access list is a sequence of entries:
    AccessListEntry(address: bytes, storage_keys: tuple[bytes, ...])

This module provides deterministic, canonical set-operations used by the
block-builder and schedulers when batching transactions.

Semantics
---------
Union:
  • Address set is the union of all input addresses.
  • For each address, storage_keys is the union of all keys seen for that address.
  • Account-only touches (no keys) are preserved (i.e., empty key tuple).
  • Result is sorted by address; keys per address are sorted and de-duplicated.

Intersection:
  • Address set is the set of addresses present in *every* input list.
  • Keys per address are the intersection of keys across input lists that specify
    keys for that address.
  • Account-only presence is treated as:
        - mode="strict"     (default): empty set of keys for that list.
        - mode="presence":  wildcard (i.e., it does not constrain the key set).
    In presence mode, if *all* lists only have account-level presence for an
    address, the result contains that address with an empty key tuple.

Both operations are stable and deterministic.

Examples
--------
    union = merge_union([alist1, alist2, alist3])
    inter = merge_intersection([alist1, alist2], mode="presence")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Mapping, MutableMapping, Sequence, Set, Tuple, Literal

# Prefer project type; fall back to a local definition for early bring-up.
try:
    from execution.types.access_list import AccessListEntry  # type: ignore
except Exception:  # pragma: no cover
    @dataclass(frozen=True)
    class AccessListEntry:  # type: ignore
        address: bytes
        storage_keys: Tuple[bytes, ...]


# ------------------------------ internal utils -------------------------------

def _to_map(entries: Iterable[AccessListEntry]) -> Dict[bytes, Set[bytes]]:
    """
    Convert entries → {address -> set(storage_keys)} with copies (no aliasing).
    """
    out: Dict[bytes, Set[bytes]] = {}
    for e in entries:
        s = out.setdefault(e.address, set())
        # e.storage_keys may be empty (account-only touch)
        for k in e.storage_keys:
            s.add(k)
    return out


def _from_map(m: Mapping[bytes, Set[bytes]]) -> List[AccessListEntry]:
    """
    Convert {address -> set(keys)} → canonical, sorted AccessListEntry list.
    """
    out: List[AccessListEntry] = []
    for addr in sorted(m.keys()):
        keys = tuple(sorted(m[addr]))
        out.append(AccessListEntry(address=addr, storage_keys=keys))
    return out


# ---------------------------------- union ------------------------------------

def merge_union(access_lists: Iterable[Iterable[AccessListEntry]]) -> List[AccessListEntry]:
    """
    Union of multiple access lists.

    Args:
        access_lists: iterable of access-list sequences.

    Returns:
        Canonical, sorted list with de-duplicated addresses and keys.
    """
    acc: Dict[bytes, Set[bytes]] = {}
    for al in access_lists:
        amap = _to_map(al)
        for addr, keys in amap.items():
            acc.setdefault(addr, set()).update(keys)
        # Preserve account-only touches too (addresses with zero keys)
        for addr in amap.keys():
            acc.setdefault(addr, acc.get(addr, set()))
    return _from_map(acc)


# ------------------------------- intersection --------------------------------

def merge_intersection(
    access_lists: Iterable[Iterable[AccessListEntry]],
    *,
    mode: Literal["strict", "presence"] = "strict",
) -> List[AccessListEntry]:
    """
    Intersection of multiple access lists.

    Address rule:
        Address must be present in *every* list.

    Key rule:
        • strict mode:
            Treat account-only presence as an empty key set; the intersection
            across lists is the set-intersection of keys (→ possibly empty).
        • presence mode:
            Treat account-only presence as a wildcard (no constraint). If *all*
            lists are account-only for an address, the result is account-only.

    Args:
        access_lists: iterable of access-list sequences.
        mode: "strict" or "presence" (see above).

    Returns:
        Canonical, sorted list of entries in the intersection.
    """
    # Convert each list to a map for faster operations.
    maps: List[Dict[bytes, Set[bytes]]] = [ _to_map(al) for al in access_lists ]
    if not maps:
        return []

    # Addresses present in every list.
    common_addrs = set(maps[0].keys())
    for m in maps[1:]:
        common_addrs.intersection_update(m.keys())

    result: Dict[bytes, Set[bytes]] = {}
    for addr in sorted(common_addrs):
        # Gather key-sets for this address across lists.
        key_sets: List[Set[bytes]] = []
        account_only_count = 0
        for m in maps:
            ks = m.get(addr, set())
            if len(ks) == 0:
                account_only_count += 1
            key_sets.append(ks)

        if mode == "strict":
            # Intersection over the concrete sets (empty constrains to empty).
            inter: Set[bytes]
            if key_sets:
                inter = set(key_sets[0])
                for ks in key_sets[1:]:
                    inter.intersection_update(ks)
            else:
                inter = set()
            result[addr] = inter

        else:  # presence mode
            # Ignore empty sets when computing intersection; if all are empty,
            # remain account-only.
            non_empty = [ks for ks in key_sets if ks]
            if not non_empty:
                result[addr] = set()
            else:
                inter = set(non_empty[0])
                for ks in non_empty[1:]:
                    inter.intersection_update(ks)
                result[addr] = inter

    return _from_map(result)


__all__ = ["merge_union", "merge_intersection"]
