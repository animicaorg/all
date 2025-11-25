"""
randomness.store
================

Package marker and light abstractions for storage backends used by the
randomness subsystem (commit–reveal records, VDF inputs/proofs, beacon
state/history, etc.).

Backends are intentionally pluggable (in-memory, SQLite, RocksDB, …). This
module exposes small typing protocols so higher layers can depend on stable
interfaces without pulling in a concrete DB.

Nothing here is consensus-critical; only bytes go in/out and callers perform
their own hashing/validation.
"""

from __future__ import annotations

from typing import Iterable, Optional, Protocol, Tuple

# Re-export module version for convenience
try:  # Local import to avoid cycles if version imports store.*
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover - during isolated typing/imports
    __version__ = "0.0.0"


class KeyValue(Protocol):
    """Minimal byte-oriented KV interface.

    Keys and values are raw bytes. Namespaces (if needed) should be handled by
    the caller via prefixed keys, e.g., b"beacon:" + round_id_bytes.
    """

    def get(self, key: bytes) -> Optional[bytes]:
        """Return value for key, or None if missing."""
        ...

    def put(self, key: bytes, value: bytes) -> None:
        """Insert or replace key with value."""
        ...

    def delete(self, key: bytes) -> None:
        """Remove key if present (no-op if absent)."""
        ...

    def has(self, key: bytes) -> bool:
        """Return True if key exists."""
        ...

    def iter_prefix(self, prefix: bytes) -> Iterable[Tuple[bytes, bytes]]:
        """Yield (key, value) pairs whose keys start with prefix.

        Ordering is backend-defined; callers must not rely on iteration order.
        """
        ...


__all__ = [
    "__version__",
    "KeyValue",
]
