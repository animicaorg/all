"""
execution.runtime.event_sink — append logs, compute bloom/receipt hash.

This module provides a lightweight event sink used during transaction execution:
- Append LogEvent records (address, topics, data)
- Compute a 2048-bit logs bloom (Ethereum-style bit mapping with SHA3-256)
- Compute a deterministic logs root (Merkle over per-log hashes)
- Produce a stable receipt digest convenient for receipt building

Design goals
------------
* Pure-Python, no heavy deps. Uses hashlib.sha3_256 for hashing.
* Defensive imports: if `execution.receipts.logs_hash` exists, we delegate to it.
  Otherwise we use the local, documented fallback implementation below.
* Operates on raw 32-byte addresses and topic bytes. Higher layers can encode/ABI
  however they like; the sink is only concerned with deterministic hashing.

Notes
-----
This file computes *internal* digests (bloom, logs_root, receipt_digest).
The final on-wire encoding of receipts is owned by `execution/receipts/*`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

# Types
try:
    # Preferred: use the canonical LogEvent if available
    from ..types.events import LogEvent  # type: ignore
except Exception:  # pragma: no cover - fallback struct for isolated tests

    @dataclass
    class LogEvent:  # type: ignore
        address: bytes
        topics: Sequence[bytes]
        data: bytes


# Optional faster / canonical helpers
try:  # pragma: no cover - exercised indirectly if module exists
    from ..receipts.logs_hash import \
        logs_bloom_2048 as _canon_logs_bloom_2048  # type: ignore
    from ..receipts.logs_hash import logs_mroot as _canon_logs_mroot
    from ..receipts.logs_hash import receipt_digest as _canon_receipt_digest
except Exception:  # pragma: no cover
    _canon_logs_bloom_2048 = None
    _canon_logs_mroot = None
    _canon_receipt_digest = None


# --------------------------------------------------------------------------------------
# Constants & hashing helpers
# --------------------------------------------------------------------------------------

BLOOM_BITS = 2048
BLOOM_BYTES = BLOOM_BITS // 8  # 256
_HASH = hashlib.sha3_256  # project standard (distinct from Keccak-256)


def _h(data: bytes) -> bytes:
    return _HASH(data).digest()


def _u32(n: int) -> bytes:
    if n < 0:
        raise ValueError("negative integer not supported")
    return n.to_bytes(4, "big")


# --------------------------------------------------------------------------------------
# Fallback implementations (used when canonical helpers are absent)
# --------------------------------------------------------------------------------------


def _bloom_map(bitarray: bytearray, x: bytes) -> None:
    """
    Ethereum-style 2048-bit bloom mapping (using SHA3-256 here).
    For each element, take 3 11-bit indices from the hash and set those bits.
    """
    h = _h(x)
    for i in range(3):
        # 11-bit value from two bytes
        idx = ((h[2 * i] << 8) | h[2 * i + 1]) & (BLOOM_BITS - 1)  # mod 2048
        bitarray[idx >> 3] |= 1 << (idx & 7)


def _hash_log(log: LogEvent) -> bytes:
    """
    Deterministic per-log hash with simple domain separation.

    Hash over: "LOG\0" || address || u32(#topics) || sha3(topic[0]) ... || u32(len(data)) || sha3(data)
    """
    buf = bytearray(b"LOG\0")
    buf += bytes(log.address)
    buf += _u32(len(log.topics))
    for t in log.topics:
        buf += _h(bytes(t))
    buf += _u32(len(log.data))
    buf += _h(bytes(log.data))
    return _h(bytes(buf))


def _merkle_pair(l: bytes, r: bytes) -> bytes:
    return _h(b"MR\0" + l + r)


def _logs_mroot_fallback(logs: Sequence[LogEvent]) -> bytes:
    """
    Compute a canonical Merkle root over per-log hashes (duplicate last when odd).
    Empty set root is sha3_256("MR\\0EMPTY").
    """
    if not logs:
        return _h(b"MR\0EMPTY")
    level = [_hash_log(log) for log in logs]
    while len(level) > 1:
        nxt: List[bytes] = []
        it = iter(level)
        for a in it:
            b = next(it, None)
            if b is None:
                b = a  # duplicate last
            nxt.append(_merkle_pair(a, b))
        level = nxt
    return level[0]


def _logs_bloom_2048_fallback(logs: Sequence[LogEvent]) -> bytes:
    """
    2048-bit bloom: set bits for the log address and each topic.
    """
    bits = bytearray(BLOOM_BYTES)
    for log in logs:
        _bloom_map(bits, bytes(log.address))
        for t in log.topics:
            _bloom_map(bits, bytes(t))
    return bytes(bits)


def _receipt_digest_fallback(
    status: int, gas_used: int, logs_root: bytes, bloom: bytes
) -> bytes:
    """
    Stable internal receipt digest:
    sha3_256("RCPT\0" || u8(status) || u64(gas_used) || logs_root(32) || bloom(256))
    """
    if not (0 <= status <= 255):
        raise ValueError("status must fit into one byte (0..255)")
    buf = bytearray(b"RCPT\0")
    buf += status.to_bytes(1, "big")
    buf += gas_used.to_bytes(8, "big", signed=False)
    buf += logs_root
    buf += bloom
    return _h(bytes(buf))


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


class EventSink:
    """
    Collects LogEvent entries during execution and provides digest helpers.

    Typical use:
        sink = EventSink()
        sink.emit(addr, [topic1, topic2], data)
        bloom = sink.logs_bloom()
        root  = sink.logs_root()
        digest = sink.receipt_digest(status=1, gas_used=...,)

    Caches computed bloom/root until the next emit() invalidates them.
    """

    __slots__ = ["_logs", "_cached_bloom", "_cached_root"]

    def __init__(self) -> None:
        self._logs: List[LogEvent] = []
        self._cached_bloom: Optional[bytes] = None
        self._cached_root: Optional[bytes] = None

    # ------------------------ mutation ------------------------

    def emit(self, address: bytes, topics: Sequence[bytes], data: bytes) -> int:
        """
        Append a new log entry. Returns the index of the appended log.
        """
        self._logs.append(
            LogEvent(
                address=bytes(address),
                topics=[bytes(t) for t in topics],
                data=bytes(data),
            )
        )
        # Invalidate caches
        self._cached_bloom = None
        self._cached_root = None
        return len(self._logs) - 1

    def extend(self, entries: Iterable[LogEvent]) -> None:
        """
        Bulk-append preconstructed LogEvent entries.
        """
        for ev in entries:
            self._logs.append(
                LogEvent(
                    address=bytes(ev.address),
                    topics=[bytes(t) for t in ev.topics],
                    data=bytes(ev.data),
                )
            )
        self._cached_bloom = None
        self._cached_root = None

    def clear(self) -> None:
        self._logs.clear()
        self._cached_bloom = None
        self._cached_root = None

    # ------------------------ accessors ------------------------

    @property
    def logs(self) -> List[LogEvent]:
        return list(self._logs)

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._logs)

    # ------------------------ digests ------------------------

    def logs_bloom(self) -> bytes:
        """
        Return the 2048-bit logs bloom (256 bytes). Cached until next emit().
        """
        if self._cached_bloom is not None:
            return self._cached_bloom
        if _canon_logs_bloom_2048 is not None:
            bloom = _canon_logs_bloom_2048(self._logs)  # type: ignore[arg-type]
        else:
            bloom = _logs_bloom_2048_fallback(self._logs)
        self._cached_bloom = bloom
        return bloom

    def logs_root(self) -> bytes:
        """
        Return the Merkle root of logs (32 bytes). Cached until next emit().
        """
        if self._cached_root is not None:
            return self._cached_root
        if _canon_logs_mroot is not None:
            root = _canon_logs_mroot(self._logs)  # type: ignore[arg-type]
        else:
            root = _logs_mroot_fallback(self._logs)
        self._cached_root = root
        return root

    def receipt_digest(self, *, status: int, gas_used: int) -> bytes:
        """
        Compute a stable internal receipt digest over (status, gas_used, logs_root, bloom).
        """
        bloom = self.logs_bloom()
        root = self.logs_root()
        if _canon_receipt_digest is not None:
            return _canon_receipt_digest(status, gas_used, root, bloom)
        return _receipt_digest_fallback(status, gas_used, root, bloom)


__all__ = [
    "EventSink",
    "LogEvent",
    "BLOOM_BITS",
    "BLOOM_BYTES",
]
