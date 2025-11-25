from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import (
    AsyncIterator,
    Callable,
    Deque,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    runtime_checkable,
)

# Shared knobs & types expected to be exported by p2p/sync/__init__.py
from . import DEFAULT_MAX_IN_FLIGHT, DEFAULT_REQUEST_TIMEOUT_SEC, Hash


# ======================================================================================
# Shares sync overview
# --------------------------------------------------------------------------------------
# This module handles gossip synchronization of *useful-work shares*:
#  • Peers announce share identifiers (INV(shares))
#  • We fetch missing share bodies (GETDATA/SHARES)
#  • We pass bodies to a local adapter for stateless/cheap validation & admission
#  • We rebroadcast locally-produced shares to peers with per-peer suppression
#
# It mirrors p2p/sync/mempool.py but is specialized for short-lived share objects used
# by miners, pools, and observers to estimate work and to form candidate blocks.
# ======================================================================================


# --------------------------------
# Protocols / adapter integration
# --------------------------------

@runtime_checkable
class ShareLike(Protocol):
    """Minimal shape a share object must have if materialized locally."""
    share_id: Hash   # canonical identifier (e.g., sha3-256 over envelope)
    raw: bytes       # canonical CBOR bytes (or wire form)


class ShareAdmissionResult:
    ADDED = "added"          # newly admitted share
    DUPLICATE = "duplicate"  # already known
    REJECTED = "rejected"    # structurally invalid, expired, wrong header, etc.


@runtime_checkable
class SharePoolAdapter(Protocol):
    """
    Bridge to the node's share pool. Implementations typically:
      • Fast-check: header binding, micro-target ratio, expiry window
      • Maintain a small rolling window keyed by share_id/nullifier
      • Optionally expose recent local shares for announces
    """
    async def has_share(self, share_id: Hash) -> bool: ...
    async def admit_share(self, raw: bytes) -> Tuple[str, Optional[Hash]]:
        """
        Attempt to add a share (raw encoded). Returns (status, share_id_opt).
        status ∈ {ShareAdmissionResult.ADDED, DUPLICATE, REJECTED}.
        """
        ...
    async def iter_local_announces(self) -> AsyncIterator[Hash]:
        """Yield share_ids of locally-originated shares to announce (may stream indefinitely)."""
        ...


@runtime_checkable
class ShareFetcher(Protocol):
    """
    Transport-agnostic fetcher. Implementations choose peers, issue GETDATA/SHARES,
    handle timeouts, and return bytes keyed by share_id for the subset it could fetch.
    """
    async def get_shares(self, share_ids: Sequence[Hash], timeout_sec: float) -> Dict[Hash, bytes]:
        ...


# ---------------------------
# Utilities (TTL structures)
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
            # drop oldest (O(n) scan acceptable for moderate caps)
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

    def mark(self, peer_id: str, ids: Iterable[Hash]) -> None:
        s = self._sent.get(peer_id)
        if s is None:
            s = TTLSet(self.ttl_sec, self.cap_per_peer)
            self._sent[peer_id] = s
        s.update(ids)

    def filter_unsent(self, peer_id: str, ids: Iterable[Hash]) -> List[Hash]:
        s = self._sent.get(peer_id)
        if s is None:
            return list(ids)
        return [h for h in ids if h not in s]


# ---------------------------
# Config & Stats
# ---------------------------

@dataclass(slots=True)
class ShareSyncConfig:
    request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC
    max_in_flight_batches: int = min(8, DEFAULT_MAX_IN_FLIGHT)
    fetch_batch_size: int = 128
    inv_batch_size: int = 1024
    max_retries: int = 2
    seen_ttl_sec: float = 2 * 60        # suppress re-fetching for 2 minutes (shares are short-lived)
    per_peer_suppress_sec: float = 20.0 # don't re-announce same share to same peer for 20s
    per_peer_cap: int = 4096
    rebroadcast_interval_sec: float = 2.0
    max_rebroadcast_batch: int = 1024


@dataclass(slots=True)
class ShareSyncStats:
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
# Core: ShareSync
# ---------------------------

class ShareSync:
    """
    Transport-agnostic share synchronization.

    Inbound path:
      handle_inv(peer_id, share_ids) → enqueue unknown → worker fetch → admit

    Outbound path:
      rebroadcast_task(send_inv_cb) periodically polls local adapter for new local
      shares to announce, with per-peer suppression to avoid storms.

    Usage:
      sync = ShareSync(pool_adapter, fetcher)
      sync.start_workers()
      await sync.handle_inv(pid, share_ids)
      asyncio.create_task(sync.rebroadcast_task(send_inv_cb))
      ...
      sync.stop(); await sync.wait_stopped()
    """

    def __init__(
        self,
        pool: SharePoolAdapter,
        fetcher: ShareFetcher,
        cfg: Optional[ShareSyncConfig] = None,
    ) -> None:
        self.pool = pool
        self.fetcher = fetcher
        self.cfg = cfg or ShareSyncConfig()
        self.stats = ShareSyncStats()

        # Work management
        self._fetch_queue: Deque[Hash] = deque()
        self._in_flight: Set[Hash] = set()

        # De-dup / suppression
        self._recently_seen = TTLSet(self.cfg.seen_ttl_sec, cap=128_000)

        # Outbound peers
        self._peers: Set[str] = set()
        self._sent_map = PerPeerRecentlySent(
            ttl_sec=self.cfg.per_peer_suppress_sec,
            cap_per_peer=self.cfg.per_peer_cap,
        )

        # Lifecycle
        self._stop = asyncio.Event()
        self._workers: List[asyncio.Task] = []

    # --------- Inbound (INV → fetch) ---------

    async def handle_inv(self, peer_id: str, share_ids: Sequence[Hash]) -> None:
        """
        Process incoming INV of share ids from a peer:
         • drop already-known/recently-seen
         • enqueue remaining for fetch
        """
        self.stats.inv_received += len(share_ids)

        to_consider: List[Hash] = []
        for sid in share_ids:
            if sid in self._recently_seen:
                continue
            if await self.pool.has_share(sid):
                self._recently_seen.add(sid)
                continue
            to_consider.append(sid)

        enq = 0
        for sid in to_consider:
            if sid in self._in_flight:
                continue
            self._fetch_queue.append(sid)
            self._recently_seen.add(sid)
            enq += 1

        if enq:
            self.stats.enqueued_fetch += enq
            self.stats.last_progress_at = time.time()

    # --------- Outbound (local → INV) ---------

    def register_peer(self, peer_id: str) -> None:
        self._peers.add(peer_id)

    def unregister_peer(self, peer_id: str) -> None:
        self._peers.discard(peer_id)

    async def rebroadcast_task(self, send_inv: Callable[[str, Sequence[Hash]], "asyncio.Future[None]"]) -> None:
        """
        Periodically polls the share pool for local announces and sends INV to peers.
        """
        try:
            while not self._stop.is_set():
                batch: List[Hash] = []
                async for sid in self._bounded_iter(self.pool.iter_local_announces(), self.cfg.max_rebroadcast_batch):
                    batch.append(sid)

                if batch and self._peers:
                    await self._broadcast_batch(batch, send_inv)

                await asyncio.wait_for(self._stop.wait(), timeout=self.cfg.rebroadcast_interval_sec)
        except asyncio.TimeoutError:
            # periodic wake-up
            return await self.rebroadcast_task(send_inv)

    async def _broadcast_batch(
        self,
        share_ids: Sequence[Hash],
        send_inv: Callable[[str, Sequence[Hash]], "asyncio.Future[None]"],
    ) -> None:
        sends: List[Tuple[str, List[Hash]]] = []
        for pid in list(self._peers):
            ids = self._sent_map.filter_unsent(pid, share_ids)
            if ids:
                sends.append((pid, ids))
        if not sends:
            return
        await asyncio.gather(*(send_inv(pid, ids) for pid, ids in sends))
        for pid, ids in sends:
            self._sent_map.mark(pid, ids)
            self.stats.rebroadcasts += len(ids)

    # --------- Fetch/Admit workers ---------

    def start_workers(self) -> None:
        if self._workers:
            return
        for _ in range(self.cfg.max_in_flight_batches):
            self._workers.append(asyncio.create_task(self._worker_loop()))

    async def _worker_loop(self) -> None:
        try:
            while not self._stop.is_set():
                batch: List[Hash] = []
                while self._fetch_queue and len(batch) < self.cfg.fetch_batch_size:
                    sid = self._fetch_queue.popleft()
                    if sid in self._in_flight:
                        continue
                    self._in_flight.add(sid)
                    batch.append(sid)

                if not batch:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=0.10)
                    except asyncio.TimeoutError:
                        pass
                    continue

                await self._fetch_and_admit(batch)
        except asyncio.CancelledError:
            raise
        finally:
            for sid in list(self._in_flight):
                self._in_flight.discard(sid)

    async def _fetch_and_admit(self, batch: Sequence[Hash]) -> None:
        timeout = self.cfg.request_timeout_sec
        remaining = list(batch)
        retries = 0

        while remaining and retries <= self.cfg.max_retries and not self._stop.is_set():
            try:
                got = await asyncio.wait_for(
                    self.fetcher.get_shares(remaining, timeout),
                    timeout=timeout + 0.05,
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

            # Admit fetched shares
            for sid, raw in got.items():
                try:
                    status, admitted_id = await self.pool.admit_share(raw)
                except Exception:
                    status, admitted_id = (ShareAdmissionResult.REJECTED, None)

                if status == ShareAdmissionResult.ADDED:
                    self.stats.admitted += 1
                elif status == ShareAdmissionResult.DUPLICATE:
                    self.stats.duplicates += 1
                else:
                    self.stats.rejected += 1

                self._recently_seen.add(sid)
                try:
                    remaining.remove(sid)
                except ValueError:
                    pass

            retries += 1

        for sid in batch:
            self._in_flight.discard(sid)

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
    async def _bounded_iter(ait: AsyncIterator[Hash], limit: int) -> AsyncIterator[Hash]:
        i = 0
        async for x in ait:
            yield x
            i += 1
            if i >= limit:
                break


# ---------------------------
# Minimal self-test (manual)
# ---------------------------

if __name__ == "__main__":
    class _StubPool:
        def __init__(self) -> None:
            self._set: Set[Hash] = set()
            self._local: Deque[Hash] = deque()

        async def has_share(self, sid: Hash) -> bool:
            return sid in self._set

        async def admit_share(self, raw: bytes) -> Tuple[str, Optional[Hash]]:
            sid = raw  # treat body==id for demo
            if not raw:
                return (ShareAdmissionResult.REJECTED, None)
            if sid in self._set:
                return (ShareAdmissionResult.DUPLICATE, sid)
            self._set.add(sid)
            if len(self._local) < 2048:
                self._local.append(sid)
            return (ShareAdmissionResult.ADDED, sid)

        async def iter_local_announces(self) -> AsyncIterator[Hash]:
            while self._local:
                yield self._local.popleft()

    class _StubFetcher:
        async def get_shares(self, ids: Sequence[Hash], timeout_sec: float) -> Dict[Hash, bytes]:
            await asyncio.sleep(0.01)
            # Echo back "raw" body == id for demo
            return {sid: sid for sid in ids}

    async def _demo():
        pool = _StubPool()
        fetcher = _StubFetcher()
        sync = ShareSync(pool, fetcher)
        sync.start_workers()
        sync.register_peer("miner-A")
        sync.register_peer("observer-1")

        inv_ids = [bytes([i]) for i in range(1, 50)]
        await sync.handle_inv("miner-A", inv_ids)

        async def send_inv(pid: str, ids: Sequence[Hash]) -> None:
            # Print only first few for brevity
            preview = [x.hex() for x in ids[:6]]
            print(f"[send_inv] -> {pid}: {preview}{'...' if len(ids) > 6 else ''}")

        reb_task = asyncio.create_task(sync.rebroadcast_task(send_inv))
        await asyncio.sleep(0.4)
        sync.stop()
        await sync.wait_stopped()
        reb_task.cancel()
        try:
            await reb_task
        except Exception:
            pass
        print("stats:", sync.stats)

    asyncio.run(_demo())
