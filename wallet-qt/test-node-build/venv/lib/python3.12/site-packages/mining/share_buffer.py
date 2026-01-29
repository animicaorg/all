from __future__ import annotations

"""
ShareBuffer: a fast, multi-producer → single-consumer buffer for HashShare results.

Why not "true lock-free"?
-------------------------
Python (CPython) doesn't provide CAS/atomics in stdlib, so we use `queue.Queue`
which is highly optimized in C and safe across threads. For devnet and typical
CPU miners, this is plenty fast. If you later add a native ring buffer in
`native/` (e.g., a lock-free MPSC ring), you can swap the backend behind the
same interface.

Key properties
--------------
- Multi-producer safe: many mining threads can call `try_push(...)` concurrently.
- Single-consumer oriented: a submitter thread drains with `pop_batch(...)`.
- Non-blocking by default: producers never stall your scanning loop.
- Backpressure policies:
    * "drop_new"     → reject when full (default).
    * "drop_oldest"  → evict one old item then enqueue the new one.
    * "block"        → producers block until space is available (not recommended
                       for inner hot loops).
- Lightweight stats for observability (enqueued, dequeued, dropped).

Usage
-----
    buf = ShareBuffer(capacity=8192, policy="drop_new")

    # In N mining threads:
    buf.try_push(found_share)

    # In one submitter thread:
    batch = buf.pop_batch(max_items=1024, timeout=0.05)
    for share in batch:
        submit(share)

Swapping backend later
----------------------
Create a subclass that implements the same public methods with a native ring.
"""

import queue
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Iterator, List, Optional

# Import type only to avoid runtime import cycles
if TYPE_CHECKING:
    from .hash_search import FoundShare


@dataclass
class BufferStats:
    enqueued: int = 0
    dequeued: int = 0
    dropped_full: int = 0
    dropped_oldest: int = 0

    def snapshot(self) -> "BufferStats":
        return BufferStats(
            enqueued=self.enqueued,
            dequeued=self.dequeued,
            dropped_full=self.dropped_full,
            dropped_oldest=self.dropped_oldest,
        )


class ShareBuffer:
    """
    MPSC share buffer with capacity and configurable backpressure policy.

    Public API
    ----------
    - try_push(share) -> bool
    - pop_one(timeout: float | None = 0.0) -> Optional[FoundShare]
    - pop_batch(max_items: int = 1024, timeout: float = 0.0) -> list[FoundShare]
    - drain_iter(stop_event: threading.Event | None, *, max_items_per_yield=1024, timeout=0.05)
    - qsize() -> int
    - capacity -> int
    - stats() -> BufferStats (snapshot)
    - close(); is_closed
    """

    __slots__ = ("_q", "_cap", "_policy", "_stats", "_closed", "_not_empty")

    def __init__(self, capacity: int = 8192, policy: str = "drop_new") -> None:
        """
        Args:
          capacity: maximum number of shares buffered at once.
          policy: "drop_new" | "drop_oldest" | "block"
        """
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if policy not in ("drop_new", "drop_oldest", "block"):
            raise ValueError("policy must be one of: drop_new, drop_oldest, block")

        # queue.Queue is thread-safe and implemented in C; non-blocking ops are fast.
        self._q: "queue.Queue[FoundShare]" = queue.Queue(maxsize=capacity)
        self._cap = capacity
        self._policy = policy
        self._stats = BufferStats()
        self._closed = False
        # Event to allow the consumer to wait efficiently for arrivals
        self._not_empty = threading.Event()

    # ──────────────────────────────────────────────────────────────────────
    # Producer side
    # ──────────────────────────────────────────────────────────────────────

    def try_push(self, share: "FoundShare") -> bool:
        """
        Non-blocking push by default.
        Returns True if accepted; False if dropped due to capacity (when policy=drop_new).

        For policy="drop_oldest", this method evicts one existing item and enqueues the new one.
        For policy="block", this will block until space is available.
        """
        if self._closed:
            return False

        # Fast path: attempt non-blocking put
        try:
            self._q.put_nowait(share)
            self._stats.enqueued += 1
            self._not_empty.set()
            return True
        except queue.Full:
            pass

        # Handle backpressure
        if self._policy == "drop_new":
            self._stats.dropped_full += 1
            return False

        if self._policy == "drop_oldest":
            # Evict exactly one old item (if any), then enqueue.
            try:
                _ = self._q.get_nowait()
                self._stats.dropped_oldest += 1
            except queue.Empty:
                # Rare race: became empty between Full and get_nowait; fall through.
                pass
            # Now a slot should be available; try to enqueue non-blocking again.
            try:
                self._q.put_nowait(share)
                self._stats.enqueued += 1
                self._not_empty.set()
                return True
            except queue.Full:
                # Extremely rare: a different producer raced to fill the slot.
                self._stats.dropped_full += 1
                return False

        # policy == "block"
        self._q.put(share)  # will block until space is available
        self._stats.enqueued += 1
        self._not_empty.set()
        return True

    # ──────────────────────────────────────────────────────────────────────
    # Consumer side
    # ──────────────────────────────────────────────────────────────────────

    def pop_one(self, timeout: Optional[float] = 0.0) -> Optional["FoundShare"]:
        """
        Pop a single share. If timeout == 0.0 or None, it's non-blocking.
        Returns None if empty (or closed and empty).
        """
        if timeout and timeout > 0:
            # Wait for event to reduce syscalls when empty
            self._not_empty.wait(timeout)
        try:
            item = self._q.get_nowait()
            self._stats.dequeued += 1
            if self._q.empty():
                self._not_empty.clear()
            return item
        except queue.Empty:
            return None

    def pop_batch(
        self, max_items: int = 1024, timeout: float = 0.0
    ) -> list["FoundShare"]:
        """
        Pop up to `max_items` shares. If `timeout>0` and the queue is empty,
        wait up to `timeout` seconds for at least one to arrive, then drain
        without further blocking. Returns an empty list if none available.
        """
        out: list["FoundShare"] = []

        if timeout > 0 and self._q.empty():
            self._not_empty.wait(timeout)

        # Non-blocking drain up to max_items
        get = self._q.get_nowait
        for _ in range(max_items):
            try:
                item = get()
                out.append(item)
            except queue.Empty:
                break

        n = len(out)
        if n:
            self._stats.dequeued += n
        if self._q.empty():
            self._not_empty.clear()
        return out

    def drain_iter(
        self,
        stop_event: Optional[threading.Event] = None,
        *,
        max_items_per_yield: int = 1024,
        timeout: float = 0.05,
    ) -> Iterator["FoundShare"]:
        """
        Convenience iterator for a consumer loop:

            for share in buf.drain_iter(stop_evt):
                submit(share)

        This batches internally to reduce scheduler overhead, but yields one-by-one.
        """
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            batch = self.pop_batch(max_items=max_items_per_yield, timeout=timeout)
            if not batch:
                # Check closed state: if closed and empty, exit
                if self._closed and self._q.empty():
                    break
                continue
            for item in batch:
                yield item

    # ──────────────────────────────────────────────────────────────────────
    # Introspection & lifecycle
    # ──────────────────────────────────────────────────────────────────────

    def qsize(self) -> int:
        return self._q.qsize()

    @property
    def capacity(self) -> int:
        return self._cap

    def stats(self) -> BufferStats:
        return self._stats.snapshot()

    def close(self) -> None:
        """Mark buffer as closed; consumer can exit once drained."""
        self._closed = True
        self._not_empty.set()  # wake any waiter

    @property
    def is_closed(self) -> bool:
        return self._closed


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test (manual)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    import random
    from dataclasses import asdict

    # Minimal stub for FoundShare if run standalone
    class _FoundShare:
        def __init__(self, n: int):
            self.nonce = n

        def __repr__(self) -> str:
            return f"FoundShare(nonce={self.nonce})"

    buf = ShareBuffer(capacity=8, policy="drop_oldest")

    stop_evt = threading.Event()

    def prod(idx: int) -> None:
        for k in range(1000):
            s = _FoundShare((idx << 20) | k)
            buf.try_push(s)
            if k % 17 == 0:
                time.sleep(0.0005)

    producers = [
        threading.Thread(target=prod, args=(i,), daemon=True) for i in range(4)
    ]
    for t in producers:
        t.start()

    consumed = 0
    start = time.perf_counter()
    for item in buf.drain_iter(stop_evt, max_items_per_yield=64, timeout=0.01):
        consumed += 1
        if consumed >= 2000:
            break
    end = time.perf_counter()

    buf.close()
    for t in producers:
        t.join(timeout=0.5)

    print("Consumed:", consumed, "in", f"{(end-start)*1000:.1f}ms")
    print("Stats:", asdict(buf.stats()))
