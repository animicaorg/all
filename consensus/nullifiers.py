"""
Nullifier stores (TTL, sliding window, persistence hooks)
=========================================================

This module provides a small, deterministic API for tracking *proof nullifiers*
over a sliding height window. It is used by consensus validation to reject
duplicate proof reuse within the recent window.

Two implementations are provided:

  • MemoryNullifierStore: fast in-memory store with O(1) membership and
    O(k) prune by-height using per-height buckets (k = number of nullifiers
    expiring at that exact height).

  • KVNullifierStore: backed by a generic KV interface (get/put/delete/
    iter_prefix). Pruning is O(N) over the prefix, suitable for moderate
    volumes or RocksDB/SQLite where prefix iteration is efficient.

Both expose:

    seen(nullifier: bytes) -> bool
    record(nullifier: bytes, height: int) -> None
    prune(current_height: int) -> int   # returns number pruned (optional)
    size() -> int                       # cardinality within the window

The *window* is expressed in block heights. A nullifier recorded at height H
remains active until current_height - H > window, after which it is pruned.

Notes
-----
- Deterministic (no wall-clock). Height is the only time source.
- No crypto here. Nullifiers are opaque bytes (already domain-separated upstream).
- Thread-safety is not provided; coordinate accesses at a higher layer if needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, Optional, Protocol, Tuple

# -----------------------------------------------------------------------------
# Public protocol (mirrors usage from consensus/validator.py)
# -----------------------------------------------------------------------------


class NullifierStore(Protocol):
    def seen(self, nullifier: bytes) -> bool: ...
    def record(self, nullifier: bytes, height: int) -> None: ...
    def prune(self, current_height: int) -> int: ...
    def size(self) -> int: ...


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    window: int = 10_000
    """
    Sliding window length in blocks. A nullifier recorded at height H expires
    strictly after current_height - H > window.
    """
    max_entries: int = 5_000_000
    """
    Soft cap for the number of in-window entries. Memory store enforces this
    by evicting the *oldest* buckets first when exceeded; KV store logs a hint
    via return value of prune() pressure (handled by caller).
    """


# -----------------------------------------------------------------------------
# In-memory implementation (bucketed by height)
# -----------------------------------------------------------------------------


class MemoryNullifierStore:
    """
    Fast in-memory TTL set.

    Data structures:
      - _by_null: {nullifier -> height}
      - _by_height: {height -> set[nullifier]} to prune in O(k) per expired height
      - _min_live_height: smallest height that still has any entries (for fast sweep)
    """

    __slots__ = ("cfg", "_by_null", "_by_height", "_min_live_height")

    def __init__(self, cfg: Config):
        if cfg.window < 0:
            raise ValueError("window must be non-negative")
        self.cfg = cfg
        self._by_null: Dict[bytes, int] = {}
        self._by_height: Dict[int, set[bytes]] = {}
        self._min_live_height: Optional[int] = None

    # Protocol methods ---------------------------------------------------------

    def seen(self, nullifier: bytes) -> bool:
        """Return True if this nullifier is currently active within the window."""
        h = self._by_null.get(nullifier)
        return h is not None

    def record(self, nullifier: bytes, height: int) -> None:
        """
        Record a nullifier at a given height. If it already exists at the same
        height, this is a no-op; if it exists at a different height (shouldn't
        happen in correct usage), we keep the *earliest* height to be strict.
        """
        if height < 0:
            raise ValueError("height must be non-negative")
        existing = self._by_null.get(nullifier)
        if existing is not None:
            # Keep earliest; nothing to do if already present.
            return

        # Insert
        self._by_null[nullifier] = height
        bucket = self._by_height.get(height)
        if bucket is None:
            bucket = set()
            self._by_height[height] = bucket
        bucket.add(nullifier)
        if self._min_live_height is None or height < self._min_live_height:
            self._min_live_height = height

        # Oversize guard: opportunistic pruning hint — evict oldest buckets first.
        if (
            len(self._by_null) > self.cfg.max_entries
            and self._min_live_height is not None
        ):
            # Evict until under cap (bounded by window; no correctness impact if we prune more).
            target = len(self._by_null) - self.cfg.max_entries
            pruned = 0
            h_cursor = self._min_live_height
            # Sweep through oldest buckets
            while pruned < target and h_cursor in self._by_height:
                pruned += self._evict_height_bucket(h_cursor)
                h_cursor += 1
            # Update min_live_height
            self._advance_min_live_from(h_cursor)

    def prune(self, current_height: int) -> int:
        """Prune entries strictly older than current_height - cfg.window."""
        if current_height < 0:
            raise ValueError("current_height must be non-negative")
        if self._min_live_height is None:
            return 0

        cutoff = current_height - self.cfg.window
        if cutoff <= 0:
            return 0

        pruned = 0
        h = self._min_live_height
        while h <= cutoff:
            if h not in self._by_height:
                h += 1
                continue
            pruned += self._evict_height_bucket(h)
            h += 1
        self._advance_min_live_from(h)
        return pruned

    def size(self) -> int:
        return len(self._by_null)

    # Internals ---------------------------------------------------------------

    def _evict_height_bucket(self, height: int) -> int:
        bucket = self._by_height.pop(height, None)
        if not bucket:
            return 0
        for n in bucket:
            self._by_null.pop(n, None)
        return len(bucket)

    def _advance_min_live_from(self, start_h: int) -> None:
        # Find the next height that still has a bucket, else None
        h = start_h
        while h in self._by_height:
            # if we immediately find a bucket at start_h (rare), keep it
            self._min_live_height = h
            return
        # scan forward until we see a present height or exhaust small range
        # (note: this is a conservative O(W) step in the worst case if buckets
        # are sparse; acceptable given prune is called at block cadence)
        for offset in range(1, 1024):
            cand = h + offset
            if cand in self._by_height:
                self._min_live_height = cand
                return
        # Give up: compute min from keys if any remain
        if self._by_height:
            self._min_live_height = min(self._by_height.keys())
        else:
            self._min_live_height = None


# -----------------------------------------------------------------------------
# Generic KV interface + KV-backed implementation
# -----------------------------------------------------------------------------


class KV(Protocol):
    """Minimal KV used by KVNullifierStore. Keys and values are raw bytes."""

    def get(self, key: bytes) -> Optional[bytes]: ...
    def put(self, key: bytes, value: bytes) -> None: ...
    def delete(self, key: bytes) -> None: ...
    def iter_prefix(self, prefix: bytes) -> Iterator[Tuple[bytes, bytes]]: ...


class InMemoryKV:
    """A tiny in-memory KV with prefix iteration for tests/dev."""

    __slots__ = ("_m",)

    def __init__(self):
        self._m: Dict[bytes, bytes] = {}

    def get(self, key: bytes) -> Optional[bytes]:
        return self._m.get(key)

    def put(self, key: bytes, value: bytes) -> None:
        self._m[key] = value

    def delete(self, key: bytes) -> None:
        self._m.pop(key, None)

    def iter_prefix(self, prefix: bytes) -> Iterator[Tuple[bytes, bytes]]:
        for k, v in self._m.items():
            if k.startswith(prefix):
                yield k, v


def _u64be(x: int) -> bytes:
    return x.to_bytes(8, "big", signed=False)


def _from_u64be(b: bytes) -> int:
    if len(b) != 8:
        raise ValueError("height value must be 8 bytes")
    return int.from_bytes(b, "big", signed=False)


class KVNullifierStore:
    """
    Persistent nullifier TTL set implemented on a generic KV.

    Layout (prefixes):
      NS = b"null/" (namespace)
      • NS + b"n/" + <32..256B nullifier>  -> height(8B big-endian)
        Used for quick membership checks and prune-by-value.

    Pruning strategy:
      - Iterate NS + b"n/" and delete entries with height < cutoff.
      - Complexity is O(N_prefix) per prune; good KV backends make this fast.

    Size:
      - Tracked lazily by counting during prune plus a moving counter.
      - `size()` returns an approximate count unless a full recount was performed.
    """

    __slots__ = ("cfg", "_kv", "_ns", "_approx_size")

    def __init__(self, cfg: Config, kv: KV, namespace: bytes = b"null/"):
        if cfg.window < 0:
            raise ValueError("window must be non-negative")
        self.cfg = cfg
        self._kv = kv
        self._ns = namespace if namespace.endswith(b"/") else namespace + b"/"
        self._approx_size = 0  # best-effort; corrected on prune()

    # Keys
    def _k_n(self, n: bytes) -> bytes:
        return self._ns + b"n/" + n

    # Protocol methods ---------------------------------------------------------

    def seen(self, nullifier: bytes) -> bool:
        v = self._kv.get(self._k_n(nullifier))
        return v is not None

    def record(self, nullifier: bytes, height: int) -> None:
        k = self._k_n(nullifier)
        if self._kv.get(k) is not None:
            return
        self._kv.put(k, _u64be(height))
        self._approx_size += 1

        # Soft-cap pressure: suggest eager prune if we grew too much
        # (callers can ignore the return value; we keep it internal)
        if self._approx_size > self.cfg.max_entries:
            # Opportunistic single-pass prune using this height as current
            self.prune(height)

    def prune(self, current_height: int) -> int:
        cutoff = current_height - self.cfg.window
        if cutoff <= 0:
            return 0
        pruned = 0
        prefix = self._ns + b"n/"
        for k, v in list(self._kv.iter_prefix(prefix)):
            try:
                h = _from_u64be(v)
            except Exception:
                # Defensive: corrupt value — delete it.
                self._kv.delete(k)
                pruned += 1
                continue
            if h < cutoff:
                self._kv.delete(k)
                pruned += 1
        if pruned:
            # Adjust approximate size (never negative)
            self._approx_size = max(0, self._approx_size - pruned)
        return pruned

    def size(self) -> int:
        # Best-effort: if caller wants the exact size, they can iterate the prefix.
        return max(0, self._approx_size)


__all__ = [
    "Config",
    "NullifierStore",
    "MemoryNullifierStore",
    "KV",
    "InMemoryKV",
    "KVNullifierStore",
]
