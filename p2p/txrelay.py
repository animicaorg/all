from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, Iterable, List, Optional, Set

from core.utils.hash import sha3_256

log = logging.getLogger("animica.p2p.txrelay")


@dataclass(slots=True)
class TxIdSetLRU:
    cap: int
    _items: "OrderedDict[bytes, None]" = field(default_factory=OrderedDict)

    def add(self, txid: bytes) -> None:
        if txid in self._items:
            self._items.move_to_end(txid)
            return
        self._items[txid] = None
        self._items.move_to_end(txid)
        if len(self._items) > self.cap:
            self._items.popitem(last=False)

    def __contains__(self, txid: bytes) -> bool:
        return txid in self._items

    def __len__(self) -> int:
        return len(self._items)


@dataclass(slots=True)
class PeerTxState:
    peer_id: str
    known_txids: TxIdSetLRU
    inv_queue: Deque[bytes] = field(default_factory=deque)
    last_sync_sent_at: float = 0.0
    last_sync_recv_at: float = 0.0


@dataclass(slots=True)
class InflightEntry:
    peer_id: str
    deadline: float
    attempts: int = 1


class TokenBucket:
    def __init__(self, rate: float, burst: float) -> None:
        self.rate = float(rate)
        self.burst = float(burst)
        self._tokens: Dict[str, float] = {}
        self._last: Dict[str, float] = {}

    def _refill(self, key: str, now: float) -> None:
        last = self._last.get(key)
        if last is None:
            self._tokens[key] = self.burst
            self._last[key] = now
            return
        if now <= last:
            return
        tokens = self._tokens.get(key, self.burst)
        tokens = min(self.burst, tokens + self.rate * (now - last))
        self._tokens[key] = tokens
        self._last[key] = now

    def consume(self, key: str, cost: float) -> bool:
        now = time.monotonic()
        self._refill(key, now)
        tokens = self._tokens.get(key, self.burst)
        if tokens >= cost:
            self._tokens[key] = tokens - cost
            return True
        return False


PeerListFn = Callable[[], Iterable[str]]
PeerEligibleFn = Callable[[str], bool]
SendFn = Callable[[str, Any], Awaitable[None]]
HasTxFn = Callable[[bytes], Awaitable[bool]]
GetTxFn = Callable[[bytes], Awaitable[Optional[bytes]]]
AdmitTxFn = Callable[[bytes, Optional[str]], Awaitable[tuple[bool, Optional[str]]]]
ListHashesFn = Callable[[int], Awaitable[List[bytes]]]
HasChainTxFn = Callable[[bytes], Awaitable[bool]]


class TxRelayService:
    def __init__(
        self,
        *,
        max_tx_bytes: int,
        inv_batch_size: int = 200,
        inv_flush_interval_s: float = 0.2,
        inflight_timeout_s: float = 10.0,
        inflight_max_retries: int = 2,
        mempool_sync_interval_s: float = 15.0,
        mempool_sync_limit: int = 2000,
        known_txids_cap: int = 50_000,
        inv_rate_per_sec: float = 2000.0,
        inv_burst: float = 4000.0,
        tx_data_rate_bytes_per_sec: float = 5_000_000.0,
        tx_data_burst_bytes: float = 10_000_000.0,
        peer_ids: PeerListFn,
        peer_eligible: PeerEligibleFn,
        send_tx_inv: SendFn,
        send_tx_get: SendFn,
        send_tx_data: SendFn,
        send_tx_notfound: SendFn,
        send_mempool_req: SendFn,
        send_mempool_resp: SendFn,
        has_tx: HasTxFn,
        has_chain_tx: HasChainTxFn,
        get_tx_raw: GetTxFn,
        admit_tx: AdmitTxFn,
        list_mempool_hashes: ListHashesFn,
    ) -> None:
        self.max_tx_bytes = int(max_tx_bytes)
        self.inv_batch_size = int(inv_batch_size)
        self.inv_flush_interval_s = float(inv_flush_interval_s)
        self.inflight_timeout_s = float(inflight_timeout_s)
        self.inflight_max_retries = int(inflight_max_retries)
        self.mempool_sync_interval_s = float(mempool_sync_interval_s)
        self.mempool_sync_limit = int(mempool_sync_limit)
        self.known_txids_cap = int(known_txids_cap)

        self._peer_ids = peer_ids
        self._peer_eligible = peer_eligible
        self._send_tx_inv = send_tx_inv
        self._send_tx_get = send_tx_get
        self._send_tx_data = send_tx_data
        self._send_tx_notfound = send_tx_notfound
        self._send_mempool_req = send_mempool_req
        self._send_mempool_resp = send_mempool_resp
        self._has_tx = has_tx
        self._has_chain_tx = has_chain_tx
        self._get_tx_raw = get_tx_raw
        self._admit_tx = admit_tx
        self._list_mempool_hashes = list_mempool_hashes

        self._peer_state: Dict[str, PeerTxState] = {}
        self._inflight: Dict[bytes, InflightEntry] = {}
        self._tx_sources: Dict[bytes, Set[str]] = {}
        self._inv_limiter = TokenBucket(inv_rate_per_sec, inv_burst)
        self._tx_data_limiter = TokenBucket(
            tx_data_rate_bytes_per_sec, tx_data_burst_bytes
        )
        self._running = False
        self._lock = asyncio.Lock()

    def register_peer(self, peer_id: str) -> None:
        if peer_id not in self._peer_state:
            self._peer_state[peer_id] = PeerTxState(
                peer_id=peer_id,
                known_txids=TxIdSetLRU(self.known_txids_cap),
            )

    def unregister_peer(self, peer_id: str) -> None:
        self._peer_state.pop(peer_id, None)
        for txid, entry in list(self._inflight.items()):
            if entry.peer_id == peer_id:
                self._inflight.pop(txid, None)

    def _eligible_peers(self) -> List[str]:
        return [p for p in self._peer_ids() if self._peer_eligible(p)]

    def _ensure_peer(self, peer_id: str) -> PeerTxState:
        state = self._peer_state.get(peer_id)
        if state is None:
            state = PeerTxState(peer_id=peer_id, known_txids=TxIdSetLRU(self.known_txids_cap))
            self._peer_state[peer_id] = state
        return state

    def _mark_known(self, peer_id: str, txid: bytes) -> None:
        self._ensure_peer(peer_id).known_txids.add(txid)

    async def on_mempool_add(self, txid: bytes, raw: bytes) -> None:
        async with self._lock:
            peers = self._eligible_peers()
            for peer_id in peers:
                state = self._ensure_peer(peer_id)
                if txid in state.known_txids:
                    continue
                state.inv_queue.append(txid)
        log.info("TX_ACCEPT_LOCAL", extra={"hash": txid.hex(), "bytes": len(raw)})

    async def on_tx_inv(self, peer_id: str, txids: Iterable[bytes]) -> None:
        tx_list = list(txids)
        log.info(
            "TX_INV_RECV",
            extra={"peer": peer_id, "count": len(tx_list)},
        )
        needs_check: List[bytes] = []
        async with self._lock:
            state = self._ensure_peer(peer_id)
            for txid in tx_list:
                state.known_txids.add(txid)
                self._tx_sources.setdefault(txid, set()).add(peer_id)
                if txid in self._inflight:
                    continue
                needs_check.append(txid)
        missing: List[bytes] = []
        now = time.time()
        for txid in needs_check:
            if await self._has_tx(txid):
                continue
            if await self._has_chain_tx(txid):
                continue
            async with self._lock:
                if txid in self._inflight:
                    continue
                self._inflight[txid] = InflightEntry(
                    peer_id=peer_id, deadline=now + self.inflight_timeout_s
                )
            missing.append(txid)
        if missing:
            log.info(
                "TX_INV_RECV",
                extra={"peer": peer_id, "count": len(missing)},
            )
            await self._send_tx_get(peer_id, missing)
            log.info(
                "TX_GET_SEND",
                extra={"peer": peer_id, "count": len(missing)},
            )

    async def on_tx_get(self, peer_id: str, txids: Iterable[bytes]) -> None:
        tx_list = list(txids)
        log.info(
            "TX_GET_RECV",
            extra={"peer": peer_id, "count": len(tx_list)},
        )
        send_items: List[dict[str, Any]] = []
        notfound: List[bytes] = []
        for txid in tx_list:
            raw = await self._get_tx_raw(txid)
            if raw is None:
                notfound.append(txid)
                continue
            if len(raw) > self.max_tx_bytes:
                notfound.append(txid)
                continue
            send_items.append({"txid": txid, "tx_bytes": raw})

        if send_items:
            total_bytes = sum(len(it["tx_bytes"]) for it in send_items)
            if not self._tx_data_limiter.consume(peer_id, total_bytes):
                log.info(
                    "TX_DATA_SEND",
                    extra={"peer": peer_id, "status": "rate_limited", "bytes": total_bytes},
                )
            else:
                await self._send_tx_data(peer_id, send_items)
                log.info(
                    "TX_DATA_SEND",
                    extra={"peer": peer_id, "count": len(send_items), "bytes": total_bytes},
                )

        if notfound:
            await self._send_tx_notfound(peer_id, notfound)
            log.info(
                "TX_NOTFOUND",
                extra={"peer": peer_id, "count": len(notfound)},
            )

    async def on_tx_data(self, peer_id: str, items: Iterable[dict[str, Any]]) -> None:
        broadcast: List[bytes] = []
        for item in items:
            txid = item.get("txid")
            raw = item.get("tx_bytes")
            if not isinstance(txid, (bytes, bytearray)) or not isinstance(
                raw, (bytes, bytearray)
            ):
                continue
            txid_bytes = bytes(txid)
            raw_bytes = bytes(raw)
            log.info(
                "TX_DATA_RECV",
                extra={"peer": peer_id, "hash": txid_bytes.hex(), "bytes": len(raw_bytes)},
            )
            if len(raw_bytes) > self.max_tx_bytes:
                log.info(
                    "TX_REJECT_MEMPOOL",
                    extra={"hash": txid_bytes.hex(), "reason": "oversize"},
                )
                self._inflight.pop(txid_bytes, None)
                continue
            computed = sha3_256(raw_bytes)
            if computed != txid_bytes:
                log.info(
                    "TX_REJECT_MEMPOOL",
                    extra={"hash": txid_bytes.hex(), "reason": "hash_mismatch"},
                )
                self._inflight.pop(txid_bytes, None)
                continue
            ok, reason = await self._admit_tx(raw_bytes, peer_id)
            self._inflight.pop(txid_bytes, None)
            self._mark_known(peer_id, txid_bytes)
            if ok:
                broadcast.append(txid_bytes)
                log.info(
                    "TX_ADD_MEMPOOL",
                    extra={"hash": txid_bytes.hex(), "origin": f"peer:{peer_id}"},
                )
            else:
                log.info(
                    "TX_REJECT_MEMPOOL",
                    extra={
                        "hash": txid_bytes.hex(),
                        "reason": reason or "reject",
                        "origin": f"peer:{peer_id}",
                    },
                )

        if broadcast:
            await self._broadcast_inv(broadcast, exclude_peer=peer_id)

    async def on_tx_notfound(self, peer_id: str, txids: Iterable[bytes]) -> None:
        tx_list = list(txids)
        for txid in tx_list:
            self._inflight.pop(txid, None)
        log.info(
            "TX_NOTFOUND",
            extra={"peer": peer_id, "count": len(tx_list)},
        )

    async def on_mempool_req(self, peer_id: str, limit: Optional[int] = None) -> None:
        lim = int(limit) if limit is not None else self.mempool_sync_limit
        txids = await self._list_mempool_hashes(lim)
        await self._send_mempool_resp(peer_id, txids)
        log.info(
            "TX_SYNC_RESP",
            extra={"peer": peer_id, "count": len(txids)},
        )

    async def on_mempool_resp(self, peer_id: str, txids: Iterable[bytes]) -> None:
        needs_check: List[bytes] = []
        async with self._lock:
            state = self._ensure_peer(peer_id)
            for txid in txids:
                state.known_txids.add(txid)
                if txid in self._inflight:
                    continue
                needs_check.append(txid)
            state.last_sync_recv_at = time.time()
        missing: List[bytes] = []
        for txid in needs_check:
            if await self._has_tx(txid):
                continue
            if await self._has_chain_tx(txid):
                continue
            async with self._lock:
                if txid in self._inflight:
                    continue
                self._inflight[txid] = InflightEntry(
                    peer_id=peer_id, deadline=time.time() + self.inflight_timeout_s
                )
            missing.append(txid)
        if missing:
            await self._send_tx_get(peer_id, missing)
            log.info(
                "TX_GET_SEND",
                extra={"peer": peer_id, "count": len(missing)},
            )

    async def _broadcast_inv(self, txids: Iterable[bytes], *, exclude_peer: Optional[str]) -> None:
        async with self._lock:
            for peer_id in self._eligible_peers():
                if exclude_peer and peer_id == exclude_peer:
                    continue
                state = self._ensure_peer(peer_id)
                for txid in txids:
                    if txid in state.known_txids:
                        continue
                    state.inv_queue.append(txid)

    async def announce_txids(
        self, txids: Iterable[bytes], *, exclude_peer: Optional[str] = None
    ) -> None:
        await self._broadcast_inv(txids, exclude_peer=exclude_peer)

    async def inv_flush_loop(self) -> None:
        self._running = True
        last_heartbeat = 0.0
        while self._running:
            try:
                await asyncio.sleep(self.inv_flush_interval_s)
                now = time.time()
                async with self._lock:
                    peer_states = list(self._peer_state.values())
                for state in peer_states:
                    if not state.inv_queue:
                        continue
                    batch: List[bytes] = []
                    while state.inv_queue and len(batch) < self.inv_batch_size:
                        batch.append(state.inv_queue.popleft())
                    if not batch:
                        continue
                    if not self._inv_limiter.consume(state.peer_id, len(batch)):
                        state.inv_queue.extendleft(reversed(batch))
                        continue
                    await self._send_tx_inv(state.peer_id, batch)
                    for txid in batch:
                        state.known_txids.add(txid)
                    log.info(
                        "TX_INV_SEND",
                        extra={"peer": state.peer_id, "count": len(batch)},
                    )
                if now - last_heartbeat >= 10.0:
                    last_heartbeat = now
                    log.info(
                        "TX_RELAY_HEARTBEAT",
                        extra={"loop": "inv_flush", "peers": len(peer_states)},
                    )
            except Exception:
                log.warning("tx inv flush loop error", exc_info=True)

    async def inflight_timeout_loop(self) -> None:
        self._running = True
        last_heartbeat = 0.0
        while self._running:
            try:
                await asyncio.sleep(0.5)
                now = time.time()
                expired: List[bytes] = []
                for txid, entry in list(self._inflight.items()):
                    if entry.deadline <= now:
                        expired.append(txid)
                for txid in expired:
                    entry = self._inflight.pop(txid, None)
                    if entry is None:
                        continue
                    log.info(
                        "TX_INFLIGHT_TIMEOUT",
                        extra={
                            "hash": txid.hex(),
                            "peer": entry.peer_id,
                            "attempts": entry.attempts,
                        },
                    )
                    sources = list(self._tx_sources.get(txid, set()))
                    candidates = [
                        p for p in sources if p != entry.peer_id and self._peer_eligible(p)
                    ]
                    if candidates and entry.attempts < self.inflight_max_retries:
                        next_peer = candidates[0]
                        self._inflight[txid] = InflightEntry(
                            peer_id=next_peer,
                            deadline=now + self.inflight_timeout_s,
                            attempts=entry.attempts + 1,
                        )
                        await self._send_tx_get(next_peer, [txid])
                        log.info(
                            "TX_GET_SEND",
                            extra={"peer": next_peer, "count": 1, "retry": True},
                        )
                if now - last_heartbeat >= 10.0:
                    last_heartbeat = now
                    log.info(
                        "TX_RELAY_HEARTBEAT",
                        extra={"loop": "inflight_timeout", "inflight": len(self._inflight)},
                    )
            except Exception:
                log.warning("tx inflight timeout loop error", exc_info=True)

    async def mempool_sync_loop(self) -> None:
        self._running = True
        last_heartbeat = 0.0
        while self._running:
            try:
                await asyncio.sleep(1.0)
                now = time.time()
                async with self._lock:
                    peer_states = list(self._peer_state.values())
                for state in peer_states:
                    if not self._peer_eligible(state.peer_id):
                        continue
                    if now - state.last_sync_sent_at < self.mempool_sync_interval_s:
                        continue
                    state.last_sync_sent_at = now
                    await self._send_mempool_req(state.peer_id, self.mempool_sync_limit)
                    log.info(
                        "TX_SYNC_REQ",
                        extra={"peer": state.peer_id, "limit": self.mempool_sync_limit},
                    )
                if now - last_heartbeat >= 10.0:
                    last_heartbeat = now
                    log.info(
                        "TX_RELAY_HEARTBEAT",
                        extra={"loop": "mempool_sync", "peers": len(peer_states)},
                    )
            except Exception:
                log.warning("tx mempool sync loop error", exc_info=True)

    def snapshot(self) -> dict[str, Any]:
        peers = []
        for state in self._peer_state.values():
            peers.append(
                {
                    "peer": state.peer_id,
                    "known_txids": len(state.known_txids),
                    "inv_queue": len(state.inv_queue),
                    "last_sync_sent_at": state.last_sync_sent_at or None,
                    "last_sync_recv_at": state.last_sync_recv_at or None,
                }
            )
        return {
            "inflight": len(self._inflight),
            "peers": peers,
        }

    async def request_mempool_sync(self, peer_id: str) -> None:
        state = self._ensure_peer(peer_id)
        state.last_sync_sent_at = time.time()
        await self._send_mempool_req(peer_id, self.mempool_sync_limit)
        log.info(
            "TX_SYNC_REQ",
            extra={"peer": peer_id, "limit": self.mempool_sync_limit, "trigger": "connect"},
        )
