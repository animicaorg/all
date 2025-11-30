"""
randomness.beacon.state
=======================

Lightweight, KV-backed persistence for the beacon's ``BeaconState``.

This module intentionally avoids taking a dependency on a specific DB.
Instead, it offers:

- A simple file-backed store (atomic, single-file) for dev/test.
- A generic mapping-backed store for in-memory or adapter-provided KV.

State is serialized with ``msgspec`` (JSON) if available, or Python's
``json`` module as a fallback. The wire format is *not* consensus
critical; it's a node-local persistence detail.

Typical usage
-------------
    from randomness.types.state import BeaconState
    from randomness.beacon.state import FileBeaconStateStore

    store = FileBeaconStateStore(path="/var/lib/animica/beacon_state.json")
    try:
        st = store.load()
    except FileNotFoundError:
        st = BeaconState.defaults(genesis_time=..., commit_sec=..., reveal_sec=..., vdf_sec=..., reveal_grace_sec=0)
        store.save(st)

    # Atomic read-modify-write
    def bump_round(s: BeaconState) -> BeaconState:
        return s.with_last_round_id(s.last_round_id + 1)
    st = store.update(bump_round)

Notes
-----
- To remain decoupled, we import only the dataclass type from
  ``randomness.types.state`` and otherwise treat it opaquely.
- If you need a different backend (SQLite, RocksDB, etc.), implement
  the small ``_KV`` protocol below and wrap it in ``BeaconStateStore``.
"""

from __future__ import annotations

import io
import os
import threading
from dataclasses import asdict, is_dataclass
from typing import (Callable, MutableMapping, Optional, Protocol,
                    runtime_checkable)

# Optional high-performance codec
try:
    import msgspec  # type: ignore[attr-defined]

    _HAS_MSGSPEC = True
except Exception:  # pragma: no cover - best-effort optional dep
    _HAS_MSGSPEC = False
import json

from randomness.types.state import BeaconState

# ------------------------- Serialization helpers ------------------------- #


def _encode_state(state: BeaconState) -> bytes:
    """Serialize BeaconState to bytes (JSON)."""
    if not is_dataclass(state):
        raise TypeError("state must be a dataclass instance")
    if _HAS_MSGSPEC:
        # Use structural encoding to avoid relying on dataclass default hook.
        return msgspec.json.encode(asdict(state))  # type: ignore[name-defined]
    # Fallback: standard library json (ensure stable key ordering)
    return json.dumps(asdict(state), separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _decode_state(payload: bytes) -> BeaconState:
    """Deserialize BeaconState from bytes (JSON)."""
    if _HAS_MSGSPEC:
        data = msgspec.json.decode(payload)  # type: ignore[name-defined]
    else:
        data = json.loads(payload.decode("utf-8"))
    # Construct via the dataclass; if the type exposes a `from_dict`, prefer it.
    ctor = getattr(BeaconState, "from_dict", None)
    if callable(ctor):
        return ctor(data)  # type: ignore[misc]
    return BeaconState(**data)  # type: ignore[arg-type]


# ---------------------------- KV abstractions ---------------------------- #


@runtime_checkable
class _KV(Protocol):
    def get(self, key: bytes) -> Optional[bytes]: ...
    def put(self, key: bytes, value: bytes) -> None: ...
    def delete(self, key: bytes) -> None: ...


class _MappingKV:
    """Adapter for a dict-like mapping (bytes→bytes)."""

    def __init__(self, mapping: Optional[MutableMapping[bytes, bytes]] = None) -> None:
        self._m: MutableMapping[bytes, bytes] = mapping if mapping is not None else {}
        self._lock = threading.RLock()

    def get(self, key: bytes) -> Optional[bytes]:
        with self._lock:
            return self._m.get(key)

    def put(self, key: bytes, value: bytes) -> None:
        with self._lock:
            self._m[key] = value

    def delete(self, key: bytes) -> None:
        with self._lock:
            if key in self._m:
                del self._m[key]


# ------------------------------ File-backed ------------------------------ #


def _atomic_write(path: str, data: bytes) -> None:
    d = os.path.dirname(os.path.abspath(path)) or "."
    tmp = os.path.join(d, "." + os.path.basename(path) + ".tmp")
    # Write to a temp file then atomically replace.
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic on POSIX


class FileBeaconStateStore:
    """Persist the beacon state in a single JSON file (atomic writes)."""

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        self._lock = threading.RLock()

    def load(self) -> BeaconState:
        with self._lock:
            try:
                with open(self.path, "rb") as f:
                    payload = f.read()
            except FileNotFoundError:
                raise
            except Exception as e:  # pragma: no cover
                raise IOError(f"error reading {self.path}: {e}") from e
            return _decode_state(payload)

    def save(self, state: BeaconState) -> None:
        payload = _encode_state(state)
        with self._lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            _atomic_write(self.path, payload)

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def update(self, fn: Callable[[BeaconState], BeaconState]) -> BeaconState:
        """Atomically read→apply→write, returning the new state."""
        with self._lock:
            st = self.load()
            new_st = fn(st)
            self.save(new_st)
            return new_st


# ------------------------------ KV-backed API ---------------------------- #

_BEACON_STATE_KEY = b"beacon/state/v1"  # domain-separated key


class BeaconStateStore:
    """Generic KV-backed store for BeaconState."""

    def __init__(
        self,
        kv: Optional[_KV] = None,
        mapping: Optional[MutableMapping[bytes, bytes]] = None,
    ) -> None:
        if kv is None:
            kv = _MappingKV(mapping)
        self._kv: _KV = kv
        self._lock = threading.RLock()

    def load(self) -> BeaconState:
        with self._lock:
            payload = self._kv.get(_BEACON_STATE_KEY)
            if payload is None:
                raise FileNotFoundError("beacon state not found in KV")
            return _decode_state(payload)

    def save(self, state: BeaconState) -> None:
        payload = _encode_state(state)
        with self._lock:
            self._kv.put(_BEACON_STATE_KEY, payload)

    def delete(self) -> None:
        with self._lock:
            self._kv.delete(_BEACON_STATE_KEY)

    def exists(self) -> bool:
        with self._lock:
            return self._kv.get(_BEACON_STATE_KEY) is not None

    def load_or_init(self, default_state: BeaconState) -> BeaconState:
        with self._lock:
            try:
                return self.load()
            except FileNotFoundError:
                self.save(default_state)
                return default_state

    def update(self, fn: Callable[[BeaconState], BeaconState]) -> BeaconState:
        with self._lock:
            st = self.load()
            new_st = fn(st)
            self.save(new_st)
            return new_st


__all__ = [
    "BeaconStateStore",
    "FileBeaconStateStore",
]
