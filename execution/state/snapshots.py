"""
execution.state.snapshots — snapshot ids & diff application.

This module provides lightweight snapshot markers for the in-memory state
represented by the `Journal` (execution.state.journal) and utilities to:

- capture a snapshot marker (by journal depth)
- compute a deterministic, canonical "diff" of all staged changes since a
  snapshot (accounts upserts/deletions and storage writes/deletes)
- apply such a diff to any (accounts, storage) base
- compose diffs (useful for schedulers)
- fast revert/commit convenience helpers that delegate to the Journal

Design notes
------------
Snapshots are *markers*, not full copies. A marker is the integer depth of the
journal at the time of capture. The diff is computed by aggregating all overlay
layers from that depth up to the current top.

This keeps things O(changes) with no deep copying until a diff is requested.

Interfaces expected from collaborators
--------------------------------------
- Journal (from execution.state.journal) with attributes/methods:
    * .depth() -> int
    * .revert_to(marker: int)
    * .commit_to(marker: int)
    * ._layers : List[_Overlay]  (internal, but same package — OK)
- Account (from execution.state.accounts)
- StorageView (from execution.state.storage) with:
    * get(addr, key, default=b"") -> bytes
    * set(addr, key, value: bytes) -> None
    * delete(addr, key) -> None
    * clear_account(addr) -> None
    * export_account_hex(addr) -> Mapping[str,str]  (only used in journal; not required here)

All bytes are opaque and domain-separated by higher layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, Optional, Set, Tuple, Iterable, MutableMapping

from .accounts import Account
from .storage import StorageView
from .journal import Journal  # same-package access to overlays is acceptable


# =============================================================================
# Diff object
# =============================================================================

@dataclass
class StateDiff:
    """
    A canonical, mergeable state diff.

    - accounts_upsert: full Account records that overwrite the target.
    - accounts_delete: addresses to delete entirely (account + all storage).
    - storage_writes: per-address dict of key -> Optional[value]
        * value is bytes to set
        * value is None to delete the key

    Invariants:
    - If an address is present in accounts_delete, storage_writes for that
      address are ignored on apply.
    - If an address is present in accounts_upsert, that upsert overrides
      any previous delete for that address (last-wins policy).
    """
    accounts_upsert: Dict[bytes, Account] = field(default_factory=dict)
    accounts_delete: Set[bytes] = field(default_factory=set)
    storage_writes: Dict[bytes, Dict[bytes, Optional[bytes]]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.accounts_upsert or self.accounts_delete or any(self.storage_writes.values()))

    def items_count(self) -> int:
        n = len(self.accounts_upsert) + len(self.accounts_delete)
        n += sum(len(m) for m in self.storage_writes.values())
        return n


# =============================================================================
# Snapshots API
# =============================================================================

SnapshotId = int  # journal depth marker (>=1)


def snapshot(journal: Journal) -> SnapshotId:
    """
    Capture a snapshot marker of the current journal depth.

    Returns
    -------
    int
        Depth marker (>= 1). Later diffs can be computed "since" this depth.
    """
    return journal.depth()


def diff_since(journal: Journal, marker: SnapshotId) -> StateDiff:
    """
    Compute a canonical StateDiff by aggregating all overlays at positions
    [marker, journal.depth()).

    Parameters
    ----------
    journal : Journal
        The journal whose overlays are diffed.
    marker : int
        A previous depth marker (typically returned by `snapshot()`).

    Returns
    -------
    StateDiff
        Aggregated changes since marker.

    Raises
    ------
    ValueError
        If marker < 1 or marker > journal.depth().
    """
    depth = journal.depth()
    if marker < 1 or marker > depth:
        raise ValueError(f"invalid snapshot marker {marker}; current depth={depth}")

    if marker == depth:
        return StateDiff()  # nothing staged since marker

    # The journal keeps a list of overlays; index 0..depth-1
    # We need to aggregate overlays from index (marker-1) .. (depth-1),
    # *excluding* the layer just below marker. We want changes *after* marker.
    #
    # Example: depth=3, marker=2  => aggregate layers index 1..2
    # (Since marker==2 means there were two layers at capture time.)
    start_index = marker - 1
    layers = journal._layers[start_index:]  # type: ignore[attr-defined]

    # Aggregate into a synthetic overlay-like accumulator using last-wins logic.
    acc_upsert: Dict[bytes, Account] = {}
    acc_delete: Set[bytes] = set()
    stor: Dict[bytes, Dict[bytes, Optional[bytes]]] = {}

    for layer in layers:
        # 1) Apply destructions (except addresses resurrected in this very layer via accounts dict)
        for addr in (layer.destroyed - set(layer.accounts.keys())):
            acc_delete.add(addr)
            acc_upsert.pop(addr, None)
            stor.pop(addr, None)

        # 2) Upserts override deletions
        for addr, acct in layer.accounts.items():
            acc_upsert[addr] = Account(nonce=acct.nonce, balance=acct.balance, code_hash=acct.code_hash)
            acc_delete.discard(addr)

        # 3) Storage writes (skip addresses that are currently deleted by accumulation)
        for addr, writes in layer.storage.items():
            if addr in acc_delete:
                continue
            m = stor.get(addr)
            if m is None:
                m = {}
                stor[addr] = m
            for k, v in writes.items():
                m[k] = v

    return StateDiff(accounts_upsert=acc_upsert, accounts_delete=acc_delete, storage_writes=stor)


def apply_diff(accounts: MutableMapping[bytes, Account],
               storage: StorageView,
               diff: StateDiff) -> None:
    """
    Apply a StateDiff to the given (accounts, storage) state.

    This mirrors Journal's base-apply semantics:
    - Deleted accounts are removed first (and storage cleared).
    - Upserted accounts overwrite previous values.
    - Storage writes are applied last and skipped for addresses deleted
      in this same diff.

    Parameters
    ----------
    accounts : MutableMapping[bytes, Account]
        Target accounts mapping.
    storage : StorageView
        Target storage view.
    diff : StateDiff
        The diff to apply (no-op if empty).
    """
    if diff.is_empty():
        return

    # 1) Deletes
    for addr in diff.accounts_delete:
        accounts.pop(addr, None)
        storage.clear_account(addr)

    # 2) Upserts
    for addr, acct in diff.accounts_upsert.items():
        accounts[addr] = Account(nonce=acct.nonce, balance=acct.balance, code_hash=acct.code_hash)

    # 3) Storage writes (skip addrs that were deleted in this diff)
    skip = diff.accounts_delete
    for addr, writes in diff.storage_writes.items():
        if addr in skip:
            continue
        for k, v in writes.items():
            if v is None or len(v) == 0:
                storage.delete(addr, k)
            else:
                storage.set(addr, k, v)


def compose(a: StateDiff, b: StateDiff) -> StateDiff:
    """
    Compose two diffs `a` then `b` (apply order: first `a`, then `b`) into a single diff.

    The result is equivalent to:
        apply_diff(state, a); apply_diff(state, b)

    Rules (last-wins):
    - If `b` deletes an address, that removal overrides any upsert/storage from `a`.
    - If `b` upserts an address, it overrides `a`'s delete/upsert for that address.
    - Storage writes from `b` override `a` for same (addr,key). Writes to addresses
      deleted by `b` are dropped.
    """
    out = StateDiff()

    # Start with A
    out.accounts_delete = set(a.accounts_delete)
    out.accounts_upsert = {addr: Account(nonce=acc.nonce, balance=acc.balance, code_hash=acc.code_hash)
                           for addr, acc in a.accounts_upsert.items()}
    for addr, writes in a.storage_writes.items():
        out.storage_writes[addr] = dict(writes)

    # Merge in B with last-wins
    # 1) deletions
    for addr in b.accounts_delete:
        out.accounts_delete.add(addr)
        out.accounts_upsert.pop(addr, None)
        out.storage_writes.pop(addr, None)

    # 2) upserts
    for addr, acc in b.accounts_upsert.items():
        out.accounts_upsert[addr] = Account(nonce=acc.nonce, balance=acc.balance, code_hash=acc.code_hash)
        out.accounts_delete.discard(addr)

    # 3) storage (skip addrs deleted by B)
    for addr, writes in b.storage_writes.items():
        if addr in b.accounts_delete:
            continue
        m = out.storage_writes.get(addr)
        if m is None:
            m = {}
            out.storage_writes[addr] = m
        for k, v in writes.items():
            m[k] = v

    return out


# -----------------------------------------------------------------------------
# Convenience helpers that delegate to Journal checkpoint controls
# -----------------------------------------------------------------------------

def revert_to(journal: Journal, marker: SnapshotId) -> None:
    """
    Revert the journal overlays back to `marker` depth (discarding newer layers).
    """
    journal.revert_to(marker)


def commit_to(journal: Journal, marker: SnapshotId) -> None:
    """
    Commit the journal overlays down to `marker` depth (merging newer layers).
    """
    journal.commit_to(marker)


__all__ = [
    "SnapshotId",
    "StateDiff",
    "snapshot",
    "diff_since",
    "apply_diff",
    "compose",
    "revert_to",
    "commit_to",
]
