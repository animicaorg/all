from __future__ import annotations

"""
storage_api — deterministic in-memory key/value for browser simulations.

Goals
-----
- Pure-Python, dependency-free, Pyodide/WASM-friendly.
- Deterministic behavior with strict validation of keys/values.
- Namespacing by contract address (or any caller-provided namespace).
- Optional snapshots/checkpoints to support revert-on-error flows in sims.

This is a *local simulator* store. It does NOT persist across page reloads and
is intentionally simple. Production nodes wire storage via the execution/state
layer; this module mirrors only the surface that the browser VM subset needs.
"""

from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Tuple

# Reuse common errors from the trimmed VM package
from ..errors import ValidationError

# ---------------- Constants & Helpers ----------------

DEFAULT_MAX_KEY = 64  # bytes
DEFAULT_MAX_VALUE = 256 * 1024  # 256 KiB for browser sims


def _ensure_bytes(name: str, v: bytes, *, allow_empty: bool = False) -> bytes:
    if not isinstance(v, (bytes, bytearray)):
        raise ValidationError(f"{name} must be bytes, got {type(v)}")
    if not allow_empty and len(v) == 0:
        raise ValidationError(f"{name} must be non-empty bytes")
    return bytes(v)


def _clamp_size(name: str, v: bytes, *, max_len: int) -> bytes:
    if len(v) > max_len:
        raise ValidationError(f"{name} length {len(v)} exceeds max {max_len}")
    return v


def _ns_prefix(ns: bytes) -> bytes:
    # Simple domain tag for namespaced keys
    return b"\x01NS|" + ns + b"|"


# ---------------- Storage Core ----------------


class Storage:
    """
    Storage(limit_key=64, limit_value=256KiB)
    ----------------------------------------
    .get(key) -> bytes | None
    .set(key, value) -> None
    .delete(key) -> bool
    .exists(key) -> bool
    .items(prefix=b"") -> iterator[(key, value)]
    .snapshot() / .restore(snap)

    Keys and values are arbitrary bytes with configurable maximum sizes.
    """

    __slots__ = ("_kv", "_max_key", "_max_value")

    def __init__(
        self, *, limit_key: int = DEFAULT_MAX_KEY, limit_value: int = DEFAULT_MAX_VALUE
    ) -> None:
        if not isinstance(limit_key, int) or limit_key <= 0:
            raise ValidationError("limit_key must be positive int")
        if not isinstance(limit_value, int) or limit_value <= 0:
            raise ValidationError("limit_value must be positive int")
        self._kv: Dict[bytes, bytes] = {}
        self._max_key = int(limit_key)
        self._max_value = int(limit_value)

    # -------- Basic API --------

    def get(self, key: bytes) -> Optional[bytes]:
        key = _ensure_bytes("key", key)
        key = _clamp_size("key", key, max_len=self._max_key)
        return self._kv.get(key)

    def set(self, key: bytes, value: bytes) -> None:
        key = _ensure_bytes("key", key)
        value = _ensure_bytes("value", value, allow_empty=True)
        key = _clamp_size("key", key, max_len=self._max_key)
        value = _clamp_size("value", value, max_len=self._max_value)
        self._kv[key] = value

    def delete(self, key: bytes) -> bool:
        key = _ensure_bytes("key", key)
        key = _clamp_size("key", key, max_len=self._max_key)
        return self._kv.pop(key, None) is not None

    def exists(self, key: bytes) -> bool:
        key = _ensure_bytes("key", key)
        key = _clamp_size("key", key, max_len=self._max_key)
        return key in self._kv

    def items(self, *, prefix: bytes = b"") -> Iterator[Tuple[bytes, bytes]]:
        prefix = _ensure_bytes("prefix", prefix, allow_empty=True)
        for k, v in self._kv.items():
            if prefix and not k.startswith(prefix):
                continue
            yield k, v

    # -------- Snapshots --------

    @dataclass(frozen=True)
    class Snapshot:
        data: Tuple[Tuple[bytes, bytes], ...]  # immutable

    def snapshot(self) -> "Storage.Snapshot":
        # Freeze as a tuple-of-tuples to prevent accidental mutation.
        return Storage.Snapshot(tuple(self._kv.items()))

    def restore(self, snap: "Storage.Snapshot") -> None:
        if not isinstance(snap, Storage.Snapshot):
            raise ValidationError("invalid snapshot object")
        self._kv = dict(snap.data)


# ---------------- Namespaced View ----------------


class NamespaceView:
    """
    A namespaced view onto a Storage. All operations transparently prefix keys
    with a deterministic namespace tag to avoid collisions between contracts.

    Example:
        store = Storage()
        view = NamespaceView(store, ns=b"contract_addr_bytes")
        view.set(b"counter", b"\x00\x00\x00\x05")
        assert store.get(b"\x01NS|contract_addr_bytes|counter") is not None
    """

    __slots__ = ("_store", "_ns", "_max_key")

    def __init__(self, store: Storage, *, namespace: bytes) -> None:
        if not isinstance(store, Storage):
            raise ValidationError("store must be a Storage instance")
        ns = _ensure_bytes("namespace", namespace)
        self._store = store
        self._ns = _ns_prefix(ns)
        # Effective key budget subtracts namespace prefix
        self._max_key = max(0, store._max_key - len(self._ns))
        if self._max_key == 0:
            raise ValidationError("namespace too large for key limit")

    def _k(self, key: bytes) -> bytes:
        key = _ensure_bytes("key", key)
        key = _clamp_size("key", key, max_len=self._max_key)
        return self._ns + key

    # Mirror Storage API

    def get(self, key: bytes) -> Optional[bytes]:
        return self._store.get(self._k(key))

    def set(self, key: bytes, value: bytes) -> None:
        self._store.set(self._k(key), value)

    def delete(self, key: bytes) -> bool:
        return self._store.delete(self._k(key))

    def exists(self, key: bytes) -> bool:
        return self._store.exists(self._k(key))

    def items(self, *, prefix: bytes = b"") -> Iterator[Tuple[bytes, bytes]]:
        # Expose unprefixed keys to the caller
        p = self._ns + _ensure_bytes("prefix", prefix, allow_empty=True)
        for k, v in self._store.items(prefix=p):
            yield k[len(self._ns) :], v


# ---------------- Convenience (U256 encode/decode) ----------------


def u256_encode(n: int) -> bytes:
    """Encode a non-negative int into a fixed 32-byte big-endian representation."""
    if not isinstance(n, int) or n < 0:
        raise ValidationError("u256 must be non-negative int")
    if n >= 1 << 256:
        raise ValidationError("u256 overflow")
    return n.to_bytes(32, "big")


def u256_decode(b: bytes) -> int:
    """Decode a fixed 32-byte big-endian into int."""
    b = _ensure_bytes("u256", b)
    if len(b) != 32:
        raise ValidationError("u256 must be exactly 32 bytes")
    return int.from_bytes(b, "big")


__all__ = [
    "Storage",
    "NamespaceView",
    "u256_encode",
    "u256_decode",
]
