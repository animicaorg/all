# -*- coding: utf-8 -*-
"""
Property tests for VM/storage primitives.

Covers two layers (skip gracefully if modules aren't present):

1) vm_py.runtime.storage_api — basic put/get invariants
   - set(k,v) then get(k) == v
   - overwrite is last-wins

2) execution.state.journal — revert/commit "laws"
   - checkpoint → writes → revert  ⇒ state equals baseline
   - checkpoint → writes → commit ⇒ state equals baseline ∪ writes (last-wins)
   - nested checkpoints behave as a stack (inner revert keeps outer writes)

These tests are defensive against naming differences and will try multiple
method names. If a capability isn't available yet, tests are skipped with a
clear reason instead of failing.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Optional, Tuple

import pytest
from hypothesis import given, settings, strategies as st


# -----------------------------------------------------------------------------
# Utilities: bytes keys/values, small maps
# -----------------------------------------------------------------------------

HKEY = st.binary(min_size=16, max_size=32)
HVAL = st.binary(min_size=0, max_size=128)
MAP_SMALL = st.dictionaries(keys=HKEY, values=HVAL, min_size=0, max_size=16)
MAP_NONEMPTY = st.dictionaries(keys=HKEY, values=HVAL, min_size=1, max_size=16)


def _merge_last_wins(base: Dict[bytes, bytes], upd: Dict[bytes, bytes]) -> Dict[bytes, bytes]:
    out = dict(base)
    out.update(upd)
    return out


# -----------------------------------------------------------------------------
# Adapters: vm_py.runtime.storage_api (put/get)
# -----------------------------------------------------------------------------

_vm_mod = None
try:
    import vm_py.runtime.storage_api as _vm_mod  # type: ignore
except Exception:
    _vm_mod = None


class _VmStoreAdapter:
    """
    Best-effort adapter over vm_py.runtime.storage_api.

    We support either a class (e.g., Storage) with get/set methods or a module-level
    get/set operating on a global store. In all cases, we expose .get(key) and .set(key, val).
    """

    def __init__(self):
        self._store_obj = None
        if _vm_mod is None:
            raise RuntimeError("vm_py.runtime.storage_api not available")

        # Try to find a storage class to instantiate
        for cls_name in ("Storage", "Store", "KV", "KeyValue"):
            cls = getattr(_vm_mod, cls_name, None)
            if cls is not None:
                try:
                    self._store_obj = cls()  # type: ignore[call-arg]
                except Exception:
                    # Try no-arg; if it fails, we'll fall back to module funcs
                    self._store_obj = None
                break

        # Discover method names
        self._get_name = None
        self._set_name = None
        for name in ("get", "load", "read", "get_bytes"):
            if self._store_obj is not None and hasattr(self._store_obj, name):
                self._get_name = name
                break
            if hasattr(_vm_mod, name):
                self._get_name = name
                break

        for name in ("set", "put", "write", "set_bytes"):
            if self._store_obj is not None and hasattr(self._store_obj, name):
                self._set_name = name
                break
            if hasattr(_vm_mod, name):
                self._set_name = name
                break

        if not (self._get_name and self._set_name):
            raise RuntimeError("No compatible get/set found on storage_api")

    def set(self, key: bytes, val: bytes) -> None:
        if self._store_obj is not None and hasattr(self._store_obj, self._set_name):  # type: ignore[arg-type]
            getattr(self._store_obj, self._set_name)(key, val)  # type: ignore[misc]
        else:
            getattr(_vm_mod, self._set_name)(key, val)  # type: ignore[misc]

    def get(self, key: bytes) -> Optional[bytes]:
        try:
            if self._store_obj is not None and hasattr(self._store_obj, self._get_name):  # type: ignore[arg-type]
                return getattr(self._store_obj, self._get_name)(key)  # type: ignore[misc]
            return getattr(_vm_mod, self._get_name)(key)  # type: ignore[misc]
        except KeyError:
            return None
        except Exception:
            # Some APIs return None for missing, some raise
            return None


def _has_vm_store() -> bool:
    try:
        _ = _VmStoreAdapter()
        return True
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Adapters: execution.state.journal (checkpoint/commit/revert)
# -----------------------------------------------------------------------------

_journal_mod = None
try:
    import execution.state.journal as _journal_mod  # type: ignore
except Exception:
    _journal_mod = None


class _JournalAdapter:
    """
    Adapter over a journaled key/value state with checkpoints.

    Expected methods (we try common variants):
      - set / put (key, val)
      - get / load (key) -> Optional[bytes] (None/missing)
      - checkpoint() / begin() -> token
      - commit(token?) / commit_checkpoint(token?) / end(token?)  (support both with/without token)
      - revert(token) / rollback(token) / abort(token)

    We always pass and require a token for revert; commit token optional.
    """

    def __init__(self):
        if _journal_mod is None:
            raise RuntimeError("execution.state.journal not available")

        # Find a journal class or factory
        self._obj = None
        ctor = None
        for name in ("Journal", "JournaledState", "StateJournal", "KVJournal"):
            ctor = getattr(_journal_mod, name, None)
            if ctor is not None:
                break
        if ctor is None:
            # Try a factory helper
            for name in ("new", "new_journal", "make", "make_journal"):
                ctor = getattr(_journal_mod, name, None)
                if ctor is not None:
                    break

        if ctor is None:
            raise RuntimeError("No journal constructor/factory found")

        try:
            self._obj = ctor()  # type: ignore[misc]
        except Exception:
            # Some journaling layers require an underlying dict/KV; try without args, else with a plain dict
            try:
                self._obj = ctor({})  # type: ignore[misc]
            except Exception as exc:
                raise RuntimeError(f"Failed to construct journal: {exc}") from exc

        # Discover method names
        self._set_name = next((n for n in ("set", "put", "write", "set_bytes") if hasattr(self._obj, n)), None)
        self._get_name = next((n for n in ("get", "load", "read", "get_bytes") if hasattr(self._obj, n)), None)
        self._cp_name = next((n for n in ("checkpoint", "begin", "start") if hasattr(self._obj, n)), None)
        self._rv_name = next((n for n in ("revert", "rollback", "abort") if hasattr(self._obj, n)), None)
        self._cm_name = next((n for n in ("commit", "commit_checkpoint", "end") if hasattr(self._obj, n)), None)

        for attr, nm in {
            "set": self._set_name,
            "get": self._get_name,
            "checkpoint": self._cp_name,
            "revert": self._rv_name,
            "commit": self._cm_name,
        }.items():
            if not nm:
                raise RuntimeError(f"Journal is missing required method for {attr}")

        # Inspect commit signature to see if it expects a token
        self._commit_takes_token = False
        try:
            sig = inspect.signature(getattr(self._obj, self._cm_name))
            # If there is at least one non-vararg parameter other than self, assume it takes a token
            params = [p for p in sig.parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            # first param likely 'self'; count beyond
            self._commit_takes_token = len(params) >= 2
        except Exception:
            self._commit_takes_token = False

    # Basic KV ops
    def set(self, k: bytes, v: bytes) -> None:
        getattr(self._obj, self._set_name)(k, v)  # type: ignore[misc]

    def get(self, k: bytes) -> Optional[bytes]:
        try:
            return getattr(self._obj, self._get_name)(k)  # type: ignore[misc]
        except KeyError:
            return None
        except Exception:
            return None

    # Checkpoint ops
    def checkpoint(self) -> Any:
        return getattr(self._obj, self._cp_name)()  # type: ignore[misc]

    def revert(self, token: Any) -> None:
        getattr(self._obj, self._rv_name)(token)  # type: ignore[misc]

    def commit(self, token: Any) -> None:
        if self._commit_takes_token:
            getattr(self._obj, self._cm_name)(token)  # type: ignore[misc]
        else:
            getattr(self._obj, self._cm_name)()  # type: ignore[misc]


def _has_journal() -> bool:
    try:
        _ = _JournalAdapter()
        return True
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Tests: vm_py storage put/get invariants
# -----------------------------------------------------------------------------

@pytest.mark.skipif(not _has_vm_store(), reason="vm_py.runtime.storage_api not available or lacks get/set")
@given(MAP_SMALL)
@settings(max_examples=150)
def test_vm_storage_put_then_get_roundtrip(m: Dict[bytes, bytes]):
    """Setting a key then reading it yields the same bytes (round-trip)."""
    store = _VmStoreAdapter()
    # Put all, then get each
    for k, v in m.items():
        store.set(k, v)
    for k, v in m.items():
        got = store.get(k)
        assert got == v, f"expected exact round-trip for key={k!r}"


@pytest.mark.skipif(not _has_vm_store(), reason="vm_py.runtime.storage_api not available or lacks get/set")
@given(MAP_NONEMPTY, MAP_SMALL)
@settings(max_examples=150)
def test_vm_storage_overwrite_last_wins(initial: Dict[bytes, bytes], updates: Dict[bytes, bytes]):
    """Overwriting the same key is last-wins."""
    store = _VmStoreAdapter()
    # Apply initial writes
    for k, v in initial.items():
        store.set(k, v)
    # Apply updates (possibly overlapping keys)
    for k, v in updates.items():
        store.set(k, v)

    model = _merge_last_wins(initial, updates)
    # Verify a sample of touched keys (union of both maps)
    touched = set(initial.keys()) | set(updates.keys())
    for k in touched:
        assert store.get(k) == model.get(k)


# -----------------------------------------------------------------------------
# Tests: execution.state.journal revert/commit laws
# -----------------------------------------------------------------------------

@pytest.mark.skipif(not _has_journal(), reason="execution.state.journal not available or incompatible")
@given(MAP_SMALL, MAP_SMALL)
@settings(max_examples=120)
def test_journal_revert_restores_baseline(baseline: Dict[bytes, bytes], changes: Dict[bytes, bytes]):
    """checkpoint → changes → revert ⇒ state equals baseline."""
    j = _JournalAdapter()
    # Establish baseline
    for k, v in baseline.items():
        j.set(k, v)
    # Snapshot
    cp = j.checkpoint()
    # Apply changes
    for k, v in changes.items():
        j.set(k, v)
    # Revert
    j.revert(cp)
    # Validate baseline restored
    all_keys = set(baseline.keys()) | set(changes.keys())
    for k in all_keys:
        assert j.get(k) == baseline.get(k)


@pytest.mark.skipif(not _has_journal(), reason="execution.state.journal not available or incompatible")
@given(MAP_SMALL, MAP_SMALL)
@settings(max_examples=120)
def test_journal_commit_accumulates_changes(baseline: Dict[bytes, bytes], changes: Dict[bytes, bytes]):
    """checkpoint → changes → commit ⇒ state equals baseline ∪ changes (last-wins)."""
    j = _JournalAdapter()
    for k, v in baseline.items():
        j.set(k, v)
    cp = j.checkpoint()
    for k, v in changes.items():
        j.set(k, v)
    j.commit(cp)

    model = _merge_last_wins(baseline, changes)
    all_keys = set(baseline.keys()) | set(changes.keys())
    for k in all_keys:
        assert j.get(k) == model.get(k)


@pytest.mark.skipif(not _has_journal(), reason="execution.state.journal not available or incompatible")
@given(MAP_SMALL, MAP_SMALL, MAP_SMALL)
@settings(max_examples=100)
def test_journal_nested_checkpoints_stack_law(
    base: Dict[bytes, bytes],
    outer_changes: Dict[bytes, bytes],
    inner_changes: Dict[bytes, bytes],
):
    """
    Nested checkpoints form a stack:
      cp1; apply A; cp2; apply B; revert cp2; commit cp1
      ⇒ state == base ∪ A (B discarded).
    """
    j = _JournalAdapter()
    # Base
    for k, v in base.items():
        j.set(k, v)

    cp1 = j.checkpoint()
    for k, v in outer_changes.items():
        j.set(k, v)

    cp2 = j.checkpoint()
    for k, v in inner_changes.items():
        j.set(k, v)

    # Revert inner; commit outer
    j.revert(cp2)
    j.commit(cp1)

    model = _merge_last_wins(base, outer_changes)
    keys = set(base) | set(outer_changes) | set(inner_changes)
    for k in keys:
        assert j.get(k) == model.get(k), "Nested revert/commit stack law violated"


