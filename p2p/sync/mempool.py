from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import (AsyncIterator, Callable, Deque, Dict, Iterable, List,
                    MutableMapping, Optional, Protocol, Sequence, Set, Tuple,
                    runtime_checkable)

# Shared knobs & types expected to be exported by p2p/sync/__init__.py
from . import DEFAULT_MAX_IN_FLIGHT, DEFAULT_REQUEST_TIMEOUT_SEC, Hash

# ---------------------------
# Protocols / adapter surfaces
# ---------------------------


@runtime_checkable
class TxLike(Protocol):
    hash: bytes  # canonical transaction hash
    raw: bytes  # canonical CBOR bytes (or wire form) for admission


class TxAdmissionResult:
    ADDED = "added"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


@runtime_checkable
class MempoolAdapter(Protocol):
    """
    Bridge to the node's mempool. This layer performs stateless checks and
    (optionally) fast account/state reads for admission; it also exposes
    a small API for discovering freshly-added local transactions to announce.

    Implementations typically live in p2p/adapters or mempool/adapters.
    """

    async def has_tx(self, h: Hash) -> bool: ...
    async def admit_tx(self, raw: bytes) -> Tuple[str, Optional[Hash]]:
        """
        Attempt to add a transaction (raw encoded). Returns (status, hash_opt).
        status ∈ {TxAdmissionResult.ADDED, DUPLICATE, REJECTED}.
        If ADDED or DUPLICATE, hash may be returned for convenience.
        """
        ...

    async def iter_local_announces(self) -> AsyncIterator[Hash]:
        """
        Iterate over hashes of locally-originated txs that are candidates for announce.
        The iterator may yield indefinitely; callers should bound by batch size.
        """
        ...


@runtime_checkable
class TxFetcher(Protocol):
    """
    Transport-agnostic fetcher. Implementations choose peers, issue GETDATA/TX,
    handle timeouts, and return bytes keyed by hash for the subset it could fetch.
    """

    async def get_txs(
        self, hashes: Sequence[Hash], timeout_sec: float
    ) -> Dict[Hash, bytes]: ...


# ---------------------------
# Small utilities
# ---------------------------


@dataclass(slots=True)
class TTLSet:
    ttl_sec: float
    cap: int
    _items: Dict[Hash, float] = field(default_factory=dict)

    def __contains__(self, h: Hash) -> bool:
        self._gc()
        exp = self._items.get(h)
        return bool(exp and exp > time.time())

    def add(self, h: Hash) -> None:
        now = time.time()
        self._gc(now)
        if len(self._items) >= self.cap:
            # drop oldest (O(n) scan acceptable for small caps)
            old = min(self._items.items(), key=lambda kv: kv[1])[0]
            self._items.pop(old, None)
        self._items[h] = now + self.ttl_sec

    def update(self, hs: Iterable[Hash]) -> None:
        for h in hs:
            self.add(h)

    def _gc(self, now: Optional[float] = None) -> None:
        t = now or time.time()
        dead = [k for k, exp in self._items.items() if exp <= t]
        for k in dead:
            self._items.pop(k, None)


@dataclass(slots=True)
class PerPeerRecentlySent:
    ttl_sec: float
    cap_per_peer: int
    _sent: Dict[str, TTLSet] = field(default_factory=dict)

    def mark(self, peer_id: str, hashes: Iterable[Hash]) -> None:
        s = self._sent.get(peer_id)
        if s is None:
            s = TTLSet(self.ttl_sec, self.cap_per_peer)
            self._sent[peer_id] = s
        s.update(hashes)

    def filter_unsent(self, peer_id: str, hashes: Iterable[Hash]) -> List[Hash]:
        s = self._sent.get(peer_id)
        if s is None:
            return list(hashes)
        return [h for h in hashes if h not in s]


# ---------------------------
# Config & Stats
# ---------------------------


@dataclass(slots=True)
class MempoolSyncConfig:
    request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC
    max_in_flight_batches: int = min(8, DEFAULT_MAX_IN_FLIGHT)
    fetch_batch_size: int = 64
    inv_batch_size: int = 512
    max_retries: int = 2
    seen_ttl_sec: float = 5 * 60  # suppress refetching the same tx for 5 minutes
    per_peer_suppress_sec: float = (
        60.0  # do not re-announce same tx to same peer for 60s
    )
    per_peer_cap: int = 2048
    rebroadcast_interval_sec: float = 7.5
    max_rebroadcast_batch: int = 512


@dataclass(slots=True)
class MempoolSyncStats:
    started_at: float = field(default_factory=time.time)
    last_progress_at: float = field(default_factory=time.time)
    inv_received: int = 0
    enqueued_fetch: int = 0
    fetched: int = 0
    admitted: int = 0
    duplicates: int = 0
    rejected: int = 0
    timeouts: int = 0
    retries: int = 0
    fetch_errors: int = 0
    rebroadcasts: int = 0


# ---------------------------
# Core: MempoolSync
# ---------------------------


class MempoolSync:
    """
    Handles:
      • INV(tx) messages → schedule GETDATA and fetch full tx bodies.
      • Admission into local mempool with basic result accounting.
      • Rebroadcast of newly added *local* txs to peers with per-peer suppression.

    This module is transport-agnostic. Callers wire:
      • inbound:  await memsync.handle_inv(peer_id, hashes)
      • outbound: run memsync.rebroadcast_task(send_inv_cb)
    """

    def __init__(
        self,
        mempool: MempoolAdapter,
        fetcher: TxFetcher,
        cfg: Optional[MempoolSyncConfig] = None,
    ) -> None:
        self.mempool = mempool
        self.fetcher = fetcher
        self.cfg = cfg or MempoolSyncConfig()
        self.stats = MempoolSyncStats()

        # Work queues
        self._fetch_queue: Deque[Hash] = deque()
        self._in_flight: Set[Hash] = set()

        # De-duplication / suppression
        self._recently_seen = TTLSet(self.cfg.seen_ttl_sec, cap=64_000)

        # Peer management for re-announces
        self._peers: Set[str] = set()
        self._sent_map = PerPeerRecentlySent(
            ttl_sec=self.cfg.per_peer_suppress_sec,
            cap_per_peer=self.cfg.per_peer_cap,
        )

        # Lifecycle
        self._stop = asyncio.Event()
        self._workers: List[asyncio.Task] = []

    # --------- Inbound (INV → fetch) ---------

    async def handle_inv(self, peer_id: str, hashes: Sequence[Hash]) -> None:
        """
        Handle incoming INV of transaction hashes from a peer:
          • filter out known/recently seen
          • enqueue remaining for fetch
        """
        self.stats.inv_received += len(hashes)
        # Fast filter: already admitted or seen recently
        to_consider: List[Hash] = []
        for h in hashes:
            if h in self._recently_seen:
                continue
            # cheap local presence check
            if await self.mempool.has_tx(h):
                self._recently_seen.add(h)
                continue
            to_consider.append(h)

        # Enqueue uniques
        enq = 0
        for h in to_consider:
            if h in self._in_flight:
                continue
            self._fetch_queue.append(h)
            self._recently_seen.add(h)
            enq += 1
        if enq:
            self.stats.enqueued_fetch += enq
            self.stats.last_progress_at = time.time()

    # --------- Outbound (local → INV) ---------

    def register_peer(self, peer_id: str) -> None:
        self._peers.add(peer_id)

    def unregister_peer(self, peer_id: str) -> None:
        self._peers.discard(peer_id)

    async def rebroadcast_task(
        self, send_inv: Callable[[str, Sequence[Hash]], "asyncio.Future[None]"]
    ) -> None:
        """
        Periodically polls local mempool for announce candidates and sends INV
        to connected peers, with per-peer suppression to avoid storms.
        """
        try:
            while not self._stop.is_set():
                batch: List[Hash] = []
                async for h in self._bounded_iter(
                    self.mempool.iter_local_announces(), self.cfg.max_rebroadcast_batch
                ):
                    batch.append(h)
                if batch and self._peers:
                    await self._broadcast_batch(batch, send_inv)
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.cfg.rebroadcast_interval_sec
                )
        except asyncio.TimeoutError:
            # normal periodic wake-up
            return await self.rebroadcast_task(send_inv)

    async def _broadcast_batch(
        self,
        hashes: Sequence[Hash],
        send_inv: Callable[[str, Sequence[Hash]], "asyncio.Future[None]"],
    ) -> None:
        # For each peer, filter out hashes we've recently sent to that peer.
        sends: List[Tuple[str, List[Hash]]] = []
        for pid in list(self._peers):
            hs = self._sent_map.filter_unsent(pid, hashes)
            if hs:
                sends.append((pid, hs))

        # Fan out (bounded)
        if not sends:
            return
        await asyncio.gather(*(send_inv(pid, hs) for pid, hs in sends))
        for pid, hs in sends:
            self._sent_map.mark(pid, hs)
            self.stats.rebroadcasts += len(hs)

    # --------- Fetch/Admit workers ---------

    def start_workers(self) -> None:
        if self._workers:
            return
        for _ in range(self.cfg.max_in_flight_batches):
            self._workers.append(asyncio.create_task(self._worker_loop()))

    async def _worker_loop(self) -> None:
        try:
            while not self._stop.is_set():
                # Build a batch
                batch: List[Hash] = []
                while self._fetch_queue and len(batch) < self.cfg.fetch_batch_size:
                    h = self._fetch_queue.popleft()
                    if h in self._in_flight:
                        continue
                    self._in_flight.add(h)
                    batch.append(h)

                if not batch:
                    # back off lightly to avoid spin when idle
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=0.15)
                    except asyncio.TimeoutError:
                        pass
                    continue

                await self._fetch_and_admit(batch)
        except asyncio.CancelledError:
            raise
        finally:
            # drain markers
            for h in list(self._in_flight):
                self._in_flight.discard(h)

    async def _fetch_and_admit(self, batch: Sequence[Hash]) -> None:
        timeout = self.cfg.request_timeout_sec
        remaining = list(batch)
        retries = 0
        while remaining and retries <= self.cfg.max_retries and not self._stop.is_set():
            try:
                got = await asyncio.wait_for(
                    self.fetcher.get_txs(remaining, timeout), timeout=timeout + 0.05
                )
            except asyncio.TimeoutError:
                self.stats.timeouts += 1
                self.stats.retries += 1
                retries += 1
                continue
            except Exception:
                self.stats.fetch_errors += 1
                self.stats.retries += 1
                retries += 1
                continue

            self.stats.fetched += len(got)

            # Admit what we have
            for h, raw in got.items():
                try:
                    status, hh = await self.mempool.admit_tx(raw)
                except Exception:
                    status, hh = (TxAdmissionResult.REJECTED, None)
                if status == TxAdmissionResult.ADDED:
                    self.stats.admitted += 1
                elif status == TxAdmissionResult.DUPLICATE:
                    self.stats.duplicates += 1
                else:
                    self.stats.rejected += 1
                # mark seen to suppress re-fetch
                self._recently_seen.add(h)
                # remove from remaining if present
                try:
                    remaining.remove(h)
                except ValueError:
                    pass

            # Anything not returned is retried (up to max_retries)
            retries += 1

        # Release in-flight flags
        for h in batch:
            self._in_flight.discard(h)

    # --------- Lifecycle ---------

    def stop(self) -> None:
        self._stop.set()
        for t in self._workers:
            t.cancel()

    async def wait_stopped(self) -> None:
        if not self._workers:
            return
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    # --------- Helpers ---------

    @staticmethod
    async def _bounded_iter(
        ait: AsyncIterator[Hash], limit: int
    ) -> AsyncIterator[Hash]:
        i = 0
        async for x in ait:
            yield x
            i += 1
            if i >= limit:
                break


# ---------------------------
# Minimal test stub (optional manual run)
# ---------------------------

if __name__ == "__main__":
    # This block is a lightweight smoke-test with in-memory stubs.
    class _StubPool:
        def __init__(self) -> None:
            self._set: Set[Hash] = set()
            self._local: Deque[Hash] = deque()

        async def has_tx(self, h: Hash) -> bool:
            return h in self._set

        async def admit_tx(self, raw: bytes) -> Tuple[str, Optional[Hash]]:
            h = raw  # treat raw as hash for the stub
            if h in self._set:
                return (TxAdmissionResult.DUPLICATE, h)
            # very naive "validation"
            if not raw:
                return (TxAdmissionResult.REJECTED, None)
            self._set.add(h)
            # locally-originated announce queue (simulate a few)
            if len(self._local) < 1024:
                self._local.append(h)
            return (TxAdmissionResult.ADDED, h)

        async def iter_local_announces(self) -> AsyncIterator[Hash]:
            # drain up to N per call site
            while self._local:
                yield self._local.popleft()

    class _StubFetcher:
        async def get_txs(
            self, hashes: Sequence[Hash], timeout_sec: float
        ) -> Dict[Hash, bytes]:
            # Return "raw" == hash for demo
            await asyncio.sleep(0.01)
            return {h: h for h in hashes}

    async def _demo():
        pool = _StubPool()
        fetcher = _StubFetcher()
        sync = MempoolSync(pool, fetcher)
        sync.start_workers()
        sync.register_peer("peerA")
        sync.register_peer("peerB")

        # Make some fake INV
        invs = [bytes([i]) for i in range(1, 20)]
        await sync.handle_inv("peerA", invs)

        # Start rebroadcast loop (send_inv simply prints)
        async def send_inv(pid: str, hs: Sequence[Hash]) -> None:
            print(
                f"[send_inv] -> {pid}: {list(map(bytes.hex, hs))[:4]}{'...' if len(hs) > 4 else ''}"
            )

        reb_task = asyncio.create_task(sync.rebroadcast_task(send_inv))
        await asyncio.sleep(0.5)
        sync.stop()
        await sync.wait_stopped()
        reb_task.cancel()
        try:
            await reb_task
        except Exception:
            pass
        print("stats:", sync.stats)

    asyncio.run(_demo())
