"""
execution.state.events — pluggable event/log sinks.

This module defines a small, production-ready interface for recording and
querying transaction log events emitted during execution. It ships with three
backends:

- InMemoryEventSink: fast, test/dev friendly; keeps all logs in RAM.
- JsonlEventSink: append-only JSONL file; durable and simple to operate.
- NullEventSink: no-op sink for benchmarks or setups that ignore logs.

Design goals
------------
- Minimal, deterministic types (no implicit mutation of stored records).
- Stable ordering: (block_number, tx_index, log_index) strictly increases for
  appended records. Ordering is provided by the caller via context.
- Simple, Ethereum-style topic filtering (per-position, with OR sets).
- Zero external deps; pure stdlib.

The execution layer is expected to compute per-transaction log indices in
emission order and to pass them through `append(...)`.

If you need Merkle/bloom computation for receipts, see execution/receipts/.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import (Iterable, List, Optional, Protocol, Sequence, Tuple, Union,
                    runtime_checkable)

from ..types.events import \
    LogEvent  # (address: bytes, topics: Sequence[bytes], data: bytes)

# =============================================================================
# Utilities
# =============================================================================


def _b2h(b: bytes) -> str:
    return "0x" + b.hex()


def _h2b(h: str) -> bytes:
    if not isinstance(h, str):
        raise TypeError("expected hex string")
    if h.startswith("0x") or h.startswith("0X"):
        h = h[2:]
    return bytes.fromhex(h)


# =============================================================================
# Public data model
# =============================================================================


@dataclass(frozen=True)
class EventRecord:
    """
    A fully-qualified event record with execution context.

    Fields
    ------
    block_number : int
        Height of the block where the event was emitted.
    tx_index : int
        0-based index of the transaction inside the block.
    log_index : int
        0-based index of the event inside the transaction, in emission order.
    tx_hash : bytes
        Hash of the transaction (domain-separated upstream).
    event : LogEvent
        The event payload (address, topics, data).
    """

    block_number: int
    tx_index: int
    log_index: int
    tx_hash: bytes
    event: LogEvent

    # Convenient projections
    @property
    def address(self) -> bytes:
        return self.event.address

    @property
    def topics(self) -> Sequence[bytes]:
        return self.event.topics

    @property
    def data(self) -> bytes:
        return self.event.data


TopicSelector = Optional[Union[bytes, Sequence[bytes]]]
# Per-position topic filter. None = wildcard; bytes = exact; sequence = OR-of-options.


# =============================================================================
# Sink interface
# =============================================================================


@runtime_checkable
class EventSink(Protocol):
    def append(
        self,
        event: LogEvent,
        *,
        block_number: int,
        tx_hash: bytes,
        tx_index: int,
        log_index: int,
    ) -> EventRecord:
        """Append a single event with its execution context. Returns stored record."""

    def get_logs(
        self,
        *,
        address: Optional[bytes] = None,
        topics: Optional[Sequence[TopicSelector]] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Iterable[EventRecord]:
        """Iterate matching logs in ascending (block, tx, log) order."""

    def flush(self) -> None:
        """Force persistence, if applicable."""

    def close(self) -> None:
        """Release resources (files, buffers)."""


# =============================================================================
# Common filter logic
# =============================================================================


def _topic_pos_matches(value: bytes, selector: TopicSelector) -> bool:
    if selector is None:
        return True
    if isinstance(selector, (bytes, bytearray)):
        return value == bytes(selector)
    # sequence of candidates: match any
    for cand in selector:  # type: ignore[union-attr]
        if isinstance(cand, (bytes, bytearray)) and value == bytes(cand):
            return True
    return False


def _topics_match(
    event_topics: Sequence[bytes], selectors: Sequence[TopicSelector]
) -> bool:
    # Each selector position constrains the same index in event_topics.
    if len(selectors) > len(event_topics):
        return False
    for i, sel in enumerate(selectors):
        if not _topic_pos_matches(event_topics[i], sel):
            return False
    return True


def _record_matches(
    rec: EventRecord,
    address: Optional[bytes],
    topics: Optional[Sequence[TopicSelector]],
    from_block: Optional[int],
    to_block: Optional[int],
) -> bool:
    if from_block is not None and rec.block_number < from_block:
        return False
    if to_block is not None and rec.block_number > to_block:
        return False
    if address is not None and rec.address != address:
        return False
    if topics is not None and not _topics_match(rec.topics, topics):
        return False
    return True


# =============================================================================
# In-memory sink
# =============================================================================


class InMemoryEventSink(EventSink):
    """
    A simple, thread-safe in-memory sink.

    Notes
    -----
    - Suitable for unit tests and devnets.
    - Keeps all logs in RAM — do not use unbounded in long-running mainnets.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: List[EventRecord] = []

    def append(
        self,
        event: LogEvent,
        *,
        block_number: int,
        tx_hash: bytes,
        tx_index: int,
        log_index: int,
    ) -> EventRecord:
        rec = EventRecord(
            block_number=block_number,
            tx_index=tx_index,
            log_index=log_index,
            tx_hash=tx_hash,
            event=event,
        )
        with self._lock:
            self._records.append(rec)
        return rec

    def get_logs(
        self,
        *,
        address: Optional[bytes] = None,
        topics: Optional[Sequence[TopicSelector]] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Iterable[EventRecord]:
        with self._lock:
            it = (
                rec
                for rec in self._records
                if _record_matches(rec, address, topics, from_block, to_block)
            )
            if limit is None:
                yield from it
            else:
                n = 0
                for rec in it:
                    yield rec
                    n += 1
                    if n >= limit:
                        break

    def flush(self) -> None:
        # Nothing to do
        return

    def close(self) -> None:
        with self._lock:
            self._records.clear()


# =============================================================================
# JSONL sink (durable)
# =============================================================================


class JsonlEventSink(EventSink):
    """
    Append-only JSONL sink. Each line is a single EventRecord in canonical form.

    Durability
    ----------
    - Opened with buffering; `flush()` fsyncs the file descriptor.
    - Safe for concurrent appends from multiple threads within the process.
      (One sink instance should be shared; it serializes a single file handle.)

    Format (one object per line)
    ----------------------------
    {
      "block": 123,
      "tx_index": 0,
      "log_index": 2,
      "tx_hash": "0x…",
      "address": "0x…",
      "topics": ["0x…", "0x…"],
      "data": "0x…"
    }
    """

    def __init__(self, path: str) -> None:
        self._path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # Open in text mode with UTF-8; ensure append and no truncation.
        self._fh = open(path, "a+", encoding="utf-8", buffering=1)  # line-buffered
        self._lock = threading.RLock()
        self._log = logging.getLogger(__name__)

    # -- persistence helpers --------------------------------------------------

    def _encode(self, rec: EventRecord) -> str:
        obj = {
            "block": rec.block_number,
            "tx_index": rec.tx_index,
            "log_index": rec.log_index,
            "tx_hash": _b2h(rec.tx_hash),
            "address": _b2h(rec.address),
            "topics": [_b2h(t) for t in rec.topics],
            "data": _b2h(rec.data),
        }
        return json.dumps(obj, separators=(",", ":"), sort_keys=False)

    @staticmethod
    def _decode(line: str) -> EventRecord:
        obj = json.loads(line)
        event = LogEvent(
            address=_h2b(obj["address"]),
            topics=[_h2b(t) for t in obj.get("topics", [])],
            data=_h2b(obj["data"]),
        )
        return EventRecord(
            block_number=int(obj["block"]),
            tx_index=int(obj["tx_index"]),
            log_index=int(obj["log_index"]),
            tx_hash=_h2b(obj["tx_hash"]),
            event=event,
        )

    # -- EventSink interface --------------------------------------------------

    def append(
        self,
        event: LogEvent,
        *,
        block_number: int,
        tx_hash: bytes,
        tx_index: int,
        log_index: int,
    ) -> EventRecord:
        rec = EventRecord(
            block_number=block_number,
            tx_index=tx_index,
            log_index=log_index,
            tx_hash=tx_hash,
            event=event,
        )
        line = self._encode(rec)
        with self._lock:
            self._fh.write(line + "\n")
        return rec

    def get_logs(
        self,
        *,
        address: Optional[bytes] = None,
        topics: Optional[Sequence[TopicSelector]] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Iterable[EventRecord]:
        # Scan file sequentially; callers should consider indexing if high QPS is needed.
        with self._lock:
            self._fh.flush()
            self._fh.seek(0)
            count = 0
            for line in self._fh:
                if not line.strip():
                    continue
                try:
                    rec = self._decode(line)
                except Exception as e:  # pragma: no cover - defensive
                    self._log.warning(
                        "Skipping malformed event line: %s (%r)", line[:120], e
                    )
                    continue
                if _record_matches(rec, address, topics, from_block, to_block):
                    yield rec
                    count += 1
                    if limit is not None and count >= limit:
                        break

    def flush(self) -> None:
        with self._lock:
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.flush()
            finally:
                try:
                    self._fh.close()
                except Exception:
                    pass


# =============================================================================
# Null sink
# =============================================================================


class NullEventSink(EventSink):
    """A sink that drops everything."""

    def append(
        self,
        event: LogEvent,
        *,
        block_number: int,
        tx_hash: bytes,
        tx_index: int,
        log_index: int,
    ) -> EventRecord:
        # Return a record to keep call sites simple, even though it's not stored.
        return EventRecord(
            block_number=block_number,
            tx_index=tx_index,
            log_index=log_index,
            tx_hash=tx_hash,
            event=event,
        )

    def get_logs(
        self,
        *,
        address: Optional[bytes] = None,
        topics: Optional[Sequence[TopicSelector]] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Iterable[EventRecord]:
        if False:  # pragma: no cover - generator placeholder
            yield  # type: ignore[misc]
        return

    def flush(self) -> None:
        return

    def close(self) -> None:
        return


__all__ = [
    "EventRecord",
    "TopicSelector",
    "EventSink",
    "InMemoryEventSink",
    "JsonlEventSink",
    "NullEventSink",
]
