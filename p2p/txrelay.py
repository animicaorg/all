from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, Iterable, List, Optional, Set, Tuple
from uuid import UUID

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
    inv_queue_timestamps: Dict[bytes, float] = field(default_factory=dict)
    last_sync_sent_at: float = 0.0
    last_sync_recv_at: float = 0.0


@dataclass(slots=True)
class PeerTxStatus:
    state: str
    last_updated_at: float
    last_reason: Optional[str] = None
    attempts: int = 0


@dataclass(slots=True)
class TxGlobalState:
    txid: bytes
    arrival_time: float
    source: str
    tx_bytes: Optional[bytes] = None
    canonical_bytes: Optional[bytes] = None
    validation_status: str = "unknown"
    validation_reason: Optional[str] = None
    mempool_status: str = "not_in_pool"
    mempool_reason: Optional[str] = None
    last_updated_at: float = 0.0
    last_peer: Optional[str] = None
    last_peer_node_id: Optional[str] = None
    last_peer_conn_id: Optional[str] = None


@dataclass(slots=True)
class InflightEntry:
    conn_id: str
    peer_node_id: Optional[str]
    deadline: float
    attempts: int = 1
    requested_at: float = 0.0


@dataclass(slots=True)
class TxRequestState:
    first_seen_at: float
    last_updated_at: float
    state: str
    last_peer: Optional[str] = None
    last_peer_node_id: Optional[str] = None
    last_peer_conn_id: Optional[str] = None
    last_reason: Optional[str] = None
    attempts: int = 0
    next_retry_at: float = 0.0
    has_bytes: bool = False
    validated_ok: bool = False
    validated_fail: bool = False
    terminal: bool = False
    requested_peers: Set[str] = field(default_factory=set)
    unavailable_failures: int = 0
    unavailable_failure_times: Deque[float] = field(default_factory=deque)


class TxRequestManager:
    def __init__(self, *, cooldown_s: float, invalid_cooldown_s: float, cap: int) -> None:
        self.cooldown_s = float(cooldown_s)
        self.invalid_cooldown_s = float(invalid_cooldown_s)
        self.cap = int(cap)
        self._states: "OrderedDict[bytes, TxRequestState]" = OrderedDict()
        self._state_rank: Dict[str, int] = {
            "announced_only": 10,
            "requested": 20,
            "received_bytes": 30,
            "received_valid_pending": 40,
            "dropped_evicted": 15,
            "unavailable": 15,
            "accepted_in_mempool": 90,
            "invalid_final": 90,
        }

    def _rank(self, state: str) -> int:
        return self._state_rank.get(state, 0)

    def _is_terminal(self, state: str) -> bool:
        return state in {"invalid_final", "accepted_in_mempool", "mined", "confirmed"}

    def _parse_peer(self, peer: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        if not peer:
            return None, None, None
        peer = str(peer)
        if peer.startswith("0x"):
            return peer.lower(), None, peer.lower()
        raw = peer.lower()
        if len(raw) == 64 and all(ch in "0123456789abcdef" for ch in raw):
            node_id = f"0x{raw}"
            return node_id, None, node_id
        try:
            UUID(peer)
            return None, peer, None
        except Exception:
            return None, peer, None

    def _touch(
        self,
        txid: bytes,
        *,
        now: float,
        state: str,
        peer: Optional[str] = None,
        reason: Optional[str] = None,
        clear_reason: bool = False,
    ) -> TxRequestState:
        entry = self._states.get(txid)
        if entry is None:
            entry = TxRequestState(
                first_seen_at=now,
                last_updated_at=now,
                state=state,
                last_peer=peer,
                last_peer_node_id=None,
                last_peer_conn_id=None,
                last_reason=reason,
                attempts=0,
                next_retry_at=0.0,
            )
            self._states[txid] = entry
        current_rank = self._rank(entry.state)
        target_rank = self._rank(state)
        if target_rank < current_rank:
            log.info(
                "TX_STATE_TRANSITION_IGNORED",
                extra={"txid": txid.hex(), "current": entry.state, "target": state},
            )
            return entry
        if self._is_terminal(entry.state) and not self._is_terminal(state):
            return entry
        entry.last_updated_at = now
        entry.state = state
        if peer is not None:
            node_id, conn_id, normalized = self._parse_peer(peer)
            entry.last_peer = normalized or peer
            if node_id is not None:
                entry.last_peer_node_id = node_id
            if conn_id is not None:
                entry.last_peer_conn_id = conn_id
        if clear_reason:
            entry.last_reason = None
        elif reason is not None:
            entry.last_reason = reason
        self._states.move_to_end(txid, last=True)
        while len(self._states) > self.cap:
            self._states.popitem(last=False)
        return entry

    def mark_announced(self, txid: bytes, *, peer: Optional[str], now: float) -> None:
        entry = self._states.get(txid)
        if entry and entry.state in {"accepted_in_mempool", "invalid_final"}:
            return
        self._touch(txid, now=now, state="announced_only", peer=peer, clear_reason=True)

    def mark_requested(self, txid: bytes, *, peer: str, now: float) -> TxRequestState:
        entry = self._touch(txid, now=now, state="requested", peer=peer, clear_reason=True)
        if entry.state != "requested":
            return entry
        entry.attempts = min(entry.attempts + 1, 10_000)
        entry.requested_peers.add(peer)
        entry.next_retry_at = now + self.cooldown_s
        return entry

    def mark_received_valid(self, txid: bytes, *, peer: Optional[str], now: float) -> None:
        entry = self._touch(txid, now=now, state="received_valid_pending", peer=peer)
        entry.has_bytes = True

    def mark_received_bytes(self, txid: bytes, *, peer: Optional[str], now: float) -> None:
        entry = self._touch(txid, now=now, state="received_bytes", peer=peer)
        entry.has_bytes = True

    def mark_received_invalid(
        self, txid: bytes, *, peer: Optional[str], reason: Optional[str], now: float
    ) -> None:
        entry = self._touch(txid, now=now, state="invalid_final", peer=peer, reason=reason)
        entry.validated_fail = True
        entry.terminal = True
        entry.next_retry_at = now + max(self.invalid_cooldown_s, self.cooldown_s)

    def mark_accepted(self, txid: bytes, *, peer: Optional[str], now: float) -> None:
        entry = self._touch(txid, now=now, state="accepted_in_mempool", peer=peer)
        entry.validated_ok = True
        entry.terminal = True

    def mark_dropped(
        self, txid: bytes, *, peer: Optional[str], reason: Optional[str], now: float
    ) -> None:
        entry = self._touch(txid, now=now, state="dropped_evicted", peer=peer, reason=reason)
        # Reset next_retry_at to allow immediate retry from other peers
        # This ensures dropped transactions can be re-requested without waiting
        # for the old cooldown period to expire
        entry.next_retry_at = now

    def mark_unavailable(
        self,
        txid: bytes,
        *,
        peer: Optional[str],
        reason: Optional[str],
        now: float,
        backoff_s: float,
    ) -> TxRequestState:
        entry = self._touch(txid, now=now, state="unavailable", peer=peer, reason=reason)
        entry.unavailable_failure_times.append(now)
        while entry.unavailable_failure_times and (now - entry.unavailable_failure_times[0]) > 300.0:
            entry.unavailable_failure_times.popleft()
        entry.unavailable_failures = len(entry.unavailable_failure_times)
        entry.next_retry_at = now + max(1.0, float(backoff_s))
        return entry

    def should_skip_unavailable(self, txid: bytes, *, now: float, threshold: int = 3) -> bool:
        entry = self._states.get(txid)
        if entry is None:
            return False
        while entry.unavailable_failure_times and (now - entry.unavailable_failure_times[0]) > 300.0:
            entry.unavailable_failure_times.popleft()
        entry.unavailable_failures = len(entry.unavailable_failure_times)
        return entry.unavailable_failures >= threshold

    def can_request(self, txid: bytes, *, now: float) -> bool:
        entry = self._states.get(txid)
        if entry is None:
            return True
        if entry.state in {"accepted_in_mempool", "invalid_final"}:
            return False
        return entry.next_retry_at <= now

    def pick_peer(self, txid: bytes, *, candidates: Iterable[str]) -> Optional[str]:
        entry = self._states.get(txid)
        if entry is None:
            return next(iter(candidates), None)
        for peer in candidates:
            if peer not in entry.requested_peers:
                return peer
        return next(iter(candidates), None)

    def get_state(self, txid: bytes) -> Optional[TxRequestState]:
        return self._states.get(txid)

    def clear_state(self, txid: bytes) -> bool:
        """
        Clear the state for a transaction ID.
        
        Returns True if state was present and cleared, False otherwise.
        Used to handle stale states (e.g., marked as accepted but not in mempool).
        """
        return self._states.pop(txid, None) is not None

    def snapshot(self, *, limit: int = 20) -> List[dict[str, Any]]:
        items = list(self._states.items())[-limit:]
        return [
            {
                "txid": "0x" + txid.hex(),
                "state": entry.state,
                "last_peer": entry.last_peer,
                "last_peer_node_id": entry.last_peer_node_id,
                "last_peer_conn_id": entry.last_peer_conn_id,
                "last_reason": entry.last_reason,
                "attempts": entry.attempts,
                "has_bytes": entry.has_bytes,
                "validated_ok": entry.validated_ok,
                "validated_fail": entry.validated_fail,
                "terminal": entry.terminal,
                "first_seen_at": entry.first_seen_at,
                "last_updated_at": entry.last_updated_at,
                "requested_peers": len(entry.requested_peers),
                "unavailable_failures": entry.unavailable_failures,
            }
            for txid, entry in items
        ]

    def counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for entry in self._states.values():
            counts[entry.state] = counts.get(entry.state, 0) + 1
        return counts


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
OnTxAcceptedFn = Callable[[bytes, bytes, Optional[str]], Awaitable[None]]


class TxRelayService:
    def __init__(
        self,
        *,
        max_tx_bytes: int,
        inv_batch_size: int = 200,
        inv_flush_interval_s: float = 0.2,
        inflight_timeout_s: float = 10.0,
        inflight_max_retries: int = 2,
        request_cooldown_s: float = 3.5,
        invalid_tx_cooldown_s: float = 1800.0,
        max_inflight_total: int = 2048,
        max_inflight_per_peer: int = 128,
        tx_state_cap: int = 50_000,
        mempool_sync_interval_s: float = 15.0,
        mempool_sync_limit: int = 2000,
        mempool_watchdog_interval_s: float = 3.0,
        mempool_watchdog_limit: int = 256,
        known_txids_cap: int = 50_000,
        reconcile_interval_s: float = 10.0,
        reconcile_batch_size: int = 64,
        debug_enabled: bool = False,
        inv_rate_per_sec: float = 2000.0,
        inv_burst: float = 4000.0,
        tx_data_rate_bytes_per_sec: float = 5_000_000.0,
        tx_data_burst_bytes: float = 10_000_000.0,
        inv_queue_timeout_s: Optional[float] = None,
        peer_ids: PeerListFn,
        peer_eligible: PeerEligibleFn,
        send_tx_inv: SendFn,
        send_tx_get: SendFn,
        send_tx_data: SendFn,
        send_tx_notfound: SendFn,
        send_mempool_req: SendFn,
        send_mempool_resp: SendFn,
        send_mempool_summary: Optional[SendFn] = None,
        has_tx: HasTxFn,
        has_chain_tx: HasChainTxFn,
        get_tx_raw: GetTxFn,
        admit_tx: AdmitTxFn,
        list_mempool_hashes: ListHashesFn,
        on_tx_accepted: Optional[OnTxAcceptedFn] = None,
    ) -> None:
        self.max_tx_bytes = int(max_tx_bytes)
        self.inv_batch_size = int(inv_batch_size)
        self.inv_flush_interval_s = float(inv_flush_interval_s)
        self.inflight_timeout_s = float(inflight_timeout_s)
        self.inflight_max_retries = int(inflight_max_retries)
        self.request_cooldown_s = float(request_cooldown_s)
        self.invalid_tx_cooldown_s = float(invalid_tx_cooldown_s)
        self.max_inflight_total = int(max_inflight_total)
        self.max_inflight_per_peer = int(max_inflight_per_peer)
        self.tx_state_cap = int(tx_state_cap)
        self.mempool_sync_interval_s = float(mempool_sync_interval_s)
        self.mempool_sync_limit = int(mempool_sync_limit)
        self.mempool_watchdog_interval_s = float(mempool_watchdog_interval_s)
        self.mempool_watchdog_limit = int(mempool_watchdog_limit)
        self.known_txids_cap = int(known_txids_cap)
        self.reconcile_interval_s = float(reconcile_interval_s)
        self.reconcile_batch_size = int(reconcile_batch_size)
        self.inv_queue_timeout_s = float(inv_queue_timeout_s or 30.0)
        self._debug_enabled = bool(debug_enabled)

        self._peer_ids = peer_ids
        self._peer_eligible = peer_eligible
        self._send_tx_inv = send_tx_inv
        self._send_tx_get = send_tx_get
        self._send_tx_data = send_tx_data
        self._send_tx_notfound = send_tx_notfound
        self._send_mempool_req = send_mempool_req
        self._send_mempool_resp = send_mempool_resp
        self._send_mempool_summary = send_mempool_summary or send_mempool_resp
        self._has_tx = has_tx
        self._has_chain_tx = has_chain_tx
        self._get_tx_raw = get_tx_raw
        self._admit_tx = admit_tx
        self._list_mempool_hashes = list_mempool_hashes
        self._on_tx_accepted = on_tx_accepted

        self._peer_state: Dict[str, PeerTxState] = {}
        self._peer_invalid_counts: Dict[str, int] = {}
        self._peer_penalty_until: Dict[str, float] = {}
        self._peer_penalty_threshold = 3
        self._peer_penalty_ttl_s = 300.0
        self._inflight: Dict[bytes, InflightEntry] = {}
        self._inflight_by_peer: Dict[str, int] = {}
        self._tx_sources: Dict[bytes, Set[str]] = {}
        self._tx_sources_order: Dict[bytes, List[str]] = {}
        self._peer_tx_state: Dict[Tuple[str, bytes], PeerTxStatus] = {}
        self._tx_store: Dict[bytes, TxGlobalState] = {}
        self._recent_txids: Deque[bytes] = deque(maxlen=4096)
        self._reject_cache: "OrderedDict[bytes, float]" = OrderedDict()
        self._reject_cache_ttl_s = float(
            max(5.0, min(self.inflight_timeout_s, 30.0))
        )
        self._reject_cache_cap = int(max(1000, min(self.known_txids_cap, 50_000)))
        # Cache for transactions that received NOTFOUND responses from peers
        # Prevents re-adding them via INV messages when peers keep advertising txids they don't have
        self._notfound_cache: "OrderedDict[bytes, float]" = OrderedDict()
        self._notfound_cache_ttl_s = 60.0  # 60 seconds cooldown before accepting re-announcements
        self._notfound_cache_cap = int(max(1000, min(self.known_txids_cap, 50_000)))
        self._inv_limiter = TokenBucket(inv_rate_per_sec, inv_burst)
        self._tx_data_limiter = TokenBucket(
            tx_data_rate_bytes_per_sec, tx_data_burst_bytes
        )
        self._request_mgr = TxRequestManager(
            cooldown_s=self.request_cooldown_s,
            invalid_cooldown_s=self.invalid_tx_cooldown_s,
            cap=self.tx_state_cap,
        )
        self._running = False
        self._lock = asyncio.Lock()
        self._tx_locks: Dict[bytes, asyncio.Lock] = {}
        self._metrics: Dict[str, Any] = {
            "announced_count": 0,
            "received_count": 0,
            "requested_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "dropped_count": 0,
            "inv_sent": 0,
            "inv_recv": 0,
            "get_sent": 0,
            "get_recv": 0,
            "data_sent": 0,
            "data_recv": 0,
            "mempool_sync_req_sent": 0,
            "mempool_sync_resp_sent": 0,
            "mempool_sync_resp_recv": 0,
            "reconcile_runs": 0,
            "reconcile_missing_found": 0,
            "mempool_summary_sent": 0,
            "mempool_summary_recv": 0,
            "last_reconcile_at": None,
            "last_announced_at": None,
            "last_received_at": None,
            "last_requested_at": None,
            "last_accepted_at": None,
            "last_rejected_at": None,
            "last_dropped_at": None,
            "imported_ok": 0,
            "imported_internal_errors": 0,
            "imported_reject_reason_counts": {},
            "event_counts": {},
        }

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
                self._clear_inflight(txid)

    def _eligible_peers(self) -> List[str]:
        now = time.time()
        return [
            p
            for p in self._peer_ids()
            if self._peer_eligible(p) and self._peer_penalty_until.get(p, 0.0) <= now
        ]

    def _note_invalid_peer(self, conn_id: str) -> None:
        now = time.time()
        count = self._peer_invalid_counts.get(conn_id, 0) + 1
        self._peer_invalid_counts[conn_id] = count
        if count >= self._peer_penalty_threshold:
            self._peer_penalty_until[conn_id] = now + self._peer_penalty_ttl_s

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

    def _record_event(self, event_id: str, *, extra: Optional[dict[str, Any]] = None) -> None:
        counts = self._metrics.setdefault("event_counts", {})
        counts[event_id] = counts.get(event_id, 0) + 1
        log.info(event_id, extra=extra or {})

    def _tx_lock(self, txid: bytes) -> asyncio.Lock:
        lock = self._tx_locks.get(txid)
        if lock is None:
            lock = asyncio.Lock()
            self._tx_locks[txid] = lock
        return lock

    def _normalize_peer_id(self, peer: Optional[str]) -> Optional[str]:
        if peer is None:
            return None
        peer = str(peer)
        if peer.startswith("0x"):
            return peer.lower()
        raw = peer.lower()
        if len(raw) == 64 and all(ch in "0123456789abcdef" for ch in raw):
            return f"0x{raw}"
        return peer

    def _parse_peer_meta(self, peer: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        if not peer:
            return None, None, None
        peer = str(peer)
        normalized = self._normalize_peer_id(peer)
        if normalized and normalized.startswith("0x"):
            return normalized, None, normalized
        try:
            UUID(peer)
            return None, peer, normalized
        except Exception:
            return None, peer, normalized

    def _set_peer_tx_state(
        self,
        conn_id: str,
        txid: bytes,
        state: str,
        *,
        reason: Optional[str] = None,
        increment_attempt: bool = False,
    ) -> None:
        key = (conn_id, txid)
        now = time.time()
        entry = self._peer_tx_state.get(key)
        if entry is None:
            entry = PeerTxStatus(state=state, last_updated_at=now, last_reason=reason, attempts=0)
            self._peer_tx_state[key] = entry
        entry.state = state
        entry.last_updated_at = now
        if reason is not None:
            entry.last_reason = reason
        if increment_attempt:
            entry.attempts += 1

    def _touch_tx_store(
        self,
        txid: bytes,
        *,
        source: Optional[str] = None,
        tx_bytes: Optional[bytes] = None,
        canonical_bytes: Optional[bytes] = None,
        validation_status: Optional[str] = None,
        validation_reason: Optional[str] = None,
        mempool_status: Optional[str] = None,
        mempool_reason: Optional[str] = None,
        last_peer: Optional[str] = None,
        last_peer_node_id: Optional[str] = None,
        last_peer_conn_id: Optional[str] = None,
    ) -> None:
        now = time.time()
        entry = self._tx_store.get(txid)
        if entry is None:
            entry = TxGlobalState(
                txid=txid,
                arrival_time=now,
                source=source or "unknown",
                tx_bytes=tx_bytes,
                canonical_bytes=canonical_bytes,
                validation_status=validation_status or "unknown",
                validation_reason=validation_reason,
                mempool_status=mempool_status or "not_in_pool",
                mempool_reason=mempool_reason,
                last_updated_at=now,
                last_peer=self._normalize_peer_id(last_peer),
                last_peer_node_id=self._normalize_peer_id(last_peer_node_id),
                last_peer_conn_id=last_peer_conn_id,
            )
            self._tx_store[txid] = entry
            return
        entry.last_updated_at = now
        if source is not None:
            entry.source = source
        if tx_bytes is not None:
            entry.tx_bytes = tx_bytes
        if canonical_bytes is not None:
            entry.canonical_bytes = canonical_bytes
        if validation_status is not None:
            entry.validation_status = validation_status
        if validation_reason is not None:
            entry.validation_reason = validation_reason
        if mempool_status is not None:
            entry.mempool_status = mempool_status
        if mempool_reason is not None:
            entry.mempool_reason = mempool_reason
        if last_peer is not None:
            entry.last_peer = self._normalize_peer_id(last_peer)
        if last_peer_node_id is not None:
            entry.last_peer_node_id = self._normalize_peer_id(last_peer_node_id)
        if last_peer_conn_id is not None:
            entry.last_peer_conn_id = last_peer_conn_id

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

    def _notfound_remember(self, txid: bytes) -> None:
        """Remember that a transaction received a NOTFOUND response."""
        expire_at = time.time() + self._notfound_cache_ttl_s
        self._notfound_cache[txid] = expire_at
        self._notfound_cache.move_to_end(txid, last=True)
        while len(self._notfound_cache) > self._notfound_cache_cap:
            self._notfound_cache.popitem(last=False)

    def _notfound_recent(self, txid: bytes) -> bool:
        """Check if a transaction recently received a NOTFOUND response."""
        now = time.time()
        expire_at = self._notfound_cache.get(txid)
        if expire_at is None:
            return False
        if expire_at <= now:
            self._notfound_cache.pop(txid, None)
            return False
        return True

    def _record_source(self, txid: bytes, conn_id: str) -> None:
        self._tx_sources.setdefault(txid, set()).add(conn_id)
        order = self._tx_sources_order.setdefault(txid, [])
        if conn_id not in order:
            order.append(conn_id)

    def _resolve_conn_id(self, peer_ref: str) -> Optional[str]:
        """Resolve peer reference to active connection id.

        Accepts conn_id directly, or a peer_node_id and maps it to an eligible conn_id.
        """
        if peer_ref in self._peer_state:
            return peer_ref
        candidates = [
            conn_id
            for conn_id, state in self._peer_state.items()
            if state.peer_node_id == peer_ref
        ]
        for conn_id in candidates:
            if self._peer_eligible(conn_id):
                return conn_id
        return candidates[0] if candidates else None

    def _candidate_conn_ids(self, peers: Iterable[str], *, exclude: Optional[str] = None) -> list[str]:
        out: list[str] = []
        for peer in peers:
            conn_id = self._resolve_conn_id(peer)
            if conn_id is None:
                log.info("TX_SOURCE_UNMAPPED", extra={"peer_ref": peer, "exclude": exclude})
                continue
            if exclude is not None and conn_id == exclude:
                continue
            if conn_id in out:
                continue
            out.append(conn_id)
        return out

    def _inflight_count_for_peer(self, conn_id: str) -> int:
        return self._inflight_by_peer.get(conn_id, 0)

    def _set_inflight(
        self,
        txid: bytes,
        *,
        conn_id: str,
        peer_node_id: Optional[str],
        now: float,
        attempts: int = 1,
    ) -> bool:
        if len(self._inflight) >= self.max_inflight_total:
            return False
        if self._inflight_count_for_peer(conn_id) >= self.max_inflight_per_peer:
            return False
        self._inflight[txid] = InflightEntry(
            conn_id=conn_id,
            peer_node_id=peer_node_id,
            deadline=now + self.inflight_timeout_s,
            attempts=attempts,
            requested_at=now,
        )
        self._inflight_by_peer[conn_id] = self._inflight_by_peer.get(conn_id, 0) + 1
        return True

    def _clear_inflight(self, txid: bytes) -> None:
        entry = self._inflight.pop(txid, None)
        if entry is None:
            return
        count = self._inflight_by_peer.get(entry.conn_id, 0)
        if count <= 1:
            self._inflight_by_peer.pop(entry.conn_id, None)
        else:
            self._inflight_by_peer[entry.conn_id] = count - 1

    async def on_mempool_add(self, txid: bytes, raw: bytes) -> None:
        log.info(f"[DIAG] on_mempool_add called: txid={txid.hex()}, len={len(raw)}")

        now = time.time()
        self._touch_tx_store(
            txid,
            source="local",
            tx_bytes=raw,
            validation_status="valid",
            mempool_status="in_pool",
            last_peer="local",
        )
        self._recent_txids.append(txid)
        self._request_mgr.mark_accepted(txid, peer="local", now=now)
        self._metrics["accepted_count"] += 1
        self._metrics["last_accepted_at"] = now
        async with self._lock:
            peers = self._eligible_peers()
            log.info(f"[DIAG] Eligible peers for tx broadcast: {len(peers)}, peers={peers}")
            for conn_id in peers:
                state = self._ensure_peer(conn_id)
                if txid in state.known_txids:
                    log.info(f"[DIAG] Peer {conn_id} already knows txid {txid.hex()}, skipping")
                    continue
                state.inv_queue.append(txid)
                state.inv_queue_timestamps[txid] = now
                log.info(f"[DIAG] Queued txid {txid.hex()} for peer {conn_id}, queue_len={len(state.inv_queue)}")
        log.info("TX_RELAY_ACCEPT_LOCAL", extra={"hash": txid.hex(), "bytes": len(raw)})
        self._record_event(
            "TX_RELAY_ACCEPT_LOCAL",
            extra={"hash": txid.hex(), "bytes": len(raw), "source": "local"},
        )
        self._record_event(
            "TX_RELAY_MEMPOOL_INSERT_OK",
            extra={"hash": txid.hex(), "source": "local"},
        )
        if self._on_tx_accepted is not None:
            try:
                await self._on_tx_accepted(txid, raw, "local")
            except Exception:
                log.warning("tx relay legacy announce failed", exc_info=True)

    async def on_tx_inv(self, conn_id: str, txids: Iterable[bytes]) -> None:
        tx_list = list(txids)
        self._metrics["inv_recv"] += len(tx_list)
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
                # Skip re-adding transactions that recently received NOTFOUND responses
                # This prevents infinite loops where peers keep advertising txids they don't have
                if self._notfound_recent(txid):
                    log.debug(
                        "TX_INV_SKIP_NOTFOUND_RECENT",
                        extra={
                            "peer": conn_id,
                            "txid": txid.hex()[:16],
                            "reason": "recently_notfound",
                            **self._peer_log_extra(conn_id),
                        },
                    )
                    continue

                state.known_txids.add(txid)
                self._record_source(txid, conn_id)
                self._set_peer_tx_state(conn_id, txid, "ANNOUNCED_BY_PEER")
                self._touch_tx_store(
                    txid,
                    source=f"peer:{conn_id}",
                    last_peer=conn_id,
                )
                self._request_mgr.mark_announced(txid, peer=conn_id, now=time.time())
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
        if needs_check:
            self._record_event(
                "TX_RELAY_ANNOUNCE_RECV",
                extra={
                    "peer": conn_id,
                    "count": len(needs_check),
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
                self._request_mgr.mark_accepted(txid, peer="local", now=now)
                self._touch_tx_store(
                    txid,
                    validation_status="valid",
                    mempool_status="in_pool",
                    last_peer="local",
                )
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
                self._request_mgr.mark_dropped(
                    txid, peer="chain", reason="in_chain", now=now
                )
                self._touch_tx_store(
                    txid,
                    mempool_status="evicted",
                    mempool_reason="in_chain",
                    last_peer="chain",
                )
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
                # Clear stale "accepted_in_mempool" state if transaction is not actually in mempool.
                # We only reach this point after confirming the transaction is neither in the mempool
                # nor in the chain (via has_tx and has_chain_tx checks above). So if the state is
                # "accepted_in_mempool" here, it means the state is stale/inconsistent.
                # This handles cases where transaction was evicted or state became inconsistent.
                req_state = self._request_mgr.get_state(txid)
                if req_state is not None and req_state.state == "accepted_in_mempool":
                    # Transaction announced by peer but marked as accepted while not in mempool
                    self._request_mgr.clear_state(txid)
                    log.info(
                        "TX_STATE_CLEARED",
                        extra={
                            "hash": txid.hex(),
                            "reason": "marked_accepted_but_not_in_mempool",
                            "peer": conn_id,
                            **self._peer_log_extra(conn_id),
                        },
                    )
                if not self._request_mgr.can_request(txid, now=now):
                    continue
                if not self._set_inflight(
                    txid,
                    conn_id=conn_id,
                    peer_node_id=self._peer_state.get(conn_id, None).peer_node_id
                    if conn_id in self._peer_state
                    else None,
                    now=now,
                    attempts=1,
                ):
                    log.info(
                        "TX_GET_SKIPPED",
                        extra={
                            "peer": conn_id,
                            "hash": txid.hex(),
                            "reason": "inflight_limit",
                            **self._peer_log_extra(conn_id),
                        },
                    )
                    continue
                self._request_mgr.mark_requested(txid, peer=conn_id, now=now)
                self._set_peer_tx_state(
                    conn_id, txid, "REQUESTED_FROM_PEER", increment_attempt=True
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
                self._metrics["get_sent"] += len(batch)
                self._metrics["requested_count"] += len(batch)
                self._metrics["last_requested_at"] = time.time()
                await self._send_tx_get(conn_id, batch)
                for txid in batch:
                    self._record_event(
                        "TX_RELAY_GET_SENT",
                        extra={"peer": conn_id, "hash": txid.hex(), **self._peer_log_extra(conn_id)},
                    )
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
        self._metrics["get_recv"] += len(tx_list)
        log.info(
            "TX_GET_RECV",
            extra={"peer": conn_id, "count": len(tx_list), **self._peer_log_extra(conn_id)},
        )
        for txid in tx_list:
            self._record_event(
                "TX_RELAY_GET_RECV",
                extra={"peer": conn_id, "hash": txid.hex(), **self._peer_log_extra(conn_id)},
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
                self._metrics["data_sent"] += len(send_items)
                await self._send_tx_data(conn_id, send_items)
                for item in send_items:
                    txid = item.get("txid")
                    if isinstance(txid, (bytes, bytearray)):
                        self._mark_known(conn_id, bytes(txid))
                        self._record_event(
                            "TX_RELAY_PUSH_SENT",
                            extra={
                                "peer": conn_id,
                                "hash": bytes(txid).hex(),
                                **self._peer_log_extra(conn_id),
                            },
                        )
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
            for txid in notfound:
                self._set_peer_tx_state(conn_id, txid, "INSERT_FAILED", reason="notfound")

    async def on_tx_data(self, conn_id: str, items: Iterable[dict[str, Any]]) -> None:
        items_list = list(items)
        self._metrics["data_recv"] += len(items_list)
        self._metrics["received_count"] += len(items_list)
        self._metrics["last_received_at"] = time.time()
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
            req_state = self._request_mgr.get_state(txid_bytes)
            if req_state is not None and req_state.state in {"invalid_final", "accepted_in_mempool", "admitted"}:
                log.info(
                    "TX_IMPORT_SKIP_TERMINAL",
                    extra={
                        "peer": conn_id,
                        "hash": txid_bytes.hex(),
                        "skip_reason": "terminal_state",
                        "state": req_state.state,
                        **self._peer_log_extra(conn_id),
                    },
                )
                self._clear_inflight(txid_bytes)
                continue
            normalized_raw = raw_bytes
            store_entry = self._tx_store.get(txid_bytes)
            if store_entry is not None and store_entry.canonical_bytes is not None:
                normalized_raw = store_entry.canonical_bytes
            else:
                try:
                    from core.utils.tx import normalize_tx_bytes

                    normalized_raw = normalize_tx_bytes(raw_bytes)
                except Exception:
                    normalized_raw = raw_bytes
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
            self._record_event(
                "TX_RELAY_PUSH_RECV",
                extra={"peer": conn_id, "hash": txid_bytes.hex(), **self._peer_log_extra(conn_id)},
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
                self._request_mgr.mark_received_invalid(
                    txid_bytes, peer=conn_id, reason="oversize", now=time.time()
                )
                self._note_invalid_peer(conn_id)
                self._set_peer_tx_state(conn_id, txid_bytes, "VALIDATED_FAIL", reason="oversize")
                self._touch_tx_store(
                    txid_bytes,
                    validation_status="invalid",
                    validation_reason="oversize",
                    last_peer=conn_id,
                )
                self._record_event(
                    "TX_RELAY_VALIDATE_FAIL",
                    extra={
                        "peer": conn_id,
                        "hash": txid_bytes.hex(),
                        "reason": "oversize",
                        **self._peer_log_extra(conn_id),
                    },
                )
                self._record_event(
                    "TX_RELAY_MEMPOOL_INSERT_FAIL",
                    extra={
                        "peer": conn_id,
                        "hash": txid_bytes.hex(),
                        "reason": "oversize",
                        **self._peer_log_extra(conn_id),
                    },
                )
                self._reject_remember(txid_bytes)
                self._clear_inflight(txid_bytes)
                continue
            computed = sha3_256(normalized_raw)
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
                self._request_mgr.mark_received_invalid(
                    txid_bytes, peer=conn_id, reason="hash_mismatch", now=time.time()
                )
                self._note_invalid_peer(conn_id)
                self._set_peer_tx_state(conn_id, txid_bytes, "VALIDATED_FAIL", reason="hash_mismatch")
                self._touch_tx_store(
                    txid_bytes,
                    validation_status="invalid",
                    validation_reason="hash_mismatch",
                    last_peer=conn_id,
                )
                self._record_event(
                    "TX_RELAY_VALIDATE_FAIL",
                    extra={
                        "peer": conn_id,
                        "hash": txid_bytes.hex(),
                        "reason": "hash_mismatch",
                        **self._peer_log_extra(conn_id),
                    },
                )
                self._record_event(
                    "TX_RELAY_MEMPOOL_INSERT_FAIL",
                    extra={
                        "peer": conn_id,
                        "hash": txid_bytes.hex(),
                        "reason": "hash_mismatch",
                        **self._peer_log_extra(conn_id),
                    },
                )
                self._reject_remember(txid_bytes)
                self._clear_inflight(txid_bytes)
                continue
            
            origin_peer = self._peer_state.get(conn_id, None)
            origin_label = origin_peer.peer_node_id if origin_peer else None
            
            log.info(
                "TX_DATA_CALLING_ADMIT",
                extra={
                    "peer": conn_id,
                    "hash": txid_bytes.hex(),
                    "bytes": len(normalized_raw),
                    "origin": origin_label or conn_id,
                    **self._peer_log_extra(conn_id),
                },
            )
            self._request_mgr.mark_received_bytes(
                txid_bytes, peer=origin_label or conn_id, now=time.time()
            )
            self._set_peer_tx_state(conn_id, txid_bytes, "RECEIVED_FROM_PEER")
            self._touch_tx_store(
                txid_bytes,
                source=f"peer:{origin_label or conn_id}",
                tx_bytes=normalized_raw,
                last_peer=origin_label or conn_id,
            )
            
            # Mark peer as knowing about this transaction BEFORE admitting to mempool
            # This prevents the P2P broadcast callback from trying to send it back to the originating peer
            self._mark_known(conn_id, txid_bytes)
            
            try:
                ok, reason = await self._admit_tx(normalized_raw, origin_label or conn_id)
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
            
            self._clear_inflight(txid_bytes)
            # Note: _mark_known() is called BEFORE admit_tx to prevent re-broadcast to sender
            if ok:
                self._metrics["imported_ok"] = int(self._metrics.get("imported_ok", 0)) + 1
                broadcast.append(txid_bytes)
                self._reject_cache.pop(txid_bytes, None)
                self._request_mgr.mark_accepted(
                    txid_bytes, peer=origin_label or conn_id, now=time.time()
                )
                self._recent_txids.append(txid_bytes)
                self._metrics["accepted_count"] += 1
                self._metrics["last_accepted_at"] = time.time()
                self._set_peer_tx_state(conn_id, txid_bytes, "VALIDATED_OK")
                self._set_peer_tx_state(conn_id, txid_bytes, "INSERTED_TO_MEMPOOL")
                self._touch_tx_store(
                    txid_bytes,
                    validation_status="valid",
                    mempool_status="in_pool",
                    mempool_reason=None,
                    canonical_bytes=normalized_raw,
                    last_peer=origin_label or conn_id,
                )
                self._record_event(
                    "TX_RELAY_VALIDATE_OK",
                    extra={
                        "peer": conn_id,
                        "hash": txid_bytes.hex(),
                        **self._peer_log_extra(conn_id),
                    },
                )
                self._record_event(
                    "TX_RELAY_MEMPOOL_INSERT_OK",
                    extra={
                        "peer": conn_id,
                        "hash": txid_bytes.hex(),
                        **self._peer_log_extra(conn_id),
                    },
                )
                if self._on_tx_accepted is not None:
                    try:
                        await self._on_tx_accepted(
                            txid_bytes, normalized_raw, origin_label or conn_id
                        )
                    except Exception:
                        log.warning("tx relay legacy announce failed", exc_info=True)
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
                self._request_mgr.mark_received_invalid(
                    txid_bytes,
                    peer=origin_label or conn_id,
                    reason=reason or "reject",
                    now=time.time(),
                )
                self._metrics["rejected_count"] += 1
                reason_key = str(reason or "unknown")
                rr = self._metrics.setdefault("imported_reject_reason_counts", {})
                if isinstance(rr, dict):
                    rr[reason_key] = int(rr.get(reason_key, 0)) + 1
                if reason_key.startswith("internal_error") or reason_key.startswith("exception:"):
                    self._metrics["imported_internal_errors"] = int(self._metrics.get("imported_internal_errors", 0)) + 1
                self._note_invalid_peer(conn_id)
                self._metrics["last_rejected_at"] = time.time()
                self._set_peer_tx_state(
                    conn_id, txid_bytes, "VALIDATED_FAIL", reason=reason or "reject"
                )
                self._set_peer_tx_state(
                    conn_id, txid_bytes, "INSERT_FAILED", reason=reason or "reject"
                )
                self._touch_tx_store(
                    txid_bytes,
                    validation_status="invalid",
                    validation_reason=reason or "reject",
                    mempool_status="not_in_pool",
                    mempool_reason=reason or "reject",
                    last_peer=origin_label or conn_id,
                )
                self._record_event(
                    "TX_RELAY_VALIDATE_FAIL",
                    extra={
                        "peer": conn_id,
                        "hash": txid_bytes.hex(),
                        "reason": reason or "reject",
                        **self._peer_log_extra(conn_id),
                    },
                )
                self._record_event(
                    "TX_RELAY_MEMPOOL_INSERT_FAIL",
                    extra={
                        "peer": conn_id,
                        "hash": txid_bytes.hex(),
                        "reason": reason or "reject",
                        **self._peer_log_extra(conn_id),
                    },
                )
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
        self._metrics["dropped_count"] += len(tx_list)
        self._metrics["last_dropped_at"] = time.time()
        now = time.time()
        retry_requests: Dict[str, List[bytes]] = {}  # peer -> [txids to request]
        
        async with self._lock:
            state = self._peer_state.get(conn_id)
            for txid in tx_list:
                self._clear_inflight(txid)
                self._notfound_remember(txid)  # Remember this txid received NOTFOUND
                self._request_mgr.mark_dropped(
                    txid, peer=conn_id, reason="notfound", now=now
                )
                self._set_peer_tx_state(conn_id, txid, "GAVE_UP", reason="notfound")
                self._touch_tx_store(
                    txid,
                    mempool_status="not_in_pool",
                    mempool_reason="notfound",
                    last_peer=conn_id,
                )
                
                # Clear txid only from the peer that responded with NOTFOUND
                if state and txid in state.known_txids:
                    state.known_txids.remove(txid)
                    log.debug(
                        "TX_NOTFOUND_CLEARED_FROM_PEER",
                        extra={
                            "hash": txid.hex(),
                            "peer": conn_id,
                            **self._peer_log_extra(conn_id),
                        },
                    )
                
                # Check if other peers still have this txid and try requesting from them
                sources = self._candidate_conn_ids(self._tx_sources.get(txid, set()), exclude=conn_id)
                ordered_sources = self._candidate_conn_ids(self._tx_sources_order.get(txid, []), exclude=conn_id)
                
                # Find eligible peers who still have the txid (excluding the one that just said NOTFOUND)
                candidates = [
                    p for p in ordered_sources
                    if p != conn_id and self._peer_eligible(p)
                ]
                if not candidates:
                    candidates = [
                        p for p in sources
                        if p != conn_id and self._peer_eligible(p)
                    ]
                
                # Filter to only peers who still have the txid in known_txids
                candidates = [
                    p for p in candidates
                    if p in self._peer_state and txid in self._peer_state[p].known_txids
                ]
                
                if candidates and self._request_mgr.can_request(txid, now=now):
                    # Try requesting from another peer
                    next_peer = self._request_mgr.pick_peer(txid, candidates=candidates)
                    if next_peer is not None:
                        peer_state = self._peer_state.get(next_peer)
                        if self._set_inflight(
                            txid,
                            conn_id=next_peer,
                            peer_node_id=peer_state.peer_node_id if peer_state else None,
                            now=now,
                            attempts=1,
                        ):
                            self._request_mgr.mark_requested(txid, peer=next_peer, now=now)
                            retry_requests.setdefault(next_peer, []).append(txid)
                            self._set_peer_tx_state(
                                next_peer, txid, "REQUESTED_FROM_PEER", increment_attempt=True
                            )
                            log.info(
                                "TX_NOTFOUND_RETRY_OTHER_PEER",
                                extra={
                                    "hash": txid.hex(),
                                    "notfound_peer": conn_id,
                                    "retry_peer": next_peer,
                                    "remaining_candidates": len(candidates),
                                    **self._peer_log_extra(next_peer),
                                },
                            )
                            continue
                
                # No retry candidate now -> unavailable with backoff, not permanently invalid.
                unavailable_entry = self._request_mgr.mark_unavailable(
                    txid,
                    peer=conn_id,
                    reason="notfound",
                    now=now,
                    backoff_s=self.request_cooldown_s,
                )
                log.info(
                    "TX_NOTFOUND_UNAVAILABLE",
                    extra={
                        "hash": txid.hex(),
                        "peer": conn_id,
                        "reason": "no_other_peers_with_txid",
                        "unavailable_failures": unavailable_entry.unavailable_failures,
                        **self._peer_log_extra(conn_id),
                    },
                )
        
        # Send retry requests outside the lock
        for retry_peer, retry_txids in retry_requests.items():
            for idx in range(0, len(retry_txids), 256):
                batch = retry_txids[idx : idx + 256]
                self._metrics["get_sent"] += len(batch)
                self._metrics["requested_count"] += len(batch)
                self._metrics["last_requested_at"] = time.time()
                await self._send_tx_get(retry_peer, batch)
                for txid in batch:
                    self._record_event(
                        "TX_RELAY_GET_SENT",
                        extra={
                            "peer": retry_peer,
                            "hash": txid.hex(),
                            "retry": True,
                            "reason": "notfound_from_other_peer",
                            **self._peer_log_extra(retry_peer),
                        },
                    )
        
        log.info(
            "TX_NOTFOUND",
            extra={
                "peer": conn_id,
                "count": len(tx_list),
                "retried": sum(len(v) for v in retry_requests.values()),
                **self._peer_log_extra(conn_id),
            },
        )
        for txid in tx_list:
            # Only record as failed if we didn't retry with another peer
            if not any(txid in txids for txids in retry_requests.values()):
                self._record_event(
                    "TX_RELAY_MEMPOOL_INSERT_FAIL",
                    extra={
                        "peer": conn_id,
                        "hash": txid.hex(),
                        "reason": "notfound",
                        **self._peer_log_extra(conn_id),
                    },
                )

    async def on_mempool_req(self, conn_id: str, limit: Optional[int] = None) -> None:
        lim = int(limit) if limit is not None else self.mempool_sync_limit
        txids = await self._list_mempool_hashes(lim)
        for txid in txids:
            if isinstance(txid, (bytes, bytearray)):
                self._mark_known(conn_id, bytes(txid))
        self._metrics["mempool_sync_resp_sent"] += 1
        await self._send_mempool_resp(conn_id, txids)
        log.info(
            "TX_SYNC_RESP_SEND",
            extra={"peer": conn_id, "count": len(txids), **self._peer_log_extra(conn_id)},
        )

    async def on_mempool_resp(self, conn_id: str, txids: Iterable[bytes]) -> None:
        tx_list = list(txids)
        self._metrics["mempool_sync_resp_recv"] += 1
        log.info(
            "TXIDS_LEARNED",
            extra={
                "peer": conn_id,
                "count": len(tx_list),
                "source": "mempool_sync",
                **self._peer_log_extra(conn_id),
            },
        )
        if tx_list:
            self._record_event(
                "TX_RELAY_ANNOUNCE_RECV",
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
                # Skip re-adding transactions that recently received NOTFOUND responses
                # This prevents infinite loops where peers keep advertising txids they don't have
                if self._notfound_recent(txid):
                    log.debug(
                        "TX_MEMPOOL_RESP_SKIP_NOTFOUND_RECENT",
                        extra={
                            "peer": conn_id,
                            "txid": txid.hex()[:16],
                            "reason": "recently_notfound",
                            **self._peer_log_extra(conn_id),
                        },
                    )
                    continue

                state.known_txids.add(txid)
                self._record_source(txid, conn_id)
                self._set_peer_tx_state(conn_id, txid, "ANNOUNCED_BY_PEER")
                self._touch_tx_store(
                    txid,
                    source=f"peer:{conn_id}",
                    last_peer=conn_id,
                )
                self._request_mgr.mark_announced(txid, peer=conn_id, now=time.time())
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
                self._request_mgr.mark_accepted(txid, peer="local", now=time.time())
                continue
            if await self._has_chain_tx(txid):
                self._request_mgr.mark_dropped(
                    txid, peer="chain", reason="in_chain", now=time.time()
                )
                continue
            async with self._lock:
                if txid in self._inflight:
                    continue
                now = time.time()
                if not self._request_mgr.can_request(txid, now=now):
                    continue
                if not self._set_inflight(
                    txid,
                    conn_id=conn_id,
                    peer_node_id=self._peer_state.get(conn_id, None).peer_node_id
                    if conn_id in self._peer_state
                    else None,
                    now=now,
                    attempts=1,
                ):
                    continue
                self._request_mgr.mark_requested(txid, peer=conn_id, now=now)
            want_txids.append(txid)
        if want_txids:
            for idx in range(0, len(want_txids), 256):
                batch = want_txids[idx : idx + 256]
                self._metrics["get_sent"] += len(batch)
                self._metrics["requested_count"] += len(batch)
                self._metrics["last_requested_at"] = time.time()
                await self._send_tx_get(conn_id, batch)
                for txid in batch:
                    self._record_event(
                        "TX_RELAY_GET_SENT",
                        extra={
                            "peer": conn_id,
                            "hash": txid.hex(),
                            **self._peer_log_extra(conn_id),
                        },
                    )
                log.info(
                    "TX_GET_SENT",
                    extra={
                        "peer": conn_id,
                        "count": len(batch),
                        **self._peer_log_extra(conn_id),
                    },
                )

    async def on_mempool_summary(
        self, conn_id: str, txids: Iterable[bytes], count: int = 0
    ) -> None:
        tx_list = list(txids)
        self._metrics["mempool_summary_recv"] += 1
        self._record_event(
            "TX_RELAY_MEMPOOL_SUMMARY_RECV",
            extra={
                "peer": conn_id,
                "count": int(count or len(tx_list)),
                "txids": len(tx_list),
                **self._peer_log_extra(conn_id),
            },
        )
        await self.on_mempool_resp(conn_id, tx_list)

    async def _broadcast_inv(
        self, txids: Iterable[bytes], *, exclude_peer: Optional[str]
    ) -> None:
        now = time.time()
        async with self._lock:
            for conn_id in self._eligible_peers():
                if exclude_peer and conn_id == exclude_peer:
                    continue
                state = self._ensure_peer(conn_id)
                for txid in txids:
                    if txid in state.known_txids:
                        continue
                    state.inv_queue.append(txid)
                    state.inv_queue_timestamps[txid] = now

    async def announce_txids(
        self, txids: Iterable[bytes], *, exclude_peer: Optional[str] = None
    ) -> None:
        await self._broadcast_inv(txids, exclude_peer=exclude_peer)

    async def inv_flush_loop(self) -> None:
        self._running = True
        log.info("[DIAG] inv_flush_loop STARTED")
        last_heartbeat = 0.0
        while self._running:
            try:
                await asyncio.sleep(self.inv_flush_interval_s)
                now = time.time()
                async with self._lock:
                    peer_states = list(self._peer_state.values())

                log.info(f"[DIAG] inv_flush tick: {len(peer_states)} peer states")

                for state in peer_states:
                    log.info(f"[DIAG] Checking peer {state.conn_id}: eligible={self._peer_eligible(state.conn_id)}, queue_len={len(state.inv_queue)}")

                    if not self._peer_eligible(state.conn_id):
                        # Don't clear queue - keep transactions for when peer becomes eligible
                        # But clean up transactions that have been queued too long
                        if state.inv_queue:
                            timeout = self.inv_queue_timeout_s
                            stale_txids = []
                            for txid in list(state.inv_queue):
                                queued_at = state.inv_queue_timestamps.get(txid)
                                if queued_at and (now - queued_at) > timeout:
                                    stale_txids.append(txid)

                            if stale_txids:
                                # Remove stale transactions
                                for txid in stale_txids:
                                    if txid in state.inv_queue:
                                        state.inv_queue.remove(txid)
                                    state.inv_queue_timestamps.pop(txid, None)

                                log.warning(
                                    f"[DIAG] Removed {len(stale_txids)} stale transactions from queue for ineligible peer {state.conn_id}",
                                    extra={
                                        "peer": state.conn_id,
                                        "stale_count": len(stale_txids),
                                        "queue_remaining": len(state.inv_queue),
                                    }
                                )
                        continue
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
                    self._metrics["inv_sent"] += len(batch)
                    self._metrics["announced_count"] += len(batch)
                    self._metrics["last_announced_at"] = time.time()
                    for txid in batch:
                        state.known_txids.add(txid)
                        state.inv_queue_timestamps.pop(txid, None)
                        self._set_peer_tx_state(state.conn_id, txid, "ANNOUNCED_TO_PEER")
                        self._record_event(
                            "TX_RELAY_ANNOUNCE_SENT",
                            extra={
                                "peer": state.conn_id,
                                "hash": txid.hex(),
                                **self._peer_log_extra(state.conn_id),
                            },
                        )
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
                    entry = self._inflight.get(txid)
                    if entry is None:
                        continue
                    self._clear_inflight(txid)
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
                    sources = self._candidate_conn_ids(self._tx_sources.get(txid, set()), exclude=entry.conn_id)
                    ordered_sources = self._candidate_conn_ids(self._tx_sources_order.get(txid, []), exclude=entry.conn_id)
                    candidates = [
                        p
                        for p in ordered_sources
                        if p != entry.conn_id and self._peer_eligible(p)
                    ]
                    if not candidates:
                        candidates = [
                            p
                            for p in sources
                            if p != entry.conn_id and self._peer_eligible(p)
                        ]
                    if candidates and entry.attempts < self.inflight_max_retries:
                        next_peer = self._request_mgr.pick_peer(
                            txid, candidates=candidates
                        )
                        if next_peer is not None and self._request_mgr.can_request(
                            txid, now=now
                        ):
                            if self._set_inflight(
                                txid,
                                conn_id=next_peer,
                                peer_node_id=self._peer_state.get(
                                    next_peer, None
                                ).peer_node_id
                                if next_peer in self._peer_state
                                else None,
                                now=now,
                                attempts=entry.attempts + 1,
                            ):
                                self._request_mgr.mark_requested(
                                    txid, peer=next_peer, now=now
                                )
                                self._metrics["get_sent"] += 1
                                self._metrics["requested_count"] += 1
                                self._metrics["last_requested_at"] = time.time()
                                await self._send_tx_get(next_peer, [txid])
                                self._set_peer_tx_state(
                                    next_peer, txid, "REQUESTED_FROM_PEER", increment_attempt=True
                                )
                                self._record_event(
                                    "TX_RELAY_GET_SENT",
                                    extra={
                                        "peer": next_peer,
                                        "hash": txid.hex(),
                                        "retry": True,
                                        **self._peer_log_extra(next_peer),
                                    },
                                )
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
                        unavailable_entry = self._request_mgr.mark_unavailable(
                            txid,
                            peer=entry.conn_id,
                            reason="fetch_timeout",
                            now=now,
                            backoff_s=min(
                                300.0,
                                self.request_cooldown_s * (2 ** max(0, entry.attempts - 1)),
                            ),
                        )
                        self._metrics["dropped_count"] += 1
                        self._metrics["last_dropped_at"] = time.time()
                        self._set_peer_tx_state(
                            entry.conn_id, txid, "GAVE_UP", reason="fetch_timeout"
                        )
                        log.info(
                            "TX_FETCH_UNAVAILABLE",
                            extra={
                                "hash": txid.hex(),
                                "attempts": entry.attempts,
                                "cleared_from_peers": len(sources),
                                "unavailable_failures": unavailable_entry.unavailable_failures,
                                "retry_at": unavailable_entry.next_retry_at,
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
        last_missing_fetch = 0.0
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
                    self._metrics["mempool_sync_req_sent"] += 1
                    log.info(
                        "TX_SYNC_REQ",
                        extra={
                            "peer": state.conn_id,
                            "limit": self.mempool_sync_limit,
                            **self._peer_log_extra(state.conn_id),
                        },
                    )
                # Periodically request missing transactions that peers know about
                # but haven't been fetched yet (e.g., due to lost responses)
                if now - last_missing_fetch >= self.mempool_sync_interval_s:
                    last_missing_fetch = now
                    requested = await self.request_missing_known(limit=128, trigger="mempool_sync_loop")
                    if requested > 0:
                        log.info(
                            "TX_MISSING_FETCH",
                            extra={
                                "requested": requested,
                                "trigger": "mempool_sync_loop",
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

    async def mempool_watchdog_loop(self) -> None:
        """
        Aggressive watchdog that continuously monitors for missing transactions.
        This runs more frequently than mempool_sync_loop to ensure no transactions
        are missed. It actively fetches known transactions that haven't been retrieved yet.
        """
        self._running = True
        last_heartbeat = 0.0
        while self._running:
            try:
                await asyncio.sleep(self.mempool_watchdog_interval_s)
                now = time.time()
                
                # Request missing known transactions more aggressively
                requested = await self.request_missing_known(
                    limit=self.mempool_watchdog_limit, 
                    trigger="mempool_watchdog"
                )
                if requested > 0:
                    log.info(
                        "TX_WATCHDOG_FETCH",
                        extra={
                            "requested": requested,
                            "trigger": "watchdog",
                            "interval_s": self.mempool_watchdog_interval_s,
                        },
                    )
                
                if now - last_heartbeat >= 30.0:
                    last_heartbeat = now
                    async with self._lock:
                        peer_count = len(self._peer_state)
                        inflight_count = len(self._inflight)
                    log.info(
                        "TX_RELAY_HEARTBEAT",
                        extra={
                            "loop": "watchdog",
                            "peers": peer_count,
                            "inflight": inflight_count,
                            "interval_s": self.mempool_watchdog_interval_s,
                        },
                    )
            except Exception:
                log.warning("tx mempool watchdog loop error", exc_info=True)

    async def inv_queue_watchdog_loop(self) -> None:
        """Monitor for stuck INV queues that aren't being flushed."""
        self._running = True
        log.info("[DIAG] inv_queue_watchdog_loop STARTED")
        while self._running:
            try:
                await asyncio.sleep(5.0)
                now = time.time()
                async with self._lock:
                    for conn_id, state in self._peer_state.items():
                        queue_size = len(state.inv_queue)
                        if queue_size > 10:
                            eligible = self._peer_eligible(conn_id)
                            oldest_age = 0.0
                            if state.inv_queue and state.inv_queue_timestamps:
                                oldest_txid = state.inv_queue[0]
                                queued_at = state.inv_queue_timestamps.get(oldest_txid, now)
                                oldest_age = now - queued_at

                            log.warning(
                                f"[DIAG] INV queue for peer {conn_id} has {queue_size} items - may be stuck",
                                extra={
                                    "peer": conn_id,
                                    "queue_size": queue_size,
                                    "eligible": eligible,
                                    "oldest_age_seconds": oldest_age,
                                }
                            )
            except Exception:
                log.warning("inv queue watchdog loop error", exc_info=True)

    async def reconcile_loop(self) -> None:
        """
        Periodically reconcile mempool inventory with peers to prevent divergence.
        """
        self._running = True
        last_heartbeat = 0.0
        while self._running:
            try:
                await asyncio.sleep(self.reconcile_interval_s)
                self._metrics["reconcile_runs"] += 1
                self._metrics["last_reconcile_at"] = time.time()
                async with self._lock:
                    peers = [p.conn_id for p in self._peer_state.values() if self._peer_eligible(p.conn_id)]
                if not peers:
                    continue
                summary_txids = await self._list_mempool_hashes(self.reconcile_batch_size)
                for peer_id in peers:
                    await self._send_mempool_summary(peer_id, summary_txids)
                    self._metrics["mempool_summary_sent"] += 1
                recent = list(self._recent_txids)[-self.reconcile_batch_size :]
                if not recent:
                    requested = await self.request_missing_known(
                        limit=self.mempool_watchdog_limit, trigger="reconcile_loop"
                    )
                    if requested:
                        self._metrics["reconcile_missing_found"] += int(requested)
                    continue
                sample_peers = random.sample(peers, k=min(2, len(peers)))
                for peer_id in sample_peers:
                    await self._send_tx_inv(peer_id, recent)
                    for txid in recent:
                        self._set_peer_tx_state(peer_id, txid, "ANNOUNCED_TO_PEER")
                        self._record_event(
                            "TX_RELAY_RECONCILE_ANNOUNCE",
                            extra={
                                "peer": peer_id,
                                "hash": txid.hex(),
                                "batch_size": len(recent),
                                **self._peer_log_extra(peer_id),
                            },
                        )
                requested = await self.request_missing_known(
                    limit=self.mempool_watchdog_limit, trigger="reconcile_loop"
                )
                if requested:
                    self._metrics["reconcile_missing_found"] += int(requested)
                    self._record_event(
                        "TX_RELAY_RECONCILE_REQUEST",
                        extra={"requested": requested, "trigger": "reconcile_loop"},
                    )
                now = time.time()
                if now - last_heartbeat >= 30.0:
                    last_heartbeat = now
                    log.info(
                        "TX_RELAY_HEARTBEAT",
                        extra={"loop": "reconcile", "peers": len(peers)},
                    )
            except Exception:
                log.warning("tx reconcile loop error", exc_info=True)

    async def request_missing_known(
        self,
        limit: int = 128,
        trigger: str = "request_missing_known",
        force: bool = False,
        *,
        max_peers: int = 2,
        batch_size: int = 64,
        include_details: bool = False,
    ) -> int | Dict[str, Any]:
        if limit <= 0:
            return 0
        requests_by_peer: Dict[str, List[bytes]] = {}
        skip_reasons: Dict[str, str] = {}
        seen_txids: Set[bytes] = set()
        async with self._lock:
            peer_states = list(self._peer_state.values())
        eligible_states = [
            state
            for state in peer_states
            if self._peer_eligible(state.conn_id)
            and self._peer_penalty_until.get(state.conn_id, 0.0) <= time.time()
            and len(state.known_txids) > 0
        ]
        eligible_states.sort(key=lambda s: len(s.known_txids), reverse=True)
        if max_peers > 0:
            eligible_states = eligible_states[:max_peers]
        batch_size = max(1, min(int(batch_size), 256))
        now = time.time()
        remaining = int(limit)
        log.info(
            "TX_IMPORT_DISCOVERED",
            extra={
                "trigger": trigger,
                "limit": limit,
                "eligible_peers": len(eligible_states),
                "advertised_txids": sum(len(s.known_txids) for s in eligible_states),
            },
        )
        for state in eligible_states:
            if remaining <= 0:
                break
            candidates = state.known_txids.sample(limit=remaining)
            for txid in candidates:
                if remaining <= 0:
                    break
                if txid in seen_txids:
                    continue
                seen_txids.add(txid)
                inflight_entry = self._inflight.get(txid)
                if inflight_entry is not None:
                    if inflight_entry.deadline <= now:
                        self._clear_inflight(txid)
                    elif force:
                        # Manual/import-triggered recovery: clear active inflight so
                        # we can re-request immediately in case the original request
                        # got stuck or the response was lost.
                        self._clear_inflight(txid)
                    else:
                        skip_reasons[f"0x{txid.hex()}"] = "inflight_backoff"
                        continue
                if self._reject_recent(txid):
                    if force:
                        self._reject_cache.pop(txid, None)
                    else:
                        skip_reasons[f"0x{txid.hex()}"] = "recent_reject"
                        continue
                # Check if we actually have the transaction in mempool or chain
                has_tx = await self._has_tx(txid)
                if has_tx:
                    self._request_mgr.mark_accepted(txid, peer="local", now=now)
                    skip_reasons[f"0x{txid.hex()}"] = "terminal_ok"
                    continue
                if await self._has_chain_tx(txid):
                    self._request_mgr.mark_dropped(
                        txid, peer="chain", reason="in_chain", now=now
                    )
                    skip_reasons[f"0x{txid.hex()}"] = "in_chain"
                    continue
                # IMPORTANT: If transaction is marked as accepted but we don't actually have it,
                # clear the state so we can re-request it. This handles cases where:
                # - Transaction was evicted from mempool
                # - State became stale/inconsistent
                # - Mempool was cleared/reset
                # Note: At this point we know has_tx is False (checked above)
                req_state = self._request_mgr.get_state(txid)
                if req_state is not None and req_state.state == "accepted_in_mempool":
                    # Transaction marked as accepted but not in mempool - clear the state
                    self._request_mgr.clear_state(txid)
                    log.info(
                        "TX_STATE_CLEARED",
                        extra={
                            "hash": txid.hex(),
                            "reason": "marked_accepted_but_not_in_mempool",
                            "trigger": trigger,
                        },
                    )
                req_state = self._request_mgr.get_state(txid)
                if req_state is not None and req_state.state == "invalid_final":
                    log.info("TX_REQUEST_SUPPRESSED_TERMINAL_INVALID", extra={"hash": txid.hex(), "trigger": trigger, "peer": state.conn_id})
                    skip_reasons[f"0x{txid.hex()}"] = "terminal_invalid"
                    continue
                if req_state is not None and req_state.state in {"accepted_in_mempool", "mined", "confirmed"}:
                    skip_reasons[f"0x{txid.hex()}"] = "terminal_ok"
                    continue
                if not force and req_state is not None and req_state.state == "requested" and req_state.next_retry_at > now:
                    skip_reasons[f"0x{txid.hex()}"] = "requested_backoff"
                    continue
                if not force and self._request_mgr.should_skip_unavailable(txid, now=now):
                    skip_reasons[f"0x{txid.hex()}"] = "unavailable_backoff"
                    continue
                if not force and not self._request_mgr.can_request(txid, now=now):
                    skip_reasons[f"0x{txid.hex()}"] = "retry_backoff"
                    continue
                if not self._set_inflight(
                    txid,
                    conn_id=state.conn_id,
                    peer_node_id=state.peer_node_id,
                    now=now,
                    attempts=1,
                ):
                    skip_reasons[f"0x{txid.hex()}"] = "inflight_limit"
                    continue
                self._request_mgr.mark_requested(txid, peer=state.conn_id, now=now)
                self._record_source(txid, state.conn_id)
                self._set_peer_tx_state(
                    state.conn_id, txid, "REQUESTED_FROM_PEER", increment_attempt=True
                )
                requests_by_peer.setdefault(state.conn_id, []).append(txid)
                remaining -= 1
            if state.conn_id in requests_by_peer:
                log.info(
                    "TX_IMPORT_SELECTED",
                    extra={
                        "trigger": trigger,
                        "peer": state.conn_id,
                        "selected": len(requests_by_peer[state.conn_id]),
                    },
                )
        total = 0
        requested_txids: list[str] = []
        requested_peers: list[str] = []
        for conn_id, txids in requests_by_peer.items():
            requested_peers.append(conn_id)
            for idx in range(0, len(txids), batch_size):
                batch = txids[idx : idx + batch_size]
                self._metrics["get_sent"] += len(batch)
                self._metrics["requested_count"] += len(batch)
                self._metrics["last_requested_at"] = time.time()
                await self._send_tx_get(conn_id, batch)
                total += len(batch)
                requested_txids.extend(f"0x{txid.hex()}" for txid in batch)
                for txid in batch:
                    self._record_event(
                        "TX_RELAY_RECONCILE_GET_SENT",
                        extra={
                            "peer": conn_id,
                            "hash": txid.hex(),
                            "trigger": trigger,
                            **self._peer_log_extra(conn_id),
                        },
                    )
                log.info(
                    "TX_GET_SENT",
                    extra={
                        "peer": conn_id,
                        "count": len(batch),
                        "txids": [f"0x{txid.hex()}" for txid in batch],
                        "trigger": trigger,
                        **self._peer_log_extra(conn_id),
                    },
                )
        if include_details:
            return {
                "requested": total,
                "requested_txids": requested_txids,
                "requested_peers": requested_peers,
                "skip_reasons": skip_reasons,
                "eligible_peers": [s.conn_id for s in eligible_states],
                "eligible_peers_count": len(eligible_states),
                "batch_size": batch_size,
                "max_peers": max_peers,
            }
        return total

    def debug_tx_import(self, txid: bytes) -> dict[str, Any]:
        req = self._request_mgr.get_state(txid)
        inflight = self._inflight.get(txid)
        sources = list(self._tx_sources.get(txid, set()))
        ordered_sources = list(self._tx_sources_order.get(txid, []))
        peers: list[dict[str, Any]] = []
        seen: set[str] = set()
        for peer_ref in ordered_sources + sources:
            if peer_ref in seen:
                continue
            seen.add(peer_ref)
            conn_id = self._resolve_conn_id(peer_ref)
            pstate = self._peer_state.get(conn_id) if conn_id else None
            peers.append(
                {
                    "peer_ref": peer_ref,
                    "resolved_conn_id": conn_id,
                    "node_id": pstate.peer_node_id if pstate else None,
                    "active_conn": bool(conn_id and self._peer_eligible(conn_id)),
                    "knows_txid": bool(pstate and txid in pstate.known_txids),
                }
            )
        current_state = self.tx_state_for(txid)
        req_state_name = req.state if req is not None else None
        terminal_invalid = req_state_name in {"invalid_final"}
        terminal_success = req_state_name in {"accepted_in_mempool", "mined", "confirmed"}
        return {
            "txid": "0x" + txid.hex(),
            "state": current_state,
            "internal_records_for_txid": 1 if self._request_mgr.get_state(txid) is not None else 0,
            "bug_duplicate_records": False,
            "terminal": {
                "terminal_invalid": terminal_invalid,
                "terminal_success": terminal_success,
                "reason": req.last_reason if req is not None else None,
                "would_request": False if (terminal_invalid or terminal_success) else self._request_mgr.can_request(txid, now=time.time()),
            },
            "peers_advertised": peers,
            "inflight": {
                "active": inflight is not None,
                "conn_id": inflight.conn_id if inflight else None,
                "peer_node_id": inflight.peer_node_id if inflight else None,
                "requested_at": inflight.requested_at if inflight else None,
                "deadline": inflight.deadline if inflight else None,
            },
            "cooldown": {
                "in_cooldown": bool(req and req.next_retry_at > time.time()),
                "next_retry_at": req.next_retry_at if req else None,
                "last_reason": req.last_reason if req else None,
                "attempts": req.attempts if req else 0,
                "unavailable_failures": req.unavailable_failures if req else 0,
            },
            "peer_tx_state": self.tx_peer_state_for(txid),
        }

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
            "inflight_by_peer": dict(self._inflight_by_peer),
            "tx_state_counts": self._request_mgr.counts(),
            "tx_state_sample": self._request_mgr.snapshot(limit=10),
            "tx_store_count": len(self._tx_store),
            "inflight_timeout_s": self.inflight_timeout_s,
            "request_cooldown_s": self.request_cooldown_s,
            "invalid_tx_cooldown_s": self.invalid_tx_cooldown_s,
            "peer_penalties": {
                peer: until
                for peer, until in self._peer_penalty_until.items()
                if until > time.time()
            },
            "reconcile_interval_s": self.reconcile_interval_s,
            "peers": peers,
        }

    def metrics(self) -> Dict[str, Any]:
        inv_queue_depth = sum(len(state.inv_queue) for state in self._peer_state.values())
        metrics = dict(self._metrics)
        metrics.update(
            {
                "inflight": len(self._inflight),
                "inv_queue_depth": inv_queue_depth,
            }
        )
        return metrics

    def tx_state_snapshot(self, limit: int = 20) -> List[dict[str, Any]]:
        return self._request_mgr.snapshot(limit=limit)

    def tx_state_counts(self) -> Dict[str, int]:
        return self._request_mgr.counts()

    def tx_state_for(self, txid: bytes) -> Optional[dict[str, Any]]:
        entry = self._request_mgr.get_state(txid)
        base = None
        if entry is not None:
            base = {
                "txid": "0x" + txid.hex(),
                "state": entry.state,
                "last_peer": entry.last_peer,
                "last_peer_node_id": entry.last_peer_node_id,
                "last_peer_conn_id": entry.last_peer_conn_id,
                "last_reason": entry.last_reason,
                "attempts": entry.attempts,
                "first_seen_at": entry.first_seen_at,
                "last_updated_at": entry.last_updated_at,
                "requested_peers": len(entry.requested_peers),
                "has_bytes": entry.has_bytes,
                "validated_ok": entry.validated_ok,
                "validated_fail": entry.validated_fail,
                "terminal": entry.terminal,
                "retry_after": entry.next_retry_at if entry.next_retry_at > time.time() else None,
            }
        store = self._tx_store.get(txid)
        if store is None:
            return base
        payload = base or {"txid": "0x" + txid.hex()}
        payload.update(
            {
                "source": store.source,
                "arrival_time": store.arrival_time,
                "has_bytes": store.tx_bytes is not None,
                "has_canonical_bytes": store.canonical_bytes is not None,
                "validation_status": store.validation_status,
                "validation_reason": store.validation_reason,
                "mempool_status": store.mempool_status,
                "mempool_reason": store.mempool_reason,
                "last_peer": store.last_peer or (base or {}).get("last_peer"),
                "last_peer_node_id": store.last_peer_node_id or (base or {}).get("last_peer_node_id"),
                "last_peer_conn_id": store.last_peer_conn_id or (base or {}).get("last_peer_conn_id"),
            }
        )
        return payload

    def tx_peer_state_for(self, txid: bytes) -> list[dict[str, Any]]:
        states: list[dict[str, Any]] = []
        for (conn_id, t_id), entry in self._peer_tx_state.items():
            if t_id != txid:
                continue
            states.append(
                {
                    "peer": conn_id,
                    "state": entry.state,
                    "reason": entry.last_reason,
                    "attempts": entry.attempts,
                    "last_updated_at": entry.last_updated_at,
                }
            )
        return states

    async def request_mempool_sync(self, conn_id: str) -> None:
        state = self._ensure_peer(conn_id)
        state.last_sync_sent_at = time.time()
        await self._send_mempool_req(conn_id, self.mempool_sync_limit)
        self._metrics["mempool_sync_req_sent"] += 1
        log.info(
            "TX_SYNC_REQ",
            extra={
                "peer": conn_id,
                "limit": self.mempool_sync_limit,
                "trigger": "connect",
                **self._peer_log_extra(conn_id),
            },
        )

    async def sync_all_peers(self, timeout_s: float = 2.0) -> int:
        """
        Synchronize mempools from all connected peers.
        
        This method requests mempool snapshots from all eligible peers
        and waits for responses. It's used when building block templates
        to ensure the miner includes transactions from all network nodes.
        
        Args:
            timeout_s: Maximum time to wait for sync to complete
            
        Returns:
            Number of peers successfully synced
        """
        async with self._lock:
            peer_states = list(self._peer_state.values())
        
        synced_count = 0
        now = time.time()
        
        # Send mempool requests to all eligible peers
        for state in peer_states:
            if not self._peer_eligible(state.conn_id):
                continue
            
            try:
                state.last_sync_sent_at = now
                await self._send_mempool_req(state.conn_id, self.mempool_sync_limit)
                synced_count += 1
                log.info(
                    "TX_SYNC_REQ",
                    extra={
                        "peer": state.conn_id,
                        "limit": self.mempool_sync_limit,
                        "trigger": "block_template_build",
                        **self._peer_log_extra(state.conn_id),
                    },
                )
            except Exception as e:
                log.warning(
                    "Failed to sync mempool from peer",
                    extra={
                        "peer": state.conn_id,
                        "error": str(e),
                    },
                )
        
        # Wait briefly for responses to arrive and be processed
        if synced_count > 0 and timeout_s > 0:
            await asyncio.sleep(min(timeout_s, 2.0))
        
        return synced_count
