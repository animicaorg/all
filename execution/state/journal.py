"""
execution.state.journal — journaling writes, checkpoints, revert/commit.

This module provides a deterministic, in-memory write journal layered over
an accounts mapping and a StorageView. It supports nested checkpoints via a
stack of overlays. Writes go to the top overlay; reads consult overlays from
top → base. `commit()` merges the top overlay into the next layer (or the base
state if it's the last layer). `revert()` discards the top overlay.

Key properties
--------------
- Pure Python, no I/O; safe for unit tests and simulations.
- Copy-on-write for accounts (Account objects are copied into overlays).
- Storage overlay per (address, key) with explicit deletion markers.
- Nested checkpoints (begin/commit/revert) with O(changes) merge cost.
- Deterministic behavior; no reliance on wall clock or randomness.

Intended usage
--------------
    j = Journal(base_accounts, base_storage)
    j.begin()                       # start a checkpoint
    acc = j.get_account_for_write(addr) or j.create_account(addr, initial_balance=123)
    acc.increment_nonce()
    j.storage_set(addr, key, b"value")
    j.commit()                      # apply to parent/base

If you need to stage multiple independent groups of changes, call `begin()`
for each group. Use `revert()` to discard staged changes in the top checkpoint.

Notes
-----
- This journal does not enforce economic rules; callers (execution/runtime)
  should perform validation/charging before writes.
- Deleting an account also clears its storage at commit time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (Dict, Iterator, List, Mapping, MutableMapping, Optional,
                    Set, Tuple)

from execution.errors import StateConflict

from .accounts import EMPTY_CODE_HASH, Account
from .storage import StorageView


def _b(x: bytes | bytearray | memoryview, *, name: str) -> bytes:
    if not isinstance(x, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    return bytes(x)


# =============================================================================
# Overlay model
# =============================================================================


@dataclass
class _Overlay:
    """
    A single journal layer.

    - `accounts`: copies of Account objects modified/created in this layer.
    - `destroyed`: addresses marked for deletion in this layer.
    - `storage`: staged storage changes. `None` means deletion for that key.
    """

    accounts: Dict[bytes, Account] = field(default_factory=dict)
    destroyed: Set[bytes] = field(default_factory=set)
    storage: Dict[bytes, Dict[bytes, Optional[bytes]]] = field(default_factory=dict)

    # --- account ops ---

    def get_account_local(self, addr: bytes) -> Optional[Account]:
        if addr in self.destroyed:
            return None
        return self.accounts.get(addr)

    def put_account_copy(self, addr: bytes, acc: Account) -> Account:
        # Store a *copy* to avoid aliasing with lower layers.
        acc_copy = Account(
            nonce=acc.nonce, balance=acc.balance, code_hash=acc.code_hash
        )
        self.accounts[addr] = acc_copy
        self.destroyed.discard(addr)
        return acc_copy

    def create_account_fresh(
        self, addr: bytes, initial_balance: int = 0, code_hash: Optional[bytes] = None
    ) -> Account:
        if addr in self.accounts and addr not in self.destroyed:
            raise StateConflict("account already exists in current overlay")
        self.destroyed.discard(addr)
        acc = Account(
            nonce=0,
            balance=int(initial_balance),
            code_hash=(EMPTY_CODE_HASH if code_hash is None else bytes(code_hash)),
        )
        self.accounts[addr] = acc
        return acc

    def destroy_account_here(self, addr: bytes) -> None:
        self.accounts.pop(addr, None)
        self.destroyed.add(addr)
        # Drop any staged storage in this layer (it becomes moot).
        self.storage.pop(addr, None)

    # --- storage ops ---

    def storage_get_local(self, addr: bytes, key: bytes) -> Optional[bytes]:
        m = self.storage.get(addr)
        if m is None:
            return None
        return m.get(key, None)

    def storage_set_local(
        self, addr: bytes, key: bytes, value: Optional[bytes]
    ) -> None:
        if addr in self.destroyed:
            # Writes to a destroyed account are irrelevant in this layer.
            return
        m = self.storage.get(addr)
        if m is None:
            m = {}
            self.storage[addr] = m
        m[key] = value


# =============================================================================
# Journal
# =============================================================================


class Journal:
    """
    A copy-on-write write journal with nested checkpoints.

    Parameters
    ----------
    accounts : MutableMapping[bytes, Account]
        The base (persisted) account mapping.
    storage : StorageView
        The base storage view.

    API highlights
    --------------
    - begin() / commit() / revert()
    - get_account(), get_account_for_write(), create_account(), destroy_account()
    - storage_get(), storage_set(), storage_delete()
    - checkpoint() / commit_to(marker) / revert_to(marker)

    Reads consult overlays from top to bottom and then the base. Writes always
    target the top overlay.
    """

    def __init__(
        self, accounts: MutableMapping[bytes, Account], storage: StorageView
    ) -> None:
        self._base_accounts = accounts
        self._base_storage = storage
        # Start with a single empty overlay for convenience.
        self._layers: List[_Overlay] = [_Overlay()]

    # --------------------------------------------------------------------- #
    # Checkpointing
    # --------------------------------------------------------------------- #

    def depth(self) -> int:
        """Number of overlays (>= 1)."""
        return len(self._layers)

    def begin(self) -> int:
        """Start a new checkpoint. Returns the new depth marker (int)."""
        self._layers.append(_Overlay())
        return len(self._layers)

    def commit(self) -> None:
        """
        Commit the top overlay into its parent or the base state if there is no
        parent (i.e., only the root layer remains).
        """
        if not self._layers:
            raise RuntimeError("journal has no layers")  # should never happen

        top = self._layers.pop()
        if not self._layers:
            # Shouldn't happen because we always keep a root; but guard anyway.
            self._apply_to_base(top)
            self._layers.append(_Overlay())
            return

        # If there is still a layer left, we merged into it; else we applied to base.
        parent_exists = len(self._layers) >= 1
        if parent_exists:
            parent = self._layers[-1]
            self._merge_layers(parent, top)
        else:
            self._apply_to_base(top)
            # Keep a fresh root layer for further journaling.
            self._layers.append(_Overlay())

    def revert(self) -> None:
        """Discard the top overlay (or clear it if it's the root)."""
        if len(self._layers) > 1:
            self._layers.pop()
        else:
            # Clear root layer
            self._layers[0] = _Overlay()

    # Markers for convenience ------------------------------------------------ #

    def checkpoint(self) -> int:
        """Alias for `begin()` returning a marker token (current depth)."""
        return self.begin()

    def commit_to(self, marker: int) -> None:
        """
        Commit repeatedly until the current depth equals `marker`.
        Committing to depth==1 applies to the base state.
        """
        if marker < 1:
            raise ValueError("marker must be >= 1")
        while len(self._layers) > marker:
            self.commit()

    def revert_to(self, marker: int) -> None:
        """
        Revert repeatedly until the current depth equals `marker`.
        Reverting to depth==1 clears the root layer.
        """
        if marker < 1:
            raise ValueError("marker must be >= 1")
        while len(self._layers) > marker:
            self.revert()

    # --------------------------------------------------------------------- #
    # Account API (read & write)
    # --------------------------------------------------------------------- #

    def _lookup_account_any(self, addr: bytes) -> Optional[Account]:
        # Scan overlays from top → root
        for layer in reversed(self._layers):
            if addr in layer.destroyed:
                return None
            local = layer.get_account_local(addr)
            if local is not None:
                return local
        # Fallback to base (unless any layer below marked it destroyed)
        return self._base_accounts.get(addr)

    def get_account(self, address: bytes | bytearray | memoryview) -> Optional[Account]:
        """Readonly lookup. Returns an Account object from overlay or base (do not mutate base!)."""
        addr = _b(address, name="address")
        return self._lookup_account_any(addr)

    def get_account_for_write(
        self, address: bytes | bytearray | memoryview
    ) -> Optional[Account]:
        """
        Fetch an Account suitable for **mutation** in the top layer:
        - If present in any lower layer/base, a copy is promoted to the top.
        - If absent (including destroyed in any layer), returns None.
        """
        addr = _b(address, name="address")
        top = self._layers[-1]
        if addr in top.destroyed:
            return None
        if addr in top.accounts:
            return top.accounts[addr]

        # Check lower layers/base for a source to copy.
        acc = self._lookup_account_any(addr)
        if acc is None:
            return None
        return top.put_account_copy(addr, acc)

    def ensure_account_for_write(
        self, address: bytes | bytearray | memoryview
    ) -> Account:
        """
        Ensure an Account exists for mutation in the top layer.
        If absent anywhere, a fresh zeroed account is created.
        """
        addr = _b(address, name="address")
        acc = self.get_account_for_write(addr)
        if acc is not None:
            return acc
        # If destroyed in any layer, we "resurrect" by creating a fresh account here.
        return self._layers[-1].create_account_fresh(addr)

    # Lifecycle helpers ------------------------------------------------------ #

    def create_account(
        self,
        address: bytes | bytearray | memoryview,
        *,
        initial_balance: int = 0,
        code_hash: Optional[bytes] = None,
    ) -> Account:
        """
        Create a new account in the **top** overlay.
        Raises StateConflict if the account exists in any layer/base (and not destroyed at the top).
        """
        addr = _b(address, name="address")
        # If it exists in any visible layer/base and is not shadowed by destruction in top, conflict.
        existing = self._lookup_account_any(addr)
        if existing is not None and addr not in self._layers[-1].destroyed:
            raise StateConflict("account already exists")
        return self._layers[-1].create_account_fresh(
            addr, initial_balance=initial_balance, code_hash=code_hash
        )

    def destroy_account(self, address: bytes | bytearray | memoryview) -> bool:
        """
        Mark account for deletion in the top overlay. Returns True if an account
        exists in visible state (overlay/base) and is now marked destroyed.
        """
        addr = _b(address, name="address")
        visible = self._lookup_account_any(addr)
        if visible is None:
            # Nothing to destroy; idempotent no-op (still mark as destroyed to shadow later creates).
            self._layers[-1].destroy_account_here(addr)
            return False
        self._layers[-1].destroy_account_here(addr)
        return True

    # --------------------------------------------------------------------- #
    # Storage API
    # --------------------------------------------------------------------- #

    def storage_get(
        self,
        address: bytes | bytearray | memoryview,
        key: bytes | bytearray | memoryview,
        default: bytes = b"",
    ) -> bytes:
        """
        Read storage with overlay precedence. Returns `default` if absent.
        """
        addr = _b(address, name="address")
        key_b = _b(key, name="key")

        # Any layer marking account destroyed shadows storage completely.
        for layer in reversed(self._layers):
            if addr in layer.destroyed:
                return default
            local = layer.storage_get_local(addr, key_b)
            if local is not None:
                return default if local is None else local

        # Fallback to base
        return self._base_storage.get(addr, key_b, default=default)

    def storage_set(
        self,
        address: bytes | bytearray | memoryview,
        key: bytes | bytearray | memoryview,
        value: bytes | bytearray | memoryview,
    ) -> None:
        """
        Stage a storage write in the top overlay. Empty value is a deletion.
        """
        addr = _b(address, name="address")
        key_b = _b(key, name="key")
        val_b = _b(value, name="value")
        top = self._layers[-1]
        if len(val_b) == 0:
            top.storage_set_local(addr, key_b, None)
        else:
            top.storage_set_local(addr, key_b, val_b)

    def storage_delete(
        self,
        address: bytes | bytearray | memoryview,
        key: bytes | bytearray | memoryview,
    ) -> None:
        """Explicit storage deletion in the top overlay."""
        addr = _b(address, name="address")
        key_b = _b(key, name="key")
        self._layers[-1].storage_set_local(addr, key_b, None)

    # Iteration helpers ------------------------------------------------------ #

    def storage_items(
        self, address: bytes | bytearray | memoryview
    ) -> Iterator[Tuple[bytes, bytes]]:
        """
        Iterate visible (key, value) for an address with overlay precedence.
        Stable order by key. Deletions in overlays are respected.
        """
        addr = _b(address, name="address")

        # If destroyed in any overlay, no storage is visible.
        for layer in reversed(self._layers):
            if addr in layer.destroyed:
                return iter(())  # type: ignore[return-value]

        # Start from base, apply overlay diffs.
        base_map = dict(self._base_storage.export_account_hex(addr))
        visible: Dict[bytes, bytes] = {
            bytes.fromhex(k): bytes.fromhex(v) for k, v in base_map.items()
        }

        # Apply overlays from bottom → top to get final view.
        for layer in self._layers:
            m = layer.storage.get(addr)
            if not m:
                continue
            for k, v in m.items():
                if v is None:
                    visible.pop(k, None)
                else:
                    visible[k] = v

        for k in sorted(visible.keys()):
            yield k, visible[k]

    # --------------------------------------------------------------------- #
    # Internal merge/apply
    # --------------------------------------------------------------------- #

    @staticmethod
    def _merge_layers(dst: _Overlay, src: _Overlay) -> None:
        """
        Merge `src` overlay into `dst` overlay (same-layer merge used for nested
        commits). This does *not* touch the base state.
        """
        # 1) Apply account destructions (excluding addresses resurrected in src.accounts)
        for addr in src.destroyed - set(src.accounts.keys()):
            dst.destroyed.add(addr)
            dst.accounts.pop(addr, None)
            dst.storage.pop(addr, None)

        # 2) Apply account upserts (these override `destroyed`)
        for addr, acc in src.accounts.items():
            dst.accounts[addr] = Account(
                nonce=acc.nonce, balance=acc.balance, code_hash=acc.code_hash
            )
            dst.destroyed.discard(addr)

        # 3) Apply storage writes (skip addresses ultimately destroyed)
        for addr, writes in src.storage.items():
            if addr in dst.destroyed:
                continue
            dm = dst.storage.get(addr)
            if dm is None:
                dm = {}
                dst.storage[addr] = dm
            for k, v in writes.items():
                dm[k] = v

    def _apply_to_base(self, layer: _Overlay) -> None:
        """
        Apply a single overlay to the base accounts/storage.
        """
        # Destroyed accounts first.
        for addr in layer.destroyed:
            self._base_accounts.pop(addr, None)
            # Clear all storage for the address.
            self._base_storage.clear_account(addr)

        # Account upserts (overrides previous base).
        for addr, acc in layer.accounts.items():
            self._base_accounts[addr] = Account(
                nonce=acc.nonce, balance=acc.balance, code_hash=acc.code_hash
            )

        # Storage writes (skip addresses that were destroyed in this layer).
        for addr, writes in layer.storage.items():
            if addr in layer.destroyed:
                continue
            for k, v in writes.items():
                if v is None or len(v) == 0:
                    self._base_storage.delete(addr, k)
                else:
                    self._base_storage.set(addr, k, v)

    # --------------------------------------------------------------------- #
    # Debug/Introspection
    # --------------------------------------------------------------------- #

    def pending_account_addrs(self) -> Set[bytes]:
        """Addresses with pending account mutations in any layer."""
        s: Set[bytes] = set()
        for layer in self._layers:
            s.update(layer.accounts.keys())
            s.update(layer.destroyed)
        return s

    def pending_storage_keys(self) -> int:
        """Total number of staged storage (addr,key) entries across layers."""
        n = 0
        for layer in self._layers:
            n += sum(len(w) for w in layer.storage.values())
        return n


__all__ = ["Journal"]
