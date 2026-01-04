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

    def remove(self, txid: bytes) -> None:
        """Remove a txid from the set if present."""
        self._items.pop(txid, None)

    def __contains__(self, txid: bytes) -> bool:
        return txid in self._items

    def __len__(self) -> int:
        return len(self._items)

    def sample(self, limit: int = 20) -> List[bytes]:
        if limit <= 0:
            return []
        items = list(self._items.keys())
        return items[-limit:]


@dataclass(slots=True)
class PeerTxState:
    conn_id: str
    peer_node_id: Optional[str]
    direction: Optional[str]
    remote: Optional[str]
    known_txids: TxIdSetLRU
    inv_queue: Deque[bytes] = field(default_factory=deque)
    last_sync_sent_at: float = 0.0
    last_sync_recv_at: float = 0.0


@dataclass(slots=True)
class InflightEntry:
    conn_id: str
    peer_node_id: Optional[str]
    deadline: float
    attempts: int = 1
    requested_at: float = 0.0


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
        self._reject_cache: "OrderedDict[bytes, float]" = OrderedDict()
        self._reject_cache_ttl_s = float(
            max(5.0, min(self.inflight_timeout_s, 30.0))
        )
        self._reject_cache_cap = int(max(1000, min(self.known_txids_cap, 50_000)))
        self._inv_limiter = TokenBucket(inv_rate_per_sec, inv_burst)
        self._tx_data_limiter = TokenBucket(
            tx_data_rate_bytes_per_sec, tx_data_burst_bytes
        )
        self._running = False
        self._lock = asyncio.Lock()

    def register_peer(
        self,
        conn_id: str,
        *,
        peer_node_id: Optional[str] = None,
        direction: Optional[str] = None,
        remote: Optional[str] = None,
    ) -> None:
        if conn_id not in self._peer_state:
            self._peer_state[conn_id] = PeerTxState(
                conn_id=conn_id,
                peer_node_id=peer_node_id,
                direction=direction,
                remote=remote,
                known_txids=TxIdSetLRU(self.known_txids_cap),
            )
        else:
            state = self._peer_state[conn_id]
            state.peer_node_id = peer_node_id or state.peer_node_id
            state.direction = direction or state.direction
            state.remote = remote or state.remote

    def unregister_peer(self, conn_id: str) -> None:
        self._peer_state.pop(conn_id, None)
        for txid, entry in list(self._inflight.items()):
            if entry.conn_id == conn_id:
                self._inflight.pop(txid, None)

    def _eligible_peers(self) -> List[str]:
        return [p for p in self._peer_ids() if self._peer_eligible(p)]

    def _ensure_peer(self, conn_id: str) -> PeerTxState:
        state = self._peer_state.get(conn_id)
        if state is None:
            state = PeerTxState(
                conn_id=conn_id,
                peer_node_id=None,
                direction=None,
                remote=None,
                known_txids=TxIdSetLRU(self.known_txids_cap),
            )
            self._peer_state[conn_id] = state
        return state

    def _mark_known(self, conn_id: str, txid: bytes) -> None:
        self._ensure_peer(conn_id).known_txids.add(txid)

    def _peer_log_extra(self, conn_id: str) -> dict[str, Optional[str]]:
        state = self._peer_state.get(conn_id)
        return {
            "conn_id": conn_id,
            "peer_id": state.peer_node_id if state else None,
            "peer_node_id": state.peer_node_id if state else None,
        }

    def _reject_remember(self, txid: bytes) -> None:
        expire_at = time.time() + self._reject_cache_ttl_s
        self._reject_cache[txid] = expire_at
        self._reject_cache.move_to_end(txid, last=True)
        while len(self._reject_cache) > self._reject_cache_cap:
            self._reject_cache.popitem(last=False)

    def _reject_recent(self, txid: bytes) -> bool:
        now = time.time()
        expire_at = self._reject_cache.get(txid)
        if expire_at is None:
            return False
        if expire_at <= now:
            self._reject_cache.pop(txid, None)
            return False
        return True

    async def on_mempool_add(self, txid: bytes, raw: bytes) -> None:
        async with self._lock:
            peers = self._eligible_peers()
            for conn_id in peers:
                state = self._ensure_peer(conn_id)
                if txid in state.known_txids:
                    continue
                state.inv_queue.append(txid)
        log.info("TX_ACCEPT_LOCAL", extra={"hash": txid.hex(), "bytes": len(raw)})

    async def on_tx_inv(self, conn_id: str, txids: Iterable[bytes]) -> None:
        tx_list = list(txids)
        log.info(
            "TX_INV_RECEIVED",
            extra={
                "peer": conn_id,
                "count": len(tx_list),
                "first_3_txids": [t.hex()[:16] for t in tx_list[:3]],
                "source": "tx_inv",
                **self._peer_log_extra(conn_id),
            },
        )
        needs_check: List[bytes] = []
        async with self._lock:
            state = self._ensure_peer(conn_id)
            for txid in tx_list:
                state.known_txids.add(txid)
                self._tx_sources.setdefault(txid, set()).add(conn_id)
                if txid in self._inflight:
                    log.debug(
                        "TX_INV_SKIP_INFLIGHT",
                        extra={
                            "peer": conn_id,
                            "txid": txid.hex()[:16],
                            **self._peer_log_extra(conn_id),
                        },
                    )
                    continue
                needs_check.append(txid)
        
        log.info(
            "TX_INV_NEEDS_CHECK",
            extra={
                "peer": conn_id,
                "needs_check_count": len(needs_check),
                **self._peer_log_extra(conn_id),
            },
        )
        
        missing: List[bytes] = []
        now = time.time()
        for txid in needs_check:
            if self._reject_recent(txid):
                log.debug(
                    "TX_INV_SKIP_REJECTED",
                    extra={
                        "peer": conn_id,
                        "txid": txid.hex()[:16],
                        **self._peer_log_extra(conn_id),
                    },
                )
                continue
            if await self._has_tx(txid):
                log.debug(
                    "TX_INV_SKIP_HAVE_TX",
                    extra={
                        "peer": conn_id,
                        "txid": txid.hex()[:16],
                        **self._peer_log_extra(conn_id),
                    },
                )
                continue
            if await self._has_chain_tx(txid):
                log.debug(
                    "TX_INV_SKIP_IN_CHAIN",
                    extra={
                        "peer": conn_id,
                        "txid": txid.hex()[:16],
                        **self._peer_log_extra(conn_id),
                    },
                )
                continue
            async with self._lock:
                if txid in self._inflight:
                    continue
                self._inflight[txid] = InflightEntry(
                    conn_id=conn_id,
                    peer_node_id=self._peer_state.get(conn_id, None).peer_node_id
                    if conn_id in self._peer_state
                    else None,
                    deadline=now + self.inflight_timeout_s,
                    requested_at=now,
                )
            missing.append(txid)
        
        log.info(
            "TX_INV_MISSING",
            extra={
                "peer": conn_id,
                "missing_count": len(missing),
                "first_3_missing": [t.hex()[:16] for t in missing[:3]],
                **self._peer_log_extra(conn_id),
            },
        )
        
        if missing:
            for idx in range(0, len(missing), 256):
                batch = missing[idx : idx + 256]
                await self._send_tx_get(conn_id, batch)
                log.info(
                    "TX_GET_SENT",
                    extra={
                        "peer": conn_id,
                        "count": len(batch),
                        "first_3_txids": [t.hex()[:16] for t in batch[:3]],
                        "batch_size": len(batch),
                        **self._peer_log_extra(conn_id),
                    },
                )
        else:
            log.info(
                "TX_INV_NO_MISSING",
                extra={
                    "peer": conn_id,
                    "total_received": len(tx_list),
                    "already_have": len(tx_list) - len(needs_check),
                    "rejected": len(needs_check) - len(missing),
                    **self._peer_log_extra(conn_id),
                },
            )

    async def on_tx_get(self, conn_id: str, txids: Iterable[bytes]) -> None:
        tx_list = list(txids)
        log.info(
            "TX_GET_RECV",
            extra={"peer": conn_id, "count": len(tx_list), **self._peer_log_extra(conn_id)},
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
            if not self._tx_data_limiter.consume(conn_id, total_bytes):
                log.info(
                    "TX_DATA_SEND",
                    extra={
                        "peer": conn_id,
                        "status": "rate_limited",
                        "bytes": total_bytes,
                        **self._peer_log_extra(conn_id),
                    },
                )
            else:
                await self._send_tx_data(conn_id, send_items)
                log.info(
                    "TX_DATA_SEND",
                    extra={
                        "peer": conn_id,
                        "count": len(send_items),
                        "bytes": total_bytes,
                        **self._peer_log_extra(conn_id),
                    },
                )

        if notfound:
            await self._send_tx_notfound(conn_id, notfound)
            log.info(
                "TX_NOTFOUND",
                extra={"peer": conn_id, "count": len(notfound), **self._peer_log_extra(conn_id)},
            )

    async def on_tx_data(self, conn_id: str, items: Iterable[dict[str, Any]]) -> None:
        items_list = list(items)
        log.info(
            "TX_DATA_RECV_START",
            extra={
                "peer": conn_id,
                "item_count": len(items_list),
                **self._peer_log_extra(conn_id),
            },
        )
        
        broadcast: List[bytes] = []
        for item in items_list:
            txid = item.get("txid")
            raw = item.get("tx_bytes")
            if not isinstance(txid, (bytes, bytearray)) or not isinstance(
                raw, (bytes, bytearray)
            ):
                log.warning(
                    "TX_DATA_INVALID_ITEM",
                    extra={
                        "peer": conn_id,
                        "has_txid": isinstance(txid, (bytes, bytearray)),
                        "has_raw": isinstance(raw, (bytes, bytearray)),
                        **self._peer_log_extra(conn_id),
                    },
                )
                continue
            txid_bytes = bytes(txid)
            raw_bytes = bytes(raw)
            log.info(
                "TX_DATA_RECV",
                extra={
                    "peer": conn_id,
                    "hash": txid_bytes.hex(),
                    "txid": txid_bytes.hex(),
                    "bytes": len(raw_bytes),
                    **self._peer_log_extra(conn_id),
                },
            )
            if len(raw_bytes) > self.max_tx_bytes:
                log.warning(
                    "TX_REJECTED",
                    extra={
                        "hash": txid_bytes.hex(),
                        "reason": "oversize",
                        "size": len(raw_bytes),
                        "max": self.max_tx_bytes,
                        **self._peer_log_extra(conn_id),
                    },
                )
                self._reject_remember(txid_bytes)
                self._inflight.pop(txid_bytes, None)
                continue
            computed = sha3_256(raw_bytes)
            if computed != txid_bytes:
                log.warning(
                    "TX_REJECTED",
                    extra={
                        "hash": txid_bytes.hex(),
                        "reason": "hash_mismatch",
                        "computed": computed.hex(),
                        "expected": txid_bytes.hex(),
                        **self._peer_log_extra(conn_id),
                    },
                )
                self._reject_remember(txid_bytes)
                self._inflight.pop(txid_bytes, None)
                continue
            
            origin_peer = self._peer_state.get(conn_id, None)
            origin_label = origin_peer.peer_node_id if origin_peer else None
            
            log.info(
                "TX_DATA_CALLING_ADMIT",
                extra={
                    "peer": conn_id,
                    "hash": txid_bytes.hex(),
                    "bytes": len(raw_bytes),
                    "origin": origin_label or conn_id,
                    **self._peer_log_extra(conn_id),
                },
            )
            
            try:
                ok, reason = await self._admit_tx(raw_bytes, origin_label or conn_id)
                log.info(
                    "TX_DATA_ADMIT_RESULT",
                    extra={
                        "peer": conn_id,
                        "hash": txid_bytes.hex(),
                        "accepted": ok,
                        "reason": reason or "none",
                        **self._peer_log_extra(conn_id),
                    },
                )
            except Exception as exc:
                log.error(
                    "TX_DATA_ADMIT_EXCEPTION",
                    extra={
                        "peer": conn_id,
                        "hash": txid_bytes.hex(),
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        **self._peer_log_extra(conn_id),
                    },
                    exc_info=True,
                )
                ok = False
                reason = f"exception:{type(exc).__name__}"
            
            self._inflight.pop(txid_bytes, None)
            self._mark_known(conn_id, txid_bytes)
            if ok:
                broadcast.append(txid_bytes)
                self._reject_cache.pop(txid_bytes, None)
                log.info(
                    "TX_ACCEPTED",
                    extra={
                        "hash": txid_bytes.hex(),
                        "origin": f"peer:{origin_label or conn_id}",
                        **self._peer_log_extra(conn_id),
                    },
                )
            else:
                self._reject_remember(txid_bytes)
                log.warning(
                    "TX_REJECTED",
                    extra={
                        "hash": txid_bytes.hex(),
                        "reason": reason or "reject",
                        "origin": f"peer:{origin_label or conn_id}",
                        **self._peer_log_extra(conn_id),
                    },
                )

        if broadcast:
            log.info(
                "TX_DATA_BROADCAST",
                extra={
                    "peer": conn_id,
                    "broadcast_count": len(broadcast),
                    "first_3": [t.hex()[:16] for t in broadcast[:3]],
                    **self._peer_log_extra(conn_id),
                },
            )
            await self._broadcast_inv(broadcast, exclude_peer=conn_id)
        else:
            log.info(
                "TX_DATA_NO_BROADCAST",
                extra={
                    "peer": conn_id,
                    "items_received": len(items_list),
                    **self._peer_log_extra(conn_id),
                },
            )

    async def on_tx_notfound(self, conn_id: str, txids: Iterable[bytes]) -> None:
        tx_list = list(txids)
        async with self._lock:
            state = self._peer_state.get(conn_id)
            for txid in tx_list:
                self._inflight.pop(txid, None)
                self._reject_remember(txid)
                # Clear from peer's known_txids since they don't have it
                if state and txid in state.known_txids:
                    state.known_txids.remove(txid)
        log.info(
            "TX_NOTFOUND",
            extra={"peer": conn_id, "count": len(tx_list), **self._peer_log_extra(conn_id)},
        )

    async def on_mempool_req(self, conn_id: str, limit: Optional[int] = None) -> None:
        lim = int(limit) if limit is not None else self.mempool_sync_limit
        txids = await self._list_mempool_hashes(lim)
        await self._send_mempool_resp(conn_id, txids)
        log.info(
            "TX_SYNC_RESP_SEND",
            extra={"peer": conn_id, "count": len(txids), **self._peer_log_extra(conn_id)},
        )

    async def on_mempool_resp(self, conn_id: str, txids: Iterable[bytes]) -> None:
        tx_list = list(txids)
        log.info(
            "TXIDS_LEARNED",
            extra={
                "peer": conn_id,
                "count": len(tx_list),
                "source": "mempool_sync",
                **self._peer_log_extra(conn_id),
            },
        )
        needs_check: List[bytes] = []
        async with self._lock:
            state = self._ensure_peer(conn_id)
            for txid in tx_list:
                state.known_txids.add(txid)
                self._tx_sources.setdefault(txid, set()).add(conn_id)
                if txid in self._inflight:
                    continue
                needs_check.append(txid)
            state.last_sync_recv_at = time.time()
        log.info(
            "TX_SYNC_RESP_RECV",
            extra={"peer": conn_id, "count": len(tx_list), **self._peer_log_extra(conn_id)},
        )
        want_txids: List[bytes] = []
        for txid in needs_check:
            if self._reject_recent(txid):
                continue
            if await self._has_tx(txid):
                continue
            if await self._has_chain_tx(txid):
                continue
            async with self._lock:
                if txid in self._inflight:
                    continue
                self._inflight[txid] = InflightEntry(
                    conn_id=conn_id,
                    peer_node_id=self._peer_state.get(conn_id, None).peer_node_id
                    if conn_id in self._peer_state
                    else None,
                    deadline=time.time() + self.inflight_timeout_s,
                    requested_at=time.time(),
                )
            want_txids.append(txid)
        if want_txids:
            for idx in range(0, len(want_txids), 256):
                batch = want_txids[idx : idx + 256]
                await self._send_tx_get(conn_id, batch)
                log.info(
                    "TX_GET_SENT",
                    extra={
                        "peer": conn_id,
                        "count": len(batch),
                        **self._peer_log_extra(conn_id),
                    },
                )

    async def _broadcast_inv(
        self, txids: Iterable[bytes], *, exclude_peer: Optional[str]
    ) -> None:
        async with self._lock:
            for conn_id in self._eligible_peers():
                if exclude_peer and conn_id == exclude_peer:
                    continue
                state = self._ensure_peer(conn_id)
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
                    if not self._inv_limiter.consume(state.conn_id, len(batch)):
                        state.inv_queue.extendleft(reversed(batch))
                        continue
                    await self._send_tx_inv(state.conn_id, batch)
                    for txid in batch:
                        state.known_txids.add(txid)
                    log.info(
                        "TX_INV_SEND",
                        extra={
                            "peer": state.conn_id,
                            "count": len(batch),
                            **self._peer_log_extra(state.conn_id),
                        },
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
                            "peer": entry.conn_id,
                            "last_peer": entry.conn_id,
                            "attempts": entry.attempts,
                            **self._peer_log_extra(entry.conn_id),
                        },
                    )
                    sources = list(self._tx_sources.get(txid, set()))
                    candidates = [
                        p for p in sources if p != entry.conn_id and self._peer_eligible(p)
                    ]
                    if candidates and entry.attempts < self.inflight_max_retries:
                        next_peer = candidates[0]
                        self._inflight[txid] = InflightEntry(
                            conn_id=next_peer,
                            peer_node_id=self._peer_state.get(next_peer, None).peer_node_id
                            if next_peer in self._peer_state
                            else None,
                            deadline=now + self.inflight_timeout_s,
                            attempts=entry.attempts + 1,
                        )
                        await self._send_tx_get(next_peer, [txid])
                        log.info(
                            "TX_GET_SENT",
                            extra={
                                "peer": next_peer,
                                "count": 1,
                                "retry": True,
                                **self._peer_log_extra(next_peer),
                            },
                        )
                    else:
                        # No more retry candidates or max retries reached.
                        # Remove txid from known_txids of all source peers so they can
                        # announce it again, enabling transaction propagation recovery.
                        async with self._lock:
                            for source_conn_id in sources:
                                state = self._peer_state.get(source_conn_id)
                                if state and txid in state.known_txids:
                                    state.known_txids.remove(txid)
                        log.info(
                            "TX_FETCH_ABANDONED",
                            extra={
                                "hash": txid.hex(),
                                "attempts": entry.attempts,
                                "cleared_from_peers": len(sources),
                            },
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
                    if not self._peer_eligible(state.conn_id):
                        continue
                    if now - state.last_sync_sent_at < self.mempool_sync_interval_s:
                        continue
                    state.last_sync_sent_at = now
                    await self._send_mempool_req(state.conn_id, self.mempool_sync_limit)
                    log.info(
                        "TX_SYNC_REQ",
                        extra={
                            "peer": state.conn_id,
                            "limit": self.mempool_sync_limit,
                            **self._peer_log_extra(state.conn_id),
                        },
                    )
                if now - last_heartbeat >= 10.0:
                    last_heartbeat = now
                    log.info(
                        "TX_RELAY_HEARTBEAT",
                        extra={"loop": "mempool_sync", "peers": len(peer_states)},
                    )
            except Exception:
                log.warning("tx mempool sync loop error", exc_info=True)

    async def request_missing_known(self, limit: int = 128) -> int:
        if limit <= 0:
            return 0
        requests_by_peer: Dict[str, List[bytes]] = {}
        async with self._lock:
            peer_states = list(self._peer_state.values())
        now = time.time()
        remaining = int(limit)
        for state in peer_states:
            if remaining <= 0:
                break
            candidates = state.known_txids.sample(limit=remaining)
            for txid in candidates:
                if remaining <= 0:
                    break
                if txid in self._inflight:
                    continue
                if self._reject_recent(txid):
                    continue
                if await self._has_tx(txid):
                    continue
                if await self._has_chain_tx(txid):
                    continue
                self._inflight[txid] = InflightEntry(
                    conn_id=state.conn_id,
                    peer_node_id=state.peer_node_id,
                    deadline=now + self.inflight_timeout_s,
                    requested_at=now,
                )
                self._tx_sources.setdefault(txid, set()).add(state.conn_id)
                requests_by_peer.setdefault(state.conn_id, []).append(txid)
                remaining -= 1
        total = 0
        for conn_id, txids in requests_by_peer.items():
            for idx in range(0, len(txids), 256):
                batch = txids[idx : idx + 256]
                await self._send_tx_get(conn_id, batch)
                total += len(batch)
                log.info(
                    "TX_GET_SENT",
                    extra={
                        "peer": conn_id,
                        "count": len(batch),
                        "trigger": "template_fetch",
                        **self._peer_log_extra(conn_id),
                    },
                )
        return total

    def snapshot(self) -> dict[str, Any]:
        peers = []
        for state in self._peer_state.values():
            peers.append(
                {
                    "conn_id": state.conn_id,
                    "peer_node_id": state.peer_node_id,
                    "direction": state.direction,
                    "remote": state.remote,
                    "known_txids": len(state.known_txids),
                    "known_txids_sample": [
                        f"0x{txid.hex()}" for txid in state.known_txids.sample()
                    ],
                    "inv_queue": len(state.inv_queue),
                    "last_sync_sent_at": state.last_sync_sent_at or None,
                    "last_sync_recv_at": state.last_sync_recv_at or None,
                }
            )
        return {
            "inflight": len(self._inflight),
            "peers": peers,
        }

    async def request_mempool_sync(self, conn_id: str) -> None:
        state = self._ensure_peer(conn_id)
        state.last_sync_sent_at = time.time()
        await self._send_mempool_req(conn_id, self.mempool_sync_limit)
        log.info(
            "TX_SYNC_REQ",
            extra={
                "peer": conn_id,
                "limit": self.mempool_sync_limit,
                "trigger": "connect",
                **self._peer_log_extra(conn_id),
            },
        )
