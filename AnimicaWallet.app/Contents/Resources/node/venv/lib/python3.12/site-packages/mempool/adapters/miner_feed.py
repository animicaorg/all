"""
MinerFeed — push-ready iterator bridging mempool → miner/header_packer
=====================================================================

Purpose
-------
Provide miners with a *low-latency* stream of ready-to-include transactions
bounded by gas/byte budgets. This adapter sits on top of the mempool's
block-builder/drain interface and exposes both synchronous and asyncio-friendly
APIs. It wakes the miner immediately when something relevant changes
(new/updated txs, fee watermark shift, reorg/head change), otherwise it falls
back to short polling.

Design
------
- Decoupled: the feed accepts a simple callable `drain(max_gas, max_bytes)`
  returning a Sequence[core.types.tx.Tx]. This keeps coupling minimal and
  allows tests to inject fakes.
- Push + pull: integrates with an optional notifier (e.g. mempool.notify)
  via a lightweight callback interface to wake waiters.
- Duplicate-safe: each batch is freshly drained atomically by the mempool;
  the feed itself does not hold items, only signals availability.
- Thread/async friendly: synchronous `next_batch()` and `iter_batches()`;
  asynchronous `anext_batch()` and `aiter_batches()`.

Typical usage
-------------
    from mempool.adapters.miner_feed import MinerFeed
    from mempool.drain import drain_ready  # your mempool's builder API

    feed = MinerFeed(drain=drain_ready, notifier=mempool_notifier)
    batch = feed.next_batch(max_gas=15_000_000, max_bytes=1_000_000, wait_s=0.25)
    header_packer.add_txs(batch.txs)

Notifier expectations
---------------------
If provided, `notifier` should implement:

    notifier.subscribe(callback: Callable[[str, dict | None], None]) -> None

Where `callback(event_name, payload)` is invoked for events like:
  - "pendingTx", "replacedTx", "droppedTx"
  - "headChanged", "feeWatermarkChanged"

The feed treats any event as a wake signal; it does not interpret payloads.

"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol, Sequence, Tuple

log = logging.getLogger(__name__)

# --- Lightweight protocols ------------------------------------------------------------


class DrainFn(Protocol):
    def __call__(self, max_gas: int, max_bytes: int) -> Sequence["Tx"]: ...


class Notifier(Protocol):
    def subscribe(self, callback: Callable[[str, Optional[dict]], None]) -> None: ...


# --- Types ----------------------------------------------------------------------------

try:
    # Preferred: import canonical Tx type for stronger typing
    from core.types.tx import Tx  # type: ignore
except Exception:  # pragma: no cover
    # Fallback typing (keeps this module importable in isolation)
    class Tx:  # type: ignore
        pass


@dataclass(frozen=True)
class MinerTxBatch:
    """A ready batch of transactions and accounting info."""

    txs: Sequence[Tx]
    total_gas: int
    total_bytes: int

    @property
    def count(self) -> int:
        return len(self.txs)


# --- Implementation ------------------------------------------------------------------


class MinerFeed:
    """
    Push-ready miner feed powered by a mempool drain function and an optional notifier.
    """

    def __init__(
        self,
        *,
        drain: DrainFn,
        notifier: Optional[Notifier] = None,
        min_wakeup_interval_s: float = 0.02,
    ) -> None:
        self._drain = drain
        self._cv = threading.Condition()
        self._closed = False
        self._last_wake = 0.0
        self._min_wake = float(min_wakeup_interval_s)

        if notifier is not None:
            notifier.subscribe(self._on_event)
            log.debug("MinerFeed: subscribed to notifier")

    # --- Public sync API --------------------------------------------------------------

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    def next_batch(
        self,
        max_gas: int,
        max_bytes: int,
        *,
        wait_s: float = 0.25,
    ) -> MinerTxBatch:
        """
        Try to drain a ready batch. If none is available, wait up to wait_s
        for a push notification (or timeout) and try once more.

        Returns an (possibly empty) MinerTxBatch. The miner may call this in a loop.
        """
        # First attempt (non-blocking)
        batch = self._drain_once(max_gas, max_bytes)
        if batch.count or wait_s <= 0:
            return batch

        # Wait for a signal or timeout, then try again
        deadline = time.monotonic() + wait_s
        with self._cv:
            while not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cv.wait(timeout=remaining)
                break  # wake once
        return self._drain_once(max_gas, max_bytes)

    def iter_batches(
        self,
        max_gas: int,
        max_bytes: int,
        *,
        idle_sleep_s: float = 0.10,
    ):
        """
        Synchronous generator that yields batches indefinitely until `close()` is called.
        Yields empty batches occasionally (keep-alive) if nothing is ready.
        """
        try:
            while not self._closed:
                batch = self.next_batch(max_gas, max_bytes, wait_s=idle_sleep_s)
                yield batch
        finally:
            # no external resources to release, but keep symmetry with async variant
            pass

    # --- Async helpers ----------------------------------------------------------------
    # Offered as convenience; avoids forcing asyncio on callers.

    async def anext_batch(
        self,
        max_gas: int,
        max_bytes: int,
        *,
        wait_s: float = 0.25,
    ) -> MinerTxBatch:
        import asyncio

        # First attempt
        batch = self._drain_once(max_gas, max_bytes)
        if batch.count or wait_s <= 0:
            return batch
        # Sleep (instead of Condition across threads) then re-try
        try:
            await asyncio.sleep(wait_s)
        except asyncio.CancelledError:  # pragma: no cover
            return batch
        return self._drain_once(max_gas, max_bytes)

    async def aiter_batches(
        self,
        max_gas: int,
        max_bytes: int,
        *,
        idle_sleep_s: float = 0.10,
    ):
        import asyncio

        while not self._closed:
            yield await self.anext_batch(max_gas, max_bytes, wait_s=idle_sleep_s)

    # --- Internal ---------------------------------------------------------------------

    def _drain_once(self, max_gas: int, max_bytes: int) -> MinerTxBatch:
        try:
            txs = self._drain(int(max_gas), int(max_bytes)) or ()
        except Exception as e:  # pragma: no cover
            # Be resilient: return empty batch, miner can log and retry
            log.warning("MinerFeed drain failed: %r", e)
            return MinerTxBatch(txs=(), total_gas=0, total_bytes=0)

        total_gas = 0
        total_bytes = 0
        # We deliberately avoid importing mempool internals; compute sizes conservatively.
        for tx in txs:
            gas = getattr(tx, "gas_limit", None) or getattr(tx, "gas", None) or 0
            total_gas += int(gas)
            raw = getattr(tx, "raw_cbor", None)
            if raw is None:
                # Try canonical encoder if available
                try:
                    from core.encoding.cbor import \
                        dumps as cbor_dumps  # type: ignore

                    raw = cbor_dumps(tx)  # type: ignore[arg-type]
                except Exception:
                    raw = b""
            total_bytes += len(raw)

        return MinerTxBatch(
            txs=tuple(txs), total_gas=total_gas, total_bytes=total_bytes
        )

    # Notifier callback: wake waiters (with throttling to avoid thundering herd)
    def _on_event(self, event_name: str, payload: Optional[dict]) -> None:
        now = time.monotonic()
        if (now - self._last_wake) < self._min_wake:
            return
        self._last_wake = now
        with self._cv:
            self._cv.notify_all()


__all__ = [
    "MinerFeed",
    "MinerTxBatch",
    "DrainFn",
    "Notifier",
]
