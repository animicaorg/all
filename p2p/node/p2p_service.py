from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import logging
import os
import random
import socket
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import MISSING, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Deque, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse
from urllib.request import urlopen
from p2p import version as p2p_version
from core.config import DEFAULT_DB_FILENAME
from p2p.crypto import keys as keys_mod
from p2p.crypto import peer_id as peer_id_mod
from p2p.peer import peerstore as pstore
from p2p.peer.peer_addr import PeerAddrParseResult, normalize_peer_addr
from p2p.peer.p2p_store import (
    apply_umask_from_env,
    ensure_writable,
    merge_peer_files,
    read_peers_json,
)
from p2p.transport.base import HandshakeError, ListenConfig
from p2p.constants import DEFAULT_TCP_PORT, MAX_INV_PER_MSG, MAX_TX_BYTES
from p2p.transport.multiaddr import parse_multiaddr
from p2p.transport.tcp import TcpTransport
from p2p.wire.encoding import decode_payload, encode_payload
from p2p.wire.frames import Framer, unpack_frame
from p2p.wire.message_ids import MsgID
from rpc.instant_tx import get_instant_tx_service_singleton, instant_enabled
from core.utils.time import maybe_normalize_unix_timestamp_seconds
from p2p.messages_tx import (
    TxData,
    TxGet,
    TxInv,
    TxMempoolReq,
    TxMempoolResp,
    TxMempoolSummary,
    TxNotFound,
    parse_tx_data_items,
    parse_txids,
)
from p2p.txrelay import TxRelayService
from p2p.metrics import (
    inc_caps_failure,
    inc_dial_attempt,
    inc_dial_success,
    inc_disconnect,
    inc_handshake_failure,
)
from p2p.wire.messages import (
    AddressAnnounce,
    Blocks,
    Error,
    GetBlocks,
    GetData,
    GetHeaders,
    GetPeers,
    HeaderCompact,
    Headers,
    Hello,
    HelloAck,
    Inv,
    InvItem,
    InvType,
    Peers,
    Tx,
)
try:
    from p2p.protocol import block_announce as proto_blk
except Exception:  # pragma: no cover - optional
    proto_blk = None
from p2p.node.peer_registry import PeerRegistry
from p2p.sync.cache_store import SyncCacheConfig, SyncCacheState, SyncCacheStore

log = logging.getLogger("animica.p2p.service")

# Sync performance tuning constants
MIN_SYNC_TICK_SEC: float = 0.005  # Minimum normal sync tick interval (5ms).
MIN_SYNC_BOOST_TICK_SEC: float = 0.001  # Boost must be faster than normal sync, not slower.

DEFAULT_BOOTSTRAP_SEEDS = [
    "/dns4/mainnet.animica.org/tcp/30333",
    "/ip4/144.126.133.21/tcp/30333",
]
FORCE_SYNC_HEADER_PEERS = {
    "144.126.133.21:30333",
    "mainnet.animica.org:30333",
}
HTTPS_SEED_TCP_UPGRADE_HOSTS = {
    "144.126.133.21",
    "mainnet.animica.org",
}
DEFAULT_VERIFIER_SEED_NODES = "3.12.224.189,144.126.133.21,mainnet.animica.org"

# Verifier seed mining constraint: allow peers/miners to be ahead of verifiers
# by a small, configurable window to absorb propagation jitter.
DEFAULT_MAX_HEIGHT_AHEAD_OF_VERIFIER = 3


def _env_value(*keys: str, default: Optional[str] = None) -> Optional[str]:
    for key in keys:
        val = os.environ.get(key)
        if val is not None and str(val).strip() != "":
            return val
    return default


def _env_flag(*keys: str, default: bool = False) -> bool:
    raw = _env_value(*keys)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except Exception:
        return False


@dataclass(slots=True)
class PeerBroadcastScore:
    """Track peer broadcast usefulness for sync selection.

    Non-broadcasting peer definition:
      - connected for > threshold,
      - no recent inventory/head announcements,
      - and no successful header/block progress delivery.
    """

    last_inventory_at: float = 0.0
    last_head_advancement_at: float = 0.0
    duplicate_header_batches: int = 0
    successful_headers_served: int = 0
    successful_blocks_served: int = 0
    timeouts: int = 0
    score: int = 0
    last_block_time: float = 0.0
    blocks_served: int = 0
    errors: int = 0
    tip_matches: int = 0
    non_broadcasting_since: Optional[float] = None
    last_classification: Optional[str] = None


STALL_BLOCK_TIMEOUT = "STALL_BLOCK_TIMEOUT"
STALL_BLOCK_PEER_UNRESPONSIVE = "STALL_BLOCK_PEER_UNRESPONSIVE"
STALL_BLOCK_NOT_FOUND_ACROSS_PEERS = "STALL_BLOCK_NOT_FOUND_ACROSS_PEERS"
STALL_BLOCK_INVALID_RESPONSE = "STALL_BLOCK_INVALID_RESPONSE"
STALL_BLOCK_NOT_ADVANCING = "STALL_BLOCK_NOT_ADVANCING"
STALL_HEADERS_EMPTY_LOOP = "STALL_HEADERS_EMPTY_LOOP"
STALL_CACHE_LOOP = "STALL_CACHE_LOOP"
STALL_CACHE_SHORT_CIRCUIT = "STALL_CACHE_SHORT_CIRCUIT"
STALL_VERIFY_BACKPRESSURE = "STALL_VERIFY_BACKPRESSURE"


def _max_reorg_depth() -> int:
    """Reorg-depth bound shared with the importer (core.chain.block_import
    DEFAULT_MAX_REORG_DEPTH reads the same env, and accepts 0 = never reorg).
    Used to bound how far below the local head a fork-sibling header is still
    worth ingesting; 0 disables sibling ingest entirely to match the importer."""
    try:
        return max(0, int(os.environ.get("ANIMICA_MAX_REORG_DEPTH", "96")))
    except (TypeError, ValueError):
        return 96


@dataclass(slots=True)
class _PeerState:
    session_id: str
    remote: str
    direction: str  # "inbound" | "outbound"
    conn: Any
    stream: Any
    framer: Framer
    write_lock: asyncio.Lock
    conn_trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    peer_id: Optional[str] = None  # hex string
    hello: Optional[dict] = None
    hello_done: asyncio.Event = field(default_factory=asyncio.Event)
    pending_headers: Optional[asyncio.Future] = None
    pending_header_request_id: Optional[str] = None
    pending_snapshot_list: Optional[asyncio.Future] = None
    pending_snapshot_chunk: Optional[asyncio.Future] = None
    ready_for_sync: bool = False
    connected_at: float = field(default_factory=time.time)
    feeler: bool = False
    known_addrs: "OrderedDict[str, float]" = field(default_factory=OrderedDict)
    misbehavior_score: int = 0
    invalid_headers: int = 0
    invalid_blocks: int = 0
    invalid_msgs: int = 0
    timeouts: int = 0
    score: int = 0
    last_block_time: float = 0.0
    blocks_served: int = 0
    notfound: int = 0
    missing_parent: int = 0
    stall_events: int = 0
    empty_header_responses: int = 0
    header_cooldown_until: float = 0.0
    not_anchored_count: int = 0
    last_not_anchored_at: float = 0.0
    anchored: bool = False
    anchor_reason: Optional[str] = None
    last_anchor_at: float = 0.0
    sync_successes: int = 0
    sync_timeouts: int = 0
    sync_failures: int = 0
    block_successes: int = 0
    block_failures: int = 0
    block_cooldown_until: float = 0.0
    last_block_failure_at: float = 0.0
    last_block_success_at: float = 0.0
    last_msg_at: float = field(default_factory=time.time)
    last_progress_at: float = field(default_factory=time.time)
    ban_until: Optional[float] = None
    latency_ewma: Optional[float] = None
    netgroup: Optional[str] = None
    last_header_request_at: Optional[float] = None
    last_block_request_at: Optional[float] = None
    repo_state_ok: bool = True
    last_tx_inv_sent_at: Optional[float] = None
    last_tx_inv_recv_at: Optional[float] = None
    last_tx_data_sent_at: Optional[float] = None
    last_tx_data_recv_at: Optional[float] = None
    broadcast: PeerBroadcastScore = field(default_factory=PeerBroadcastScore)
    negotiated_caps: set[str] = field(default_factory=set)
    accepts_inbound: Optional[bool] = None
    # ANM-H03/H04 per-peer inbound message-rate limiter state
    rl_tokens: float = 0.0
    rl_last_mono: float = 0.0
    rl_primed: bool = False
    rl_strikes: int = 0
    rl_last_warn_s: float = 0.0


class _NullTxRelay:
    def __init__(self) -> None:
        self._running = False

    def snapshot(self) -> dict[str, Any]:
        return {}

    def register_peer(
        self,
        conn_id: str,
        *,
        peer_node_id: Optional[str] = None,
        direction: Optional[str] = None,
        remote: Optional[str] = None,
    ) -> None:
        return None

    def unregister_peer(self, conn_id: str) -> None:
        return None

    async def request_mempool_sync(self, conn_id: str) -> int:
        return 0

    async def on_tx_inv(self, conn_id: str, txids: list[bytes]) -> None:
        return None

    async def on_tx_get(self, conn_id: str, txids: list[bytes]) -> None:
        return None

    async def on_tx_data(self, conn_id: str, items: list[dict[str, Any]]) -> None:
        return None

    async def on_tx_notfound(self, conn_id: str, txids: list[bytes]) -> None:
        return None

    async def on_mempool_req(self, conn_id: str, limit: int = 0) -> None:
        return None

    async def on_mempool_resp(self, conn_id: str, txids: list[bytes]) -> None:
        return None

    async def on_mempool_add(self, txid: bytes, raw: bytes) -> None:
        return None

    async def request_missing_known(self, limit: int = 0) -> int:
        return 0

    async def sync_all_peers(self, timeout_s: float = 0.0) -> int:
        return 0

    async def announce_txids(
        self, txids: list[bytes], *, exclude_peer: Optional[str] = None
    ) -> None:
        return None

    async def inv_flush_loop(self) -> None:
        return None

    async def inflight_timeout_loop(self) -> None:
        return None

    async def mempool_sync_loop(self) -> None:
        return None

    async def mempool_watchdog_loop(self) -> None:
        return None

    async def reconcile_loop(self) -> None:
        return None


class PeerMisbehavior(Exception):
    def __init__(
        self,
        reason: str,
        *,
        points: Optional[int] = None,
        ban_ttl: Optional[float] = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.points = points
        self.ban_ttl = ban_ttl


@dataclass(slots=True)
class _AddrRecord:
    address: str
    last_seen: float
    last_success: Optional[float] = None
    last_failure: Optional[float] = None
    failure_reason: Optional[str] = None
    failures: int = 0
    score: float = 0.0
    penalty_score: float = 0.0
    source: str = "unknown"

    def touch_seen(self, now: float) -> None:
        self.last_seen = now

    def mark_success(self, now: float) -> None:
        self.last_success = now
        self.failures = 0
        self.score = min(self.score + 1.0, 100.0)

    def mark_failure(self, now: float, reason: Optional[str] = None) -> None:
        self.last_failure = now
        if reason:
            self.failure_reason = reason
        self.failures += 1
        self.score = max(self.score - 0.5, -10.0)
        self.penalty_score = min(self.penalty_score + 1.0, 100.0)


class _AddrMan:
    def __init__(self) -> None:
        self._records: dict[str, _AddrRecord] = {}

    def add(
        self,
        address: str,
        *,
        now: Optional[float] = None,
        source: Optional[str] = None,
        last_seen: Optional[float] = None,
        last_success: Optional[float] = None,
        last_failure: Optional[float] = None,
        failure_reason: Optional[str] = None,
        score: Optional[float] = None,
    ) -> None:
        now = time.time() if now is None else now
        rec = self._records.get(address)
        if rec:
            rec.touch_seen(last_seen or now)
            if last_success:
                rec.last_success = last_success
            if last_failure:
                rec.last_failure = last_failure
            if failure_reason:
                rec.failure_reason = failure_reason
            if score is not None:
                rec.score = max(rec.score, float(score))
            if source:
                rec.source = source
            return
        rec = _AddrRecord(address=address, last_seen=last_seen or now)
        if last_success:
            rec.last_success = last_success
        if last_failure:
            rec.last_failure = last_failure
        if failure_reason:
            rec.failure_reason = failure_reason
        if score is not None:
            rec.score = float(score)
        if source:
            rec.source = source
        self._records[address] = rec

    def mark_success(self, address: str) -> None:
        rec = self._records.get(address)
        now = time.time()
        if rec is None:
            rec = _AddrRecord(address=address, last_seen=now)
            self._records[address] = rec
        rec.mark_success(now)

    def mark_failure(self, address: str, *, reason: Optional[str] = None) -> None:
        rec = self._records.get(address)
        now = time.time()
        if rec is None:
            rec = _AddrRecord(address=address, last_seen=now)
            self._records[address] = rec
        rec.mark_failure(now, reason=reason)

    def size(self) -> int:
        return len(self._records)

    def sample(self, *, limit: int, exclude: Optional[set[str]] = None) -> list[str]:
        exclude = exclude or set()
        candidates = [
            rec for rec in self._records.values() if rec.address not in exclude
        ]
        if not candidates:
            return []
        candidates.sort(key=lambda r: (r.score, r.last_seen), reverse=True)
        pool = candidates[: max(limit * 3, limit)]
        random.shuffle(pool)
        return [rec.address for rec in pool[:limit]]

    def records(self) -> list[_AddrRecord]:
        return list(self._records.values())


@dataclass(slots=True)
class P2PStatusSnapshot:
    p2p_running: bool
    listen_addrs: list[str]
    bound_listen_addrs: list[str]
    peers_total: int
    peers_inbound: int
    peers_outbound: int
    bootstrap_attempts_last_5m: int
    last_peer_connect_at: Optional[float]
    last_peer_disconnect_at: Optional[float]
    seed_sources: dict[str, list[str]]
    dial_queue_depth: int
    addrman_size: Optional[int]
    dial_attempts: int
    dial_successes: int
    dial_attempt_history: list[dict[str, Any]]
    learned_addrs_1m: int
    announced_addrs_1m: int
    persisted_peer_count: Optional[int]
    seed_list: list[str]
    outbound_dialing_enabled: bool
    outbound_target: int
    caps_config: dict[str, Any]
    dial_last_error: Optional[dict[str, Any]] = None
    bootstrap_last_attempt: Optional[dict[str, Any]] = None
    bootstrap_last_success: Optional[dict[str, Any]] = None
    bootstrap_last_error: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "p2p_running": self.p2p_running,
            "listen_addrs": list(self.listen_addrs),
            "bound_listen_addrs": list(self.bound_listen_addrs),
            "peers_total": self.peers_total,
            "peers_inbound": self.peers_inbound,
            "peers_outbound": self.peers_outbound,
            "bootstrap_attempts_last_5m": self.bootstrap_attempts_last_5m,
            "last_peer_connect_at": self.last_peer_connect_at,
            "last_peer_disconnect_at": self.last_peer_disconnect_at,
            "seed_sources": self.seed_sources,
            "dial_queue_depth": self.dial_queue_depth,
            "addrman_size": self.addrman_size,
            "dial_attempts": self.dial_attempts,
            "dial_successes": self.dial_successes,
            "dial_attempt_history": list(self.dial_attempt_history),
            "learned_addrs_1m": self.learned_addrs_1m,
            "announced_addrs_1m": self.announced_addrs_1m,
            "persisted_peer_count": self.persisted_peer_count,
            "seed_list": list(self.seed_list),
            "outbound_dialing_enabled": self.outbound_dialing_enabled,
            "outbound_target": self.outbound_target,
            "caps_config": dict(self.caps_config),
            "dial_last_error": self.dial_last_error,
            "bootstrap_last_attempt": self.bootstrap_last_attempt,
            "bootstrap_last_success": self.bootstrap_last_success,
            "bootstrap_last_error": self.bootstrap_last_error,
        }


@dataclass(slots=True)
class _SyncHeader:
    hash: bytes
    parent_hash: bytes
    height: int
    theta_micro: int
    timestamp: int


@dataclass(slots=True)
class _SyncBlock:
    block: Any
    hash: bytes
    parent_hash: bytes
    origin_peer: Optional[str] = None
    received_at: float = 0.0


@dataclass(slots=True)
class _SyncVerifyTask:
    sync_block: _SyncBlock
    peer_remote: str
    enqueued_at: float
    raw_bytes_len: int = 0


@dataclass(slots=True)
class _SyncRequest:
    request_id: str
    peer_id: str
    kind: str
    started_at: float
    deadline_at: float
    retry_count: int = 0
    item_hash: Optional[bytes] = None
    start_height: Optional[int] = None
    count: Optional[int] = None
    locator: Optional[list[bytes]] = None
    locator_mode: Optional[str] = None
    anchor_height: Optional[int] = None
    anchor_hash: Optional[bytes] = None


@dataclass(slots=True)
class _PeerHeadInfo:
    height: int
    updated_at: float
    source: str
    head_hash: Optional[bytes] = None
    total_work: Optional[int] = None
    cooldown_until: float = 0.0
    last_error: Optional[str] = None


@dataclass(slots=True)
class _SyncTargetTip:
    height: int
    hash: bytes
    peer_id: str
    last_seen_ts: float
    total_work: Optional[int] = None
    timestamp: Optional[int] = None
    peer_score: Optional[float] = None


@dataclass(slots=True)
class _SyncStatusTruth:
    target_height: Optional[int]
    target_height_source: Optional[str]
    network_best_height: Optional[int]
    observed_network_height: Optional[int]
    synchronized: bool
    at_tip: bool
    phase: str
    phase_reason: str
    useful_header_peer: Optional[str]
    useful_block_peer: Optional[str]


@dataclass(slots=True)
class SyncStatusSnapshot:
    phase: str
    head_height: int
    head_hash: Optional[str]
    best_header_height: int
    best_header_hash: Optional[str]
    best_block_height: int
    best_block_hash: Optional[str]
    network_best_height: Optional[int]
    in_flight: int
    in_flight_headers: int
    in_flight_blocks: int
    queued_blocks_count: int
    last_progress_at: float
    last_head_height: int
    last_head_hash: Optional[str]
    last_header_height: int
    last_block_fetch_height: int
    last_header_progress_at: float
    last_block_progress_at: float
    last_header_at: float
    last_block_at: float
    last_header_request_at: float
    last_header_response_at: float
    last_header_response_count: int
    last_headers_accepted_count: int
    last_headers_discarded_count: int
    last_headers_discard_reason_counts: Dict[str, int]
    headers_accepted_total: int
    headers_seen_total: int
    last_block_request_at: float
    last_block_response_at: float
    last_block_download_at: float
    last_header_request_peer: Optional[str]
    last_header_response_peer: Optional[str]
    last_header_error: Optional[str]
    last_header_error_at: Optional[float]
    last_block_error: Optional[str]
    fatal_error: Optional[str]
    active_peer_for_headers: Optional[str]
    active_peer_for_blocks: Optional[str]
    active_peers_for_headers: list[str]
    active_peers_for_blocks: list[str]
    eligible_peers_for_headers: list[str]
    ineligible_peers_for_headers: Dict[str, str]
    eligible_peers_for_blocks: list[str]
    ineligible_peers_for_blocks: Dict[str, str]
    pending_header_batches: int
    header_cooldown_count: int
    header_cooldown_next_expiry: Optional[float]
    block_cooldown_count: int
    block_cooldown_next_expiry: Optional[float]
    recovery_attempts: int
    last_recovery_action: Optional[str]
    last_recovery_at: Optional[float]
    recovery_reason: Optional[str]
    last_locator_summary: Optional[dict[str, Any]]
    sync_head_height: Optional[int]
    sync_head_hash: Optional[str]
    last_matched_ancestor_height: Optional[int]
    last_matched_ancestor_hash: Optional[str]
    last_anchor_check: Optional[dict[str, Any]]
    checkpoint_height: Optional[int]
    checkpoint_hash: Optional[str]
    checkpoint_mode_enabled: bool
    checkpoint_validation: Optional[str]
    last_checkpoint_action: Optional[str]
    synchronized: bool
    at_tip: bool
    paused: bool
    sync_enabled: bool
    target_height: Optional[int]
    target_height_source: Optional[str]
    observed_network_height: Optional[int]
    peers_total: int
    cache_size_bytes: int
    cache_entries: int
    peer_penalties: Dict[str, int]
    last_block_error_peer: Optional[str]
    block_error_summary: Dict[str, dict[str, Any]]
    block_peer_failures: Dict[str, int]
    recent_block_recovery_peers: list[str]
    next_block_needed_height: Optional[int]
    next_block_needed_hash: Optional[str]
    next_block_attempt_peers: list[str]
    verify_queue_depth: int
    stall_timeout_s: float
    stall_reason: Optional[str]
    stall_elapsed_s: float
    status_reason: str
    useful_peer_for_headers: Optional[str]
    useful_peer_for_blocks: Optional[str]
    peer_anchor_states: Dict[str, dict[str, Any]]
    snapshot_auto_enabled: bool
    snapshot_last_attempt_at: float
    snapshot_last_success_at: float
    snapshot_last_error: Optional[str]
    snapshot_cooldown_remaining_s: float
    snapshot_last_manifest_height: Optional[int]
    snapshot_last_manifest_hash: Optional[str]
    snapshot_last_manifest_url: Optional[str]
    cache_interval_ms: int
    cache_age_ms: int
    cache_hits: int
    cache_misses: int
    cache_refreshes: int
    cache_last_refresh_at: float
    cache_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "head_height": self.head_height,
            "head_hash": self.head_hash,
            "best_header_height": self.best_header_height,
            "best_header_hash": self.best_header_hash,
            "best_block_height": self.best_block_height,
            "best_block_hash": self.best_block_hash,
            "network_best_height": self.network_best_height,
            "in_flight": self.in_flight,
            "in_flight_headers": self.in_flight_headers,
            "in_flight_blocks": self.in_flight_blocks,
            "queued_blocks_count": self.queued_blocks_count,
            "last_progress_at": self.last_progress_at,
            "last_head_height": self.last_head_height,
            "last_head_hash": self.last_head_hash,
            "last_header_height": self.last_header_height,
            "last_block_fetch_height": self.last_block_fetch_height,
            "last_header_progress_at": self.last_header_progress_at,
            "last_block_progress_at": self.last_block_progress_at,
            "last_header_at": self.last_header_at,
            "last_block_at": self.last_block_at,
            "last_header_request_at": self.last_header_request_at,
            "last_header_response_at": self.last_header_response_at,
            "last_header_response_count": self.last_header_response_count,
            "last_headers_accepted_count": self.last_headers_accepted_count,
            "last_headers_discarded_count": self.last_headers_discarded_count,
            "last_headers_discard_reason_counts": dict(
                self.last_headers_discard_reason_counts
            ),
            "headers_accepted_total": self.headers_accepted_total,
            "headers_seen_total": self.headers_seen_total,
            "last_block_request_at": self.last_block_request_at,
            "last_block_response_at": self.last_block_response_at,
            "last_block_download_at": self.last_block_download_at,
            "last_header_request_peer": self.last_header_request_peer,
            "last_header_response_peer": self.last_header_response_peer,
            "last_header_error": self.last_header_error,
            "last_header_error_at": self.last_header_error_at,
            "last_block_error": self.last_block_error,
            "fatal_error": self.fatal_error,
            "active_peer_for_headers": self.active_peer_for_headers,
            "active_peer_for_blocks": self.active_peer_for_blocks,
            "active_peers_for_headers": list(self.active_peers_for_headers),
            "active_peers_for_blocks": list(self.active_peers_for_blocks),
            "eligible_peers_for_headers": list(self.eligible_peers_for_headers),
            "ineligible_peers_for_headers": dict(self.ineligible_peers_for_headers),
            "eligible_peers_for_blocks": list(self.eligible_peers_for_blocks),
            "ineligible_peers_for_blocks": dict(self.ineligible_peers_for_blocks),
            "pending_header_batches": self.pending_header_batches,
            "header_cooldown_count": self.header_cooldown_count,
            "header_cooldown_next_expiry": self.header_cooldown_next_expiry,
            "block_cooldown_count": self.block_cooldown_count,
            "block_cooldown_next_expiry": self.block_cooldown_next_expiry,
            "recovery_attempts": self.recovery_attempts,
            "last_recovery_action": self.last_recovery_action,
            "last_recovery_at": self.last_recovery_at,
            "recovery_reason": self.recovery_reason,
            "last_locator_summary": dict(self.last_locator_summary)
            if isinstance(self.last_locator_summary, dict)
            else self.last_locator_summary,
            "sync_head_height": self.sync_head_height,
            "sync_head_hash": self.sync_head_hash,
            "last_matched_ancestor_height": self.last_matched_ancestor_height,
            "last_matched_ancestor_hash": self.last_matched_ancestor_hash,
            "last_anchor_check": dict(self.last_anchor_check)
            if isinstance(self.last_anchor_check, dict)
            else self.last_anchor_check,
            "checkpoint_height": self.checkpoint_height,
            "checkpoint_hash": self.checkpoint_hash,
            "checkpoint_mode_enabled": self.checkpoint_mode_enabled,
            "checkpoint_validation": self.checkpoint_validation,
            "last_checkpoint_action": self.last_checkpoint_action,
            "synchronized": self.synchronized,
            "at_tip": self.at_tip,
            "paused": self.paused,
            "sync_enabled": self.sync_enabled,
            "target_height": self.target_height,
            "target_height_source": self.target_height_source,
            "observed_network_height": self.observed_network_height,
            "peers_total": self.peers_total,
            "cache_size_bytes": self.cache_size_bytes,
            "cache_entries": self.cache_entries,
            "peer_penalties": dict(self.peer_penalties),
            "last_block_error_peer": self.last_block_error_peer,
            "block_error_summary": dict(self.block_error_summary),
            "block_peer_failures": dict(self.block_peer_failures),
            "recent_block_recovery_peers": list(self.recent_block_recovery_peers),
            "next_block_needed_height": self.next_block_needed_height,
            "next_block_needed_hash": self.next_block_needed_hash,
            "next_block_attempt_peers": list(self.next_block_attempt_peers),
            "verify_queue_depth": self.verify_queue_depth,
            "stall_timeout_s": self.stall_timeout_s,
            "stall_reason": self.stall_reason,
            "stall_elapsed_s": self.stall_elapsed_s,
            "status_reason": self.status_reason,
            "useful_peer_for_headers": self.useful_peer_for_headers,
            "useful_peer_for_blocks": self.useful_peer_for_blocks,
            "peer_anchor_states": dict(self.peer_anchor_states),
            "snapshot_auto_enabled": self.snapshot_auto_enabled,
            "snapshot_last_attempt_at": self.snapshot_last_attempt_at,
            "snapshot_last_success_at": self.snapshot_last_success_at,
            "snapshot_last_error": self.snapshot_last_error,
            "snapshot_cooldown_remaining_s": self.snapshot_cooldown_remaining_s,
            "snapshot_last_manifest_height": self.snapshot_last_manifest_height,
            "snapshot_last_manifest_hash": self.snapshot_last_manifest_hash,
            "snapshot_last_manifest_url": self.snapshot_last_manifest_url,
            "cache_interval_ms": self.cache_interval_ms,
            "cache_age_ms": self.cache_age_ms,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_refreshes": self.cache_refreshes,
            "cache_last_refresh_at": self.cache_last_refresh_at,
            "cache_source": self.cache_source,
        }


class P2PService:
    """
    Production P2P service: inv/getdata gossip + P2P-first sync.

    This service is used by the RPC process. It does not require a "trusted RPC"
    upstream: it syncs from peers by default and only uses local core DBs for
    validation/import.
    """

    def __init__(
        self,
        *,
        listen_addrs: list[str] | None = None,
        seeds: list[str] | None = None,
        chain_id: int = 0,
        enable_quic: bool = False,
        enable_ws: bool = False,
        nat: bool = False,
        deps: Any = None,
        peerstore_path: str | None = None,
    ) -> None:
        # Parameters kept for backward compatibility; TCP-only transport is used
        # by default in this service implementation.
        _ = (enable_quic, enable_ws, nat)
        apply_umask_from_env()
        self._log = logging.getLogger("animica.p2p")

        self.listen_addrs = listen_addrs or ["/ip4/0.0.0.0/tcp/30333"]
        self._configured_seeds = list(seeds or [])
        merged_seeds = list(self._configured_seeds)
        disable_default_seeds = os.environ.get(
            "ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", ""
        ).lower() in ("1", "true", "yes", "on")
        if not disable_default_seeds:
            for addr in DEFAULT_BOOTSTRAP_SEEDS:
                if addr not in merged_seeds:
                    merged_seeds.append(addr)
        self.chain_id = int(chain_id)
        self.deps = deps
        self._allow_ws_addrs = False
        self._allow_quic_addrs = False
        self.seeds = []
        for addr in merged_seeds:
            normalized = self._normalize_seed(addr)
            if normalized and normalized not in self.seeds:
                self.seeds.append(normalized)
        self._seed_sources = self._build_seed_sources(self._configured_seeds)
        self._seed_keys = {self._addr_key(s).strip().lower() for s in self.seeds}
        trusted_seed_env = os.environ.get("ANIMICA_P2P_TRUSTED_SEEDS", "")
        trusted_from_env = [x.strip() for x in trusted_seed_env.split(",") if x.strip()]
        trusted_from_p2p_env = [x.strip() for x in os.environ.get("ANIMICA_P2P_SEEDS", "").split(",") if x.strip()]
        trusted_candidates = ["rpc.animica.org:30303", *trusted_from_env, *trusted_from_p2p_env]
        self._trusted_seeds: list[str] = []
        for addr in trusted_candidates:
            normalized = self._normalize_seed(addr)
            if normalized and normalized not in self._trusted_seeds:
                self._trusted_seeds.append(normalized)
        self._trusted_seed_keys = {self._addr_key(a).strip().lower() for a in self._trusted_seeds}
        self._trusted_seed_hosts = self._seed_hostnames(self._trusted_seeds)
        self._sync_drop_slow_peers = _env_flag("ANIMICA_SYNC_DROP_SLOW_PEERS", default=True)

        # Resolve peerstore path (prefer chain-specific data dir)
        if peerstore_path is None:
            env_peerstore = os.environ.get("ANIMICA_PEER_STORE_PATH") or os.environ.get(
                "ANIMICA_P2P_DATA_DIR"
            )
            if env_peerstore:
                peerstore_path = os.path.expanduser(env_peerstore)
            else:
                base_dir = Path(os.environ.get("ANIMICA_DATA_DIR") or "~/.animica").expanduser()
                peerstore_path = base_dir / f"chain-{self.chain_id}" / "p2p"

        peerstore_path = Path(peerstore_path).expanduser()
        requested_peerstore_path = peerstore_path
        writable_peerstore = ensure_writable(peerstore_path)
        peerstore_path = writable_peerstore.path
        if writable_peerstore.used_fallback:
            self._log.warning(
                "P2P peerstore path not writable; using fallback",
                extra={
                    "requested": str(requested_peerstore_path),
                    "effective": str(peerstore_path),
                },
            )
        peerstore_dir = (
            peerstore_path if not peerstore_path.suffix else peerstore_path.parent
        )
        self._chain_data_dir = (
            peerstore_dir.parent if peerstore_dir.name == "p2p" else peerstore_dir
        )
        self._peerstore_dir = peerstore_dir
        self._peers_json_path = peerstore_dir / "peers.json"
        self._peerstore_fallback_path = writable_peerstore.fallback_path

        # Identity + stable peer id (co-locate with peerstore by default)
        identity_path = os.environ.get("ANIMICA_P2P_IDENTITY_PATH")
        if not identity_path:
            identity_path = peerstore_dir / "identity.json"
        identity_path = Path(identity_path).expanduser()
        requested_identity_path = identity_path
        writable_identity = ensure_writable(identity_path)
        identity_path = writable_identity.path
        if writable_identity.used_fallback:
            self._log.warning(
                "P2P identity path not writable; using fallback",
                extra={
                    "requested": str(requested_identity_path),
                    "effective": str(identity_path),
                },
            )
        self._ensure_peerstore_dir(identity_path.parent)

        passphrase = os.environ.get("ANIMICA_P2P_KEY_PASSPHRASE", "")
        try:
            self._identity = keys_mod.load_or_create(identity_path, passphrase)
            self._peer_id_bytes = bytes(
                peer_id_mod.peer_id_from_identity(self._identity)
            )
        except Exception as e:  # pragma: no cover - depends on pq backend availability
            # Minimal environments (CI without pq keygen) may not support identity generation.
            # Fall back to an ephemeral, process-local peer id so P2P can still run.
            log.warning(
                "P2P identity unavailable; using ephemeral peer_id",
                extra={"err": str(e)},
            )
            self._identity = None
            self._peer_id_bytes = hashlib.sha3_256(os.urandom(32)).digest()

        log.info(
            "P2P chain identity",
            extra={
                "chain_id": self.chain_id,
                "genesis_hash": self._genesis_hash().hex(),
                "fork_id": self._fork_id(),
                "consensus_id": self._consensus_id(),
                "protocol_version": self._protocol_version(),
            },
        )
        self._repo_state = p2p_version.git_describe(default="").strip()
        self._require_repo_state_match = _env_flag(
            "ANIMICA_P2P_REQUIRE_REPO_STATE",
            "ANIMICA_P2P_STRICT_REPO_STATE",
            default=False,
        )

        # Persistent peerstore
        self._ensure_peerstore_dir(peerstore_dir)
        fallback_json = (
            self._peerstore_fallback_path / "peers.json"
            if self._peerstore_fallback_path
            else None
        )
        self.peerstore = pstore.open_peerstore(peerstore_path, json_fallback=fallback_json)

        # Transport (TCP only for now)
        # Trusted seeds bypass the transport's inbound-connection rate gate. The
        # verifier-seed hosts/IPs are computed later in __init__ and folded in via
        # add_trusted_hosts() once available.
        self._transport = TcpTransport(
            handshake_prologue=b"animica/tcp/1",
            chain_id=self.chain_id,
            trusted_hosts=set(self._trusted_seed_hosts),
        )

        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._child_tasks: Set[asyncio.Task] = set()
        self._dial_inflight: Set[str] = set()
        self._dial_backoff: dict[str, float] = {}
        self._dial_attempts: dict[str, int] = {}
        self._dial_attempt_total: int = 0
        self._dial_success_total: int = 0
        self._dial_last_error: Optional[dict[str, Any]] = None
        self._bootstrap_seed_rate_limit = int(
            os.environ.get("ANIMICA_P2P_SEED_RATE_LIMIT", "6") or 6
        )
        self._bootstrap_seed_rate_window = float(
            os.environ.get("ANIMICA_P2P_SEED_RATE_WINDOW", "300") or 300
        )
        self._seed_hosts = self._seed_hostnames(self.seeds)

        self._peer_lock = asyncio.Lock()
        self._peers: dict[tuple[str, str], _PeerState] = {}  # (remote, direction) -> state
        self._peers_by_session: dict[str, _PeerState] = {}
        self._peer_registry = PeerRegistry(
            max_inbound_per_ip=int(os.environ.get("ANIMICA_P2P_MAX_INBOUND_PER_IP", "10") or 10),
            handshake_timeout_s=float(os.environ.get("ANIMICA_P2P_HANDSHAKE_TIMEOUT", "3.0") or 3.0),
            handshake_rate_limit_per_ip=int(
                os.environ.get("ANIMICA_P2P_HANDSHAKE_RATE_PER_IP", "30") or 30
            ),
            handshake_rate_limit_per_netgroup=int(
                os.environ.get("ANIMICA_P2P_HANDSHAKE_RATE_PER_NETGROUP", "120") or 120
            ),
            handshake_rate_window_s=float(
                os.environ.get("ANIMICA_P2P_HANDSHAKE_RATE_WINDOW", "60.0") or 60.0
            ),
            handshake_rate_netgroup_v4_bits=int(
                os.environ.get("ANIMICA_P2P_HANDSHAKE_RATE_NETGROUP_V4", "24") or 24
            ),
            handshake_rate_netgroup_v6_bits=int(
                os.environ.get("ANIMICA_P2P_HANDSHAKE_RATE_NETGROUP_V6", "48") or 48
            ),
            trusted_reconnect_grace_s=float(
                os.environ.get("ANIMICA_P2P_TRUSTED_RECONNECT_GRACE", "180.0") or 180.0
            ),
            trusted_hosts=set(self._trusted_seed_hosts),
        )
        self._hello_timeout_grace_s = float(
            os.environ.get("ANIMICA_P2P_HELLO_TIMEOUT_GRACE", "5.0") or 5.0
        )
        self._hello_timeout_grace_used: set[str] = set()

        # Seen LRU (dedupe + rebroadcast suppression)
        self._seen_tx: "OrderedDict[bytes, float]" = OrderedDict()
        self._seen_blocks: "OrderedDict[bytes, float]" = OrderedDict()
        self._seen_tx_cap = 50_000
        self._seen_block_cap = 10_000
        self._tx_inv_seen: "OrderedDict[bytes, float]" = OrderedDict()
        self._tx_inv_seen_cap = 50_000
        self._txblock_seen: "OrderedDict[str, float]" = OrderedDict()
        self._txblock_seen_cap = 50_000
        self._tx_requested: "OrderedDict[bytes, tuple[float, str]]" = OrderedDict()
        self._tx_requested_cap = 50_000
        self._tx_sent_by_peer: dict[str, "OrderedDict[bytes, float]"] = {}
        self._tx_inv_sent_by_peer: dict[str, "OrderedDict[bytes, float]"] = {}
        self._tx_rebroadcast_task: Optional[asyncio.Task] = None
        self._tx_relay_heartbeat_at: float = 0.0
        self._tx_recent_rejects: Deque[dict[str, Any]] = deque(
            maxlen=int(os.environ.get("ANIMICA_P2P_TX_REJECT_LOG", "50") or 50)
        )
        self._tx_relay_ttl_s = float(
            os.environ.get("ANIMICA_P2P_TX_RELAY_TTL_SECONDS", "120") or 120
        )
        self._tx_relay_enabled = _env_flag("ANIMICA_P2P_TX_RELAY", default=True)
        self._tx_relay_v2_enabled = _env_flag("ANIMICA_TX_RELAY_V2", default=True)
        self._tx_relay_debug = _env_flag("ANIMICA_TX_RELAY_DEBUG", default=False)
        self._tx_gossip_enabled = _env_flag("ANIMICA_P2P_TX_GOSSIP", default=True)
        self._mempool_gossip_enabled = _env_flag(
            "ANIMICA_P2P_MEMPOOL_GOSSIP", default=True
        )
        self._p2p_tx_enabled = _env_flag("ANIMICA_P2P_TX_ENABLED", default=True)
        self._bootstrap_mode = _env_flag(
            "ANIMICA_BOOTSTRAP_NODE", "ANIMICA_RPC_BOOTSTRAP_NODE", default=False
        )
        self._tx_inv_reannounce_interval_s = float(
            os.environ.get("ANIMICA_P2P_TX_REANNOUNCE_SEC", "15") or 15
        )
        required_caps = os.environ.get("ANIMICA_P2P_REQUIRED_CAPS", "")
        self._required_caps: set[str] = {
            c.strip() for c in required_caps.split(",") if c.strip()
        }
        self._max_tx_bytes = int(
            os.environ.get("ANIMICA_P2P_MAX_TX_BYTES", str(MAX_TX_BYTES))
            or MAX_TX_BYTES
        )
        self._max_inv_per_msg = int(
            os.environ.get("ANIMICA_P2P_MAX_INV_PER_MSG", str(MAX_INV_PER_MSG))
            or MAX_INV_PER_MSG
        )
        self._max_getdata_per_min_per_peer = int(
            os.environ.get("ANIMICA_P2P_MAX_GETDATA_PER_MIN_PER_PEER", "120") or 120
        )
        self._max_tx_per_min_per_peer = int(
            os.environ.get("ANIMICA_P2P_MAX_TX_PER_MIN_PER_PEER", "120") or 120
        )
        self._tx_rate_window_s = float(
            os.environ.get("ANIMICA_P2P_TX_RATE_WINDOW_S", "60") or 60
        )
        self._txblock_rate_window_s = float(
            os.environ.get("ANIMICA_P2P_TXBLOCK_RATE_WINDOW_S", "60") or 60
        )
        self._max_txblock_per_min_per_peer = int(
            os.environ.get("ANIMICA_P2P_MAX_TXBLOCK_PER_MIN_PER_PEER", "120") or 120
        )
        self._txblock_inflight_by_peer: dict[str, Deque[float]] = {}
        self._getdata_inflight_by_peer: dict[str, Deque[float]] = {}
        self._tx_inflight_by_peer: dict[str, Deque[float]] = {}
        self._getdata_out_by_peer: dict[str, Deque[float]] = {}
        self._tx_inv_seed_limit = int(
            os.environ.get("ANIMICA_P2P_TX_INV_SEED_LIMIT", "256") or 256
        )
        self._tx_inv_seed_batch = int(
            os.environ.get("ANIMICA_P2P_TX_INV_SEED_BATCH", "128") or 128
        )
        self._tx_inv_rate_per_sec = float(
            os.environ.get("ANIMICA_P2P_TX_INV_RATE_PER_SEC", "2000") or 2000
        )
        self._tx_inv_rate_burst = float(
            os.environ.get("ANIMICA_P2P_TX_INV_RATE_BURST", "4000") or 4000
        )
        self._tx_data_rate_bytes_per_sec = float(
            os.environ.get("ANIMICA_P2P_TX_DATA_RATE_BYTES_PER_SEC", "5000000")
            or 5000000
        )
        self._tx_data_rate_burst_bytes = float(
            os.environ.get("ANIMICA_P2P_TX_DATA_RATE_BURST_BYTES", "10000000")
            or 10000000
        )
        self._tx_mempool_sync_interval_s = float(
            os.environ.get("ANIMICA_P2P_TX_MEMPOOL_SYNC_SEC", "15") or 15
        )
        self._tx_relay_reconcile_interval_s = float(
            os.environ.get("ANIMICA_TX_RELAY_RECONCILE_INTERVAL", "10") or 10
        )
        self._tx_mempool_sync_limit = int(
            os.environ.get("ANIMICA_P2P_TX_MEMPOOL_SYNC_LIMIT", "2000") or 2000
        )
        # Watchdog for more aggressive mempool monitoring
        self._tx_mempool_watchdog_interval_s = float(
            os.environ.get("ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_SEC", "3") or 3
        )
        self._tx_mempool_watchdog_limit = int(
            os.environ.get("ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_LIMIT", "256") or 256
        )
        self._tx_inv_queue_timeout_s = float(
            os.environ.get("ANIMICA_TX_INV_QUEUE_TIMEOUT_SEC", "30") or 30
        )
        self._txrelay_inv_flush_task: Optional[asyncio.Task] = None
        self._txrelay_inflight_task: Optional[asyncio.Task] = None
        self._txrelay_sync_task: Optional[asyncio.Task] = None
        self._txrelay_watchdog_task: Optional[asyncio.Task] = None
        self._txrelay_reconcile_task: Optional[asyncio.Task] = None
        self._txrelay_inv_watchdog_task: Optional[asyncio.Task] = None
        self._mempool_bind_task: Optional[asyncio.Task] = None
        self._mempool_callback_bound = False
        try:
            self._txrelay = TxRelayService(
                max_tx_bytes=self._max_tx_bytes,
                inv_batch_size=200,
                inv_flush_interval_s=0.2,
                inflight_timeout_s=10.0,
                inflight_max_retries=2,
                invalid_tx_cooldown_s=float(
                    os.environ.get("ANIMICA_P2P_TX_INVALID_COOLDOWN_SEC", "1800") or 1800
                ),
                mempool_sync_interval_s=self._tx_mempool_sync_interval_s,
                mempool_sync_limit=self._tx_mempool_sync_limit,
                mempool_watchdog_interval_s=self._tx_mempool_watchdog_interval_s,
                mempool_watchdog_limit=self._tx_mempool_watchdog_limit,
                reconcile_interval_s=self._tx_relay_reconcile_interval_s,
                debug_enabled=self._tx_relay_debug,
                known_txids_cap=50_000,
                inv_rate_per_sec=self._tx_inv_rate_per_sec,
                inv_burst=self._tx_inv_rate_burst,
                tx_data_rate_bytes_per_sec=self._tx_data_rate_bytes_per_sec,
                tx_data_burst_bytes=self._tx_data_rate_burst_bytes,
                inv_queue_timeout_s=self._tx_inv_queue_timeout_s,
                peer_ids=self._txrelay_peer_ids,
                peer_eligible=self._txrelay_peer_eligible,
                send_tx_inv=self._txrelay_send_inv,
                send_tx_get=self._txrelay_send_get,
                send_tx_data=self._txrelay_send_data,
                send_tx_notfound=self._txrelay_send_notfound,
                send_mempool_req=self._txrelay_send_mempool_req,
                send_mempool_resp=self._txrelay_send_mempool_resp,
                send_mempool_summary=self._txrelay_send_mempool_summary,
                has_tx=self._txrelay_has_tx,
                has_chain_tx=self._txrelay_has_chain_tx,
                get_tx_raw=self._txrelay_get_tx_raw,
                admit_tx=self._txrelay_admit_tx,
                list_mempool_hashes=self._txrelay_list_mempool_hashes,
                on_tx_accepted=self._legacy_tx_relay_announce,
            )
        except Exception as exc:
            log.warning(
                "Tx relay initialization failed; disabling tx relay",
                extra={"error": str(exc)},
                exc_info=True,
            )
            self._txrelay = _NullTxRelay()
            self._tx_relay_enabled = False
            self._tx_relay_v2_enabled = False
        self._addr_peer_known_ttl = float(
            os.environ.get("ANIMICA_P2P_ADDR_KNOWN_TTL", "600") or 600
        )
        self._peer_addr_rate_limit = int(
            os.environ.get("ANIMICA_P2P_ADDR_RATE_LIMIT", "256") or 256
        )
        self._peer_addr_rate_window = float(
            os.environ.get("ANIMICA_P2P_ADDR_RATE_WINDOW", "60") or 60
        )
        self._peer_addr_rate: dict[str, Deque[float]] = {}
        self._sync_peer_backoff: dict[str, float] = {}
        self._sync_peer_backoff_reason: dict[str, str] = {}
        self._sync_block_peer_backoff: dict[str, float] = {}
        self._sync_block_peer_backoff_reason: dict[str, str] = {}
        self._sync_block_failure_events: dict[str, Deque[float]] = {}
        self._sync_block_failure_window_s = float(
            os.environ.get("ANIMICA_P2P_BLOCK_FAILURE_WINDOW", "600") or 600
        )
        self._sync_block_cooldown_base_s = float(
            os.environ.get("ANIMICA_P2P_BLOCK_COOLDOWN_BASE", "60") or 60
        )
        self._sync_block_cooldown_cap_s = float(
            os.environ.get("ANIMICA_P2P_BLOCK_COOLDOWN_CAP", "600") or 600
        )
        self._sync_block_recovery_peers = int(
            os.environ.get("ANIMICA_P2P_BLOCK_RECOVERY_PEERS", "5") or 5
        )
        self._sync_block_recovery_concurrency = int(
            os.environ.get("ANIMICA_P2P_BLOCK_RECOVERY_CONCURRENCY", "2") or 2
        )
        self._sync_last_block_recovery_peers: list[str] = []
        self._sync_block_chunk_size_default = int(
            os.environ.get("ANIMICA_P2P_BLOCK_CHUNK_SIZE", "16") or 16
        )
        self._sync_block_chunk_size_min = int(
            os.environ.get("ANIMICA_P2P_BLOCK_CHUNK_SIZE_MIN", "1") or 1
        )
        self._sync_block_chunk_size_current = max(
            self._sync_block_chunk_size_min, self._sync_block_chunk_size_default
        )
        self._sync_block_queue_limit = int(
            os.environ.get("ANIMICA_P2P_BLOCK_QUEUE_LIMIT", "50000") or 50000
        )
        self._sync_cache_failure_window_s = float(
            os.environ.get("ANIMICA_P2P_CACHE_FAILURE_WINDOW", "300") or 300
        )
        self._sync_cache_failure_cap = int(
            os.environ.get("ANIMICA_P2P_CACHE_FAILURE_CAP", "3") or 3
        )
        self._sync_cache_failures: dict[bytes, Deque[float]] = {}
        self._sync_peer_eligibility_cache: dict[str, str] = {}
        self._sync_last_phase_reported: Optional[str] = None
        self._sync_header_events: Deque[dict[str, Any]] = deque(
            maxlen=int(os.environ.get("ANIMICA_P2P_SYNC_DEBUG_EVENTS", "50") or 50)
        )
        self._sync_header_sources: Dict[bytes, str] = {}
        self._sync_header_votes: "OrderedDict[bytes, dict[str, Any]]" = OrderedDict()
        self._sync_header_vote_ttl_s = float(
            os.environ.get("ANIMICA_P2P_HEADER_VOTE_TTL", "600") or 600
        )
        self._sync_header_vote_cap = int(
            os.environ.get("ANIMICA_P2P_HEADER_VOTE_CAP", "200000") or 200000
        )
        self._sync_block_confirm_min_peers = max(
            1,
            int(
                os.environ.get("ANIMICA_P2P_BLOCK_CONFIRM_MIN_PEERS", "2")
                or 2
            ),
        )
        self._sync_block_confirm_require_force_peer = _env_flag(
            "ANIMICA_P2P_BLOCK_CONFIRM_REQUIRE_FORCE_PEER",
            default=True,
        )
        self._sync_no_headers_threshold = int(
            os.environ.get("ANIMICA_P2P_NO_HEADERS_THRESHOLD", "3") or 3
        )
        self._sync_no_headers_backoff = float(
            os.environ.get("ANIMICA_P2P_NO_HEADERS_BACKOFF", "15.0") or 15.0
        )
        self._sync_header_empty_cooldown_base_s = float(
            os.environ.get("ANIMICA_P2P_HEADER_EMPTY_COOLDOWN_BASE", "30") or 30
        )
        self._sync_header_empty_cooldown_cap_s = float(
            os.environ.get("ANIMICA_P2P_HEADER_EMPTY_COOLDOWN_CAP", "600") or 600
        )
        self._sync_block_attempts_by_hash: dict[bytes, Deque[str]] = {}
        self._sync_block_retry_counts: dict[bytes, int] = {}
        self._sync_block_attempts_cap = int(
            os.environ.get("ANIMICA_P2P_BLOCK_ATTEMPT_HISTORY", "8") or 8
        )
        self._sync_last_cache_error_at = 0.0
        self._sync_last_cache_error_hash: Optional[bytes] = None
        self._sync_not_anchored_backoff = float(
            os.environ.get("ANIMICA_P2P_NOT_ANCHORED_BACKOFF", "30.0") or 30.0
        )
        self._sync_not_anchored_backoff_cap = float(
            os.environ.get("ANIMICA_P2P_NOT_ANCHORED_BACKOFF_CAP", "30.0") or 30.0
        )
        self._sync_not_anchored_reset_threshold = int(
            os.environ.get("ANIMICA_P2P_NOT_ANCHORED_RESET_THRESHOLD", "3") or 3
        )
        self._sync_not_anchored_reset_height = int(
            os.environ.get("ANIMICA_P2P_NOT_ANCHORED_RESET_HEIGHT", "10") or 10
        )
        self._sync_not_anchored_window = float(
            os.environ.get("ANIMICA_P2P_NOT_ANCHORED_WINDOW", "300") or 300
        )
        self._sync_duplicate_headers_threshold = int(
            os.environ.get("ANIMICA_P2P_DUPLICATE_HEADERS_THRESHOLD", "2") or 2
        )
        self._sync_not_anchored_attempts = 0
        self._sync_last_not_anchored_at = 0.0
        self._sync_recovery_attempts = 0
        self._sync_last_recovery_action: Optional[str] = None
        # 38728-wedge telemetry/limits: consecutive full-window overlap-only
        # header batches (see _process_headers), and per-height counts of
        # fork-sibling block enqueues (anti-spam cap in
        # _enqueue_missing_blocks).
        self._sync_overlap_full_batches = 0
        self._sibling_enqueue_counts: dict[int, int] = {}
        self._sync_last_recovery_at: Optional[float] = None
        self._sync_last_recovery_reason: Optional[str] = None
        self._sync_last_locator_summary: Optional[dict[str, Any]] = None
        self._sync_anchor_probe_hash: Optional[bytes] = None
        self._sync_anchor_probe_until = 0.0
        self._sync_anchor_probe_peer: Optional[str] = None
        self._sync_checkpoint_height: Optional[int] = None
        self._sync_checkpoint_hash: Optional[bytes] = None
        self._sync_checkpoint_mode_enabled = False
        self._sync_checkpoint_validation: Optional[str] = None
        self._sync_last_checkpoint_action: Optional[str] = None
        self._sync_checkpoint_locator_after = float(
            os.environ.get("ANIMICA_P2P_CHECKPOINT_LOCATOR_AFTER", "30") or 30
        )
        self._sync_checkpoint_safety_margin = int(
            os.environ.get("ANIMICA_P2P_CHECKPOINT_SAFETY_MARGIN", "0") or 0
        )
        self._sync_last_locator_info: list[dict[str, Any]] = []
        self._sync_last_locator_at = 0.0
        self._sync_locator_depth_hint = 0
        self._sync_duplicate_header_ranges: dict[
            str, tuple[tuple[str, str, int], int]
        ] = {}
        self._sync_stale_network_best_at = 0.0
        self._sync_stale_network_best_count = 0
        self._sync_stale_network_best_cooldown = float(
            os.environ.get("ANIMICA_P2P_STALE_NETWORK_BEST_COOLDOWN", "30.0") or 30.0
        )
        self._sync_peer_head_stale_sec = float(
            os.environ.get("ANIMICA_P2P_PEER_HEAD_STALE_SEC", "60.0") or 60.0
        )
        self._sync_verifier_rewind_grace_sec = float(
            os.environ.get("ANIMICA_P2P_VERIFIER_REWIND_GRACE_SEC", "30.0") or 30.0
        )
        self._sync_verifier_ahead_since: Optional[float] = None
        self._sync_peer_head_cooldown_sec = float(
            os.environ.get("ANIMICA_P2P_PEER_HEAD_COOLDOWN_SEC", "120.0") or 120.0
        )
        self._sync_peer_broadcast_recent_sec = float(
            os.environ.get("ANIMICA_P2P_BROADCAST_RECENT_SEC", "120") or 120
        )
        self._sync_peer_non_broadcasting_sec = float(
            os.environ.get("ANIMICA_P2P_NON_BROADCASTING_SEC", "120") or 120
        )
        self._sync_max_height_ahead_of_verifier = max(
            0,
            int(
                os.environ.get(
                    "ANIMICA_P2P_MAX_HEIGHT_AHEAD_OF_VERIFIER",
                    str(DEFAULT_MAX_HEIGHT_AHEAD_OF_VERIFIER),
                )
                or DEFAULT_MAX_HEIGHT_AHEAD_OF_VERIFIER
            ),
        )
        self._sync_verifier_rewind_idle_sec = max(
            0.0,
            float(
                os.environ.get(
                    "ANIMICA_P2P_VERIFIER_REWIND_IDLE_SEC",
                    str(self._sync_peer_non_broadcasting_sec),
                )
                or self._sync_peer_non_broadcasting_sec
            ),
        )
        self._sync_peer_score_log_interval = float(
            os.environ.get("ANIMICA_P2P_PEER_SCORE_LOG_INTERVAL", "60") or 60
        )
        self._sync_last_peer_score_log_at = 0.0
        self._sync_no_progress_timeout = float(
            _env_value(
                "SYNC_NO_PROGRESS_TIMEOUT_S",
                "ANIMICA_SYNC_NO_PROGRESS_TIMEOUT_S",
                "ANIMICA_P2P_SYNC_NO_PROGRESS_TIMEOUT",
                default="60.0",
            )
            or 60.0
        )
        self._sync_inflight_ttl = float(
            _env_value(
                "SYNC_INFLIGHT_TTL_S",
                "ANIMICA_SYNC_INFLIGHT_TTL_S",
                "ANIMICA_P2P_SYNC_INFLIGHT_TTL",
                default="120.0",
            )
            or 120.0
        )
        self._sync_inflight_max_retries = int(
            _env_value(
                "SYNC_INFLIGHT_MAX_RETRIES",
                "ANIMICA_SYNC_INFLIGHT_MAX_RETRIES",
                "ANIMICA_P2P_SYNC_INFLIGHT_MAX_RETRIES",
                default="3",
            )
            or 3
        )
        self._sync_nonfatal_penalty_window_s = float(
            os.environ.get("ANIMICA_P2P_NONFATAL_PENALTY_WINDOW", "300") or 300
        )
        self._sync_nonfatal_penalty_limit = int(
            os.environ.get("ANIMICA_P2P_NONFATAL_PENALTY_LIMIT", "5") or 5
        )
        self._sync_nonfatal_penalty_events: dict[str, Deque[float]] = {}
        self._sync_last_target_hash: Optional[bytes] = None
        self._sync_target_tip: Optional[_SyncTargetTip] = None
        self._sync_target_mismatch_count = 0
        self._sync_missing_parent_recoveries = 0

        # Tiny metrics snapshot used by RPC/CLI
        self._stats: dict[str, int] = {
            "peers": 0,
            "dial_attempts": 0,
            "dial_successes": 0,
            "dial_skipped_outbound_only": 0,
            "handshake_failures": 0,
            "caps_failures": 0,
            "disconnects": 0,
            "inv_tx_sent": 0,
            "inv_tx_recv": 0,
            "tx_inv_sent_total": 0,
            "tx_inv_recv_total": 0,
            "tx_inv_dedup": 0,
            "tx_getdata_sent": 0,
            "tx_getdata_recv": 0,
            "tx_getdata_rate_limited": 0,
            "tx_getdata_skipped": 0,
            "tx_recv": 0,
            "tx_sent": 0,
            "tx_data_sent_total": 0,
            "tx_data_recv_total": 0,
            "tx_sent_dedup": 0,
            "tx_recv_rate_limited": 0,
            "inv_block_sent": 0,
            "inv_block_recv": 0,
            "blocks_sent": 0,
            "blocks_recv": 0,
            "blocks_requested": 0,
            "blocks_received": 0,
            "headers_req_sent": 0,
            "headers_req_timeout": 0,
            "headers_req_ok": 0,
            "headers_req_empty": 0,
            "blocks_req_sent": 0,
            "blocks_req_timeout": 0,
            "blocks_req_ok": 0,
            "blocks_req_empty": 0,
            "blocks_orphaned": 0,
            "blocks_applied": 0,
            "blocks_validated_ok": 0,
            "blocks_imported": 0,
            "blocks_rejected": 0,
            "sync_rounds": 0,
            "sync_loop_errors": 0,
            "p2p_peers_rejected_genesis_mismatch": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "stall_recoveries": 0,
            "sync_target_set": 0,
            "sync_progress": 0,
            "sync_stall_detected": 0,
            "sync_inflight_reset": 0,
            "sync_missing_parent_recover": 0,
            "sync_reorg_applied": 0,
            "peer_broadcast_good": 0,
            "peer_broadcast_stale": 0,
            "peer_duplicate_header_batches": 0,
            "peer_selected_for_headers": 0,
            "peer_selected_for_blocks": 0,
        }

        # Address discovery / relay state
        self._addr_request_interval = float(
            os.environ.get("ANIMICA_P2P_ADDR_REQUEST_INTERVAL", "30") or 30
        )
        self._addr_response_interval = float(
            os.environ.get("ANIMICA_P2P_ADDR_RESPONSE_INTERVAL", "15") or 15
        )
        self._addr_request_max = int(
            os.environ.get("ANIMICA_P2P_ADDR_REQUEST_MAX", "32") or 32
        )
        self._peer_exchange_limit = int(
            os.environ.get("ANIMICA_P2P_PEER_EXCHANGE_LIMIT", "128") or 128
        )
        self._addr_relay_interval = float(
            os.environ.get("ANIMICA_P2P_ADDR_RELAY_INTERVAL", "45") or 45
        )
        self._addr_relay_sample = int(
            os.environ.get("ANIMICA_P2P_ADDR_RELAY_SAMPLE", "24") or 24
        )
        self._addr_peer_known_cap = int(
            os.environ.get("ANIMICA_P2P_ADDR_KNOWN_CAP", "2048") or 2048
        )
        self._addr_last_request: dict[str, float] = {}
        self._addr_last_response: dict[str, float] = {}
        self._addr_seen: "OrderedDict[str, float]" = OrderedDict()
        self._addr_seen_cap = int(
            os.environ.get("ANIMICA_P2P_ADDR_SEEN_CAP", "5000") or 5000
        )
        self._addrman = _AddrMan()
        self._addr_learned_events: deque[float] = deque(maxlen=10_000)
        self._addr_announced_events: deque[float] = deque(maxlen=10_000)
        self._persist_peers_event = asyncio.Event()
        self._persist_peers_interval = float(
            os.environ.get("ANIMICA_P2P_PEER_PERSIST_INTERVAL", "20") or 20
        )
        self._persisted_peer_count: Optional[int] = None
        self._allow_private_addrs = os.environ.get(
            "ANIMICA_P2P_PRIVATE_NETWORK", "false"
        ).lower() in ("1", "true", "yes", "on")
        self._allow_self_peers = _env_flag("ANIMICA_P2P_ALLOW_SELF_PEERS", default=False)
        self._enforce_inbound_reachability = _env_flag(
            "ANIMICA_P2P_ENFORCE_INBOUND_REACHABILITY",
            default=bool(int(self.chain_id) == 1),
        )
        self._forfeit_outbound_only_blocks = _env_flag(
            "ANIMICA_P2P_FORFEIT_OUTBOUND_ONLY_BLOCKS",
            default=self._enforce_inbound_reachability,
        )
        self._outbound_only_ban_ttl = float(
            os.environ.get("ANIMICA_P2P_OUTBOUND_ONLY_BAN_TTL", "21600") or 21600
        )
        self._outbound_only_blocklist: dict[str, float] = {}
        self._maybe_enable_private_from_config()
        self._external_ip = os.environ.get("ANIMICA_P2P_EXTERNAL_IP")
        self._external_ip_endpoint = (
            os.environ.get("ANIMICA_P2P_EXTERNAL_IP_ENDPOINT")
            or os.environ.get("ANIMICA_PUBLIC_IP_ENDPOINT")
        )
        
        # Verifier seed nodes for height validation
        # These nodes are considered authoritative for determining the highest block height
        self._enable_verifier_seeds = _env_flag("ANIMICA_P2P_ENABLE_VERIFIER_SEEDS", default=True)
        verifier_nodes_env = os.environ.get(
            "ANIMICA_P2P_VERIFIER_SEED_IPS", DEFAULT_VERIFIER_SEED_NODES
        )
        self._verifier_seed_nodes = {
            entry.strip().lower()
            for entry in verifier_nodes_env.split(",")
            if entry.strip()
        }
        self._verifier_seed_ips = {
            entry for entry in self._verifier_seed_nodes if _is_ip_literal(entry)
        }
        self._verifier_seed_hosts = (
            self._verifier_seed_nodes - self._verifier_seed_ips
        )
        log.info(
            "Verifier seed configuration",
            extra={
                "enabled": self._enable_verifier_seeds,
                "verifier_ips": sorted(self._verifier_seed_ips),
                "verifier_hosts": sorted(self._verifier_seed_hosts),
            }
        )
        # Exempt our own verifier seeds from the transport inbound-rate gate and
        # the registry per-IP inbound-count cap too.
        _verifier_trusted = set(self._verifier_seed_hosts) | set(self._verifier_seed_ips)
        self._transport.add_trusted_hosts(_verifier_trusted)
        self._peer_registry.add_trusted_hosts(_verifier_trusted)
        
        self._seeding_mode = True
        self._feeler_interval = float(
            os.environ.get("ANIMICA_P2P_FEELER_INTERVAL", "25") or 25
        )
        self._feeler_hold_s = float(
            os.environ.get("ANIMICA_P2P_FEELER_HOLD_S", "5") or 5
        )
        self._max_payload_bytes = int(
            os.environ.get("ANIMICA_P2P_MAX_PAYLOAD_BYTES", str(8 * 1024 * 1024))
            or 8 * 1024 * 1024
        )
        self._max_blocks_per_message = int(
            os.environ.get("ANIMICA_P2P_MAX_BLOCKS_PER_MSG", "64") or 64
        )
        self._max_headers_per_message = int(
            os.environ.get("ANIMICA_P2P_MAX_HEADERS_PER_MSG", "512") or 512
        )
        self._clock_skew_s = float(
            os.environ.get("ANIMICA_P2P_CLOCK_SKEW", "300") or 300
        )

        self._netgroup_v4_bits = int(
            os.environ.get("ANIMICA_P2P_NETGROUP_V4", "16") or 16
        )
        self._netgroup_v6_bits = int(
            os.environ.get("ANIMICA_P2P_NETGROUP_V6", "48") or 48
        )
        self._max_outbound_per_netgroup = int(
            os.environ.get("ANIMICA_P2P_MAX_OUTBOUND_PER_NETGROUP", "1") or 1
        )
        self._max_inbound_per_netgroup = int(
            os.environ.get("ANIMICA_P2P_MAX_INBOUND_PER_NETGROUP", "2") or 2
        )
        self._min_outbound = int(
            os.environ.get("ANIMICA_P2P_MIN_OUTBOUND", "4") or 4
        )

        self._misbehavior_decay_interval = float(
            os.environ.get("ANIMICA_P2P_SCORE_DECAY_INTERVAL", "60") or 60
        )
        self._misbehavior_decay_points = int(
            os.environ.get("ANIMICA_P2P_SCORE_DECAY_POINTS", "5") or 5
        )
        self._misbehavior_score_cap = int(
            os.environ.get("ANIMICA_P2P_SCORE_CAP", "2000") or 2000
        )

        # ── ANM-H03/H04: per-peer inbound message-rate limiting (DoS cap) ──────
        # Sheds, then disconnects, peers that flood us with messages. The token
        # bucket is intentionally generous (≈100x real peer traffic, even during
        # aggressive block/header sync), so legitimate peers are never affected —
        # only genuine floods trip the guard. Enabled by default with a sane cap;
        # fully tunable and disengageable via env (set rate/burst <= 0 or the
        # ENABLED flag to 0 to turn off).
        self._peer_msg_rate_enabled = _env_flag(
            "ANIMICA_P2P_PEER_MSG_RATE_ENABLED", default=True
        )
        self._peer_msg_rate_per_s = float(
            os.environ.get("ANIMICA_P2P_PEER_MSG_RATE_PER_S", "1000") or 1000.0
        )
        self._peer_msg_rate_burst = float(
            os.environ.get("ANIMICA_P2P_PEER_MSG_RATE_BURST", "2000") or 2000.0
        )
        self._peer_msg_rate_max_strikes = int(
            os.environ.get("ANIMICA_P2P_PEER_MSG_RATE_MAX_STRIKES", "20") or 20
        )
        self._peer_msg_rate_ban_ttl = float(
            os.environ.get("ANIMICA_P2P_PEER_MSG_RATE_BAN_TTL_S", "300") or 300.0
        )
        if self._peer_msg_rate_per_s <= 0 or self._peer_msg_rate_burst <= 0:
            # Non-positive settings disable enforcement rather than wedge peers.
            self._peer_msg_rate_enabled = False
        if self._peer_msg_rate_enabled:
            log.info(
                "Per-peer inbound message rate limiting enabled",
                extra={
                    "rate_per_s": self._peer_msg_rate_per_s,
                    "burst": self._peer_msg_rate_burst,
                    "max_strikes": self._peer_msg_rate_max_strikes,
                    "ban_ttl_s": self._peer_msg_rate_ban_ttl,
                },
            )
        else:
            log.warning(
                "Per-peer inbound message rate limiting DISABLED "
                "(ANIMICA_P2P_PEER_MSG_RATE_* off) — node has no per-peer "
                "message flood protection"
            )

        self._score_points = {
            "malformed_message": int(
                os.environ.get("ANIMICA_P2P_SCORE_MALFORMED", "50") or 50
            ),
            "wrong_genesis": int(
                os.environ.get("ANIMICA_P2P_SCORE_GENESIS", "1000") or 1000
            ),
            "wrong_chain": int(
                os.environ.get("ANIMICA_P2P_SCORE_CHAIN", "1000") or 1000
            ),
            "invalid_header": int(
                os.environ.get("ANIMICA_P2P_SCORE_HEADER", "200") or 200
            ),
            "invalid_block": int(
                os.environ.get("ANIMICA_P2P_SCORE_BLOCK", "500") or 500
            ),
            "timeout": int(os.environ.get("ANIMICA_P2P_SCORE_TIMEOUT", "10") or 10),
            "missing_parent": int(
                os.environ.get("ANIMICA_P2P_SCORE_MISSING_PARENT", "25") or 25
            ),
            "stall": int(os.environ.get("ANIMICA_P2P_SCORE_STALL", "25") or 25),
        }
        self._ban_thresholds = [
            (
                int(os.environ.get("ANIMICA_P2P_BAN_SCORE_TEMP", "200") or 200),
                float(os.environ.get("ANIMICA_P2P_BAN_TEMP_S", "1800") or 1800),
            ),
            (
                int(os.environ.get("ANIMICA_P2P_BAN_SCORE_LONG", "500") or 500),
                float(os.environ.get("ANIMICA_P2P_BAN_LONG_S", "21600") or 21600),
            ),
            (
                int(os.environ.get("ANIMICA_P2P_BAN_SCORE_MAX", "1000") or 1000),
                float(os.environ.get("ANIMICA_P2P_BAN_MAX_S", "86400") or 86400),
            ),
        ]
        # ANM-L07 (event-loop wedge fix): peer banning MUST be enabled so that
        # provably-incompatible peers — wrong_genesis / wrong_chain (1000 pts →
        # 24h ban tier) — stop reconnecting. While banning was off, such a peer
        # was penalised and dropped but never banned, so it reconnected in a hot
        # loop and re-ran the CPU-heavy pure-Python AEAD handshake every time.
        # That saturates the single asyncio event loop and halts block
        # production. Banning is a LOCAL networking policy only — it does not
        # touch block/state/consensus validation, so enabling it is not a
        # consensus change and cannot fork or diverge the chain. Env-gated
        # (default ON) so an operator can still disable it with
        # ANIMICA_P2P_BAN_ENABLED=0. Seed / exempt / docker-local peers are
        # already skipped inside _ban_peer / _penalize_peer.
        self._ban_enabled = (
            str(os.environ.get("ANIMICA_P2P_BAN_ENABLED", "1")).strip().lower()
            not in ("0", "false", "no", "off", "")
        )
        self._banlist_path = Path.home() / ".animica" / "banlist.json"
        self._banlist: dict[str, dict[str, Any]] = {}
        self._banlist_event = asyncio.Event()
        self._banlist_persist_interval = float(
            os.environ.get("ANIMICA_P2P_BAN_PERSIST_INTERVAL", "15") or 15
        )
        self._last_score_decay_at = time.time()
        self._last_rotation_at = 0.0
        self._rotation_interval = float(
            os.environ.get("ANIMICA_P2P_ROTATE_INTERVAL", "300") or 300
        )
        self._max_orphan_blocks = int(
            os.environ.get("ANIMICA_P2P_MAX_ORPHANS", "128") or 128
        )
        self._sync_orphan_ttl = float(
            os.environ.get("ANIMICA_P2P_ORPHAN_TTL_S", "60") or 60
        )
        self._missing_parent_threshold = int(
            os.environ.get("ANIMICA_P2P_MISSING_PARENT_THRESHOLD", "3") or 3
        )

        self._sync_lock = asyncio.Lock()
        self._sync_wakeup = asyncio.Event()
        self._sync_phase = "IDLE"
        self._sync_best_header: Optional[_SyncHeader] = None
        self._sync_headers: Dict[bytes, _SyncHeader] = {}
        self._sync_header_queue: Deque[Tuple[str, List[HeaderCompact]]] = deque()
        self._sync_header_retry_queue: Deque[_SyncRequest] = deque()
        self._sync_inflight_header_requests: Dict[tuple[str, str], _SyncRequest] = {}
        self._sync_inflight_blocks: Dict[bytes, float] = {}
        self._sync_inflight_peers: Dict[bytes, str] = {}
        self._sync_inflight_block_requests: Dict[bytes, _SyncRequest] = {}
        self._sync_block_buffer: "OrderedDict[bytes, _SyncBlock]" = OrderedDict()
        self._sync_block_queue: Deque[bytes] = deque()
        self._sync_block_queue_set: set[bytes] = set()
        self._sync_block_queue_heights: Dict[bytes, int] = {}
        self._sync_last_block_error: Optional[str] = None
        self._sync_last_block_error_at: Optional[float] = None
        self._sync_last_block_error_peer: Optional[str] = None
        self._sync_block_error_summary: Dict[str, dict[str, Any]] = {}
        self._sync_header_reject_log_at: dict[str, float] = {}
        self._sync_header_reject_log_min_s = float(
            os.environ.get("ANIMICA_P2P_HEADER_REJECT_LOG_MIN_S", "5.0") or 5.0
        )
        self._sync_pow_mismatch_votes: "OrderedDict[bytes, dict[str, Any]]" = OrderedDict()
        self._sync_fatal_error: Optional[str] = None
        self._sync_block_stalled_reason: Optional[str] = None
        self._sync_last_validated_height = 0
        self._sync_peer_penalties: Dict[str, int] = {}
        self._sync_peer_penalty_events: dict[str, Deque[float]] = {}
        self._peer_exemptions = set(FORCE_SYNC_HEADER_PEERS)
        for force_remote in FORCE_SYNC_HEADER_PEERS:
            host = self._extract_host(force_remote)
            if host:
                self._peer_exemptions.add(host.lower())
        self._peer_exemptions.update(self._verifier_seed_ips)
        self._peer_exemptions.update(self._verifier_seed_hosts)
        self._sync_peer_penalty_whitelist = set(self._peer_exemptions)
        self._sync_last_progress_at = time.time()
        self._sync_last_inflight_reset_at = 0.0
        self._sync_last_head_height = 0
        self._sync_last_head_hash: Optional[str] = None
        self._sync_last_header_height = 0
        self._sync_last_block_fetch_height = 0
        self._sync_last_queue_depth = 0
        self._sync_last_header_at = 0.0
        self._sync_last_block_at = 0.0
        self._sync_last_header_request_at = 0.0
        self._sync_last_header_response_at = 0.0
        self._sync_last_header_response_count = 0
        self._sync_last_headers_accepted_count = 0
        self._sync_last_headers_discarded_count = 0
        self._sync_last_headers_discard_reason_counts: dict[str, int] = {}
        self._sync_last_locator_head_height: Optional[int] = None
        self._sync_last_locator_head_hash: Optional[bytes] = None
        self._sync_last_matched_ancestor_height: Optional[int] = None
        self._sync_last_matched_ancestor_hash: Optional[bytes] = None
        self._sync_last_anchor_check: Optional[dict[str, Any]] = None
        self._sync_headers_accepted_total = 0
        self._sync_headers_seen_total = 0
        self._sync_zero_accept_batches = 0
        self._sync_zero_accept_last_at = 0.0
        self._sync_last_block_request_at = 0.0
        self._sync_last_block_response_at = 0.0
        self._sync_last_block_download_at = 0.0
        self._sync_next_block_stable_hash: Optional[bytes] = None
        self._sync_next_block_stable_since = 0.0
        self._sync_last_header_request_peer: Optional[str] = None
        self._sync_last_header_response_peer: Optional[str] = None
        self._sync_last_header_error: Optional[str] = None
        self._sync_last_header_error_at: Optional[float] = None
        self._sync_last_header_error_peer: Optional[str] = None
        self._sync_active_header_peer: Optional[str] = None
        self._sync_active_block_peer: Optional[str] = None
        self._sync_block_peer_cursor = 0
        self._sync_inflight_headers = 0
        self._sync_peer_heads: Dict[str, _PeerHeadInfo] = {}
        self._sync_paused = False
        self._sync_enabled = _env_flag("SYNC_ENABLED", "ANIMICA_SYNC_ENABLED", default=True)
        self._sync_requested = False
        self._sync_requested_at: Optional[float] = None
        tick_ms = float(_env_value("SYNC_TICK_MS", "ANIMICA_SYNC_TICK_MS", default="5") or 5)  # Massively reduced from 25ms to 5ms for ultra-fast sync (hundreds-to-thousands blocks/sec)
        self._sync_tick_sec = max(MIN_SYNC_TICK_SEC, tick_ms / 1000.0)  # Use named constant for minimum
        self._sync_boost_until: Optional[float] = None
        self._sync_boost_tick_sec: Optional[float] = None
        self._sync_target_height: Optional[int] = None
        self._sync_max_inflight_headers = int(
            _env_value(
                "SYNC_MAX_INFLIGHT_HEADERS",
                "ANIMICA_SYNC_MAX_INFLIGHT_HEADERS",
                default="8192",  # Massively increased from 1024 for ultra-fast sync (hundreds-to-thousands blocks/sec)
            )
            or 4
        )
        self._sync_max_inflight = int(
            _env_value(
                "SYNC_MAX_INFLIGHT_BLOCKS",
                "ANIMICA_SYNC_MAX_INFLIGHT_BLOCKS",
                "ANIMICA_SYNC_INFLIGHT_BLOCKS",
                "ANIMICA_P2P_SYNC_INFLIGHT",
                default="16",
            )
            or 32
        )
        self._sync_max_inflight_per_peer = int(
            os.environ.get("ANIMICA_P2P_SYNC_INFLIGHT_PER_PEER", "2048") or 2048  # Massively increased from 512 for ultra-fast sync
        )
        self._sync_max_parallel_peers = int(
            os.environ.get("ANIMICA_SYNC_MAX_PEERS", "16") or 16
        )
        cpu_count = os.cpu_count() or 1
        default_verify_workers = min(max(4, cpu_count - 1), 16)
        self._sync_verify_workers = int(
            os.environ.get("ANIMICA_SYNC_VERIFY_WORKERS", str(default_verify_workers))
            or default_verify_workers
        )
        self._sync_verify_queue_limit = int(
            os.environ.get("ANIMICA_SYNC_VERIFY_QUEUE_LIMIT", "10000") or 10000
        )
        self._sync_db_batch_blocks = int(
            os.environ.get("ANIMICA_SYNC_DB_BATCH_BLOCKS", "1000") or 1000
        )
        self._sync_db_batch_blocks_current = max(1, self._sync_db_batch_blocks)
        self._sync_fast_mode_enabled = os.environ.get(
            "ANIMICA_SYNC_FAST_MODE", "1"
        ).lower() in ("1", "true", "yes", "on")
        self._sync_fast_mode_active = False
        self._sync_target_bps = float(
            os.environ.get("ANIMICA_SYNC_TARGET_BPS", "100") or 100
        )
        self._sync_headers_batch = int(
            os.environ.get("ANIMICA_P2P_SYNC_HEADERS_BATCH", "16384") or 16384  # Massively increased from 4096 for ultra-fast sync
        )
        self._sync_headers_batch_min = int(
            os.environ.get("ANIMICA_P2P_SYNC_HEADERS_BATCH_MIN", "256") or 256
        )
        self._sync_headers_batch_max = int(
            os.environ.get("ANIMICA_P2P_SYNC_HEADERS_BATCH_MAX", "16384") or 16384
        )
        if self._sync_headers_batch_max < self._sync_headers_batch:
            self._sync_headers_batch_max = self._sync_headers_batch
        if self._sync_headers_batch_min > self._sync_headers_batch_max:
            self._sync_headers_batch_min = self._sync_headers_batch_max
        self._sync_headers_batch_current = min(
            max(self._sync_headers_batch, self._sync_headers_batch_min),
            self._sync_headers_batch_max,
        )
        self._sync_request_timeout = float(
            os.environ.get("ANIMICA_P2P_SYNC_TIMEOUT", "8.0") or 8.0
        )
        self._sync_header_watchdog_timeout = float(
            os.environ.get("ANIMICA_P2P_HEADER_REQ_TIMEOUT", "10.0") or 10.0
        )
        self._sync_zero_accept_threshold = int(
            os.environ.get("ANIMICA_P2P_HEADERS_NO_PROGRESS_THRESHOLD", "2") or 2
        )
        self._sync_peer_penalty_threshold = int(
            os.environ.get("ANIMICA_P2P_SYNC_PENALTY_THRESHOLD", "6") or 6
        )
        self._sync_peer_penalty_window_s = float(
            os.environ.get("ANIMICA_P2P_SYNC_PENALTY_WINDOW", "600") or 600
        )
        self._sync_stall_timeout = float(
            _env_value(
                "SYNC_STALL_TIMEOUT_S",
                "ANIMICA_SYNC_STALL_TIMEOUT_S",
                "ANIMICA_P2P_SYNC_STALL_TIMEOUT",
                default="20.0",
            )
            or 20.0
        )
        self._sync_retry_limit = int(os.environ.get("ANIMICA_SYNC_RETRY_LIMIT", "3") or 3)
        self._sync_watchdog_timeout = float(
            os.environ.get("ANIMICA_SYNC_WATCHDOG_TIMEOUT_S", "60") or 60
        )
        self._sync_watchdog_last_height: int = 0
        self._sync_watchdog_last_hash: Optional[str] = None
        self._sync_watchdog_last_progress_at = time.time()
        self._sync_watchdog_last_action_at: float = 0.0
        self._sync_watchdog_attempts = 0
        self._snapshot_recovery_task: Optional[asyncio.Task] = None
        self._snapshot_recovery_last_attempt_at: float = 0.0
        self._snapshot_recovery_last_success_at: float = 0.0
        self._snapshot_recovery_last_error: Optional[str] = None
        self._snapshot_recovery_last_manifest_height: Optional[int] = None
        self._snapshot_recovery_last_manifest_hash: Optional[str] = None
        self._snapshot_recovery_last_manifest_url: Optional[str] = None
        self._snapshot_recovery_cooldown = float(
            os.environ.get("ANIMICA_SNAPSHOT_COOLDOWN_SECS", "1800") or 1800
        )
        self._snapshot_recovery_min_advance_blocks = int(
            os.environ.get("ANIMICA_SNAPSHOT_MIN_ADVANCE_BLOCKS", "500") or 500
        )
        self._snapshot_recovery_window_sec = float(
            os.environ.get("ANIMICA_SNAPSHOT_RECOVERY_WINDOW_SECS", "3600") or 3600
        )
        self._snapshot_recovery_max_per_window = int(
            os.environ.get("ANIMICA_SNAPSHOT_RECOVERY_MAX_PER_WINDOW", "2") or 2
        )
        self._snapshot_recovery_attempts: deque[float] = deque()
        self._snapshot_recovery_min_stall_recoveries = int(
            os.environ.get("ANIMICA_SNAPSHOT_MIN_STALL_RECOVERIES", "3") or 3
        )
        self._sync_tip_tolerance = int(
            os.environ.get("ANIMICA_P2P_SYNC_TIP_TOLERANCE", "2") or 2
        )
        self._sync_snapshot_threshold = int(
            os.environ.get("ANIMICA_SYNC_SNAPSHOT_THRESHOLD", "2000") or 2000
        )
        self._sync_force_always = os.environ.get(
            "ANIMICA_P2P_FORCE_SYNC_ALWAYS", "1"
        ).lower() in ("1", "true", "yes", "on")
        self._sync_status_cache: Optional[SyncStatusSnapshot] = None
        self._sync_status_cache_at = 0.0
        self._sync_status_cache_hits = 0
        self._sync_status_cache_refreshes = 0
        self._sync_status_cache_interval = float(
            os.environ.get("ANIMICA_SYNC_STATUS_CACHE_INTERVAL", "0.25") or 0.25
        )
        self._sync_cycle_log_interval = float(
            os.environ.get("ANIMICA_SYNC_LOG_INTERVAL", "5.0") or 5.0
        )
        self._sync_last_cycle_log_at = 0.0
        self._sync_block_queue_empty_log_interval = float(
            os.environ.get("ANIMICA_SYNC_EMPTY_QUEUE_LOG_INTERVAL", "15.0") or 15.0
        )
        self._sync_block_queue_empty_log_at = 0.0
        self._sync_cache: Optional[SyncCacheStore] = None
        self._sync_cache_state_interval = float(
            os.environ.get("ANIMICA_SYNC_CACHE_STATE_INTERVAL", "5") or 5
        )
        self._sync_cache_prune_interval = float(
            os.environ.get("ANIMICA_SYNC_CACHE_PRUNE_INTERVAL", "60") or 60
        )
        self._sync_cache_max_bytes = int(
            os.environ.get("ANIMICA_SYNC_CACHE_MAX_MB", "1024") or 1024  # Massively increased from 256MB to 1024MB for ultra-fast sync
        ) * 1024 * 1024
        self._sync_cache_max_blocks = int(
            os.environ.get("ANIMICA_SYNC_CACHE_MAX_BLOCKS", "10000") or 10000  # Massively increased from 2000 for ultra-fast sync
        )
        self._sync_cache_max_headers = int(
            os.environ.get("ANIMICA_SYNC_CACHE_MAX_HEADERS", "20000") or 20000  # Massively increased from 5000 for ultra-fast sync
        )
        self._sync_verify_queue: asyncio.Queue[_SyncVerifyTask] = asyncio.Queue(
            maxsize=self._sync_verify_queue_limit
        )
        self._sync_verify_tasks: list[asyncio.Task] = []
        self._sync_metrics_log_interval = float(
            os.environ.get("ANIMICA_SYNC_METRICS_INTERVAL", "2.0") or 2.0
        )
        self._sync_last_metrics_log_at = 0.0
        self._sync_metrics_window_s = float(
            os.environ.get("ANIMICA_SYNC_METRICS_WINDOW_S", "10") or 10
        )
        self._sync_metrics_block_received_events: Deque[float] = deque()
        self._sync_metrics_block_committed_events: Deque[float] = deque()
        self._sync_metrics_bytes_received_events: Deque[tuple[float, int]] = deque()
        self._sync_metrics_peer_block_events: dict[str, Deque[float]] = {}
        self._sync_metrics_verify_ms: Deque[float] = deque(maxlen=2000)
        self._sync_metrics_db_commit_ms: Deque[float] = deque(maxlen=2000)
        self._sync_metrics_db_batch_sizes: Deque[int] = deque(maxlen=2000)
        self._sync_metrics_fsync_count = 0
        self._sync_last_tuning_at = 0.0
        self._sync_cache_task: Optional[asyncio.Task] = None
        self._sync_last_reorg_at: Optional[float] = None
        self._sync_locator_backtrack_threshold = int(
            os.environ.get("ANIMICA_P2P_LOCATOR_BACKTRACK_THRESHOLD", "128") or 128
        )
        self._load_bootstrap_checkpoint()
        self._load_sync_cache_state()
        self._refresh_locator_summary()
        self._bootstrap_attempts: deque[dict[str, Any]] = deque(maxlen=512)
        self._last_bootstrap_attempt: Optional[dict[str, Any]] = None
        self._last_bootstrap_success: Optional[dict[str, Any]] = None
        self._last_bootstrap_error: Optional[dict[str, Any]] = None
        self._last_peer_connect_at: Optional[float] = None
        self._last_peer_disconnect_at: Optional[float] = None
        self._no_peers_since: Optional[float] = None
        self._no_peers_last_log_at: float = 0.0
        self._no_peers_grace_s = float(
            os.environ.get("ANIMICA_P2P_NO_PEERS_GRACE", "30") or 30
        )
        self._no_peers_log_interval_s = float(
            os.environ.get("ANIMICA_P2P_NO_PEERS_LOG_INTERVAL", "60") or 60
        )
        self._disconnect_reason_window_s = float(
            os.environ.get("ANIMICA_P2P_DISCONNECT_REASON_WINDOW", "600") or 600
        )
        self._disconnect_reason_events: Deque[tuple[float, str]] = deque()
        self._dial_attempt_log: Deque[dict[str, Any]] = deque(maxlen=20)
        self._handshake_failures_by_reason: dict[str, int] = {}
        self._caps_failures_by_reason: dict[str, int] = {}
        self._invalid_seed_addrs: set[str] = set()

        class _Metrics:
            def __init__(self, svc: "P2PService") -> None:
                self._svc = svc

            @property
            def peer_count(self) -> int:
                return int(self._svc._stats.get("peers", 0))

        self.metrics = _Metrics(self)
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    # ---------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------

    def _create_child_task(
        self, coro: Awaitable[Any], *, name: str
    ) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._child_tasks.add(task)

        def _discard(t: asyncio.Task) -> None:
            self._child_tasks.discard(t)
            with contextlib.suppress(asyncio.CancelledError):
                exc = t.exception()
                if exc is not None:
                    log.warning("Child task %s failed: %s", t.get_name(), exc, exc_info=True)

        task.add_done_callback(_discard)
        return task

    def _start_verify_workers(self) -> None:
        self._prune_verify_workers()
        worker_count = max(1, int(self._sync_verify_workers))
        while len(self._sync_verify_tasks) < worker_count:
            idx = len(self._sync_verify_tasks)
            task = asyncio.create_task(
                self._verify_worker_loop(idx),
                name=f"p2p.sync.verify[{idx}]",
            )
            self._sync_verify_tasks.append(task)

    def _prune_verify_workers(self) -> None:
        if not self._sync_verify_tasks:
            return
        alive: list[asyncio.Task] = []
        for task in self._sync_verify_tasks:
            if not task.done():
                alive.append(task)
                continue
            exc: Optional[BaseException] = None
            with contextlib.suppress(asyncio.CancelledError):
                exc = task.exception()
            if exc is not None:
                log.warning(
                    "Sync verify worker exited unexpectedly",
                    extra={"worker": task.get_name(), "error": repr(exc)},
                )
        self._sync_verify_tasks = alive

    async def _stop_verify_workers(self) -> None:
        if not self._sync_verify_tasks:
            return
        for task in self._sync_verify_tasks:
            task.cancel()
        await asyncio.gather(*self._sync_verify_tasks, return_exceptions=True)
        self._sync_verify_tasks.clear()

    async def _verify_worker_loop(self, idx: int) -> None:
        _ = idx
        try:
            while self._running:
                first = await self._sync_verify_queue.get()
                tasks = [first]
                batch_limit = max(1, int(self._sync_db_batch_blocks_current))
                while len(tasks) < batch_limit:
                    try:
                        tasks.append(self._sync_verify_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                for task in tasks:
                    try:
                        start = time.time()
                        ok = False
                        reason = None
                        try:
                            ok, reason = await self._import_block_payload(
                                task.sync_block.block, origin_remote=task.peer_remote
                            )
                        except Exception as exc:
                            ok = False
                            reason = f"verify_error:{exc}"
                        duration_ms = (time.time() - start) * 1000
                        self._record_verify_metrics(duration_ms, batch_size=len(tasks))
                        await self._finalize_block_import(
                            task.sync_block,
                            peer_remote=task.peer_remote,
                            ok=ok,
                            reason=reason,
                        )
                    except Exception as exc:
                        self._handle_verify_task_failure(task, exc)
                    finally:
                        self._sync_verify_queue.task_done()
        except asyncio.CancelledError:
            return

    def _handle_verify_task_failure(
        self, task: _SyncVerifyTask, exc: BaseException
    ) -> None:
        block_hash = task.sync_block.hash
        self._sync_inflight_blocks.pop(block_hash, None)
        self._sync_inflight_peers.pop(block_hash, None)
        self._sync_inflight_block_requests.pop(block_hash, None)
        if not self._has_block(block_hash):
            if block_hash not in self._sync_block_queue_set:
                self._sync_block_queue.appendleft(block_hash)
                self._sync_block_queue_set.add(block_hash)
                height_hint = self._block_height_hint(block_hash)
                if height_hint is not None:
                    self._sync_block_queue_heights[block_hash] = height_hint
        else:
            self._sync_block_retry_counts.pop(block_hash, None)
        self._sync_last_block_error = f"verify_worker_error:{exc.__class__.__name__}"
        self._sync_last_block_error_at = time.time()
        self._sync_last_block_error_peer = task.peer_remote
        self._sync_block_stalled_reason = STALL_BLOCK_INVALID_RESPONSE
        log.warning(
            "Sync verify task failed; block requeued",
            extra={
                "remote": task.peer_remote,
                "hash": block_hash.hex(),
                "error": repr(exc),
            },
        )
        self._sync_wakeup.set()

    def _bind_mempool_callback(self) -> bool:
        if not self._tx_relay_enabled or not self._p2p_tx_enabled:
            return False
        if self._mempool_callback_bound:
            return True
        mempool_service = None
        try:
            from rpc.methods import tx as tx_methods

            mempool_service = tx_methods._get_mempool_service()  # type: ignore[attr-defined]
        except Exception:
            mempool_service = None
        if mempool_service is None:
            try:
                from rpc.mempool_service import get_mempool_service_singleton

                mempool_service = get_mempool_service_singleton()
            except Exception:
                mempool_service = None
        if mempool_service is None:
            return False
        if mempool_service is None or not hasattr(
            mempool_service, "set_p2p_broadcast_callback"
        ):
            return False
        try:
            pending_path = getattr(mempool_service, "_persist_path", None)
            log.info(
                "Tx relay mempool binding",
                extra={
                    "chain_id": self.chain_id,
                    "mempool_id": hex(id(mempool_service)),
                    "pending_path": str(pending_path) if pending_path else None,
                },
            )
            log.info(f"[DIAG] Binding mempool callback to TxRelayService.on_mempool_add")
            mempool_service.set_p2p_broadcast_callback(
                self._txrelay.on_mempool_add,
                loop=self.loop,
            )
            if hasattr(mempool_service, "set_instant_block_callback"):
                mempool_service.set_instant_block_callback(
                    self._on_mempool_tx_accepted_instant,
                    loop=self.loop,
                )
            self._mempool_callback_bound = True
            log.info(
                "P2P broadcast callback registered",
                extra={"mempool_id": hex(id(mempool_service))},
            )
            log.info(f"[DIAG] Mempool callback bound successfully")
            return True
        except Exception as e:
            log.warning(
                "Failed to set P2P broadcast callback",
                extra={"error": str(e)},
            )
            return False

    async def _mempool_bind_loop(self) -> None:
        try:
            while self._running and not self._mempool_callback_bound:
                if self._bind_mempool_callback():
                    return
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    async def start(self) -> None:
        if self._running:
            return
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        self._running = True
        self._sync_paused = False
        self._bind_mempool_callback()
        if not self._mempool_callback_bound:
            self._mempool_bind_task = asyncio.create_task(
                self._mempool_bind_loop(), name="p2p.mempool_bind"
            )
        await self._maybe_detect_external_ip()

        if self._peerstore_fallback_path:
            fallback_snapshot = self._peerstore_fallback_path / "peers.json"
            merged = merge_peer_files(self._peers_json_path, [fallback_snapshot])
            if merged:
                log.info("Merged fallback peer snapshot into primary store")

        if self._ban_enabled:
            self._load_banlist()
        else:
            self._banlist.clear()

        if self._tx_inv_reannounce_interval_s > 0:
            self._tx_rebroadcast_task = self._create_child_task(
                self._tx_rebroadcast_supervisor(),
                name="p2p.pending_tx_rebroadcast",
            )

        # Persist configured seeds so a restarted node reuses them immediately
        if self.seeds:
            self._seed_peerstore(self.seeds)
        self._load_addrman_from_peerstore()
        for addr in self._advertised_addrs():
            self._addrman.add(addr)
        discovered = await self._discover_seed_peers()
        if discovered:
            self._seed_peerstore(discovered)
            for addr in discovered:
                if addr not in self.seeds:
                    self.seeds.append(addr)
            self._seed_keys.update({self._addr_key(a).strip().lower() for a in discovered})
            self._seed_sources.setdefault("discovery", []).extend(discovered)
        log.info(
            "Loaded %d seed(s)",
            len(self.seeds),
            extra={"seed_sources": self._seed_sources},
        )

        # Listen
        for ma in self.listen_addrs:
            parsed = parse_multiaddr(ma)
            if parsed.transport != "tcp":
                continue
            host = parsed.host or "0.0.0.0"
            port = int(parsed.port or 0)
            cfg = ListenConfig(
                addr=f"tcp://{host}:{port}", max_frame_bytes=8 * 1024 * 1024
            )
            await self._transport.listen(cfg)

        self._start_verify_workers()
        self._tasks = [
            asyncio.create_task(self._accept_loop(), name="p2p.accept"),
            asyncio.create_task(self._dial_loop(), name="p2p.dial"),
            asyncio.create_task(self._head_watch_loop(), name="p2p.head_watch"),
            asyncio.create_task(self._sync_loop(), name="p2p.sync"),
            *(
                [asyncio.create_task(self._sync_cache_loop(), name="p2p.sync_cache")]
                if self._sync_cache is not None
                else []
            ),
            asyncio.create_task(self._addr_request_loop(), name="p2p.addr_request"),
            asyncio.create_task(self._feeler_loop(), name="p2p.feeler"),
            asyncio.create_task(self._addr_relay_loop(), name="p2p.addr_relay"),
            asyncio.create_task(self._persist_peers_loop(), name="p2p.peer_persist"),
            *(
                [asyncio.create_task(self._persist_banlist_loop(), name="p2p.ban_persist")]
                if self._ban_enabled
                else []
            ),
            asyncio.create_task(self._score_decay_loop(), name="p2p.score_decay"),
            asyncio.create_task(self._metrics_loop(), name="p2p.metrics"),
            asyncio.create_task(self._startup_sync_kick(), name="p2p.startup_sync"),
        ]
        if self._tx_relay_enabled and self._p2p_tx_enabled:
            log.info(f"[DIAG] Starting txrelay background tasks: tx_relay_enabled={self._tx_relay_enabled}, p2p_tx_enabled={self._p2p_tx_enabled}")
            try:
                self._txrelay_inv_flush_task = asyncio.create_task(
                    self._txrelay.inv_flush_loop(), name="p2p.txrelay.inv_flush"
                )
                self._txrelay_inflight_task = asyncio.create_task(
                    self._txrelay.inflight_timeout_loop(), name="p2p.txrelay.inflight"
                )
                self._txrelay_sync_task = asyncio.create_task(
                    self._txrelay.mempool_sync_loop(), name="p2p.txrelay.mempool_sync"
                )
                self._txrelay_watchdog_task = asyncio.create_task(
                    self._txrelay.mempool_watchdog_loop(),
                    name="p2p.txrelay.mempool_watchdog",
                )
                self._txrelay_reconcile_task = asyncio.create_task(
                    self._txrelay.reconcile_loop(), name="p2p.txrelay.reconcile"
                )
                self._txrelay_inv_watchdog_task = asyncio.create_task(
                    self._txrelay.inv_queue_watchdog_loop(), name="p2p.txrelay.inv_watchdog"
                )
                self._tasks.extend(
                    [
                        self._txrelay_inv_flush_task,
                        self._txrelay_inflight_task,
                        self._txrelay_sync_task,
                        self._txrelay_watchdog_task,
                        self._txrelay_reconcile_task,
                        self._txrelay_inv_watchdog_task,
                    ]
                )
            except Exception as exc:
                log.warning(
                    "Tx relay background tasks failed; disabling tx relay",
                    extra={"error": str(exc)},
                    exc_info=True,
                )
                self._tx_relay_enabled = False
                self._tx_relay_v2_enabled = False
        if self._mempool_bind_task is not None:
            self._tasks.append(self._mempool_bind_task)
        self._sync_wakeup.set()
        log.info(
            "P2P started",
            extra={
                "peer_id": self._peer_id_bytes.hex(),
                "chain_id": self.chain_id,
                "listen_addrs": self.listen_addrs,
                "seeds": len(self.seeds),
                "peerstore": str(self.peerstore.path),
            },
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._txrelay._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._stop_verify_workers()

        for t in list(self._child_tasks):
            t.cancel()
        if self._child_tasks:
            await asyncio.gather(*self._child_tasks, return_exceptions=True)
            self._child_tasks.clear()

        async with self._peer_lock:
            peers = list(self._peers.values())
            self._peers.clear()
            self._stats["peers"] = 0

        for p in peers:
            with contextlib.suppress(Exception):
                await p.conn.close()
            if p.peer_id:
                with contextlib.suppress(Exception):
                    self.peerstore.record_disconnection(p.peer_id, reason="shutdown")

        with contextlib.suppress(Exception):
            await self._transport.close()

        self._flush_sync_cache_state()
        log.info("P2P stopped")

    # ---------------------------------------------------------------------
    # Public API (RPC/CLI)
    # ---------------------------------------------------------------------

    @property
    def peers(self) -> Dict[str, Dict[str, Any]]:
        # Deduplicated view sourced from the peer registry
        return {
            snap.get("remote", f"session:{idx}"): snap
            for idx, snap in enumerate(self._peer_registry.snapshot())
        }

    @property
    def peer_registry(self) -> PeerRegistry:
        return self._peer_registry

    def peer_stats_snapshot(self) -> list[dict[str, Any]]:
        stats: list[dict[str, Any]] = []
        now = time.time()
        for peer in list(self._peers.values()):
            stats.append(
                {
                    "remote": peer.remote,
                    "peer_id": peer.peer_id,
                    "direction": peer.direction,
                    "score": peer.misbehavior_score,
                    "invalid_headers": peer.invalid_headers,
                    "invalid_blocks": peer.invalid_blocks,
                    "invalid_msgs": peer.invalid_msgs,
                    "timeouts": peer.timeouts,
                    "score": peer.score,
                    "blocks_served": peer.blocks_served,
                    "last_block_time": peer.last_block_time,
                    "trusted": self._is_trusted_peer(peer),
                    "notfound": peer.notfound,
                    "missing_parent": peer.missing_parent,
                    "stall_events": peer.stall_events,
                    "connected_at": peer.connected_at,
                    "last_msg_at": peer.last_msg_at,
                    "last_progress_at": peer.last_progress_at,
                    "ban_until": peer.ban_until,
                    "latency_ms": round(peer.latency_ewma * 1000, 2)
                    if peer.latency_ewma is not None
                    else None,
                    "netgroup": peer.netgroup,
                    "is_banned": self._is_banned(peer.remote, now=now),
                }
            )
        return stats

    def banlist_snapshot(self) -> list[dict[str, Any]]:
        if not self._ban_enabled:
            return []
        now = time.time()
        bans = []
        for key, info in list(self._banlist.items()):
            until = info.get("ban_until")
            try:
                until_f = float(until)
            except (TypeError, ValueError):
                continue
            if until_f <= now:
                continue
            bans.append(
                {
                    "key": key,
                    "ban_until": until_f,
                    "reason": info.get("reason"),
                    "score": info.get("score"),
                }
            )
        return bans

    def ban_peer(self, key: str, *, ttl_s: float, reason: str = "manual") -> None:
        if not self._ban_enabled:
            return
        if self._is_peer_exempt(key):
            log.info(
                "Skipping manual ban for exempt peer",
                extra={"key": key, "reason": reason},
            )
            return
        until = time.time() + max(0.0, float(ttl_s))
        self._banlist[str(key)] = {"ban_until": until, "reason": reason, "score": None}
        self._banlist_event.set()

    def unban_peer(self, key: str) -> None:
        if not self._ban_enabled:
            return
        self._banlist.pop(str(key), None)
        self._banlist_event.set()

    def penalize_peer(
        self, peer: _PeerState, reason: str, points: int, *, ban_ttl: float | None = None
    ) -> None:
        self._apply_misbehavior(peer, reason, points=points, ban_ttl=ban_ttl)

    def decay_scores(self) -> None:
        if self._misbehavior_decay_points <= 0:
            return
        for peer in list(self._peers.values()):
            if peer.misbehavior_score <= 0:
                continue
            peer.misbehavior_score = max(
                0, peer.misbehavior_score - self._misbehavior_decay_points
            )
            self._update_peer_meta(peer)
        self._last_score_decay_at = time.time()

    def _parse_seed_env(self, raw: str | None) -> list[str]:
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _build_seed_sources(self, configured: list[str]) -> dict[str, list[str]]:
        sources: dict[str, list[str]] = {
            "defaults": list(DEFAULT_BOOTSTRAP_SEEDS),
        }
        env_seeds = []
        for env_name in ("ANIMICA_P2P_SEEDS", "P2P_SEEDS"):
            env_seeds.extend(self._parse_seed_env(os.environ.get(env_name)))
        if env_seeds:
            sources["env"] = env_seeds
        if configured:
            sources["config"] = list(configured)
        dns_seeds = [s for s in self.seeds if "/dns" in s]
        if dns_seeds:
            sources["dns"] = dns_seeds
        return sources

    def _env_list(self, name: str) -> list[str]:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _ensure_peerstore_dir(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            log.warning("Failed to ensure peerstore dir %s: %s", path, exc)
            return
        try:
            path.chmod(0o775)
        except Exception:
            return

    @staticmethod
    def _peer_key(remote: str, direction: str) -> tuple[str, str]:
        return (remote, direction)

    def _peer_by_remote(self, remote: Optional[str]) -> Optional[_PeerState]:
        if not remote:
            return None
        outbound = self._peers.get(self._peer_key(remote, "outbound"))
        if outbound is not None:
            return outbound
        inbound = self._peers.get(self._peer_key(remote, "inbound"))
        if inbound is not None:
            return inbound
        for key, peer in self._peers.items():
            if isinstance(key, tuple) and len(key) == 2:
                peer_remote = key[0]
            else:
                peer_remote = str(key)
            if peer_remote == remote:
                return peer
        return None

    def _peer_by_id(
        self, peer_id: Optional[str], *, direction: Optional[str] = None
    ) -> Optional[_PeerState]:
        if not peer_id:
            return None
        for peer in self._peers.values():
            if peer.peer_id == peer_id and (
                direction is None or peer.direction == direction
            ):
                return peer
        return None

    async def _maybe_detect_external_ip(self) -> None:
        if self._external_ip or not self._external_ip_endpoint:
            return
        endpoint = self._external_ip_endpoint
        try:
            ip_text = await asyncio.to_thread(self._fetch_external_ip, endpoint)
        except Exception as exc:
            log.debug("External IP detection failed: %s", exc)
            return
        if not ip_text:
            return
        try:
            ipaddress.ip_address(ip_text)
        except ValueError:
            log.debug("External IP endpoint returned invalid ip: %s", ip_text)
            return
        self._external_ip = ip_text
        log.info("Detected external IP %s", ip_text)

    def _fetch_external_ip(self, endpoint: str) -> Optional[str]:
        with urlopen(endpoint, timeout=3.0) as resp:
            raw = resp.read()
        try:
            text = raw.decode().strip()
        except Exception:
            return None
        return text or None

    def _addr_events_last_minute(self, events: deque[float]) -> int:
        cutoff = time.time() - 60.0
        while events and events[0] < cutoff:
            events.popleft()
        return len(events)

    def _record_addr_learned(self, count: int) -> None:
        now = time.time()
        for _ in range(max(0, count)):
            self._addr_learned_events.append(now)

    def _record_addr_announced(self, count: int) -> None:
        now = time.time()
        for _ in range(max(0, count)):
            self._addr_announced_events.append(now)

    def _schedule_peer_persist(self) -> None:
        if not self._persist_peers_event.is_set():
            self._persist_peers_event.set()

    def _advertised_addrs(self) -> list[str]:
        addrs: list[str] = []
        for key in ("ANIMICA_P2P_ADVERTISED_ADDRS", "ANIMICA_P2P_ADVERTISE_ADDR"):
            for entry in self._env_list(key):
                normalized = self._normalize_seed(entry)
                if normalized:
                    addrs.append(normalized)
        if addrs:
            return list(dict.fromkeys(addrs))
        if self._external_ip:
            port = self._local_listen_port()
            normalized = self._normalize_seed(f"{self._external_ip}:{port}")
            if normalized:
                addrs.append(normalized)
            return list(dict.fromkeys(addrs))
        for addr in self.listen_addrs:
            try:
                parsed = parse_multiaddr(addr)
                host = parsed.host or ""
                if host in {"0.0.0.0", "::"}:
                    continue
                try:
                    ip_obj = ipaddress.ip_address(host)
                except ValueError:
                    ip_obj = None
                if ip_obj is not None:
                    if ip_obj.is_loopback:
                        continue
                    if ip_obj.is_private and not self._allow_private_addrs:
                        continue
                if parsed.transport == "tcp" and parsed.port:
                    normalized = self._normalize_seed(f"{host}:{parsed.port}")
                    if normalized:
                        addrs.append(normalized)
            except Exception:
                continue
        return list(dict.fromkeys(addrs))

    def _listen_ports(self) -> set[int]:
        ports: set[int] = set()
        for addr in self.listen_addrs:
            try:
                parsed = parse_multiaddr(addr)
            except Exception:
                continue
            if parsed.transport != "tcp":
                continue
            if parsed.port:
                with contextlib.suppress(TypeError, ValueError):
                    port = int(parsed.port)
                    if 1 <= port <= 65535:
                        ports.add(port)
        if not ports:
            ports.add(self._local_listen_port())
        return ports

    def _self_endpoints(self) -> list[tuple[str, int]]:
        endpoints: list[tuple[str, int]] = []
        for addr in self._advertised_addrs():
            parsed = self._normalize_peer_addr(
                addr, fallback_port=self._local_listen_port()
            )
            if parsed.addr and parsed.addr.host and parsed.addr.port:
                endpoints.append((parsed.addr.host, int(parsed.addr.port)))
        for addr in self.listen_addrs:
            try:
                parsed = parse_multiaddr(addr)
            except Exception:
                continue
            host = parsed.host or ""
            if host and parsed.transport == "tcp" and parsed.port:
                with contextlib.suppress(TypeError, ValueError):
                    endpoints.append((host, int(parsed.port)))
        if self._external_ip:
            endpoints.append((self._external_ip, self._local_listen_port()))
        return endpoints

    def _is_self_address(self, host: str, port: int) -> bool:
        if not host or not port:
            return False
        listen_ports = self._listen_ports()
        lowered = host.lower()
        if lowered == "localhost":
            return port in listen_ports
        try:
            ip_obj = ipaddress.ip_address(host)
        except ValueError:
            ip_obj = None
        if ip_obj is not None and ip_obj.is_loopback:
            return port in listen_ports
        for local_host, local_port in self._self_endpoints():
            if local_port != port:
                continue
            if local_host == host:
                return True
            try:
                local_ip = ipaddress.ip_address(local_host)
            except ValueError:
                local_ip = None
            if ip_obj is not None and local_ip is not None and ip_obj == local_ip:
                return True
        if self._external_ip:
            try:
                ext_ip = ipaddress.ip_address(self._external_ip)
            except ValueError:
                ext_ip = None
            if ext_ip is not None and ip_obj is not None and ip_obj == ext_ip:
                if port in listen_ports or self._is_ephemeral_port(port):
                    return True
        return False

    def _local_listen_port(self) -> int:
        for addr in self.listen_addrs:
            try:
                parsed = parse_multiaddr(addr)
            except Exception:
                continue
            if parsed.transport != "tcp":
                continue
            if parsed.port:
                try:
                    port = int(parsed.port)
                    if 1 <= port <= 65535:
                        return port
                except (TypeError, ValueError):
                    continue
        return int(os.environ.get("ANIMICA_P2P_TCP_PORT", DEFAULT_TCP_PORT))

    def _is_ephemeral_port(self, port: int) -> bool:
        return int(port) >= 49152

    def _is_routable_host(self, host: str) -> bool:
        lowered = host.lower()
        if lowered in {"localhost"}:
            return self._allow_private_addrs
        try:
            ip_obj = ipaddress.ip_address(host)
        except ValueError:
            return True
        if self._allow_private_addrs:
            return True
        if ip_obj.is_global:
            return True
        if ip_obj.is_private or ip_obj.is_loopback:
            return False
        if ip_obj.is_multicast or ip_obj.is_unspecified or ip_obj.is_reserved:
            return False
        if ip_obj.is_link_local:
            return False
        return False

    def _sanitize_peer_addr(
        self,
        address: str,
        *,
        fallback_port: int,
        source: Optional[str] = None,
    ) -> Optional[str]:
        if not address:
            return None
        result = self._normalize_peer_addr(
            address,
            fallback_port=fallback_port,
            source=source or "sanitize",
        )
        if not result.addr:
            return None
        host = result.addr.host
        port = result.addr.port
        if not host:
            return None
        if not self._is_routable_host(host):
            return None
        if not port or port <= 0 or port > 65535:
            port = fallback_port
        if self._is_self_address(host, port):
            return None
        if self._is_ephemeral_port(port) and fallback_port and port != fallback_port:
            port = fallback_port
        normalized = self._normalize_peer_addr(
            f"{host}:{port}",
            fallback_port=fallback_port,
            source=source or "sanitize",
        )
        return normalized.addr.canonical if normalized.addr else None

    def _remember_addr(self, addr: str) -> None:
        addr_key = self._addr_key(addr)
        now = time.time()
        self._addr_seen[addr_key] = now
        self._addr_seen.move_to_end(addr_key)
        while len(self._addr_seen) > self._addr_seen_cap:
            self._addr_seen.popitem(last=False)

    def _load_addrman_from_peerstore(self) -> None:
        try:
            for _, address, _ in self.peerstore.list_addresses(limit=5000):
                if address:
                    normalized = self._normalize_seed(address)
                    if normalized:
                        self._addrman.add(normalized, source="peerstore")
        except Exception:
            pass

        self._load_addrman_from_snapshot(self._peers_json_path, source="snapshot")
        if self._peerstore_fallback_path:
            fallback_snapshot = self._peerstore_fallback_path / "peers.json"
            self._load_addrman_from_snapshot(fallback_snapshot, source="fallback")

    def _load_addrman_from_snapshot(self, path: Path, *, source: str) -> None:
        if not path.exists():
            return
        data = read_peers_json(path)
        for peer in data.get("peers", []) or []:
            if not isinstance(peer, dict):
                continue
            addrs = peer.get("addrs") or []
            for addr in addrs:
                normalized = self._normalize_seed(str(addr))
                if not normalized:
                    continue
                self._addrman.add(
                    normalized,
                    source=peer.get("source") or source,
                    last_seen=peer.get("last_seen"),
                    last_success=peer.get("last_success"),
                    last_failure=peer.get("last_failure"),
                    failure_reason=peer.get("failure_reason"),
                    score=peer.get("score"),
                )

    async def _send_get_peers(self, peer: _PeerState) -> None:
        if not peer.hello_done.is_set():
            return
        now = time.time()
        last = self._addr_last_request.get(peer.session_id, 0.0)
        if now - last < self._addr_request_interval:
            return
        self._addr_last_request[peer.session_id] = now
        await self._send(peer, MsgID.GET_PEERS, GetPeers(max_peers=self._addr_request_max))

    def _peer_knows_addr(self, peer: _PeerState, addr: str) -> bool:
        key = self._addr_key(addr)
        ts = peer.known_addrs.get(key)
        if ts is None:
            return False
        if time.time() - ts > self._addr_peer_known_ttl:
            peer.known_addrs.pop(key, None)
            return False
        return True

    def _mark_peer_known(self, peer: _PeerState, addr: str) -> None:
        if not addr:
            return
        key = self._addr_key(addr)
        peer.known_addrs[key] = time.time()
        peer.known_addrs.move_to_end(key)
        while len(peer.known_addrs) > self._addr_peer_known_cap:
            peer.known_addrs.popitem(last=False)

    def _sample_addrs_for_peer(self, peer: _PeerState, *, limit: int) -> list[str]:
        if limit <= 0:
            return []
        exclude_keys = {self._addr_key(peer.remote)}
        exclude_keys.update(peer.known_addrs)
        candidates = self._addrman.sample(limit=limit * 3, exclude=set())
        results: list[str] = []
        for addr in candidates:
            if self._addr_key(addr) in exclude_keys:
                continue
            results.append(addr)
            if len(results) >= limit:
                break
        return results

    async def _send_addr_sample(
        self,
        peer: _PeerState,
        *,
        limit: int,
        include_advertised: bool = False,
    ) -> None:
        if not peer.hello_done.is_set():
            return
        addrs: list[str] = []
        if include_advertised:
            for addr in self._advertised_addrs():
                if not self._peer_knows_addr(peer, addr):
                    addrs.append(addr)
        addrs.extend(self._sample_addrs_for_peer(peer, limit=limit))
        addrs = list(dict.fromkeys(addrs))
        if not addrs:
            return
        await self._send(peer, MsgID.ADDRESS_ANNOUNCE, AddressAnnounce(addresses=addrs))
        for addr in addrs:
            self._mark_peer_known(peer, addr)
        self._record_addr_announced(len(addrs))

    async def _send_peer_exchange(self, peer: _PeerState, *, limit: int) -> None:
        if not peer.hello_done.is_set():
            return
        exclude = {self._addr_key(peer.remote)}
        entries = []
        for entry in self._collect_peer_entries(limit=limit, exclude=exclude):
            addr = entry.get("addr")
            if not isinstance(addr, str) or not addr:
                continue
            if self._peer_knows_addr(peer, addr):
                continue
            entry = dict(entry)
            entry["peer_id"] = hashlib.sha3_256(addr.encode()).digest()
            entries.append(entry)
            self._mark_peer_known(peer, addr)
        if not entries:
            return
        await self._send(peer, MsgID.PEERS, Peers(entries=entries))

    def _collect_peer_entries(
        self, *, limit: int, exclude: Optional[set[str]] = None
    ) -> list[dict[str, Any]]:
        exclude = exclude or set()
        records: dict[str, dict[str, Any]] = {}
        for rec in self._addrman.records():
            key = self._addr_key(rec.address)
            if key in exclude:
                continue
            # Coerce float timestamps/scores to int: the PEERS message is encoded
            # with the canonical CBOR encoder, which rejects floats (consensus
            # determinism). Integer unix-seconds are plenty for addr bookkeeping.
            records[key] = {
                "addr": rec.address,
                "last_seen": int(rec.last_seen or 0),
                "last_success": int(rec.last_success) if rec.last_success is not None else None,
                "last_failure": int(rec.last_failure) if rec.last_failure is not None else None,
                "failure_reason": rec.failure_reason,
                "score": int(rec.score or 0),
                "source": rec.source,
            }
        try:
            for peer_id, address, last_seen in self.peerstore.list_addresses(
                limit=limit
            ):
                if not address:
                    continue
                normalized = self._normalize_seed(address)
                if not normalized:
                    continue
                key = self._addr_key(normalized)
                if key in exclude or key in records:
                    continue
                records[key] = {
                    "addr": normalized,
                    "last_seen": int(last_seen or 0),
                    "score": 0,
                    "source": "peerstore",
                }
        except Exception:
            pass
        entries = list(records.values())
        entries.sort(
            key=lambda e: (
                float(e.get("score") or 0.0),
                float(e.get("last_seen") or 0.0),
            ),
            reverse=True,
        )
        return entries[:limit]

    def _ingest_peer_entries(
        self,
        entries: list[dict[str, Any]],
        *,
        source: str,
        source_peer: Optional[_PeerState] = None,
    ) -> int:
        if not entries:
            return 0
        now = time.time()
        rate: Optional[Deque[float]] = None
        if source_peer is not None:
            rate = self._peer_addr_rate.setdefault(source_peer.session_id, deque())
            cutoff = now - self._peer_addr_rate_window
            while rate and rate[0] < cutoff:
                rate.popleft()
            if len(rate) >= self._peer_addr_rate_limit:
                log.debug(
                    "Rate-limiting peer addr intake",
                    extra={"peer": source_peer.remote, "source": source},
                )
                return 0
        stored = 0
        fallback_port = self._local_listen_port()
        for entry in entries:
            if source_peer is not None and rate is not None and len(rate) >= self._peer_addr_rate_limit:
                break
            addr = entry.get("addr") or entry.get("address")
            if not isinstance(addr, str) or not addr:
                continue
            source_label = entry.get("source") or source
            normalized = self._sanitize_peer_addr(
                addr,
                fallback_port=fallback_port,
                source=str(source_label) if source_label else None,
            )
            if not normalized:
                continue
            self._remember_addr(normalized)
            last_seen = entry.get("last_seen")
            last_success = entry.get("last_success")
            last_failure = entry.get("last_failure")
            failure_reason = entry.get("failure_reason")
            score = entry.get("score")
            self._addrman.add(
                normalized,
                source=entry.get("source") or source,
                last_seen=last_seen if isinstance(last_seen, (int, float)) else None,
                last_success=(
                    float(last_success)
                    if isinstance(last_success, (int, float))
                    else None
                ),
                last_failure=(
                    float(last_failure)
                    if isinstance(last_failure, (int, float))
                    else None
                ),
                failure_reason=(
                    str(failure_reason) if isinstance(failure_reason, str) else None
                ),
                score=float(score) if isinstance(score, (int, float)) else None,
            )
            try:
                peer_id = self._peer_id_from_addr(normalized)
                self.peerstore.add(
                    peer_id=peer_id, addrs=[normalized], direction="outbound"
                )
                self.peerstore.record_seen(peer_id, normalized)
                stored += 1
            except Exception:
                continue
            if source_peer is not None:
                rate = self._peer_addr_rate.setdefault(source_peer.session_id, deque())
                rate.append(now)
        if stored:
            log.debug("Discovered %d peer(s) from %s", stored, source)
            self._record_addr_learned(stored)
            self._schedule_peer_persist()
        return stored

    async def _discover_seed_peers(self) -> list[str]:
        if os.environ.get("ANIMICA_P2P_ENABLE_DNS_SEEDS", "true").lower() in (
            "0",
            "false",
            "no",
            "off",
        ):
            return []
        dns_names = self._env_list("ANIMICA_P2P_SEEDS_DNS")
        https_urls = self._env_list("ANIMICA_P2P_SEEDS_HTTPS")
        try:
            from p2p.discovery import seeds as seed_discovery
        except Exception:
            return []
        try:
            if dns_names or https_urls:
                bundle = await seed_discovery.discover_all(
                    dns_names=dns_names,
                    https_urls=https_urls,
                    static_addrs=[],
                    resolve=True,
                    include_fallbacks=False,
                )
                source = "custom discovery"
            else:
                bundle = await seed_discovery.discover_for_network(
                    self.chain_id, resolve=True, include_fallbacks=False
                )
                source = "network discovery"
        except Exception as exc:
            log.debug("Seed discovery failed: %s", exc)
            return []
        discovered: list[str] = []
        for endpoint in bundle.endpoints:
            if getattr(endpoint, "scheme", "") != "tcp":
                continue
            host = getattr(endpoint, "host", "")
            port = getattr(endpoint, "port", None)
            if not host or not port:
                continue
            normalized = self._normalize_seed(f"{host}:{port}")
            if normalized:
                discovered.append(normalized)
        if discovered:
            log.info("Discovered %d seed(s) via %s", len(discovered), source)
        return list(dict.fromkeys(discovered))

    async def _addr_request_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._addr_request_interval)
                if not self._running:
                    return
                peers = list(self._peers_by_session.values())
                if not peers:
                    continue
                peer = random.choice(peers)
                await self._send_get_peers(peer)
        except asyncio.CancelledError:
            return

    async def _addr_relay_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._addr_relay_interval)
                if not self._running:
                    return
                peers = list(self._peers_by_session.values())
                for peer in peers:
                    await self._send_addr_sample(
                        peer,
                        limit=self._addr_relay_sample,
                        include_advertised=False,
                    )
        except asyncio.CancelledError:
            return

    async def _persist_peers_loop(self) -> None:
        try:
            while self._running:
                try:
                    await asyncio.wait_for(
                        self._persist_peers_event.wait(),
                        timeout=self._persist_peers_interval,
                    )
                except asyncio.TimeoutError:
                    pass
                if not self._running:
                    return
                if self._persist_peers_event.is_set():
                    self._persist_peers_event.clear()
                await self._persist_peers_snapshot()
        except asyncio.CancelledError:
            return

    async def _metrics_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(60.0)
                now = time.time()
                peer_count = self._peer_registry.peer_count()
                if peer_count == 0:
                    if self._no_peers_since is None:
                        self._no_peers_since = now
                    elapsed = now - self._no_peers_since
                    if (
                        elapsed >= self._no_peers_grace_s
                        and now - self._no_peers_last_log_at >= self._no_peers_log_interval_s
                    ):
                        self._no_peers_last_log_at = now
                        self._seeding_mode = True
                        self._dial_backoff.clear()
                        self._dial_inflight.clear()
                        if self.seeds:
                            self._seed_peerstore(self.seeds)
                        self._sync_wakeup.set()
                        log.warning(
                            "NO_PEERS: retrying seed/dial cycle",
                            extra={
                                "peer_count": peer_count,
                                "no_peers_for_s": round(elapsed, 2),
                                "disconnect_reasons": self._disconnect_reason_top(),
                                "seed_count": len(self.seeds),
                            },
                        )
                else:
                    self._no_peers_since = None
                addrman_size = self._addrman.size()
                learned_1m = self._addr_events_last_minute(self._addr_learned_events)
                announced_1m = self._addr_events_last_minute(self._addr_announced_events)
                log.info(
                    "P2P addr metrics",
                    extra={
                        "addrman_size": addrman_size,
                        "learned_addrs_1m": learned_1m,
                        "announced_addrs_1m": announced_1m,
                        "persisted_peer_count": self._persisted_peer_count,
                    },
                )
        except asyncio.CancelledError:
            return

    def _prune_metric_events(self, events: Deque[float], now: float) -> None:
        window = self._sync_metrics_window_s
        while events and now - events[0] > window:
            events.popleft()

    def _prune_metric_bytes(
        self, events: Deque[tuple[float, int]], now: float
    ) -> None:
        window = self._sync_metrics_window_s
        while events and now - events[0][0] > window:
            events.popleft()

    def _record_block_received_metrics(self, peer_remote: str, raw_len: int) -> None:
        now = time.time()
        self._sync_metrics_block_received_events.append(now)
        self._sync_metrics_bytes_received_events.append((now, raw_len))
        peer_events = self._sync_metrics_peer_block_events.setdefault(peer_remote, deque())
        peer_events.append(now)
        self._prune_metric_events(self._sync_metrics_block_received_events, now)
        self._prune_metric_bytes(self._sync_metrics_bytes_received_events, now)
        self._prune_metric_events(peer_events, now)

    def _record_block_committed_metrics(self) -> None:
        now = time.time()
        self._sync_metrics_block_committed_events.append(now)
        self._prune_metric_events(self._sync_metrics_block_committed_events, now)

    def _record_verify_metrics(self, duration_ms: float, *, batch_size: int) -> None:
        self._sync_metrics_verify_ms.append(duration_ms)
        self._sync_metrics_db_commit_ms.append(duration_ms)
        self._sync_metrics_db_batch_sizes.append(batch_size)

    def _metric_summary(self, samples: Deque[float]) -> dict[str, float]:
        if not samples:
            return {"avg": 0.0, "p50": 0.0, "p95": 0.0}
        values = sorted(samples)
        total = sum(values)
        count = len(values)
        def _pct(p: float) -> float:
            if not values:
                return 0.0
            idx = int(round((p / 100.0) * (count - 1)))
            return float(values[min(max(idx, 0), count - 1)])

        return {
            "avg": total / count,
            "p50": _pct(50.0),
            "p95": _pct(95.0),
        }

    def _metric_rate(self, events: Deque[float], now: float) -> float:
        self._prune_metric_events(events, now)
        window = max(1.0, self._sync_metrics_window_s)
        return len(events) / window

    def _metric_bytes_rate(self, now: float) -> float:
        self._prune_metric_bytes(self._sync_metrics_bytes_received_events, now)
        window = max(1.0, self._sync_metrics_window_s)
        total_bytes = sum(size for _, size in self._sync_metrics_bytes_received_events)
        return total_bytes / window

    def _prune_disconnect_reasons(self, now: float) -> None:
        window = max(1.0, self._disconnect_reason_window_s)
        while self._disconnect_reason_events and now - self._disconnect_reason_events[0][0] > window:
            self._disconnect_reason_events.popleft()

    def _record_disconnect_reason(self, reason: str) -> None:
        now = time.time()
        self._disconnect_reason_events.append((now, reason))
        self._prune_disconnect_reasons(now)
        self._stats["disconnects"] += 1
        inc_disconnect(reason)

    def _disconnect_reason_top(self, *, limit: int = 5) -> list[tuple[str, int]]:
        now = time.time()
        self._prune_disconnect_reasons(now)
        counts: dict[str, int] = {}
        for _, reason in self._disconnect_reason_events:
            counts[reason] = counts.get(reason, 0) + 1
        return sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]

    def _sync_metrics_snapshot(self) -> dict[str, Any]:
        now = time.time()
        best_header_height = self._sync_best_header.height if self._sync_best_header else 0
        head_height, _head_hash = self._local_head()
        committed_bps = self._metric_rate(self._sync_metrics_block_committed_events, now)
        received_bps = self._metric_rate(self._sync_metrics_block_received_events, now)
        bytes_per_s = self._metric_bytes_rate(now)
        peer_throughput = {}
        for remote, events in self._sync_metrics_peer_block_events.items():
            peer_throughput[remote] = self._metric_rate(events, now)
        return {
            "headers_tip": int(best_header_height),
            "blocks_tip": int(head_height or 0),
            "verify_tip": int(self._sync_last_validated_height),
            "download_inflight_blocks": len(self._sync_inflight_blocks),
            "trusted_seeds": list(self._trusted_seeds),
            "download_queue_depth": len(self._sync_block_queue),
            "verify_queue_depth": self._sync_verify_queue.qsize()
            + len(self._sync_block_buffer),
            "verify_workers": int(self._sync_verify_workers),
            "verify_ms_per_block": self._metric_summary(self._sync_metrics_verify_ms),
            "db_batch_size": self._metric_summary(
                deque(float(v) for v in self._sync_metrics_db_batch_sizes)
            ),
            "db_commit_ms": self._metric_summary(self._sync_metrics_db_commit_ms),
            "fsync_count": int(self._sync_metrics_fsync_count),
            "net_blocks_received_per_s": received_bps,
            "blocks_committed_per_s": committed_bps,
            "net_mb_per_s": bytes_per_s / (1024 * 1024),
            "peer_throughput": peer_throughput,
            "orphan_count": int(self._stats.get("blocks_orphaned", 0)),
            "reject_count": int(self._stats.get("blocks_rejected", 0)),
        }

    def _log_sync_metrics(self, now: float) -> None:
        if now - self._sync_last_metrics_log_at < self._sync_metrics_log_interval:
            return
        self._sync_last_metrics_log_at = now
        metrics = self._sync_metrics_snapshot()
        log.info("Sync metrics", extra=metrics)
        self._maybe_adjust_sync_tuning(metrics, now)

    def _maybe_adjust_sync_tuning(self, metrics: dict[str, Any], now: float) -> None:
        if now - self._sync_last_tuning_at < max(1.0, self._sync_metrics_log_interval):
            return
        self._sync_last_tuning_at = now
        committed_bps = float(metrics.get("blocks_committed_per_s") or 0.0)
        if self._sync_fast_mode_enabled:
            ahead = int(metrics.get("headers_tip") or 0) - int(metrics.get("blocks_tip") or 0)
            fast_active = ahead > 100
            if fast_active != self._sync_fast_mode_active:
                self._sync_fast_mode_active = fast_active
                self._sync_db_batch_blocks_current = (
                    max(1, self._sync_db_batch_blocks) if fast_active else 1
                )
                log.info(
                    "Sync fast mode toggled",
                    extra={
                        "active": self._sync_fast_mode_active,
                        "ahead_blocks": ahead,
                        "db_batch_blocks": self._sync_db_batch_blocks_current,
                    },
                )
        if committed_bps >= self._sync_target_bps:
            return
        inflight = len(self._sync_inflight_blocks)
        queue_depth = len(self._sync_block_queue)
        inflight_cap = max(
            self._sync_max_inflight,
            self._sync_max_inflight_per_peer * max(1, self._sync_max_parallel_peers),
        )
        if queue_depth > 0 and inflight < self._sync_max_inflight:
            self._sync_max_inflight = min(
                inflight_cap,
                max(self._sync_max_inflight + 256, int(self._sync_max_inflight * 1.1)),
            )
        if self._sync_verify_queue.qsize() > self._sync_verify_queue_limit * 0.9:
            self._sync_max_inflight = max(1, int(self._sync_max_inflight * 0.8))

    async def _persist_peers_snapshot(self) -> None:
        data = self._build_peers_snapshot()
        if not data:
            return
        ok = await asyncio.to_thread(self._write_peers_snapshot, data)
        if ok:
            self._persisted_peer_count = len(data.get("peers", []))

    def _load_banlist(self) -> None:
        if not self._ban_enabled:
            self._banlist.clear()
            return
        if not self._banlist_path.exists():
            return
        try:
            raw = self._banlist_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            log.warning("Failed to load banlist: %s", exc)
            return
        now = time.time()
        items = {}
        for entry in data.get("bans", []) if isinstance(data, dict) else []:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key") or "")
            until = entry.get("ban_until")
            if not key or until is None:
                continue
            try:
                until_f = float(until)
            except (TypeError, ValueError):
                continue
            if until_f <= now:
                continue
            items[key] = {
                "ban_until": until_f,
                "reason": entry.get("reason"),
                "score": entry.get("score"),
            }
        self._banlist = items

    async def _persist_banlist_loop(self) -> None:
        try:
            while self._running:
                try:
                    await asyncio.wait_for(
                        self._banlist_event.wait(), timeout=self._banlist_persist_interval
                    )
                except asyncio.TimeoutError:
                    pass
                if not self._running:
                    return
                if self._banlist_event.is_set():
                    self._banlist_event.clear()
                await asyncio.to_thread(self._persist_banlist)
        except asyncio.CancelledError:
            return

    def _persist_banlist(self) -> None:
        self._ensure_peerstore_dir(self._banlist_path.parent)
        now = time.time()
        bans = []
        for key, info in list(self._banlist.items()):
            until = info.get("ban_until")
            try:
                until_f = float(until)
            except (TypeError, ValueError):
                self._banlist.pop(key, None)
                continue
            if until_f <= now:
                self._banlist.pop(key, None)
                continue
            bans.append(
                {
                    "key": key,
                    "ban_until": until_f,
                    "reason": info.get("reason"),
                    "score": info.get("score"),
                }
            )
        data = {"bans": bans, "updated_at": now}
        tmp_name = f".{self._banlist_path.name}.{uuid.uuid4().hex}.tmp"
        tmp_path = self._banlist_path.parent / tmp_name
        try:
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.replace(tmp_path, self._banlist_path)
        except Exception as exc:
            log.warning("Failed to persist banlist: %s", exc)
            with contextlib.suppress(Exception):
                tmp_path.unlink()

    async def _score_decay_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(max(1.0, self._misbehavior_decay_interval))
                if not self._running:
                    return
                self.decay_scores()
        except asyncio.CancelledError:
            return

    def _build_peers_snapshot(self) -> dict[str, Any]:
        peers: dict[str, dict[str, Any]] = {}
        for record in self._addrman.records():
            peer_id = self._peer_id_from_addr(record.address)
            entry = peers.setdefault(
                peer_id,
                {
                    "peer_id": peer_id,
                    "addrs": [],
                    "score": record.score,
                    "last_seen": record.last_seen,
                    "last_success": record.last_success,
                    "last_failure": record.last_failure,
                    "failure_reason": record.failure_reason,
                    "source": record.source,
                    "connected": False,
                    "banned_until": None,
                    "tags": {},
                },
            )
            if record.address not in entry["addrs"]:
                entry["addrs"].append(record.address)
            entry["last_seen"] = max(entry["last_seen"], record.last_seen)
            entry["score"] = max(float(entry["score"]), float(record.score))
        try:
            for peer_id, address, last_seen in self.peerstore.list_addresses(limit=5000):
                if not address:
                    continue
                normalized = self._normalize_seed(address)
                if not normalized:
                    continue
                entry = peers.setdefault(
                    peer_id,
                    {
                        "peer_id": peer_id,
                        "addrs": [],
                        "score": 0.0,
                        "last_seen": last_seen,
                        "source": "peerstore",
                        "connected": False,
                        "banned_until": None,
                        "tags": {},
                    },
                )
                if normalized not in entry["addrs"]:
                    entry["addrs"].append(normalized)
                entry["last_seen"] = max(entry["last_seen"], last_seen)
        except Exception:
            pass
        return {"peers": list(peers.values())}

    def _write_peers_snapshot(self, data: dict[str, Any]) -> bool:
        path = self._peers_json_path
        self._ensure_peerstore_dir(path.parent)
        attempts = 3
        for attempt in range(attempts):
            tmp_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
            tmp_path = path.parent / tmp_name
            try:
                with tmp_path.open("w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2)
                os.replace(tmp_path, path)
                return True
            except Exception as exc:
                log.warning(
                    "Failed to persist peers.json (attempt %d/%d): %s",
                    attempt + 1,
                    attempts,
                    exc,
                )
                with contextlib.suppress(Exception):
                    tmp_path.unlink()
                time.sleep(0.2)
        return False

    async def _feeler_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._feeler_interval)
                if not self._running:
                    return
                candidate = None
                try:
                    known = self.peerstore.list_known(limit=64, order_by="last_seen")
                except Exception:
                    known = []
                random.shuffle(known)
                async with self._peer_lock:
                    active_keys = {self._addr_key(p.remote) for p in self._peers.values()}
                now = time.time()
                for peer in known:
                    addr = getattr(peer, "address", None)
                    if not isinstance(addr, str) or not addr:
                        continue
                    addr_key = self._addr_key(addr)
                    if addr_key in active_keys:
                        continue
                    if addr_key in self._dial_inflight:
                        continue
                    if self._dial_backoff.get(addr_key, 0.0) > now:
                        continue
                    candidate = addr
                    break
                if candidate:
                    self._dial_inflight.add(self._addr_key(candidate))
                    self._create_child_task(
                        self._dial(candidate, feeler=True),
                        name=f"p2p.feeler@{candidate}",
                    )
        except asyncio.CancelledError:
            return

    def _record_bootstrap_attempt(
        self,
        addr: str,
        *,
        success: bool,
        error: Optional[str] = None,
        record_error: bool = True,
    ) -> None:
        now = time.time()
        entry = {
            "at": now,
            "addr": addr,
            "success": success,
        }
        if error:
            entry["error"] = error
        self._bootstrap_attempts.append(entry)
        self._last_bootstrap_attempt = entry
        if success:
            self._last_bootstrap_success = entry
        elif record_error:
            self._last_bootstrap_error = entry

    def _seed_attempts_recent(self, addr_key: str) -> int:
        window = float(self._bootstrap_seed_rate_window)
        now = time.time()
        return sum(
            1
            for entry in self._bootstrap_attempts
            if now - float(entry.get("at", 0)) <= window
            and self._addr_key(str(entry.get("addr", ""))) == addr_key
        )

    def _new_conn_trace_id(self) -> str:
        return uuid.uuid4().hex

    def _record_dial_attempt(
        self,
        *,
        addr: str,
        stage: str,
        success: bool,
        reason: Optional[str] = None,
        conn_trace_id: Optional[str] = None,
    ) -> None:
        self._dial_attempt_log.append(
            {
                "at": time.time(),
                "addr": addr,
                "stage": stage,
                "success": success,
                "reason": reason,
                "conn_trace_id": conn_trace_id,
            }
        )

    def _record_handshake_failure(self, reason: str) -> None:
        self._stats["handshake_failures"] += 1
        self._handshake_failures_by_reason[reason] = (
            self._handshake_failures_by_reason.get(reason, 0) + 1
        )
        inc_handshake_failure(reason)

    def _record_caps_failure(self, reason: str) -> None:
        self._stats["caps_failures"] += 1
        self._caps_failures_by_reason[reason] = (
            self._caps_failures_by_reason.get(reason, 0) + 1
        )
        inc_caps_failure(reason)

    def _dial_delay(self, addr_key: str) -> float:
        attempts = self._dial_attempts.get(addr_key, 0)
        base = 2.0 * (2 ** min(attempts, 5))
        jitter = random.uniform(0.6, 1.4)
        return min(60.0, base * jitter)

    def _dial_delay_for_error(self, addr_key: str, error: str) -> float:
        delay = self._dial_delay(addr_key)
        lowered = error.lower()
        if "connectionrefusederror" in lowered or "econnrefused" in lowered:
            delay = min(300.0, delay * 4.0)
        return delay

    def _is_invalid_seed_error(self, error: str) -> bool:
        lowered = error.lower()
        return "invalid handshake magic" in lowered or "handshakeerror" in lowered

    def _mark_dial_failure(
        self,
        addr: str,
        *,
        is_seed: bool,
        error: str,
        stage: str = "tcp",
        conn_trace_id: Optional[str] = None,
    ) -> None:
        addr_key = self._addr_key(addr)
        attempts = self._dial_attempts.get(addr_key, 0) + 1
        self._dial_attempts[addr_key] = attempts
        self._dial_last_error = {
            "addr": addr,
            "error": error,
            "attempts": attempts,
            "at": time.time(),
            "stage": stage,
        }
        self._record_dial_attempt(
            addr=addr,
            stage=stage,
            success=False,
            reason=error,
            conn_trace_id=conn_trace_id,
        )
        log.info(
            "P2P_DIAL_FAIL",
            extra={
                "conn_trace_id": conn_trace_id,
                "peer_addr": addr,
                "stage": stage,
                "reason": error,
            },
        )
        delay = self._dial_delay_for_error(addr_key, error)
        next_retry = time.time() + delay
        self._dial_backoff[addr_key] = next_retry
        normalized = self._sanitize_peer_addr(addr, fallback_port=self._local_listen_port())
        if normalized:
            self._addrman.mark_failure(normalized, reason=error)
        if self._is_invalid_seed_error(error):
            self._invalid_seed_addrs.add(addr)
            if is_seed:
                with contextlib.suppress(ValueError):
                    self.seeds.remove(addr)
                self._seed_keys.discard(addr_key.strip().lower())
            log.warning("Dropping invalid P2P endpoint %s: %s", addr, error)
            return
        if is_seed:
            recent_success = False
            last_success = self._last_bootstrap_success
            if isinstance(last_success, dict):
                try:
                    recent_success = time.time() - float(last_success.get("at", 0)) <= 600
                except (TypeError, ValueError):
                    recent_success = False
            self._record_bootstrap_attempt(
                addr, success=False, error=error, record_error=not recent_success
            )
            log.warning(
                "Seed %s failed: %s; next retry in %.1fs", addr, error, delay
            )
        else:
            log.info("Dial to %s failed: %s (retry in %.1fs)", addr, error, delay)
            if attempts >= 3:
                fallback_port = self._local_listen_port()
                normalized = self._sanitize_peer_addr(addr, fallback_port=fallback_port)
                if normalized:
                    peer_id = self._peer_id_from_addr(normalized)
                    with contextlib.suppress(Exception):
                        self.peerstore.increment_score(peer_id, -1.0)
                        self.peerstore.record_seen(peer_id, normalized)

    def _mark_dial_success(
        self, addr: str, *, is_seed: bool, conn_trace_id: Optional[str] = None
    ) -> None:
        addr_key = self._addr_key(addr)
        self._dial_attempts.pop(addr_key, None)
        self._dial_backoff.pop(addr_key, None)
        self._dial_success_total += 1
        self._record_dial_attempt(
            addr=addr,
            stage="success",
            success=True,
            conn_trace_id=conn_trace_id,
        )
        normalized = self._sanitize_peer_addr(addr, fallback_port=self._local_listen_port())
        if normalized:
            self._addrman.mark_success(normalized)
        if is_seed:
            self._record_bootstrap_attempt(addr, success=True)
            log.info("Seed %s handshake complete", addr)
            self._sync_wakeup.set()

    def bootstrap_peer_bonus(self) -> int:
        last = self._last_bootstrap_success
        if not last:
            return 0
        addr = last.get("addr") if isinstance(last, dict) else None
        if not addr:
            return 0
        try:
            at = float(last.get("at", 0))
        except (TypeError, ValueError):
            at = 0.0
        if at and time.time() - at > 600:
            return 0
        seed_key = self._addr_key(str(addr))
        active_keys = {
            self._addr_key(str(p.get("remote", "")))
            for p in self._peer_registry.snapshot()
            if p.get("remote")
        }
        if seed_key in active_keys:
            return 0
        return 1

    def status_snapshot(self) -> P2PStatusSnapshot:
        snapshot = self._peer_registry.snapshot()
        inbound = sum(1 for p in snapshot if p.get("direction") == "inbound")
        outbound = sum(1 for p in snapshot if p.get("direction") == "outbound")
        bootstrap_bonus = self.bootstrap_peer_bonus()
        now = time.time()
        attempts_last_5m = sum(
            1 for entry in self._bootstrap_attempts if now - entry.get("at", 0) <= 300
        )
        addrman_size = self._addrman.size()
        learned_1m = self._addr_events_last_minute(self._addr_learned_events)
        announced_1m = self._addr_events_last_minute(self._addr_announced_events)
        outbound_target = max(
            int(os.environ.get("ANIMICA_P2P_OUTBOUND", "8") or 8), self._min_outbound
        )
        outbound_enabled = outbound_target > 0

        return P2PStatusSnapshot(
            p2p_running=self._running,
            listen_addrs=list(self.listen_addrs),
            bound_listen_addrs=self._transport.addresses(),
            peers_total=len(snapshot) + bootstrap_bonus,
            peers_inbound=inbound,
            peers_outbound=outbound + bootstrap_bonus,
            bootstrap_attempts_last_5m=attempts_last_5m,
            last_peer_connect_at=self._last_peer_connect_at,
            last_peer_disconnect_at=self._last_peer_disconnect_at,
            seed_sources=dict(self._seed_sources),
            dial_queue_depth=len(self._dial_inflight),
            addrman_size=addrman_size,
            dial_attempts=self._dial_attempt_total,
            dial_successes=self._dial_success_total,
            dial_attempt_history=list(self._dial_attempt_log),
            learned_addrs_1m=learned_1m,
            announced_addrs_1m=announced_1m,
            persisted_peer_count=self._persisted_peer_count,
            seed_list=list(self.seeds),
            outbound_dialing_enabled=outbound_enabled,
            outbound_target=outbound_target,
            caps_config={
                "tx_relay_v2_enabled": self._tx_relay_v2_enabled,
                "required_caps": sorted(self._required_caps),
            },
            dial_last_error=self._dial_last_error,
            bootstrap_last_attempt=self._last_bootstrap_attempt,
            bootstrap_last_success=self._last_bootstrap_success,
            bootstrap_last_error=self._last_bootstrap_error,
        )

    def sync_status_snapshot(self, *, refresh: bool = False) -> SyncStatusSnapshot:
        now = time.time()
        if (
            not refresh
            and self._sync_status_cache is not None
            and self._sync_status_cache_interval > 0
            and (now - self._sync_status_cache_at) < self._sync_status_cache_interval
        ):
            self._sync_status_cache_hits += 1
            return self._apply_sync_status_cache_meta(
                self._sync_status_cache, now, source="cache"
            )
        previous_snapshot = self._sync_status_cache
        snapshot = self._build_sync_status_snapshot()
        if previous_snapshot is not None:
            stale_fields = (
                ("head_height", previous_snapshot.head_height, snapshot.head_height),
                (
                    "network_best_height",
                    previous_snapshot.network_best_height,
                    snapshot.network_best_height,
                ),
                ("target_height", previous_snapshot.target_height, snapshot.target_height),
            )
            for field_name, old_value, new_value in stale_fields:
                if old_value != new_value:
                    log.debug(
                        "STATUS_STALE_FIELD_DETECTED",
                        extra={
                            "field": field_name,
                            "old": old_value,
                            "new": new_value,
                        },
                    )
        self._sync_status_cache = snapshot
        self._sync_status_cache_at = now
        self._sync_status_cache_refreshes += 1
        log.debug(
            "STATUS_CACHE_REFRESH",
            extra={"reason": "sync_status_snapshot", "at": now},
        )
        return self._apply_sync_status_cache_meta(snapshot, now, source="refresh")

    def _apply_sync_status_cache_meta(
        self,
        snapshot: SyncStatusSnapshot,
        now: float,
        *,
        source: str,
    ) -> SyncStatusSnapshot:
        age = max(0.0, now - self._sync_status_cache_at)
        snapshot.cache_interval_ms = int(self._sync_status_cache_interval * 1000)
        snapshot.cache_age_ms = int(age * 1000)
        snapshot.cache_hits = self._sync_status_cache_hits
        snapshot.cache_refreshes = self._sync_status_cache_refreshes
        snapshot.cache_last_refresh_at = self._sync_status_cache_at
        snapshot.cache_source = source
        return snapshot

    def _build_sync_status_snapshot(self) -> SyncStatusSnapshot:
        height, head_hash = self._canonical_head_for_status()
        head_hex = head_hash
        raw_best_header_height = (
            self._sync_best_header.height if self._sync_best_header else 0
        )
        raw_best_header_hash = (
            "0x" + self._sync_best_header.hash.hex()
            if self._sync_best_header is not None
            else None
        )
        if head_hex and height >= raw_best_header_height:
            best_header_height = int(height or 0)
            best_header_hash = head_hex
        else:
            best_header_height = raw_best_header_height
            best_header_hash = raw_best_header_hash
        best_block_height = int(height or 0)
        best_block_hash = head_hex
        queued_blocks_count = self._queued_blocks_count(best_block_height)
        pending_header_batches = len(self._sync_header_queue) + len(
            self._sync_header_retry_queue
        )
        eligible_peers, ineligible_peers = self._eligible_sync_peers()
        eligible_block_peers, ineligible_block_peers = self._eligible_block_peers()
        header_cooldown_count, header_cooldown_next_expiry = self._header_cooldown_snapshot()
        now = time.time()
        block_cooldown_peers = [
            peer for peer in self._peers.values() if peer.block_cooldown_until > now
        ]
        block_cooldown_count = len(block_cooldown_peers)
        block_cooldown_next_expiry = (
            min((peer.block_cooldown_until for peer in block_cooldown_peers), default=None)
            if block_cooldown_peers
            else None
        )
        truth = self._compute_sync_status_truth(
            best_header_height=best_header_height,
            best_block_height=best_block_height,
            pending_header_batches=pending_header_batches,
            queued_blocks_count=queued_blocks_count,
            eligible_header_peers=len(eligible_peers),
            peers_total=len(self._peers),
        )
        phase = truth.phase
        phase_reason = truth.phase_reason
        if phase != self._sync_last_phase_reported:
            log.info(
                "Sync phase transition",
                extra={
                    "from": self._sync_last_phase_reported,
                    "to": phase,
                    "reason": phase_reason,
                    "head_height": best_block_height,
                    "best_header_height": best_header_height,
                    "synchronized": truth.synchronized,
                    "target_height": truth.target_height,
                    "headers_accepted_total": self._sync_headers_accepted_total,
                    "headers_seen_total": self._sync_headers_seen_total,
                    "observed_network_height": truth.observed_network_height,
                    "eligible_peers": len(eligible_peers),
                    "peers_total": len(self._peers),
                },
            )
            self._sync_last_phase_reported = phase
        active_peers_for_headers = (
            [self._sync_active_header_peer] if self._sync_active_header_peer else []
        )
        active_peers_for_blocks = list(
            dict.fromkeys(
                [
                    peer
                    for peer in self._sync_inflight_peers.values()
                    if isinstance(peer, str) and peer
                ]
            )
        )
        if self._sync_active_block_peer and self._sync_active_block_peer not in active_peers_for_blocks:
            active_peers_for_blocks.append(self._sync_active_block_peer)
        next_block_height, next_block_hash = self._next_block_needed()
        next_block_hash_hex = (
            "0x" + next_block_hash.hex() if next_block_hash is not None else None
        )
        next_block_attempts = []
        if next_block_hash is not None:
            next_block_attempts = list(
                self._sync_block_attempts_by_hash.get(next_block_hash, deque())
            )
        stall_elapsed_s = max(0.0, now - self._sync_last_progress_at)
        checkpoint_hash = (
            "0x" + self._sync_checkpoint_hash.hex()
            if self._sync_checkpoint_hash is not None
            else None
        )
        peer_anchor_states = {
            peer.remote: {
                "anchored": bool(peer.anchored),
                "anchor_reason": peer.anchor_reason,
                "last_anchor_at": peer.last_anchor_at,
                "not_anchored_count": peer.not_anchored_count,
                "last_not_anchored_at": peer.last_not_anchored_at,
            }
            for peer in self._peers.values()
        }
        cache_size_bytes = self._sync_cache.cache_size_bytes() if self._sync_cache else 0
        cache_entries = self._sync_cache.cache_entries() if self._sync_cache else 0
        sync_head_height = self._sync_last_locator_head_height
        sync_head_hash = self._sync_last_locator_head_hash
        if sync_head_height is None or sync_head_hash is None:
            sync_head_height, sync_head_hash, _source = self._sync_head_for_locator()
        sync_head_hash_hex = (
            "0x" + sync_head_hash.hex() if sync_head_hash is not None else None
        )
        last_ancestor_hash = self._canon_hash0x(self._sync_last_matched_ancestor_hash)
        snapshot_auto_enabled = self._snapshot_auto_enabled()
        cooldown_remaining = 0.0
        if self._snapshot_recovery_last_attempt_at:
            cooldown_remaining = max(
                0.0,
                self._snapshot_recovery_cooldown
                - (time.time() - self._snapshot_recovery_last_attempt_at),
            )
        return SyncStatusSnapshot(
            phase=phase,
            head_height=best_block_height,
            head_hash=head_hex,
            best_header_height=best_header_height,
            best_header_hash=best_header_hash,
            best_block_height=best_block_height,
            best_block_hash=best_block_hash,
            network_best_height=truth.network_best_height,
            in_flight=len(self._sync_inflight_blocks),
            in_flight_headers=int(self._sync_inflight_headers),
            in_flight_blocks=len(self._sync_inflight_blocks),
            queued_blocks_count=queued_blocks_count,
            last_progress_at=self._sync_last_progress_at,
            last_head_height=self._sync_last_head_height,
            last_head_hash=self._sync_last_head_hash,
            last_header_height=self._sync_last_header_height,
            last_block_fetch_height=self._sync_last_block_fetch_height,
            last_header_progress_at=self._sync_last_header_at,
            last_block_progress_at=self._sync_last_block_at,
            last_header_at=self._sync_last_header_at,
            last_block_at=self._sync_last_block_at,
            last_header_request_at=self._sync_last_header_request_at,
            last_header_response_at=self._sync_last_header_response_at,
            last_header_response_count=self._sync_last_header_response_count,
            last_headers_accepted_count=self._sync_last_headers_accepted_count,
            last_headers_discarded_count=self._sync_last_headers_discarded_count,
            last_headers_discard_reason_counts=dict(
                self._sync_last_headers_discard_reason_counts
            ),
            headers_accepted_total=self._sync_headers_accepted_total,
            headers_seen_total=self._sync_headers_seen_total,
            last_block_request_at=self._sync_last_block_request_at,
            last_block_response_at=self._sync_last_block_response_at,
            last_block_download_at=self._sync_last_block_download_at,
            last_header_request_peer=self._sync_last_header_request_peer,
            last_header_response_peer=self._sync_last_header_response_peer,
            last_header_error=self._sync_last_header_error,
            last_header_error_at=self._sync_last_header_error_at,
            last_block_error=self._sync_last_block_error,
            fatal_error=self._sync_fatal_error,
            active_peer_for_headers=self._sync_active_header_peer,
            active_peer_for_blocks=self._sync_active_block_peer,
            active_peers_for_headers=active_peers_for_headers,
            active_peers_for_blocks=active_peers_for_blocks,
            eligible_peers_for_headers=[peer.remote for peer in eligible_peers],
            ineligible_peers_for_headers=dict(ineligible_peers),
            eligible_peers_for_blocks=[peer.remote for peer in eligible_block_peers],
            ineligible_peers_for_blocks=dict(ineligible_block_peers),
            pending_header_batches=pending_header_batches,
            header_cooldown_count=header_cooldown_count,
            header_cooldown_next_expiry=header_cooldown_next_expiry,
            block_cooldown_count=block_cooldown_count,
            block_cooldown_next_expiry=block_cooldown_next_expiry,
            recovery_attempts=self._sync_recovery_attempts,
            last_recovery_action=self._sync_last_recovery_action,
            last_recovery_at=self._sync_last_recovery_at,
            recovery_reason=self._sync_last_recovery_reason,
            last_locator_summary=self._sync_last_locator_summary,
            sync_head_height=sync_head_height,
            sync_head_hash=sync_head_hash_hex,
            last_matched_ancestor_height=self._sync_last_matched_ancestor_height,
            last_matched_ancestor_hash=last_ancestor_hash,
            last_anchor_check=self._sync_last_anchor_check,
            checkpoint_height=self._sync_checkpoint_height,
            checkpoint_hash=checkpoint_hash,
            checkpoint_mode_enabled=self._sync_checkpoint_mode_enabled,
            checkpoint_validation=self._sync_checkpoint_validation,
            last_checkpoint_action=self._sync_last_checkpoint_action,
            synchronized=truth.synchronized,
            at_tip=truth.at_tip,
            paused=self._sync_paused,
            sync_enabled=self._sync_enabled,
            target_height=truth.target_height,
            target_height_source=truth.target_height_source,
            observed_network_height=truth.observed_network_height,
            peers_total=len(self._peers),
            cache_size_bytes=cache_size_bytes,
            cache_entries=cache_entries,
            peer_penalties={
                remote: count
                for remote, count in self._sync_peer_penalties.items()
                if not self._is_peer_exempt(remote)
            },
            last_block_error_peer=self._sync_last_block_error_peer,
            block_error_summary=dict(self._sync_block_error_summary),
            block_peer_failures={
                peer.remote: peer.block_failures
                for peer in self._peers.values()
                if peer.block_failures > 0
            },
            recent_block_recovery_peers=list(self._sync_last_block_recovery_peers),
            next_block_needed_height=next_block_height,
            next_block_needed_hash=next_block_hash_hex,
            next_block_attempt_peers=next_block_attempts,
            verify_queue_depth=self._sync_verify_queue.qsize()
            + len(self._sync_block_buffer),
            stall_timeout_s=float(self._sync_stall_timeout),
            stall_reason=self._sync_block_stalled_reason,
            stall_elapsed_s=stall_elapsed_s,
            status_reason=phase_reason,
            useful_peer_for_headers=truth.useful_header_peer,
            useful_peer_for_blocks=truth.useful_block_peer,
            peer_anchor_states=peer_anchor_states,
            snapshot_auto_enabled=snapshot_auto_enabled,
            snapshot_last_attempt_at=self._snapshot_recovery_last_attempt_at,
            snapshot_last_success_at=self._snapshot_recovery_last_success_at,
            snapshot_last_error=self._snapshot_recovery_last_error,
            snapshot_cooldown_remaining_s=cooldown_remaining,
            snapshot_last_manifest_height=self._snapshot_recovery_last_manifest_height,
            snapshot_last_manifest_hash=self._snapshot_recovery_last_manifest_hash,
            snapshot_last_manifest_url=self._snapshot_recovery_last_manifest_url,
            cache_interval_ms=0,
            cache_age_ms=0,
            cache_hits=int(self._stats.get("cache_hits", 0)),
            cache_misses=int(self._stats.get("cache_misses", 0)),
            cache_refreshes=0,
            cache_last_refresh_at=0.0,
            cache_source="refresh",
        )

    def _observed_network_sync_height(
        self, *, network_best_height: Optional[int]
    ) -> tuple[Optional[int], Optional[str]]:
        observed_height: Optional[int] = None
        observed_peer: Optional[str] = None
        now = time.time()
        for peer in self._peers.values():
            if not peer.hello_done.is_set() or not peer.repo_state_ok:
                continue
            info = self._sync_peer_heads.get(peer.remote)
            if not self._is_peer_responsive(info, now):
                continue
            ok, _reason = self._sync_peer_eligibility(peer, now=now)
            if not ok:
                continue
            try:
                candidate = int((peer.hello or {}).get("network_best_height") or 0)
            except Exception:
                continue
            if candidate <= 0:
                continue
            if network_best_height is not None:
                candidate = min(candidate, int(network_best_height))
            if observed_height is None or candidate > observed_height:
                observed_height = candidate
                observed_peer = peer.remote
        return observed_height, observed_peer

    def _resolve_sync_target_height(
        self,
        *,
        responsive_peer_height: Optional[int],
        observed_network_height: Optional[int],
        target_tip_height: Optional[int],
    ) -> tuple[Optional[int], Optional[str]]:
        sources: dict[int, list[str]] = {}

        def _add_source(name: str, value: Optional[int]) -> None:
            if value is None:
                return
            sources.setdefault(int(value), []).append(name)

        _add_source("manual", self._sync_target_height)
        _add_source("checkpoint", self._sync_checkpoint_height)
        _add_source("peer", responsive_peer_height)
        _add_source("network", observed_network_height)
        _add_source("target_tip", target_tip_height)
        if not sources:
            return None, None
        target_height = max(sources)
        return target_height, ",".join(sorted(sources[target_height]))

    def _compute_sync_status_truth(
        self,
        *,
        best_header_height: int,
        best_block_height: int,
        pending_header_batches: int,
        queued_blocks_count: int,
        eligible_header_peers: int,
        peers_total: int,
    ) -> _SyncStatusTruth:
        best_peer, responsive_peer_height, _best_peer_hash = self._best_peer_head()
        network_best_height = self._network_best_height()
        observed_network_height, observed_network_peer = (
            self._observed_network_sync_height(
                network_best_height=network_best_height
            )
        )
        target_tip_height = self._sync_target_tip.height if self._sync_target_tip else None
        target_height, target_height_source = self._resolve_sync_target_height(
            responsive_peer_height=responsive_peer_height,
            observed_network_height=observed_network_height,
            target_tip_height=target_tip_height,
        )
        useful_header_peer = self._sync_active_header_peer or (
            observed_network_peer or (best_peer.remote if best_peer is not None else None)
        )
        useful_block_peer = self._sync_active_block_peer
        next_block_height, _next_block_hash = self._next_block_needed()
        if (
            useful_block_peer is None
            and next_block_height is not None
            and responsive_peer_height is not None
            and responsive_peer_height >= next_block_height
            and best_peer is not None
        ):
            useful_block_peer = best_peer.remote
        behind_headers = best_header_height > best_block_height
        behind_target = (
            target_height is not None and best_block_height < int(target_height)
        )
        synchronized = (
            target_height is not None
            and best_block_height > 0
            and not behind_headers
            and not behind_target
            and queued_blocks_count == 0
            and pending_header_batches == 0
            and int(self._sync_inflight_headers) == 0
            and len(self._sync_inflight_blocks) == 0
            and not self._sync_block_buffer
            and not self._sync_block_stalled_reason
            and not self._sync_last_block_error
            and self._sync_last_header_error in {None, "at_tip", "duplicate_headers"}
        )
        at_tip = bool(
            synchronized
            or (
                target_height is not None
                and best_block_height >= int(target_height)
                and not behind_headers
            )
            or (
                target_height is None
                and best_block_height > 0
                and not behind_headers
                and self._sync_last_header_error in {"at_tip", "headers_empty"}
                and queued_blocks_count == 0
                and pending_header_batches == 0
                and int(self._sync_inflight_headers) == 0
                and len(self._sync_inflight_blocks) == 0
                and not self._sync_block_buffer
                and not self._sync_block_stalled_reason
            )
        )
        phase, phase_reason = self._derive_sync_phase(
            best_header_height=best_header_height,
            best_block_height=best_block_height,
            pending_header_batches=pending_header_batches,
            queued_blocks_count=queued_blocks_count,
            buffered_blocks_count=len(self._sync_block_buffer),
            eligible_header_peers=eligible_header_peers,
            last_header_error=self._sync_last_header_error,
            active_block_peer=self._sync_active_block_peer,
            synchronized=synchronized,
            peers_total=peers_total,
            sync_enabled=self._sync_enabled,
            sync_requested=self._sync_requested,
            target_height=target_height,
            stall_elapsed_s=max(0.0, time.time() - self._sync_last_progress_at),
        )
        return _SyncStatusTruth(
            target_height=target_height,
            target_height_source=target_height_source,
            network_best_height=network_best_height,
            observed_network_height=observed_network_height,
            synchronized=synchronized,
            at_tip=at_tip,
            phase=phase,
            phase_reason=phase_reason,
            useful_header_peer=useful_header_peer,
            useful_block_peer=useful_block_peer,
        )

    def sync_debug_snapshot(self) -> dict[str, Any]:
        eligible, ineligible = self._eligible_sync_peers()
        peers: list[dict[str, Any]] = []
        for peer in self._peers.values():
            hello = peer.hello or {}
            genesis_hash = bytes(hello.get("genesis_hash") or b"")
            genesis_identity = bytes(hello.get("genesis_identity") or b"")
            params_hash = bytes(hello.get("network_params_hash") or b"")
            head_hash = bytes(hello.get("head_hash") or b"")
            peers.append(
                {
                    "remote": peer.remote,
                    "peer_id": peer.peer_id,
                    "direction": peer.direction,
                    "handshake_done": peer.hello_done.is_set(),
                    "ready_for_sync": peer.ready_for_sync,
                    "version": hello.get("version"),
                    "agent": hello.get("agent"),
                    "chain_id": hello.get("chain_id"),
                    "genesis_hash": genesis_hash.hex() if genesis_hash else None,
                    "fork_id": hello.get("fork_id"),
                    "consensus_id": hello.get("consensus_id"),
                    "protocol_version": hello.get("protocol_version"),
                    "genesis_identity": genesis_identity.hex() if genesis_identity else None,
                    "network_params_hash": params_hash.hex() if params_hash else None,
                    "head_height": hello.get("head_height"),
                    "head_hash": head_hash.hex() if head_hash else None,
                    "capabilities": list(hello.get("capabilities") or []),
                    "last_msg_at": peer.last_msg_at,
                    "last_progress_at": peer.last_progress_at,
                }
            )
        locator = self._build_locator()
        return {
            "expected_chain_id": self.chain_id,
            "expected_genesis_hash": self._genesis_hash().hex(),
            "expected_genesis_identity": self._genesis_identity().hex(),
            "expected_network_params_hash": self._network_params_hash().hex(),
            "locator": self._locator_debug(locator),
            "locator_summary": self._sync_last_locator_summary,
            "recovery_attempts": self._sync_recovery_attempts,
            "last_recovery_action": self._sync_last_recovery_action,
            "eligible_peers_for_headers": [peer.remote for peer in eligible],
            "ineligible_peers_for_headers": dict(ineligible),
            "connected_peers": peers,
            "header_events": list(self._sync_header_events),
        }

    def _normalize_peer_addr(
        self,
        address: str,
        *,
        fallback_port: Optional[int] = None,
        source: Optional[str] = None,
    ) -> PeerAddrParseResult:
        result = normalize_peer_addr(
            address,
            fallback_port=fallback_port,
            allow_ws=self._allow_ws_addrs,
            allow_quic=self._allow_quic_addrs,
            allow_tcp=True,
        )
        if not result.addr:
            reason = result.reason or "invalid"
            log_fn = log.info if reason.startswith("unsupported") else log.debug
            log_fn(
                "Ignoring unsupported peer address",
                extra={
                    "addr": address,
                    "reason": reason,
                    "source": source or "unknown",
                    "advertised_by": source or "unknown",
                },
            )
        return result

    def _normalize_seed(self, address: str) -> Optional[str]:
        result = self._normalize_peer_addr(
            address,
            fallback_port=self._local_listen_port(),
            source="seed",
        )
        if not result.addr:
            return None
        if result.addr.port == 443:
            host = str(result.addr.host or "").strip().lower()
            if host in HTTPS_SEED_TCP_UPGRADE_HOSTS:
                upgraded = self._normalize_peer_addr(
                    f"{result.addr.host}:{DEFAULT_TCP_PORT}",
                    fallback_port=DEFAULT_TCP_PORT,
                    source="seed_https_upgrade",
                )
                if upgraded.addr:
                    return upgraded.addr.canonical
            log.warning("Ignoring HTTPS seed for P2P transport: %s", address)
            return None
        return result.addr.canonical

    def _seed_hostnames(self, seeds: list[str]) -> set[str]:
        hosts: set[str] = set()
        fallback_port = self._local_listen_port()
        for seed in seeds:
            parsed = self._normalize_peer_addr(
                seed, fallback_port=fallback_port, source="seed_host"
            )
            if parsed.addr and parsed.addr.host:
                hosts.add(str(parsed.addr.host).strip().lower())
        return hosts

    def _addr_key(self, address: str) -> str:
        """
        Normalize an address so we can deduplicate against active connections.

        Peers are stored using the transport's remote_addr (e.g. "1.2.3.4:30333"),
        while dial targets might include schemes or multiaddr prefixes. Converting
        everything to a simple "host:port" string lets us skip redialing peers we
        are already connected to and proceed to additional candidates.
        """

        result = normalize_peer_addr(
            address,
            fallback_port=self._local_listen_port(),
            allow_ws=True,
            allow_quic=True,
            allow_tcp=True,
        )
        if result.addr:
            return f"{result.addr.host}:{result.addr.port}"
        return address

    def _peer_backoff_key(self, peer: _PeerState | str) -> str:
        remote = peer.remote if isinstance(peer, _PeerState) else peer
        return self._addr_key(remote)

    def _peer_eligibility_key(self, peer: _PeerState) -> str:
        if peer.peer_id:
            return f"peer_id:{peer.peer_id}"
        return f"addr:{self._addr_key(peer.remote)}"

    def _extract_host(self, remote: str) -> str:
        if "://" in remote:
            parsed = urlparse(remote)
            if parsed.hostname:
                return parsed.hostname
        if remote.startswith("[") and "]" in remote:
            return remote.split("]", 1)[0].lstrip("[")
        if ":" in remote:
            return remote.rsplit(":", 1)[0]
        return remote

    def _extract_port(self, remote: str) -> Optional[int]:
        if "://" in remote:
            parsed = urlparse(remote)
            if parsed.port:
                return int(parsed.port)
        if remote.startswith("[") and "]" in remote:
            remainder = remote.split("]", 1)[-1]
            if remainder.startswith(":"):
                with contextlib.suppress(ValueError):
                    return int(remainder[1:])
            return None
        if ":" in remote:
            with contextlib.suppress(ValueError):
                return int(remote.rsplit(":", 1)[1])
        return None

    def _host_is_private(self, host: str) -> bool:
        if not host:
            return False
        if host.lower() == "localhost":
            return True
        try:
            ip_obj = ipaddress.ip_address(host)
        except ValueError:
            return False
        return bool(
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_reserved
        )

    def _maybe_enable_private_network(self, host: str, *, reason: str) -> None:
        if self._allow_private_addrs:
            return
        if not self._host_is_private(host):
            return
        self._allow_private_addrs = True
        log.info(
            "Enabling private network address sharing (%s)",
            reason,
            extra={"host": host},
        )

    def _maybe_enable_private_from_config(self) -> None:
        if self._allow_private_addrs:
            return
        for addr in self.listen_addrs:
            try:
                parsed = parse_multiaddr(addr)
            except Exception:
                continue
            if parsed.host:
                self._maybe_enable_private_network(
                    parsed.host, reason="listen_addr"
                )
                if self._allow_private_addrs:
                    return
        for addr in self.seeds:
            host = self._extract_host(addr)
            self._maybe_enable_private_network(host, reason="seed_addr")
            if self._allow_private_addrs:
                return

    def _netgroup_key(self, remote: str) -> str:
        host = self._extract_host(remote)
        try:
            ip_obj = ipaddress.ip_address(host)
        except ValueError:
            return host
        if isinstance(ip_obj, ipaddress.IPv4Address):
            bits = max(1, min(32, self._netgroup_v4_bits))
            network = ipaddress.ip_network(f"{ip_obj}/{bits}", strict=False)
        else:
            bits = max(1, min(128, self._netgroup_v6_bits))
            network = ipaddress.ip_network(f"{ip_obj}/{bits}", strict=False)
        return str(network.network_address) + f"/{bits}"

    def _ban_keys_for_peer(self, peer: _PeerState) -> set[str]:
        keys: set[str] = set()
        if peer.peer_id:
            keys.add(peer.peer_id)
            return keys
        host = self._extract_host(peer.remote)
        if host:
            keys.add(host)
            return keys
        keys.add(peer.remote)
        return keys

    def _penalty_key(self, peer: _PeerState) -> str:
        if peer.peer_id:
            return peer.peer_id
        host = self._extract_host(peer.remote)
        if host:
            return host
        return peer.remote

    def _is_docker_local(self, host: str) -> bool:
        try:
            ip_obj = ipaddress.ip_address(host)
        except ValueError:
            return False
        if ip_obj.version == 4 and ip_obj in ipaddress.ip_network("172.16.0.0/12"):
            return True
        return False

    def _is_seed_peer(self, peer: _PeerState) -> bool:
        addr_key = self._addr_key(peer.remote).strip().lower()
        if addr_key in self._seed_keys:
            return True
        host = self._extract_host(peer.remote).strip().lower()
        if host and host in self._seed_hosts:
            return True
        return False


    def _is_trusted_peer(self, peer: _PeerState | None) -> bool:
        if peer is None:
            return False
        addr_key = self._addr_key(peer.remote).strip().lower()
        if addr_key in self._trusted_seed_keys:
            return True
        host = self._extract_host(peer.remote).strip().lower()
        return bool(host and host in self._trusted_seed_hosts)

    def _is_peer_exempt(self, key: str) -> bool:
        if not key:
            return False
        normalized_key = key.strip().lower()
        if normalized_key in self._peer_exemptions:
            return True
        host = self._extract_host(key)
        if host and host.strip().lower() in self._peer_exemptions:
            return True
        return False

    def _is_banned(self, key: str, *, now: Optional[float] = None) -> bool:
        if not self._ban_enabled:
            return False
        now = time.time() if now is None else now
        if not key:
            return False
        if self._is_peer_exempt(key):
            return False
        entries = [key]
        host = self._extract_host(key)
        if host and host != key:
            entries.append(host)
        for entry in entries:
            info = self._banlist.get(entry)
            if not info:
                continue
            until = info.get("ban_until")
            try:
                until_f = float(until)
            except (TypeError, ValueError):
                continue
            if until_f > now:
                return True
            self._banlist.pop(entry, None)
        return False

    def _derive_sync_phase(
        self,
        *,
        best_header_height: int,
        best_block_height: int,
        pending_header_batches: int,
        queued_blocks_count: int,
        buffered_blocks_count: int,
        eligible_header_peers: int = 0,
        last_header_error: Optional[str] = None,
        active_block_peer: Optional[str] = None,
        synchronized: bool = False,
        peers_total: int = 0,
        sync_enabled: bool = True,
        sync_requested: bool = False,
        target_height: Optional[int] = None,
        stall_elapsed_s: float = 0.0,
    ) -> tuple[str, str]:
        behind_target = (
            target_height is not None and best_block_height < int(target_height)
        )
        behind_headers = best_header_height > best_block_height
        if self._sync_block_stalled_reason:
            return "STALLED", f"stalled:{self._sync_block_stalled_reason}"
        if pending_header_batches > 0 or self._sync_inflight_headers:
            return "HEADERS", "pending_headers"
        if behind_headers:
            if (
                queued_blocks_count
                or self._sync_inflight_blocks
                or buffered_blocks_count
                or active_block_peer
            ):
                return "BLOCKS", "headers_ahead_blocks_pending"
            if stall_elapsed_s >= float(self._sync_stall_timeout):
                return "STALLED", "headers_ahead_without_block_progress"
            return "SYNCING", "headers_ahead"
        if self._sync_inflight_blocks or buffered_blocks_count:
            return "VERIFYING", "verifying_blocks"
        if synchronized:
            return "SYNCED", "at_tip"
        if (
            target_height is None
            and best_block_height > 0
            and not behind_headers
            and last_header_error in {"at_tip", "headers_empty"}
        ):
            return "IDLE", "no_higher_target"
        if not sync_enabled:
            return "IDLE", "sync_disabled"
        if behind_target:
            if stall_elapsed_s >= float(self._sync_stall_timeout) and (
                eligible_header_peers > 0
                or peers_total > 0
                or last_header_error
                or sync_requested
            ):
                return "STALLED", "behind_target_without_progress"
            if eligible_header_peers > 0 or sync_requested or last_header_error:
                return "SYNCING", "behind_target"
            return "IDLE", "behind_target_no_eligible_peers"
        if last_header_error and last_header_error != "at_tip":
            return "SYNCING", f"last_header_error:{last_header_error}"
        if sync_requested:
            return "SYNCING", "sync_requested"
        if eligible_header_peers > 0 and target_height is not None:
            return "SYNCING", "peer_available_for_target_probe"
        if peers_total > 0:
            return "IDLE", "peer_connected_no_confirmed_target"
        return "IDLE", "idle"

    def _sync_status_invariants(
        self,
        *,
        head_height: int,
        best_header_height: int,
        queued_blocks_count: int,
        in_flight_headers: int,
        in_flight_blocks: int,
        last_header_error: Optional[str],
        last_block_error: Optional[str],
        target_height: Optional[int],
    ) -> bool:
        if self._sync_block_stalled_reason:
            return False
        if queued_blocks_count != 0:
            return False
        if in_flight_headers != 0 or in_flight_blocks != 0:
            return False
        if target_height is not None and head_height < max(0, int(target_height) - 1):
            return False
        if best_header_height > head_height:
            return False
        if last_block_error:
            return False
        if last_header_error not in (None, "at_tip", "duplicate_headers"):
            return False
        return True

    def _canonical_head_for_status(self) -> tuple[int, Optional[str]]:
        local_height, _local_hash = self._local_head()
        local_height = int(local_height or 0)
        local_hash0x = self._canon_hash0x(_local_hash)
        try:
            bdb = self._block_db()
        except Exception:
            return local_height, local_hash0x
        head = self._safe_db_head(bdb)
        if head:
            height = int(head[0])
            head_hash = bytes(head[1])
            if local_height > height:
                return local_height, local_hash0x
            if self._has_header(head_hash):
                return height, "0x" + head_hash.hex()
            recovered = self._recover_head_from_canonical(height)
            if recovered is not None:
                recovered_height, recovered_hash = recovered
                return recovered_height, "0x" + recovered_hash.hex()
        return local_height, local_hash0x

    def _safe_db_head(self, bdb: Any) -> Any:
        """Best-effort block-db head lookup for mixed DB adapter shapes."""
        head = None
        if hasattr(bdb, "get_canonical_head"):
            with contextlib.suppress(Exception):
                head = bdb.get_canonical_head()
        if head is None and hasattr(bdb, "get_head"):
            with contextlib.suppress(Exception):
                head = bdb.get_head()
        return head

    def _maybe_mark_block_stalled(self, now: float) -> None:
        if self._sync_best_header is None:
            return
        if self._sync_inflight_headers or self._sync_header_queue:
            return
        next_block_height, next_block_hash = self._next_block_needed()
        if next_block_hash is None:
            if self._sync_block_stalled_reason in {
                STALL_BLOCK_TIMEOUT,
                STALL_BLOCK_PEER_UNRESPONSIVE,
                STALL_BLOCK_NOT_FOUND_ACROSS_PEERS,
                STALL_BLOCK_INVALID_RESPONSE,
                STALL_CACHE_LOOP,
                STALL_CACHE_SHORT_CIRCUIT,
                STALL_VERIFY_BACKPRESSURE,
                STALL_BLOCK_NOT_ADVANCING,
                STALL_HEADERS_EMPTY_LOOP,
            }:
                self._sync_block_stalled_reason = None
                self._sync_last_block_error = None
                self._sync_last_block_error_at = None
            return
        block_progress_at = max(
            self._sync_last_block_at,
            self._sync_last_block_download_at,
            self._sync_last_block_request_at,
        )
        if now - block_progress_at <= self._sync_stall_timeout:
            return
        if self._sync_inflight_blocks:
            return
        local_height, _ = self._local_head()
        if self._sync_best_header.height <= int(local_height or 0):
            return
        stall_reason = STALL_BLOCK_TIMEOUT
        eligible_peers, _ = self._eligible_block_peers()
        if not eligible_peers:
            stall_reason = STALL_BLOCK_NOT_FOUND_ACROSS_PEERS
        elif (
            self._sync_last_cache_error_at
            and self._sync_last_cache_error_at >= self._sync_last_block_request_at
        ):
            stall_reason = STALL_CACHE_SHORT_CIRCUIT
        elif self._sync_last_block_error:
            if self._sync_last_block_error == STALL_CACHE_SHORT_CIRCUIT:
                stall_reason = STALL_CACHE_SHORT_CIRCUIT
            else:
                stall_reason = STALL_BLOCK_INVALID_RESPONSE
        if not self._sync_block_stalled_reason:
            self._sync_block_stalled_reason = stall_reason
            self._sync_last_block_error = stall_reason
            self._sync_last_block_error_at = now
            log.warning(
                "Block sync stalled",
                extra={
                    "last_block_request_at": self._sync_last_block_request_at,
                    "last_block_at": self._sync_last_block_at,
                    "last_block_download_at": self._sync_last_block_download_at,
                    "next_block_height": next_block_height,
                    "next_block_hash": next_block_hash.hex(),
                    "stall_reason": stall_reason,
                },
            )

    def _discard_blocks_from_ineligible_peers(self) -> dict[str, int]:
        """
        Discard blocks from ineligible peers in the sync buffer and inflight blocks.
        This prevents stalls when peers become ineligible (e.g., handshake_pending).
        Returns counts of discarded blocks by source.
        """
        eligible_block_peers, ineligible_block_peers = self._eligible_block_peers()
        eligible_remotes = {peer.remote for peer in eligible_block_peers}
        
        discarded_buffer = 0
        discarded_inflight = 0
        discarded_cache = 0
        
        # Discard blocks from ineligible peers in the buffer
        for h, blk in list(self._sync_block_buffer.items()):
            if blk.origin_peer and blk.origin_peer not in eligible_remotes:
                confirmed, _reason, _ctx = self._block_corroboration_status(
                    h, origin_remote=None
                )
                if confirmed:
                    continue
                self._sync_block_buffer.pop(h, None)
                discarded_buffer += 1
                # Re-queue the block to be fetched from an eligible peer
                if not self._has_block(h) and h not in self._sync_block_queue_set:
                    self._sync_block_queue.append(h)
                    self._sync_block_queue_set.add(h)
                    height_hint = self._block_height_hint(h)
                    if height_hint is not None:
                        self._sync_block_queue_heights[h] = height_hint
        
        # Discard inflight blocks from ineligible peers
        for h in list(self._sync_inflight_blocks.keys()):
            peer_remote = self._sync_inflight_peers.get(h)
            if peer_remote and peer_remote not in eligible_remotes:
                self._sync_inflight_blocks.pop(h, None)
                self._sync_inflight_peers.pop(h, None)
                self._sync_inflight_block_requests.pop(h, None)
                discarded_inflight += 1
                # Re-queue the block to be fetched from an eligible peer
                if not self._has_block(h) and h not in self._sync_block_queue_set:
                    self._sync_block_queue.appendleft(h)
                    self._sync_block_queue_set.add(h)
                    height_hint = self._block_height_hint(h)
                    if height_hint is not None:
                        self._sync_block_queue_heights[h] = height_hint
        
        # Invalidate cache entries if the cache has blocks from peers with errors
        if self._sync_cache is not None and self._sync_last_block_error_peer:
            if self._sync_last_block_error_peer not in eligible_remotes:
                # Clear cache if the error peer is ineligible
                self._sync_cache.clear()
                discarded_cache = 1
        
        result = {
            "buffer": discarded_buffer,
            "inflight": discarded_inflight,
            "cache": discarded_cache,
            "ineligible_peers": len(ineligible_block_peers),
            "eligible_peers": len(eligible_remotes),
        }
        
        if discarded_buffer or discarded_inflight or discarded_cache:
            log.info(
                "Discarded blocks from ineligible peers",
                extra={
                    "discarded_buffer": discarded_buffer,
                    "discarded_inflight": discarded_inflight,
                    "discarded_cache": discarded_cache,
                    "ineligible_peers": list(ineligible_block_peers.keys()),
                    "eligible_peers": list(eligible_remotes),
                },
            )
        
        return result

    def _highest_available_block_height(self) -> Optional[int]:
        heights: list[int] = []
        network_best_height = self._network_best_height()
        if network_best_height is not None:
            heights.append(int(network_best_height))
        now = time.time()
        eligible_peers, _ineligible = self._eligible_block_peers()
        for peer in eligible_peers:
            if not peer.hello_done.is_set():
                continue
            peer_height = self._peer_sync_head_height(peer, now=now)
            if peer_height > 0:
                heights.append(int(peer_height))
        return max(heights) if heights else None

    def _drop_unserviceable_header_target(
        self, *, reason: str, local_height: int
    ) -> bool:
        if self._sync_best_header is None:
            return False
        local_height = int(local_height or 0)
        best_header_height = int(self._sync_best_header.height)
        if best_header_height <= local_height:
            return False
        available_height = self._highest_available_block_height()
        if available_height is None or int(available_height) > local_height:
            return False

        removed_headers = 0
        for header_hash, header in list(self._sync_headers.items()):
            if int(header.height) > local_height:
                self._sync_headers.pop(header_hash, None)
                self._sync_header_sources.pop(header_hash, None)
                self._sync_header_votes.pop(header_hash, None)
                removed_headers += 1
        removed_queued_blocks = len(self._sync_block_queue)
        removed_buffered_blocks = len(self._sync_block_buffer)

        self._sync_header_queue.clear()
        self._sync_header_retry_queue.clear()
        self._sync_inflight_header_requests.clear()
        self._sync_inflight_headers = 0
        self._sync_block_queue.clear()
        self._sync_block_queue_set.clear()
        self._sync_block_queue_heights.clear()
        self._sync_block_retry_counts.clear()
        self._sync_block_buffer.clear()
        self._sync_inflight_blocks.clear()
        self._sync_inflight_peers.clear()
        self._sync_inflight_block_requests.clear()
        self._sync_duplicate_header_ranges.clear()
        self._sync_zero_accept_batches = 0
        self._sync_zero_accept_last_at = 0.0
        self._sync_last_headers_accepted_count = 0
        self._sync_last_headers_discarded_count = 0
        self._sync_last_headers_discard_reason_counts = {}
        self._sync_last_queue_depth = 0
        self._sync_next_block_stable_hash = None
        self._sync_next_block_stable_since = 0.0
        self._sync_active_header_peer = None
        self._sync_active_block_peer = None
        self._sync_block_stalled_reason = None
        self._sync_last_block_error = None
        self._sync_last_block_error_at = None
        self._sync_last_block_error_peer = None
        if self._sync_last_header_error in {
            "duplicate_headers",
            "overlap_headers",
            "no_progress_headers",
            "headers_empty",
            "stale_network_best",
            "peer_behind",
        }:
            self._sync_last_header_error = None
            self._sync_last_header_error_at = None
            self._sync_last_header_error_peer = None
        self._sync_best_header = None
        _head_height, head_hash_hex = self._local_head()
        head_hash = self._parse_hash_bytes(head_hash_hex)
        if head_hash is not None:
            head_header = self._sync_header_by_hash(head_hash)
            if head_header is not None:
                self._sync_best_header = head_header
        if (
            self._sync_target_tip is not None
            and int(self._sync_target_tip.height) > int(available_height)
        ):
            self._sync_target_tip = None
        if (
            self._sync_target_height is not None
            and int(self._sync_target_height) > max(local_height, int(available_height))
        ):
            self._update_sync_target_height(
                None, reason="drop_unserviceable_header_target"
            )
        self._sync_recovery_attempts += 1
        self._sync_last_recovery_action = "drop_unserviceable_header_target"
        self._sync_last_recovery_at = time.time()
        self._sync_last_recovery_reason = reason
        self._sync_wakeup.set()
        log.warning(
            "Dropped unserviceable sync header target",
            extra={
                "reason": reason,
                "local_height": local_height,
                "best_header_height": best_header_height,
                "available_block_height": int(available_height),
                "removed_headers": removed_headers,
                "removed_queued_blocks": removed_queued_blocks,
                "removed_buffered_blocks": removed_buffered_blocks,
            },
        )
        return True

    async def _recover_missing_block(
        self,
        *,
        block_hash: bytes,
        block_height: Optional[int],
        reason: str,
    ) -> None:
        eligible, ineligible = self._eligible_block_peers()
        peers = [peer for peer in eligible if peer.hello_done.is_set()]
        if not peers:
            self._sync_last_block_recovery_peers = []
            log.warning(
                "Block recovery skipped: no eligible peers",
                extra={
                    "reason": reason,
                    "block_height": block_height,
                    "block_hash": block_hash.hex(),
                    "ineligible_peers": list(ineligible.keys()),
                },
            )
            return
        random.shuffle(peers)
        if self._sync_active_block_peer:
            peers.sort(
                key=lambda p: 1
                if p.remote == self._sync_active_block_peer
                else 0
            )
        selected = peers[: max(1, self._sync_block_recovery_peers)]
        self._sync_last_block_recovery_peers = [peer.remote for peer in selected]
        semaphore = asyncio.Semaphore(max(1, self._sync_block_recovery_concurrency))

        async def _request(peer: _PeerState) -> None:
            async with semaphore:
                try:
                    self._sync_last_block_request_at = time.time()
                    peer.last_block_request_at = self._sync_last_block_request_at
                    self._record_block_attempt(peer, block_hash)
                    await self._send(
                        peer,
                        MsgID.GET_BLOCKS,
                        GetBlocks(by_hash=[block_hash], max_blocks=1),
                    )
                except Exception:
                    self._record_block_failure(peer, reason="recovery_request_error")

        tasks = [asyncio.create_task(_request(peer)) for peer in selected]
        await asyncio.gather(*tasks, return_exceptions=True)

    def _handle_sync_stall(self, *, reason: str) -> None:
        now = time.time()
        # Allow stall handling even when _sync_best_header is None or equals local height
        # This handles the case where headers == blocks and we're stuck
        if self._last_rotation_at and now - self._last_rotation_at < 5.0:
            return
        if reason == STALL_HEADERS_EMPTY_LOOP:
            self._sync_last_header_error = None
            self._sync_last_header_error_at = None
            self._sync_last_header_error_peer = None
            head_height, _ = self._local_head()
            self._clear_transient_header_penalties(
                needed_height=int(head_height or 0) + 1
            )
            self._enqueue_header_stall_retry(reason="headers_empty_rotate")
            self._sync_last_recovery_action = "headers_empty_rotate"
            self._sync_last_recovery_at = now
            self._sync_last_recovery_reason = reason
            self._sync_block_stalled_reason = None
            self._last_rotation_at = now
            return
        
        # Discard blocks from ineligible peers before attempting recovery
        discarded = self._discard_blocks_from_ineligible_peers()
        local_height, _ = self._local_head()
        if self._drop_unserviceable_header_target(
            reason=reason, local_height=int(local_height or 0)
        ):
            self._last_rotation_at = now
            return
        target_height = self._known_sync_target_height()
        header_recovery_enqueued = False
        cleared_header_penalties = 0
        if (
            target_height is not None
            and int(target_height) > int(local_height or 0)
            and (
                self._sync_best_header is None
                or int(self._sync_best_header.height) <= int(local_height or 0)
            )
        ):
            cleared_header_penalties = self._clear_transient_header_penalties(
                needed_height=int(local_height or 0) + 1
            )
            self._enqueue_header_stall_retry(reason=f"stall_header_recovery:{reason}")
            header_recovery_enqueued = True
        
        old_peer = None
        if self._sync_active_block_peer:
            old_peer = self._peer_by_remote(self._sync_active_block_peer)
        if old_peer and old_peer.last_block_request_at:
            self._penalize_peer(
                old_peer,
                "stall",
                points=self._score_points["stall"],
                severity=2,
                quarantine_s=30.0,
            )
            self._record_block_failure(old_peer, reason="stall")
            old_peer.last_progress_at = now
        for h in list(self._sync_inflight_blocks.keys()):
            if self._has_block(h):
                continue
            if h not in self._sync_block_queue_set:
                self._sync_block_queue.appendleft(h)
                self._sync_block_queue_set.add(h)
        self._sync_inflight_blocks.clear()
        self._sync_inflight_peers.clear()
        self._sync_inflight_block_requests.clear()
        if reason == STALL_VERIFY_BACKPRESSURE and self._sync_block_buffer:
            dropped_hash, dropped_block = self._sync_block_buffer.popitem(last=False)
            log.warning(
                "Relieving verify backpressure by dropping buffered block",
                extra={
                    "hash": dropped_hash.hex(),
                    "origin_peer": dropped_block.origin_peer,
                },
            )
            if dropped_hash not in self._sync_block_queue_set:
                self._sync_block_queue.appendleft(dropped_hash)
                self._sync_block_queue_set.add(dropped_hash)
        needed_height, _ = self._next_block_needed()
        if self._sync_block_chunk_size_current > self._sync_block_chunk_size_min:
            self._sync_block_chunk_size_current = max(
                self._sync_block_chunk_size_min,
                self._sync_block_chunk_size_current // 2,
            )
        avoid_remotes = {old_peer.remote} if old_peer is not None else None
        new_peer = self._select_block_peer(
            needed_height=needed_height,
            require_anchored=self._should_enforce_checkpoint_anchor(),
            avoid_remotes=avoid_remotes,
        )
        if new_peer is None and self._should_enforce_checkpoint_anchor():
            new_peer = self._select_block_peer(
                needed_height=needed_height,
                require_anchored=False,
                avoid_remotes=avoid_remotes,
            )
        if new_peer is None and avoid_remotes:
            new_peer = self._select_block_peer(
                needed_height=needed_height,
                require_anchored=self._should_enforce_checkpoint_anchor(),
            )
        if (
            new_peer is None
            and avoid_remotes
            and self._should_enforce_checkpoint_anchor()
        ):
            new_peer = self._select_block_peer(
                needed_height=needed_height,
                require_anchored=False,
            )
        cleared_transient_penalties = 0
        if new_peer is None:
            cleared_transient_penalties = self._clear_transient_block_penalties(
                needed_height=needed_height
            )
            if cleared_transient_penalties:
                new_peer = self._select_block_peer(
                    needed_height=needed_height,
                    require_anchored=self._should_enforce_checkpoint_anchor(),
                    avoid_remotes=avoid_remotes,
                )
                if new_peer is None and self._should_enforce_checkpoint_anchor():
                    new_peer = self._select_block_peer(
                        needed_height=needed_height,
                        require_anchored=False,
                        avoid_remotes=avoid_remotes,
                    )
                if new_peer is None and avoid_remotes:
                    new_peer = self._select_block_peer(
                        needed_height=needed_height,
                        require_anchored=self._should_enforce_checkpoint_anchor(),
                    )
                if (
                    new_peer is None
                    and avoid_remotes
                    and self._should_enforce_checkpoint_anchor()
                ):
                    new_peer = self._select_block_peer(
                        needed_height=needed_height,
                        require_anchored=False,
                    )
        if new_peer:
            self._sync_active_block_peer = new_peer.remote
            self._sync_last_recovery_action = "retry_blocks_new_peer"
            self._sync_block_stalled_reason = None
            self._stats["stall_recoveries"] += 1
            self._sync_wakeup.set()
        elif header_recovery_enqueued:
            self._sync_last_recovery_action = "retry_headers_after_stall"
            self._sync_block_stalled_reason = None
        else:
            self._sync_last_recovery_action = "stall_no_peer"
        self._sync_last_recovery_at = now
        self._sync_last_recovery_reason = reason
        self._last_rotation_at = now
        needed_height, needed_hash = self._next_block_needed()
        if needed_hash:
            self._create_child_task(
                self._recover_missing_block(
                    block_hash=needed_hash,
                    block_height=needed_height,
                    reason=reason,
                ),
                name=f"p2p.block_recovery@{needed_hash.hex()}",
            )
        best_header_height = (
            self._sync_best_header.height if self._sync_best_header else local_height
        )
        eligible_block_peers, _ineligible_block_peers = self._eligible_block_peers()
        log.warning(
            "Block sync stall handled",
            extra={
                "reason": reason,
                "old_peer": old_peer.remote if old_peer else None,
                "new_peer": new_peer.remote if new_peer else None,
                "old_peer_score": old_peer.misbehavior_score if old_peer else None,
                "new_peer_score": new_peer.misbehavior_score if new_peer else None,
                "local_height": local_height,
                "best_header_height": best_header_height,
                "queued_blocks": len(self._sync_block_queue),
                "eligible_block_peers": [p.remote for p in eligible_block_peers],
                "discarded_blocks": discarded,
                "cleared_transient_penalties": cleared_transient_penalties,
                "cleared_header_penalties": cleared_header_penalties,
                "header_recovery_enqueued": header_recovery_enqueued,
                "recovery_peers": list(self._sync_last_block_recovery_peers),
            },
        )

    def _force_peer_refresh(self, *, reason: str) -> None:
        self._seeding_mode = True
        self._dial_backoff.clear()
        self._dial_attempts.clear()
        self._sync_last_recovery_action = reason
        log.info("Sync peer refresh requested", extra={"reason": reason})

    def _should_reset_from_highest_next_height(
        self, *, now: float, head_height: int
    ) -> bool:
        network_best_height = self._network_best_height()
        if network_best_height is None:
            # 38728 wedge: every stuck node reported network_best_height=null
            # (no peer survived the responsive-peer filter), which disarmed
            # this recovery even while a seed kept serving it full header
            # batches. Fall back to the heights of blocks already QUEUED for
            # download — concrete, pipeline-vetted evidence the network
            # extends past us (the live stuck nodes held ~10 queued blocks).
            # Deliberately NOT raw header heights: those are peer-claimed and
            # unvalidated, so a single malicious peer could arm resets at tip.
            queued = self._sync_block_queue_heights.values()
            if not queued:
                return False
            network_best_height = max(queued)
        # Only reset if we're at least 2 blocks behind to avoid false positives
        # when verifier+1 allows miners but no one is at that height yet
        if int(network_best_height) <= int(head_height) + 1:
            return False
        stalled = now - self._sync_last_progress_at > self._sync_stall_timeout
        duplicate_headers = (
            self._sync_last_header_error == "duplicate_headers"
            or bool(self._sync_last_headers_discard_reason_counts.get("duplicate_headers"))
        )
        overlap_headers = bool(
            self._sync_last_headers_discard_reason_counts.get("overlap_headers")
        )
        return stalled or duplicate_headers or overlap_headers

    def _sync_watchdog_check(
        self, *, now: float, head_height: int, head_hash: Optional[str]
    ) -> None:
        if not self._peers:
            return
        # At the tip => not a stall. When no sync target is set (no peer is
        # strictly ahead of our head — see _select_sync_target_tip's
        # heaviest-chain guard), "head not advancing" just means we're waiting
        # for the next block, not that sync is stuck. Escalating watchdog
        # recovery here spins forever on a node that is correctly at the tip of
        # the heaviest chain it can see (e.g. after its DB was restored above
        # the network's diverged fork) and churns sync state every cycle, which
        # blocks normal block production. Treat it as progress and stand down.
        # Only stand down if no CONNECTED peer is actually ahead. target_tip can
        # be None because the sole ahead peer was penalized out of the candidate
        # set (consensus_mismatch/headers_timeout backoff); in that case we are
        # NOT at the tip and must keep escalating recovery to re-engage it.
        # _max_peer_head_height still counts ahead-but-filtered peers.
        if (
            getattr(self, "_sync_target_tip", None) is None
            and self._max_peer_head_height(now=now) <= head_height
        ):
            self._sync_watchdog_last_height = head_height
            self._sync_watchdog_last_hash = head_hash
            self._sync_watchdog_last_progress_at = now
            self._sync_watchdog_last_action_at = 0.0
            self._sync_watchdog_attempts = 0
            return
        if head_height > self._sync_watchdog_last_height or (
            head_hash and head_hash != self._sync_watchdog_last_hash
        ):
            self._sync_watchdog_last_height = head_height
            self._sync_watchdog_last_hash = head_hash
            self._sync_watchdog_last_progress_at = now
            self._sync_watchdog_last_action_at = 0.0
            self._sync_watchdog_attempts = 0
            return

        last_watchdog_event_at = max(
            float(self._sync_watchdog_last_progress_at or 0.0),
            float(self._sync_watchdog_last_action_at or 0.0),
        )
        if now - last_watchdog_event_at < self._sync_watchdog_timeout:
            return

        self._sync_watchdog_attempts += 1
        action = f"watchdog_attempt_{self._sync_watchdog_attempts}"
        if self._sync_watchdog_attempts == 1:
            self._handle_sync_stall(reason="watchdog_no_progress")
            self._sync_kick(reason="watchdog_requeue", aggressive=False)
            action = "watchdog_requeue"
        elif self._sync_watchdog_attempts == 2:
            should_reset = self._should_reset_from_highest_next_height(
                now=now, head_height=head_height
            )
            if should_reset:
                self._reset_from_highest_next_height(
                    reason="watchdog_reset_from_highest_next_height"
                )
                action = "reset_from_highest_next_height"
            else:
                self._force_peer_refresh(reason="watchdog_refresh_peers")
                self._sync_kick(reason="watchdog_refresh_peers", aggressive=True)
                action = "watchdog_refresh_peers"
        elif self._sync_watchdog_attempts == 3:
            self._reset_sync_state(reason="watchdog_reset_pipeline")
            self._sync_kick(reason="watchdog_reset_pipeline", aggressive=True)
            action = "watchdog_reset_pipeline"
        else:
            network_best_height = self._network_best_height()
            height_gap = (
                None
                if network_best_height is None
                else max(0, int(network_best_height) - int(head_height or 0))
            )
            if (
                height_gap is not None
                and height_gap >= self._sync_snapshot_threshold
            ):
                self._maybe_trigger_snapshot_recovery(
                    reason="watchdog_snapshot_recovery"
                )
                action = "watchdog_snapshot_recovery"
            else:
                self._force_peer_refresh(reason="watchdog_retry_blocks")
                self._sync_kick(reason="watchdog_retry_blocks", aggressive=True)
                action = "retry_blocks_new_peer"

        self._sync_last_recovery_action = action
        self._sync_watchdog_last_action_at = now
        if action != "reset_from_highest_next_height":
            self._sync_last_recovery_at = now
            self._sync_last_recovery_reason = "watchdog"
        log.warning(
            "Sync watchdog recovery triggered",
            extra={
                "action": action,
                "head_height": head_height,
                "last_progress_at": self._sync_watchdog_last_progress_at,
                "peers": len(self._peers),
            },
        )

    def _enforce_sync_invariants(
        self,
        *,
        now: float,
        best_block_height: int,
        best_header_height: int,
        target_height: Optional[int],
        best_peer: Optional[_PeerState],
    ) -> None:
        if (
            self._sync_inflight_headers
            and now - self._sync_last_progress_at > max(1.0, self._sync_request_timeout)
        ):
            self._expire_inflight_headers()
        if (
            self._sync_inflight_headers
            and self._sync_last_header_request_at
            and now - self._sync_last_header_request_at
            > max(1.0, self._sync_header_watchdog_timeout)
        ):
            log.warning(
                "Header request watchdog triggered",
                extra={
                    "inflight_headers": int(self._sync_inflight_headers),
                    "age_s": round(now - self._sync_last_header_request_at, 3),
                    "timeout_s": self._sync_header_watchdog_timeout,
                },
            )
            self._expire_inflight_headers()

        if target_height is not None and int(target_height) > best_block_height + 2:
            pending = any(
                [
                    self._sync_header_queue,
                    self._sync_header_retry_queue,
                    self._sync_block_queue,
                    self._sync_inflight_headers,
                    self._sync_inflight_blocks,
                ]
            )
            if not pending:
                head_height, head_hash = self._local_head()
                anchor_hash = self._parse_hash_bytes(head_hash)
                anchor_height = int(head_height or 0)
                locator = self._build_headers_locator()
                if not locator:
                    fallback = self._genesis_hash()
                    if fallback:
                        locator = [fallback]
                self._enqueue_header_retry(
                    peer=best_peer,
                    locator=locator,
                    locator_mode="idle_while_behind",
                    anchor_height=anchor_height,
                    anchor_hash=anchor_hash,
                    request_start_height=anchor_height + 1,
                    max_headers=self._sync_headers_batch_current,
                    reason="idle_while_behind",
                )
                self._sync_kick(reason="idle_while_behind", aggressive=True)

        if (
            best_header_height > best_block_height
            and not self._sync_block_queue
            and not self._sync_inflight_blocks
        ):
            self._ensure_block_queue()

    def _should_unpause_sync(self) -> bool:
        if not self._sync_enabled:
            return False
        if self._snapshot_recovery_task and not self._snapshot_recovery_task.done():
            return False
        now = time.time()
        if any(until > now for until in self._sync_peer_backoff.values()):
            return False
        if any(until > now for until in self._sync_block_peer_backoff.values()):
            return False
        network_best_height = self._network_best_height()
        head_height, _head_hash = self._local_head()
        if network_best_height is None:
            return False
        return int(network_best_height) > int(head_height or 0)

    def _handle_sync_loop_exception(self, exc: BaseException) -> None:
        now = time.time()
        self._stats["sync_loop_errors"] = self._stats.get("sync_loop_errors", 0) + 1
        self._sync_last_block_error = f"sync_loop_error:{exc.__class__.__name__}"
        self._sync_last_block_error_at = now
        self._sync_last_recovery_action = "sync_loop_restart"
        self._sync_last_recovery_at = now
        self._sync_last_recovery_reason = repr(exc)
        self._sync_active_header_peer = None
        self._sync_active_block_peer = None

        for peer in list(self._peers.values()):
            if peer.pending_headers is not None and not peer.pending_headers.done():
                with contextlib.suppress(Exception):
                    peer.pending_headers.set_result(None)
            peer.pending_headers = None
            peer.pending_header_request_id = None

        self._sync_inflight_header_requests.clear()
        self._sync_inflight_headers = 0
        for h in list(self._sync_inflight_blocks.keys()):
            if self._has_block(h):
                continue
            if h not in self._sync_block_queue_set:
                self._sync_block_queue.appendleft(h)
                self._sync_block_queue_set.add(h)
                height_hint = self._block_height_hint(h)
                if height_hint is not None:
                    self._sync_block_queue_heights[h] = height_hint
        self._sync_inflight_blocks.clear()
        self._sync_inflight_peers.clear()
        self._sync_inflight_block_requests.clear()
        self._sync_kick(reason="sync_loop_exception", aggressive=True)
        log.exception(
            "Sync loop iteration failed; restarting",
            extra={
                "error_class": exc.__class__.__name__,
                "queued_blocks": len(self._sync_block_queue),
                "peers": len(self._peers),
            },
        )

    async def _startup_sync_kick(self) -> None:
        deadline = time.time() + 10.0
        while self._running and time.time() < deadline:
            if self._peers:
                break
            await asyncio.sleep(0.5)
        if not self._running:
            return
        head_height, _head_hash = self._local_head()
        network_best = self._network_best_height()
        behind = 0
        if network_best is not None:
            behind = max(0, int(network_best) - int(head_height or 0))
        if behind >= self._sync_snapshot_threshold:
            self._maybe_trigger_snapshot_recovery(reason="startup_snapshot_threshold")
        self._sync_kick(reason="startup", aggressive=True)

    def _resolve_db_uri(self) -> Optional[str]:
        db_uri = getattr(self.deps, "db_uri", None) if self.deps else None
        if db_uri:
            return db_uri
        env_db_uri = os.environ.get("ANIMICA_DB_URI")
        if env_db_uri and env_db_uri.strip():
            return env_db_uri.strip()
        chain_dir = self._chain_data_dir
        if not chain_dir:
            base_dir = Path(os.environ.get("ANIMICA_DATA_DIR") or "~/.animica").expanduser()
            chain_dir = base_dir / f"chain-{self.chain_id}"
        db_path = Path(chain_dir) / DEFAULT_DB_FILENAME
        return f"sqlite:///{db_path}"

    def __getattribute__(self, name: str) -> Any:
        if name == "_log":
            try:
                return object.__getattribute__(self, name)
            except AttributeError:
                logger = logging.getLogger("animica.p2p")
                object.__setattr__(self, "_log", logger)
                return logger
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:
        if name == "_log":
            logger = logging.getLogger("animica.p2p")
            object.__setattr__(self, "_log", logger)
            return logger
        raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")

    def _maybe_trigger_snapshot_recovery(self, *, reason: str) -> None:
        if not self._snapshot_auto_enabled():
            return
        now = time.time()
        if self._snapshot_recovery_task and not self._snapshot_recovery_task.done():
            return
        if (
            self._snapshot_recovery_last_attempt_at
            and now - self._snapshot_recovery_last_attempt_at
            < self._snapshot_recovery_cooldown
        ):
            return
        window = self._snapshot_recovery_window_sec
        if window > 0:
            while self._snapshot_recovery_attempts and (
                now - self._snapshot_recovery_attempts[0] > window
            ):
                self._snapshot_recovery_attempts.popleft()
        if (
            self._snapshot_recovery_max_per_window > 0
            and len(self._snapshot_recovery_attempts)
            >= self._snapshot_recovery_max_per_window
        ):
            self._snapshot_recovery_last_error = (
                f"snapshot recovery rate limited "
                f"(max {self._snapshot_recovery_max_per_window} per {int(window)}s)"
            )
            log.warning(
                "Snapshot recovery rate limited",
                extra={
                    "reason": reason,
                    "window_s": int(window),
                    "attempts": len(self._snapshot_recovery_attempts),
                    "max_attempts": self._snapshot_recovery_max_per_window,
                },
            )
            return
        self._snapshot_recovery_attempts.append(now)
        self._snapshot_recovery_task = asyncio.create_task(
            self._run_snapshot_recovery(reason=reason), name="p2p.snapshot_recovery"
        )

    async def _run_snapshot_recovery(self, *, reason: str) -> None:
        from p2p.deps import P2PDeps
        from p2p.sync.snapshot_sync import try_snapshot_bootstrap
        from core.snapshot.inventory import latest_snapshot

        now = time.time()
        self._snapshot_recovery_last_attempt_at = now
        self._snapshot_recovery_last_error = None
        self._sync_paused = True

        db_uri = self._resolve_db_uri()
        genesis_path = getattr(self.deps, "genesis_path", None) if self.deps else None
        head_height, _head_hash = self._local_head()
        block_db = self._block_db()
        state_db = self._state_db()

        log.warning(
            "Snapshot recovery starting",
            extra={"reason": reason, "head_height": head_height, "db_uri": db_uri},
        )

        if not db_uri:
            self._snapshot_recovery_last_error = "missing db_uri"
            self._sync_paused = False
            return

        try:
            if self.deps is not None:
                kv = getattr(self.deps, "_kv", None)
                close = getattr(kv, "close", None)
                if callable(close):
                    close()
        except Exception:
            pass

        success, error = await try_snapshot_bootstrap(
            block_db=block_db,
            state_db=state_db,
            chain_id=self.chain_id,
            current_height=int(head_height or 0),
            p2p_service=self,
            db_uri=db_uri,
            force=True,
        )

        if success:
            latest = latest_snapshot(
                chain_id=int(self.chain_id),
                snapshots_dir=self._get_snapshots_dir(),
            )
            if latest is not None:
                self._snapshot_recovery_last_manifest_height = latest.checkpoint_height
                self._snapshot_recovery_last_manifest_hash = latest.manifest_hash
                self._snapshot_recovery_last_manifest_url = latest.path
            self._snapshot_recovery_last_success_at = time.time()
            try:
                self.deps = P2PDeps.open(db_uri, genesis_path)
            except Exception as exc:  # noqa: BLE001
                self._snapshot_recovery_last_error = f"reopen failed: {exc}"
            self._reset_sync_state(reason="snapshot_recovery")
            self._sync_last_progress_at = time.time()
            self._sync_watchdog_last_progress_at = time.time()
            self._sync_watchdog_attempts = 0
            self._sync_wakeup.set()
        else:
            self._snapshot_recovery_last_error = error or "snapshot recovery failed"

        self._sync_paused = False

    def _rotate_sync_peer(self) -> None:
        active = (
            self._peer_by_remote(self._sync_active_block_peer)
            if self._sync_active_block_peer
            else None
        )
        candidate = self._select_sync_peer(avoid_peer=active)
        if not candidate or (active and candidate.remote == active.remote):
            return
        if active is None:
            self._sync_active_block_peer = candidate.remote
            return
        try:
            active_height = int((active.hello or {}).get("head_height") or 0)
        except Exception:
            active_height = 0
        try:
            candidate_height = int((candidate.hello or {}).get("head_height") or 0)
        except Exception:
            candidate_height = 0
        active_latency = active.latency_ewma if active.latency_ewma is not None else 9999.0
        candidate_latency = (
            candidate.latency_ewma if candidate.latency_ewma is not None else 9999.0
        )
        if (
            candidate_height > active_height
            or (
                candidate_height == active_height
                and (
                    candidate.misbehavior_score < active.misbehavior_score
                    or candidate_latency < active_latency
                )
            )
        ):
            self._sync_active_block_peer = candidate.remote
            log.info(
                "Rotated sync peer",
                extra={
                    "old_peer": active.remote,
                    "new_peer": candidate.remote,
                    "old_score": active.misbehavior_score,
                    "new_score": candidate.misbehavior_score,
                    "old_latency_ms": round(active_latency * 1000, 2),
                    "new_latency_ms": round(candidate_latency * 1000, 2),
                },
            )

    def _reported_peer_addr(self, remote: str, listen_port: int) -> Optional[str]:
        host: Optional[str] = None
        if "://" in remote:
            parsed = urlparse(remote)
            host = parsed.hostname
        elif remote.startswith("[") and "]" in remote:
            host = remote.split("]", 1)[0].lstrip("[")
        elif ":" in remote:
            host = remote.rsplit(":", 1)[0]
        else:
            host = remote
        if not host:
            return None
        port = int(listen_port) if 1 <= int(listen_port) <= 65535 else 0
        if not port:
            return None
        return self._sanitize_peer_addr(f"{host}:{port}", fallback_port=port)

    def _strict_reported_peer_addrs(
        self, *, peer: _PeerState, listen_port: int, listen_addrs: list[str]
    ) -> list[str]:
        addrs: list[str] = []
        reported = self._reported_peer_addr(peer.remote, listen_port)
        if reported:
            addrs.append(reported)
        fallback_port = (
            int(listen_port)
            if 1 <= int(listen_port) <= 65535
            else DEFAULT_TCP_PORT
        )
        for addr in listen_addrs:
            parsed = self._normalize_peer_addr(
                addr, fallback_port=fallback_port, source=f"hello:{peer.remote}"
            )
            if (
                parsed.addr
                and not self._allow_self_peers
                and self._is_self_address(parsed.addr.host, parsed.addr.port)
            ):
                log.info(
                    "Ignoring self-like advertised peer address",
                    extra={"remote": peer.remote, "reported_addr": addr},
                )
                continue
            sanitized = self._sanitize_peer_addr(
                addr, fallback_port=fallback_port, source=f"hello:{peer.remote}"
            )
            if sanitized:
                addrs.append(sanitized)
        return list(dict.fromkeys(addrs))

    def _outbound_only_blocklist_keys(
        self, *, peer_id: Optional[str], remote: Optional[str]
    ) -> set[str]:
        keys: set[str] = set()
        if peer_id:
            keys.add(f"peer:{peer_id}")
        if remote:
            keys.add(f"remote:{self._addr_key(remote).strip().lower()}")
            host = self._extract_host(remote).strip().lower()
            if host:
                keys.add(f"host:{host}")
        return keys

    def _is_outbound_only_blocklisted(
        self, *, peer_id: Optional[str], remote: Optional[str], now: Optional[float] = None
    ) -> bool:
        now = time.time() if now is None else now
        keys = self._outbound_only_blocklist_keys(peer_id=peer_id, remote=remote)
        blocked = False
        for key in keys:
            until = self._outbound_only_blocklist.get(key)
            if until is None:
                continue
            if until > now:
                blocked = True
                continue
            self._outbound_only_blocklist.pop(key, None)
        return blocked

    def _mark_outbound_only_blocklisted(
        self, *, peer_id: Optional[str], remote: Optional[str], reason: str
    ) -> None:
        ttl = max(0.0, float(self._outbound_only_ban_ttl))
        if ttl <= 0:
            return
        until = time.time() + ttl
        for key in self._outbound_only_blocklist_keys(peer_id=peer_id, remote=remote):
            self._outbound_only_blocklist[key] = until
        log.warning(
            "Outbound-only peer blocklisted",
            extra={
                "peer_id": peer_id,
                "remote": remote,
                "reason": reason,
                "ttl_s": ttl,
            },
        )

    def _peer_is_outbound_only(self, peer: _PeerState) -> bool:
        return peer.accepts_inbound is False

    def _enforce_outbound_only_policy_for_peer(self, peer: _PeerState) -> bool:
        if not self._enforce_inbound_reachability:
            return False
        if self._is_peer_exempt(peer.remote):
            return False
        return self._peer_is_outbound_only(peer)

    def _should_forfeit_peer_blocks(self, peer: _PeerState) -> bool:
        if not self._forfeit_outbound_only_blocks:
            return False
        if self._is_peer_exempt(peer.remote):
            return False
        return self._peer_is_outbound_only(peer)

    def _update_peer_meta(self, peer: _PeerState) -> None:
        self._peer_registry.update_meta(
            peer.session_id,
            score=peer.misbehavior_score,
            ban_until=peer.ban_until,
            netgroup=peer.netgroup,
            latency_ms=round(peer.latency_ewma * 1000, 2)
            if peer.latency_ewma is not None
            else None,
            last_msg_at=peer.last_msg_at,
            last_progress_at=peer.last_progress_at,
        )

    def _update_peer_head(
        self, peer: _PeerState, *, height: int, head_hash: Optional[bytes]
    ) -> None:
        if height < 0:
            return
        if peer.hello is None:
            peer.hello = {}
        try:
            current = int(peer.hello.get("head_height") or 0)
        except Exception:
            current = 0
        current_head_hash = bytes(peer.hello.get("head_hash") or b"")
        allow_decrease = self._is_verifier_seed_peer(peer.remote)
        if height < current:
            if allow_decrease:
                peer.hello["head_height"] = int(height)
                if head_hash:
                    peer.hello["head_hash"] = bytes(head_hash)
                self._update_peer_head_table(
                    peer,
                    height=int(height),
                    source="peer_head",
                    head_hash=head_hash or None,
                )
                return
            self._update_peer_head_table(
                peer,
                height=int(current),
                source="peer_head",
                head_hash=current_head_hash or None,
            )
            return
        if height == current:
            if head_hash and not current_head_hash:
                peer.hello["head_hash"] = bytes(head_hash)
                current_head_hash = bytes(head_hash)
            self._update_peer_head_table(
                peer,
                height=int(current),
                source="peer_head",
                head_hash=head_hash or current_head_hash or None,
            )
            return
        peer.broadcast.last_head_advancement_at = time.time()
        self._stats["peer_broadcast_good"] += 1
        peer.hello["head_height"] = int(height)
        if head_hash:
            peer.hello["head_hash"] = bytes(head_hash)
        if peer.peer_id:
            with contextlib.suppress(Exception):
                self.peerstore.update_head_height(peer.peer_id, int(height))
                self._schedule_peer_persist()
        local_height, _ = self._local_head()
        if int(height) > int(local_height or 0):
            self._sync_kick(reason="peer_head_advance", aggressive=False)
        self._update_peer_head_table(
            peer, height=int(height), source="peer_head", head_hash=head_hash
        )

    def _update_peer_head_table(
        self, peer: _PeerState, *, height: int, source: str, head_hash: Optional[bytes]
    ) -> None:
        if height <= 0:
            return
        now = time.time()
        info = self._sync_peer_heads.get(peer.remote)
        if info is None:
            self._sync_peer_heads[peer.remote] = _PeerHeadInfo(
                height=int(height),
                updated_at=now,
                source=source,
                head_hash=bytes(head_hash) if head_hash else None,
            )
            return
        if height < info.height:
            if self._is_verifier_seed_peer(peer.remote):
                info.height = int(height)
                if head_hash:
                    info.head_hash = bytes(head_hash)
            info.updated_at = now
            info.source = source
            return
        if height > info.height:
            info.height = int(height)
        if head_hash:
            info.head_hash = bytes(head_hash)
        info.updated_at = now
        info.source = source

    def _mark_peer_head_issue(
        self,
        peer: _PeerState,
        *,
        reason: str,
        cooldown: Optional[float] = None,
    ) -> None:
        now = time.time()
        info = self._sync_peer_heads.get(peer.remote)
        if info is None:
            info = _PeerHeadInfo(height=0, updated_at=now, source="unknown")
            self._sync_peer_heads[peer.remote] = info
        delay = cooldown if cooldown is not None else self._sync_peer_head_cooldown_sec
        info.cooldown_until = max(info.cooldown_until, now + max(1.0, delay))
        info.last_error = reason

    def _best_peer_head(
        self,
    ) -> tuple[Optional[_PeerState], Optional[int], Optional[bytes]]:
        now = time.time()
        best_peer: Optional[_PeerState] = None
        best_height: Optional[int] = None
        best_hash: Optional[bytes] = None
        for peer in self._peers.values():
            if not peer.hello_done.is_set():
                continue
            if not self._peer_is_sync_eligible(peer):
                continue
            height, head_hash = self._fresh_peer_head(peer, now=now)
            if height <= 0:
                continue
            if best_height is None or height > best_height:
                best_height = height
                best_peer = peer
                best_hash = head_hash
        return best_peer, best_height, best_hash

    def _max_peer_head_height(self, *, now: Optional[float] = None) -> int:
        """Highest fresh head height reported by ANY connected (hello-done) peer,
        IGNORING sync-eligibility.

        Unlike _best_peer_head (which skips peers that are penalized / in sync
        backoff / cooldown), this still counts a peer that is currently ahead but
        temporarily filtered out. The at-tip => SYNCED transition and the sync
        watchdog stand-down both key off "no sync target", which becomes None
        both when no peer is ahead (genuinely at tip) AND when the only ahead
        peer was just penalized out of the candidate set (we are far behind).
        This lets those call sites tell the two apart so a behind node does not
        falsely declare itself synced.
        """
        now = time.time() if now is None else now
        best = 0
        for peer in self._peers.values():
            if not peer.hello_done.is_set():
                continue
            height, _head_hash = self._fresh_peer_head(peer, now=now)
            if height > best:
                best = height
        return best

    def _fresh_peer_head(
        self, peer: _PeerState, *, now: Optional[float] = None
    ) -> tuple[int, Optional[bytes]]:
        now = time.time() if now is None else now
        info = self._sync_peer_heads.get(peer.remote)
        if info is not None:
            if self._is_peer_responsive(info, now):
                return int(info.height), info.head_hash
            return 0, None
        try:
            height = int((peer.hello or {}).get("head_height") or 0)
        except Exception:
            height = 0
        head_hash = bytes((peer.hello or {}).get("head_hash") or b"") or None
        return height, head_hash

    def _peer_sync_head_height(
        self, peer: _PeerState, *, now: Optional[float] = None
    ) -> int:
        height, _head_hash = self._fresh_peer_head(peer, now=now)
        return height

    def _best_broadcast_peer_head(
        self,
        *,
        now: Optional[float] = None,
    ) -> tuple[Optional[_PeerState], Optional[int], Optional[bytes]]:
        now = time.time() if now is None else now
        best_peer: Optional[_PeerState] = None
        best_height: Optional[int] = None
        best_hash: Optional[bytes] = None
        for peer in self._peers.values():
            if not peer.hello_done.is_set():
                continue
            if not self._peer_is_sync_eligible(peer):
                continue
            _score, _classification, non_broadcasting = self._peer_broadcast_state(
                peer, now=now
            )
            if non_broadcasting:
                continue
            height, head_hash = self._fresh_peer_head(peer, now=now)
            if height <= 0:
                continue
            if best_height is None or height > best_height:
                best_height = height
                best_peer = peer
                best_hash = head_hash
        return best_peer, best_height, best_hash

    def _peer_is_sync_eligible(self, peer: _PeerState) -> bool:
        ok, _reason = self._sync_peer_eligibility(peer, now=time.time())
        return ok

    def _peer_score_snapshot(self, peer: _PeerState) -> Optional[float]:
        if not peer.peer_id:
            return None
        try:
            entry = self.peerstore.get(peer.peer_id)
        except Exception:
            entry = None
        if entry is None:
            return None
        return float(getattr(entry, "score", 0.0))

    def _select_sync_target_tip(self, now: float) -> Optional[_SyncTargetTip]:
        best: Optional[_SyncTargetTip] = None
        for peer in self._peers.values():
            if not peer.hello_done.is_set():
                continue
            info = self._sync_peer_heads.get(peer.remote)
            if info is None:
                continue
            if now - info.updated_at > self._sync_peer_head_stale_sec:
                continue
            if info.cooldown_until and info.cooldown_until > now:
                continue
            if not self._peer_is_sync_eligible(peer):
                continue
            head_hash = info.head_hash or bytes((peer.hello or {}).get("head_hash") or b"")
            if not head_hash:
                continue
            total_work = None
            peer_hello = peer.hello or {}
            raw_work = peer_hello.get("total_work") or peer_hello.get("chain_work")
            if raw_work is not None:
                with contextlib.suppress(Exception):
                    total_work = int(raw_work)
            timestamp = None
            with contextlib.suppress(Exception):
                ts_val = peer_hello.get("timestamp")
                if ts_val is not None:
                    timestamp = int(ts_val)
            peer_score = self._peer_score_snapshot(peer)
            candidate = _SyncTargetTip(
                height=int(info.height),
                hash=bytes(head_hash),
                peer_id=peer.peer_id or peer.remote,
                last_seen_ts=info.updated_at,
                total_work=total_work,
                timestamp=timestamp,
                peer_score=peer_score,
            )
            if best is None:
                best = candidate
                continue
            if candidate.total_work is not None or best.total_work is not None:
                if candidate.total_work is None:
                    continue
                if best.total_work is None or candidate.total_work > best.total_work:
                    best = candidate
                    continue
                if candidate.total_work < best.total_work:
                    continue
            if candidate.height > best.height:
                best = candidate
                continue
            if candidate.height < best.height:
                continue
            cand_ts = candidate.timestamp if candidate.timestamp is not None else 1 << 60
            best_ts = best.timestamp if best.timestamp is not None else 1 << 60
            if cand_ts < best_ts:
                best = candidate
                continue
            if cand_ts > best_ts:
                continue
            cand_score = candidate.peer_score if candidate.peer_score is not None else 0.0
            best_score = best.peer_score if best.peer_score is not None else 0.0
            if cand_score > best_score:
                best = candidate
                continue
        # Heaviest-chain guard: a sync target must be strictly AHEAD of our own
        # head. A peer on a shorter/lighter fork — e.g. after this node's chain
        # was restored to a height ABOVE the network's diverged fork — must never
        # become a sync target. Otherwise the sync driver loops forever on
        # "Sync target hash mismatch" trying to reorg backward to a lighter
        # chain, freezing the head (the exact "chain reset" stall). Such a peer
        # should reorg up to us; the bulk sync driver only ever pulls us forward.
        if best is not None:
            try:
                local_height, _ = self._local_head()
                if local_height is not None and int(best.height) <= int(local_height):
                    return None
            except Exception:
                pass
        return best

    def _update_sync_target_tip(self, now: float) -> Optional[_SyncTargetTip]:
        tip = self._select_sync_target_tip(now)
        if tip is None:
            if self._sync_target_tip is not None:
                log.info(
                    "Sync target cleared",
                    extra={
                        "reason": "no_eligible_target_tip",
                        "previous_height": self._sync_target_tip.height,
                        "previous_hash": self._sync_target_tip.hash.hex(),
                        "previous_peer_id": self._sync_target_tip.peer_id,
                    },
                )
            self._sync_target_tip = None
            self._sync_last_target_hash = None
            return None
        changed = (
            self._sync_target_tip is None
            or tip.hash != self._sync_target_tip.hash
            or tip.height != self._sync_target_tip.height
        )
        self._sync_target_tip = tip
        self._sync_last_target_hash = tip.hash
        if changed:
            self._stats["sync_target_set"] += 1
            log.info(
                "Sync target updated",
                extra={
                    "height": tip.height,
                    "hash": tip.hash.hex(),
                    "peer_id": tip.peer_id,
                    "timestamp": tip.timestamp,
                    "total_work": tip.total_work,
                    "peer_score": tip.peer_score,
                },
            )
        return tip

    def _peer_id_from_addr(self, address: str) -> str:
        if "/p2p/" in address:
            return address.split("/p2p/", 1)[1].split("/")[0]
        if "/ipfs/" in address:
            return address.split("/ipfs/", 1)[1].split("/")[0]
        return hashlib.sha256(address.encode()).hexdigest()[:32]

    def _seed_peerstore(self, addresses: list[str]) -> int:
        added = 0
        fallback_port = self._local_listen_port()
        for raw in addresses:
            addr = self._sanitize_peer_addr(raw, fallback_port=fallback_port)
            if not addr:
                continue
            peer_id = self._peer_id_from_addr(addr)
            try:
                self.peerstore.add(peer_id=peer_id, addrs=[addr], direction="outbound")
                self.peerstore.record_seen(peer_id, addr)
                self._addrman.add(addr, source="seed")
                added += 1
            except Exception:
                continue
        if added:
            self._schedule_peer_persist()
        return added

    def peer_count(self) -> int:
        return self._peer_registry.peer_count() + self.bootstrap_peer_bonus()

    async def import_peers(self, addresses: list[str]) -> dict[str, Any]:
        if not addresses:
            return {
                "success": False,
                "added": 0,
                "skipped": 0,
                "invalid": 0,
                "dial_attempted": 0,
                "dial_success": 0,
                "errors": ["no addresses provided"],
            }

        fallback_port = self._local_listen_port()
        normalized: list[str] = []
        errors: list[str] = []
        skipped = 0
        invalid = 0
        for raw in addresses:
            addr = self._sanitize_peer_addr(raw, fallback_port=fallback_port)
            if not addr:
                skipped += 1
                invalid += 1
                errors.append(f"invalid address: {raw}")
                continue
            normalized.append(addr)

        deduped = list(dict.fromkeys(normalized))
        added = self._seed_peerstore(deduped)

        tasks = [
            self._create_child_task(self._dial(addr), name=f"p2p.import_dial@{addr}")
            for addr in deduped
        ]
        dial_attempted = len(tasks)
        dial_success = 0
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for addr, result in zip(deduped, results):
                if isinstance(result, Exception):
                    errors.append(f"{addr}: {result}")
                    continue
                if result:
                    dial_success += 1
                else:
                    errors.append(f"{addr}: dial failed")

        self._sync_wakeup.set()
        return {
            "success": bool(added or dial_attempted),
            "added": added,
            "skipped": skipped,
            "invalid": invalid,
            "dial_attempted": dial_attempted,
            "dial_success": dial_success,
            "errors": errors,
        }

    async def force_sync(self) -> dict[str, Any]:
        self._sync_kick(reason="force_sync", aggressive=True)
        return await self._sync_once(force=True)

    def _request_sync(self, *, reason: str) -> None:
        self._sync_kick(reason=reason, aggressive=False)

    def _default_sync_boost_tick_sec(self) -> float:
        return min(
            self._sync_tick_sec,
            max(MIN_SYNC_BOOST_TICK_SEC, self._sync_tick_sec / 5.0),
        )

    def _sync_kick(self, *, reason: str, aggressive: bool = False) -> None:
        now = time.time()
        self._sync_requested = True
        self._sync_requested_at = now
        if aggressive:
            self._sync_boost_until = now + max(1.0, self._sync_request_timeout)
            self._sync_boost_tick_sec = self._default_sync_boost_tick_sec()
        self._sync_wakeup.set()
        log.info(
            "Sync kick requested",
            extra={"reason": reason, "aggressive": aggressive},
        )

    def _note_sync_progress(
        self,
        *,
        reason: str,
        head_height: Optional[int] = None,
        head_hash: Optional[str] = None,
        header_height: Optional[int] = None,
        block_fetch_height: Optional[int] = None,
        queue_depth: Optional[int] = None,
    ) -> None:
        now = time.time()
        self._sync_last_progress_at = now
        self._stats["sync_progress"] += 1
        if head_height is not None:
            self._sync_last_head_height = int(head_height)
        if head_hash is not None:
            self._sync_last_head_hash = head_hash
        if header_height is not None:
            self._sync_last_header_height = int(header_height)
        if block_fetch_height is not None:
            self._sync_last_block_fetch_height = int(block_fetch_height)
        if queue_depth is not None:
            self._sync_last_queue_depth = int(queue_depth)
        self._sync_stale_network_best_at = 0.0
        self._sync_stale_network_best_count = 0
        self._sync_watchdog_last_action_at = 0.0
        log.debug("Sync progress recorded", extra={"reason": reason})

    def boost_sync(
        self, *, duration_s: float, tick_ms: Optional[int] = None
    ) -> dict[str, Any]:
        if duration_s <= 0:
            self._sync_boost_until = None
            self._sync_boost_tick_sec = None
            return {"boosted": False}
        self._sync_boost_until = time.time() + float(duration_s)
        if tick_ms is not None:
            requested_tick = max(MIN_SYNC_BOOST_TICK_SEC, float(tick_ms) / 1000.0)
            self._sync_boost_tick_sec = min(self._sync_tick_sec, requested_tick)
        else:
            self._sync_boost_tick_sec = self._default_sync_boost_tick_sec()
        self._sync_wakeup.set()
        return {
            "boosted": True,
            "boost_until": self._sync_boost_until,
            "boost_tick_ms": int(self._sync_boost_tick_sec * 1000),
        }

    async def force_sync_with_cache(
        self, *, clear_cache: bool = False
    ) -> dict[str, Any]:
        if clear_cache:
            self._clear_sync_cache()
        self._sync_kick(reason="force_sync_with_cache", aggressive=True)
        result = await self._sync_once(force=True)
        if clear_cache:
            result["cache_cleared"] = True
        return result

    def pause_sync(self) -> dict[str, Any]:
        self._sync_paused = True
        return {"paused": True}

    def enable_sync(self, enabled: bool = True) -> dict[str, Any]:
        self._sync_enabled = bool(enabled)
        if self._sync_enabled:
            self._sync_wakeup.set()
        return {"enabled": self._sync_enabled}

    def resume_sync(self) -> dict[str, Any]:
        self._sync_paused = False
        self._sync_wakeup.set()
        return {"paused": False}

    def set_sync_target(self, height: Optional[int]) -> dict[str, Any]:
        if height is not None and height < 0:
            height = None
        self._update_sync_target_height(height, reason="manual_set_sync_target")
        self._sync_wakeup.set()
        return {"target_height": self._sync_target_height}

    def _update_sync_target_height(self, height: Optional[int], *, reason: str) -> None:
        new_target = None if height is None else int(height)
        old_target = self._sync_target_height
        self._sync_target_height = new_target
        if new_target is None:
            if old_target is not None:
                log.info("SYNC_TARGET_CLEARED", extra={"reason": reason})
            return
        if old_target != new_target:
            log.info(
                "SYNC_TARGET_SET",
                extra={"reason": reason, "target_height": new_target},
            )

    async def dial(self, addr: str) -> None:
        normalized = self._sanitize_peer_addr(
            addr, fallback_port=self._local_listen_port()
        )
        if normalized:
            await self._dial(normalized)
            self._sync_wakeup.set()

    def status(self) -> Dict[str, Any]:
        height, hh = self._local_head()
        return {
            "peer_id": self._peer_id_bytes.hex(),
            "chain_id": self.chain_id,
            "head_height": height,
            "head_hash": hh,
            "peers": int(self._stats.get("peers", 0)),
            "stats": dict(self._stats),
        }

    async def debug_status(self) -> Dict[str, Any]:
        pending = await self._mempool_size()
        txrelay_snapshot = self._txrelay.snapshot()
        txrelay_peer_map = {
            entry.get("conn_id"): entry for entry in txrelay_snapshot.get("peers", [])
        }
        async with self._peer_lock:
            peers = list(self._peers.values())
        peer_entries: list[dict[str, Any]] = []
        for peer in peers:
            hello = peer.hello or {}
            caps = list(hello.get("capabilities") or [])
            peer_key = self._peer_tx_key(peer)
            txrelay_peer = txrelay_peer_map.get(peer_key, {})
            peer_entries.append(
                {
                    "remote": peer.remote,
                    "conn_id": peer.session_id,
                    "peer_id": peer.peer_id,
                    "peer_node_id": peer.peer_id,
                    "direction": peer.direction,
                    "handshake_complete": peer.hello_done.is_set(),
                    "chain_match": self._peer_chain_matches(peer),
                    "relay_caps": {
                        "txs": "txs" in caps or "tx" in caps,
                        "blocks": "blocks" in caps,
                        "headers": "headers" in caps,
                    },
                    "last_inv_sent_at": peer.last_tx_inv_sent_at,
                    "last_inv_recv_at": peer.last_tx_inv_recv_at,
                    "last_tx_data_sent_at": peer.last_tx_data_sent_at,
                    "last_tx_data_recv_at": peer.last_tx_data_recv_at,
                    "known_tx_inv_lru": len(self._tx_inv_sent_by_peer.get(peer_key, {})),
                    "known_tx_sent_lru": len(self._tx_sent_by_peer.get(peer_key, {})),
                    "txrelay_known_txids": txrelay_peer.get("known_txids"),
                    "txrelay_known_txids_sample": txrelay_peer.get(
                        "known_txids_sample"
                    ),
                    "txrelay_inv_queue": txrelay_peer.get("inv_queue"),
                    "txrelay_last_sync_sent_at": txrelay_peer.get("last_sync_sent_at"),
                    "txrelay_last_sync_recv_at": txrelay_peer.get("last_sync_recv_at"),
                    "txrelay_conn_id": txrelay_peer.get("conn_id"),
                    "txrelay_peer_node_id": txrelay_peer.get("peer_node_id"),
                    "relay_eligible": self._tx_peer_eligibility(peer)[0],
                }
            )

        return {
            "tx_relay": {
                "enabled": self._tx_relay_allowed(),
                "bootstrap_mode": self._bootstrap_mode,
                "relay_flags": {
                    "tx_relay": self._tx_relay_enabled,
                    "tx_gossip": self._tx_gossip_enabled,
                    "mempool_gossip": self._mempool_gossip_enabled,
                    "p2p_tx_enabled": self._p2p_tx_enabled,
                },
                "queue_depth": pending,
                "inflight_requests": len(self._tx_requested),
                "seen_inv": len(self._tx_inv_seen),
                "rebroadcast_task_alive": bool(
                    self._tx_rebroadcast_task
                    and not self._tx_rebroadcast_task.done()
                ),
                "last_heartbeat_at": self._tx_relay_heartbeat_at,
            },
            "tx_relay_v2": {
                "enabled": self._tx_relay_enabled and self._p2p_tx_enabled,
                "inflight": txrelay_snapshot.get("inflight"),
                "mempool_sync_interval_s": self._tx_mempool_sync_interval_s,
                "mempool_sync_limit": self._tx_mempool_sync_limit,
                "mempool_watchdog_interval_s": self._tx_mempool_watchdog_interval_s,
                "mempool_watchdog_limit": self._tx_mempool_watchdog_limit,
            },
            "sync_metrics": self._sync_metrics_snapshot(),
            "peers": peer_entries,
            "recent_rejects": list(self._tx_recent_rejects),
        }

    async def relay_tx(self, raw_cbor: bytes) -> str:
        from core.utils.hash import sha3_256
        try:
            from core.utils.tx import normalize_tx_bytes
        except Exception:
            normalize_tx_bytes = None

        canonical_raw = raw_cbor
        if normalize_tx_bytes is not None:
            try:
                canonical_raw = normalize_tx_bytes(raw_cbor)
            except Exception:
                canonical_raw = raw_cbor

        txh = sha3_256(canonical_raw)
        self._remember(self._seen_tx, txh, self._seen_tx_cap)
        if not self._tx_relay_enabled or not self._p2p_tx_enabled:
            log.info(
                "tx relay disabled; skipping announce",
                extra={"tx_hash": txh.hex()},
            )
            return "0x" + txh.hex()

        # best-effort local admission
        admitted, reason = await self._admit_tx_result(
            canonical_raw, local=True, origin_peer="local"
        )
        if admitted:
            mempool_size = await self._mempool_size()
            log.info("tx accepted for relay", extra={"tx_hash": txh.hex()})
            log.info(
                "TX_ACCEPTED",
                extra={
                    "hash": txh.hex(),
                    "origin": "local",
                    "mempool_size": mempool_size,
                },
            )
        else:
            log.debug(
                "tx relay admission failed",
                extra={"tx_hash": txh.hex(), "reason": reason},
            )
            log.info(
                "TX_VALIDATE_REJECT",
                extra={
                    "hash": txh.hex(),
                    "reason": self._tx_reject_category(reason),
                    "detail": reason,
                    **self._tx_debug_fields(raw_cbor),
                },
            )
            log.info(
                "TX_MEMPOOL_REJECTED",
                extra={
                    "hash": txh.hex(),
                    "origin": "local",
                    "reason": reason,
                },
            )
            self._record_tx_reject(
                tx_hash=txh.hex(), origin="local", reason=reason
            )

        async with self._peer_lock:
            peer_count = len(self._peers)
        log.info(
            "TX_INV_ENQUEUE",
            extra={"hash": txh.hex(), "peers": peer_count},
        )
        if admitted:
            if self._tx_relay_v2_enabled:
                await self._txrelay.on_mempool_add(txh, canonical_raw)
            else:
                await self._legacy_tx_relay_announce(txh, canonical_raw, "local")
        log.info(
            "tx relay announced",
            extra={"tx_hash": txh.hex(), "peers": peer_count},
        )
        return "0x" + txh.hex()

    async def request_missing_txids(
        self,
        limit: int = 128,
        force: bool = False,
        *,
        max_peers: int = 2,
        batch_size: int = 64,
        include_details: bool = False,
    ) -> int | dict[str, Any]:
        if (
            not self._tx_relay_enabled
            or not self._p2p_tx_enabled
            or not self._tx_relay_v2_enabled
        ):
            return {"requested": 0, "requested_txids": [], "requested_peers": []} if include_details else 0
        return await self._txrelay.request_missing_known(
            limit=limit,
            force=force,
            trigger="request_missing_txids",
            max_peers=max_peers,
            batch_size=batch_size,
            include_details=include_details,
        )

    async def sync_all_peer_mempools(self, timeout_s: float = 2.0) -> int:
        """
        Synchronize mempools from all connected peers.
        
        This is called when building a block template to ensure the miner
        has transactions from all other nodes in the network.
        
        Args:
            timeout_s: Maximum time to wait for sync completion
            
        Returns:
            Number of peers synced
        """
        if (
            not self._tx_relay_enabled
            or not self._p2p_tx_enabled
            or not self._tx_relay_v2_enabled
        ):
            return 0
        return await self._txrelay.sync_all_peers(timeout_s=timeout_s)

    async def relay_block(self, block_hash: bytes) -> None:
        self._remember(self._seen_blocks, block_hash, self._seen_block_cap)
        await self._broadcast_inv(
            [InvItem(typ=InvType.BLOCK, h=block_hash)], exclude_remote=None, is_tx=False
        )
        await self._broadcast_block_announce(block_hash, exclude_remote=None)

    async def _propagate_network_height_update(self, network_best_height: int) -> None:
        """
        Propagate network best height updates to peers to ensure multi-hop height awareness.

        This is called when we discover a significantly higher network height, ensuring
        all peers in the network stay informed about the true highest height even if
        they're not directly connected to the node with that height.
        """
        async with self._peer_lock:
            peers = list(self._peers.values())

        for peer in peers:
            if not peer.hello_done.is_set():
                continue
            if not peer.hello:
                continue

            # Update peer's hello with new network best if it's higher
            try:
                peer_network_best = peer.hello.get("network_best_height")
                if peer_network_best is None or int(peer_network_best) < network_best_height:
                    # Store this for future reference, actual propagation happens
                    # on next Hello exchange or via other sync mechanisms
                    pass
            except Exception:
                pass

    # ---------------------------------------------------------------------
    # Connection management
    # ---------------------------------------------------------------------

    async def _accept_loop(self) -> None:
        try:
            while self._running:
                conn = await self._transport.accept()
                self._create_child_task(
                    self._register_conn(conn, direction="inbound"), name="p2p.peer.in"
                )
        except asyncio.CancelledError:
            return
        except Exception:
            if self._running:
                log.warning("accept loop failed", exc_info=True)

    async def _dial_loop(self) -> None:
        target_outbound = int(os.environ.get("ANIMICA_P2P_OUTBOUND", "8") or 8)
        target_outbound = max(target_outbound, self._min_outbound)
        try:
            while self._running:
                await asyncio.sleep(1.0)

                async with self._peer_lock:
                    outbound = [
                        p for p in self._peers.values() if p.direction == "outbound"
                    ]
                    active_keys = {self._addr_key(p.remote) for p in self._peers.values()}
                if len(outbound) >= target_outbound:
                    if self._seeding_mode:
                        self._seeding_mode = False
                        log.info("Seeding mode complete: outbound peers at target")
                    continue

                if not self._seeding_mode and self._addrman.size() < target_outbound:
                    self._seeding_mode = True
                    log.info("Re-entering seeding mode (addrman size low)")

                candidates: list[str] = []
                candidates.extend(self._addrman.sample(limit=64, exclude=set()))
                if self._seeding_mode or not candidates:
                    candidates.extend(self.seeds)
                try:
                    for peer in self.peerstore.list_known(
                        limit=64, order_by="last_seen"
                    ):
                        addr = getattr(peer, "address", None)
                        if isinstance(addr, str) and addr:
                            candidates.append(addr)
                except Exception:
                    pass

                addrs: list[str] = []
                fallback_port = self._local_listen_port()
                for c in candidates:
                    normalized = self._sanitize_peer_addr(c, fallback_port=fallback_port)
                    if normalized:
                        addrs.append(normalized)

                addrs = list(dict.fromkeys(addrs))
                now = time.time()
                for addr in addrs:
                    # Skip peers we're already connected to so we can reach new ones.
                    addr_key = self._addr_key(addr)
                    if addr_key in active_keys:
                        continue
                    if addr in self._invalid_seed_addrs:
                        continue
                    if not self._is_peer_exempt(addr) and self._is_banned(addr):
                        continue
                    if addr_key in self._dial_inflight:
                        continue
                    if self._dial_backoff.get(addr_key, 0.0) > now:
                        continue
                    if (
                        self._max_outbound_per_netgroup > 0
                        and self._netgroup_key(addr)
                        in {
                            p.netgroup
                            for p in outbound
                            if p.netgroup is not None
                        }
                        and sum(
                            1
                            for p in outbound
                            if p.netgroup == self._netgroup_key(addr)
                        )
                        >= self._max_outbound_per_netgroup
                    ):
                        continue
                    self._dial_inflight.add(addr_key)
                    is_seed = addr_key.strip().lower() in self._seed_keys
                    if is_seed and self._bootstrap_seed_rate_limit > 0:
                        recent = self._seed_attempts_recent(addr_key)
                        if recent >= self._bootstrap_seed_rate_limit:
                            log.info(
                                "Seed dial rate limited",
                                extra={
                                    "addr": addr,
                                    "attempts": recent,
                                    "window_s": self._bootstrap_seed_rate_window,
                                },
                            )
                            self._dial_inflight.discard(addr_key)
                            continue
                    if is_seed:
                        log.info("Attempting dial to seed %s", addr)
                    self._create_child_task(
                        self._dial(addr, is_seed=is_seed),
                        name=f"p2p.dial@{addr}",
                    )
                    break
        except asyncio.CancelledError:
            return

    async def _resolve_seed_host(self, addr: str) -> bool:
        host: Optional[str] = None
        port: Optional[int] = None
        parsed = self._normalize_peer_addr(
            addr, fallback_port=self._local_listen_port(), source="seed_resolve"
        )
        if parsed.addr:
            host = parsed.addr.host
            port = parsed.addr.port
        if not host:
            return True
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            pass
        try:
            loop = asyncio.get_running_loop()
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP),
                timeout=3.0,
            )
            log.info("Resolved seed host %s to %d address(es)", host, len(infos))
            return True
        except Exception:
            return False

    async def _dial(
        self, addr: str, *, is_seed: bool = False, feeler: bool = False
    ) -> bool:
        parsed = self._normalize_peer_addr(
            addr, fallback_port=self._local_listen_port(), source="dial"
        )
        if not parsed.addr:
            log.info("Skipping unsupported dial target %s", addr)
            return False
        if self._is_self_address(parsed.addr.host, parsed.addr.port):
            log.info(
                "Skipping self dial target %s",
                parsed.addr.canonical,
                extra={
                    "requested_addr": addr,
                    "listen_addrs": list(self.listen_addrs),
                    "listen_ports": sorted(self._listen_ports()),
                    "self_endpoints": self._self_endpoints(),
                },
            )
            return False
        addr = parsed.addr.canonical
        addr_key = self._addr_key(addr)
        if self._is_outbound_only_blocklisted(peer_id=None, remote=addr):
            self._stats["dial_skipped_outbound_only"] += 1
            self._dial_inflight.discard(addr_key)
            log.info(
                "Skipping dial to outbound-only blocklisted peer",
                extra={"peer_addr": addr},
            )
            return False
        conn_trace_id = self._new_conn_trace_id()
        self._dial_attempt_total += 1
        self._stats["dial_attempts"] += 1
        inc_dial_attempt("out")
        dial_started_at = time.time()
        log.info(
            "P2P_DIAL_START",
            extra={"conn_trace_id": conn_trace_id, "peer_addr": addr},
        )
        if is_seed:
            resolved = await self._resolve_seed_host(addr)
            if not resolved:
                self._mark_dial_failure(
                    addr,
                    is_seed=True,
                    error="dns_lookup_failed",
                    stage="tcp",
                    conn_trace_id=conn_trace_id,
                )
                self._dial_inflight.discard(addr_key)
                return False
        try:
            conn = await self._transport.dial(addr, timeout=5.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            err = f"{exc.__class__.__name__}: {exc}"
            stage = "handshake" if isinstance(exc, HandshakeError) else "tcp"
            if "handshake" in err.lower():
                stage = "handshake"
            if stage == "handshake":
                self._record_handshake_failure("transport_handshake")
            self._mark_dial_failure(
                addr,
                is_seed=is_seed,
                error=err,
                stage=stage,
                conn_trace_id=conn_trace_id,
            )
            return False
        finally:
            self._dial_inflight.discard(addr_key)
        conn.info.conn_trace_id = conn_trace_id
        dial_latency_ms = (time.time() - dial_started_at) * 1000.0
        log.info(
            "P2P_TCP_CONNECTED",
            extra={
                "conn_trace_id": conn_trace_id,
                "peer_addr": addr,
                "direction": "out",
                "latency_ms": round(dial_latency_ms, 2),
            },
        )
        self._mark_dial_success(addr, is_seed=is_seed, conn_trace_id=conn_trace_id)
        self._stats["dial_successes"] += 1
        inc_dial_success("out")
        await self._register_conn(
            conn, direction="outbound", feeler=feeler, conn_trace_id=conn_trace_id
        )
        return True

    async def _register_conn(
        self,
        conn: Any,
        *,
        direction: str,
        feeler: bool = False,
        conn_trace_id: Optional[str] = None,
    ) -> None:
        remote = getattr(conn.info, "remote_addr", None) or "unknown"
        if conn_trace_id is None:
            conn_trace_id = getattr(conn.info, "conn_trace_id", None) or self._new_conn_trace_id()
        if (
            not self._allow_self_peers
            and self._is_self_address(
                self._extract_host(remote), self._extract_port(remote) or 0
            )
        ):
            log.info("Rejecting self peer %s", remote)
            with contextlib.suppress(Exception):
                await conn.close()
            return
        if not self._is_peer_exempt(remote) and self._is_banned(remote):
            log.info("Rejecting banned peer %s", remote)
            with contextlib.suppress(Exception):
                await conn.close()
            return
        try:
            stream = await conn.open_stream()
        except Exception:
            with contextlib.suppress(Exception):
                await conn.close()
            return
        try:
            session = self._peer_registry.register(remote, direction)
        except ValueError as exc:
            log.info("Rejecting %s peer %s: %s", direction, remote, exc)
            with contextlib.suppress(Exception):
                await conn.close()
            return

        netgroup = self._netgroup_key(remote)
        async with self._peer_lock:
            existing = [
                p
                for p in self._peers.values()
                if p.direction == direction and p.netgroup == netgroup
            ]
        limit = (
            self._max_inbound_per_netgroup
            if direction == "inbound"
            else self._max_outbound_per_netgroup
        )
        if limit > 0 and len(existing) >= limit:
            log.info(
                "Rejecting %s peer %s: netgroup %s limit reached",
                direction,
                remote,
                netgroup,
            )
            with contextlib.suppress(Exception):
                await conn.close()
            return

        peer = _PeerState(
            session_id=session.session_id,
            remote=remote,
            direction=direction,
            conn=conn,
            stream=stream,
            framer=Framer(aead=None),
            write_lock=asyncio.Lock(),
            conn_trace_id=conn_trace_id,
            connected_at=session.connected_at,
            feeler=feeler,
            netgroup=netgroup,
        )

        log.info(
            "Peer TCP connected",
            extra={
                "remote": remote,
                "direction": direction,
                "stage": "tcp_connected",
                "conn_trace_id": conn_trace_id,
            },
        )
        log.info(
            "P2P_TCP_CONNECTED",
            extra={
                "conn_trace_id": conn_trace_id,
                "peer_addr": remote,
                "direction": "in" if direction == "inbound" else "out",
                "latency_ms": 0.0,
            },
        )

        async with self._peer_lock:
            self._peers[self._peer_key(remote, direction)] = peer
            self._peers_by_session[peer.session_id] = peer
            self._stats["peers"] = self._peer_registry.peer_count()
            self._last_peer_connect_at = time.time()

        self._create_child_task(self._peer_loop(peer), name=f"p2p.peer@{remote}")
        self._create_child_task(
            self._enforce_handshake_timeout(peer), name=f"p2p.handshake@{remote}"
        )

    async def _enforce_handshake_timeout(self, peer: _PeerState) -> None:
        try:
            await asyncio.wait_for(
                peer.hello_done.wait(), timeout=self._peer_registry.handshake_timeout_s
            )
        except asyncio.TimeoutError:
            peer_key = self._peer_key(peer.remote, peer.direction)
            should_grant_grace = (
                self._hello_timeout_grace_s > 0
                and peer_key not in self._hello_timeout_grace_used
                and (self._sync_target_height is not None or self._network_best_height() is not None)
            )
            if should_grant_grace:
                self._hello_timeout_grace_used.add(peer_key)
                log.warning(
                    "Deferring hello-timeout drop during active sync",
                    extra={
                        "remote": peer.remote,
                        "direction": peer.direction,
                        "grace_s": self._hello_timeout_grace_s,
                        "target_height": self._sync_target_height,
                        "network_best_height": self._network_best_height(),
                    },
                )
                try:
                    await asyncio.wait_for(
                        peer.hello_done.wait(), timeout=self._hello_timeout_grace_s
                    )
                    return
                except asyncio.TimeoutError:
                    pass
            log.info("Dropping peer %s due to hello timeout", peer.remote)
            await self._drop_peer(peer, reason="hello_timeout")

    def _inbound_rate_check(self, peer: _PeerState) -> str:
        """Per-peer inbound message-rate guard (ANM-H03/H04 DoS cap).

        Returns one of:
          "ok"   — within budget, process the message normally
          "shed" — momentarily over budget: drop THIS message but keep the peer
          "ban"  — sustained flood: caller should penalize + disconnect

        Exempt, docker-local (and thereby trusted-local) peers are never
        throttled. Uses a per-peer token bucket sized generously enough that a
        legitimate peer — even mid block/header sync — never trips it.
        """
        if not self._peer_msg_rate_enabled:
            return "ok"
        host = self._extract_host(peer.remote) or ""
        if self._is_peer_exempt(peer.remote) or (host and self._is_docker_local(host)):
            return "ok"
        now = time.monotonic()
        if not peer.rl_primed:
            peer.rl_primed = True
            peer.rl_last_mono = now
            peer.rl_tokens = self._peer_msg_rate_burst
        else:
            dt = now - peer.rl_last_mono
            if dt > 0:
                peer.rl_tokens = min(
                    self._peer_msg_rate_burst,
                    peer.rl_tokens + dt * self._peer_msg_rate_per_s,
                )
                peer.rl_last_mono = now
        if peer.rl_tokens >= 1.0:
            peer.rl_tokens -= 1.0
            # Forgive accrued strikes once comfortably back under budget so a
            # brief burst never leaves a long-lived peer one strike from a ban.
            if peer.rl_strikes and peer.rl_tokens > (self._peer_msg_rate_burst * 0.5):
                peer.rl_strikes = max(0, peer.rl_strikes - 1)
            return "ok"
        # Over budget.
        peer.rl_strikes += 1
        wnow = time.time()
        if wnow - peer.rl_last_warn_s > 5.0:
            peer.rl_last_warn_s = wnow
            log.warning(
                "Peer exceeded inbound message rate",
                extra={
                    "remote": peer.remote,
                    "peer_id": peer.peer_id or "unknown",
                    "strikes": peer.rl_strikes,
                    "rate_per_s": self._peer_msg_rate_per_s,
                    "burst": self._peer_msg_rate_burst,
                },
            )
        if peer.rl_strikes >= self._peer_msg_rate_max_strikes:
            return "ban"
        return "shed"

    async def _peer_loop(self, peer: _PeerState) -> None:
        # Send HELLO immediately (both sides do this; handler is symmetric).
        try:
            await self._send_hello(peer)
        except Exception as exc:
            self._record_handshake_failure("hello_send_failed")
            log.warning(
                "Failed to send HELLO",
                extra={"remote": peer.remote, "direction": peer.direction},
                exc_info=exc,
            )
            log.info(
                "P2P_DIAL_FAIL",
                extra={
                    "conn_trace_id": peer.conn_trace_id,
                    "peer_addr": peer.remote,
                    "stage": "handshake",
                    "reason": "hello_send_failed",
                },
            )
            await self._drop_peer(peer, reason="hello_send_failed")
            return

        disconnect_reason = "loop_exit"
        try:
            while self._running:
                data = await peer.stream.recv()
                if data == b"":
                    disconnect_reason = "remote_closed"
                    break
                self._peer_registry.mark_seen(peer.session_id)
                peer.last_msg_at = time.time()
                # ANM-H03/H04: shed / disconnect peers that flood us with
                # messages before spending CPU on decode + dispatch.
                verdict = self._inbound_rate_check(peer)
                if verdict == "ban":
                    self._penalize_peer(
                        peer,
                        "msg_rate_flood",
                        points=self._score_points["malformed_message"],
                        ban_ttl=self._peer_msg_rate_ban_ttl,
                    )
                    disconnect_reason = "msg_rate_flood"
                    break
                if verdict == "shed":
                    continue
                try:
                    frame = unpack_frame(data, aead=None)
                except Exception:
                    self._penalize_peer(
                        peer,
                        "malformed_frame",
                        points=self._score_points["malformed_message"],
                    )
                    disconnect_reason = "malformed_frame"
                    break
                try:
                    await self._handle(peer, frame.msg_id, frame.payload)
                except PeerMisbehavior as exc:
                    self._penalize_peer(
                        peer,
                        exc.reason,
                        points=exc.points,
                        ban_ttl=exc.ban_ttl,
                    )
                    disconnect_reason = f"peer_error:{exc.reason}"
                    break
        except asyncio.CancelledError:
            disconnect_reason = "cancelled"
        except Exception as exc:
            disconnect_reason = f"error:{type(exc).__name__}"
            log.warning(
                "peer loop error",
                extra={"remote": peer.remote, "reason": disconnect_reason},
                exc_info=True,
            )
        finally:
            await self._drop_peer(peer, reason=disconnect_reason)

    async def _drop_peer(self, peer: _PeerState, *, reason: str) -> None:
        with contextlib.suppress(Exception):
            await peer.conn.close()
        self._hello_timeout_grace_used.discard(self._peer_key(peer.remote, peer.direction))
        self._peer_registry.remove(peer.session_id)
        self._txrelay.unregister_peer(self._peer_tx_key(peer))
        requeued_blocks = 0
        removed_header_votes = 0
        discarded_on_drop = {"buffer": 0, "inflight": 0, "cache": 0}

        async with self._peer_lock:
            if peer.pending_headers is not None and not peer.pending_headers.done():
                peer.pending_headers.set_result(None)
            peer.pending_headers = None
            peer.pending_header_request_id = None
            inflight_block_hashes = [
                block_hash
                for block_hash, remote in list(self._sync_inflight_peers.items())
                if remote == peer.remote
            ]
            for block_hash in inflight_block_hashes:
                self._sync_inflight_blocks.pop(block_hash, None)
                self._sync_inflight_peers.pop(block_hash, None)
                self._sync_inflight_block_requests.pop(block_hash, None)
                if not self._has_block(block_hash) and block_hash not in self._sync_block_queue_set:
                    self._sync_block_queue.appendleft(block_hash)
                    self._sync_block_queue_set.add(block_hash)
                    if block_hash not in self._sync_block_queue_heights:
                        self._sync_block_queue_heights[block_hash] = -1
                    requeued_blocks += 1
            inflight_header_keys = [
                key
                for key in list(self._sync_inflight_header_requests.keys())
                if key[0] == peer.remote
            ]
            if inflight_header_keys:
                for key in inflight_header_keys:
                    self._sync_inflight_header_requests.pop(key, None)
                self._sync_inflight_headers = len(self._sync_inflight_header_requests)
                self._sync_wakeup.set()
            self._peers.pop(self._peer_key(peer.remote, peer.direction), None)
            self._peers_by_session.pop(peer.session_id, None)
            self._stats["peers"] = self._peer_registry.peer_count()
            self._last_peer_disconnect_at = time.time()
        self._sync_peer_heads.pop(peer.remote, None)
        self._sync_peer_backoff.pop(self._peer_backoff_key(peer), None)
        self._sync_peer_backoff_reason.pop(self._peer_backoff_key(peer), None)
        self._sync_block_peer_backoff.pop(self._peer_backoff_key(peer), None)
        self._sync_block_peer_backoff_reason.pop(self._peer_backoff_key(peer), None)
        if self._sync_active_header_peer == peer.remote:
            self._sync_active_header_peer = None
        if self._sync_active_block_peer == peer.remote:
            self._sync_active_block_peer = None
        self._update_sync_target_tip(time.time())
        removed_header_votes = self._remove_header_votes_from_peer(peer.remote)
        discarded_on_drop = self._discard_blocks_from_ineligible_peers()
        if (
            discarded_on_drop.get("buffer", 0)
            or discarded_on_drop.get("inflight", 0)
            or discarded_on_drop.get("cache", 0)
        ):
            self._sync_wakeup.set()

        if peer.peer_id:
            with contextlib.suppress(Exception):
                self.peerstore.record_disconnection(peer.peer_id, reason=reason)

        uptime = time.time() - peer.connected_at if peer.connected_at else 0.0
        self._record_disconnect_reason(reason)
        if requeued_blocks:
            self._sync_last_block_error = STALL_BLOCK_PEER_UNRESPONSIVE
            self._sync_last_block_error_at = time.time()
            self._sync_last_block_error_peer = peer.remote
            self._sync_wakeup.set()
            self._sync_kick(reason="peer_drop_requeue_blocks", aggressive=False)
        log.info(
            "Peer disconnected",
            extra={
                "peer_id": peer.peer_id or "unknown",
                "remote": peer.remote,
                "reason": reason,
                "direction": peer.direction,
                "uptime_s": round(uptime, 2),
                "conn_trace_id": peer.conn_trace_id,
                "requeued_blocks": requeued_blocks,
                "discarded_buffer": int(discarded_on_drop.get("buffer", 0)),
                "discarded_inflight": int(discarded_on_drop.get("inflight", 0)),
                "header_votes_removed": removed_header_votes,
            },
        )

    # ---------------------------------------------------------------------
    # Wire send/recv helpers
    # ---------------------------------------------------------------------

    async def _send(self, peer: _PeerState, msg_id: MsgID, payload_obj: Any) -> None:
        # Drop msg_id field inside payload (frame header already carries it).
        if hasattr(payload_obj, "__dataclass_fields__"):
            payload = {
                k: getattr(payload_obj, k)
                for k in payload_obj.__dataclass_fields__.keys()  # type: ignore[attr-defined]
                if k != "msg_id"
            }
        else:
            payload = payload_obj

        encoded = encode_payload(payload)
        framed = peer.framer.pack(int(msg_id), encoded)
        async with peer.write_lock:
            await peer.stream.send(framed)

    async def _send_raw(self, peer: _PeerState, msg_id: MsgID, payload: bytes) -> None:
        if len(payload) > self._max_payload_bytes:
            raise PeerMisbehavior(
                "payload_too_large", points=self._score_points["malformed_message"]
            )
        framed = peer.framer.pack(int(msg_id), payload)
        async with peer.write_lock:
            await peer.stream.send(framed)

    def _decode_map(self, payload: bytes) -> dict:
        if len(payload) > self._max_payload_bytes:
            raise PeerMisbehavior(
                "payload_too_large", points=self._score_points["malformed_message"]
            )
        try:
            obj = decode_payload(payload, max_bytes=self._max_payload_bytes)
        except Exception as exc:
            log.debug(
                "Failed to decode payload",
                extra={"error": str(exc), "payload_bytes": len(payload)},
            )
            raise PeerMisbehavior(
                "decode_failed", points=self._score_points["malformed_message"]
            ) from exc
        if not isinstance(obj, dict):
            raise PeerMisbehavior(
                "payload_not_map", points=self._score_points["malformed_message"]
            )
        obj.pop("msg_id", None)
        return obj

    def _decode_payload_map(self, payload: bytes) -> dict:
        if len(payload) > self._max_payload_bytes:
            raise PeerMisbehavior(
                "payload_too_large", points=self._score_points["malformed_message"]
            )
        try:
            obj = decode_payload(payload, max_bytes=self._max_payload_bytes)
        except Exception as exc:
            log.debug(
                "Failed to decode payload",
                extra={"error": str(exc), "payload_bytes": len(payload)},
            )
            raise PeerMisbehavior(
                "decode_failed", points=self._score_points["malformed_message"]
            ) from exc
        if not isinstance(obj, dict):
            raise PeerMisbehavior(
                "payload_not_map", points=self._score_points["malformed_message"]
            )
        return obj

    def _local_capabilities(self) -> list[str]:
        caps = ["tx", "blocks", "sync"]
        if self._tx_relay_v2_enabled:
            caps.append("tx_relay_v2")
        if instant_enabled():
            caps.append("INSTANT_TXBLOCK_V1")
        return caps

    async def _send_hello(self, peer: _PeerState) -> None:
        height, head_hash_hex = self._local_head()
        head_hash = self._parse_hash_bytes(head_hash_hex) or (b"\x00" * 32)
        best_header = self._sync_best_header
        if best_header is not None and best_header.height > int(height or 0):
            height = int(best_header.height)
            head_hash = bytes(best_header.hash)

        # Compute network best height: max of our height and what we've seen from peers
        network_best = self._network_best_height()
        if network_best is None or network_best < int(height or 0):
            network_best = int(height or 0)

        genesis_header_hash = self._genesis_header_hash()
        genesis_block_hash = self._genesis_block_hash()
        listen_port = self._local_listen_port()
        listen_addrs = self._advertised_addrs()
        hello = Hello(
            version="2",
            agent=f"animica-p2p/{p2p_version.__version__}",
            repo_state=self._repo_state,
            chain_id=self.chain_id,
            listen_port=listen_port,
            listen_addrs=listen_addrs,
            genesis_hash=genesis_header_hash,
            genesis_header_hash=genesis_header_hash,
            genesis_block_hash=genesis_block_hash,
            fork_id=self._fork_id(),
            consensus_id=self._consensus_id(),
            protocol_version=self._protocol_version(),
            genesis_identity=self._genesis_identity(),
            network_params_hash=self._network_params_hash(),
            peer_id=self._peer_id_bytes,
            head_height=height,
            head_hash=head_hash,
            alg_policy_root=b"",
            capabilities=self._local_capabilities(),
            timestamp=int(time.time()),
            network_best_height=network_best,
        )
        log.info(
            "Handshake sent",
            extra={
                "remote": peer.remote,
                "direction": peer.direction,
                "stage": "handshake_sent",
                "conn_trace_id": peer.conn_trace_id,
            },
        )
        log.info(
            "P2P_HANDSHAKE_SEND",
            extra={
                "conn_trace_id": peer.conn_trace_id,
                "peer_addr": peer.remote,
                "direction": "in" if peer.direction == "inbound" else "out",
            },
        )
        await self._send(peer, MsgID.HELLO, hello)

    async def _maybe_announce_headers_on_hello(
        self, peer: _PeerState, *, remote_height: int, remote_head_hash: Optional[bytes]
    ) -> None:
        local_height, _ = self._local_head()
        best_header_height = (
            self._sync_best_header.height if self._sync_best_header else int(local_height or 0)
        )
        if int(best_header_height or 0) <= int(remote_height or 0):
            return
        locator: list[bytes] = []
        if (
            remote_head_hash
            and remote_head_hash != b"\x00" * 32
            and self._has_header(remote_head_hash)
        ):
            locator = [bytes(remote_head_hash)]
        else:
            bdb = self._block_db()
            genesis = (
                bdb.get_canonical_hash(0)
                or bdb.get_genesis_hash()
                or self._genesis_hash()
            )
            if genesis:
                locator = [bytes(genesis)]
        headers = self._headers_after_locator(
            locator, limit=int(self._max_headers_per_message)
        )
        if not headers:
            return
        info = self._headers_debug_info(headers)
        log.info(
            "Proactively sending headers",
            extra={
                "remote": peer.remote,
                "remote_head_height": int(remote_height or 0),
                "local_head_height": int(local_height or 0),
                "best_header_height": int(best_header_height or 0),
                **info,
            },
        )
        await self._send(peer, MsgID.HEADERS, Headers(headers=headers))

    # ---------------------------------------------------------------------
    # Handlers
    # ---------------------------------------------------------------------

    async def _handle(self, peer: _PeerState, msg_id: int, payload: bytes) -> None:
        mid = int(msg_id)
        if mid == int(MsgID.HELLO):
            await self._handle_hello(peer, payload)
            return
        if mid == int(MsgID.HELLO_ACK):
            return
        if mid == int(MsgID.GET_PEERS):
            await self._handle_get_peers(peer, payload)
            return
        if mid == int(MsgID.PEERS):
            await self._handle_peers(peer, payload)
            return
        if mid == int(MsgID.ADDRESS_ANNOUNCE):
            await self._handle_address_announce(peer, payload)
            return
        if mid == int(MsgID.HEADERS):
            await self._handle_headers(peer, payload)
            return
        if mid == int(MsgID.INV):
            await self._handle_inv(peer, payload)
            return
        if mid == int(MsgID.TX_INV):
            await self._handle_tx_inv(peer, payload)
            return
        if mid == int(MsgID.TX_GET):
            await self._handle_tx_get(peer, payload)
            return
        if mid == int(MsgID.TX_DATA):
            await self._handle_tx_data(peer, payload)
            return
        if mid == int(MsgID.TX_NOTFOUND_V2):
            await self._handle_tx_notfound(peer, payload)
            return
        if mid == int(MsgID.TX_MEMPOOL_REQ):
            await self._handle_tx_mempool_req(peer, payload)
            return
        if mid == int(MsgID.TX_MEMPOOL_RESP):
            await self._handle_tx_mempool_resp(peer, payload)
            return
        if mid == int(MsgID.TX_MEMPOOL_SUMMARY):
            await self._handle_tx_mempool_summary(peer, payload)
            return
        if mid == int(MsgID.TXBLOCK_INV):
            await self._handle_txblock_inv(peer, payload)
            return
        if mid == int(MsgID.TXBLOCK_GET):
            await self._handle_txblock_get(peer, payload)
            return
        if mid == int(MsgID.TXBLOCK_DATA):
            await self._handle_txblock_data(peer, payload)
            return
        if mid == int(MsgID.GETDATA):
            await self._handle_getdata(peer, payload)
            return
        if mid == int(MsgID.GET_SNAPSHOTS):
            await self._handle_get_snapshots(peer, payload)
            return
        if mid == int(MsgID.SNAPSHOTS):
            await self._handle_snapshots(peer, payload)
            return
        if mid == int(MsgID.GET_SNAPSHOT_CHUNK):
            await self._handle_get_snapshot_chunk(peer, payload)
            return
        if mid == int(MsgID.SNAPSHOT_CHUNK):
            await self._handle_snapshot_chunk(peer, payload)
            return
        if mid == int(MsgID.TX):
            await self._handle_tx(peer, payload)
            return
        if mid == int(MsgID.GET_HEADERS):
            await self._handle_get_headers(peer, payload)
            return
        if mid == int(MsgID.GET_BLOCKS):
            await self._handle_get_blocks(peer, payload)
            return
        if mid == int(MsgID.BLOCKS):
            await self._handle_blocks(peer, payload)
            return
        if mid == int(MsgID.BLOCK_ANNOUNCE):
            await self._handle_block_announce(peer, payload)
            return
        raise PeerMisbehavior(
            "unknown_message", points=self._score_points["malformed_message"]
        )

    def _log_handshake_mismatch(
        self,
        peer: _PeerState,
        *,
        reason: str,
        peer_chain_id: Optional[int],
        peer_genesis_hash: Optional[bytes],
        peer_genesis_header_hash: Optional[bytes] = None,
        peer_genesis_block_hash: Optional[bytes] = None,
        peer_genesis_identity: Optional[bytes] = None,
        peer_network_params_hash: Optional[bytes] = None,
        peer_fork_id: Optional[int] = None,
        peer_consensus_id: Optional[str] = None,
        peer_protocol_version: Optional[str] = None,
        peer_repo_state: Optional[str] = None,
        local_repo_state: Optional[str] = None,
    ) -> None:
        local_genesis = self._genesis_hash()
        local_genesis_header = self._genesis_header_hash()
        local_genesis_block = self._genesis_block_hash()
        local_genesis_identity = self._genesis_identity()
        local_params_hash = self._network_params_hash()
        log.warning(
            "Peer handshake mismatch",
            extra={
                "remote": peer.remote,
                "reason": reason,
                "local_chain_id": self.chain_id,
                "local_genesis_hash": local_genesis.hex(),
                "local_genesis_header_hash": local_genesis_header.hex(),
                "local_genesis_block_hash": local_genesis_block.hex(),
                "local_genesis_identity": local_genesis_identity.hex(),
                "local_network_params_hash": local_params_hash.hex(),
                "local_fork_id": self._fork_id(),
                "local_consensus_id": self._consensus_id(),
                "local_protocol_version": self._protocol_version(),
                "local_repo_state": local_repo_state,
                "peer_chain_id": peer_chain_id,
                "peer_genesis_hash": peer_genesis_hash.hex()
                if peer_genesis_hash
                else None,
                "peer_genesis_header_hash": peer_genesis_header_hash.hex()
                if peer_genesis_header_hash
                else None,
                "peer_genesis_block_hash": peer_genesis_block_hash.hex()
                if peer_genesis_block_hash
                else None,
                "peer_genesis_identity": peer_genesis_identity.hex()
                if peer_genesis_identity
                else None,
                "peer_network_params_hash": peer_network_params_hash.hex()
                if peer_network_params_hash
                else None,
                "peer_fork_id": peer_fork_id,
                "peer_consensus_id": peer_consensus_id,
                "peer_protocol_version": peer_protocol_version,
                "peer_repo_state": peer_repo_state,
            },
        )

    async def _handle_hello(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        allowed = set(Hello.__dataclass_fields__)
        hello_defaults: dict[str, Any] = {}
        for name, spec in Hello.__dataclass_fields__.items():
            if spec.default_factory is not MISSING:  # type: ignore[comparison-overlap]
                hello_defaults[name] = spec.default_factory()
            else:
                hello_defaults[name] = spec.default

        def _coerce_fixed_bytes(value: Any, size: int) -> Optional[bytes]:
            if isinstance(value, (bytes, bytearray)):
                return bytes(value) if len(value) == size else None
            if isinstance(value, str):
                cleaned = value.strip().lower()
                if cleaned.startswith("0x"):
                    cleaned = cleaned[2:]
                if len(cleaned) != size * 2:
                    return None
                try:
                    return bytes.fromhex(cleaned)
                except ValueError:
                    return None
            return None

        def _normalize_hello_payload(values: dict, *, for_fallback: bool) -> dict:
            normalized = {k: v for k, v in values.items() if k in allowed}
            for field_name, size in (
                ("genesis_hash", 32),
                ("genesis_header_hash", 32),
                ("genesis_block_hash", 32),
                ("genesis_identity", 32),
                ("network_params_hash", 32),
                ("peer_id", 32),
                ("head_hash", 32),
                ("alg_policy_root", 64),
            ):
                if field_name not in normalized:
                    continue
                parsed = _coerce_fixed_bytes(normalized[field_name], size)
                if parsed is not None:
                    normalized[field_name] = parsed
                elif for_fallback:
                    normalized[field_name] = b""
            if for_fallback:
                merged = dict(hello_defaults)
                merged.update(normalized)
                return merged
            return normalized

        hello_payload = _normalize_hello_payload(data, for_fallback=False)
        try:
            hello = Hello(**hello_payload)
        except Exception as exc:
            log.info(
                "Handshake payload normalized with fallback",
                extra={"remote": peer.remote, "error": str(exc)},
            )
            fallback_payload = _normalize_hello_payload(data, for_fallback=True)
            hello = SimpleNamespace(**fallback_payload)
        log.info(
            "Handshake received",
            extra={
                "remote": peer.remote,
                "direction": peer.direction,
                "stage": "handshake_received",
                "conn_trace_id": peer.conn_trace_id,
            },
        )
        log.info(
            "P2P_HANDSHAKE_RECV",
            extra={
                "conn_trace_id": peer.conn_trace_id,
                "peer_addr": peer.remote,
                "direction": "in" if peer.direction == "inbound" else "out",
            },
        )
        missing_fields: list[str] = []

        def _handshake_fail(reason: str, *, stage: str = "handshake") -> None:
            if stage == "caps":
                self._record_caps_failure(reason)
            else:
                self._record_handshake_failure(reason)
            log.info(
                "P2P_HANDSHAKE_VALIDATE_FAIL",
                extra={
                    "conn_trace_id": peer.conn_trace_id,
                    "peer_addr": peer.remote,
                    "direction": "in" if peer.direction == "inbound" else "out",
                    "reason": reason,
                },
            )
            log.info(
                "P2P_DIAL_FAIL",
                extra={
                    "conn_trace_id": peer.conn_trace_id,
                    "peer_addr": peer.remote,
                    "stage": stage,
                    "reason": reason,
                },
            )

        def _normalize_caps_list(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(c) for c in value if isinstance(c, (str, int))]

        def _hash_bytes(value: Any) -> bytes:
            parsed = self._parse_hash_bytes(value)
            return parsed or b""

        if int(hello.chain_id) != int(self.chain_id):
            _handshake_fail("chain_id_mismatch")
            self._log_handshake_mismatch(
                peer,
                reason="chain_id_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=_hash_bytes(getattr(hello, "genesis_hash", None)),
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="chain_id_mismatch"),
            )
            raise PeerMisbehavior(
                "chain_id_mismatch", points=0
            )

        peer_genesis_header = _hash_bytes(
            getattr(hello, "genesis_header_hash", None)
        ) or _hash_bytes(getattr(hello, "genesis_hash", None))
        peer_genesis_block = _hash_bytes(getattr(hello, "genesis_block_hash", None))
        local_genesis_header = self._genesis_header_hash()
        local_genesis_block = self._genesis_block_hash()

        if not peer_genesis_header and not peer_genesis_block:
            self._stats["p2p_peers_rejected_genesis_mismatch"] += 1
            _handshake_fail("genesis_missing")
            self._log_handshake_mismatch(
                peer,
                reason="genesis_missing",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=None,
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="genesis_missing"),
            )
            raise PeerMisbehavior(
                "genesis_missing",
                points=self._score_points["wrong_genesis"],
            )

        if peer_genesis_header and peer_genesis_header != local_genesis_header:
            self._stats["p2p_peers_rejected_genesis_mismatch"] += 1
            _handshake_fail("genesis_mismatch")
            self._log_handshake_mismatch(
                peer,
                reason="genesis_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=peer_genesis_header,
                peer_genesis_header_hash=peer_genesis_header,
                peer_genesis_block_hash=peer_genesis_block or None,
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="genesis_mismatch"),
            )
            raise PeerMisbehavior(
                "genesis_mismatch",
                points=self._score_points["wrong_genesis"],
            )
        if not peer_genesis_header and peer_genesis_block:
            if peer_genesis_block != local_genesis_block:
                self._stats["p2p_peers_rejected_genesis_mismatch"] += 1
                _handshake_fail("genesis_mismatch")
                self._log_handshake_mismatch(
                    peer,
                    reason="genesis_mismatch",
                    peer_chain_id=int(hello.chain_id or 0),
                    peer_genesis_hash=peer_genesis_block,
                    peer_genesis_header_hash=peer_genesis_header or None,
                    peer_genesis_block_hash=peer_genesis_block,
                )
                await self._send(
                    peer,
                    MsgID.HELLO_ACK,
                    HelloAck(accepted=False, reason="genesis_mismatch"),
                )
                raise PeerMisbehavior(
                    "genesis_mismatch",
                    points=self._score_points["wrong_genesis"],
                )
            log.info(
                "Peer provided legacy genesis block hash",
                extra={
                    "remote": peer.remote,
                    "peer_genesis_block_hash": peer_genesis_block.hex(),
                    "local_genesis_block_hash": local_genesis_block.hex(),
                },
            )

        peer_fork_id = int(getattr(hello, "fork_id", 0) or 0)
        if peer_fork_id and peer_fork_id != int(self._fork_id()):
            _handshake_fail("fork_id_mismatch")
            self._log_handshake_mismatch(
                peer,
                reason="fork_id_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=peer_genesis_header or peer_genesis_block or b"",
                peer_fork_id=peer_fork_id,
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="fork_id_mismatch"),
            )
            raise PeerMisbehavior(
                "fork_id_mismatch",
                points=self._score_points["wrong_chain"],
            )
        if not peer_fork_id:
            missing_fields.append("fork_id")

        peer_consensus_id = str(getattr(hello, "consensus_id", "") or "")
        if peer_consensus_id and peer_consensus_id != str(self._consensus_id()):
            _handshake_fail("consensus_mismatch")
            self._log_handshake_mismatch(
                peer,
                reason="consensus_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=peer_genesis_header or peer_genesis_block or b"",
                peer_consensus_id=peer_consensus_id,
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="consensus_mismatch"),
            )
            raise PeerMisbehavior(
                "consensus_mismatch",
                points=self._score_points["wrong_chain"],
            )
        if not peer_consensus_id:
            missing_fields.append("consensus_id")

        peer_protocol_version = str(getattr(hello, "protocol_version", "") or "")
        if peer_protocol_version and peer_protocol_version != str(self._protocol_version()):
            _handshake_fail("protocol_mismatch")
            self._log_handshake_mismatch(
                peer,
                reason="protocol_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=peer_genesis_header or peer_genesis_block or b"",
                peer_protocol_version=peer_protocol_version,
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="protocol_mismatch"),
            )
            raise PeerMisbehavior(
                "protocol_mismatch",
                points=self._score_points["wrong_chain"],
            )
        if not peer_protocol_version:
            missing_fields.append("protocol_version")

        local_repo_state = self._repo_state
        peer_repo_state = str(
            getattr(hello, "repo_state", "")
            or data.get("repo_state")
            or data.get("repoState")
            or ""
        ).strip()
        if local_repo_state:
            if not peer_repo_state or peer_repo_state != local_repo_state:
                peer.repo_state_ok = False
                self._log_handshake_mismatch(
                    peer,
                    reason="repo_state_mismatch",
                    peer_chain_id=int(hello.chain_id or 0),
                    peer_genesis_hash=peer_genesis_header or peer_genesis_block or b"",
                    peer_repo_state=peer_repo_state or None,
                    local_repo_state=local_repo_state,
                )
                if self._require_repo_state_match:
                    _handshake_fail("repo_state_mismatch")
                    await self._send(
                        peer,
                        MsgID.HELLO_ACK,
                        HelloAck(accepted=False, reason="repo_state_mismatch"),
                    )
                    raise PeerMisbehavior(
                        "repo_state_mismatch",
                        points=self._score_points["wrong_chain"],
                        ban_ttl=self._ban_thresholds[-1][1],
                    )
            else:
                peer.repo_state_ok = True
        else:
            peer.repo_state_ok = True

        peer_genesis_identity = _hash_bytes(getattr(hello, "genesis_identity", None))
        if peer_genesis_identity and peer_genesis_identity != self._genesis_identity():
            _handshake_fail("genesis_identity_mismatch")
            self._log_handshake_mismatch(
                peer,
                reason="genesis_identity_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=peer_genesis_header or peer_genesis_block or b"",
                peer_genesis_identity=peer_genesis_identity,
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="genesis_identity_mismatch"),
            )
            raise PeerMisbehavior(
                "genesis_identity_mismatch",
                points=self._score_points["wrong_chain"],
            )
        if not peer_genesis_identity:
            missing_fields.append("genesis_identity")

        peer_network_params_hash = _hash_bytes(
            getattr(hello, "network_params_hash", None)
        )
        if peer_network_params_hash and peer_network_params_hash != self._network_params_hash():
            _handshake_fail("network_params_mismatch")
            self._log_handshake_mismatch(
                peer,
                reason="network_params_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=peer_genesis_header or peer_genesis_block or b"",
                peer_genesis_identity=peer_genesis_identity,
                peer_network_params_hash=peer_network_params_hash,
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="network_params_mismatch"),
            )
            raise PeerMisbehavior(
                "network_params_mismatch",
                points=self._score_points["wrong_chain"],
            )
        if not peer_network_params_hash:
            missing_fields.append("network_params_hash")

        if missing_fields:
            log.info(
                "Legacy handshake missing fields",
                extra={
                    "remote": peer.remote,
                    "direction": peer.direction,
                    "missing_fields": missing_fields,
                    "stage": "handshake_legacy",
                },
            )

        if hello.version and str(hello.version) not in {"1", "2"}:
            _handshake_fail("version_mismatch")
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="version_mismatch"),
            )
            raise PeerMisbehavior("version_mismatch", points=50)

        now = time.time()
        try:
            peer_ts = int(getattr(hello, "timestamp", 0) or 0)
        except Exception:
            peer_ts = 0
        if peer_ts and abs(now - peer_ts) > self._clock_skew_s:
            _handshake_fail("clock_skew")
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="clock_skew"),
            )
            raise PeerMisbehavior("clock_skew", points=20)

        peer_id_bytes = _hash_bytes(getattr(hello, "peer_id", None))
        peer.peer_id = peer_id_bytes.hex()
        if (
            not self._allow_self_peers
            and peer.peer_id
            and peer.peer_id == self._peer_id_bytes.hex()
        ):
            _handshake_fail("self_peer")
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="self_peer"),
            )
            raise PeerMisbehavior("self_peer", points=0)
        if (
            not self._is_peer_exempt(peer.remote)
            and peer.peer_id
            and self._is_banned(peer.peer_id)
        ):
            _handshake_fail("banned")
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="banned"),
            )
            raise PeerMisbehavior("banned", points=0)
        if self._is_outbound_only_blocklisted(
            peer_id=peer.peer_id, remote=peer.remote
        ):
            _handshake_fail("outbound_only_banned")
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="outbound_only_banned"),
            )
            raise PeerMisbehavior("outbound_only_banned", points=0)
        remote_host = self._extract_host(peer.remote)
        remote_port = self._extract_port(peer.remote) or 0
        self._maybe_enable_private_network(remote_host, reason="connected_peer")
        if not self._allow_self_peers and self._is_self_address(remote_host, remote_port):
            _handshake_fail("self_peer")
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="self_peer"),
            )
            raise PeerMisbehavior("self_peer", points=0)
        normalized = dict(data)
        remote_caps = _normalize_caps_list(
            getattr(hello, "capabilities", None) or data.get("capabilities")
        )
        local_caps = set(self._local_capabilities())
        negotiated_caps = sorted(local_caps.intersection(remote_caps))
        if self._required_caps:
            missing_required = sorted(self._required_caps - set(remote_caps))
            if missing_required:
                _handshake_fail("caps_missing", stage="caps")
                await self._send(
                    peer,
                    MsgID.HELLO_ACK,
                    HelloAck(accepted=False, reason="caps_missing"),
                )
                raise PeerMisbehavior(
                    "caps_missing", points=self._score_points["wrong_chain"]
                )
        log.info(
            "P2P_CAPS_NEGOTIATED",
            extra={
                "conn_trace_id": peer.conn_trace_id,
                "peer_addr": peer.remote,
                "direction": "in" if peer.direction == "inbound" else "out",
                "local_caps": sorted(local_caps),
                "remote_caps": remote_caps,
                "negotiated": negotiated_caps,
            },
        )
        normalized["chain_id"] = int(getattr(hello, "chain_id", 0) or 0)
        normalized["head_height"] = int(
            getattr(hello, "head_height", 0)
            or data.get("head_height")
            or data.get("headHeight")
            or data.get("height")
            or 0
        )
        peer_head_hash = _hash_bytes(getattr(hello, "head_hash", None))
        normalized["head_hash"] = peer_head_hash or _hash_bytes(
            data.get("head_hash") or data.get("headHash")
        )
        normalized["genesis_hash"] = (
            peer_genesis_header
            or peer_genesis_block
            or _hash_bytes(data.get("genesis_hash") or data.get("genesisHash"))
        )
        normalized["genesis_header_hash"] = (
            peer_genesis_header
            or _hash_bytes(data.get("genesis_header_hash") or data.get("genesisHeaderHash"))
        )
        normalized["genesis_block_hash"] = (
            peer_genesis_block
            or _hash_bytes(data.get("genesis_block_hash") or data.get("genesisBlockHash"))
        )
        normalized["fork_id"] = int(
            getattr(hello, "fork_id", 0)
            or data.get("fork_id")
            or data.get("forkId")
            or 0
        )
        normalized["consensus_id"] = str(
            getattr(hello, "consensus_id", "")
            or data.get("consensus_id")
            or data.get("consensusId")
            or ""
        )
        normalized["protocol_version"] = str(
            getattr(hello, "protocol_version", "")
            or data.get("protocol_version")
            or data.get("protocolVersion")
            or ""
        )
        normalized["repo_state"] = peer_repo_state
        normalized["genesis_identity"] = peer_genesis_identity or _hash_bytes(
            data.get("genesis_identity") or data.get("genesisIdentity")
        )
        normalized["network_params_hash"] = peer_network_params_hash or _hash_bytes(
            data.get("network_params_hash") or data.get("networkParamsHash")
        )
        normalized["capabilities"] = remote_caps
        peer.hello = normalized
        peer.negotiated_caps = set(negotiated_caps)
        peer.hello_done.set()
        log.info(
            "Handshake verified",
            extra={
                "remote": peer.remote,
                "peer_id": peer.peer_id,
                "direction": peer.direction,
                "stage": "handshake_verified",
                "missing_fields": missing_fields or None,
                "conn_trace_id": peer.conn_trace_id,
            },
        )
        log.info(
            "P2P_HANDSHAKE_VALIDATE_OK",
            extra={
                "conn_trace_id": peer.conn_trace_id,
                "peer_addr": peer.remote,
                "peer_id": peer.peer_id,
                "direction": "in" if peer.direction == "inbound" else "out",
            },
        )
        if normalized.get("head_height"):
            self._update_peer_head_table(
                peer,
                height=int(normalized["head_height"]),
                source="hello",
                head_hash=normalized.get("head_hash"),
            )
        peer.ready_for_sync = True
        peer_key = self._peer_tx_key(peer)
        self._txrelay.register_peer(
            peer_key,
            peer_node_id=peer.peer_id,
            direction=peer.direction,
            remote=peer.remote,
        )
        self._create_child_task(
            self._txrelay.request_mempool_sync(peer_key),
            name=f"p2p.txrelay.mempool_sync@{peer_key}",
        )

        listen_port = int(getattr(hello, "listen_port", 0) or 0)
        reported_addr = self._reported_peer_addr(peer.remote, listen_port)
        if reported_addr:
            parsed = self._normalize_peer_addr(
                reported_addr, fallback_port=int(listen_port or DEFAULT_TCP_PORT)
            )
            if (
                parsed.addr
                and not self._allow_self_peers
                and self._is_self_address(parsed.addr.host, parsed.addr.port)
            ):
                log.info(
                    "Ignoring self-like reported peer address",
                    extra={"remote": peer.remote, "reported_addr": reported_addr},
                )
                reported_addr = None
        reported_addrs = self._strict_reported_peer_addrs(
            peer=peer,
            listen_port=listen_port,
            listen_addrs=list(getattr(hello, "listen_addrs", []) or []),
        )
        peer.accepts_inbound = bool(reported_addrs)
        if self._enforce_outbound_only_policy_for_peer(peer):
            self._mark_outbound_only_blocklisted(
                peer_id=peer.peer_id,
                remote=peer.remote,
                reason="no_inbound_advertised",
            )
            _handshake_fail("outbound_only_no_inbound")
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="outbound_only_no_inbound"),
            )
            raise PeerMisbehavior(
                "outbound_only_no_inbound",
                points=self._score_points["wrong_chain"],
                ban_ttl=self._outbound_only_ban_ttl if self._outbound_only_ban_ttl > 0 else None,
            )
        for addr in reported_addrs:
            self._addrman.add(addr)

        self._peer_registry.update_meta(
            peer.session_id,
            peer_id=peer.peer_id,
            last_seen=time.time(),
            height=int(normalized["head_height"]),
            remote=peer.remote,
            direction=peer.direction,
            feeler=peer.feeler,
            reported_addr=reported_addr,
            listen_port=listen_port or None,
        )
        self._update_peer_meta(peer)

        # Track duplicate peer_id sessions; drop older duplicates.
        to_drop = self._peer_registry.mark_identified(peer.session_id, peer.peer_id)
        if to_drop:
            log.info(
                "Duplicate peer_id detected; dropping older sessions",
                extra={"peer_id": peer.peer_id, "sessions": list(to_drop)},
            )
            for session_id in to_drop:
                dup_peer = self._peers_by_session.get(session_id)
                if dup_peer is None:
                    continue
                if dup_peer.session_id == peer.session_id:
                    await self._drop_peer(peer, reason="duplicate_peer_id")
                    return
                self._create_child_task(
                    self._drop_peer(dup_peer, reason="duplicate_peer_id"),
                    name=f"p2p.drop_peer@{dup_peer.remote}",
                )
        # Resolve duplicate bidirectional links for the same peer_id by selecting a
        # deterministic single direction. This prevents simultaneous inbound/outbound
        # churn between two nodes that dial each other at the same time.
        opposite_direction = "inbound" if peer.direction == "outbound" else "outbound"
        opposite = self._peer_by_id(peer.peer_id, direction=opposite_direction)
        if opposite is not None and opposite.session_id != peer.session_id:
            local_peer_id_hex = self._peer_id_bytes.hex()
            keep_direction = (
                "outbound" if local_peer_id_hex < peer.peer_id else "inbound"
            )
            if peer.direction != keep_direction:
                await self._drop_peer(
                    peer, reason=f"duplicate_bidirectional_prefer_{keep_direction}"
                )
                return
            self._create_child_task(
                self._drop_peer(
                    opposite, reason=f"duplicate_bidirectional_prefer_{keep_direction}"
                ),
                name=f"p2p.drop_peer@{opposite.remote}",
            )
        self._stats["peers"] = self._peer_registry.peer_count()
        log.info(
            "P2P_SESSION_READY",
            extra={
                "conn_trace_id": peer.conn_trace_id,
                "peer_addr": peer.remote,
                "peer_id": peer.peer_id,
                "direction": "in" if peer.direction == "inbound" else "out",
            },
        )
        log.info(
            "P2P_PEER_ADDED",
            extra={
                "conn_trace_id": peer.conn_trace_id,
                "peer_id": peer.peer_id,
                "direction": "in" if peer.direction == "inbound" else "out",
            },
        )

        with contextlib.suppress(Exception):
            addrs = reported_addrs or ([reported_addr] if reported_addr else [])
            if not addrs:
                addrs = []
            self.peerstore.add(
                peer.peer_id, addrs=addrs, score=0.0, direction=peer.direction
            )
            if reported_addr:
                self.peerstore.record_seen(peer.peer_id, reported_addr)
            self.peerstore.record_connection(peer.peer_id)
            self.peerstore.update_head_height(peer.peer_id, int(normalized["head_height"]))
            self._schedule_peer_persist()
            log.info(
                "Peer added to peerstore",
                extra={
                    "remote": peer.remote,
                    "peer_id": peer.peer_id,
                    "direction": peer.direction,
                    "stage": "added_to_peerstore",
                },
            )

        await self._send(peer, MsgID.HELLO_ACK, HelloAck(accepted=True, reason=None))
        self._sync_wakeup.set()
        self._create_child_task(
            self._announce_pending_txs(peer),
            name=f"p2p.announce_pending_txs@{peer.remote}",
        )
        self._create_child_task(
            self._maybe_announce_headers_on_hello(
                peer,
                remote_height=int(normalized["head_height"]),
                remote_head_hash=bytes(normalized.get("head_hash") or b""),
            ),
            name=f"p2p.headers_hello@{peer.remote}",
        )
        self._create_child_task(
            self._send_addr_sample(
                peer,
                limit=max(10, min(self._addr_relay_sample, 50)),
                include_advertised=True,
            ),
            name=f"p2p.addr_sample@{peer.remote}",
        )
        self._create_child_task(
            self._send_peer_exchange(peer, limit=self._peer_exchange_limit),
            name=f"p2p.peer_exchange@{peer.remote}",
        )
        self._create_child_task(
            self._send_get_peers(peer),
            name=f"p2p.get_peers@{peer.remote}",
        )
        if peer.feeler:
            self._create_child_task(
                self._close_feeler_after_delay(peer),
                name=f"p2p.feeler_close@{peer.remote}",
            )

    async def _handle_get_peers(self, peer: _PeerState, payload: bytes) -> None:
        now = time.time()
        last = self._addr_last_response.get(peer.session_id, 0.0)
        if now - last < self._addr_response_interval:
            return
        self._addr_last_response[peer.session_id] = now

        data = self._decode_map(payload)
        max_peers = int(data.get("max_peers") or self._addr_request_max)
        max_peers = max(1, min(max_peers, 256))

        exclude = {self._addr_key(peer.remote)}
        entries = []
        for entry in self._collect_peer_entries(limit=max_peers, exclude=exclude):
            addr = entry.get("addr")
            if not isinstance(addr, str) or not addr:
                continue
            if self._peer_knows_addr(peer, addr):
                continue
            try:
                pid = hashlib.sha3_256(addr.encode()).digest()
            except Exception:
                pid = b"\x00" * 32
            entry = dict(entry)
            entry["peer_id"] = pid
            entries.append(entry)
            self._mark_peer_known(peer, addr)
        await self._send(peer, MsgID.PEERS, Peers(entries=entries))

    async def _handle_peers(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        raw_entries = data.get("entries") or []
        entries: list[dict[str, Any]] = []
        for entry in raw_entries:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                addr = entry[1]
                entries.append({"addr": addr})
                continue
            if isinstance(entry, dict):
                entries.append(entry)
                continue
        for entry in entries:
            addr = entry.get("addr") or entry.get("address")
            if isinstance(addr, bytes):
                try:
                    entry["addr"] = addr.decode()
                except Exception:
                    entry["addr"] = ""
        if entries:
            for entry in entries:
                addr = entry.get("addr")
                if isinstance(addr, str) and addr:
                    self._mark_peer_known(peer, addr)
            self._ingest_peer_entries(entries, source=f"peer:{peer.remote}", source_peer=peer)
            self._sync_wakeup.set()

    async def _handle_address_announce(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        addresses = data.get("addresses") or []
        entries: list[dict[str, Any]] = []
        for addr in addresses:
            if isinstance(addr, bytes):
                try:
                    addr = addr.decode()
                except Exception:
                    continue
            if isinstance(addr, str) and addr:
                entries.append({"addr": addr, "source": f"announce:{peer.remote}"})
        if entries:
            for entry in entries:
                addr = entry.get("addr")
                if isinstance(addr, str) and addr:
                    self._mark_peer_known(peer, addr)
            self._ingest_peer_entries(entries, source=f"announce:{peer.remote}", source_peer=peer)

    async def query_peer_snapshots(
        self, peer: _PeerState, chain_id: Optional[int] = None, timeout: float = 10.0
    ) -> Optional[list[dict[str, Any]]]:
        """
        Query a peer for available snapshots via P2P.

        Args:
            peer: Peer state object
            chain_id: Optional chain ID filter
            timeout: Timeout in seconds

        Returns:
            List of snapshot info dicts, or None if request failed/timed out
        """
        try:
            from p2p.wire.messages import GetSnapshots
            from p2p.wire.message_ids import MsgID

            # Create future for response
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            peer.pending_snapshot_list = fut

            # Send GET_SNAPSHOTS request
            req = GetSnapshots(chain_id=chain_id)
            await self._send(peer, MsgID.GET_SNAPSHOTS, req)

            # Wait for response
            try:
                response = await asyncio.wait_for(fut, timeout=timeout)
                return response
            except asyncio.TimeoutError:
                self._log.debug(f"Snapshot list request to {peer.remote} timed out")
                return None
            finally:
                peer.pending_snapshot_list = None

        except Exception as e:
            self._log.warning(f"Error querying peer {peer.remote} for snapshots: {e}")
            peer.pending_snapshot_list = None
            return None

    async def query_peer_snapshot_chunk(
        self,
        peer: _PeerState,
        chain_id: int,
        checkpoint_height: int,
        chunk_name: str,
        timeout: float = 30.0,
    ) -> Optional[tuple[bytes, bool]]:
        """
        Query a peer for a specific snapshot chunk via P2P.

        Args:
            peer: Peer state object
            chain_id: Chain ID
            checkpoint_height: Snapshot checkpoint height
            chunk_name: Name of the chunk (e.g., "blocks.tar.zst")
            timeout: Timeout in seconds

        Returns:
            Tuple of (chunk_data, found) or None if request failed/timed out
        """
        try:
            from p2p.wire.messages import GetSnapshotChunk
            from p2p.wire.message_ids import MsgID

            # Create future for response
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            peer.pending_snapshot_chunk = fut

            # Send GET_SNAPSHOT_CHUNK request
            req = GetSnapshotChunk(
                chain_id=chain_id,
                checkpoint_height=checkpoint_height,
                chunk_name=chunk_name,
            )
            await self._send(peer, MsgID.GET_SNAPSHOT_CHUNK, req)

            # Wait for response
            try:
                response = await asyncio.wait_for(fut, timeout=timeout)
                return response
            except asyncio.TimeoutError:
                self._log.debug(
                    f"Snapshot chunk request to {peer.remote} "
                    f"(chunk={chunk_name}) timed out"
                )
                return None
            finally:
                peer.pending_snapshot_chunk = None

        except Exception as e:
            self._log.warning(
                f"Error querying peer {peer.remote} for snapshot chunk {chunk_name}: {e}"
            )
            peer.pending_snapshot_chunk = None
            return None

    async def _close_feeler_after_delay(self, peer: _PeerState) -> None:
        try:
            await asyncio.sleep(self._feeler_hold_s)
            await self._drop_peer(peer, reason="feeler_complete")
        except asyncio.CancelledError:
            return

    async def _handle_inv(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        items = data.get("items") or []
        self._prune_ttl(self._tx_inv_seen, cap=self._tx_inv_seen_cap)
        self._prune_requested()
        if len(items) > self._max_inv_per_msg:
            raise PeerMisbehavior(
                "inv_oversized",
                points=self._score_points["malformed_message"],
            )
        inv_items: list[InvItem] = []
        for it in items:
            if isinstance(it, dict):
                inv_items.append(InvItem(**it))
        inv = Inv(items=inv_items)
        tx_inv_count = sum(1 for it in inv.items if int(it.typ) == int(InvType.TX))
        if tx_inv_count:
            log.info(
                "tx.inv_recv",
                extra={"peer": peer.remote, "count": tx_inv_count},
            )
            for it in inv.items:
                if int(it.typ) == int(InvType.TX):
                    peer.last_tx_inv_recv_at = time.time()
                    log.info(
                        "TX_INV_RECV",
                        extra={"peer": peer.remote, "hash": bytes(it.h).hex()},
                    )
                    log.info(
                        "tx.inv_recv",
                        extra={"peer": peer.remote, "hash": bytes(it.h).hex()},
                    )
                    log.info(
                        "p2p.tx.inv_recv",
                        extra={"peer": peer.remote, "hash": bytes(it.h).hex()},
                    )
            self._stats["tx_inv_recv_total"] += tx_inv_count
            if not self._tx_relay_allowed():
                inv.items = [it for it in inv.items if int(it.typ) != int(InvType.TX)]

        want: list[InvItem] = []
        saw_block_inv = False
        now = time.time()
        for it in inv.items:
            if int(it.typ) == int(InvType.TX):
                self._stats["inv_tx_recv"] += 1
                tx_hash = bytes(it.h)
                if self._seen(self._tx_inv_seen, tx_hash):
                    self._stats["tx_inv_dedup"] += 1
                    continue
                self._remember_ttl(
                    self._tx_inv_seen, tx_hash, self._tx_inv_seen_cap, self._tx_relay_ttl_s
                )
                if self._requested_recently(tx_hash):
                    self._stats["tx_getdata_skipped"] += 1
                    continue
                if await self._pending_get(tx_hash) is None and not self._seen(
                    self._seen_tx, tx_hash
                ):
                    want.append(InvItem(typ=InvType.TX, h=tx_hash))
            elif int(it.typ) == int(InvType.BLOCK):
                self._stats["inv_block_recv"] += 1
                saw_block_inv = True
                if not self._has_block(bytes(it.h)):
                    want.append(InvItem(typ=InvType.BLOCK, h=bytes(it.h)))
        if saw_block_inv:
            peer.broadcast.last_inventory_at = now
            self._stats["peer_broadcast_good"] += 1

        if want:
            tx_items = [it for it in want if int(it.typ) == int(InvType.TX)]
            if tx_items:
                if self._rate_limit(
                    self._getdata_out_by_peer,
                    peer.remote,
                    self._max_getdata_per_min_per_peer,
                    self._tx_rate_window_s,
                ):
                    self._stats["tx_getdata_rate_limited"] += 1
                    log.warning(
                        "getdata rate limited (outgoing)",
                        extra={"peer": peer.remote, "count": len(tx_items)},
                    )
                    want = [it for it in want if int(it.typ) != int(InvType.TX)]
                else:
                    for it in tx_items:
                        self._remember_requested(bytes(it.h), peer.remote)
                        log.info(
                            "TX_GET_SENT",
                            extra={"peer": peer.remote, "hash": bytes(it.h).hex()},
                        )
                        log.info(
                            "p2p.tx.request_sent",
                            extra={"peer": peer.remote, "hash": bytes(it.h).hex()},
                        )
                    self._stats["tx_getdata_sent"] += len(tx_items)
                    log.info(
                        "tx.getdata_sent",
                        extra={"peer": peer.remote, "count": len(tx_items)},
                    )
            if want:
                await self._send(peer, MsgID.GETDATA, GetData(items=want))
                if any(int(it.typ) == int(InvType.BLOCK) for it in want):
                    self._sync_wakeup.set()

    async def _handle_tx_inv(self, peer: _PeerState, payload: bytes) -> None:
        if not self._tx_relay_enabled or not self._p2p_tx_enabled or not self._tx_relay_v2_enabled:
            return
        data = self._decode_payload_map(payload)
        txids = parse_txids(data.get("txids") or [])
        peer_key = self._peer_tx_key(peer)
        await self._txrelay.on_tx_inv(peer_key, txids)

    async def _handle_tx_get(self, peer: _PeerState, payload: bytes) -> None:
        if not self._tx_relay_enabled or not self._p2p_tx_enabled or not self._tx_relay_v2_enabled:
            return
        data = self._decode_payload_map(payload)
        txids = parse_txids(data.get("txids") or [])
        peer_key = self._peer_tx_key(peer)
        log.info(
            "TX_GET_RECV",
            extra={"peer": peer_key, "count": len(txids)},
        )
        await self._txrelay.on_tx_get(peer_key, txids)

    async def _handle_tx_data(self, peer: _PeerState, payload: bytes) -> None:
        if not self._tx_relay_enabled or not self._p2p_tx_enabled or not self._tx_relay_v2_enabled:
            return
        data = self._decode_payload_map(payload)
        items = parse_tx_data_items(data.get("items") or [])
        peer_key = self._peer_tx_key(peer)
        await self._txrelay.on_tx_data(
            peer_key,
            [{"txid": item.txid, "tx_bytes": item.tx_bytes} for item in items],
        )

    async def _handle_tx_notfound(self, peer: _PeerState, payload: bytes) -> None:
        if not self._tx_relay_enabled or not self._p2p_tx_enabled or not self._tx_relay_v2_enabled:
            return
        data = self._decode_payload_map(payload)
        txids = parse_txids(data.get("txids") or [])
        peer_key = self._peer_tx_key(peer)
        await self._txrelay.on_tx_notfound(peer_key, txids)

    async def _handle_tx_mempool_req(self, peer: _PeerState, payload: bytes) -> None:
        if not self._tx_relay_enabled or not self._p2p_tx_enabled or not self._tx_relay_v2_enabled:
            return
        data = self._decode_payload_map(payload)
        limit = data.get("limit")
        peer_key = self._peer_tx_key(peer)
        await self._txrelay.on_mempool_req(peer_key, limit=limit)

    async def _handle_tx_mempool_resp(self, peer: _PeerState, payload: bytes) -> None:
        if not self._tx_relay_enabled or not self._p2p_tx_enabled or not self._tx_relay_v2_enabled:
            return
        data = self._decode_payload_map(payload)
        txids = parse_txids(data.get("txids") or [])
        peer_key = self._peer_tx_key(peer)
        await self._txrelay.on_mempool_resp(peer_key, txids)

    async def _handle_tx_mempool_summary(self, peer: _PeerState, payload: bytes) -> None:
        if not self._tx_relay_enabled or not self._p2p_tx_enabled or not self._tx_relay_v2_enabled:
            return
        data = self._decode_payload_map(payload)
        txids = parse_txids(data.get("txids") or [])
        count = int(data.get("count") or len(txids))
        peer_key = self._peer_tx_key(peer)
        await self._txrelay.on_mempool_summary(peer_key, txids, count=count)

    async def _handle_getdata(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        items = data.get("items") or []
        if len(items) > self._max_inv_per_msg:
            raise PeerMisbehavior(
                "getdata_oversized",
                points=self._score_points["malformed_message"],
            )
        req_items: list[InvItem] = []
        for it in items:
            if isinstance(it, dict):
                req_items.append(InvItem(**it))
        req = GetData(items=req_items)

        txs: list[tuple[bytes, bytes, bytes]] = []
        blocks: list[bytes] = []
        for it in req.items:
            if int(it.typ) == int(InvType.TX):
                if not self._tx_relay_allowed():
                    continue
                self._stats["tx_getdata_recv"] += 1
                if self._rate_limit(
                    self._getdata_inflight_by_peer,
                    peer.remote,
                    self._max_getdata_per_min_per_peer,
                    self._tx_rate_window_s,
                ):
                    self._stats["tx_getdata_rate_limited"] += 1
                    self._penalize_peer(
                        peer,
                        "getdata_rate_limited",
                        points=self._score_points["malformed_message"],
                        nonfatal=True,
                    )
                    log.warning(
                        "getdata rate limited (incoming)",
                        extra={"peer": peer.remote},
                    )
                    return
                raw = await self._pending_get(bytes(it.h))
                if raw:
                    canonical_raw = raw
                    try:
                        from core.utils.tx import normalize_tx_bytes

                        canonical_raw = normalize_tx_bytes(raw)
                    except Exception:
                        canonical_raw = raw
                    try:
                        from core.utils.hash import sha3_256

                        canonical_hash = sha3_256(canonical_raw)
                    except Exception:
                        canonical_hash = hashlib.sha3_256(canonical_raw).digest()
                    if len(raw) > self._max_tx_bytes:
                        self._penalize_peer(
                            peer,
                            "getdata_tx_oversize",
                            points=self._score_points["malformed_message"],
                            nonfatal=True,
                        )
                        continue
                    request_hash = bytes(it.h)
                    if self._sent_recently(
                        self._peer_tx_key(peer), request_hash
                    ) or self._sent_recently(self._peer_tx_key(peer), canonical_hash):
                        self._stats["tx_sent_dedup"] += 1
                        continue
                    txs.append((canonical_raw, canonical_hash, request_hash))
            elif int(it.typ) == int(InvType.BLOCK):
                rawb = self._get_block_raw(bytes(it.h))
                if rawb:
                    blocks.append(rawb)

        for raw, txh, request_hash in txs:
            await self._send(peer, MsgID.TX, Tx(raw_cbor=raw))
            self._stats["tx_sent"] += 1
            self._stats["tx_data_sent_total"] += 1
            self._remember_sent(self._peer_tx_key(peer), txh)
            if request_hash != txh:
                self._remember_sent(self._peer_tx_key(peer), request_hash)
            peer.last_tx_data_sent_at = time.time()
            log.info(
                "tx delivered to peer",
                extra={"peer": peer.remote, "tx_hash": txh.hex()},
            )
            log.info(
                "TX_DATA_SENT",
                extra={"peer": peer.remote, "hash": txh.hex(), "bytes": len(raw)},
            )
            log.info(
                "p2p.tx.data_sent",
                extra={"peer": peer.remote, "hash": txh.hex(), "bytes": len(raw)},
            )

        if blocks:
            # Chunk to avoid oversized frames.
            chunk: list[bytes] = []
            size = 0
            for b in blocks:
                if size + len(b) > 6 * 1024 * 1024 and chunk:
                    await self._send(peer, MsgID.BLOCKS, Blocks(blocks=chunk))
                    self._stats["blocks_sent"] += len(chunk)
                    chunk, size = [], 0
                chunk.append(b)
                size += len(b)
            if chunk:
                await self._send(peer, MsgID.BLOCKS, Blocks(blocks=chunk))
                self._stats["blocks_sent"] += len(chunk)

    async def _handle_tx(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        txm = Tx(**data)
        raw = bytes(txm.raw_cbor)
        if not raw:
            return
        if not self._tx_relay_allowed():
            return
        if len(raw) > self._max_tx_bytes:
            self._penalize_peer(
                peer,
                "tx_oversize",
                points=self._score_points["malformed_message"],
                nonfatal=True,
            )
            log.warning(
                "oversized tx from peer",
                extra={"peer": peer.remote, "size": len(raw)},
            )
            return
        if self._rate_limit(
            self._tx_inflight_by_peer,
            peer.remote,
            self._max_tx_per_min_per_peer,
            self._tx_rate_window_s,
        ):
            self._stats["tx_recv_rate_limited"] += 1
            self._penalize_peer(
                peer,
                "tx_rate_limited",
                points=self._score_points["malformed_message"],
                nonfatal=True,
            )
            log.warning(
                "tx rate limited from peer",
                extra={"peer": peer.remote},
            )
            return

        from core.utils.hash import sha3_256
        from core.utils.tx import normalize_tx_bytes

        try:
            canonical_raw = normalize_tx_bytes(raw)
        except Exception:
            canonical_raw = raw

        txh = sha3_256(canonical_raw)
        if self._seen(self._seen_tx, txh):
            return
        self._remember(self._seen_tx, txh, self._seen_tx_cap)
        self._stats["tx_recv"] += 1
        self._stats["tx_data_recv_total"] += 1
        self._tx_requested.pop(txh, None)
        peer.last_tx_data_recv_at = time.time()
        log.info(
            "tx.tx_recv",
            extra={"tx_hash": txh.hex(), "peer": peer.remote},
        )
        log.info(
            "TX_DATA_RECV",
            extra={"peer": peer.remote, "hash": txh.hex(), "bytes": len(raw)},
        )
        log.info(
            "p2p.tx.data_recv",
            extra={"peer": peer.remote, "hash": txh.hex(), "bytes": len(raw)},
        )

        ok, reason = await self._admit_tx_result(
            raw, local=False, origin_peer=peer.peer_id or peer.remote
        )
        if ok:
            mempool_size = await self._mempool_size()
            log.info(
                "TX_VALIDATE_OK",
                extra={"hash": txh.hex()},
            )
            log.info(
                "tx.mempool_added",
                extra={"tx_hash": txh.hex(), "peer": peer.remote},
            )
            log.info(
                "TX_MEMPOOL_ADDED",
                extra={
                    "hash": txh.hex(),
                    "origin": f"peer:{peer.peer_id or peer.remote}",
                    "mempool_size": mempool_size,
                },
            )
            await self._broadcast_inv(
                [InvItem(typ=InvType.TX, h=txh)], exclude_remote=peer.remote, is_tx=True
            )
        else:
            log.info(
                "TX_VALIDATE_REJECT",
                extra={
                    "hash": txh.hex(),
                    "reason": self._tx_reject_category(reason),
                    "detail": reason,
                    **self._tx_debug_fields(raw),
                },
            )
            log.info(
                "tx.mempool_rejected",
                extra={"tx_hash": txh.hex(), "peer": peer.remote, "reason": reason},
            )
            log.info(
                "tx.rejected",
                extra={"hash": txh.hex(), "peer": peer.remote, "reason": reason},
            )
            log.info(
                "TX_MEMPOOL_REJECTED",
                extra={
                    "hash": txh.hex(),
                    "origin": f"peer:{peer.peer_id or peer.remote}",
                    "reason": reason,
                },
            )
            self._record_tx_reject(
                tx_hash=txh.hex(),
                origin=f"peer:{peer.peer_id or peer.remote}",
                reason=reason,
            )

    async def _handle_get_headers(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        req = GetHeaders(**data)
        locator = [bytes(h) for h in req.locator if isinstance(h, (bytes, bytearray))]
        bdb = self._block_db()
        genesis = bdb.get_canonical_hash(0) or bdb.get_genesis_hash() or self._genesis_hash()
        if not locator and genesis:
            locator = [bytes(genesis)]
        if locator and not any(self._has_header(h) for h in locator):
            if genesis and bytes(genesis) in locator:
                locator = [bytes(genesis)]
            elif self._peer_chain_matches(peer):
                if genesis:
                    locator = [bytes(genesis)]
            else:
                log.warning(
                    "Rejecting getheaders locator: unknown chain",
                    extra={
                        "remote": peer.remote,
                        "locator_count": len(locator),
                        "locator_start": locator[0].hex() if locator else None,
                        "locator_end": locator[-1].hex() if locator else None,
                    },
                )
                await self._send(
                    peer,
                    MsgID.ERROR,
                    Error(code=1, message="wrong_network", details="unknown locator"),
                )
                return
        (
            anchor_height,
            anchor_hash,
            head_height,
            _db_head_height,
            _chain_headers,
        ) = self._locate_anchor(locator)
        headers = self._headers_after_locator(locator, limit=int(req.max_headers or 64))
        info = self._headers_debug_info(headers)
        log.debug(
            "Serving headers",
            extra={
                "remote": peer.remote,
                "locator_count": len(locator),
                "locator_start": locator[0].hex() if locator else None,
                "locator_end": locator[-1].hex() if locator else None,
                "anchor_height": anchor_height,
                "anchor_hash": anchor_hash.hex() if anchor_hash else None,
                "tip_height": head_height,
                "tip_hash": self._canonical_head_for_status()[1],
                "served_from_height": info.get("first_height"),
                "served_to_height": info.get("last_height"),
                "served_from_hash": info.get("first_hash"),
                "served_to_hash": info.get("last_hash"),
                "count": info.get("count"),
            },
        )
        await self._send(peer, MsgID.HEADERS, Headers(headers=headers))

    async def _handle_headers(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        headers: list[HeaderCompact] = []
        for h in data.get("headers") or []:
            if isinstance(h, dict):
                headers.append(HeaderCompact(**h))
            elif isinstance(h, HeaderCompact):
                headers.append(h)
        msg = Headers(headers=headers)
        if len(msg.headers) > self._max_headers_per_message:
            raise PeerMisbehavior(
                "headers_oversized", points=self._score_points["malformed_message"]
            )
        info = self._headers_debug_info(headers)
        log.debug(
            "Received headers message",
            extra={
                "remote": peer.remote,
                "peer_id": peer.peer_id,
                **info,
            },
        )

        # If we have a pending request waiting on this response, fulfill it.
        fut = peer.pending_headers
        if fut is not None and not fut.done() and self._match_header_response(peer):
            if peer.last_header_request_at:
                self._update_latency(peer, peer.last_header_request_at)
            self._clear_header_request(peer)
            fut.set_result(msg)
            peer.pending_headers = None
            self._sync_last_header_response_at = time.time()
            self._sync_last_header_response_peer = peer.remote
            self._sync_last_header_response_count = len(msg.headers)
            if msg.headers:
                last = msg.headers[-1]
                self._update_peer_head(
                    peer,
                    height=int(last.height),
                    head_hash=bytes(last.hash),
                )
                self._sync_last_header_error = None
                self._sync_last_header_error_at = None
                self._sync_last_header_error_peer = None
            return

        if fut is not None and not fut.done():
            log.debug(
                "Ignoring unsolicited headers response",
                extra={"remote": peer.remote, "peer_id": peer.peer_id, **info},
            )
        # Treat as announcements; queue for sync loop to validate & download.
        if msg.headers:
            last = msg.headers[-1]
            local_height, _ = self._local_head()
            if int(local_height or 0) >= int(last.height):
                all_known = all(
                    self._has_header(bytes(h.hash))
                    or bytes(h.hash) in self._sync_headers
                    for h in msg.headers
                )
                if all_known:
                    self._update_peer_head(
                        peer,
                        height=int(last.height),
                        head_hash=bytes(last.hash),
                    )
                    return
            self._update_peer_head(
                peer,
                height=int(last.height),
                head_hash=bytes(last.hash),
            )
            self._record_sync_header_event(
                {
                    "type": "announce",
                    "peer": peer.remote,
                    "peer_id": peer.peer_id,
                    **info,
                }
            )
            self._sync_header_queue.append((peer.remote, list(msg.headers)))
            self._sync_wakeup.set()

    async def _handle_snapshots(self, peer: _PeerState, payload: bytes) -> None:
        """Handle SNAPSHOTS response from peer."""
        try:
            from p2p.wire.messages import SnapshotInfo

            def _snapshot_from_sequence(seq: list[Any] | tuple[Any, ...]) -> dict[str, Any]:
                fields = (
                    "chain_id",
                    "checkpoint_height",
                    "checkpoint_hash",
                    "blocks_count",
                    "accounts_count",
                    "size_mb",
                    "timestamp",
                    "created_at",
                    "manifest_hash",
                )
                snapshot_dict = {field: value for field, value in zip(fields, seq)}
                for key in ("checkpoint_hash", "manifest_hash"):
                    value = snapshot_dict.get(key)
                    if isinstance(value, (bytes, bytearray)):
                        snapshot_dict[key] = "0x" + bytes(value).hex()
                return snapshot_dict

            data = self._decode_map(payload)
            raw_snapshots = data.get("snapshots") or []

            # Convert raw snapshot data to dicts
            snapshots = []
            for snap_data in raw_snapshots:
                if isinstance(snap_data, dict):
                    snapshots.append(snap_data)
                elif isinstance(snap_data, (list, tuple)):
                    snapshots.append(_snapshot_from_sequence(snap_data))
                elif hasattr(snap_data, 'to_dict'):
                    snapshots.append(snap_data.to_dict())
                else:
                    # Try to extract fields from SnapshotInfo-like object
                    try:
                        snapshot_dict = {
                            "chain_id": getattr(snap_data, 'chain_id', 0),
                            "checkpoint_height": getattr(snap_data, 'checkpoint_height', 0),
                            "checkpoint_hash": getattr(snap_data, 'checkpoint_hash', ''),
                            "blocks_count": getattr(snap_data, 'blocks_count', 0),
                            "accounts_count": getattr(snap_data, 'accounts_count', 0),
                            "size_mb": getattr(snap_data, 'size_mb', 0.0),
                            "timestamp": getattr(snap_data, 'timestamp', 0),
                            "created_at": getattr(snap_data, 'created_at', ''),
                            "manifest_hash": getattr(snap_data, 'manifest_hash', ''),
                        }
                        snapshots.append(snapshot_dict)
                    except Exception as e:
                        self._log.debug(f"Failed to convert snapshot data: {e}")
                        continue

            self._log.debug(
                f"Received {len(snapshots)} snapshot(s) from {peer.remote}"
            )

            # If we have a pending request, fulfill it
            fut = peer.pending_snapshot_list
            if fut is not None and not fut.done():
                fut.set_result(snapshots)

        except Exception as e:
            self._log.warning(f"Error handling SNAPSHOTS from {peer.remote}: {e}")
            fut = peer.pending_snapshot_list
            if fut is not None and not fut.done():
                fut.set_result([])

    async def _handle_snapshot_chunk(self, peer: _PeerState, payload: bytes) -> None:
        """Handle SNAPSHOT_CHUNK response from peer."""
        try:
            data = self._decode_map(payload)
            chunk_data = data.get("data") or b""
            found = data.get("found", False)

            self._log.debug(
                f"Received snapshot chunk from {peer.remote}: "
                f"{len(chunk_data)} bytes, found={found}"
            )

            # If we have a pending request, fulfill it
            fut = peer.pending_snapshot_chunk
            if fut is not None and not fut.done():
                fut.set_result((chunk_data, found))

        except Exception as e:
            self._log.warning(f"Error handling SNAPSHOT_CHUNK from {peer.remote}: {e}")
            fut = peer.pending_snapshot_chunk
            if fut is not None and not fut.done():
                fut.set_result((b"", False))

    async def _handle_get_snapshots(self, peer: _PeerState, payload: bytes) -> None:
        """Handle GET_SNAPSHOTS request from peer - respond with local snapshot list."""
        try:
            from p2p.wire.messages import GetSnapshots, Snapshots, SnapshotInfo
            from p2p.wire.message_ids import MsgID

            # Decode request
            data = self._decode_map(payload)
            req = GetSnapshots(**data)

            self._log.debug(
                f"Received GET_SNAPSHOTS request from {peer.remote}, chain_id={req.chain_id}"
            )

            # List available snapshots
            snapshots = self._list_local_snapshots(req.chain_id)

            # Build and send response
            response = Snapshots(snapshots=snapshots)
            await self._send(peer, MsgID.SNAPSHOTS, response)

            self._log.debug(f"Sent {len(snapshots)} snapshot(s) to {peer.remote}")

        except Exception as e:
            self._log.warning(f"Error handling GET_SNAPSHOTS from {peer.remote}: {e}", exc_info=True)
            # Send empty response on error
            try:
                from p2p.wire.messages import Snapshots
                from p2p.wire.message_ids import MsgID
                empty_response = Snapshots(snapshots=[])
                await self._send(peer, MsgID.SNAPSHOTS, empty_response)
            except Exception:
                pass  # Best effort

    async def _handle_get_snapshot_chunk(self, peer: _PeerState, payload: bytes) -> None:
        """Handle GET_SNAPSHOT_CHUNK request from peer - respond with chunk data."""
        # Initialize defaults for error handling
        req_chain_id = 0
        req_height = 0
        req_chunk_name = ""

        try:
            from p2p.wire.messages import GetSnapshotChunk, SnapshotChunk
            from p2p.wire.message_ids import MsgID

            # Decode request
            data = self._decode_map(payload)
            req = GetSnapshotChunk(**data)
            req_chain_id = req.chain_id
            req_height = req.checkpoint_height
            req_chunk_name = req.chunk_name

            self._log.debug(
                f"Received GET_SNAPSHOT_CHUNK request from {peer.remote}: "
                f"chain_id={req.chain_id}, height={req.checkpoint_height}, chunk={req.chunk_name}"
            )

            # Read the chunk file
            chunk_data, found = self._read_snapshot_chunk(
                req.chain_id, req.checkpoint_height, req.chunk_name
            )

            # Build and send response
            response = SnapshotChunk(
                chain_id=req.chain_id,
                checkpoint_height=req.checkpoint_height,
                chunk_name=req.chunk_name,
                data=chunk_data,
                found=found,
            )
            await self._send(peer, MsgID.SNAPSHOT_CHUNK, response)

            if found:
                self._log.info(
                    f"Sent snapshot chunk {req.chunk_name} ({len(chunk_data)} bytes) "
                    f"to {peer.remote}"
                )
            else:
                self._log.debug(f"Snapshot chunk {req.chunk_name} not found for {peer.remote}")

        except Exception as e:
            self._log.warning(f"Error handling GET_SNAPSHOT_CHUNK from {peer.remote}: {e}", exc_info=True)
            # Send not-found response on error using captured values
            try:
                from p2p.wire.messages import SnapshotChunk
                from p2p.wire.message_ids import MsgID
                error_response = SnapshotChunk(
                    chain_id=req_chain_id,
                    checkpoint_height=req_height,
                    chunk_name=req_chunk_name,
                    data=b"",
                    found=False,
                )
                await self._send(peer, MsgID.SNAPSHOT_CHUNK, error_response)
            except Exception:
                pass  # Best effort

    def _get_snapshots_dir(self) -> Path:
        """Get the snapshots directory path."""
        from core.snapshot.paths import get_snapshots_dir

        return get_snapshots_dir(self._chain_data_dir)

    def _get_snapshots_dirs(self) -> list[Path]:
        """Get all snapshot directory paths to scan."""
        from core.snapshot.paths import get_snapshots_dirs

        return get_snapshots_dirs(self._chain_data_dir)

    def _list_local_snapshots(self, chain_id: Optional[int] = None) -> list:
        """
        List available snapshots from the local snapshots directory.

        Args:
            chain_id: Optional chain ID filter

        Returns:
            List of SnapshotInfo objects
        """
        from core.snapshot.inventory import list_snapshots_from_dirs
        from p2p.wire.messages import SnapshotInfo

        snapshots = []
        snapshots_dirs = self._get_snapshots_dirs()
        entries = list_snapshots_from_dirs(snapshots_dirs)
        for entry in entries:
            if chain_id is not None and entry.chain_id != chain_id:
                continue
            size_mb = entry.total_size / (1024 * 1024)
            snapshots.append(
                SnapshotInfo(
                    chain_id=entry.chain_id,
                    checkpoint_height=entry.checkpoint_height,
                    checkpoint_hash=entry.checkpoint_hash,
                    blocks_count=entry.blocks_count,
                    accounts_count=entry.accounts_count,
                    size_mb=size_mb,
                    timestamp=entry.timestamp,
                    created_at=entry.created_at,
                    manifest_hash=entry.manifest_hash,
                )
            )

        snapshots.sort(key=lambda s: (s.chain_id, -s.checkpoint_height))

        self._log.debug(
            f"Found {len(snapshots)} snapshot(s) across {len(snapshots_dirs)} directory(ies)"
        )
        return snapshots

    def _read_snapshot_chunk(
        self, chain_id: int, checkpoint_height: int, chunk_name: str
    ) -> tuple[bytes, bool]:
        """
        Read a snapshot chunk file.

        Args:
            chain_id: Chain ID
            checkpoint_height: Snapshot checkpoint height
            chunk_name: Name of the chunk file (e.g., "blocks.tar.zst")

        Returns:
            Tuple of (chunk_data, found)
        """
        snapshots_dirs = self._get_snapshots_dirs()
        if not snapshots_dirs:
            return b"", False

        # Construct snapshot directory name
        snapshot_dir = None
        for candidate_root in snapshots_dirs:
            if not candidate_root.exists():
                continue
            candidate_dir = candidate_root / f"chain-{chain_id}-height-{checkpoint_height}"
            if candidate_dir.exists() and candidate_dir.is_dir():
                snapshot_dir = candidate_dir
                break
        if snapshot_dir is None:
            self._log.debug(
                f"Snapshot directory not found for chain {chain_id} height {checkpoint_height}"
            )
            return b"", False

        # Read the chunk file
        chunk_path = snapshot_dir / chunk_name
        if not chunk_path.exists() or not chunk_path.is_file():
            self._log.debug(f"Chunk file not found: {chunk_path}")
            return b"", False

        try:
            with open(chunk_path, "rb") as f:
                data = f.read()
            self._log.debug(f"Read chunk {chunk_name} ({len(data)} bytes) from {snapshot_dir}")
            return data, True
        except (IOError, OSError) as e:
            self._log.warning(f"Failed to read chunk {chunk_path}: {e}")
            return b"", False

    async def _handle_get_blocks(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        req = GetBlocks(**data)
        blocks: list[bytes] = []
        for h in list(req.by_hash)[: int(req.max_blocks or 16)]:
            rawb = self._get_block_raw(bytes(h))
            if rawb:
                blocks.append(rawb)
        if blocks:
            chunk: list[bytes] = []
            size = 0
            for b in blocks:
                if size + len(b) > 6 * 1024 * 1024 and chunk:
                    await self._send(peer, MsgID.BLOCKS, Blocks(blocks=chunk))
                    self._stats["blocks_sent"] += len(chunk)
                    chunk, size = [], 0
                chunk.append(b)
                size += len(b)
            if chunk:
                await self._send(peer, MsgID.BLOCKS, Blocks(blocks=chunk))
                self._stats["blocks_sent"] += len(chunk)

    async def _enqueue_verify_task(
        self, peer: _PeerState, sync_block: _SyncBlock, raw_len: int
    ) -> None:
        if self._running:
            self._start_verify_workers()
        if not self._running or not self._sync_verify_tasks:
            start = time.time()
            ok = False
            reason = None
            try:
                ok, reason = await self._import_block_payload(
                    sync_block.block, origin_remote=peer.remote
                )
            except Exception as exc:
                ok = False
                reason = f"verify_error:{exc}"
            duration_ms = (time.time() - start) * 1000
            self._record_verify_metrics(duration_ms, batch_size=1)
            await self._finalize_block_import(
                sync_block,
                peer_remote=peer.remote,
                ok=ok,
                reason=reason,
            )
            return
        if self._sync_verify_queue.full():
            self._sync_block_stalled_reason = STALL_VERIFY_BACKPRESSURE
            self._sync_last_block_error = STALL_VERIFY_BACKPRESSURE
            self._sync_last_block_error_at = time.time()
            log.warning(
                "Verify queue full; buffering block",
                extra={
                    "remote": peer.remote,
                    "verify_queue_depth": self._sync_verify_queue.qsize(),
                    "verify_queue_limit": self._sync_verify_queue_limit,
                },
            )
            sync_block.origin_peer = peer.remote
            self._buffer_orphan_block(sync_block)
            self._sync_inflight_blocks.pop(sync_block.hash, None)
            self._sync_inflight_peers.pop(sync_block.hash, None)
            self._sync_inflight_block_requests.pop(sync_block.hash, None)
            self._sync_block_retry_counts.pop(sync_block.hash, None)
            return
        task = _SyncVerifyTask(
            sync_block=sync_block,
            peer_remote=peer.remote,
            enqueued_at=time.time(),
            raw_bytes_len=raw_len,
        )
        await self._sync_verify_queue.put(task)

    async def _finalize_block_import(
        self,
        sync_block: _SyncBlock,
        *,
        peer_remote: str,
        ok: bool,
        reason: Optional[str],
    ) -> None:
        peer = self._peer_by_remote(peer_remote)
        self._sync_block_retry_counts.pop(sync_block.hash, None)
        if not ok and self._is_duplicate_reason(reason):
            self._drop_from_block_queue(sync_block.hash)
            ok = True
            reason = None
        if ok:
            if peer is not None:
                self._record_block_success(peer)
                self._sync_block_attempts_by_hash.pop(sync_block.hash, None)
                try:
                    if hasattr(sync_block.block, "header"):
                        self._update_peer_head(
                            peer,
                            height=int(getattr(sync_block.block.header, "height", 0)),
                            head_hash=sync_block.hash,
                        )
                except Exception:
                    pass
                peer.broadcast.successful_blocks_served += 1
                peer.score += 10
                peer.blocks_served += 1
                peer.last_block_time = time.time()
                peer.broadcast.last_head_advancement_at = time.time()
                self._stats["peer_broadcast_good"] += 1
                peer.sync_successes += 1
                peer.last_progress_at = time.time()
            self._sync_inflight_blocks.pop(sync_block.hash, None)
            self._sync_inflight_peers.pop(sync_block.hash, None)
            self._sync_inflight_block_requests.pop(sync_block.hash, None)
            self._sync_last_block_at = time.time()
            self._sync_last_progress_at = self._sync_last_block_at
            head_height, head_hash = self._local_head()
            log.info(
                "BLOCK_IMPORTED",
                extra={
                    "height": int(head_height or 0),
                    "hash": self._canon_hash0x(sync_block.hash),
                },
            )
            self._note_sync_progress(
                reason="block_imported",
                head_height=int(head_height or 0),
                head_hash=head_hash,
            )
            self._sync_block_stalled_reason = None
            self._sync_wakeup.set()
            self._refresh_locator_summary()
            self._record_block_committed_metrics()
            await self._drain_block_buffer()
            return

        self._sync_inflight_blocks.pop(sync_block.hash, None)
        self._sync_inflight_peers.pop(sync_block.hash, None)
        self._sync_inflight_block_requests.pop(sync_block.hash, None)
        if self._is_orphan_reason(reason):
            if peer is not None:
                sync_block.origin_peer = peer.remote
            self._stats["blocks_orphaned"] += 1
            self._buffer_orphan_block(sync_block)
            if peer is not None:
                self._handle_missing_parent(peer, sync_block)
            return
        reject_reason = reason or "block_rejected"
        if peer is not None:
            self._record_block_failure(peer, reason=reject_reason)
        if self._sync_cache is not None and not self._is_orphan_reason(reject_reason):
            self._sync_cache.invalidate_block(sync_block.hash)
        self._sync_last_block_error_peer = peer_remote
        summary = self._sync_block_error_summary.setdefault(
            peer_remote,
            {"count": 0, "last_error": None, "last_at": 0.0},
        )
        summary["count"] = int(summary.get("count", 0)) + 1
        summary["last_error"] = reject_reason
        summary["last_at"] = time.time()
        if self._is_db_write_error(reject_reason):
            self._sync_block_stalled_reason = STALL_BLOCK_INVALID_RESPONSE
            self._sync_last_block_error = f"db not writable: {reject_reason}"
            log.error(
                "Block DB write failed",
                extra={"remote": peer_remote, "error": reject_reason},
            )
        log.warning(
            "Block rejected",
            extra={
                "remote": peer_remote,
                "reason": reject_reason,
            },
        )
        if "pow target not met" in reject_reason.lower() and peer is not None:
            corroborated = self._record_pow_mismatch(sync_block.hash, peer=peer)
            self._set_block_backoff(peer, reason="consensus_mismatch_pow", delay=60.0)
            if sync_block.hash not in self._sync_block_queue_set:
                self._sync_block_queue.append(sync_block.hash)
                self._sync_block_queue_set.add(sync_block.hash)
            if corroborated:
                self._penalize_peer(
                    peer,
                    "consensus_mismatch_pow",
                    severity=2,
                    quarantine_s=300.0,
                )
        elif peer is not None:
            self._set_block_backoff(peer, reason="bad_block", delay=60.0)
            self._penalize_peer(
                peer,
                f"block_rejected:{reject_reason}",
                severity=2,
                quarantine_s=300.0,
            )

    async def _handle_blocks(self, peer: _PeerState, payload: bytes) -> None:
        if self._should_forfeit_peer_blocks(peer):
            self._stats["blocks_rejected"] += 1
            self._record_block_failure(peer, reason="outbound_only_block_forfeit")
            self._mark_outbound_only_blocklisted(
                peer_id=peer.peer_id,
                remote=peer.remote,
                reason="block_forfeit",
            )
            log.warning(
                "Forfeiting block payload from outbound-only peer",
                extra={"remote": peer.remote, "peer_id": peer.peer_id},
            )
            self._create_child_task(
                self._drop_peer(peer, reason="outbound_only_block_forfeit"),
                name=f"p2p.drop_peer@{peer.remote}",
            )
            return
        data = self._decode_map(payload)
        msg = Blocks(**data)
        if len(msg.blocks) > self._max_blocks_per_message:
            raise PeerMisbehavior(
                "blocks_oversized", points=self._score_points["malformed_message"]
            )
        if not msg.blocks:
            self._stats["blocks_req_empty"] += 1
            self._record_block_failure(peer, reason="empty_blocks")
            return
        if peer.last_block_request_at:
            self._update_latency(peer, peer.last_block_request_at)
        for rawb in msg.blocks:
            self._stats["blocks_recv"] += 1
            self._stats["blocks_received"] += 1
            self._sync_last_block_response_at = time.time()
            self._sync_last_block_download_at = self._sync_last_block_response_at
            self._sync_active_block_peer = peer.remote
            log.info(
                "Block received",
                extra={"remote": peer.remote, "bytes": len(rawb)},
            )
            raw_bytes = bytes(rawb)
            self._record_block_received_metrics(peer.remote, len(raw_bytes))
            try:
                sync_block = self._decode_block(raw_bytes)
            except Exception as e:
                self._penalize_peer(peer, f"bad_block_decode:{e.__class__.__name__}")
                self._record_block_failure(peer, reason="decode_error")
                continue
            sync_block.received_at = time.time()
            self._record_header_vote(sync_block.hash, peer.remote)
            if sync_block.hash in self._sync_inflight_block_requests:
                self._stats["blocks_req_ok"] += 1
            if self._sync_cache is not None:
                height_hint = None
                if hasattr(sync_block.block, "header"):
                    try:
                        height_hint = int(getattr(sync_block.block.header, "height", 0))
                    except Exception:
                        height_hint = None
                self._sync_cache.put_block(
                    sync_block.hash,
                    raw_bytes,
                    height=height_hint,
                    source_peer=peer.remote,
                )
            confirmed, confirmation_reason, confirmation_ctx = (
                self._block_corroboration_status(
                    sync_block.hash, origin_remote=peer.remote
                )
            )
            if not confirmed:
                sync_block.origin_peer = peer.remote
                self._buffer_orphan_block(sync_block)
                self._sync_inflight_blocks.pop(sync_block.hash, None)
                self._sync_inflight_peers.pop(sync_block.hash, None)
                self._sync_inflight_block_requests.pop(sync_block.hash, None)
                self._sync_block_retry_counts.pop(sync_block.hash, None)
                self._sync_last_block_error = confirmation_reason
                self._sync_last_block_error_at = time.time()
                self._sync_last_block_error_peer = peer.remote
                log.info(
                    "Deferred block pending corroboration",
                    extra={
                        "remote": peer.remote,
                        "hash": sync_block.hash.hex(),
                        "reason": confirmation_reason,
                        "votes": confirmation_ctx.get("votes"),
                        "required_votes": confirmation_ctx.get("required"),
                        "eligible_peers": confirmation_ctx.get("eligible_peers"),
                        "force_connected": confirmation_ctx.get("force_connected"),
                        "force_voted": confirmation_ctx.get("force_voted"),
                    },
                )
                self._sync_wakeup.set()
                continue
            if sync_block.parent_hash and not self._has_block(sync_block.parent_hash):
                sync_block.origin_peer = peer.remote
                self._stats["blocks_orphaned"] += 1
                self._buffer_orphan_block(sync_block)
                self._handle_missing_parent(peer, sync_block)
                self._sync_inflight_blocks.pop(sync_block.hash, None)
                self._sync_inflight_peers.pop(sync_block.hash, None)
                self._sync_inflight_block_requests.pop(sync_block.hash, None)
                self._sync_block_retry_counts.pop(sync_block.hash, None)
                continue
            await self._enqueue_verify_task(peer, sync_block, len(raw_bytes))

    async def _handle_block_announce(self, peer: _PeerState, payload: bytes) -> None:
        if self._should_forfeit_peer_blocks(peer):
            self._mark_outbound_only_blocklisted(
                peer_id=peer.peer_id,
                remote=peer.remote,
                reason="block_announce_forfeit",
            )
            log.warning(
                "Ignoring block announce from outbound-only peer",
                extra={"remote": peer.remote, "peer_id": peer.peer_id},
            )
            self._create_child_task(
                self._drop_peer(peer, reason="outbound_only_block_announce"),
                name=f"p2p.drop_peer@{peer.remote}",
            )
            return
        if proto_blk is None:
            log.debug("Block announce protocol unavailable")
            return
        if len(payload) > self._max_payload_bytes:
            raise PeerMisbehavior(
                "payload_too_large", points=self._score_points["malformed_message"]
            )
        announce = None
        try:
            announce = proto_blk.parse_announce(payload)
        except Exception:
            announce = None

        if announce is not None:
            now = time.time()
            peer.broadcast.last_inventory_at = now
            peer.broadcast.last_head_advancement_at = now
            self._stats["peer_broadcast_good"] += 1
            if self._has_block(announce.header_hash) or self._seen(
                self._seen_blocks, announce.header_hash
            ):
                return
            self._remember(self._seen_blocks, announce.header_hash, self._seen_block_cap)
            self._sync_header_sources[announce.header_hash] = peer.remote
            self._record_header_vote(announce.header_hash, peer.remote)
            if announce.header_hash in self._sync_block_buffer:
                self._create_child_task(
                    self._drain_block_buffer(),
                    name=f"p2p.drain_block_buffer@{peer.remote}",
                )
            self._update_peer_head(
                peer,
                height=announce.height,
                head_hash=announce.header_hash,
            )
            if (
                announce.parent_hash
                and announce.parent_hash != b"\x00" * 32
                and not self._has_block(announce.parent_hash)
            ):
                if announce.parent_hash not in self._sync_block_queue_set:
                    parent_height = max(0, int(announce.height) - 1)
                    self._sync_block_queue.appendleft(announce.parent_hash)
                    self._sync_block_queue_set.add(announce.parent_hash)
                    self._sync_block_queue_heights[announce.parent_hash] = parent_height
            if announce.header_hash not in self._sync_block_queue_set:
                self._sync_block_queue.append(announce.header_hash)
                self._sync_block_queue_set.add(announce.header_hash)
                self._sync_block_queue_heights[announce.header_hash] = int(
                    announce.height
                )
            log.info(
                "Block announced",
                extra={
                    "remote": peer.remote,
                    "height": announce.height,
                    "hash": announce.header_hash.hex(),
                    "parent": announce.parent_hash.hex(),
                },
            )
            self._sync_wakeup.set()
            await self._schedule_block_requests(peer)
            return

        try:
            req = proto_blk.parse_get_block(payload)
        except Exception:
            log.debug(
                "Ignoring unrecognized block announce payload",
                extra={"remote": peer.remote},
            )
            return
        rawb = self._get_block_raw(req.header_hash)
        if rawb:
            await self._send(peer, MsgID.BLOCKS, Blocks(blocks=[rawb]))

    # ---------------------------------------------------------------------
    # Gossip + sync loops
    # ---------------------------------------------------------------------

    async def _head_watch_loop(self) -> None:
        last: Optional[str] = None
        last_height = 0
        last_network_best = 0
        try:
            while self._running:
                await asyncio.sleep(1.0)
                height, hh = self._local_head()
                if hh and hh != last:
                    if last is not None and (
                        height < last_height
                        or (height == last_height and hh != last)
                    ):
                        self._handle_reorg(height, hh)
                    last = hh
                    last_height = height
                    with contextlib.suppress(Exception):
                        block_hash = self._parse_hash_bytes(hh)
                        if block_hash:
                            await self.relay_block(block_hash)

                # Propagate network best height updates to keep all peers informed
                current_network_best = self._network_best_height() or 0
                if current_network_best > last_network_best + 10:  # Significant change threshold
                    last_network_best = current_network_best
                    log.debug(
                        "Network best height updated",
                        extra={
                            "network_best_height": current_network_best,
                            "local_height": height,
                        }
                    )
                    # Trigger re-handshake or send update to all peers
                    await self._propagate_network_height_update(current_network_best)
        except asyncio.CancelledError:
            return

    def _header_meta(self, h: bytes) -> Optional[Tuple[int, int]]:
        cached = self._sync_headers.get(h)
        if cached is not None:
            return cached.height, cached.timestamp
        try:
            hdr = self._block_db().get_header_by_hash(h)
        except Exception:
            hdr = None
        if hdr is None:
            return None
        try:
            height = int(getattr(hdr, "height"))
            ts = maybe_normalize_unix_timestamp_seconds(getattr(hdr, "timestamp", 0))
            if ts is None:
                ts = 0
            return height, ts
        except Exception:
            return None

    def _snapshot_anchor(self) -> Optional[tuple[int, bytes, str]]:
        from core.snapshot.inventory import latest_snapshot

        entry = latest_snapshot(
            chain_id=int(self.chain_id),
            snapshots_dir=self._get_snapshots_dir(),
        )
        if entry is None or not entry.checkpoint_hash:
            return None
        hash_bytes = self._parse_hash_bytes(entry.checkpoint_hash)
        if not hash_bytes:
            return None
        return entry.checkpoint_height, hash_bytes, "snapshot_inventory"

    def _anchor_candidates(self) -> dict[bytes, tuple[int, str]]:
        anchors: dict[bytes, tuple[int, str]] = {}
        genesis = self._genesis_hash()
        if genesis:
            anchors[bytes(genesis)] = (0, "genesis")
        local_height, local_hash_hex = self._local_head()
        local_hash = self._parse_hash_bytes(local_hash_hex)
        if local_hash:
            anchors[bytes(local_hash)] = (int(local_height or 0), "local_head")
        if self._sync_best_header is not None:
            anchors[bytes(self._sync_best_header.hash)] = (
                int(self._sync_best_header.height),
                "best_header_tip",
            )
        if self._sync_checkpoint_hash is not None and self._sync_checkpoint_height is not None:
            anchors[bytes(self._sync_checkpoint_hash)] = (
                int(self._sync_checkpoint_height),
                "checkpoint",
            )
        snapshot_anchor = self._snapshot_anchor()
        if snapshot_anchor is not None:
            height, anchor_hash, source = snapshot_anchor
            anchors[bytes(anchor_hash)] = (int(height), source)
        return anchors

    def _anchor_candidates_summary(self) -> list[dict[str, Any]]:
        summary = []
        for h, (height, source) in self._anchor_candidates().items():
            summary.append(
                {
                    "hash": h.hex(),
                    "height": height,
                    "source": source,
                }
            )
        return summary

    def _header_from_compact(self, hc: HeaderCompact) -> _SyncHeader:
        ts = maybe_normalize_unix_timestamp_seconds(getattr(hc, "timestamp", 0))
        if ts is None:
            ts = 0
        return _SyncHeader(
            hash=bytes(hc.hash),
            parent_hash=bytes(hc.parent),
            height=int(hc.height),
            theta_micro=int(hc.theta_micro),
            timestamp=int(ts),
        )

    def _sync_header_from_db(self, hdr: Any) -> _SyncHeader:
        ts = maybe_normalize_unix_timestamp_seconds(getattr(hdr, "timestamp", 0))
        if ts is None:
            ts = 0
        return _SyncHeader(
            hash=hdr.hash(),
            parent_hash=bytes(hdr.parentHash),
            height=int(hdr.height),
            theta_micro=int(getattr(hdr, "thetaMicro", 0)),
            timestamp=int(ts),
        )

    def _sync_header_by_hash(self, h: bytes) -> Optional[_SyncHeader]:
        cached = self._sync_headers.get(h)
        if cached is not None:
            return cached
        try:
            hdr = self._block_db().get_header_by_hash(h)
        except Exception:
            hdr = None
        if hdr is None:
            return None
        converted = self._sync_header_from_db(hdr)
        # Persist into the in-memory header cache so subsequent lookups
        # (notably _build_locator, which walks ~8000 parents per sync tick
        # via exponentially growing step sizes) don't re-read SQLite. Cache
        # capacity is bounded by chain length, which is ~12K entries on
        # mainnet — well under a few MB.
        self._sync_headers[h] = converted
        return converted

    def _sync_update_best_header(self, header: _SyncHeader) -> None:
        best = self._sync_best_header
        if best is None:
            self._sync_best_header = header
            return
        if header.height > best.height:
            self._sync_best_header = header
            return
        if header.height == best.height and header.hash > best.hash:
            self._sync_best_header = header

    def _header_extends_anchor(
        self,
        header: _SyncHeader,
        *,
        anchor_height: int,
        anchor_hash: Optional[bytes],
    ) -> bool:
        if int(header.height) != int(anchor_height) + 1:
            return False
        if anchor_hash is not None and header.parent_hash == anchor_hash:
            return True
        if int(anchor_height) == 0:
            genesis_hashes = {
                h
                for h in (
                    anchor_hash,
                    self._genesis_header_hash(),
                    self._genesis_block_hash(),
                    self._genesis_hash(),
                )
                if h
            }
            return header.parent_hash in genesis_hashes
        return False

    def _next_header_after_anchor(
        self,
        *,
        anchor_height: int,
        anchor_hash: Optional[bytes],
    ) -> Optional[_SyncHeader]:
        expected_height = int(anchor_height) + 1
        for header in self._sync_headers.values():
            if int(header.height) != expected_height:
                continue
            if self._header_extends_anchor(
                header,
                anchor_height=int(anchor_height),
                anchor_hash=anchor_hash,
            ):
                return header
        return None

    def _has_expected_block_work(
        self,
        *,
        expected_height: int,
    ) -> bool:
        for block_hash in self._sync_block_queue:
            if self._block_height_hint(block_hash) == int(expected_height):
                return True
        for block_hash in self._sync_inflight_blocks:
            if self._block_height_hint(block_hash) == int(expected_height):
                return True
        for block_hash in self._sync_block_buffer:
            if self._block_height_hint(block_hash) == int(expected_height):
                return True
        return False

    def _sync_cursor_disconnected_from_local_head(
        self,
        *,
        head_height: int,
        head_hash: Optional[bytes],
    ) -> bool:
        if self._sync_best_header is None:
            return False
        if int(self._sync_best_header.height) <= int(head_height):
            return False
        expected_height = int(head_height) + 1
        if self._next_header_after_anchor(
            anchor_height=int(head_height),
            anchor_hash=head_hash,
        ):
            return False
        if self._has_expected_block_work(expected_height=expected_height):
            return False
        return True

    def _canonical_hash_at_height(self, height: int) -> Optional[bytes]:
        try:
            bdb = self._block_db()
        except Exception:
            return None
        if not hasattr(bdb, "get_canonical_hash"):
            return None
        try:
            canonical_hash = bdb.get_canonical_hash(int(height))
        except Exception:
            canonical_hash = None
        if not canonical_hash:
            return None
        return bytes(canonical_hash)

    def _header_on_local_chain(self, header_hash: bytes, *, height: int) -> bool:
        canonical_hash = self._canonical_hash_at_height(height)
        if canonical_hash is None:
            return False
        return canonical_hash == bytes(header_hash)

    def _is_fork_sibling_header(self, header: "_SyncHeader") -> bool:
        """True when `header` is the base of a competing branch at/below our head.

        A node that accepted the losing side of a same-height fork (block 28167,
        block 38728) needs the WINNING sibling — a header whose height is at or
        below its own head, whose hash differs from its canonical block at that
        height, and whose parent IS on its canonical chain. The legacy headers
        pipeline dropped exactly that header at every layer (overlap trim,
        first-header anchor_mismatch, not-actionable reuse, below-head enqueue
        skip), so the losing branch could never hand fork choice the competing
        base and the node wedged forever. Bounded to the reorg depth the
        importer will actually accept.
        """
        try:
            height = int(header.height)
            if height <= 0:
                return False
            local_height, _ = self._local_head()
            local_height_int = int(local_height or 0)
            if height > local_height_int:
                return False
            depth = _max_reorg_depth()
            if depth <= 0 or (local_height_int - height) >= depth:
                return False
            if self._header_on_local_chain(header.hash, height=height):
                return False
            return self._header_on_local_chain(
                header.parent_hash, height=height - 1
            )
        except Exception:
            return False

    def _trim_leading_canonical_overlap(
        self, peer: _PeerState, headers: list[HeaderCompact], *, local_height: int
    ) -> tuple[list[HeaderCompact], int]:
        if not headers or local_height <= 0:
            return headers, 0
        trim_count = 0
        while trim_count < len(headers):
            header = self._header_from_compact(headers[trim_count])
            if header.height > local_height:
                break
            if not self._header_on_local_chain(header.hash, height=header.height):
                break
            trim_count += 1
        if trim_count > 0:
            first_trimmed = self._header_from_compact(headers[0])
            last_trimmed = self._header_from_compact(headers[trim_count - 1])
            log.info(
                "Trimming canonical overlap from headers batch",
                extra={
                    "remote": peer.remote,
                    "count": trim_count,
                    "first_height": int(first_trimmed.height),
                    "last_height": int(last_trimmed.height),
                    "local_height": int(local_height),
                },
            )
        return headers[trim_count:], trim_count

    def _reuse_known_headers(
        self, peer: _PeerState, headers: list[HeaderCompact]
    ) -> list[_SyncHeader]:
        if not headers:
            return []
        local_height, _ = self._local_head()
        local_height_int = int(local_height or 0)
        reused: list[_SyncHeader] = []
        actionable: list[_SyncHeader] = []
        recovered_missing_headers = False
        for hc in headers:
            header_hash = bytes(hc.hash)
            was_buffered = header_hash in self._sync_headers
            header = self._sync_header_by_hash(header_hash)
            if header is None:
                header = self._header_from_compact(hc)
            if header.hash not in self._sync_headers:
                self._sync_headers[header.hash] = header
                recovered_missing_headers = True
            self._sync_header_sources[header.hash] = peer.remote
            self._record_header_vote(header.hash, peer.remote)
            reused.append(header)
            if header.height > local_height_int:
                actionable.append(header)
            elif self._is_fork_sibling_header(header):
                # Base of a competing branch at/below our head: fetch its block
                # so fork choice can weigh the branch (38728 wedge — reused
                # headers at local height were never actionable, so the winning
                # sibling was dropped here even when a peer kept re-serving it).
                # Deliberately does NOT set recovered_missing_headers: progress
                # is only credited when the enqueue below actually queues the
                # block, so a re-served sibling whose fetch keeps failing can't
                # keep resetting the stall clock.
                actionable.append(header)
            elif not was_buffered and self._needs_local_block_replay(
                header.hash, height_hint=header.height
            ):
                recovered_missing_headers = True
        if not actionable:
            return []

        queued = self._enqueue_missing_blocks(actionable)
        if not recovered_missing_headers and queued <= 0:
            return []

        self._sync_headers_accepted_total += len(actionable)
        self._note_header_progress(peer, reason="headers_reused")
        now = time.time()
        peer.broadcast.successful_headers_served += 1
        peer.broadcast.last_head_advancement_at = now
        self._stats["peer_broadcast_good"] += 1
        self._sync_anchor_probe_hash = None
        self._sync_anchor_probe_peer = None
        self._sync_anchor_probe_until = 0.0
        self._sync_not_anchored_attempts = 0
        self._sync_recovery_attempts = 0
        self._sync_last_recovery_action = None
        for header in reused:
            self._sync_update_best_header(header)
        last_header = reused[-1]
        self._update_peer_head_table(
            peer,
            height=int(last_header.height),
            source="headers_reused",
            head_hash=last_header.hash,
        )
        if not peer.anchored:
            self._mark_peer_anchored(peer, reason="headers_reused")
        log.info(
            "Reused known headers for sync recovery",
            extra={
                "remote": peer.remote,
                "count": len(actionable),
                "best_header_height": self._sync_best_header.height
                if self._sync_best_header
                else None,
                "queued_blocks": queued,
            },
        )
        return actionable

    def _needs_local_block_replay(
        self, block_hash: bytes, *, height_hint: Optional[int]
    ) -> bool:
        if self._get_block_raw(block_hash) is None:
            return False
        if height_hint is None:
            meta = self._header_meta(block_hash)
            if meta is not None:
                height_hint = meta[0]
        if height_hint is None:
            return False
        local_height, _ = self._local_head()
        if int(height_hint) <= int(local_height or 0):
            return False
        return not self._header_on_local_chain(block_hash, height=int(height_hint))

    async def _try_import_local_block(self, block_hash: bytes) -> bool:
        raw_bytes = self._get_block_raw(block_hash)
        if raw_bytes is None:
            return False
        ok, reason = await self._import_block_payload(raw_bytes, origin_remote="local-db")
        if ok:
            log.info(
                "Replayed local block for sync",
                extra={
                    "hash": block_hash.hex(),
                    "height": self._block_height_hint(block_hash),
                },
            )
            return True
        if self._is_duplicate_reason(reason):
            self._drop_from_block_queue(block_hash)
            return True
        if self._is_orphan_reason(reason):
            return False
        log.warning(
            "Failed to replay local block for sync",
            extra={
                "hash": block_hash.hex(),
                "height": self._block_height_hint(block_hash),
                "reason": reason,
            },
        )
        return False

    def _enqueue_missing_blocks(self, headers: list[_SyncHeader]) -> int:
        if not headers:
            return 0
        local_height, _ = self._local_head()
        added = 0
        for hdr in sorted(headers, key=lambda h: h.height):
            if len(self._sync_block_queue) >= self._sync_block_queue_limit:
                log.warning(
                    "Block queue at capacity; deferring additional blocks",
                    extra={
                        "queue_limit": self._sync_block_queue_limit,
                        "queued_blocks": len(self._sync_block_queue),
                        "last_header_height": hdr.height,
                    },
                )
                break
            if hdr.height <= int(local_height or 0):
                # At/below-head headers are normally already ours — but a fork
                # SIBLING (same height, different hash, parent on our chain) is
                # the base of a competing branch and must be fetched or a node
                # on the losing side of a fork can never reorg (38728 wedge).
                if not self._is_fork_sibling_header(hdr):
                    continue
                # A sibling claim is cheap to fabricate (compact headers carry
                # no verifiable PoW at this layer), so cap how many DISTINCT
                # candidates per height we will fetch: junk blocks fail import
                # anyway, this just bounds the wasted downloads. A real fork
                # has one winning sibling; 4 leaves room for deeper races.
                h_key = int(hdr.height)
                if self._sibling_enqueue_counts.get(h_key, 0) >= 4:
                    continue
            if self._has_block(hdr.hash) and not self._needs_local_block_replay(
                hdr.hash, height_hint=hdr.height
            ):
                continue
            if (
                hdr.hash in self._sync_inflight_blocks
                or hdr.hash in self._sync_block_buffer
                or hdr.hash in self._sync_block_queue_set
            ):
                continue
            self._sync_block_queue.append(hdr.hash)
            self._sync_block_queue_set.add(hdr.hash)
            self._sync_block_queue_heights[hdr.hash] = hdr.height
            if hdr.height <= int(local_height or 0):
                h_key = int(hdr.height)
                self._sibling_enqueue_counts[h_key] = (
                    self._sibling_enqueue_counts.get(h_key, 0) + 1
                )
                if len(self._sibling_enqueue_counts) > 256:
                    floor = int(local_height or 0) - _max_reorg_depth()
                    for k in [
                        k for k in self._sibling_enqueue_counts if k < floor
                    ]:
                        del self._sibling_enqueue_counts[k]
            added += 1
        if added:
            self._sync_wakeup.set()
        return added

    def _drop_from_block_queue(self, block_hash: bytes) -> None:
        if block_hash not in self._sync_block_queue_set:
            return
        self._sync_block_queue_set.discard(block_hash)
        self._sync_block_queue_heights.pop(block_hash, None)
        self._sync_block_attempts_by_hash.pop(block_hash, None)
        self._sync_block_retry_counts.pop(block_hash, None)
        with contextlib.suppress(ValueError):
            self._sync_block_queue.remove(block_hash)

    async def _try_import_cached_block(self, block_hash: bytes) -> bool:
        if self._sync_cache is None:
            return False
        if self._should_skip_cache_block(block_hash):
            log.debug(
                "Skipping sync cache block after repeated failures",
                extra={"hash": block_hash.hex()},
            )
            return False
        raw_bytes = self._sync_cache.get_block(block_hash)
        if raw_bytes is None:
            self._stats["cache_misses"] += 1
            log.debug(
                "Sync cache miss",
                extra={"hash": block_hash.hex()},
            )
            return False
        self._stats["cache_hits"] += 1
        confirmed, confirmation_reason, confirmation_ctx = self._block_corroboration_status(
            block_hash, origin_remote=None
        )
        if not confirmed:
            eligible_peers = int(confirmation_ctx.get("eligible_peers") or 0)
            force_connected = bool(confirmation_ctx.get("force_connected"))
            bypass_corroboration = (
                eligible_peers <= 0
                or (
                    confirmation_reason == "await_force_peer_connection"
                    and not force_connected
                )
            )
            if bypass_corroboration:
                log.info(
                    "Bypassing cache block corroboration due to unavailable peers",
                    extra={
                        "hash": block_hash.hex(),
                        "reason": confirmation_reason,
                        "eligible_peers": eligible_peers,
                        "force_connected": force_connected,
                    },
                )
            else:
                log.debug(
                    "Skipping cache block pending corroboration",
                    extra={
                        "hash": block_hash.hex(),
                        "reason": confirmation_reason,
                        "votes": confirmation_ctx.get("votes"),
                        "required_votes": confirmation_ctx.get("required"),
                    },
                )
                return False
        ok, reason = await self._import_block_payload(
            raw_bytes, origin_remote="sync-cache"
        )
        if ok:
            return True
        if not self._is_orphan_reason(reason):
            self._sync_cache.invalidate_block(block_hash)
        events = self._sync_cache_failures.setdefault(block_hash, deque())
        now = time.time()
        window = self._sync_cache_failure_window_s
        while events and now - events[0] > window:
            events.popleft()
        events.append(now)
        self._sync_last_cache_error_at = now
        self._sync_last_cache_error_hash = block_hash
        self._sync_last_block_error = STALL_CACHE_SHORT_CIRCUIT
        self._sync_last_block_error_at = now
        return False

    def _ensure_block_queue(self) -> int:
        if self._sync_best_header is None:
            return 0
        local_height, _ = self._local_head()
        if self._sync_best_header.height <= int(local_height or 0):
            return 0
        headers = [
            h
            for h in self._sync_headers.values()
            if h.height > int(local_height or 0)
        ]
        headers.sort(key=lambda h: h.height)
        return self._enqueue_missing_blocks(headers)

    def _queued_blocks_count(self, best_block_height: Optional[int] = None) -> int:
        _ = best_block_height
        return len(self._sync_block_queue)

    def _block_height_hint(self, block_hash: bytes) -> Optional[int]:
        height_hint = self._sync_block_queue_heights.get(block_hash)
        if height_hint is not None:
            return height_hint
        if block_hash in self._sync_headers:
            return self._sync_headers[block_hash].height
        meta = self._header_meta(block_hash)
        if meta is not None:
            return meta[0]
        return None

    def _next_block_needed(self) -> tuple[Optional[int], Optional[bytes]]:
        if not self._sync_block_queue:
            local_height, _ = self._local_head()
            local_height_int = int(local_height or 0)
            best_header_height = (
                int(self._sync_best_header.height)
                if self._sync_best_header is not None
                else local_height_int
            )
            target_height = self._sync_target_height
            if target_height is None:
                target_height = self._network_best_height()
            if target_height is not None:
                best_header_height = max(best_header_height, int(target_height))
            if best_header_height > local_height_int:
                expected_next_height = local_height_int + 1
                # Fork recovery: if the peer's header chain diverged from ours
                # BELOW our head (matched ancestor < local head), we followed/mined
                # a losing branch. Requesting local_head+1 only ever yields an
                # orphan — its parent is the peer's block at our head's height,
                # which we don't have — so the node wedges at its fork tip forever
                # (the classic "stuck at the block I mined" after solo mining).
                # Instead, fetch the competing branch from the fork point so we
                # build the heavier side-chain and the fork choice reorgs onto it.
                anc = self._sync_last_matched_ancestor_height
                if anc is not None and int(anc) < local_height_int:
                    expected_next_height = int(anc) + 1
                next_hash = next(
                    (
                        h
                        for h, hdr in self._sync_headers.items()
                        if int(hdr.height) == expected_next_height
                    ),
                    None,
                )
                return expected_next_height, next_hash
            return None, None
        best_hash = None
        best_height = None
        for h in self._sync_block_queue:
            height_hint = self._block_height_hint(h)
            if best_height is None or (
                height_hint is not None and height_hint < best_height
            ):
                best_height = height_hint
                best_hash = h
        if best_hash is None:
            best_hash = self._sync_block_queue[0]
        return best_height, best_hash

    def _is_orphan_reason(self, reason: Optional[str]) -> bool:
        if not reason:
            return False
        lowered = str(reason).lower()
        return "missing parent" in lowered or "orphan" in lowered

    def _is_duplicate_reason(self, reason: Optional[str]) -> bool:
        if not reason:
            return False
        lowered = str(reason).lower()
        return "duplicate" in lowered or "already exists" in lowered

    def _buffer_orphan_block(self, sync_block: _SyncBlock) -> None:
        if sync_block.received_at <= 0:
            sync_block.received_at = time.time()
        self._sync_block_buffer[sync_block.hash] = sync_block
        while len(self._sync_block_buffer) > self._max_orphan_blocks:
            self._sync_block_buffer.popitem(last=False)

    def _prune_orphan_buffer(self) -> None:
        if not self._sync_block_buffer:
            return
        now = time.time()
        expired: list[tuple[bytes, _SyncBlock]] = []
        for h, blk in list(self._sync_block_buffer.items()):
            if blk.received_at <= 0:
                blk.received_at = now
            if now - blk.received_at > self._sync_orphan_ttl:
                expired.append((h, blk))
        if not expired:
            return
        for h, blk in expired:
            self._sync_block_buffer.pop(h, None)
            if not self._has_block(h) and h not in self._sync_block_queue_set:
                self._sync_block_queue.append(h)
                self._sync_block_queue_set.add(h)
                height_hint = self._block_height_hint(h)
                if height_hint is not None:
                    self._sync_block_queue_heights[h] = height_hint
        log.info(
            "Pruned orphan buffer entries",
            extra={"count": len(expired), "ttl_s": self._sync_orphan_ttl},
        )
        self._sync_wakeup.set()

    def _handle_missing_parent(self, peer: _PeerState, sync_block: _SyncBlock) -> None:
        now = time.time()
        self._stats["sync_missing_parent_recover"] += 1
        self._sync_missing_parent_recoveries += 1
        parent_header_known = False
        parent_block_known = False
        parent_inflight = False
        parent_queued = False
        parent_buffered = False
        if sync_block.parent_hash:
            parent_inflight = sync_block.parent_hash in self._sync_inflight_blocks
            parent_queued = sync_block.parent_hash in self._sync_block_queue_set
            parent_buffered = sync_block.parent_hash in self._sync_block_buffer
            parent_header_known = bool(
                self._has_header(sync_block.parent_hash)
                or sync_block.parent_hash in self._sync_headers
            )
            parent_block_known = bool(self._has_block(sync_block.parent_hash))
        benign_out_of_order = bool(
            sync_block.parent_hash
            and not parent_block_known
            and (parent_header_known or parent_inflight or parent_queued or parent_buffered)
        )
        if benign_out_of_order:
            if (
                self._sync_last_block_error == "missing parent"
                and self._sync_last_block_error_peer == peer.remote
            ):
                self._sync_last_block_error = None
                self._sync_last_block_error_at = None
                self._sync_last_block_error_peer = None
        else:
            peer.missing_parent += 1
            self._sync_last_block_error = "missing parent"
            self._sync_last_block_error_at = now
            self._sync_last_block_error_peer = peer.remote
            if peer.missing_parent >= self._missing_parent_threshold:
                self._sync_block_stalled_reason = "missing parent"
        if sync_block.parent_hash and not parent_block_known:
            if (
                sync_block.parent_hash not in self._sync_block_queue_set
                and sync_block.parent_hash not in self._sync_inflight_blocks
                and sync_block.parent_hash not in self._sync_block_buffer
            ):
                parent_height = None
                if sync_block.hash in self._sync_block_queue_heights:
                    parent_height = self._sync_block_queue_heights.get(sync_block.hash)
                    if parent_height is not None:
                        parent_height = parent_height - 1
                if parent_height is None:
                    meta = self._header_meta(sync_block.parent_hash)
                    if meta is not None:
                        parent_height = meta[0]
                if parent_height is None:
                    local_height, _ = self._local_head()
                    parent_height = int(local_height or 0) + 1
                self._sync_block_queue.appendleft(sync_block.parent_hash)
                self._sync_block_queue_set.add(sync_block.parent_hash)
                if parent_height is not None:
                    self._sync_block_queue_heights[sync_block.parent_hash] = parent_height
                self._sync_wakeup.set()
                log.info(
                    "Buffered orphan; requesting missing parent",
                    extra={
                        "remote": peer.remote,
                        "parent_hash": sync_block.parent_hash.hex(),
                        "parent_height": parent_height,
                    },
                )
        if (
            sync_block.parent_hash
            and not parent_header_known
            and not parent_inflight
            and not parent_queued
            and not parent_buffered
        ):
            anchor_height, anchor_hash_hex = self._local_head()
            anchor_hash = self._parse_hash_bytes(anchor_hash_hex)
            locator = self._build_headers_locator()
            if not locator:
                fallback = self._genesis_hash()
                if fallback:
                    locator = [fallback]
            self._enqueue_header_retry(
                peer=peer,
                locator=locator,
                locator_mode="missing_parent_header",
                anchor_height=int(anchor_height or 0),
                anchor_hash=anchor_hash,
                request_start_height=int(anchor_height or 0) + 1,
                max_headers=self._sync_headers_batch_current,
                reason="missing_parent_header",
            )
            self._sync_kick(reason="missing_parent_header", aggressive=True)
        if not benign_out_of_order:
            self._reset_sync_inflight(
                now=now,
                reason="missing_parent",
                expired_hash=sync_block.hash,
            )
        log.info(
            "Missing parent detected",
            extra={
                "remote": peer.remote,
                "block_hash": sync_block.hash.hex(),
                "parent_hash": sync_block.parent_hash.hex()
                if sync_block.parent_hash
                else None,
                "parent_header_known": parent_header_known,
                "parent_block_known": parent_block_known,
                "parent_inflight": parent_inflight,
                "parent_queued": parent_queued,
                "parent_buffered": parent_buffered,
                "benign_out_of_order": benign_out_of_order,
            },
        )
        if benign_out_of_order and self._sync_block_stalled_reason == "missing parent":
            self._sync_block_stalled_reason = None
        if (
            not benign_out_of_order
            and peer.missing_parent >= self._missing_parent_threshold
        ):
            self._penalize_peer(
                peer,
                "missing_parent",
                points=self._score_points["missing_parent"],
                severity=2,
                quarantine_s=30.0,
            )

    def _is_db_write_error(self, reason: Optional[str]) -> bool:
        if not reason:
            return False
        lowered = str(reason).lower()
        markers = (
            "permission",
            "eacces",
            "read-only",
            "read only",
            "readonly",
            "not writable",
            "lock",
            "locked",
            "io error",
            "rocksdb",
        )
        return any(token in lowered for token in markers)

    async def _drain_block_buffer(self) -> None:
        if not self._sync_block_buffer:
            return
        progressed = True
        while progressed:
            progressed = False
            for h, blk in list(self._sync_block_buffer.items()):
                if not self._has_block(blk.parent_hash):
                    continue
                confirmed, _confirmation_reason, _confirmation_ctx = (
                    self._block_corroboration_status(
                        h, origin_remote=blk.origin_peer
                    )
                )
                if not confirmed:
                    continue
                ok, reason = await self._import_block_payload(
                    blk.block, origin_remote=blk.origin_peer
                )
                if ok:
                    self._sync_block_buffer.pop(h, None)
                    progressed = True
                    self._sync_wakeup.set()
                    continue
                if not self._is_orphan_reason(reason):
                    self._sync_block_buffer.pop(h, None)
                    if blk.origin_peer:
                        reject_reason = reason or "block_rejected"
                        self._penalize_peer(
                            self._peer_by_remote(blk.origin_peer),
                            f"block_rejected:{reject_reason}",
                            severity=2,
                            quarantine_s=300.0,
                        )

    def _expire_inflight_blocks(self) -> None:
        if not self._sync_inflight_blocks:
            return
        now = time.time()
        timeout = max(1.0, self._sync_request_timeout)
        ttl = max(timeout, self._sync_inflight_ttl)
        expired = [
            h
            for h, request in list(self._sync_inflight_block_requests.items())
            if now >= request.deadline_at
        ]
        if len(expired) < len(self._sync_inflight_blocks):
            for h, started in list(self._sync_inflight_blocks.items()):
                if h in self._sync_inflight_block_requests:
                    continue
                if now - started >= ttl:
                    expired.append(h)
        for h in expired:
            self._sync_inflight_blocks.pop(h, None)
            peer_remote = self._sync_inflight_peers.pop(h, None)
            request = self._sync_inflight_block_requests.pop(h, None)
            retry_count = int(self._sync_block_retry_counts.get(h, 0))
            if request is not None:
                retry_count = max(retry_count, int(request.retry_count)) + 1
                request.retry_count = retry_count
            else:
                retry_count += 1
            self._sync_block_retry_counts[h] = retry_count
            if not self._has_block(h):
                if h not in self._sync_block_queue_set:
                    self._sync_block_queue.appendleft(h)
                    self._sync_block_queue_set.add(h)
                    if h not in self._sync_block_queue_heights:
                        self._sync_block_queue_heights[h] = -1
            else:
                self._sync_block_retry_counts.pop(h, None)
            if peer_remote:
                peer = self._peer_by_remote(peer_remote)
                if peer is not None:
                    self._record_block_failure(peer, reason="block_timeout")
                    self._set_block_backoff(peer, reason="block_timeout", delay=60.0)
                    peer.sync_timeouts += 1
                    peer.sync_failures += 1
                    peer.timeouts += 1
                    peer.score -= 50
                    if peer.timeouts >= 3:
                        self._ban_peer(peer, ban_ttl=600.0, reason="sync_timeouts")
                    if peer.score < -100:
                        self._ban_peer(peer, ban_ttl=600.0, reason="low_score")
                self._penalize_peer(peer, "block_timeout", nonfatal=True)
                if peer is not None:
                    self._mark_peer_head_issue(peer, reason="block_timeout")
                self._sync_last_block_error = STALL_BLOCK_TIMEOUT
                self._sync_last_block_error_at = now
                self._sync_last_block_error_peer = peer_remote
            if request is not None:
                self._stats["blocks_req_timeout"] += 1
                log.warning(
                    "Block request expired",
                    extra={
                        "request_id": request.request_id,
                        "peer": request.peer_id,
                        "kind": request.kind,
                        "age_s": round(now - request.started_at, 3),
                        "retry_count": retry_count,
                        "item_hash": request.item_hash.hex() if request.item_hash else None,
                        "start_height": request.start_height,
                    },
                )
                if retry_count >= self._sync_inflight_max_retries:
                    self._sync_block_retry_counts.pop(h, None)
                    self._reset_sync_inflight(
                        now=now,
                        reason="block_request_retries_exhausted",
                        expired_hash=h,
                    )
        if expired:
            self._sync_wakeup.set()
            self._sync_kick(reason="block_timeout", aggressive=False)

    def _reset_sync_inflight(
        self, *, now: float, reason: str, expired_hash: Optional[bytes] = None
    ) -> None:
        last_reset = self._sync_last_inflight_reset_at
        if last_reset and now - last_reset < 5.0:
            return
        reset_blocks = [
            h
            for h, started in list(self._sync_inflight_blocks.items())
            if now - started >= self._sync_inflight_ttl
        ]
        reset_headers = [
            key
            for key, req in list(self._sync_inflight_header_requests.items())
            if now - req.started_at >= self._sync_inflight_ttl
        ]
        if expired_hash is not None and expired_hash not in reset_blocks:
            reset_blocks.append(expired_hash)
        for h in reset_blocks:
            self._sync_inflight_blocks.pop(h, None)
            self._sync_inflight_peers.pop(h, None)
            self._sync_inflight_block_requests.pop(h, None)
            if not self._has_block(h) and h not in self._sync_block_queue_set:
                self._sync_block_queue.appendleft(h)
                self._sync_block_queue_set.add(h)
        for key in reset_headers:
            self._sync_inflight_header_requests.pop(key, None)
        if reset_headers:
            self._sync_inflight_headers = len(self._sync_inflight_header_requests)
        if reset_blocks or reset_headers:
            self._stats["sync_inflight_reset"] += 1
            self._sync_last_inflight_reset_at = now
            log.warning(
                "Sync inflight reset",
                extra={
                    "reason": reason,
                    "reset_blocks": len(reset_blocks),
                    "reset_headers": len(reset_headers),
                    "expired_hash": expired_hash.hex() if expired_hash else None,
                },
            )
            anchor_height, anchor_hash_hex = self._local_head()
            anchor_hash = self._parse_hash_bytes(anchor_hash_hex)
            locator = self._build_headers_locator()
            if not locator:
                fallback = self._genesis_hash()
                if fallback:
                    locator = [fallback]
            self._enqueue_header_retry(
                peer=None,
                locator=locator,
                locator_mode="sync_inflight_reset",
                anchor_height=int(anchor_height or 0),
                anchor_hash=anchor_hash,
                request_start_height=int(anchor_height or 0) + 1,
                max_headers=self._sync_headers_batch_current,
                reason="sync_inflight_reset",
            )
            self._sync_kick(reason="sync_inflight_reset", aggressive=True)

    def _handle_reorg(self, new_height: int, new_hash: Optional[str]) -> None:
        self._sync_last_reorg_at = time.time()
        self._stats["sync_reorg_applied"] += 1
        removed_headers = 0
        for h, header in list(self._sync_headers.items()):
            if header.height > new_height:
                self._sync_headers.pop(h, None)
                self._sync_header_sources.pop(h, None)
                removed_headers += 1
        if self._sync_best_header and self._sync_best_header.height > new_height:
            self._sync_best_header = None
            if self._sync_headers:
                self._sync_best_header = max(
                    self._sync_headers.values(), key=lambda hdr: hdr.height
                )
        removed_queue = 0
        for h in list(self._sync_block_queue):
            height_hint = self._sync_block_queue_heights.get(h)
            if height_hint is None and h in self._sync_headers:
                height_hint = self._sync_headers[h].height
            if height_hint is not None and height_hint > new_height:
                self._drop_from_block_queue(h)
                removed_queue += 1
        removed_buffer = 0
        for h, blk in list(self._sync_block_buffer.items()):
            height_hint = None
            if hasattr(blk.block, "header"):
                try:
                    height_hint = int(getattr(blk.block.header, "height", 0))
                except Exception:
                    height_hint = None
            if height_hint is not None and height_hint > new_height:
                self._sync_block_buffer.pop(h, None)
                removed_buffer += 1
        removed_cache = 0
        if self._sync_cache is not None:
            removed_cache = self._sync_cache.invalidate_from_height(new_height)
        log.info(
            "Reorg detected; cache invalidated",
            extra={
                "new_height": new_height,
                "new_hash": new_hash,
                "headers_removed": removed_headers,
                "queue_removed": removed_queue,
                "buffer_removed": removed_buffer,
                "cache_blocks_removed": removed_cache,
            },
        )
        self._refresh_locator_summary()

    def _not_anchored_delay(self, attempt: int) -> float:
        base = max(0.1, float(self._sync_not_anchored_backoff))
        exp = max(0, int(attempt) - 1)
        delay = base * (2 ** exp)
        return min(delay, float(self._sync_not_anchored_backoff_cap))

    def _note_not_anchored_probe(self, peer: _PeerState, *, reason: str) -> None:
        now = time.time()
        if now - peer.last_not_anchored_at > self._sync_not_anchored_window:
            peer.not_anchored_count = 0
        peer.not_anchored_count += 1
        peer.last_not_anchored_at = now
        peer.sync_failures += 1
        peer.anchored = False
        peer.anchor_reason = reason
        peer.last_anchor_at = now
        if now - self._sync_last_not_anchored_at > self._sync_not_anchored_window:
            self._sync_not_anchored_attempts = 0
        self._sync_not_anchored_attempts += 1
        self._sync_last_not_anchored_at = now
        self._sync_recovery_attempts += 1
        self._sync_last_header_error = "not_anchored"
        self._sync_last_header_error_at = now
        self._sync_last_header_error_peer = peer.remote
        cooldown = self._not_anchored_delay(peer.not_anchored_count)
        self._set_sync_backoff(peer, reason="not_anchored", delay=cooldown)
        peer.anchored = False
        peer.anchor_reason = reason
        peer.last_anchor_at = now
        self._sync_last_checkpoint_action = f"checkpoint_probe_{reason}"

    async def _request_headers_with_locator(
        self,
        peer: _PeerState,
        *,
        locator: list[bytes],
        max_headers: int,
        locator_mode: str,
        anchor_height: int,
        anchor_hash: Optional[bytes],
        request_start_height: int,
    ) -> Optional[List[HeaderCompact]]:
        max_headers_limit = max(1, int(self._max_headers_per_message or 0))
        if max_headers > max_headers_limit:
            log.debug(
                "Capping header request to message limit",
                extra={
                    "remote": peer.remote,
                    "requested": max_headers,
                    "limit": max_headers_limit,
                    "locator_mode": locator_mode,
                },
            )
            max_headers = max_headers_limit
        if self._sync_inflight_headers >= self._sync_max_inflight_headers:
            self._sync_last_header_error = "headers_inflight_maxed"
            self._sync_last_header_error_at = time.time()
            self._sync_last_header_error_peer = peer.remote
            log.debug(
                "Skipped getheaders: inflight limit reached",
                extra={
                    "remote": peer.remote,
                    "inflight_headers": int(self._sync_inflight_headers),
                    "max_inflight_headers": self._sync_max_inflight_headers,
                },
            )
            return None
        locator_info = self._locator_debug(locator)
        self._sync_last_locator_info = locator_info
        self._sync_last_locator_at = time.time()
        self._sync_last_locator_summary = self._locator_summary(locator_info)
        locator_start = locator_info[0] if locator_info else None
        locator_end = locator_info[-1] if locator_info else None
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        peer.pending_headers = fut
        request_id = self._register_header_request(
            peer,
            locator=locator,
            max_headers=max_headers,
            locator_mode=locator_mode,
            anchor_height=anchor_height,
            anchor_hash=anchor_hash,
            request_start_height=request_start_height,
        )
        self._stats["headers_req_sent"] += 1
        self._sync_last_header_request_at = time.time()
        peer.last_header_request_at = self._sync_last_header_request_at
        self._sync_active_header_peer = peer.remote
        self._sync_last_header_request_peer = peer.remote
        self._stats["peer_selected_for_headers"] += 1
        log.debug(
            "Sending getheaders",
            extra={
                "remote": peer.remote,
                "peer_id": peer.peer_id,
                "request_id": request_id,
                "local_head_height": self._sync_last_locator_head_height,
                "local_head_hash": self._sync_last_locator_head_hash.hex()
                if self._sync_last_locator_head_hash
                else None,
                "anchor_height": anchor_height,
                "anchor_hash": anchor_hash.hex() if anchor_hash else None,
                "request_start_height": request_start_height,
                "locator_mode": locator_mode,
                "locator": locator_info,
                "locator_start": locator_start,
                "locator_end": locator_end,
                "limit": max_headers,
            },
        )
        self._record_sync_header_event(
            {
                "type": "request",
                "peer": peer.remote,
                "peer_id": peer.peer_id,
                "request_id": request_id,
                "local_head_height": self._sync_last_locator_head_height,
                "local_head_hash": self._sync_last_locator_head_hash.hex()
                if self._sync_last_locator_head_hash
                else None,
                "anchor_height": anchor_height,
                "anchor_hash": anchor_hash.hex() if anchor_hash else None,
                "request_start_height": request_start_height,
                "locator_mode": locator_mode,
                "locator": locator_info,
                "locator_start": locator_start,
                "locator_end": locator_end,
                "limit": max_headers,
            }
        )
        try:
            await self._send(
                peer,
                MsgID.GET_HEADERS,
                GetHeaders(locator=locator, max_headers=max_headers),
            )
        except Exception:
            peer.pending_headers = None
            self._clear_header_request(peer)
            self._sync_last_header_error = "headers_send_failed"
            self._sync_last_header_error_at = time.time()
            self._sync_last_header_error_peer = peer.remote
            self._penalize_peer(peer, "headers_send_failed", nonfatal=True)
            peer.broadcast.errors += 1
            self._set_sync_backoff(
                peer,
                reason="headers_send_failed",
                delay=self._sync_no_headers_backoff,
            )
            self._record_sync_header_event(
                {
                    "type": "response",
                    "peer": peer.remote,
                    "peer_id": peer.peer_id,
                    "request_id": request_id,
                    "count": 0,
                    "error": "headers_send_failed",
                }
            )
            return None

        try:
            headers_msg: Optional[Headers] = await asyncio.wait_for(
                fut, timeout=self._sync_request_timeout
            )
            if headers_msg is None:
                raise asyncio.TimeoutError()
        except Exception:
            peer.pending_headers = None
            self._clear_header_request(peer)
            self._sync_last_header_error = "headers_timeout"
            self._sync_last_header_error_at = time.time()
            self._sync_last_header_error_peer = peer.remote
            self._stats["headers_req_timeout"] += 1
            self._adjust_header_batch(success=False, reason="headers_timeout")
            self._penalize_peer(peer, "headers_timeout", nonfatal=True)
            peer.broadcast.timeouts += 1
            self._record_sync_header_event(
                {
                    "type": "response",
                    "peer": peer.remote,
                    "peer_id": peer.peer_id,
                    "request_id": request_id,
                    "count": 0,
                    "error": "headers_timeout",
                }
            )
            return None
        finally:
            if peer.pending_headers is fut:
                peer.pending_headers = None
            self._clear_header_request(peer)
            self._sync_active_header_peer = None
        self._sync_last_header_response_at = time.time()
        self._sync_last_header_response_peer = peer.remote
        self._sync_last_header_response_count = len(headers_msg.headers)
        self._sync_last_header_error = None
        self._sync_last_header_error_at = None
        self._sync_last_header_error_peer = None
        info = self._headers_debug_info(list(headers_msg.headers))
        log.debug(
            "Received headers response",
            extra={
                "remote": peer.remote,
                "peer_id": peer.peer_id,
                "request_id": request_id,
                **info,
            },
        )
        self._record_sync_header_event(
            {
                "type": "response",
                "peer": peer.remote,
                "peer_id": peer.peer_id,
                "request_id": request_id,
                **info,
            }
        )
        if headers_msg.headers:
            self._stats["headers_req_ok"] += 1
            self._adjust_header_batch(success=True, reason="headers_ok")
        else:
            self._stats["headers_req_empty"] += 1
        return list(headers_msg.headers)

    async def _checkpoint_anchor_probe(
        self,
        peer: _PeerState,
        *,
        anchor_height: int,
        anchor_hash: Optional[bytes],
    ) -> bool:
        if not self._should_enforce_checkpoint_anchor():
            return True
        if self._sync_checkpoint_hash is None or self._sync_checkpoint_height is None:
            return True
        if peer.anchored:
            return True
        locator = [self._sync_checkpoint_hash]
        probe_headers = await self._request_headers_with_locator(
            peer,
            locator=locator,
            max_headers=1,
            locator_mode="checkpoint_probe",
            anchor_height=anchor_height,
            anchor_hash=anchor_hash,
            request_start_height=int(self._sync_checkpoint_height),
        )
        if probe_headers is None:
            return True
        if not probe_headers:
            self._note_not_anchored_probe(peer, reason="checkpoint_empty")
            return True
        probe_header = self._header_from_compact(probe_headers[0])
        if (
            probe_header.parent_hash != self._sync_checkpoint_hash
            or probe_header.height != int(self._sync_checkpoint_height) + 1
        ):
            self._note_not_anchored_probe(peer, reason="checkpoint_mismatch")
            return True
        self._mark_peer_anchored(peer, reason="checkpoint_verified")
        return True

    async def _fetch_headers(self, peer: _PeerState) -> Optional[List[HeaderCompact]]:
        local_height, local_hash_hex = self._local_head()
        local_hash = self._parse_hash_bytes(local_hash_hex)
        anchor_height = int(local_height or 0)
        anchor_hash = local_hash
        request_start_height = anchor_height + 1 if anchor_hash else anchor_height
        if not await self._checkpoint_anchor_probe(
            peer, anchor_height=anchor_height, anchor_hash=anchor_hash
        ):
            return None
        if self._sync_header_retry_queue:
            retry = self._sync_header_retry_queue.popleft()
            target_peer = self._peer_by_remote(retry.peer_id) or peer
            if not target_peer.hello_done.is_set():
                target_peer = peer
            if not self._peer_is_sync_eligible(target_peer):
                retry.retry_count += 1
                self._sync_header_retry_queue.append(retry)
            else:
                retry_locator = retry.locator or []
                if not retry_locator:
                    fallback = self._genesis_hash()
                    if fallback:
                        retry_locator = [fallback]
                log.info(
                    "Retrying header request",
                    extra={
                        "peer": target_peer.remote,
                        "request_id": retry.request_id,
                        "retry_count": retry.retry_count,
                        "start_height": retry.start_height,
                        "count": retry.count,
                        "locator_mode": retry.locator_mode,
                    },
                )
                return await self._request_headers_with_locator(
                    target_peer,
                    locator=retry_locator,
                    max_headers=retry.count or self._sync_headers_batch_current,
                    locator_mode=retry.locator_mode or "retry",
                    anchor_height=retry.anchor_height or anchor_height,
                    anchor_hash=retry.anchor_hash,
                    request_start_height=retry.start_height or request_start_height,
                )
        locator, max_headers, locator_mode = self._select_header_locator(peer)
        if not locator:
            fallback = self._genesis_hash()
            if fallback:
                log.error("Empty header locator generated; falling back to genesis")
                locator = [fallback]
            else:
                log.error("Empty header locator generated without fallback genesis")
        return await self._request_headers_with_locator(
            peer,
            locator=locator,
            max_headers=max_headers,
            locator_mode=locator_mode,
            anchor_height=anchor_height,
            anchor_hash=anchor_hash,
            request_start_height=request_start_height,
        )

    def _process_headers(
        self, peer: _PeerState, headers: List[HeaderCompact]
    ) -> tuple[List[bytes], Optional[str], Dict[str, int]]:
        if not headers:
            return [], None, {}
        peer.empty_header_responses = 0
        self._sync_headers_seen_total += len(headers)
        local_height, local_hash_hex = self._local_head()
        local_hash: Optional[bytes] = None
        if local_hash_hex:
            local_hash = self._parse_hash_bytes(local_hash_hex)
        local_anchor_height = int(local_height or 0)
        local_anchor_hash = local_hash
        headers, trimmed_overlap = self._trim_leading_canonical_overlap(
            peer, headers, local_height=local_anchor_height
        )
        if not headers:
            if trimmed_overlap > 0:
                batch_limit = max(1, int(self._max_headers_per_message or 0))
                overlap_filled_batch = trimmed_overlap >= batch_limit
                if overlap_filled_batch:
                    # The peer FILLED the response with headers we already have:
                    # it provably has at least a full window more chain than the
                    # window we asked about, yet we accepted nothing. This is
                    # the 28167/38728 wedge signature (a node parked on the
                    # losing side of a fork re-receiving its own prefix), not
                    # benign tip idling — do NOT reset the stall clock, or the
                    # watchdog/recovery paths can never fire.
                    self._sync_overlap_full_batches += 1
                    log.warning(
                        "Full-window overlap-only headers batch; not counting as progress",
                        extra={
                            "remote": peer.remote,
                            "local_height": local_anchor_height,
                            "trimmed_overlap": trimmed_overlap,
                            "consecutive_full_overlap": self._sync_overlap_full_batches,
                            "network_best_height": self._network_best_height(),
                        },
                    )
                elif not self._is_sync_target_ahead(local_anchor_height):
                    self._sync_overlap_full_batches = 0
                    self._note_header_progress(peer, reason="headers_overlap")
                else:
                    log.info(
                        "Overlap-only headers while behind target; not counting as progress",
                        extra={
                            "remote": peer.remote,
                            "local_height": local_anchor_height,
                            "trimmed_overlap": trimmed_overlap,
                            "target_height": self._sync_target_height,
                            "network_best_height": self._network_best_height(),
                        },
                    )
                if not peer.anchored:
                    self._mark_peer_anchored(peer, reason="headers_overlap")
                return [], None, {"overlap_headers": trimmed_overlap}
            return [], None, {}
        anchor_height = int(local_height or 0)
        anchor_hash = local_hash
        anchor_source = "local_head"
        first_header = self._header_from_compact(headers[0])
        anchor_candidates = self._anchor_candidates()
        prev_meta = self._header_meta(first_header.parent_hash)
        anchor_from_candidates = False
        if prev_meta is None:
            candidate = anchor_candidates.get(first_header.parent_hash)
            if candidate is not None:
                anchor_height, _source = candidate
                anchor_hash = first_header.parent_hash
                anchor_source = _source
                prev_meta = (anchor_height, 0)
                anchor_from_candidates = True
        if prev_meta is not None:
            anchor_height = int(prev_meta[0])
            anchor_hash = first_header.parent_hash
            if not anchor_from_candidates:
                anchor_source = "prev_hash"
            self._update_matched_ancestor(
                anchor_height, anchor_hash, source="prev_hash"
            )
        self._sync_last_anchor_check = {
            "prev_hash": self._canon_hash0x(first_header.parent_hash),
            "anchor_hash": self._canon_hash0x(anchor_hash),
            "anchor_height": anchor_height,
            "anchor_source": anchor_source,
            "prev_hash_known": prev_meta is not None,
            "anchor_candidates": self._anchor_candidates_summary(),
        }
        prev_known = prev_meta is not None
        if self._should_enforce_checkpoint_anchor() and not self._peer_is_anchored(peer):
            order, reason = self._note_not_anchored(
                peer,
                header=first_header,
                anchor_height=anchor_height,
                anchor_hash=anchor_hash,
                reason="checkpoint_unverified",
            )
            return order, reason, {"checkpoint_unverified": len(headers)}
        if headers and anchor_hash is not None:
            if first_header.height == anchor_height and first_header.hash == anchor_hash:
                log.info(
                    "Trimming inclusive anchor header",
                    extra={
                        "remote": peer.remote,
                        "anchor_height": anchor_height,
                        "anchor_hash": anchor_hash.hex(),
                    },
                )
                headers = headers[1:]
                if not headers:
                    return [], None, {}

        contiguous: List[_SyncHeader] = []
        prev: Optional[_SyncHeader] = None
        seen_hashes: set[bytes] = set()
        expected_genesis = self._genesis_header_hash()
        expected_genesis_block = self._genesis_block_hash()
        discard_reason_counts: Dict[str, int] = {}
        abort_reason: Optional[str] = None
        abort_index: Optional[int] = None
        parent_ts: Optional[int] = None

        for idx, hc in enumerate(headers):
            header = self._header_from_compact(hc)
            if header.hash in seen_hashes:
                discard_reason_counts["duplicate_headers"] = (
                    discard_reason_counts.get("duplicate_headers", 0) + 1
                )
                continue
            seen_hashes.add(header.hash)
            if header.theta_micro < 0 or header.theta_micro > 10**12:
                abort_reason = "header_theta_out_of_range"
                abort_index = idx
                self._penalize_peer(peer, "header_theta_out_of_range")
                self._log_header_reject(peer, header, reason=abort_reason, parent_ts=parent_ts)
                break
            if idx == 0:
                if (
                    local_anchor_hash is not None
                    and local_anchor_height > 0
                    and header.height <= local_anchor_height
                    and not (
                        header.height == local_anchor_height
                        and header.hash == local_anchor_hash
                    )
                ):
                    if self._is_fork_sibling_header(header):
                        # The batch starts with the WINNING sibling of a fork we
                        # took the losing side of. Discarding it here (the
                        # legacy anchor_mismatch path) is what wedged nodes at
                        # 38728 for days: the competing branch base could never
                        # reach fork choice, so the reorg never happened. Let
                        # the batch flow — the parent-anchor checks below
                        # validate it against its (canonical) parent — and make
                        # sure its block gets fetched even if the header was
                        # already buffered.
                        log.warning(
                            "Fork sibling at/below local head — ingesting competing branch",
                            extra={
                                "remote": peer.remote,
                                "height": int(header.height),
                                "sibling_hash": header.hash.hex(),
                                "local_height": local_anchor_height,
                            },
                        )
                        self._enqueue_missing_blocks([header])
                    else:
                        order, reason = self._note_not_anchored(
                            peer,
                            header=header,
                            anchor_height=local_anchor_height,
                            anchor_hash=local_anchor_hash,
                            reason="anchor_mismatch",
                            allow_probe=True,
                        )
                        return order, reason, {"anchor_mismatch": len(headers)}
                if header.height == 0:
                    if header.hash != expected_genesis:
                        self._stats["p2p_peers_rejected_genesis_mismatch"] += 1
                        self._penalize_peer(peer, "genesis_mismatch", severity=2)
                        self._log_header_reject(peer, header, reason="genesis_mismatch")
                        return [], "genesis_mismatch", {"genesis_mismatch": len(headers)}
                else:
                    if anchor_hash is not None and header.height <= anchor_height:
                        if not (
                            header.height == anchor_height and header.hash == anchor_hash
                        ):
                            order, reason = self._note_not_anchored(
                                peer,
                                header=header,
                                anchor_height=anchor_height,
                                anchor_hash=anchor_hash,
                                reason="anchor_mismatch",
                                allow_probe=True,
                            )
                            return order, reason, {"anchor_mismatch": len(headers)}
                    if (
                        anchor_hash is not None
                        and header.height == anchor_height + 1
                        and header.parent_hash != anchor_hash
                    ):
                        if anchor_height == 0 and header.parent_hash in {
                            expected_genesis,
                            expected_genesis_block,
                        }:
                            pass
                        else:
                            order, reason = self._note_not_anchored(
                                peer,
                                header=header,
                                anchor_height=anchor_height,
                                anchor_hash=anchor_hash,
                                reason="anchor_parent_mismatch",
                                allow_probe=True,
                            )
                            return order, reason, {"anchor_parent_mismatch": len(headers)}
                    if header.height == 1 and header.parent_hash not in {
                        expected_genesis,
                        expected_genesis_block,
                    }:
                        self._stats["p2p_peers_rejected_genesis_mismatch"] += 1
                        self._penalize_peer(peer, "genesis_mismatch", severity=2)
                        self._log_header_reject(peer, header, reason="genesis_mismatch")
                        return [], "genesis_mismatch", {"genesis_mismatch": len(headers)}
                    if (
                        header.height == 1
                        and header.parent_hash
                        in {
                            expected_genesis,
                            expected_genesis_block,
                        }
                    ):
                        parent_height = 0
                        parent_ts = None
                    elif (
                        self._checkpoint_parent_meta(header.parent_hash) is None
                        and not self._has_header(header.parent_hash)
                        and header.parent_hash not in self._sync_headers
                        and header.parent_hash not in anchor_candidates
                    ):
                        order, reason = self._note_not_anchored(
                            peer,
                            header=header,
                            anchor_height=anchor_height,
                            anchor_hash=anchor_hash,
                            reason="parent_unknown",
                            allow_probe=True,
                        )
                        return order, reason, {"parent_unknown": len(headers)}
                    if header.height == 1 and header.parent_hash in {
                        expected_genesis,
                        expected_genesis_block,
                    }:
                        parent_height = 0
                        parent_ts = None
                    else:
                        checkpoint_parent = self._checkpoint_parent_meta(header.parent_hash)
                        if checkpoint_parent is not None:
                            parent_height, parent_ts = checkpoint_parent
                        else:
                            parent_info = self._header_meta(header.parent_hash)
                            if parent_info is None:
                                candidate = anchor_candidates.get(header.parent_hash)
                                if candidate is None:
                                    order, reason = self._note_not_anchored(
                                        peer,
                                        header=header,
                                        anchor_height=anchor_height,
                                        anchor_hash=anchor_hash,
                                        reason="parent_meta_missing",
                                        allow_probe=True,
                                    )
                                    return order, reason, {"parent_meta_missing": len(headers)}
                                parent_height, _source = candidate
                                parent_ts = None
                            else:
                                parent_height, parent_ts = parent_info
                    if header.height != parent_height + 1:
                        abort_reason = "header_height_mismatch"
                        abort_index = idx
                        self._penalize_peer(peer, "header_height_mismatch")
                        self._log_header_reject(peer, header, reason=abort_reason, parent_ts=parent_ts)
                        break
                    if parent_ts and header.timestamp < parent_ts:
                        abort_reason = "header_timestamp_regress"
                        abort_index = idx
                        self._penalize_peer(peer, "header_timestamp_regress")
                        self._log_header_reject(peer, header, reason=abort_reason, parent_ts=parent_ts)
                        break
            else:
                if prev is None:
                    abort_reason = "header_prev_missing"
                    abort_index = idx
                    self._log_header_reject(peer, header, reason=abort_reason)
                    break
                if header.parent_hash != prev.hash:
                    abort_reason = "header_parent_mismatch"
                    abort_index = idx
                    self._penalize_peer(peer, "header_parent_mismatch")
                    self._log_header_reject(peer, header, reason=abort_reason, parent_ts=prev.timestamp)
                    break
                if header.height != prev.height + 1:
                    abort_reason = "header_height_gap"
                    abort_index = idx
                    self._penalize_peer(peer, "header_height_gap")
                    self._log_header_reject(peer, header, reason=abort_reason, parent_ts=prev.timestamp)
                    break
                if header.timestamp < prev.timestamp:
                    abort_reason = "header_timestamp_regress"
                    abort_index = idx
                    self._penalize_peer(peer, "header_timestamp_regress")
                    self._log_header_reject(peer, header, reason=abort_reason, parent_ts=prev.timestamp)
                    break

            self._record_header_vote(header.hash, peer.remote)
            if header.hash not in self._sync_headers and not self._has_header(header.hash):
                self._sync_headers[header.hash] = header
                self._sync_header_sources[header.hash] = peer.remote
                contiguous.append(header)
            prev = header

        if abort_reason is not None:
            discard_reason_counts[abort_reason] = (
                discard_reason_counts.get(abort_reason, 0) + 1
            )
            if abort_index is not None:
                remaining = len(headers) - (abort_index + 1)
                if remaining > 0:
                    discard_reason_counts["skipped_after_reject"] = (
                        discard_reason_counts.get("skipped_after_reject", 0) + remaining
                    )

        if not contiguous:
            all_known = all(
                self._has_header(bytes(h.hash))
                or bytes(h.hash) in self._sync_headers
                for h in headers
            )
            if discard_reason_counts:
                peer.broadcast.errors += 1
                return [], "invalid_headers", discard_reason_counts
            if all_known:
                reused = self._reuse_known_headers(peer, headers)
                if reused:
                    return [h.hash for h in reused], None, {}
                peer.broadcast.duplicate_header_batches += 1
                self._stats["peer_duplicate_header_batches"] += 1
                if self._is_sync_target_ahead(local_anchor_height):
                    log.info(
                        "Duplicate-only headers while behind target; not counting as progress",
                        extra={
                            "remote": peer.remote,
                            "local_height": local_anchor_height,
                            "count": len(headers),
                            "target_height": self._sync_target_height,
                            "network_best_height": self._network_best_height(),
                        },
                    )
                else:
                    self._note_header_progress(peer, reason="duplicate_headers")
                return [], None, {"duplicate_headers": len(headers)}
            peer.broadcast.errors += 1
            return [], "invalid_headers", {"invalid_headers": len(headers)}

        self._sync_headers_accepted_total += len(contiguous)
        log.info(
            "HEADER_ACCEPTED",
            extra={
                "count": len(contiguous),
                "first": int(contiguous[0].height) if contiguous else None,
                "last": int(contiguous[-1].height) if contiguous else None,
                "peer": peer.remote,
            },
        )
        self._note_header_progress(peer, reason="headers_accepted")
        now = time.time()
        peer.broadcast.successful_headers_served += 1
        peer.broadcast.last_head_advancement_at = now
        self._stats["peer_broadcast_good"] += 1
        self._sync_anchor_probe_hash = None
        self._sync_anchor_probe_peer = None
        self._sync_anchor_probe_until = 0.0
        self._sync_not_anchored_attempts = 0
        self._sync_recovery_attempts = 0
        self._sync_last_recovery_action = None
        for h in contiguous:
            self._sync_update_best_header(h)
        last_header = contiguous[-1] if contiguous else None
        if last_header is not None:
            self._update_peer_head_table(
                peer,
                height=int(last_header.height),
                source="headers",
                head_hash=last_header.hash,
            )
            peer_head_hash = bytes((peer.hello or {}).get("head_hash") or b"")
            peer_head_height = int((peer.hello or {}).get("head_height") or 0)
            if peer_head_hash and peer_head_hash == last_header.hash:
                peer.broadcast.tip_matches += 1
            elif peer_head_height and peer_head_height <= int(last_header.height):
                peer.broadcast.tip_matches += 1
        if not peer.anchored:
            self._mark_peer_anchored(peer, reason="headers_accepted")
        log.info(
            "Header batch accepted",
            extra={
                "remote": peer.remote,
                "count": len(contiguous),
                "best_header_height": self._sync_best_header.height
                if self._sync_best_header
                else None,
            },
        )
        self._sync_locator_depth_hint = 0
        self._update_checkpoint_validation(contiguous)
        queued = self._enqueue_missing_blocks(contiguous)
        if queued:
            log.info(
                "Blocks queued from headers",
                extra={"remote": peer.remote, "count": queued},
            )
        return [h.hash for h in contiguous], None, discard_reason_counts

    def _note_header_progress(self, peer: _PeerState, *, reason: str) -> None:
        now = time.time()
        self._sync_last_header_at = now
        self._sync_last_header_response_at = now
        self._sync_last_progress_at = now
        peer.last_progress_at = now
        peer.empty_header_responses = 0
        peer.header_cooldown_until = 0.0
        peer.sync_successes += 1
        header_height = self._sync_best_header.height if self._sync_best_header else 0
        self._note_sync_progress(
            reason=f"headers:{reason}",
            header_height=header_height,
        )
        log.debug(
            "Header progress noted",
            extra={"remote": peer.remote, "reason": reason},
        )

    def _update_matched_ancestor(
        self,
        height: int,
        anchor_hash: Optional[bytes],
        *,
        source: str,
    ) -> None:
        if anchor_hash is None:
            return
        head_height, head_hash = self._local_head()
        head_height_int = int(head_height or 0)
        now = time.time()
        if height > head_height_int:
            log.warning(
                "LOCATOR_BUG: matched ancestor beyond head",
                extra={
                    "anchor_height": height,
                    "anchor_hash": self._canon_hash0x(anchor_hash),
                    "head_height": head_height_int,
                    "head_hash": head_hash,
                    "source": source,
                },
            )
            return
        previous_height = self._sync_last_matched_ancestor_height
        if (
            previous_height is not None
            and height < previous_height - self._sync_locator_backtrack_threshold
            and (
                self._sync_last_reorg_at is None
                or now - self._sync_last_reorg_at > self._sync_stall_timeout
            )
        ):
            log.warning(
                "LOCATOR_BUG: matched ancestor jumped backwards",
                extra={
                    "anchor_height": height,
                    "anchor_hash": self._canon_hash0x(anchor_hash),
                    "previous_height": previous_height,
                    "previous_hash": self._canon_hash0x(self._sync_last_matched_ancestor_hash),
                    "head_height": head_height_int,
                    "head_hash": head_hash,
                    "source": source,
                },
            )
            return
        self._sync_last_matched_ancestor_height = int(height)
        self._sync_last_matched_ancestor_hash = bytes(anchor_hash)

    async def _queue_block_requests(
        self, peer: _PeerState, hashes: List[bytes]
    ) -> int:
        if not hashes:
            return 0

        requested: List[bytes] = []
        inflight_for_peer = sum(
            1 for remote in self._sync_inflight_peers.values() if remote == peer.remote
        )
        best_fetch_height = None
        for h in hashes:
            if (
                self._has_block(h)
                or h in self._sync_inflight_blocks
                or h in self._sync_block_buffer
            ):
                continue
            if len(self._sync_inflight_blocks) >= self._sync_max_inflight:
                break
            if inflight_for_peer >= self._sync_max_inflight_per_peer:
                break
            started_at = time.time()
            deadline = started_at + max(1.0, self._sync_request_timeout)
            request = _SyncRequest(
                request_id=uuid.uuid4().hex,
                peer_id=peer.remote,
                kind="blocks",
                started_at=started_at,
                deadline_at=deadline,
                retry_count=int(self._sync_block_retry_counts.get(h, 0)),
                item_hash=h,
                start_height=None,
                count=1,
            )
            self._sync_inflight_blocks[h] = started_at
            self._sync_inflight_peers[h] = peer.remote
            self._sync_inflight_block_requests[h] = request
            requested.append(h)
            self._record_block_attempt(peer, h)
            log.info(
                "BLOCK_SCHEDULED",
                extra={
                    "height": self._block_height_hint(h),
                    "hash": self._canon_hash0x(h),
                    "peer": peer.remote,
                },
            )
            inflight_for_peer += 1
            height_hint = self._block_height_hint(h)
            if height_hint is not None:
                request.start_height = int(height_hint)
                if best_fetch_height is None or height_hint > best_fetch_height:
                    best_fetch_height = height_hint

        if not requested:
            return 0

        # Chunk requests to keep payloads small.
        chunk_size = max(self._sync_block_chunk_size_min, self._sync_block_chunk_size_current)
        for i in range(0, len(requested), chunk_size):
            chunk = requested[i : i + chunk_size]
            with contextlib.suppress(Exception):
                self._sync_last_block_request_at = time.time()
                peer.last_block_request_at = self._sync_last_block_request_at
                self._sync_active_block_peer = peer.remote
                await self._send(
                    peer,
                    MsgID.GET_BLOCKS,
                    GetBlocks(by_hash=chunk, max_blocks=len(chunk)),
                )
            await asyncio.sleep(0)
        self._stats["blocks_requested"] += len(requested)
        self._stats["blocks_req_sent"] += len(requested)
        if best_fetch_height is not None:
            self._note_sync_progress(
                reason="blocks_requested",
                block_fetch_height=best_fetch_height,
            )
        log.info(
            "Blocks requested",
            extra={"remote": peer.remote, "count": len(requested)},
        )
        return len(requested)

    async def _schedule_block_requests(
        self, peer: Optional[_PeerState] = None
    ) -> int:
        if len(self._sync_block_buffer) >= self._max_orphan_blocks:
            self._sync_block_stalled_reason = STALL_VERIFY_BACKPRESSURE
            self._sync_last_block_error = STALL_VERIFY_BACKPRESSURE
            self._sync_last_block_error_at = time.time()
            log.warning(
                "Block download backpressure: verify queue full",
                extra={
                    "buffer_size": len(self._sync_block_buffer),
                    "buffer_limit": self._max_orphan_blocks,
                },
            )
            return 0
        if not self._sync_block_queue:
            seeded = 0
            if self._sync_best_header is not None:
                local_height, _ = self._local_head()
                if self._sync_best_header.height > int(local_height or 0):
                    seeded = self._ensure_block_queue()
                    if seeded:
                        log.info(
                            "Seeded block queue from headers",
                            extra={
                                "count": seeded,
                                "best_header_height": self._sync_best_header.height,
                                "local_height": int(local_height or 0),
                            },
                        )
            if not self._sync_block_queue:
                now = time.time()
                local_height, _ = self._local_head()
                if (
                    self._sync_best_header is not None
                    and self._sync_best_header.height > int(local_height or 0)
                    and now - self._sync_block_queue_empty_log_at
                    >= self._sync_block_queue_empty_log_interval
                ):
                    self._sync_block_queue_empty_log_at = now
                    log.warning(
                        "Block queue empty while headers ahead",
                        extra={
                            "local_height": int(local_height or 0),
                            "best_header_height": self._sync_best_header.height,
                            "queued_blocks": len(self._sync_block_queue),
                            "inflight_blocks": len(self._sync_inflight_blocks),
                            "headers_buffered": len(self._sync_headers),
                            "block_buffered": len(self._sync_block_buffer),
                            "last_block_error": self._sync_last_block_error,
                            "stall_reason": self._sync_block_stalled_reason,
                        },
                    )
                log.debug(
                    "Skipped block requests: block queue empty",
                    extra={"seeded": seeded},
                )
                return 0
        next_block_height, next_block_hash = self._next_block_needed()
        attempted_peers: set[str] = set()
        if next_block_hash is not None:
            attempted_peers = set(
                self._sync_block_attempts_by_hash.get(next_block_hash, deque())
            )
        require_anchored = self._should_enforce_checkpoint_anchor()
        required_height = int(next_block_height or 1)
        selected_peers: list[_PeerState] = []
        selected_reason = "eligible"
        now = time.time()

        def _is_usable_block_peer(candidate: _PeerState) -> bool:
            if not candidate.hello_done.is_set():
                return False
            if require_anchored and not self._peer_is_anchored(candidate):
                return False
            return self._peer_sync_head_height(candidate, now=now) >= required_height

        if peer is None:
            eligible_peers, _ = self._eligible_block_peers()
            eligible_peers = [p for p in eligible_peers if _is_usable_block_peer(p)]
            preferred = [p for p in eligible_peers if p.remote not in attempted_peers]
            if not preferred and attempted_peers:
                preferred = eligible_peers
            if not preferred:
                selected_reason = "no_height_eligible_peer"

            def _peer_score(p: _PeerState) -> tuple[int, float, int]:
                events = self._sync_metrics_peer_block_events.get(p.remote, deque())
                rate = self._metric_rate(events, now)
                inflight = sum(
                    1 for remote in self._sync_inflight_peers.values() if remote == p.remote
                )
                head_height = self._peer_sync_head_height(p, now=now)
                trusted = 1 if self._is_trusted_peer(p) else 0
                return (trusted, head_height, rate, -inflight)

            preferred.sort(key=_peer_score, reverse=True)
            selected_peers = preferred[: max(1, self._sync_max_parallel_peers)]
        else:
            if _is_usable_block_peer(peer):
                selected_peers = [peer]
            elif not peer.hello_done.is_set():
                selected_reason = "handshake_pending"
            elif require_anchored and not self._peer_is_anchored(peer):
                selected_reason = "anchor_required"
            else:
                selected_reason = "peer_below_required_height"

        if not selected_peers:
            eligible_block_peers, ineligible_block_reasons = self._eligible_block_peers()
            log.info(
                "BLOCK_FETCH_NOT_SCHEDULED",
                extra={
                    "reason": selected_reason if peer is not None else selected_reason,
                    "needed_height": next_block_height,
                    "needed_hash": self._canon_hash0x(next_block_hash),
                    "eligible_peers": [p.remote for p in eligible_block_peers],
                    "ineligible_reasons": dict(ineligible_block_reasons),
                    "queue_len": len(self._sync_block_queue),
                    "inflight_blocks": len(self._sync_inflight_blocks),
                },
            )
            return 0
        self._sync_active_block_peer = selected_peers[0].remote
        log.debug(
            "Selected sync peer for blocks",
            extra=self._sync_peer_log_context(selected_peers[0]),
        )
        self._stats["peer_selected_for_blocks"] += 1
        local_height, _ = self._local_head()
        expected_height = int(local_height or 0) + 1
        best_header_height = (
            self._sync_best_header.height if self._sync_best_header else expected_height
        )
        target_height = min(
            best_header_height, expected_height + max(1, self._sync_max_inflight) - 1
        )
        if self._sync_target_height is not None:
            target_height = min(target_height, int(self._sync_target_height))
        parent_requests = {
            blk.parent_hash for blk in self._sync_block_buffer.values() if blk.parent_hash
        }
        queued = list(self._sync_block_queue)
        self._sync_block_queue.clear()
        ordered = sorted(
            queued,
            key=lambda h: (
                self._sync_block_queue_heights.get(h)
                or (self._sync_headers.get(h).height if h in self._sync_headers else None)
                or (self._header_meta(h)[0] if self._header_meta(h) else 1_000_000_000)
            ),
        )
        to_request: list[bytes] = []
        deferred: list[tuple[bytes, Optional[int]]] = []
        for h in ordered:
            height_hint = self._sync_block_queue_heights.get(h)
            if (
                height_hint is None
                and next_block_hash is not None
                and h == next_block_hash
            ):
                height_hint = expected_height
            if height_hint is None:
                if h in self._sync_headers:
                    height_hint = self._sync_headers[h].height
                else:
                    meta = self._header_meta(h)
                    if meta is not None:
                        height_hint = meta[0]
            replay_local = self._needs_local_block_replay(h, height_hint=height_hint)
            if replay_local and await self._try_import_local_block(h):
                self._sync_block_queue_set.discard(h)
                self._sync_block_queue_heights.pop(h, None)
                if height_hint == expected_height:
                    expected_height += 1
                continue
            if await self._try_import_cached_block(h):
                self._sync_block_queue_set.discard(h)
                self._sync_block_queue_heights.pop(h, None)
                continue
            if replay_local:
                deferred.append((h, height_hint))
                continue
            if (
                self._has_block(h)
                or h in self._sync_inflight_blocks
                or h in self._sync_block_buffer
            ):
                self._sync_block_queue_set.discard(h)
                self._sync_block_queue_heights.pop(h, None)
                continue
            if not (self._has_header(h) or h in self._sync_headers):
                if h in parent_requests:
                    height_hint = height_hint or expected_height
                elif height_hint is not None and height_hint <= expected_height:
                    # Allow fetching the next expected block from announcements even
                    # when we do not yet have the header (block payload carries it).
                    pass
                else:
                    deferred.append((h, height_hint))
                    continue
            if height_hint is not None and height_hint > expected_height:
                deferred.append((h, height_hint))
                continue
            if height_hint is not None and height_hint > target_height:
                deferred.append((h, height_hint))
                continue
            if len(self._sync_inflight_blocks) >= self._sync_max_inflight:
                deferred.append((h, height_hint))
                continue
            self._sync_block_queue_set.discard(h)
            self._sync_block_queue_heights.pop(h, None)
            to_request.append(h)
            if height_hint == expected_height:
                expected_height += 1
        for h, height_hint in deferred:
            self._sync_block_queue.append(h)
            self._sync_block_queue_set.add(h)
            if height_hint is not None:
                self._sync_block_queue_heights[h] = height_hint
        if not to_request:
            log.info(
                "BLOCK_FETCH_NOT_SCHEDULED",
                extra={
                    "reason": "no_eligible_blocks",
                    "remote": selected_peers[0].remote,
                    "needed_height": next_block_height,
                    "needed_hash": self._canon_hash0x(next_block_hash),
                    "queue_len": len(self._sync_block_queue),
                    "inflight_blocks": len(self._sync_inflight_blocks),
                },
            )
            return 0
        peer_slots: dict[str, int] = {}
        peer_rates: dict[str, float] = {}
        now = time.time()
        for target_peer in selected_peers:
            inflight_for_peer = sum(
                1 for remote in self._sync_inflight_peers.values() if remote == target_peer.remote
            )
            peer_slots[target_peer.remote] = max(
                0, self._sync_max_inflight_per_peer - inflight_for_peer
            )
            events = self._sync_metrics_peer_block_events.get(target_peer.remote, deque())
            peer_rates[target_peer.remote] = self._metric_rate(events, now)

        def _pick_peer() -> Optional[_PeerState]:
            best_peer = None
            best_score = None
            for candidate in selected_peers:
                slots = peer_slots.get(candidate.remote, 0)
                if slots <= 0:
                    continue
                score = (peer_rates.get(candidate.remote, 0.0), slots)
                if best_score is None or score > best_score:
                    best_score = score
                    best_peer = candidate
            return best_peer

        groups: "OrderedDict[str, list[bytes]]" = OrderedDict()
        unassigned: list[bytes] = []
        for h in to_request:
            preferred_remote = self._sync_header_sources.get(h)
            preferred_peer = (
                self._peer_by_remote(preferred_remote) if preferred_remote else None
            )
            if preferred_peer is not None:
                ok, _reason = self._block_peer_eligibility(preferred_peer)
                if (
                    ok
                    and preferred_peer.hello_done.is_set()
                    and preferred_peer.remote in peer_slots
                    and peer_slots.get(preferred_peer.remote, 0) > 0
                ):
                    peer_slots[preferred_peer.remote] -= 1
                    groups.setdefault(preferred_peer.remote, []).append(h)
                    continue
            target_peer = _pick_peer()
            if target_peer is None:
                unassigned.append(h)
                continue
            peer_slots[target_peer.remote] -= 1
            groups.setdefault(target_peer.remote, []).append(h)
        if unassigned:
            for h in unassigned:
                self._sync_block_queue.append(h)
                self._sync_block_queue_set.add(h)
                height_hint = self._block_height_hint(h)
                if height_hint is not None:
                    self._sync_block_queue_heights[h] = height_hint
        requested = 0
        for remote, hashes in groups.items():
            target_peer = self._peer_by_remote(remote) or selected_peers[0]
            requested += await self._queue_block_requests(target_peer, hashes)
        return requested

    async def _sync_once(self, *, force: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "started": False,
            "peer": None,
            "remoteHeight": None,
            "localHeight": None,
        }

        async with self._sync_lock:
            self._ensure_sync_cursor_integrity()
            local_height, _ = self._local_head()
            result["localHeight"] = local_height
            if (
                self._sync_target_height is not None
                and local_height >= self._sync_target_height
                and not force
            ):
                network_best_height = self._network_best_height()
                if (
                    network_best_height is None
                    or int(network_best_height) <= int(local_height or 0)
                ):
                    self._sync_phase = "TARGET_REACHED"
                    return result
            eligible_peers, _ = self._eligible_sync_peers()
            if not eligible_peers:
                if self._sync_last_header_error == "invalid_headers":
                    cleared = self._clear_sync_backoff_reason("invalid_headers")
                    if cleared:
                        self._sync_last_header_error = None
                        self._sync_last_header_error_at = None
                        self._sync_last_header_error_peer = None
                        log.info(
                            "Cleared invalid headers backoff to retry sync",
                            extra={"cleared": cleared},
                        )
                        eligible_peers, _ = self._eligible_sync_peers()
                        if eligible_peers:
                            log.info(
                                "Retrying sync after clearing invalid headers backoff",
                                extra={"eligible_peers": len(eligible_peers)},
                            )
                if not eligible_peers:
                    self._sync_phase = "IDLE"
                    target_height = self._known_sync_target_height()
                    if (
                        target_height is not None
                        and int(target_height) > int(local_height or 0)
                    ):
                        cleared = self._clear_transient_header_penalties(
                            needed_height=int(local_height or 0) + 1
                        )
                        if cleared:
                            eligible_peers, _ = self._eligible_sync_peers()
                            if eligible_peers:
                                self._sync_kick(
                                    reason="clear_header_penalties_while_behind",
                                    aggressive=True,
                                )
                    if not eligible_peers:
                        _eligible, ineligible = self._eligible_sync_peers()
                        log.info(
                            "PEER_NOT_ACTIVATED",
                            extra={
                                "reason": "no_eligible_header_peers",
                                "eligible_count": len(_eligible),
                                "ineligible_reasons": ineligible,
                            },
                        )
                        log.debug("Sync idle: no eligible peers for headers")
                        return result

            best_header_height = (
                self._sync_best_header.height if self._sync_best_header else 0
            )
            if best_header_height > int(local_height or 0) and (
                self._sync_block_queue or self._sync_inflight_blocks
            ):
                self._sync_phase = "BLOCKS"
                log.debug(
                    "Skipping header sync: blocks behind headers",
                    extra={
                        "best_header_height": best_header_height,
                        "local_height": int(local_height or 0),
                        "queued_blocks": len(self._sync_block_queue),
                        "inflight_blocks": len(self._sync_inflight_blocks),
                    },
                )
                return result

            self._stats["sync_rounds"] += 1
            self._sync_phase = "HEADERS"

            if force and self._sync_inflight_blocks:
                self._sync_inflight_blocks.clear()
                self._sync_inflight_peers.clear()
                self._sync_inflight_block_requests.clear()

            # When forcing sync, clear "at_tip" error to allow re-requesting headers
            # This helps when the node is stuck because all connected peers are at the same
            # height, but there are actually higher blocks available on the network
            if force and self._sync_last_header_error == "at_tip":
                self._sync_last_header_error = None
                self._sync_last_header_error_at = None
                self._sync_last_header_error_peer = None
                log.info("Cleared 'at_tip' error state due to forced sync")

            tried_peers: set[str] = set()
            no_headers_responses = 0
            eligible_count = len(eligible_peers)
            requested = 0

            while True:
                peer = self._select_sync_peer(
                    avoid_remotes=tried_peers
                )
                if peer is None or not peer.hello_done.is_set():
                    best_header_height = (
                        self._sync_best_header.height if self._sync_best_header else 0
                    )
                    local_height, _ = self._local_head()
                    if self._peers and best_header_height > int(local_height or 0):
                        self._sync_phase = "SYNCING"
                    else:
                        self._sync_phase = "IDLE"
                    
                    # Recovery logic: If we've tried all peers and gotten no progress for a while,
                    # clear the duplicate headers state to allow retry with fresh locators
                    now = time.time()
                    if (
                        len(tried_peers) >= eligible_count
                        and eligible_count > 0
                        and self._sync_last_header_error == "duplicate_headers"
                        and now - self._sync_last_progress_at > self._sync_stall_timeout
                    ):
                        log.warning(
                            "All peers returned duplicate headers with no progress; resetting sync state",
                            extra={
                                "tried_peers": len(tried_peers),
                                "eligible_peers": eligible_count,
                                "stall_duration_s": now - self._sync_last_progress_at,
                                "locator_depth_hint": self._sync_locator_depth_hint,
                            },
                        )
                        # Reset locator depth hint to get more detailed locators
                        self._sync_locator_depth_hint = 0
                        # Clear duplicate headers error to allow retry
                        self._sync_last_header_error = None
                        self._sync_last_header_error_at = None
                        self._sync_last_header_error_peer = None
                        # Clear all duplicate header tracking
                        self._sync_duplicate_header_ranges.clear()
                        # Clear peer backoffs for duplicate_headers reason
                        for backoff_key in list(self._sync_peer_backoff.keys()):
                            if self._sync_peer_backoff_reason.get(backoff_key) == "duplicate_headers":
                                self._sync_peer_backoff.pop(backoff_key, None)
                                self._sync_peer_backoff_reason.pop(backoff_key, None)
                    
                    log.debug(
                        "Sync skipped header request",
                        extra={
                            "reason": "no_ready_peer",
                            "eligible_count": eligible_count,
                            "tried_peers": list(tried_peers),
                        },
                    )
                    return result

                local_height, _ = self._local_head()
                remote_height = int((peer.hello or {}).get("head_height") or 0)
                result.update(
                    {
                        "peer": peer.remote,
                        "remoteHeight": remote_height,
                        "localHeight": local_height,
                    }
                )
                log.debug("Selected sync peer for headers", extra=self._sync_peer_log_context(peer))

                probe_headers = (
                    local_height == 0
                    and remote_height == 0
                    and self._sync_best_header is None
                    and not self._sync_header_queue
                )

                if (
                    remote_height <= local_height
                    and not force
                    and not self._sync_header_queue
                    and not probe_headers
                ):
                    network_best_height = self._network_best_height()
                    if (
                        network_best_height is not None
                        and int(network_best_height) > int(local_height or 0)
                    ):
                        log.info(
                            "Local head behind network; continuing header sync (multi-hop height propagation)",
                            extra={
                                "remote": peer.remote,
                                "local_height": local_height,
                                "remote_height": remote_height,
                                "network_best_height": network_best_height,
                                "height_gap": int(network_best_height) - int(local_height or 0),
                            },
                        )
                        # Continue syncing - don't stop here even if peer's own height is lower
                        # This allows us to find the higher height through peer-of-peer connections
                    else:
                        # Check if we still have pending block downloads before marking as synced
                        if (
                            self._sync_best_header is None
                            or self._sync_best_header.height <= local_height
                        ) and not self._sync_block_queue and not self._sync_inflight_blocks:
                            target_height = self._sync_target_height
                            if target_height is None:
                                target_height = remote_height
                            if target_height is not None and local_height >= max(
                                0, int(target_height) - 1
                            ):
                                self._sync_phase = "SYNCED" if local_height > 0 else "IDLE"
                            log.debug(
                                "Skipped header request: already at tip",
                                extra={
                                    "remote": peer.remote,
                                    "local_height": local_height,
                                    "remote_height": remote_height,
                                },
                            )
                            return result
                        if self._sync_block_queue or self._sync_inflight_blocks:
                            # We have pending blocks, continue to download them
                            log.debug(
                                "Continuing sync for pending blocks",
                                extra={
                                    "remote": peer.remote,
                                    "local_height": local_height,
                                    "block_queue": len(self._sync_block_queue),
                                    "inflight_blocks": len(self._sync_inflight_blocks),
                                },
                            )
                            # Skip header sync but continue with block downloads
                            return result

                saw_headers = False
                while True:
                    headers: Optional[List[HeaderCompact]] = None
                    if self._sync_header_queue:
                        queued_peer, headers = self._sync_header_queue.popleft()
                        if queued_peer != peer.remote:
                            peer = self._peer_by_remote(queued_peer) or peer
                            remote_height = int((peer.hello or {}).get("head_height") or 0)
                            result.update(
                                {
                                    "peer": peer.remote,
                                    "remoteHeight": remote_height,
                                    "localHeight": local_height,
                                }
                            )

                    if headers is None:
                        headers = await self._fetch_headers(peer)
                    if not headers:
                        if not saw_headers:
                            self._sync_last_headers_accepted_count = 0
                            self._sync_last_headers_discarded_count = 0
                            self._sync_last_headers_discard_reason_counts = {}
                        if not saw_headers:
                            network_best_height = self._network_best_height()
                            empty_reason = self._empty_headers_reason(
                                peer,
                                local_height,
                                remote_height,
                                network_best_height=network_best_height,
                                eligible_peer_count=eligible_count,
                            )
                            log.debug(
                                "No headers returned",
                                extra={
                                    "remote": peer.remote,
                                    "reason": empty_reason,
                                    "local_height": local_height,
                                    "remote_height": remote_height,
                                    "network_best_height": network_best_height,
                                },
                            )
                            if empty_reason != "at_tip":
                                result.setdefault(
                                    "error", empty_reason.replace("_", "-")
                                )
                            self._sync_last_header_error = (
                                "at_tip" if empty_reason == "at_tip" else empty_reason
                            )
                            self._sync_last_header_error_at = time.time()
                            self._sync_last_header_error_peer = peer.remote
                            if empty_reason == "genesis_mismatch":
                                self._set_sync_backoff(
                                    peer,
                                    reason="genesis_mismatch",
                                    delay=self._sync_no_headers_backoff,
                                )
                                self._penalize_peer(peer, "genesis_mismatch")
                                tried_peers.add(peer.remote)
                            elif empty_reason == "headers_empty":
                                self._record_header_empty(peer)
                                self._adjust_header_batch(
                                    success=False, reason="headers_empty"
                                )
                                self._penalize_peer(peer, "headers_empty", nonfatal=True)
                                self._mark_peer_head_issue(
                                    peer,
                                    reason="headers_empty",
                                    cooldown=self._sync_no_headers_backoff,
                                )
                                at_tip = False
                                best_header_height = (
                                    self._sync_best_header.height
                                    if self._sync_best_header
                                    else 0
                                )
                                if (
                                    network_best_height is not None
                                    and int(network_best_height)
                                    <= int(local_height or 0)
                                ):
                                    at_tip = True
                                elif (
                                    network_best_height is None
                                    and best_header_height <= int(local_height or 0)
                                ):
                                    at_tip = True
                                if (
                                    at_tip
                                    and not self._sync_block_queue
                                    and not self._sync_inflight_blocks
                                ):
                                    target_tip = self._sync_target_tip
                                    local_hash_bytes = self._parse_hash_bytes(
                                        self._local_head()[1]
                                    )
                                    if (
                                        target_tip is not None
                                        and local_hash_bytes
                                        and local_hash_bytes != target_tip.hash
                                    ):
                                        self._sync_phase = "SYNCING"
                                    else:
                                        self._sync_phase = (
                                            "SYNCED"
                                            if int(local_height or 0) > 0
                                            else "IDLE"
                                        )
                                    log.info(
                                        "Headers empty at tip; idling",
                                        extra={
                                            "remote": peer.remote,
                                            "local_height": local_height,
                                            "network_best_height": network_best_height,
                                        },
                                    )
                                if peer.empty_header_responses >= self._sync_no_headers_threshold:
                                    self._penalize_peer(
                                        peer, "headers_empty", nonfatal=True
                                    )
                                tried_peers.add(peer.remote)
                                no_headers_responses += 1
                            elif empty_reason == "stale_network_best":
                                self._force_peer_refresh(reason="stale_network_best")
                                self._sync_kick(
                                    reason="stale_network_best",
                                    aggressive=True,
                                )
                                tried_peers.add(peer.remote)
                            elif empty_reason == "peer_behind":
                                self._penalize_peer(peer, "peer_behind", nonfatal=True)
                                self._set_sync_backoff(
                                    peer,
                                    reason="peer_behind",
                                    delay=self._sync_no_headers_backoff,
                                )
                                self._mark_peer_head_issue(
                                    peer,
                                    reason="peer_behind",
                                    cooldown=self._sync_no_headers_backoff,
                                )
                                tried_peers.add(peer.remote)
                            elif empty_reason == "at_tip":
                                self._note_header_progress(peer, reason="at_tip")
                                tried_peers.add(peer.remote)
                            else:
                                self._set_sync_backoff(
                                    peer,
                                    reason=empty_reason,
                                    delay=self._sync_no_headers_backoff,
                                )
                                no_headers_responses += 1
                                tried_peers.add(peer.remote)
                        break

                    saw_headers = True
                    peer.empty_header_responses = 0
                    peer.header_cooldown_until = 0.0
                    response_info = self._headers_debug_info(headers)
                    self._sync_last_header_response_at = time.time()
                    self._sync_last_header_response_peer = peer.remote
                    self._sync_last_header_response_count = len(headers)
                    log.debug(
                        "Processing headers batch",
                        extra={
                            "remote": peer.remote,
                            "peer_id": peer.peer_id,
                            **response_info,
                        },
                    )
                    order, header_error, discard_reason_counts = self._process_headers(
                        peer, headers
                    )
                    accepted_count = len(order)
                    if discard_reason_counts:
                        discarded_count = sum(discard_reason_counts.values())
                    else:
                        discarded_count = max(0, len(headers) - accepted_count)
                    all_known = False
                    if not order and len(headers) > 0:
                        all_known = all(
                            self._has_header(bytes(h.hash))
                            or bytes(h.hash) in self._sync_headers
                            for h in headers
                        )
                        if all_known:
                            header_error = None
                        else:
                            if result.get("error") is None:
                                result["error"] = "invalid-headers"
                            if header_error is None:
                                header_error = "invalid_headers"
                            self._sync_last_header_error = header_error
                            self._sync_last_header_error_at = time.time()
                            self._sync_last_header_error_peer = peer.remote
                            break

                    if header_error:
                        if result.get("error") is None:
                            result["error"] = header_error.replace("_", "-")
                        self._sync_last_header_error = header_error
                        self._sync_last_header_error_at = time.time()
                        self._sync_last_header_error_peer = peer.remote
                    elif headers:
                        self._sync_last_header_error = None
                        self._sync_last_header_error_at = None
                        self._sync_last_header_error_peer = None

                    if not discard_reason_counts:
                        discard_reason_counts = {}
                        if accepted_count == 0 and headers:
                            discard_reason = (
                                "already_known_headers" if all_known else header_error
                            )
                            if discard_reason:
                                discard_reason_counts[discard_reason] = len(headers)
                        elif discarded_count > 0:
                            discard_reason_counts["already_known_headers"] = discarded_count
                    self._sync_last_headers_accepted_count = accepted_count
                    self._sync_last_headers_discarded_count = discarded_count
                    self._sync_last_headers_discard_reason_counts = discard_reason_counts
                    if discarded_count > 0 or header_error:
                        discard_reason = header_error or ",".join(
                            sorted(discard_reason_counts.keys())
                        )
                        log.info(
                            "HEADER_DISCARDED",
                            extra={"reason": discard_reason, "peer": peer.remote},
                        )
                        first_header = headers[0] if headers else None
                        log.info(
                            "HEADER_BATCH_DISCARDED",
                            extra={
                                "reason": header_error or "partial_discard",
                                "remote": peer.remote,
                                "first_height": int(first_header.height) if first_header else None,
                                "first_prev_hash": self._canon_hash0x(
                                    bytes(
                                        getattr(
                                            first_header,
                                            "parent_hash",
                                            getattr(first_header, "parent", b""),
                                        )
                                    )
                                )
                                if first_header
                                else None,
                                "accepted": accepted_count,
                                "discarded": discarded_count,
                                "discard_reason_counts": discard_reason_counts,
                            },
                        )

                    rotate_peer = False
                    harmless_overlap = bool(
                        discard_reason_counts
                        and set(discard_reason_counts.keys()) == {"overlap_headers"}
                    )
                    if header_error == "invalid_headers":
                        self._penalize_peer(
                            peer, "invalid_headers", nonfatal=True
                        )
                        self._set_sync_backoff(
                            peer,
                            reason="invalid_headers",
                            delay=self._sync_no_headers_backoff,
                        )
                        self._mark_peer_head_issue(
                            peer,
                            reason="invalid_headers",
                            cooldown=self._sync_no_headers_backoff,
                        )
                        tried_peers.add(peer.remote)
                        rotate_peer = True
                    if accepted_count > 0:
                        self._sync_zero_accept_batches = 0
                        if self._sync_block_buffer:
                            await self._drain_block_buffer()
                    elif headers:
                        self._sync_zero_accept_batches += 1
                        self._sync_zero_accept_last_at = time.time()
                        if (
                            self._sync_zero_accept_batches
                            >= max(1, self._sync_zero_accept_threshold)
                            and header_error != "invalid_headers"
                            and not all_known
                        ):
                            anchor_height, anchor_hash_hex = self._local_head()
                            anchor_hash = self._parse_hash_bytes(anchor_hash_hex)
                            locator = self._build_headers_locator()
                            if not locator:
                                fallback = self._genesis_hash()
                                if fallback:
                                    locator = [fallback]
                            log.warning(
                                "No header progress; rotating peer and retrying",
                                extra={
                                    "remote": peer.remote,
                                    "batch_count": len(headers),
                                    "zero_accept_batches": self._sync_zero_accept_batches,
                                    "discard_reasons": discard_reason_counts,
                                },
                            )
                            self._adjust_header_batch(
                                success=False, reason="no_progress_headers"
                            )
                            self._sync_last_header_error = "no_progress_headers"
                            self._sync_last_header_error_at = time.time()
                            self._sync_last_header_error_peer = peer.remote
                            self._enqueue_header_retry(
                                peer=peer,
                                locator=locator,
                                locator_mode="no_progress_headers",
                                anchor_height=int(anchor_height or 0),
                                anchor_hash=anchor_hash,
                                request_start_height=int(anchor_height or 0) + 1,
                                max_headers=self._sync_headers_batch_current,
                                reason="no_progress_headers",
                            )
                            tried_peers.add(peer.remote)
                            rotate_peer = True
                    if accepted_count > 0:
                        self._reset_duplicate_header_range(peer)
                    elif all_known and headers:
                        if harmless_overlap:
                            if self._is_sync_target_ahead(int(local_height or 0)):
                                overlap_count = self._track_duplicate_header_range(
                                    peer, headers
                                )
                                if (
                                    overlap_count
                                    >= max(1, self._sync_duplicate_headers_threshold)
                                ):
                                    now = time.time()
                                    anchor_height, anchor_hash_hex = self._local_head()
                                    anchor_hash = self._parse_hash_bytes(anchor_hash_hex)
                                    locator = self._build_headers_locator()
                                    if not locator:
                                        fallback = self._genesis_hash()
                                        if fallback:
                                            locator = [fallback]
                                    self._adjust_header_batch(
                                        success=False, reason="overlap_headers"
                                    )
                                    self._sync_last_header_error = "overlap_headers"
                                    self._sync_last_header_error_at = now
                                    self._sync_last_header_error_peer = peer.remote
                                    self._set_sync_backoff(
                                        peer,
                                        reason="overlap_headers",
                                        delay=max(
                                            1.0,
                                            min(
                                                self._sync_no_headers_backoff,
                                                self._sync_request_timeout,
                                            ),
                                        ),
                                    )
                                    self._enqueue_header_retry(
                                        peer=peer,
                                        locator=locator,
                                        locator_mode="overlap_headers",
                                        anchor_height=int(anchor_height or 0),
                                        anchor_hash=anchor_hash,
                                        request_start_height=int(anchor_height or 0)
                                        + 1,
                                        max_headers=self._sync_headers_batch_current,
                                        reason="overlap_headers",
                                    )
                                    tried_peers.add(peer.remote)
                                    rotate_peer = True
                                    log.warning(
                                        "Repeated overlap-only headers while behind target; rotating peer",
                                        extra={
                                            "remote": peer.remote,
                                            "overlap_count": overlap_count,
                                            "batch_count": len(headers),
                                            "local_height": int(local_height or 0),
                                            "target_height": self._sync_target_height,
                                            "network_best_height": self._network_best_height(),
                                        },
                                    )
                            if not rotate_peer:
                                break
                        if not (harmless_overlap and rotate_peer):
                            if not peer.anchored:
                                self._mark_peer_anchored(peer, reason="headers_duplicate")
                            duplicate_count = self._track_duplicate_header_range(
                                peer, headers
                            )
                            if duplicate_count >= self._sync_duplicate_headers_threshold:
                                now = time.time()
                                stall_duration = now - self._sync_last_progress_at

                                # If we've been stalled too long, reset instead of increasing depth
                                if (
                                    stall_duration > self._sync_stall_timeout
                                    and self._sync_locator_depth_hint > 0
                                ):
                                    log.warning(
                                        "Duplicate headers with extended stall; resetting locator depth",
                                        extra={
                                            "peer": peer.remote,
                                            "duplicate_count": duplicate_count,
                                            "stall_duration_s": stall_duration,
                                            "old_depth_hint": self._sync_locator_depth_hint,
                                        },
                                    )
                                    # Reset to allow more detailed locators
                                    self._sync_locator_depth_hint = 0
                                    # Clear error state for fresh retry
                                    self._sync_last_header_error = None
                                    self._sync_last_header_error_at = None
                                    self._sync_last_header_error_peer = None
                                    # Don't penalize the peer - it might be giving us the right headers
                                    # Just try again with a better locator
                                    tried_peers.add(peer.remote)
                                    rotate_peer = True
                                else:
                                    # Normal duplicate handling: increase depth and penalize
                                    self._sync_locator_depth_hint = min(
                                        self._sync_locator_depth_hint + 8, 64
                                    )
                                    tried_peers.add(peer.remote)
                                    self._sync_last_header_error = "duplicate_headers"
                                    self._sync_last_header_error_at = now
                                    self._sync_last_header_error_peer = peer.remote
                                    self._penalize_peer(
                                        peer, "duplicate_headers", nonfatal=True
                                    )
                                    rotate_peer = True

                    if rotate_peer:
                        saw_headers = False
                        break
                    if not order and len(headers) > 0 and all_known:
                        break

                    best_known_header_height = (
                        self._sync_best_header.height if self._sync_best_header else 0
                    )
                    if (
                        remote_height > 0
                        and best_known_header_height >= int(remote_height or 0)
                    ):
                        break
                    more_headers_expected = (
                        accepted_count > 0
                        and remote_height > 0
                        and best_known_header_height < int(remote_height or 0)
                    )
                    if len(headers) >= self._sync_headers_batch_current or more_headers_expected:
                        log.info(
                            "Scheduling next header request",
                            extra={
                                "remote": peer.remote,
                                "last_batch": len(headers),
                                "batch_size": self._sync_headers_batch_current,
                                "best_known_header_height": best_known_header_height,
                                "remote_height": remote_height,
                                "reason": (
                                    "remote_tip_ahead"
                                    if more_headers_expected
                                    and len(headers) < self._sync_headers_batch_current
                                    else "batch_full"
                                ),
                            },
                        )
                        continue
                    break

                if saw_headers:
                    break
                if len(tried_peers) >= eligible_count:
                    break

            best_header_height = (
                self._sync_best_header.height if self._sync_best_header else 0
            )
            if no_headers_responses and len(tried_peers) >= eligible_count:
                network_best_height = self._network_best_height()
                if (
                    (network_best_height is not None and network_best_height > local_height)
                    or best_header_height > local_height
                ):
                    anchor_height, anchor_hash_hex = self._local_head()
                    anchor_hash = self._parse_hash_bytes(anchor_hash_hex)
                    locator = self._build_headers_locator()
                    if not locator:
                        fallback = self._genesis_hash()
                        if fallback:
                            locator = [fallback]
                    self._enqueue_header_retry(
                        peer=None,
                        locator=locator,
                        locator_mode="headers_empty_all_peers",
                        anchor_height=int(anchor_height or 0),
                        anchor_hash=anchor_hash,
                        request_start_height=int(anchor_height or 0) + 1,
                        max_headers=self._sync_headers_batch_current,
                        reason="headers_empty_all_peers",
                    )
                    self._sync_kick(
                        reason="headers_empty_all_peers", aggressive=True
                    )
                if (
                    self._sync_last_header_error == "headers_empty"
                    and self._sync_last_header_at > 0
                    and (
                        (
                            network_best_height is not None
                            and int(network_best_height) > int(local_height or 0)
                        )
                        or best_header_height > int(local_height or 0)
                        or (
                            self._sync_target_height is not None
                            and int(self._sync_target_height)
                            > int(local_height or 0)
                        )
                    )
                    and time.time() - self._sync_last_header_at > self._sync_stall_timeout
                ):
                    self._sync_block_stalled_reason = STALL_HEADERS_EMPTY_LOOP
                    self._sync_last_block_error = STALL_HEADERS_EMPTY_LOOP
                    self._sync_last_block_error_at = time.time()
                    self._sync_kick(
                        reason="stall:headers_empty_loop", aggressive=True
                    )
            if best_header_height > local_height:
                self._sync_phase = "SYNCING"
                if self._should_defer_blocks_for_checkpoint(best_header_height):
                    self._sync_last_checkpoint_action = "waiting_for_checkpoint_headers"
                    log.info(
                        "Deferring block sync until checkpoint headers reached",
                        extra={
                            "best_header_height": best_header_height,
                            "checkpoint_height": self._sync_checkpoint_height,
                            "checkpoint_validation": self._sync_checkpoint_validation,
                        },
                    )
                else:
                    self._expire_inflight_blocks()
                    added = self._ensure_block_queue()
                    if added:
                        log.info(
                            "Blocks queued",
                            extra={"count": added, "best_header": best_header_height},
                        )
                    requested = await self._schedule_block_requests(peer)
                    if requested:
                        self._sync_phase = "BLOCKS"

            new_height, new_hash_hex = self._local_head()
            target_height = self._sync_target_height
            if target_height is None:
                target_height = remote_height
            if (
                target_height is not None
                and new_height >= max(0, int(target_height) - 1)
                and best_header_height <= new_height
            ):
                target_tip = self._sync_target_tip
                local_hash_bytes = self._parse_hash_bytes(new_hash_hex)
                if (
                    target_tip is not None
                    and target_tip.hash
                    and local_hash_bytes
                    and local_hash_bytes != target_tip.hash
                ):
                    self._sync_phase = "SYNCING"
                else:
                    self._sync_phase = "SYNCED" if new_height > 0 else "IDLE"
            network_best_after = self._network_best_height()
            if (
                self._sync_target_height is not None
                and network_best_after is not None
                and int(new_height or 0) >= int(network_best_after)
            ):
                self._update_sync_target_height(
                    None, reason="converged_to_network_best"
                )
                log.info(
                    "SYNC_CONVERGED",
                    extra={
                        "local_height": int(new_height or 0),
                        "remote_height": int(network_best_after),
                    },
                )

            result["started"] = True
            result["blocksRequested"] = requested
            return result

    async def _sync_loop(self) -> None:
        while self._running:
            try:
                await self._sync_loop_forever()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._handle_sync_loop_exception(exc)
                try:
                    await asyncio.sleep(
                        min(1.0, max(float(self._sync_tick_sec), 0.05))
                    )
                except asyncio.CancelledError:
                    return

    async def _sync_loop_forever(self) -> None:
        # Rebind the sync lock to THIS running loop. The node can end up running
        # the sync driver under a different event loop than the one that first
        # acquired self._sync_lock (e.g. after a soft loop restart). A lock bound
        # to a dead loop makes every `async with self._sync_lock` raise "Lock is
        # bound to a different event loop", which freezes sync — the head stops
        # advancing and the chain looks reset. Recreating it here (entered only
        # at startup and after a crash-restart, not per tick) keeps it correct.
        self._sync_lock = asyncio.Lock()
        try:
            while self._running:
                if not self._sync_enabled:
                    await asyncio.sleep(self._sync_tick_sec)
                    continue
                if self._sync_paused:
                    if self._should_unpause_sync():
                        self._sync_paused = False
                        self._sync_kick(reason="unpause_behind", aggressive=True)
                    else:
                        await asyncio.sleep(self._sync_tick_sec)
                        continue
                tick = self._sync_tick_sec
                now = time.time()
                
                # Calculate boosted tick rate once
                boosted_tick = (
                    self._sync_boost_tick_sec
                    if self._sync_boost_tick_sec is not None
                    else self._default_sync_boost_tick_sec()
                )
                
                if self._sync_boost_until and now < self._sync_boost_until:
                    tick = boosted_tick
                elif self._sync_boost_until and now >= self._sync_boost_until:
                    # Adaptive boost: maintain boost mode while actively syncing blocks
                    # This prevents the dramatic slowdown that occurs when boost expires
                    # while blocks are still in-flight or queued
                    active_sync = (
                        len(self._sync_block_queue) > 0
                        or len(self._sync_inflight_blocks) > 0
                        or len(self._sync_block_buffer) > 0
                        or (self._sync_best_header and self._sync_best_header.height > int(self._local_head()[0] or 0))
                    )
                    
                    if active_sync:
                        # Extend boost by another request timeout period
                        self._sync_boost_until = now + max(1.0, self._sync_request_timeout)
                        tick = boosted_tick
                        log.debug(
                            "Extended sync boost due to active block syncing",
                            extra={
                                "queued_blocks": len(self._sync_block_queue),
                                "inflight_blocks": len(self._sync_inflight_blocks),
                                "buffered_blocks": len(self._sync_block_buffer),
                                "boost_until": self._sync_boost_until,
                            },
                        )
                    else:
                        self._sync_boost_until = None
                        self._sync_boost_tick_sec = None
                try:
                    await asyncio.wait_for(self._sync_wakeup.wait(), timeout=tick)
                except asyncio.TimeoutError:
                    pass
                self._sync_wakeup.clear()
                now = time.time()
                head_height, head_hash = self._local_head()
                best_header_height = (
                    self._sync_best_header.height if self._sync_best_header else 0
                )
                best_block_height = int(head_height or 0)
                if (
                    best_block_height != self._sync_last_head_height
                    or head_hash != self._sync_last_head_hash
                ):
                    self._note_sync_progress(
                        reason="head_advanced",
                        head_height=best_block_height,
                        head_hash=head_hash,
                    )
                if best_header_height > self._sync_last_header_height:
                    self._note_sync_progress(
                        reason="headers_advanced",
                        header_height=best_header_height,
                    )
                current_queue_depth = len(self._sync_block_queue)
                if (
                    self._sync_last_queue_depth > 0
                    and current_queue_depth < self._sync_last_queue_depth
                ):
                    self._note_sync_progress(
                        reason="queue_drained",
                        queue_depth=current_queue_depth,
                    )
                else:
                    self._sync_last_queue_depth = current_queue_depth
                best_peer, best_peer_height, best_peer_hash = self._best_peer_head()
                target_tip = self._update_sync_target_tip(now)
                # At-tip => SYNCED. When no target is ahead of us (the
                # heaviest-chain guard in _select_sync_target_tip cleared it)
                # and there's no pending block work, we are at the tip of the
                # best chain we can see. Declare SYNCED so the miner/template
                # path runs and block production resumes — otherwise a node
                # whose only peers are behind/foreign (e.g. after its DB was
                # restored above the network's diverged fork) stays pinned in
                # HEADERS forever and never produces a block.
                # Guard: only declare SYNCED when no CONNECTED peer is strictly
                # ahead of us. target_tip goes None both when we are genuinely at
                # the tip AND when the only ahead peer was penalized out of the
                # sync-candidate set (consensus_mismatch/headers_timeout backoff).
                # Without this check a node far behind a known-but-filtered peer
                # latches SYNCED and stops syncing — the 1.9.13 "stuck behind"
                # regression. _max_peer_head_height counts ahead-but-filtered
                # peers, so it stays > our head until we actually catch up.
                if (
                    target_tip is None
                    and best_block_height > 0
                    and self._max_peer_head_height(now=now) <= best_block_height
                    and not self._sync_block_queue
                    and not self._sync_inflight_blocks
                    and self._sync_phase not in ("SYNCED", "IDLE")
                ):
                    self._sync_phase = "SYNCED"
                log.debug(
                    "Sync loop tick",
                    extra={
                        "phase": self._sync_phase,
                        "head_height": best_block_height,
                        "head_hash": head_hash,
                        "best_header_height": best_header_height,
                        "best_block_height": best_block_height,
                        "target_height": self._sync_target_height,
                        "best_peer": best_peer.remote if best_peer else None,
                        "best_peer_height": best_peer_height,
                        "best_peer_hash": best_peer_hash.hex() if best_peer_hash else None,
                        "target_tip_height": target_tip.height if target_tip else None,
                        "target_tip_hash": target_tip.hash.hex() if target_tip else None,
                        "target_tip_peer": target_tip.peer_id if target_tip else None,
                        "inflight_headers": int(self._sync_inflight_headers),
                        "inflight_blocks": len(self._sync_inflight_blocks),
                        "queued_blocks": len(self._sync_block_queue),
                        "queued_headers": len(self._sync_header_queue),
                        "queued_header_retries": len(self._sync_header_retry_queue),
                        "last_progress_age_s": round(
                            max(0.0, now - self._sync_last_progress_at), 3
                        ),
                        "sync_requested": self._sync_requested,
                    },
                )
                self._expire_inflight_headers()
                self._expire_inflight_blocks()
                self._prune_orphan_buffer()
                if (
                    self._sync_drop_slow_peers
                    and self._sync_active_block_peer
                    and (now - self._sync_last_progress_at) > 10.0
                    and not self._sync_inflight_blocks
                ):
                    active = self._peer_by_remote(self._sync_active_block_peer)
                    if active is not None and not self._is_trusted_peer(active):
                        await self._drop_peer(active, reason="stall_no_inflight")
                        trusted = [p for p in self._peers.values() if self._is_trusted_peer(p)]
                        if trusted:
                            self._sync_active_block_peer = trusted[0].remote

                
                # Periodically discard blocks from ineligible peers to prevent stalls
                # This is especially important when peers have handshake_pending status
                if now - self._sync_last_progress_at > self._sync_stall_timeout / 2:
                    self._discard_blocks_from_ineligible_peers()

                next_block_height, next_block_hash = self._next_block_needed()
                if next_block_hash != self._sync_next_block_stable_hash:
                    self._sync_next_block_stable_hash = next_block_hash
                    self._sync_next_block_stable_since = now
                elif (
                    next_block_hash is not None
                    and best_header_height > best_block_height
                    and not self._sync_inflight_blocks
                    and not self._sync_block_buffer
                    and now - self._sync_next_block_stable_since > self._sync_stall_timeout
                ):
                    if self._sync_block_stalled_reason != STALL_BLOCK_NOT_ADVANCING:
                        self._sync_block_stalled_reason = STALL_BLOCK_NOT_ADVANCING
                        self._sync_last_block_error = STALL_BLOCK_NOT_ADVANCING
                        self._sync_last_block_error_at = now
                        self._sync_kick(
                            reason="stall:block_not_advancing", aggressive=True
                        )
                
                self._maybe_mark_block_stalled(now)
                self._sync_watchdog_check(
                    now=now, head_height=best_block_height, head_hash=head_hash
                )
                
                # Check if local chain has blocks past verifier's highest height
                # and discount them if necessary
                self._check_and_discount_blocks_past_verifier()
                
                network_best_height = self._network_best_height()
                previous_target = self._sync_target_height
                target_height = best_peer_height
                if target_tip is not None:
                    target_height = target_tip.height
                if network_best_height is not None:
                    target_height = (
                        max(int(network_best_height), int(target_height or 0))
                        if target_height is not None
                        else int(network_best_height)
                    )
                self._update_sync_target_height(
                    target_height,
                    reason="sync_loop_target_update",
                )
                if (
                    target_height is not None
                    and (previous_target is None or target_height > int(previous_target))
                ):
                    self._sync_kick(reason="peer_target_advance", aggressive=False)

                if target_tip is not None:
                    local_hash_bytes = self._parse_hash_bytes(head_hash)
                    if (
                        local_hash_bytes
                        and target_tip.hash
                        and local_hash_bytes != target_tip.hash
                        and best_block_height >= target_tip.height
                    ):
                        self._sync_target_mismatch_count += 1
                        log.warning(
                            "Sync target hash mismatch; continuing sync to target tip",
                            extra={
                                "local_height": best_block_height,
                                "local_hash": head_hash,
                                "target_height": target_tip.height,
                                "target_hash": target_tip.hash.hex(),
                                "mismatch_count": self._sync_target_mismatch_count,
                            },
                        )
                        anchor_height, anchor_hash_hex = self._local_head()
                        anchor_hash = self._parse_hash_bytes(anchor_hash_hex)
                        locator = self._build_headers_locator()
                        if not locator:
                            fallback = self._genesis_hash()
                            if fallback:
                                locator = [fallback]
                        target_peer = self._peer_by_id(target_tip.peer_id) or best_peer
                        self._enqueue_header_retry(
                            peer=target_peer,
                            locator=locator,
                            locator_mode="target_tip_mismatch",
                            anchor_height=int(anchor_height or 0),
                            anchor_hash=anchor_hash,
                            request_start_height=int(anchor_height or 0) + 1,
                            max_headers=self._sync_headers_batch_current,
                            reason="target_tip_mismatch",
                        )
                        self._sync_kick(reason="target_tip_mismatch", aggressive=True)

                self._enforce_sync_invariants(
                    now=now,
                    best_block_height=best_block_height,
                    best_header_height=best_header_height,
                    target_height=target_height,
                    best_peer=best_peer,
                )

                if (
                    best_header_height > best_block_height
                    and now - self._sync_last_block_at > self._sync_stall_timeout
                    and now - self._sync_last_header_at <= self._sync_stall_timeout
                ):
                    self._sync_kick(reason="headers_ahead_blocks_idle", aggressive=False)
                    self._ensure_block_queue()

                if (
                    network_best_height is not None
                    and int(network_best_height) - best_block_height >= 3
                    and now - self._sync_last_progress_at > self._sync_stall_timeout
                ):
                    inflight_stuck = bool(
                        self._sync_inflight_blocks or self._sync_inflight_headers
                    )
                    queues_empty = not self._sync_block_queue and not self._sync_header_queue
                    anchor_invalid = (
                        self._sync_last_header_error == "not_anchored"
                        or not any(self._peer_is_anchored(p) for p in self._peers.values())
                    )
                    if inflight_stuck or queues_empty or anchor_invalid:
                        stall_reason = STALL_BLOCK_PEER_UNRESPONSIVE
                        if anchor_invalid:
                            stall_reason = STALL_BLOCK_NOT_FOUND_ACROSS_PEERS
                        self._sync_block_stalled_reason = stall_reason
                        self._sync_last_block_error = stall_reason
                        self._sync_last_block_error_at = now
                        self._sync_kick(reason=f"stall:{stall_reason}", aggressive=True)

                # Detect when headers == blocks and we're not making progress.
                # Treat an unknown sync header as equal to the local head so a
                # missing _sync_best_header does not suppress stall recovery.
                effective_best_header_height = (
                    best_header_height if self._sync_best_header is not None else best_block_height
                )
                target_ahead = False
                if (
                    network_best_height is not None
                    and int(network_best_height) > best_block_height
                ):
                    target_ahead = True
                elif (
                    self._sync_target_height is not None
                    and int(self._sync_target_height) > best_block_height
                ):
                    target_ahead = True
                elif best_peer_height is not None and int(best_peer_height) > best_block_height:
                    target_ahead = True

                if (
                    effective_best_header_height == best_block_height
                    and best_block_height > 0
                    and target_ahead
                    and not self._sync_inflight_headers
                    and not self._sync_inflight_blocks
                    and not self._sync_block_queue
                    and now - self._sync_last_progress_at > self._sync_stall_timeout
                    and self._peers  # Have peers but not making progress
                ):
                    # Mark as stalled to trigger peer rotation and recovery
                    if self._sync_block_stalled_reason != STALL_BLOCK_PEER_UNRESPONSIVE:
                        log.warning(
                            "Sync stalled: headers == blocks with no progress",
                            extra={
                                "height": best_block_height,
                                "best_header_height": effective_best_header_height,
                                "network_best_height": network_best_height,
                                "target_height": self._sync_target_height,
                                "best_peer_height": best_peer_height,
                                "stall_elapsed_s": max(0.0, now - self._sync_last_progress_at),
                                "peers": len(self._peers),
                                "last_header_error": self._sync_last_header_error,
                            },
                        )
                        self._sync_block_stalled_reason = STALL_BLOCK_PEER_UNRESPONSIVE
                        self._sync_last_block_error = STALL_BLOCK_PEER_UNRESPONSIVE
                        self._sync_last_block_error_at = now
                        self._sync_kick(
                            reason="stall:headers_equal_blocks",
                            aggressive=True,
                        )

                if (
                    network_best_height is not None
                    and best_block_height < int(network_best_height)
                    and not self._sync_inflight_blocks
                    and not self._sync_inflight_headers
                    and not self._sync_block_queue
                    and not self._sync_header_queue
                    and now - self._sync_last_progress_at > self._sync_stall_timeout
                ):
                    log.debug(
                        "Sync invariant violated: behind network without block requests",
                        extra={
                            "head_height": best_block_height,
                            "network_best_height": network_best_height,
                            "stall_elapsed_s": max(0.0, now - self._sync_last_progress_at),
                        },
                    )
                    self._sync_block_stalled_reason = STALL_BLOCK_TIMEOUT
                    self._ensure_block_queue()
                    self._sync_kick(reason="behind_network_empty_queue", aggressive=True)
                if now - self._sync_last_progress_at > self._sync_no_progress_timeout:
                    self._stats["sync_stall_detected"] += 1
                    self._reset_sync_inflight(
                        now=now, reason="no_progress_timeout"
                    )
                    self._sync_block_queue.clear()
                    self._sync_block_queue_set.clear()
                    self._sync_block_queue_heights.clear()
                    self._sync_block_retry_counts.clear()
                    self._ensure_block_queue()
                    target_height_for_recovery = self._known_sync_target_height()
                    if (
                        target_height_for_recovery is not None
                        and int(target_height_for_recovery) > best_block_height
                        and best_header_height <= best_block_height
                    ):
                        self._clear_transient_header_penalties(
                            needed_height=best_block_height + 1
                        )
                        self._enqueue_header_stall_retry(
                            reason="no_progress_header_recovery"
                        )
                    self._sync_kick(reason="no_progress_timeout", aggressive=True)
                stalled = self._sync_block_stalled_reason is not None
                if stalled:
                    self._handle_sync_stall(
                        reason=self._sync_block_stalled_reason or "stalled"
                    )
                    if (
                        now - self._sync_last_progress_at
                        >= max(60.0, self._sync_watchdog_timeout)
                        and self._sync_recovery_attempts
                        >= self._snapshot_recovery_min_stall_recoveries
                    ):
                        self._maybe_trigger_snapshot_recovery(
                            reason="stall_snapshot_recovery"
                        )
                elif (
                    self._rotation_interval > 0
                    and now - self._last_rotation_at >= self._rotation_interval
                ):
                    self._rotate_sync_peer()
                    self._last_rotation_at = now
                pending_sync_request = bool(self._sync_requested)
                pending_sync_request_at = (
                    float(self._sync_requested_at or 0.0)
                    if pending_sync_request
                    else None
                )
                force_sync = (
                    stalled or self._sync_force_always or pending_sync_request
                )
                await self._sync_once(force=force_sync)
                if (
                    pending_sync_request
                    and self._sync_requested
                    and self._sync_requested_at == pending_sync_request_at
                ):
                    self._sync_requested = False
                self._log_sync_cycle()
                # Schedule block requests regardless of stall status
                # This breaks the catch-22 where stall detection prevented recovery
                # _handle_sync_stall() above still provides peer rotation and diagnostics
                # Single call is sufficient: _schedule_block_requests() handles all cases
                # internally (seeding from headers, checking inflight, respecting limits)
                await self._schedule_block_requests()
                self._log_sync_metrics(time.time())
                # Recovery paths can set wakeups repeatedly; always yield once per cycle.
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            return

    def _sync_phase_reason(self, *, best_header_height: int, best_block_height: int) -> str:
        _phase, reason = self._derive_sync_phase(
            best_header_height=best_header_height,
            best_block_height=best_block_height,
            pending_header_batches=len(self._sync_header_queue)
            + len(self._sync_header_retry_queue),
            queued_blocks_count=self._queued_blocks_count(best_block_height),
            buffered_blocks_count=len(self._sync_block_buffer),
            eligible_header_peers=len(self._eligible_sync_peers()[0]),
            last_header_error=self._sync_last_header_error,
            active_block_peer=self._sync_active_block_peer,
            synchronized=False,
            peers_total=len(self._peers),
            sync_enabled=self._sync_enabled,
            sync_requested=self._sync_requested,
            target_height=self._sync_target_height,
            stall_elapsed_s=max(0.0, time.time() - self._sync_last_progress_at),
        )
        return reason

    def _log_sync_cycle(self) -> None:
        now = time.time()
        if now - self._sync_last_cycle_log_at < self._sync_cycle_log_interval:
            return
        self._sync_last_cycle_log_at = now
        head_height, head_hash = self._local_head()
        best_header_height = self._sync_best_header.height if self._sync_best_header else 0
        best_block_height = int(head_height or 0)
        eligible_peers, ineligible_peers = self._eligible_sync_peers()
        eligible_block_peers, ineligible_block_peers = self._eligible_block_peers()
        next_block_height, next_block_hash = self._next_block_needed()
        stall_elapsed_s = max(0.0, now - self._sync_last_progress_at)
        log.info(
            "Sync cycle",
            extra={
                "phase": self._sync_phase,
                "phase_reason": self._sync_phase_reason(
                    best_header_height=best_header_height,
                    best_block_height=best_block_height,
                ),
                "head_height": int(head_height or 0),
                "head_hash": head_hash,
                "best_header_height": best_header_height,
                "next_block_height": next_block_height,
                "next_block_hash": next_block_hash.hex() if next_block_hash else None,
                "eligible_peers_for_headers": [p.remote for p in eligible_peers],
                "ineligible_peers_for_headers": dict(ineligible_peers),
                "eligible_peers_for_blocks": [p.remote for p in eligible_block_peers],
                "ineligible_peers_for_blocks": dict(ineligible_block_peers),
                "last_progress_at": self._sync_last_progress_at,
                "stall_reason": self._sync_block_stalled_reason,
                "stall_elapsed_s": stall_elapsed_s,
            },
        )
        self._log_peer_broadcast_scores()

    async def _request_blocks(self, peer: _PeerState, hashes: list[bytes]) -> None:
        if not hashes:
            return
        # Request only blocks we don't already have.
        need = [h for h in hashes if not self._has_block(h)]
        if not need:
            return
        # Bounded batching to keep payloads small and requests manageable.
        for i in range(0, len(need), 16):
            chunk = need[i : i + 16]
            with contextlib.suppress(Exception):
                await self._send(
                    peer,
                    MsgID.GET_BLOCKS,
                    GetBlocks(by_hash=chunk, max_blocks=len(chunk)),
                )
            # Let imports progress; avoids building up large outstanding queues.
            await asyncio.sleep(0)

    def _best_peer(self) -> Optional[_PeerState]:
        return self._select_sync_peer()

    def _sync_peer_log_context(self, peer: _PeerState) -> dict[str, Any]:
        hello = peer.hello or {}
        genesis_hash = bytes(hello.get("genesis_hash") or b"")
        genesis_header_hash = bytes(hello.get("genesis_header_hash") or b"")
        genesis_block_hash = bytes(hello.get("genesis_block_hash") or b"")
        genesis_identity = bytes(hello.get("genesis_identity") or b"")
        params_hash = bytes(hello.get("network_params_hash") or b"")
        return {
            "remote": peer.remote,
            "peer_id": peer.peer_id,
            "direction": peer.direction,
            "version": hello.get("version"),
            "agent": hello.get("agent"),
            "chain_id": hello.get("chain_id"),
            "genesis_hash": genesis_hash.hex() if genesis_hash else None,
            "genesis_header_hash": genesis_header_hash.hex()
            if genesis_header_hash
            else None,
            "genesis_block_hash": genesis_block_hash.hex()
            if genesis_block_hash
            else None,
            "genesis_identity": genesis_identity.hex() if genesis_identity else None,
            "network_params_hash": params_hash.hex() if params_hash else None,
            "head_height": hello.get("head_height"),
            "head_hash": bytes(hello.get("head_hash") or b"").hex()
            if hello.get("head_hash")
            else None,
            "capabilities": list(hello.get("capabilities") or []),
        }

    def _log_peer_eligibility(self, peer: _PeerState, ok: bool, reason: str) -> None:
        key = self._peer_eligibility_key(peer)
        prev = self._sync_peer_eligibility_cache.get(key)
        current = f"{'eligible' if ok else 'ineligible'}:{reason}"
        if prev == current:
            return
        self._sync_peer_eligibility_cache[key] = current
        log.info(
            "Sync peer eligibility update",
            extra={
                "peer_key": key,
                "remote": peer.remote,
                "peer_id": peer.peer_id,
                "eligible": ok,
                "reason": reason,
                "anchored": bool(peer.anchored),
                "anchor_reason": peer.anchor_reason,
                "backoff_reason": self._sync_peer_backoff_reason.get(
                    self._peer_backoff_key(peer)
                ),
            },
        )

    def _clear_sync_backoff_reason(self, reason: str) -> int:
        removed = 0
        for key, current_reason in list(self._sync_peer_backoff_reason.items()):
            if current_reason != reason:
                continue
            self._sync_peer_backoff_reason.pop(key, None)
            self._sync_peer_backoff.pop(key, None)
            removed += 1
        return removed

    def _highest_announced_peer_height(self) -> Optional[int]:
        best: Optional[int] = None
        now = time.time()
        for peer in self._peers.values():
            if not peer.hello_done.is_set():
                continue
            if not peer.repo_state_ok:
                continue
            if not self._peer_chain_matches(peer):
                continue
            height = 0
            info = self._sync_peer_heads.get(peer.remote)
            if (
                info is not None
                and now - info.updated_at <= self._sync_peer_head_stale_sec
            ):
                height = max(height, int(info.height))
            try:
                height = max(height, int((peer.hello or {}).get("head_height") or 0))
            except Exception:
                pass
            if height <= 0:
                continue
            best = height if best is None else max(best, height)
        return best

    def _known_sync_target_height(self) -> Optional[int]:
        heights: list[int] = []
        if self._sync_target_height is not None:
            heights.append(int(self._sync_target_height))
        network_best_height = self._network_best_height()
        if network_best_height is not None:
            heights.append(int(network_best_height))
        announced_height = self._highest_announced_peer_height()
        if announced_height is not None:
            heights.append(int(announced_height))
        if self._sync_best_header is not None:
            heights.append(int(self._sync_best_header.height))
        return max(heights) if heights else None

    def _clear_transient_header_penalties(
        self, *, needed_height: Optional[int] = None
    ) -> int:
        now = time.time()
        cleared = 0
        transient_reasons = {
            "headers_empty",
            "headers_timeout",
            "headers_watchdog_timeout",
            "headers_inflight_maxed",
            "peer_behind",
            "stale_network_best",
            "duplicate_headers",
            "overlap_headers",
            "no_progress_headers",
        }
        for peer in self._peers.values():
            if not peer.hello_done.is_set():
                continue
            height = 0
            info = self._sync_peer_heads.get(peer.remote)
            if info is not None:
                height = max(height, int(info.height))
            try:
                height = max(height, int((peer.hello or {}).get("head_height") or 0))
            except Exception:
                pass
            if needed_height is not None and height < int(needed_height):
                continue

            backoff_key = self._peer_backoff_key(peer)
            backoff_reason = self._sync_peer_backoff_reason.get(backoff_key)
            active_backoff = self._sync_peer_backoff.get(backoff_key, 0.0) > now
            clear_backoff = bool(
                active_backoff and backoff_reason in transient_reasons
            )
            clear_header_cooldown = bool(
                peer.header_cooldown_until > now
                and (
                    peer.empty_header_responses > 0
                    or backoff_reason in transient_reasons
                    or self._sync_last_header_error in transient_reasons
                )
            )
            clear_head_cooldown = bool(
                info is not None
                and info.cooldown_until > now
                and (
                    info.last_error in transient_reasons
                    or self._sync_last_header_error in transient_reasons
                )
            )
            if not clear_backoff and not clear_header_cooldown and not clear_head_cooldown:
                continue
            if clear_backoff:
                self._sync_peer_backoff.pop(backoff_key, None)
                self._sync_peer_backoff_reason.pop(backoff_key, None)
            if clear_header_cooldown:
                peer.header_cooldown_until = 0.0
                peer.empty_header_responses = 0
            if clear_head_cooldown and info is not None:
                info.cooldown_until = 0.0
                info.last_error = None
            cleared += 1
        if cleared:
            log.info(
                "Cleared transient header sync penalties",
                extra={"cleared": cleared, "needed_height": needed_height},
            )
        return cleared

    def _enqueue_header_stall_retry(self, *, reason: str) -> None:
        head_height, head_hash = self._local_head()
        anchor_hash = self._parse_hash_bytes(head_hash)
        anchor_height = int(head_height or 0)
        locator = self._build_headers_locator()
        if not locator:
            fallback = self._genesis_hash()
            if fallback:
                locator = [fallback]
        target_peer = self._select_sync_peer(allow_pow_backoff=True)
        self._enqueue_header_retry(
            peer=target_peer,
            locator=locator,
            locator_mode="header_stall_recovery",
            anchor_height=anchor_height,
            anchor_hash=anchor_hash,
            request_start_height=anchor_height + 1,
            max_headers=self._sync_headers_batch_current,
            reason=reason,
        )
        self._sync_zero_accept_batches = 0
        self._sync_duplicate_header_ranges.clear()
        self._sync_locator_depth_hint = 0
        if self._sync_last_header_error in {
            "headers_empty",
            "headers_timeout",
            "headers_watchdog_timeout",
            "headers_inflight_maxed",
            "peer_behind",
            "stale_network_best",
            "duplicate_headers",
            "overlap_headers",
            "no_progress_headers",
            "at_tip",
        }:
            self._sync_last_header_error = None
            self._sync_last_header_error_at = None
            self._sync_last_header_error_peer = None
        self._sync_kick(reason=reason, aggressive=True)

    def _clear_transient_block_penalties(
        self, *, needed_height: Optional[int] = None
    ) -> int:
        now = time.time()
        cleared = 0
        transient_reasons = {"block_timeout"}
        transient_stall_reasons = {
            STALL_BLOCK_TIMEOUT,
            STALL_BLOCK_PEER_UNRESPONSIVE,
            "watchdog_no_progress",
        }
        for peer in self._peers.values():
            if not peer.hello_done.is_set():
                continue
            if needed_height is not None and self._peer_sync_head_height(peer, now=now) < int(
                needed_height
            ):
                continue
            backoff_key = self._peer_backoff_key(peer)
            backoff_reason = self._sync_block_peer_backoff_reason.get(backoff_key)
            active_backoff = self._sync_block_peer_backoff.get(backoff_key, 0.0) > now
            clear_backoff = bool(active_backoff and backoff_reason in transient_reasons)
            clear_cooldown = bool(
                peer.block_cooldown_until > now
                and peer.block_failures > 0
                and (
                    clear_backoff
                    or backoff_reason in transient_reasons
                    or self._sync_last_block_error in transient_stall_reasons
                )
            )
            if not clear_backoff and not clear_cooldown:
                continue
            if clear_backoff:
                self._sync_block_peer_backoff.pop(backoff_key, None)
                self._sync_block_peer_backoff_reason.pop(backoff_key, None)
            if clear_cooldown:
                peer.block_cooldown_until = 0.0
                peer.block_failures = 0
                peer.last_block_failure_at = 0.0
                events = self._sync_block_failure_events.get(peer.remote)
                if events:
                    events.clear()
            cleared += 1
        return cleared

    def _is_force_sync_remote(self, remote: Optional[str]) -> bool:
        if not remote:
            return False
        key = self._addr_key(remote).strip().lower()
        if key in FORCE_SYNC_HEADER_PEERS:
            return True
        host = self._extract_host(remote).strip().lower()
        if not host:
            return False
        return f"{host}:{DEFAULT_TCP_PORT}" in FORCE_SYNC_HEADER_PEERS

    def _prune_header_votes(self, now: float) -> None:
        ttl = max(1.0, float(self._sync_header_vote_ttl_s))
        while self._sync_header_votes:
            _hash, entry = next(iter(self._sync_header_votes.items()))
            last_at = float(entry.get("last_at") or 0.0)
            if now - last_at <= ttl:
                break
            self._sync_header_votes.popitem(last=False)
        while len(self._sync_header_votes) > self._sync_header_vote_cap:
            self._sync_header_votes.popitem(last=False)

    def _record_header_vote(self, header_hash: Optional[bytes], remote: Optional[str]) -> None:
        if not header_hash or not remote:
            return
        key = self._addr_key(remote)
        now = time.time()
        self._prune_header_votes(now)
        entry = self._sync_header_votes.get(header_hash)
        if entry is None:
            entry = {"peers": set(), "last_at": now}
            self._sync_header_votes[header_hash] = entry
        peers = entry.get("peers")
        if not isinstance(peers, set):
            peers = set(peers or [])
            entry["peers"] = peers
        peers.add(key)
        entry["last_at"] = now
        self._sync_header_votes.move_to_end(header_hash, last=True)
        while len(self._sync_header_votes) > self._sync_header_vote_cap:
            self._sync_header_votes.popitem(last=False)

    def _remove_header_votes_from_peer(self, remote: Optional[str]) -> int:
        if not remote:
            return 0
        key = self._addr_key(remote)
        removed = 0
        for header_hash, entry in list(self._sync_header_votes.items()):
            peers = entry.get("peers")
            if not isinstance(peers, set):
                peers = set(peers or [])
                entry["peers"] = peers
            if key in peers:
                peers.discard(key)
                removed += 1
            if not peers:
                self._sync_header_votes.pop(header_hash, None)
        for header_hash, source_remote in list(self._sync_header_sources.items()):
            if self._addr_key(source_remote) == key:
                self._sync_header_sources.pop(header_hash, None)
        return removed

    def _header_vote_peers(
        self, header_hash: bytes, *, include_origin_remote: Optional[str] = None
    ) -> set[str]:
        now = time.time()
        self._prune_header_votes(now)
        peers: set[str] = set()
        entry = self._sync_header_votes.get(header_hash)
        if entry is not None:
            recorded = entry.get("peers")
            if isinstance(recorded, set):
                peers.update(str(p) for p in recorded if p)
            elif recorded:
                with contextlib.suppress(Exception):
                    peers.update(str(p) for p in recorded if p)
        source_remote = self._sync_header_sources.get(header_hash)
        if source_remote:
            peers.add(self._addr_key(source_remote))
        if include_origin_remote:
            peers.add(self._addr_key(include_origin_remote))
        return peers

    def _block_corroboration_status(
        self, block_hash: Optional[bytes], *, origin_remote: Optional[str]
    ) -> tuple[bool, str, dict[str, Any]]:
        if not block_hash:
            return True, "no_hash", {}
        if origin_remote and self._is_force_sync_remote(origin_remote):
            return True, "force_origin", {}
        required_votes = max(1, int(self._sync_block_confirm_min_peers))
        confirmation_peers = self._header_vote_peers(
            block_hash, include_origin_remote=origin_remote
        )
        vote_count = len(confirmation_peers)
        if vote_count >= required_votes:
            return True, "quorum_met", {"votes": vote_count, "required": required_votes}
        eligible, _ = self._eligible_block_peers()
        eligible_total = len(eligible)
        block_height: Optional[int] = None
        with contextlib.suppress(Exception):
            height_hint = self._block_height_hint(block_hash)
            if height_hint is not None:
                block_height = int(height_hint)
        if block_height is not None:
            now = time.time()
            height_eligible: list[_PeerState] = []
            for peer in eligible:
                peer_height = 0
                try:
                    peer_height = int(self._peer_sync_head_height(peer, now=now))
                except Exception:
                    with contextlib.suppress(Exception):
                        peer_height = int((peer.hello or {}).get("head_height") or 0)
                if peer_height >= int(block_height):
                    height_eligible.append(peer)
            eligible = height_eligible
        eligible_count = len(eligible)
        force_connected = any(self._is_force_sync_remote(peer.remote) for peer in eligible)
        force_voted = any(self._is_force_sync_remote(remote) for remote in confirmation_peers)
        low_peer_mode = eligible_count < required_votes
        if low_peer_mode and self._sync_block_confirm_require_force_peer:
            if force_connected:
                if force_voted:
                    return True, "force_peer_confirmed", {
                        "votes": vote_count,
                        "required": required_votes,
                        "eligible_peers": eligible_count,
                        "eligible_peers_total": eligible_total,
                        "block_height": block_height,
                    }
                return False, "await_force_peer_header", {
                    "votes": vote_count,
                    "required": required_votes,
                    "eligible_peers": eligible_count,
                    "eligible_peers_total": eligible_total,
                    "block_height": block_height,
                    "force_connected": force_connected,
                    "force_voted": force_voted,
                }
            # Fail-open in low-peer mode when no force peers are connected at all.
            # Without this, 1-2 peer topologies can deadlock forever waiting for an
            # unreachable force peer quorum.
            if vote_count > 0:
                return True, "low_peer_mode_no_force_connected", {
                    "votes": vote_count,
                    "required": required_votes,
                    "eligible_peers": eligible_count,
                    "eligible_peers_total": eligible_total,
                    "block_height": block_height,
                    "force_connected": force_connected,
                    "force_voted": force_voted,
                }
            return False, "await_force_peer_connection", {
                "votes": vote_count,
                "required": required_votes,
                "eligible_peers": eligible_count,
                "eligible_peers_total": eligible_total,
                "block_height": block_height,
                "force_connected": force_connected,
                "force_voted": force_voted,
            }
        return False, "await_header_corroboration", {
            "votes": vote_count,
            "required": required_votes,
            "eligible_peers": eligible_count,
            "eligible_peers_total": eligible_total,
            "block_height": block_height,
            "force_connected": force_connected,
            "force_voted": force_voted,
        }

    def _sync_peer_eligibility(
        self,
        peer: _PeerState,
        *,
        now: Optional[float] = None,
        ignore_backoff_reason: Optional[str] = None,
        enforce_header_cooldown: bool = True,
    ) -> tuple[bool, str]:
        now = time.time() if now is None else now
        if peer.remote in FORCE_SYNC_HEADER_PEERS:
            return True, "force_eligible"
        if not peer.hello_done.is_set():
            return False, "handshake_pending"
        if peer.hello is None or not isinstance(peer.hello, dict):
            return False, "hello_missing"
        if not peer.peer_id:
            return False, "peer_id_missing"
        if not peer.ready_for_sync:
            return False, "not_ready"
        if self._enforce_outbound_only_policy_for_peer(peer):
            return False, "outbound_only_no_inbound"
        if enforce_header_cooldown and peer.header_cooldown_until > now:
            return False, "headers_cooldown"
        if self._is_self_address(
            self._extract_host(peer.remote), self._extract_port(peer.remote) or 0
        ):
            return False, "self"
        is_exempt = self._is_peer_exempt(peer.remote)
        if not is_exempt:
            if peer.peer_id and self._is_banned(peer.peer_id, now=now):
                return False, "banned_peer_id"
            if self._is_banned(peer.remote, now=now):
                return False, "banned"
        backoff_key = self._peer_backoff_key(peer)
        backoff_until = self._sync_peer_backoff.get(backoff_key, 0.0)
        if backoff_until and backoff_until > now:
            reason = self._sync_peer_backoff_reason.get(backoff_key, "backoff")
            if reason == "not_anchored" and peer.anchored:
                self._sync_peer_backoff.pop(backoff_key, None)
                self._sync_peer_backoff_reason.pop(backoff_key, None)
            elif ignore_backoff_reason != reason:
                return False, reason
        version = str(peer.hello.get("version") or "")
        if version and version not in {"1", "2"}:
            return False, "version_mismatch"
        try:
            chain_id = int(peer.hello.get("chain_id") or 0)
        except Exception:
            chain_id = 0
        if chain_id != int(self.chain_id):
            return False, "chain_mismatch"
        genesis_header_hash = bytes(
            peer.hello.get("genesis_header_hash")
            or peer.hello.get("genesis_hash")
            or b""
        )
        genesis_block_hash = bytes(peer.hello.get("genesis_block_hash") or b"")
        if not genesis_header_hash and not genesis_block_hash:
            return False, "genesis_missing"
        local_genesis_header = self._genesis_header_hash()
        local_genesis_block = self._genesis_block_hash()
        if genesis_header_hash:
            if genesis_header_hash != local_genesis_header:
                return False, "genesis_mismatch"
        elif genesis_block_hash:
            if genesis_block_hash != local_genesis_block:
                return False, "genesis_mismatch"
        missing_fields: list[str] = []
        fork_id = int(peer.hello.get("fork_id") or 0)
        if fork_id:
            if fork_id != int(self._fork_id()):
                return False, "fork_id_mismatch"
        else:
            missing_fields.append("fork_id")
        consensus_id = str(peer.hello.get("consensus_id") or "")
        if consensus_id:
            if consensus_id != str(self._consensus_id()):
                return False, "consensus_mismatch"
        else:
            missing_fields.append("consensus_id")
        protocol_version = str(peer.hello.get("protocol_version") or "")
        if protocol_version:
            if protocol_version != str(self._protocol_version()):
                return False, "protocol_mismatch"
        else:
            missing_fields.append("protocol_version")
        genesis_identity = bytes(peer.hello.get("genesis_identity") or b"")
        if genesis_identity:
            if genesis_identity != self._genesis_identity():
                return False, "genesis_identity_mismatch"
        else:
            missing_fields.append("genesis_identity")
        params_hash = bytes(peer.hello.get("network_params_hash") or b"")
        if params_hash:
            if params_hash != self._network_params_hash():
                return False, "network_params_mismatch"
        else:
            missing_fields.append("network_params_hash")
        if not self._peer_head_matches_known_chain(peer):
            log.debug(
                "Peer head hash mismatch (non-fatal)",
                extra={
                    "remote": peer.remote,
                    "peer_id": peer.peer_id,
                    "head_height": peer.hello.get("head_height"),
                },
            )
        caps = peer.hello.get("capabilities")
        head_height = int(peer.hello.get("head_height") or 0)
        if isinstance(caps, list) and caps:
            if "sync" not in caps and "blocks" not in caps and "headers" not in caps:
                if head_height <= 0:
                    return False, "no_sync_capability"
        elif head_height <= 0:
            return False, "no_chain_data"
        if missing_fields:
            return True, "legacy_handshake"
        return True, "eligible"

    def _block_peer_eligibility(
        self, peer: _PeerState, *, now: Optional[float] = None
    ) -> tuple[bool, str]:
        now = time.time() if now is None else now
        ok, reason = self._sync_peer_eligibility(
            peer,
            now=now,
            ignore_backoff_reason="headers_empty",
            enforce_header_cooldown=False,
        )
        if not ok:
            return False, reason
        if peer.block_cooldown_until and peer.block_cooldown_until > now:
            return False, "block_cooldown"
        backoff_key = self._peer_backoff_key(peer)
        backoff_until = self._sync_block_peer_backoff.get(backoff_key, 0.0)
        if backoff_until and backoff_until > now:
            reason = self._sync_block_peer_backoff_reason.get(
                backoff_key, "block_backoff"
            )
            return False, reason
        return True, "eligible"

    def _eligible_block_peers(self) -> tuple[list[_PeerState], dict[str, str]]:
        eligible: list[_PeerState] = []
        ineligible: dict[str, str] = {}
        now = time.time()
        for peer in self._peers.values():
            ok, reason = self._block_peer_eligibility(peer, now=now)
            if ok:
                eligible.append(peer)
            else:
                ineligible[peer.remote] = reason
        return eligible, ineligible

    def _set_block_backoff(self, peer: _PeerState, *, reason: str, delay: float) -> None:
        until = time.time() + max(0.0, delay)
        key = self._peer_backoff_key(peer)
        self._sync_block_peer_backoff[key] = until
        self._sync_block_peer_backoff_reason[key] = reason

    def _record_header_empty(self, peer: _PeerState) -> None:
        peer.empty_header_responses += 1
        now = time.time()
        failures = max(1, peer.empty_header_responses)
        cooldown = min(
            self._sync_header_empty_cooldown_base_s * (2 ** (failures - 1)),
            self._sync_header_empty_cooldown_cap_s,
        )
        if cooldown > 0:
            peer.header_cooldown_until = max(peer.header_cooldown_until, now + cooldown)
        log.info(
            "Header peer cooldown applied",
            extra={
                "remote": peer.remote,
                "failures": failures,
                "cooldown_s": round(cooldown, 2),
            },
        )

    def _record_block_attempt(self, peer: _PeerState, block_hash: bytes) -> None:
        history = self._sync_block_attempts_by_hash.setdefault(
            block_hash, deque(maxlen=self._sync_block_attempts_cap)
        )
        if peer.remote in history:
            return
        history.append(peer.remote)

    def _record_block_failure(self, peer: Optional[_PeerState], *, reason: str) -> None:
        if peer is None:
            return
        now = time.time()
        events = self._sync_block_failure_events.setdefault(peer.remote, deque())
        window = self._sync_block_failure_window_s
        while events and now - events[0] > window:
            events.popleft()
        events.append(now)
        failures = len(events)
        peer.block_failures = failures
        peer.last_block_failure_at = now
        cooldown = min(
            self._sync_block_cooldown_base_s * (2 ** (failures - 1)),
            self._sync_block_cooldown_cap_s,
        )
        if cooldown > 0:
            peer.block_cooldown_until = max(peer.block_cooldown_until, now + cooldown)
        log.info(
            "Block peer cooldown applied",
            extra={
                "remote": peer.remote,
                "reason": reason,
                "failures": failures,
                "cooldown_s": round(cooldown, 2),
            },
        )

    def _record_block_success(self, peer: Optional[_PeerState]) -> None:
        if peer is None:
            return
        now = time.time()
        peer.block_successes += 1
        peer.last_block_success_at = now
        events = self._sync_block_failure_events.get(peer.remote)
        if events:
            events.clear()
        peer.block_failures = 0
        peer.block_cooldown_until = 0.0
        self._sync_block_chunk_size_current = min(
            self._sync_block_chunk_size_default,
            max(self._sync_block_chunk_size_min, self._sync_block_chunk_size_current + 1),
        )

    def _should_skip_cache_block(self, block_hash: bytes) -> bool:
        events = self._sync_cache_failures.get(block_hash)
        if not events:
            return False
        now = time.time()
        window = self._sync_cache_failure_window_s
        while events and now - events[0] > window:
            events.popleft()
        return len(events) >= self._sync_cache_failure_cap

    def _should_enforce_checkpoint_anchor(self) -> bool:
        if not self._sync_checkpoint_mode_enabled:
            return False
        if self._sync_checkpoint_validation in {"verified", "mismatch"}:
            return False
        if self._sync_checkpoint_hash is None or self._sync_checkpoint_height is None:
            return False
        return True

    def _peer_is_anchored(self, peer: _PeerState) -> bool:
        if not self._should_enforce_checkpoint_anchor():
            return True
        return bool(peer.anchored)

    def _mark_peer_anchored(self, peer: _PeerState, *, reason: str) -> None:
        peer.anchored = True
        peer.anchor_reason = reason
        peer.last_anchor_at = time.time()
        backoff_key = self._peer_backoff_key(peer)
        if self._sync_peer_backoff_reason.get(backoff_key) == "not_anchored":
            self._sync_peer_backoff.pop(backoff_key, None)
            self._sync_peer_backoff_reason.pop(backoff_key, None)
        if self._sync_peer_backoff_reason.get(peer.remote) == "not_anchored":
            self._sync_peer_backoff.pop(peer.remote, None)
            self._sync_peer_backoff_reason.pop(peer.remote, None)

    def _peer_chain_matches(self, peer: _PeerState) -> bool:
        if peer.hello is None or not isinstance(peer.hello, dict):
            return False
        try:
            chain_id = int(peer.hello.get("chain_id") or 0)
        except Exception:
            return False
        if chain_id != int(self.chain_id):
            return False
        genesis_header_hash = bytes(
            peer.hello.get("genesis_header_hash")
            or peer.hello.get("genesis_hash")
            or b""
        )
        genesis_block_hash = bytes(peer.hello.get("genesis_block_hash") or b"")
        if genesis_header_hash:
            return genesis_header_hash == self._genesis_header_hash()
        if genesis_block_hash:
            return genesis_block_hash == self._genesis_block_hash()
        return False

    def _peer_head_matches_known_chain(self, peer: _PeerState) -> bool:
        if peer.hello is None or not isinstance(peer.hello, dict):
            return True
        try:
            head_height = int(peer.hello.get("head_height") or 0)
        except Exception:
            head_height = 0
        if head_height <= 0:
            return True
        head_hash = bytes(peer.hello.get("head_hash") or b"")
        if not head_hash:
            return True
        try:
            bdb = self._block_db()
        except Exception:
            return True
        canonical_hash = None
        if hasattr(bdb, "get_canonical_hash"):
            with contextlib.suppress(Exception):
                canonical_hash = bdb.get_canonical_hash(head_height)
        if canonical_hash:
            return bytes(canonical_hash) == head_hash
        header = None
        if hasattr(bdb, "get_header_by_height"):
            with contextlib.suppress(Exception):
                header = bdb.get_header_by_height(head_height)
        if header is None:
            return True
        local_hash = self._header_hash_for_status(header)
        if not local_hash:
            return True
        local_hash_bytes = self._parse_hash_bytes(local_hash)
        if local_hash_bytes is None:
            return True
        return local_hash_bytes == head_hash

    def _eligible_sync_peers(
        self,
        *,
        ignore_backoff_reason: Optional[str] = None,
    ) -> tuple[list[_PeerState], dict[str, str]]:
        eligible: list[_PeerState] = []
        ineligible: dict[str, str] = {}
        now = time.time()
        for peer in self._peers.values():
            ok, reason = self._sync_peer_eligibility(
                peer, now=now, ignore_backoff_reason=ignore_backoff_reason
            )
            if ok:
                eligible.append(peer)
            else:
                ineligible[peer.remote] = reason
            self._log_peer_eligibility(peer, ok, reason)
        return eligible, ineligible

    def _set_sync_backoff(
        self, peer: _PeerState, *, reason: str, delay: float
    ) -> None:
        until = time.time() + max(0.0, delay)
        key = self._peer_backoff_key(peer)
        self._sync_peer_backoff[key] = until
        self._sync_peer_backoff_reason[key] = reason

    def _peer_sync_score(self, peer: _PeerState) -> tuple[float, int, int]:
        total = peer.sync_successes + peer.sync_timeouts + peer.sync_failures
        success_rate = peer.sync_successes / max(1, total)
        return (success_rate, -peer.sync_timeouts, -peer.not_anchored_count)

    def _peer_broadcast_state(
        self, peer: _PeerState, *, now: Optional[float] = None
    ) -> tuple[float, str, bool]:
        now = time.time() if now is None else now
        b = peer.broadcast
        score = 0.0
        recent_cutoff = self._sync_peer_broadcast_recent_sec
        if b.last_inventory_at and now - b.last_inventory_at <= recent_cutoff:
            score += 2.0
        if b.last_head_advancement_at and now - b.last_head_advancement_at <= recent_cutoff:
            score += 2.0
        if b.successful_headers_served:
            score += min(3.0, b.successful_headers_served / 2.0)
        if b.successful_blocks_served:
            score += min(3.0, b.successful_blocks_served / 2.0)
        if b.tip_matches:
            score += min(2.0, float(b.tip_matches))
        score -= 0.5 * b.duplicate_header_batches
        score -= 1.0 * b.timeouts
        score -= 1.5 * b.errors

        non_broadcasting = False
        if now - peer.connected_at >= self._sync_peer_non_broadcasting_sec:
            recent_inventory = (
                b.last_inventory_at
                and now - b.last_inventory_at <= self._sync_peer_non_broadcasting_sec
            )
            recent_head = (
                b.last_head_advancement_at
                and now - b.last_head_advancement_at <= self._sync_peer_non_broadcasting_sec
            )
            has_progress = b.successful_headers_served > 0 or b.successful_blocks_served > 0
            if not recent_inventory and not recent_head and not has_progress:
                non_broadcasting = True

        if non_broadcasting:
            if b.non_broadcasting_since is None:
                b.non_broadcasting_since = now
        else:
            b.non_broadcasting_since = None

        classification = "good" if score >= 1.0 else "stale"
        if non_broadcasting:
            classification = "non_broadcasting"
        if classification != b.last_classification:
            if classification == "non_broadcasting":
                self._stats["peer_broadcast_stale"] += 1
            b.last_classification = classification
        return score, classification, non_broadcasting

    def _log_peer_broadcast_scores(self) -> None:
        now = time.time()
        if now - self._sync_last_peer_score_log_at < self._sync_peer_score_log_interval:
            return
        self._sync_last_peer_score_log_at = now
        if not self._peers:
            return
        scored: list[tuple[float, _PeerState, str]] = []
        for peer in self._peers.values():
            score, classification, _ = self._peer_broadcast_state(peer, now=now)
            scored.append((score, peer, classification))
        scored.sort(key=lambda item: item[0], reverse=True)
        top = [
            {
                "remote": peer.remote,
                "peer_id": peer.peer_id,
                "score": round(score, 3),
                "classification": classification,
                "last_inventory_s": round(now - peer.broadcast.last_inventory_at, 1)
                if peer.broadcast.last_inventory_at
                else None,
                "last_head_adv_s": round(now - peer.broadcast.last_head_advancement_at, 1)
                if peer.broadcast.last_head_advancement_at
                else None,
            }
            for score, peer, classification in scored[:5]
        ]
        log.info("Top peers by broadcast score", extra={"peers": top})

    def _select_sync_peer(
        self,
        *,
        avoid_peer: Optional[_PeerState] = None,
        avoid_remotes: Optional[set[str]] = None,
        allow_pow_backoff: bool = False,
        require_anchored: bool = False,
    ) -> Optional[_PeerState]:
        avoid_netgroup = avoid_peer.netgroup if avoid_peer else None
        eligible, _ = self._eligible_sync_peers(
            ignore_backoff_reason="consensus_mismatch_pow" if allow_pow_backoff else None
        )
        if not eligible:
            eligible, _ = self._eligible_sync_peers(ignore_backoff_reason="not_anchored")
        if require_anchored:
            anchored = [p for p in eligible if self._peer_is_anchored(p)]
            if anchored:
                eligible = anchored
        avoid_remotes = avoid_remotes or set()

        candidates: list[tuple[int, float, bool, _PeerState]] = []
        now = time.time()
        local_height, _ = self._local_head()
        local_height = int(local_height or 0)
        for p in eligible:
            if p.remote in avoid_remotes:
                continue
            if require_anchored and not self._peer_is_anchored(p):
                continue
            h, _head_hash = self._fresh_peer_head(p, now=now)
            broadcast_score, _classification, non_broadcasting = self._peer_broadcast_state(
                p, now=now
            )
            candidates.append((h, broadcast_score, non_broadcasting, p))
        if not candidates:
            log.debug(
                "PEER_NOT_ACTIVATED",
                extra={
                    "reason": "no_candidates_after_filter",
                    "require_anchored": require_anchored,
                    "avoid_remotes_count": len(avoid_remotes),
                },
            )
            return None
        max_height = max(h for h, _score, _nb, _ in candidates)
        floor_height = max(local_height + 1, max_height - 2)
        height_filtered = [
            (h, score, non_broadcasting, peer)
            for h, score, non_broadcasting, peer in candidates
            if h >= floor_height
        ]
        if not height_filtered:
            height_filtered = list(candidates)
        if any(
            not non_broadcasting
            for _h, _score, non_broadcasting, _peer in height_filtered
        ):
            height_filtered = [
                (h, score, non_broadcasting, peer)
                for h, score, non_broadcasting, peer in height_filtered
                if not non_broadcasting
            ]
        scored: list[tuple[tuple[float, ...], _PeerState]] = []
        for head_height, broadcast_score, non_broadcasting, p in height_filtered:
            latency = p.latency_ewma if p.latency_ewma is not None else 9999.0
            force_bonus = 1 if self._is_force_sync_remote(p.remote) else 0
            outbound_bonus = 1 if p.direction == "outbound" else 0
            netgroup_penalty = 1 if avoid_netgroup and p.netgroup == avoid_netgroup else 0
            sync_score = self._peer_sync_score(p)
            anchored_bonus = 1 if self._peer_is_anchored(p) else 0
            proven_headers_bonus = 1 if p.broadcast.successful_headers_served > 0 else 0
            lag_delta = max(0, int(head_height) - local_height)
            capped_delta = min(lag_delta, 64)
            non_broadcasting_penalty = -5.0 if non_broadcasting else 0.0
            recent_header_bonus = (
                1.0
                if p.broadcast.last_head_advancement_at
                and now - p.broadcast.last_head_advancement_at
                <= self._sync_peer_broadcast_recent_sec
                else 0.0
            )
            score = (
                force_bonus,
                anchored_bonus,
                proven_headers_bonus,
                broadcast_score,
                recent_header_bonus,
                capped_delta,
                non_broadcasting_penalty,
                outbound_bonus,
                *sync_score,
                -p.misbehavior_score,
                -latency,
                -netgroup_penalty,
            )
            scored.append((score, p))
        if not scored:
            log.debug(
                "PEER_NOT_ACTIVATED",
                extra={
                    "reason": "no_scored_candidates",
                    "candidate_count": len(height_filtered),
                    "local_height": local_height,
                    "max_height": max_height,
                },
            )
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    def _select_block_peer(
        self,
        *,
        needed_height: Optional[int] = None,
        require_anchored: bool = False,
        avoid_remotes: Optional[set[str]] = None,
    ) -> Optional[_PeerState]:
        eligible, _ = self._eligible_block_peers()
        if require_anchored:
            anchored = [peer for peer in eligible if self._peer_is_anchored(peer)]
            if anchored:
                eligible = anchored
        if not eligible:
            return None
        avoid_remotes = avoid_remotes or set()

        candidates: list[tuple[int, float, bool, _PeerState]] = []
        now = time.time()
        local_height, _ = self._local_head()
        local_height = int(local_height or 0)
        for peer in eligible:
            if peer.remote in avoid_remotes:
                continue
            if require_anchored and not self._peer_is_anchored(peer):
                continue
            head_height, _head_hash = self._fresh_peer_head(peer, now=now)
            if head_height <= 0:
                continue
            if needed_height is not None and head_height < needed_height:
                continue
            broadcast_score, _classification, non_broadcasting = self._peer_broadcast_state(
                peer, now=now
            )
            candidates.append((head_height, broadcast_score, non_broadcasting, peer))
        if not candidates:
            return None
        max_height = max(h for h, _score, _nb, _ in candidates)
        floor_height = max(local_height + 1, max_height - 2)
        height_filtered = [
            (h, score, non_broadcasting, peer)
            for h, score, non_broadcasting, peer in candidates
            if h >= floor_height
        ]
        if not height_filtered:
            height_filtered = list(candidates)
        if any(
            not non_broadcasting
            for _head_height, _score, non_broadcasting, _peer in height_filtered
        ):
            height_filtered = [
                (h, score, non_broadcasting, peer)
                for h, score, non_broadcasting, peer in height_filtered
                if not non_broadcasting
            ]
        scored: list[tuple[tuple[float, ...], _PeerState]] = []
        for head_height, broadcast_score, non_broadcasting, peer in height_filtered:
            latency = peer.latency_ewma if peer.latency_ewma is not None else 9999.0
            force_bonus = 1 if self._is_force_sync_remote(peer.remote) else 0
            outbound_bonus = 1 if peer.direction == "outbound" else 0
            anchored_bonus = 1 if self._peer_is_anchored(peer) else 0
            sync_score = self._peer_sync_score(peer)
            proven_headers_bonus = 1 if peer.broadcast.successful_headers_served > 0 else 0
            proven_blocks_bonus = 1 if peer.broadcast.successful_blocks_served > 0 else 0
            lag_delta = max(0, int(head_height) - local_height)
            capped_delta = min(lag_delta, 64)
            block_failure_penalty = -1.0 * float(peer.block_failures or 0)
            non_broadcasting_penalty = -5.0 if non_broadcasting else 0.0
            recent_block_bonus = (
                1.0
                if peer.broadcast.last_head_advancement_at
                and now - peer.broadcast.last_head_advancement_at
                <= self._sync_peer_broadcast_recent_sec
                else 0.0
            )
            score = (
                force_bonus,
                anchored_bonus,
                proven_blocks_bonus,
                proven_headers_bonus,
                broadcast_score,
                recent_block_bonus,
                capped_delta,
                non_broadcasting_penalty,
                outbound_bonus,
                *sync_score,
                block_failure_penalty,
                -peer.misbehavior_score,
                -latency,
                peer.last_progress_at,
            )
            scored.append((score, peer))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        top_count = max(1, min(3, len(scored)))
        top_peers = [peer for _score, peer in scored[:top_count]]
        jittered = top_peers[:]
        random.shuffle(jittered)
        candidate = jittered[self._sync_block_peer_cursor % len(jittered)]
        self._sync_block_peer_cursor = (self._sync_block_peer_cursor + 1) % 1000000
        return candidate

    # ---------------------------------------------------------------------
    # Storage helpers
    # ---------------------------------------------------------------------

    def _block_db(self) -> Any:
        if self.deps is None:
            raise RuntimeError("P2P deps not set")
        if hasattr(self.deps, "block_db"):
            return getattr(self.deps, "block_db")
        if hasattr(self.deps, "_sync") and hasattr(self.deps._sync, "_block_db"):
            return getattr(self.deps._sync, "_block_db")
        if hasattr(self.deps, "_block_db"):
            return getattr(self.deps, "_block_db")
        raise RuntimeError("deps has no block_db")

    def _state_db(self) -> Any:
        if self.deps is None:
            raise RuntimeError("P2P deps not set")
        if hasattr(self.deps, "state_db"):
            return getattr(self.deps, "state_db")
        if hasattr(self.deps, "_state_db"):
            return getattr(self.deps, "_state_db")
        if hasattr(self.deps, "_sync") and hasattr(self.deps._sync, "_state_db"):
            return getattr(self.deps._sync, "_state_db")
        raise RuntimeError("deps has no state_db")

    def _snapshot_auto_enabled(self) -> bool:
        try:
            from core.snapshot.policy import SnapshotPolicy

            return SnapshotPolicy.from_env(chain_id=self.chain_id).auto_enabled
        except Exception:
            return False

    def _local_head(self) -> tuple[int, Optional[str]]:
        header = None
        height = None
        head_hash: Optional[str] = None
        try:
            if self.deps is not None:
                sync = getattr(self.deps, "_sync", None)
                if sync is not None and hasattr(sync, "head"):
                    height, header = sync.head()
                elif hasattr(self.deps, "head"):
                    height, header = self.deps.head()
        except Exception:
            header = None
            height = None
        if header is not None:
            head_hash = self._header_hash_for_status(header)
            if head_hash:
                head_hash = self._canon_hash(head_hash)
                head_bytes = self._parse_hash_bytes(head_hash)
                try:
                    if head_bytes is not None and self._has_header(head_bytes):
                        return int(height or 0), head_hash
                except RuntimeError:
                    return int(height or 0), head_hash
                if not hasattr(self.deps, "block_db") and not hasattr(self.deps, "_block_db"):
                    return int(height or 0), head_hash
        try:
            bdb = self._block_db()
            head = None
            if hasattr(bdb, "get_canonical_head"):
                head = bdb.get_canonical_head()
            if head is None:
                head = bdb.get_head()
            if head:
                height = int(head[0])
                header = None
                if hasattr(bdb, "get_header_by_hash"):
                    header = bdb.get_header_by_hash(head[1])
                if header is None and hasattr(bdb, "get_header_by_height"):
                    header = bdb.get_header_by_height(height)
                if header is not None:
                    head_hash = self._header_hash_for_status(header)
                    if head_hash:
                        return height, self._canon_hash(head_hash)
                recovered = self._recover_head_from_canonical(height)
                if recovered is not None:
                    recovered_height, recovered_hash = recovered
                    return recovered_height, recovered_hash.hex()
                return height, bytes(head[1]).hex()
        except RuntimeError:
            if head_hash:
                return int(height or 0), head_hash
        except Exception:
            pass
        genesis = self._block_db().get_genesis_hash()
        if genesis and self._has_header(bytes(genesis)):
            return 0, bytes(genesis).hex()
        return 0, None

    def _recover_head_from_canonical(self, start_height: int) -> Optional[tuple[int, bytes]]:
        bdb = self._block_db()
        for height in range(int(start_height), -1, -1):
            h = None
            try:
                h = bdb.get_canonical_hash(height)
            except Exception:
                h = None
            if h and self._has_header(bytes(h)):
                return height, bytes(h)
        genesis = bdb.get_genesis_hash()
        if genesis and self._has_header(bytes(genesis)):
            return 0, bytes(genesis)
        return None

    def _is_peer_responsive(self, info: Optional[_PeerHeadInfo], now: float) -> bool:
        """
        Check if a peer is responsive (not stale and not in cooldown).

        Args:
            info: The peer head info to check
            now: Current timestamp

        Returns:
            True if peer is responsive, False otherwise
        """
        if info is None:
            return False
        if now - info.updated_at > self._sync_peer_head_stale_sec:
            return False
        if info.cooldown_until and info.cooldown_until > now:
            return False
        return True

    def _is_verifier_seed_peer(self, peer_remote: str) -> bool:
        """
        Check if a peer is one of the trusted verifier seed nodes.
        
        Args:
            peer_remote: The peer's remote address (e.g., "3.12.224.189:30333")
            
        Returns:
            True if the peer is a verifier seed, False otherwise
        """
        if not self._enable_verifier_seeds or not self._verifier_seed_nodes:
            return False

        try:
            host = self._extract_host(peer_remote).strip().lower()
        except Exception:
            return False
        if not host:
            return False
        return host in self._verifier_seed_nodes

    def _local_is_verifier_seed(self) -> bool:
        if not self._enable_verifier_seeds or not self._verifier_seed_ips:
            return False
        if not self._external_ip:
            return False
        return self._external_ip in self._verifier_seed_ips

    def _max_height_ahead_of_verifier(self) -> int:
        return max(0, int(self._sync_max_height_ahead_of_verifier))

    def _verifier_rewind_idle_elapsed(self, *, now: float) -> bool:
        idle_sec = max(0.0, float(self._sync_verifier_rewind_idle_sec))
        if idle_sec <= 0.0:
            return True
        last_progress = float(self._sync_last_progress_at or 0.0)
        return (now - last_progress) >= idle_sec

    def _get_max_verifier_height(self) -> Optional[int]:
        """
        Get the maximum height from verifier seed peers.
        
        Returns:
            Maximum height from verifier seeds, or None if no verifier seeds are present
        """
        if not self._enable_verifier_seeds or not self._verifier_seed_ips:
            return None
        
        verifier_heights: list[int] = []
        now = time.time()
        
        for peer in self._peers.values():
            if not peer.hello_done.is_set():
                continue
            if not peer.repo_state_ok:
                continue
            
            info = self._sync_peer_heads.get(peer.remote)
            if self._is_peer_responsive(info, now):
                is_verifier = self._is_verifier_seed_peer(peer.remote)
                if is_verifier:
                    peer_height = int(info.height)
                    verifier_heights.append(peer_height)
        
        if self._local_is_verifier_seed():
            local_height, _ = self._local_head()
            verifier_heights.append(int(local_height))

        if not verifier_heights:
            return None
        
        return max(verifier_heights)

    def _check_and_discount_blocks_past_verifier(self) -> None:
        """
        Check if local chain has blocks past the verifier's highest height.
        If so, discount those blocks and reset the chain back to the verifier's height.
        
        This ensures the node doesn't get stuck on blocks that are ahead of the
        trusted verifier seeds, which could indicate a fork or invalid chain.
        
        This check only triggers when:
        1. Verifier seeds are enabled
        2. At least one verifier seed is present and responsive
        3. Local height is GREATER than verifier height (not equal)
        4. Local height remains above verifier bounds past grace and idle windows
        """
        if not self._enable_verifier_seeds:
            return
        if self._local_is_verifier_seed():
            # Verifier nodes are authoritative anchors and must never auto-rewind
            # from peer-reported verifier limits.
            self._sync_verifier_ahead_since = None
            return
        
        max_verifier_height = self._get_max_verifier_height()
        if max_verifier_height is None:
            # No verifier seeds present - skip check
            self._sync_verifier_ahead_since = None
            return
        
        local_height, _local_hash = self._local_head()
        
        now = time.time()
        if local_height <= max_verifier_height:
            self._sync_verifier_ahead_since = None
            return

        max_ahead = self._max_height_ahead_of_verifier()

        # Allow a brief grace period for temporary near-tip jitter. If we remain
        # above the verifier tip after the grace window, trigger rewind to
        # converge nodes automatically.
        if local_height <= max_verifier_height + max_ahead:
            if self._sync_verifier_ahead_since is None:
                self._sync_verifier_ahead_since = now
                return
            if (
                self._sync_verifier_rewind_grace_sec > 0.0
                and (now - self._sync_verifier_ahead_since)
                < self._sync_verifier_rewind_grace_sec
            ):
                return
        else:
            # Materially above verifier bound is suspicious; mark ahead start.
            self._sync_verifier_ahead_since = now

        # Only rewind when we've been idle for long enough; this avoids regressing
        # an actively progressing chain during temporary verifier lag.
        if not self._verifier_rewind_idle_elapsed(now=now):
            return

        # Local chain is ahead of verifier - this shouldn't happen
        # Log and reset to verifier-constrained height.
        log.warning(
            "Local chain exceeds verifier highest height - discounting blocks and resetting",
            extra={
                "local_height": local_height,
                "max_verifier_height": max_verifier_height,
                "blocks_to_discount": local_height - max_verifier_height,
            }
        )

        # Enforce canonical height anchored to verifier tip (plus configured jitter window).
        # This ensures we actively reorg down any canonical blocks that exceed the
        # verifier-constrained limit instead of only scheduling background sync.
        with contextlib.suppress(Exception):
            self._reorg_canonical_to_verifier_limit(max_verifier_height)

        # Clear the sync state to force resync from verifier's constrained height.
        self._reset_from_highest_next_height(
            reason="blocks_past_verifier_height"
        )
        self._sync_verifier_ahead_since = None

    def _reorg_canonical_to_verifier_limit(self, max_verifier_height: int) -> bool:
        local_height, _ = self._local_head()
        max_allowed_height = int(max_verifier_height) + int(self._max_height_ahead_of_verifier())
        if int(local_height or 0) <= max_allowed_height:
            return False

        bdb = self._block_db()
        target_height = max_allowed_height
        target_hash = bdb.get_canonical_hash(target_height)
        if target_hash is None and target_height > int(max_verifier_height):
            target_height = int(max_verifier_height)
            target_hash = bdb.get_canonical_hash(target_height)
        if target_hash is None:
            recovered = self._recover_head_from_canonical(int(max_verifier_height))
            if recovered is None:
                return False
            target_height, target_hash = recovered
        else:
            target_hash = bytes(target_hash)

        batch_fn = getattr(getattr(bdb, "kv", None), "batch", None)
        if callable(batch_fn):
            with bdb.kv.batch() as batch:
                bdb.set_canonical_head(
                    int(target_height),
                    bytes(target_hash),
                    batch=batch,
                    allow_reorg=True,
                )
                self._prune_canonical_heights(bdb, above_height=int(target_height), batch=batch)
        else:
            bdb.set_canonical_head(int(target_height), bytes(target_hash), allow_reorg=True)
            self._prune_canonical_heights(bdb, above_height=int(target_height), batch=None)

        log.warning(
            "Reorged canonical chain down to verifier-constrained height",
            extra={
                "local_height": int(local_height or 0),
                "max_verifier_height": int(max_verifier_height),
                "max_allowed_height": int(max_allowed_height),
                "target_height": int(target_height),
                "target_hash": bytes(target_hash).hex(),
            },
        )
        return True

    def invalidate_block(self, block_hash: str) -> bool:
        self._sync_last_block_error = f"invalidated:{block_hash}"
        self._sync_last_block_error_at = time.time()
        return True

    def rewind_to_common_ancestor(self, peer_chain: list[str]) -> bool:
        self._reset_from_highest_next_height(reason="force_canonical_rewind")
        self._sync_inflight_blocks.clear()
        self._sync_inflight_peers.clear()
        self._sync_inflight_block_requests.clear()
        return True

    def _select_sync_target_peer(self) -> Optional[_PeerState]:
        """Pick the peer to (re)sync toward for a forced canonical reorg.

        Returns the connected peer with the highest fresh head. Prefers the
        normal sync-eligible best peer; if every ahead peer is currently
        filtered out (penalized / in sync backoff / cooldown), fall back to the
        highest-head hello-done peer so an operator-forced reorg can still make
        progress against a peer the routine sync loop would skip. Returns None
        when no connected peer reports a fresh head (``force-canonical`` then
        reports ``no_peer``).

        NOTE: this was referenced by ``force_canonical_reorg`` but never defined
        (introduced in 59e50aa4), so ``debug force-canonical`` raised
        ``AttributeError: 'P2PService' object has no attribute
        '_select_sync_target_peer'`` on 6.0.3/6.0.4.
        """
        best_peer, _height, _head_hash = self._best_peer_head()
        if best_peer is not None:
            return best_peer
        now = time.time()
        fallback: Optional[_PeerState] = None
        fallback_height = 0
        for peer in self._peers.values():
            if not peer.hello_done.is_set():
                continue
            height, _hh = self._fresh_peer_head(peer, now=now)
            if height > fallback_height:
                fallback_height = height
                fallback = peer
        return fallback

    def force_canonical_reorg(self, *, force: bool = False) -> dict[str, Any]:
        if not force:
            return {"success": False, "error": "--force required"}
        local_height, _local_hash = self._local_head()
        best = self._select_sync_target_peer()
        if best is None:
            return {"success": False, "error": "no_peer"}
        best_height = self._peer_sync_head_height(best, now=time.time())
        if best_height <= int(local_height or 0):
            return {"success": True, "changed": False, "local_height": local_height, "peer_height": best_height}
        self.invalidate_block(_local_hash or "")
        self.rewind_to_common_ancestor([])
        self._sync_active_block_peer = best.remote
        self._sync_kick(reason="force_canonical", aggressive=True)
        return {"success": True, "changed": True, "local_height": local_height, "peer_height": best_height, "peer": best.remote}

    def _network_best_height(self) -> Optional[int]:
        """
        Compute the highest height we know about in the network.

        This considers:
        1. Direct peer heights (head_height)
        2. Peer's network views (network_best_height) - enabling multi-hop propagation
        3. Verifier seed constraints: non-verifier peers can only be a few blocks ahead

        Only heights from responsive peers (not stale or in cooldown) are considered.
        This prevents unresponsive high-height nodes from blocking chain reorganization
        to active seed nodes.
        
        When verifier seeds are enabled, the network best height is constrained by:
        - If verifier seeds are present: max(verifier_heights) + allowance (to absorb jitter)
        - Otherwise: max of all peer heights (backward compatible)
        """
        heights: list[int] = []
        verifier_heights: list[int] = []
        now = time.time()
        
        for peer in self._peers.values():
            if not peer.hello_done.is_set():
                continue
            if not peer.repo_state_ok:
                continue
            if self._enforce_outbound_only_policy_for_peer(peer):
                continue
            # Only count peers PROVEN to serve valid headers on OUR chain. A
            # different-genesis / dead-fork peer runs ahead on its own branch and
            # even passes the (optimistic) anchored check, but it never serves us a
            # valid header (genesis_mismatch at sync), so successful_headers_served
            # stays 0. Excluding such peers stops the phantom (e.g. 25718) from
            # poisoning net_best — which otherwise makes the node believe it is
            # behind, wedge, and (via the mining gate) halt mining while it is
            # actually AT the canonical tip. If no proven peer is ahead, net_best
            # is None and the node treats itself as at-tip (mining allowed).
            bc = getattr(peer, "broadcast", None)
            if bc is None or int(getattr(bc, "successful_headers_served", 0)) <= 0:
                continue
            info = self._sync_peer_heads.get(peer.remote)

            # Check if peer is responsive (not stale and not in cooldown)
            if self._is_peer_responsive(info, now):
                peer_height = int(info.height)
                is_verifier = self._is_verifier_seed_peer(peer.remote)
                
                # Track verifier seed heights separately
                if is_verifier:
                    verifier_heights.append(peer_height)
                
                # Add peer's direct height
                heights.append(peer_height)
                # NOTE: peers-of-peers network_best_height views are intentionally
                # NOT counted — they propagate a dead fork's height even through
                # anchored peers, re-poisoning net_best. Direct heights from
                # anchored (same-chain) peers are authoritative for our tip.
        
        if not heights:
            return None

        # Apply verifier seed constraint if enabled and verifier seeds are present
        if self._enable_verifier_seeds and verifier_heights:
            max_verifier_height = max(verifier_heights)
            max_ahead = self._max_height_ahead_of_verifier()
            # Filter heights to only allow up to max_ahead blocks ahead of the highest verifier
            # This ensures the network is anchored to the verifier seeds' highest height, while allowing
            # miners who just found one or a few blocks to be slightly ahead.
            # Any peer claiming to be more than max_ahead blocks ahead is ignored.
            max_allowed_height = max_verifier_height + max_ahead
            constrained_heights = [h for h in heights if h <= max_allowed_height]
            
            if constrained_heights:
                constrained_max = max(constrained_heights)
                unconstrained_max = max(heights)
                
                # Log when we constrain heights
                if constrained_max < unconstrained_max:
                    log.info(
                        "Network height constrained by verifier seeds",
                        extra={
                            "max_verifier_height": max_verifier_height,
                            "unconstrained_height": unconstrained_max,
                            "constrained_height": max_allowed_height,
                            "verifier_count": len(verifier_heights),
                        }
                    )
                
                # Keep a minimal +1 forward target so miners can advance even if peers
                # have not yet echoed the next block.
                return max(constrained_max, max_verifier_height + 1)
            else:
                # If all heights are filtered out, fall back to verifier max + 1.
                return max_verifier_height + 1

        # No verifier constraint, return max height
        return max(heights)

    def _is_sync_target_ahead(self, local_height: int) -> bool:
        local_height_int = int(local_height or 0)
        target_height: Optional[int] = (
            int(self._sync_target_height)
            if self._sync_target_height is not None
            else None
        )
        network_best_height = self._network_best_height()
        if network_best_height is not None:
            target_height = max(int(network_best_height), int(target_height or 0))
        best_header_height = (
            int(self._sync_best_header.height)
            if self._sync_best_header is not None
            else local_height_int
        )
        if target_height is None:
            target_height = best_header_height
        else:
            target_height = max(int(target_height), best_header_height)
        # Keep +1 tolerance for near-tip jitter.
        return int(target_height) > local_height_int + 1

    def get_verifier_seed_status(self) -> dict[str, Any]:
        """
        Get status information about verifier seed peers.
        
        Returns a dict with:
        - enabled: Whether verifier seeds are enabled
        - configured_ips: List of configured verifier seed IPs
        - connected_verifiers: List of connected verifier seed peers with their heights
        - max_verifier_height: Maximum height among connected verifiers (None if none connected)
        - max_allowed_height: Maximum height allowed for mining (max_verifier + allowance)
        - local_height: Current local chain height
        - local_is_verifier_seed: Whether this node is a configured verifier seed
        - can_mine: Whether mining is allowed based on verifier constraints
        """
        local_height, local_hash = self._local_head()
        max_verifier = self._get_max_verifier_height()
        local_is_verifier_seed = self._local_is_verifier_seed()
        
        connected_verifiers = []
        now = time.time()
        
        if self._enable_verifier_seeds and self._verifier_seed_ips:
            for peer in self._peers.values():
                if not peer.hello_done.is_set():
                    continue
                if not peer.repo_state_ok:
                    continue
                
                if self._is_verifier_seed_peer(peer.remote):
                    info = self._sync_peer_heads.get(peer.remote)
                    if info and self._is_peer_responsive(info, now):
                        connected_verifiers.append({
                            "remote": peer.remote,
                            "height": int(info.height),
                            "head_hash": "0x" + info.head_hash.hex() if info.head_hash else None,
                        })
        
        max_ahead = self._max_height_ahead_of_verifier()
        max_allowed = None if max_verifier is None else max_verifier + max_ahead
        
        # Can mine if:
        # 1. Verifier seeds are disabled, OR
        # 2. No verifiers connected (backward compatible), OR
        # 3. Local height is at verifier height or within max-ahead allowance
        can_mine = (
            not self._enable_verifier_seeds
            or max_verifier is None
            or local_height <= (max_verifier + max_ahead)
        )
        
        return {
            "enabled": self._enable_verifier_seeds,
            "configured_ips": sorted(list(self._verifier_seed_ips)) if self._verifier_seed_ips else [],
            "configured_hosts": sorted(list(self._verifier_seed_hosts)) if self._verifier_seed_hosts else [],
            "connected_verifiers": connected_verifiers,
            "max_verifier_height": max_verifier,
            "max_allowed_height": max_allowed,
            "local_height": local_height,
            "local_is_verifier_seed": local_is_verifier_seed,
            "can_mine": can_mine,
        }

    def _register_header_request(
        self,
        peer: _PeerState,
        *,
        locator: list[bytes],
        max_headers: int,
        locator_mode: str,
        anchor_height: int,
        anchor_hash: Optional[bytes],
        request_start_height: int,
    ) -> str:
        request_id = uuid.uuid4().hex
        peer.pending_header_request_id = request_id
        started_at = time.monotonic()
        deadline = started_at + max(1.0, self._sync_request_timeout)
        self._sync_inflight_header_requests[(peer.remote, request_id)] = _SyncRequest(
            request_id=request_id,
            peer_id=peer.remote,
            kind="headers",
            started_at=started_at,
            deadline_at=deadline,
            start_height=request_start_height,
            count=max_headers,
            locator=locator,
            locator_mode=locator_mode,
            anchor_height=anchor_height,
            anchor_hash=anchor_hash,
        )
        self._sync_inflight_headers = len(self._sync_inflight_header_requests)
        return request_id

    def _enqueue_header_retry(
        self,
        *,
        peer: Optional[_PeerState],
        locator: list[bytes],
        locator_mode: str,
        anchor_height: int,
        anchor_hash: Optional[bytes],
        request_start_height: int,
        max_headers: int,
        reason: str,
    ) -> None:
        request_id = uuid.uuid4().hex
        started_at = time.monotonic()
        deadline = started_at + max(1.0, self._sync_request_timeout)
        self._sync_header_retry_queue.append(
            _SyncRequest(
                request_id=request_id,
                peer_id=peer.remote if peer else "",
                kind="headers",
                started_at=started_at,
                deadline_at=deadline,
                retry_count=0,
                start_height=request_start_height,
                count=max_headers,
                locator=locator,
                locator_mode=locator_mode,
                anchor_height=anchor_height,
                anchor_hash=anchor_hash,
            )
        )
        log.info(
            "Header retry enqueued",
            extra={
                "reason": reason,
                "peer": peer.remote if peer else None,
                "request_id": request_id,
                "start_height": request_start_height,
                "count": max_headers,
                "locator_mode": locator_mode,
            },
        )

    def _adjust_header_batch(self, *, success: bool, reason: str) -> None:
        if success:
            if self._sync_headers_batch_current >= self._sync_headers_batch_max:
                return
            increment = max(1, self._sync_headers_batch_current // 4)
            updated = min(
                self._sync_headers_batch_max,
                self._sync_headers_batch_current + increment,
            )
        else:
            if self._sync_headers_batch_current <= self._sync_headers_batch_min:
                return
            updated = max(
                self._sync_headers_batch_min,
                max(1, self._sync_headers_batch_current // 2),
            )
        if updated != self._sync_headers_batch_current:
            log.info(
                "Adjusted header batch size",
                extra={
                    "from": self._sync_headers_batch_current,
                    "to": updated,
                    "reason": reason,
                    "success": success,
                },
            )
            self._sync_headers_batch_current = updated

    def _clear_header_request(self, peer: _PeerState) -> None:
        request_id = peer.pending_header_request_id
        if request_id:
            self._sync_inflight_header_requests.pop((peer.remote, request_id), None)
        peer.pending_header_request_id = None
        self._sync_inflight_headers = len(self._sync_inflight_header_requests)

    def _match_header_response(self, peer: _PeerState) -> bool:
        request_id = peer.pending_header_request_id
        if not request_id:
            return False
        return (peer.remote, request_id) in self._sync_inflight_header_requests

    def _expire_inflight_headers(self) -> None:
        if not self._sync_inflight_header_requests:
            return
        now = time.monotonic()
        watchdog_timeout = max(1.0, float(self._sync_header_watchdog_timeout))
        expired: list[tuple[str, str]] = []
        for key, request in list(self._sync_inflight_header_requests.items()):
            age_s = now - request.started_at
            if now >= request.deadline_at or age_s >= watchdog_timeout:
                expired.append(key)
        for remote, request_id in expired:
            request = self._sync_inflight_header_requests.pop((remote, request_id), None)
            peer = self._peer_by_remote(remote)
            timeout_reason = "headers_timeout"
            if request is not None and now - request.started_at >= watchdog_timeout:
                timeout_reason = "headers_watchdog_timeout"
            if peer and peer.pending_header_request_id == request_id:
                peer.pending_header_request_id = None
                fut = peer.pending_headers
                peer.pending_headers = None
                if fut is not None and not fut.done():
                    fut.set_result(None)
                self._penalize_peer(peer, timeout_reason, nonfatal=True)
                peer.sync_timeouts += 1
                self._set_sync_backoff(peer, reason=timeout_reason, delay=5.0)
                if request is not None:
                    request.retry_count += 1
                    self._sync_header_retry_queue.append(request)
                    self._stats["headers_req_timeout"] += 1
                    self._mark_peer_head_issue(peer, reason=timeout_reason)
                    log.warning(
                        "Header request expired",
                        extra={
                            "request_id": request.request_id,
                            "peer": request.peer_id,
                            "kind": request.kind,
                            "age_s": round(now - request.started_at, 3),
                            "timeout_reason": timeout_reason,
                            "retry_count": request.retry_count,
                            "start_height": request.start_height,
                            "count": request.count,
                            "locator_mode": request.locator_mode,
                        },
                    )
        if expired:
            self._sync_inflight_headers = len(self._sync_inflight_header_requests)
            log.info(
                "Expired in-flight header requests",
                extra={"count": len(expired)},
            )
            self._sync_kick(reason="headers_timeout", aggressive=False)

    def _ensure_sync_cursor_integrity(self) -> None:
        head_height, head_hash = self._local_head()
        head_bytes: Optional[bytes] = self._parse_hash_bytes(head_hash)
        head_missing = head_bytes is not None and not self._has_header(head_bytes)
        best_missing = (
            self._sync_best_header is not None
            and not self._has_header(self._sync_best_header.hash)
        )
        best_disconnected = self._sync_cursor_disconnected_from_local_head(
            head_height=int(head_height or 0),
            head_hash=head_bytes,
        )
        if not head_missing and not best_missing and not best_disconnected:
            return
        recovered = None
        if head_bytes is None and head_height >= 0:
            recovered = self._recover_head_from_canonical(head_height)
        elif head_bytes is not None and head_missing:
            recovered = self._recover_head_from_canonical(head_height)
        if recovered is not None:
            head_height, head_bytes = recovered
        log.warning(
            "sync: reset cursor to canonical head",
            extra={
                "reason": "disconnected_best_header"
                if best_disconnected
                else "missing_header",
                "head_height": head_height,
                "head_hash": head_hash,
                "best_header_height": self._sync_best_header.height if self._sync_best_header else None,
                "best_header_hash": self._sync_best_header.hash.hex() if self._sync_best_header else None,
            },
        )
        self._sync_inflight_blocks.clear()
        self._sync_inflight_peers.clear()
        self._sync_inflight_block_requests.clear()
        self._sync_inflight_header_requests.clear()
        self._sync_inflight_headers = 0
        self._sync_active_header_peer = None
        self._sync_active_block_peer = None
        self._sync_header_queue.clear()
        self._sync_header_retry_queue.clear()
        self._sync_headers.clear()
        self._sync_header_sources.clear()
        self._sync_header_votes.clear()
        self._sync_last_locator_info = []
        self._sync_last_locator_at = 0.0
        self._sync_last_locator_summary = None
        self._sync_duplicate_header_ranges.clear()
        self._sync_last_headers_accepted_count = 0
        self._sync_last_headers_discarded_count = 0
        self._sync_last_headers_discard_reason_counts = {}
        self._sync_headers_accepted_total = 0
        self._sync_headers_seen_total = 0
        self._sync_zero_accept_batches = 0
        self._sync_zero_accept_last_at = 0.0
        self._sync_block_queue.clear()
        self._sync_block_queue_set.clear()
        self._sync_block_queue_heights.clear()
        self._sync_block_retry_counts.clear()
        self._sync_pow_mismatch_votes.clear()
        self._sync_block_peer_cursor = 0
        for peer in self._peers.values():
            peer.pending_headers = None
            peer.pending_header_request_id = None
        self._sync_best_header = None
        if head_bytes is not None:
            hdr = self._sync_header_by_hash(head_bytes)
            if hdr is not None:
                self._sync_best_header = hdr

    def _header_hash_for_status(self, header: Any) -> Optional[str]:
        if isinstance(header, (bytes, bytearray, memoryview)):
            raw = bytes(header)
            if raw:
                return "0x" + raw.hex()
        try:
            if hasattr(header, "hash"):
                return "0x" + bytes(header.hash()).hex()
        except Exception:
            pass
        try:
            from core.encoding.cbor import dumps as _cbor_dumps
            from core.utils.hash import sha3_256

            return "0x" + sha3_256(_cbor_dumps(header)).hex()
        except Exception:
            pass
        return None

    def _genesis_hash(self) -> bytes:
        expected = None
        if self.deps is not None:
            expected = getattr(self.deps, "expected_genesis_hash", None)
            if not expected and hasattr(self.deps, "_sync"):
                expected = getattr(self.deps._sync, "expected_genesis_hash", None)
        if expected:
            return bytes(expected)
        bdb = self._block_db()
        g = bdb.get_genesis_hash()
        if g:
            return bytes(g)
        h0 = bdb.get_canonical_hash(0)
        if h0:
            return bytes(h0)
        params_hash = self._genesis_hash_from_params()
        if params_hash:
            return params_hash
        return b"\x00" * 32

    def _genesis_identity(self) -> bytes:
        return self._genesis_hash()

    def _genesis_header_hash(self) -> bytes:
        return self._genesis_hash()

    def _genesis_block_hash(self) -> bytes:
        if self.deps is not None:
            db_hash = getattr(self.deps, "db_genesis_hash", None)
            if db_hash:
                return bytes(db_hash)
        return self._genesis_hash()

    def _fork_id(self) -> int:
        if self.deps is not None:
            fork_id = getattr(self.deps, "fork_id", None)
            if fork_id is None and hasattr(self.deps, "_sync"):
                fork_id = getattr(self.deps._sync, "fork_id", None)
            if fork_id is not None:
                return int(fork_id)
        try:
            from core.chain.identity import derive_fork_id

            return derive_fork_id(self._genesis_hash())
        except Exception:
            return 0

    def _consensus_id(self) -> str:
        if self.deps is not None:
            consensus_id = getattr(self.deps, "consensus_id", None)
            if consensus_id is None and hasattr(self.deps, "_sync"):
                consensus_id = getattr(self.deps._sync, "consensus_id", None)
            if consensus_id:
                return str(consensus_id)
        try:
            from core.chain.identity import consensus_id_from_runtime

            return consensus_id_from_runtime(
                chain_id=int(self.chain_id), genesis_hash=self._genesis_hash()
            )
        except Exception:
            return "poies/unknown"

    def _protocol_version(self) -> str:
        if self.deps is not None:
            protocol_version = getattr(self.deps, "protocol_version", None)
            if protocol_version is None and hasattr(self.deps, "_sync"):
                protocol_version = getattr(self.deps._sync, "protocol_version", None)
            if protocol_version:
                return str(protocol_version)
        try:
            from core.chain.identity import protocol_version_from_runtime

            return protocol_version_from_runtime()
        except Exception:
            return "1.0"

    def _network_params_hash(self) -> bytes:
        try:
            from core.network_params import compute_network_params_hash

            return compute_network_params_hash(self.chain_id)
        except Exception:
            return b"\x00" * 32

    def _genesis_hash_from_params(self) -> Optional[bytes]:
        params = getattr(self.deps, "params", None)
        if params is None and hasattr(self.deps, "_sync"):
            params = getattr(self.deps._sync, "params", None)
        if params is not None:
            if hasattr(params, "genesis_hash"):
                gh = getattr(params, "genesis_hash")
                if isinstance(gh, (bytes, bytearray)):
                    return bytes(gh)
                if isinstance(gh, str):
                    with contextlib.suppress(ValueError):
                        return bytes.fromhex(gh[2:] if gh.startswith("0x") else gh)
            if isinstance(params, dict):
                genesis = params.get("genesis") if isinstance(params.get("genesis"), dict) else {}
                gh = genesis.get("hash") or params.get("genesis_hash")
                if isinstance(gh, str):
                    with contextlib.suppress(ValueError):
                        return bytes.fromhex(gh[2:] if gh.startswith("0x") else gh)
        try:
            from core.types.params import load_default_params

            loaded = load_default_params(chain_id_hint=self.chain_id)
            return bytes(loaded.genesis_hash)
        except Exception:
            return None

    def _headers_after_locator(self, locator: list[bytes], *, limit: int) -> list[Any]:
        from p2p.wire.messages import HeaderCompact

        anchor_height, anchor_hash, head_height, db_head_height, chain_headers = self._locate_anchor(locator)
        bdb = self._block_db()
        start = anchor_height

        out: list[Any] = []
        lim = max(1, min(int(limit), 512))
        for n in range(start + 1, min(head_height + 1, start + 1 + lim)):
            if n in chain_headers:
                hdr = chain_headers[n]
                out.append(
                    HeaderCompact(
                        hash=bytes(hdr.hash),
                        height=int(hdr.height),
                        parent=bytes(hdr.parent_hash),
                        theta_micro=int(hdr.theta_micro),
                        timestamp=int(
                            maybe_normalize_unix_timestamp_seconds(hdr.timestamp) or 0
                        ),
                    )
                )
                continue
            if n > db_head_height:
                break
            hdr = bdb.get_header_by_height(n)
            if hdr is None:
                break
            out.append(
                HeaderCompact(
                    hash=hdr.hash(),
                    height=int(hdr.height),
                    parent=bytes(hdr.parentHash),
                    theta_micro=int(getattr(hdr, "thetaMicro", 0)),
                    timestamp=int(
                        maybe_normalize_unix_timestamp_seconds(
                            getattr(hdr, "timestamp", 0)
                        )
                        or 0
                    ),
                )
            )
        return out

    def _locate_anchor(
        self, locator: list[bytes]
    ) -> tuple[int, Optional[bytes], int, int, dict[int, _SyncHeader]]:
        bdb = self._block_db()
        head = self._safe_db_head(bdb)
        if not head:
            genesis = bdb.get_canonical_hash(0) or bdb.get_genesis_hash() or self._genesis_hash()
            return 0, bytes(genesis) if genesis else None, 0, 0, {}
        db_head_height = int(head[0])
        chain_headers: dict[int, _SyncHeader] = {}
        best_header = self._sync_best_header
        if best_header is not None and best_header.height > db_head_height:
            cursor = best_header
            seen: set[bytes] = set()
            while cursor is not None and cursor.hash not in seen:
                seen.add(cursor.hash)
                chain_headers[int(cursor.height)] = cursor
                if cursor.height <= 0:
                    break
                parent = self._sync_header_by_hash(cursor.parent_hash)
                if parent is None:
                    break
                cursor = parent
        head_height = max(db_head_height, max(chain_headers.keys(), default=db_head_height))

        # ANM-L08 (event-loop DoS fix): resolve the anchor by walking the peer's
        # locator (bounded — a header locator is a few dozen hashes) instead of
        # scanning EVERY height from the head down to genesis. The previous code
        # did one synchronous SQLite read per height, so a peer sending a locator
        # full of unknown/foreign-chain hashes forced an O(chain-height) walk —
        # tens of thousands of blocking DB reads on the single event loop per
        # GET_HEADERS request — silently pegging the loop and stalling block
        # production + RPC (and growing worse as the chain lengthens). We now do
        # at most one header lookup + one canonical check per locator entry, and
        # pick the highest-height locator hash that is on our canonical chain —
        # identical semantics, bounded cost.
        above_db_by_hash = {bytes(ch.hash): int(h) for h, ch in chain_headers.items()}
        max_scan = int(os.environ.get("ANIMICA_P2P_MAX_LOCATOR_SCAN", "512") or 512)
        anchor_height = 0
        anchor_hash: Optional[bytes] = None
        best = -1
        checked = 0
        for h in locator:
            if not isinstance(h, (bytes, bytearray)):
                continue
            checked += 1
            if checked > max_scan:
                break
            hb = bytes(h)
            hh = above_db_by_hash.get(hb)
            if hh is None:
                hdr = bdb.get_header_by_hash(hb)
                if hdr is not None:
                    cand = int(getattr(hdr, "height", -1))
                    if 0 <= cand <= db_head_height and bdb.get_canonical_hash(cand) == hb:
                        hh = cand
            if hh is not None and hh > best:
                best = hh
                anchor_height = hh
                anchor_hash = hb
        if anchor_hash is None:
            genesis = bdb.get_canonical_hash(0) or bdb.get_genesis_hash() or self._genesis_hash()
            if genesis:
                anchor_height = 0
                anchor_hash = bytes(genesis)
        return anchor_height, anchor_hash, head_height, db_head_height, chain_headers

    def _build_headers_locator(self, max_entries: int = 32) -> list[bytes]:
        depth = max_entries + int(self._sync_locator_depth_hint or 0)
        locator = self._build_locator(max_entries=depth)
        if locator:
            return locator
        bdb = self._block_db()
        genesis = bdb.get_canonical_hash(0) or bdb.get_genesis_hash() or self._genesis_hash()
        if genesis:
            log.error("Empty header locator; falling back to genesis")
            return [bytes(genesis)]
        log.error("Empty header locator and no genesis hash available")
        return []

    def _refresh_locator_summary(self) -> None:
        try:
            locator = self._build_locator()
        except Exception:
            return
        locator_info = self._locator_debug(locator)
        self._sync_last_locator_info = locator_info
        self._sync_last_locator_at = time.time()
        self._sync_last_locator_summary = self._locator_summary(locator_info)

    def _locator_debug(self, locator: list[bytes]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for h in locator:
            if not isinstance(h, (bytes, bytearray)):
                continue
            meta = self._header_meta(bytes(h))
            out.append(
                {
                    "hash": bytes(h).hex(),
                    "height": meta[0] if meta else None,
                }
            )
        return out

    def _locator_summary(self, locator_info: list[dict[str, Any]]) -> dict[str, Any]:
        first = locator_info[0] if locator_info else None
        last = locator_info[-1] if locator_info else None
        return {
            "count": len(locator_info),
            "first": first,
            "last": last,
        }

    def _header_cooldown_snapshot(self) -> tuple[int, Optional[float]]:
        now = time.time()
        expiries = [
            peer.header_cooldown_until
            for peer in self._peers.values()
            if peer.header_cooldown_until and peer.header_cooldown_until > now
        ]
        expiries.extend(
            [
                until
                for remote, until in self._sync_peer_backoff.items()
                if until > now
                and self._sync_peer_backoff_reason.get(remote) == "not_anchored"
            ]
        )
        if not expiries:
            return 0, None
        return len(expiries), min(expiries)

    def _headers_debug_info(self, headers: list[HeaderCompact]) -> dict[str, Any]:
        if not headers:
            return {"count": 0}
        first = headers[0]
        last = headers[-1]
        return {
            "count": len(headers),
            "first_height": int(first.height),
            "first_hash": bytes(first.hash).hex(),
            "last_height": int(last.height),
            "last_hash": bytes(last.hash).hex(),
        }

    def _log_header_reject(
        self,
        peer: _PeerState,
        header: _SyncHeader,
        *,
        reason: str,
        parent_ts: Optional[int] = None,
    ) -> None:
        now = time.time()
        key = f"{peer.remote}:{reason}"
        last = self._sync_header_reject_log_at.get(key, 0.0)
        if now - last < self._sync_header_reject_log_min_s:
            return
        self._sync_header_reject_log_at[key] = now
        target = None
        pow_hash_int = None
        try:
            from core.chain.block_import import _theta_to_target

            target = _theta_to_target(int(header.theta_micro))
        except Exception:
            target = None
        try:
            pow_hash_int = int.from_bytes(header.hash, "big")
        except Exception:
            pow_hash_int = None
        adaptive_pow = int(self.chain_id) == 1 and int(header.height) >= 1
        log.warning(
            "Header rejected",
            extra={
                "remote": peer.remote,
                "reason": reason,
                "height": header.height,
                "hash": header.hash.hex(),
                "parent": header.parent_hash.hex(),
                "theta_micro": header.theta_micro,
                "target": target,
                "target_hex": hex(int(target)) if target is not None else None,
                "pow_hash_int": pow_hash_int,
                "timestamp": header.timestamp,
                "parent_timestamp": parent_ts,
                "adaptive_pow": adaptive_pow,
            },
        )

    def _track_duplicate_header_range(
        self, peer: _PeerState, headers: list[HeaderCompact]
    ) -> int:
        if not headers:
            return 0
        range_key = (
            bytes(headers[0].hash).hex(),
            bytes(headers[-1].hash).hex(),
            len(headers),
        )
        previous = self._sync_duplicate_header_ranges.get(peer.remote)
        if previous and previous[0] == range_key:
            count = previous[1] + 1
        else:
            count = 1
        self._sync_duplicate_header_ranges[peer.remote] = (range_key, count)
        return count

    def _reset_duplicate_header_range(self, peer: _PeerState) -> None:
        self._sync_duplicate_header_ranges.pop(peer.remote, None)

    def _canon_hash(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, (bytes, bytearray)):
            if len(value) != 32:
                return None
            return bytes(value).hex()
        if isinstance(value, str):
            cleaned = value.strip().lower()
            if cleaned.startswith("0x"):
                cleaned = cleaned[2:]
            if not cleaned:
                return None
            if len(cleaned) != 64:
                return None
            try:
                bytes.fromhex(cleaned)
            except ValueError:
                return None
            return cleaned
        return None

    def _canon_hash0x(self, value: Any) -> Optional[str]:
        canon = self._canon_hash(value)
        if canon is None:
            return None
        return f"0x{canon}"

    def _parse_hash_bytes(self, value: Any) -> Optional[bytes]:
        canon = self._canon_hash(value)
        if canon is None:
            return None
        return bytes.fromhex(canon)

    def _load_bootstrap_checkpoint(self) -> None:
        try:
            from animica.bootstrap.state import get_bootstrap_checkpoint
        except Exception as exc:
            log.debug("Bootstrap checkpoint loader unavailable", extra={"error": str(exc)})
            return
        try:
            bdb = self._block_db()
            head = self._safe_db_head(bdb)
            head_height = int(head[0]) if head else 0
        except Exception:
            head_height = 0
        if head_height > 10:
            return
        checkpoint = get_bootstrap_checkpoint(self.chain_id, str(self._chain_data_dir))
        if not checkpoint:
            return
        height, hash_hex = checkpoint
        hash_bytes = self._parse_hash_bytes(hash_hex)
        if height is None or not hash_bytes:
            return
        self._sync_checkpoint_height = int(height)
        self._sync_checkpoint_hash = hash_bytes
        self._sync_checkpoint_mode_enabled = True
        self._sync_checkpoint_validation = "unknown"
        self._sync_last_checkpoint_action = "loaded_from_bootstrap_cache"

    def _sync_cache_dir(self) -> Path:
        env_dir = os.environ.get("ANIMICA_SYNC_CACHE_DIR") or os.environ.get(
            "ANIMICA_P2P_SYNC_CACHE_DIR"
        )
        if env_dir:
            return Path(env_dir).expanduser()
        return self._chain_data_dir / "sync"

    def _init_sync_cache(self) -> None:
        if self._sync_cache is not None:
            return
        cache_dir = self._sync_cache_dir()
        config = SyncCacheConfig(
            base_dir=cache_dir,
            max_block_bytes=self._sync_cache_max_bytes,
            max_block_entries=self._sync_cache_max_blocks,
            max_header_entries=self._sync_cache_max_headers,
            state_flush_interval_sec=self._sync_cache_state_interval,
            prune_interval_sec=self._sync_cache_prune_interval,
        )
        self._sync_cache = SyncCacheStore(config)

    def _decode_cached_header(self, payload: dict[str, Any]) -> Optional[_SyncHeader]:
        try:
            hash_bytes = self._parse_hash_bytes(payload.get("hash"))
            parent_bytes = self._parse_hash_bytes(payload.get("parent_hash"))
            if not hash_bytes or not parent_bytes:
                return None
            ts = maybe_normalize_unix_timestamp_seconds(payload.get("timestamp") or 0)
            if ts is None:
                ts = 0
            return _SyncHeader(
                hash=hash_bytes,
                parent_hash=parent_bytes,
                height=int(payload.get("height") or 0),
                theta_micro=int(payload.get("theta_micro") or 0),
                timestamp=int(ts),
            )
        except Exception:
            return None

    def _load_sync_cache_state(self) -> None:
        self._init_sync_cache()
        if self._sync_cache is None:
            return
        state = self._sync_cache.load_state()
        self._sync_paused = bool(state.paused)
        self._update_sync_target_height(
            state.target_height, reason="load_sync_cache_state"
        )
        self._sync_peer_penalties.update(state.peer_penalties)
        self._sync_last_validated_height = int(state.last_validated_height or 0)
        for header_payload in state.headers:
            header = self._decode_cached_header(header_payload)
            if header is None:
                continue
            if header.hash not in self._sync_headers:
                self._sync_headers[header.hash] = header
        best_hash_hex = state.best_header_hash or ""
        best_hash = self._parse_hash_bytes(best_hash_hex)
        if best_hash:
            best = self._sync_headers.get(best_hash)
            if best is not None:
                self._sync_best_header = best
        for h_hex in state.block_queue:
            h = self._parse_hash_bytes(h_hex)
            if h is None:
                continue
            if self._has_block(h):
                continue
            if h in self._sync_block_queue_set:
                continue
            self._sync_block_queue.append(h)
            self._sync_block_queue_set.add(h)
        for h_hex, height in state.block_queue_heights.items():
            h = self._parse_hash_bytes(h_hex)
            if h is None:
                continue
            self._sync_block_queue_heights[h] = int(height)
        if state.headers or state.best_header_hash or state.block_queue:
            self._ensure_sync_cursor_integrity()

    def _build_sync_cache_state(self) -> SyncCacheState:
        head_height, _ = self._local_head()
        self._sync_last_validated_height = max(
            int(self._sync_last_validated_height or 0), int(head_height or 0)
        )
        headers = list(self._sync_headers.values())
        headers.sort(key=lambda h: h.height)
        if len(headers) > self._sync_cache_max_headers:
            headers = headers[-self._sync_cache_max_headers :]
        header_payloads = [
            {
                "hash": h.hash.hex(),
                "parent_hash": h.parent_hash.hex(),
                "height": h.height,
                "theta_micro": h.theta_micro,
                "timestamp": h.timestamp,
            }
            for h in headers
        ]
        best_hash = (
            self._sync_best_header.hash.hex()
            if self._sync_best_header is not None
            else None
        )
        block_queue = [h.hex() for h in self._sync_block_queue]
        block_queue_heights = {
            h.hex(): int(height)
            for h, height in self._sync_block_queue_heights.items()
        }
        return SyncCacheState(
            headers=header_payloads,
            best_header_hash=best_hash,
            block_queue=block_queue,
            block_queue_heights=block_queue_heights,
            peer_penalties=dict(self._sync_peer_penalties),
            last_validated_height=self._sync_last_validated_height,
            target_height=self._sync_target_height,
            paused=self._sync_paused,
        )

    def _flush_sync_cache_state(self) -> None:
        if self._sync_cache is None:
            return
        try:
            state = self._build_sync_cache_state()
            self._sync_cache.save_state(state)
        except Exception as exc:
            log.debug("Failed to flush sync cache state", extra={"error": str(exc)})

    def _clear_sync_cache(self) -> None:
        if self._sync_cache is not None:
            self._sync_cache.clear()
        self._sync_header_queue.clear()
        self._sync_header_retry_queue.clear()
        self._sync_inflight_header_requests.clear()
        self._sync_inflight_headers = 0
        self._sync_headers.clear()
        self._sync_best_header = None
        self._sync_block_queue.clear()
        self._sync_block_queue_set.clear()
        self._sync_block_queue_heights.clear()
        self._sync_block_retry_counts.clear()
        self._sync_block_buffer.clear()
        self._sync_inflight_blocks.clear()
        self._sync_inflight_peers.clear()
        self._sync_inflight_block_requests.clear()

    async def _sync_cache_loop(self) -> None:
        last_state_flush = 0.0
        last_prune = 0.0
        try:
            while self._running:
                now = time.time()
                if self._sync_cache is None:
                    await asyncio.sleep(1.0)
                    continue
                if now - last_state_flush >= self._sync_cache_state_interval:
                    self._flush_sync_cache_state()
                    last_state_flush = now
                if now - last_prune >= self._sync_cache_prune_interval:
                    with contextlib.suppress(Exception):
                        self._sync_cache.prune()
                    last_prune = now
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    def _empty_headers_reason(
        self,
        peer: _PeerState,
        local_height: int,
        remote_height: int,
        *,
        network_best_height: Optional[int],
        eligible_peer_count: int,
    ) -> str:
        hello = peer.hello or {}
        genesis_hash = bytes(hello.get("genesis_hash") or b"")
        if genesis_hash and genesis_hash != self._genesis_hash():
            return "genesis_mismatch"
        _best_peer, best_peer_height, _best_peer_hash = self._best_peer_head()
        max_observed_height = best_peer_height
        max_peer_height: Optional[int] = None
        for candidate in self._peers.values():
            if not candidate.hello_done.is_set():
                continue
            if not candidate.repo_state_ok:
                continue
            try:
                candidate_height = int((candidate.hello or {}).get("head_height") or 0)
            except Exception:
                continue
            if max_peer_height is None or candidate_height > max_peer_height:
                max_peer_height = candidate_height
        if (
            remote_height <= local_height
            and (network_best_height is None or network_best_height <= local_height)
            and (max_observed_height is None or max_observed_height <= local_height + 1)
        ):
            return "at_tip"
        if (
            remote_height <= local_height
            and network_best_height is not None
            and network_best_height > local_height
            and max_peer_height is not None
            and max_peer_height <= local_height
        ):
            now = time.time()
            if (
                self._sync_stale_network_best_at
                and now - self._sync_stale_network_best_at
                < self._sync_stale_network_best_cooldown
            ):
                self._sync_stale_network_best_count += 1
                return "at_tip"
            self._sync_stale_network_best_at = now
            self._sync_stale_network_best_count = 1
            return "stale_network_best"
        if remote_height <= local_height:
            return "peer_behind"
        return "headers_empty"

    def _record_sync_header_event(self, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload["at"] = time.time()
        self._sync_header_events.append(payload)

    def _sync_head_for_locator(self) -> tuple[int, Optional[bytes], str]:
        try:
            bdb = self._block_db()
        except Exception:
            bdb = None
        head = None
        if bdb is not None and hasattr(bdb, "get_canonical_head"):
            with contextlib.suppress(Exception):
                head = bdb.get_canonical_head()
        if head is None and bdb is not None:
            with contextlib.suppress(Exception):
                head = bdb.get_head()
        if head:
            height = int(head[0])
            head_hash = bytes(head[1])
            if self._has_header(head_hash):
                return height, head_hash, "canonical_head"
            recovered = self._recover_head_from_canonical(height)
            if recovered is not None:
                recovered_height, recovered_hash = recovered
                return recovered_height, recovered_hash, "canonical_recovered"
        local_height, local_hash_hex = self._local_head()
        if local_hash_hex:
            local_hash = self._parse_hash_bytes(local_hash_hex)
            if local_hash is not None:
                return int(local_height or 0), local_hash, "local_head"
        genesis = None
        if bdb is not None:
            genesis = bdb.get_canonical_hash(0) or bdb.get_genesis_hash()
        if genesis is None:
            genesis = self._genesis_hash()
        if genesis:
            return 0, bytes(genesis), "genesis"
        return 0, None, "unknown"

    def _build_locator(self, max_entries: int = 32) -> list[bytes]:
        bdb = self._block_db()
        head_height, head_hash, head_source = self._sync_head_for_locator()
        if head_hash is None:
            genesis = bdb.get_canonical_hash(0) or bdb.get_genesis_hash()
            if genesis:
                return [bytes(genesis)]
            return [self._genesis_hash()]

        start_hash = head_hash
        start_height = head_height

        if (
            self._sync_best_header
            and self._sync_best_header.height > head_height
            and not self._sync_cursor_disconnected_from_local_head(
                head_height=int(head_height),
                head_hash=head_hash,
            )
        ):
            start_hash = self._sync_best_header.hash
            start_height = self._sync_best_header.height

        out: list[bytes] = []
        step = 1
        cursor_hash: Optional[bytes] = start_hash
        cursor_height = start_height

        while cursor_hash is not None and len(out) < max_entries:
            out.append(cursor_hash)
            if cursor_height <= 0:
                break
            step = 1 if len(out) <= 10 else step * 2
            for _ in range(step):
                hdr = self._sync_header_by_hash(cursor_hash)
                if hdr is None:
                    cursor_hash = None
                    break
                cursor_hash = hdr.parent_hash
                cursor_height = max(0, cursor_height - 1)
                if cursor_hash is None:
                    break
            if cursor_hash is None:
                break

        g = bdb.get_canonical_hash(0) or bdb.get_genesis_hash()
        if g and (not out or out[-1] != bytes(g)):
            out.append(bytes(g))
        if head_height <= 1 and g:
            genesis_hash = bytes(g)
            if start_hash != genesis_hash and start_hash not in out:
                out.insert(0, start_hash)
            if genesis_hash not in out:
                out.append(genesis_hash)
        locator_info = self._locator_debug(out)
        self._sync_last_locator_head_height = int(head_height)
        self._sync_last_locator_head_hash = bytes(head_hash) if head_hash is not None else None
        log.debug(
            "Built header locator",
            extra={
                "head_height": head_height,
                "head_hash": head_hash.hex() if head_hash else None,
                "head_source": head_source,
                "locator_count": len(locator_info),
                "locator_first": locator_info[0] if locator_info else None,
                "locator_last": locator_info[-1] if locator_info else None,
            },
        )
        return out

    def _build_checkpoint_locator(self, max_entries: int = 32) -> list[bytes]:
        return self._build_headers_locator(max_entries=max_entries)

    def _build_probe_locator(self, anchor_hash: bytes) -> list[bytes]:
        bdb = self._block_db()
        out = [bytes(anchor_hash)]
        genesis = bdb.get_canonical_hash(0) or bdb.get_genesis_hash() or self._genesis_hash()
        if genesis and bytes(genesis) not in out:
            out.append(bytes(genesis))
        return out

    def _select_header_locator(
        self, peer: _PeerState
    ) -> tuple[list[bytes], int, str]:
        now = time.time()
        if self._should_use_checkpoint_locator(now):
            self._sync_last_checkpoint_action = "locator_from_checkpoint"
            return (
                self._build_checkpoint_locator(),
                self._sync_headers_batch_current,
                "checkpoint",
            )
        if (
            self._sync_anchor_probe_hash is not None
            and now <= self._sync_anchor_probe_until
            and (
                self._sync_anchor_probe_peer is None
                or self._sync_anchor_probe_peer == peer.remote
            )
        ):
            return (
                self._build_probe_locator(self._sync_anchor_probe_hash),
                1,
                "probe",
            )
        return (self._build_headers_locator(), self._sync_headers_batch_current, "default")

    def _should_use_checkpoint_locator(self, now: float) -> bool:
        if not self._sync_checkpoint_mode_enabled:
            return False
        if self._sync_checkpoint_validation == "mismatch":
            return False
        if self._sync_checkpoint_hash is None or self._sync_checkpoint_height is None:
            return False
        if self._sync_last_header_error == "not_anchored":
            return True
        if self._sync_last_header_at <= 0:
            return True
        best_height = self._sync_best_header.height if self._sync_best_header else 0
        if best_height < self._sync_checkpoint_height:
            return now - self._sync_last_header_at >= self._sync_checkpoint_locator_after
        return False

    def _should_defer_blocks_for_checkpoint(self, best_header_height: int) -> bool:
        _ = best_header_height
        if not self._should_enforce_checkpoint_anchor():
            return False
        if not any(self._peer_is_anchored(peer) for peer in self._peers.values()):
            eligible, _ = self._eligible_sync_peers(ignore_backoff_reason="not_anchored")
            if eligible:
                return False
            return True
        return False

    def _reset_sync_state(self, *, reason: str) -> None:
        self._sync_inflight_blocks.clear()
        self._sync_inflight_peers.clear()
        self._sync_inflight_block_requests.clear()
        self._sync_inflight_header_requests.clear()
        self._sync_inflight_headers = 0
        self._sync_active_header_peer = None
        self._sync_active_block_peer = None
        self._sync_header_queue.clear()
        self._sync_header_retry_queue.clear()
        self._sync_headers.clear()
        self._sync_header_sources.clear()
        self._sync_header_votes.clear()
        self._sync_duplicate_header_ranges.clear()
        self._sync_block_peer_backoff.clear()
        self._sync_block_peer_backoff_reason.clear()
        self._sync_last_headers_accepted_count = 0
        self._sync_last_headers_discarded_count = 0
        self._sync_last_headers_discard_reason_counts = {}
        self._sync_headers_accepted_total = 0
        self._sync_headers_seen_total = 0
        self._sync_last_locator_head_height = None
        self._sync_last_locator_head_hash = None
        self._sync_last_matched_ancestor_height = None
        self._sync_last_matched_ancestor_hash = None
        self._sync_last_header_error = None
        self._sync_last_header_error_at = None
        self._sync_last_header_error_peer = None
        self._sync_block_queue.clear()
        self._sync_block_queue_set.clear()
        self._sync_block_queue_heights.clear()
        self._sync_block_retry_counts.clear()
        for peer in self._peers.values():
            peer.pending_headers = None
            peer.pending_header_request_id = None
        self._sync_best_header = None
        self._sync_anchor_probe_hash = None
        self._sync_anchor_probe_peer = None
        self._sync_anchor_probe_until = 0.0
        self._sync_not_anchored_attempts = 0
        self._sync_last_not_anchored_at = 0.0
        self._sync_recovery_attempts = 0
        self._sync_last_recovery_action = None
        self._sync_last_recovery_at = None
        self._sync_last_recovery_reason = None
        self._sync_last_block_error = None
        self._sync_last_block_error_at = None
        self._sync_last_block_error_peer = None
        self._sync_block_error_summary.clear()
        self._sync_pow_mismatch_votes.clear()
        self._sync_last_block_recovery_peers = []
        self._sync_last_head_height = 0
        self._sync_last_head_hash = None
        self._sync_last_header_height = 0
        self._sync_last_block_fetch_height = 0
        self._sync_last_block_download_at = 0.0
        self._sync_last_queue_depth = 0
        log.info("Reset sync state", extra={"reason": reason})

    def _reset_from_highest_next_height(self, *, reason: str) -> None:
        now = time.time()
        local_height, local_hash_hex = self._local_head()
        local_hash = self._parse_hash_bytes(local_hash_hex)

        self._sync_header_queue.clear()
        self._sync_header_retry_queue.clear()
        self._expire_inflight_headers()
        self._expire_inflight_blocks()
        self._sync_active_header_peer = None
        self._sync_active_block_peer = None
        if self._sync_enabled:
            self._sync_paused = False

        best_peer, best_height, _best_hash = self._best_broadcast_peer_head(now=now)
        if best_height is not None:
            self._update_sync_target_height(
                int(best_height), reason="reset_from_highest_next_height"
            )

        locator = self._build_headers_locator()
        if not locator:
            fallback = self._genesis_hash()
            if fallback:
                locator = [fallback]

        self._sync_recovery_attempts += 1
        self._sync_last_recovery_action = "reset_from_highest_next_height"
        self._sync_last_recovery_at = now
        self._sync_last_recovery_reason = reason

        request_start_height = int(local_height or 0) + 1
        self._enqueue_header_retry(
            peer=best_peer,
            locator=locator,
            locator_mode="reset_from_highest_next_height",
            anchor_height=int(local_height or 0),
            anchor_hash=local_hash,
            request_start_height=request_start_height,
            max_headers=self._sync_headers_batch_current,
            reason="reset_from_highest_next_height",
        )
        self._sync_kick(reason="reset_from_highest_next_height", aggressive=True)
        log.warning(
            "Resetting sync from highest next height",
            extra={
                "reason": reason,
                "local_height": int(local_height or 0),
                "target_height": best_height,
                "peer": best_peer.remote if best_peer else None,
            },
        )

    def _reset_chain_to_genesis(self, *, reason: str) -> bool:
        bdb = self._block_db()
        genesis = bdb.get_canonical_hash(0) or bdb.get_genesis_hash() or self._genesis_hash()
        if not genesis:
            log.warning("Unable to reset chain to genesis", extra={"reason": reason})
            return False
        batch_fn = getattr(bdb.kv, "batch", None)
        if callable(batch_fn):
            with bdb.kv.batch() as batch:
                bdb.set_canonical_head(0, bytes(genesis), batch=batch, allow_reorg=True)
                self._prune_canonical_heights(bdb, above_height=0, batch=batch)
        else:
            bdb.set_canonical_head(0, bytes(genesis), allow_reorg=True)
            self._prune_canonical_heights(bdb, above_height=0, batch=None)
        with contextlib.suppress(Exception):
            from core.chain.block_import import reset_importer_cache

            reset_importer_cache(bdb)
        self._reset_sync_state(reason=reason)
        self._clear_sync_cache()
        self._update_sync_target_height(None, reason="reset_chain_to_genesis")
        self._sync_paused = False
        hdr = self._sync_header_by_hash(bytes(genesis))
        if hdr is not None:
            self._sync_best_header = hdr
        log.warning(
            "Reset chain to genesis after repeated not_anchored",
            extra={"reason": reason, "genesis_hash": bytes(genesis).hex()},
        )
        return True

    def _prune_canonical_heights(
        self,
        bdb: Any,
        *,
        above_height: int,
        batch: Optional[Any],
    ) -> int:
        kv = getattr(bdb, "kv", None)
        if kv is None or not hasattr(kv, "iter_prefix"):
            return 0
        deletions: list[bytes] = []
        for key, _value in kv.iter_prefix(b"\x12"):
            if len(key) != 9:
                continue
            height = int.from_bytes(key[1:], "big")
            if height > above_height:
                deletions.append(key)
        for key in deletions:
            if batch is not None:
                batch.delete(key)
            else:
                kv.delete(key)
        # Reconcile META_CANONICAL_HEIGHT to the rolled-down chain. Pruning the
        # canonical index above `above_height` lowers the head, but the non-instant
        # counter is stored separately and would otherwise stay inflated — making
        # the node believe it is far behind, refuse to mine, and shift the halving
        # schedule. This is the same bug the snapshot/startup fixes address, on the
        # P2P reorg-down paths (verifier-limit reorg + reset-to-genesis). Recompute
        # as the count of non-instant canonical blocks in [1, above_height]
        # (genesis is always canonical_height 0).
        try:
            from core.db.snapshot import _count_non_instant_canonical
            if hasattr(bdb, "set_canonical_height"):
                new_ch = _count_non_instant_canonical(bdb, int(above_height))
                bdb.set_canonical_height(new_ch, batch=batch)
        except Exception as exc:  # noqa: BLE001
            log.warning("canonical_height reconcile after prune failed: %s", exc)
        return len(deletions)

    def _checkpoint_parent_meta(self, parent_hash: bytes) -> Optional[Tuple[int, int]]:
        if not self._sync_checkpoint_mode_enabled:
            return None
        if self._sync_checkpoint_hash is None or self._sync_checkpoint_height is None:
            return None
        if parent_hash != self._sync_checkpoint_hash:
            return None
        return self._sync_checkpoint_height, 0

    def _update_checkpoint_validation(self, headers: list[_SyncHeader]) -> None:
        if not self._sync_checkpoint_mode_enabled:
            return
        if self._sync_checkpoint_validation == "mismatch":
            return
        if self._sync_checkpoint_hash is None or self._sync_checkpoint_height is None:
            return
        for header in headers:
            if header.height != self._sync_checkpoint_height:
                continue
            if header.hash == self._sync_checkpoint_hash:
                self._sync_checkpoint_validation = "verified"
                self._sync_last_checkpoint_action = "accepted_chain"
            else:
                self._sync_checkpoint_validation = "mismatch"
                self._sync_checkpoint_mode_enabled = False
                self._sync_last_checkpoint_action = "checkpoint_mismatch"
            return

    def _note_not_anchored(
        self,
        peer: _PeerState,
        *,
        header: _SyncHeader,
        anchor_height: int,
        anchor_hash: Optional[bytes],
        reason: str,
        allow_probe: bool = True,
        probe_hash: Optional[bytes] = None,
    ) -> tuple[list[bytes], str]:
        now = time.time()
        peer_genesis = bytes(
            (peer.hello or {}).get("genesis_header_hash")
            or (peer.hello or {}).get("genesis_hash")
            or b""
        )
        peer_genesis_block = bytes((peer.hello or {}).get("genesis_block_hash") or b"")
        expected_genesis = {
            self._genesis_hash(),
            self._genesis_header_hash(),
            self._genesis_block_hash(),
        }
        if (peer_genesis and peer_genesis not in expected_genesis) or (
            peer_genesis_block and peer_genesis_block not in expected_genesis
        ):
            self._sync_last_header_error = "wrong_network"
            self._sync_last_header_error_at = now
            self._sync_last_header_error_peer = peer.remote
            self._set_sync_backoff(
                peer,
                reason="wrong_network",
                delay=self._sync_not_anchored_backoff,
            )
            log.info(
                "Rejecting header batch: wrong network",
                extra={
                    "remote": peer.remote,
                    "anchor_height": anchor_height,
                    "anchor_hash": anchor_hash.hex() if anchor_hash else None,
                    "first_height": header.height,
                    "first_hash": header.hash.hex(),
                    "first_prev_hash": header.parent_hash.hex(),
                },
            )
            return [], "wrong_network"
        prior_probe_hash = self._sync_anchor_probe_hash
        prior_probe_until = self._sync_anchor_probe_until
        if now - peer.last_not_anchored_at > self._sync_not_anchored_window:
            peer.not_anchored_count = 0
        peer.not_anchored_count += 1
        peer.last_not_anchored_at = now
        if now - self._sync_last_not_anchored_at > self._sync_not_anchored_window:
            self._sync_not_anchored_attempts = 0
        self._sync_not_anchored_attempts += 1
        self._sync_last_not_anchored_at = now
        self._sync_recovery_attempts += 1
        self._sync_last_header_error = "not_anchored"
        self._sync_last_header_error_at = now
        self._sync_last_header_error_peer = peer.remote
        cooldown = self._not_anchored_delay(peer.not_anchored_count)
        genesis_bootstrap = anchor_height == 0 and self._sync_checkpoint_mode_enabled
        if genesis_bootstrap and peer.not_anchored_count == 1:
            genesis_hash = self._genesis_hash()
            self._sync_anchor_probe_hash = genesis_hash
            self._sync_anchor_probe_peer = peer.remote
            self._sync_anchor_probe_until = now + max(1.0, self._sync_request_timeout)
        else:
            self._set_sync_backoff(
                peer,
                reason="not_anchored",
                delay=cooldown,
            )
            if allow_probe:
                self._sync_anchor_probe_hash = (
                    probe_hash if probe_hash is not None else header.parent_hash
                )
                self._sync_anchor_probe_peer = None
                self._sync_anchor_probe_until = now + cooldown
            else:
                self._sync_anchor_probe_hash = None
                self._sync_anchor_probe_peer = None
                self._sync_anchor_probe_until = 0.0
        locator_info = self._sync_last_locator_info or []
        locator_start = locator_info[0] if locator_info else None
        locator_end = locator_info[-1] if locator_info else None
        self._sync_last_locator_summary = self._locator_summary(locator_info)
        action = "fork_discovery"
        if genesis_bootstrap and peer.not_anchored_count == 1:
            action = "retry_genesis_locator"
        if (
            prior_probe_hash == header.parent_hash
            and prior_probe_until
            and now <= prior_probe_until
        ):
            action = "retry_locator"
        if not allow_probe and reason in {
            "anchor_mismatch",
            "anchor_parent_mismatch",
            "parent_unknown",
            "parent_meta_missing",
        }:
            action = "retry_locator"
        if (
            anchor_height <= self._sync_not_anchored_reset_height
            and self._sync_not_anchored_attempts
            >= self._sync_not_anchored_reset_threshold
        ):
            self._sync_last_checkpoint_action = "not_anchored_backoff"
        if (
            self._sync_checkpoint_mode_enabled
            and self._sync_checkpoint_validation == "unknown"
            and action != "reset_to_genesis"
            and self._sync_last_checkpoint_action == "locator_from_checkpoint"
            and self._sync_not_anchored_attempts
            >= self._sync_not_anchored_reset_threshold
        ):
            self._sync_checkpoint_validation = "unreachable"
            self._sync_last_checkpoint_action = "checkpoint_unreachable"
        should_reset = (
            anchor_height <= self._sync_not_anchored_reset_height
            and self._sync_not_anchored_attempts
            >= self._sync_not_anchored_reset_threshold
        )
        if should_reset and self._reset_chain_to_genesis(reason="not_anchored"):
            action = "reset_to_genesis"
            self._sync_last_checkpoint_action = "reset_to_genesis"
            self._sync_last_recovery_action = "reset_to_genesis"
        if action in {"fork_discovery", "retry_locator", "retry_genesis_locator"}:
            self._sync_active_header_peer = None
            self._sync_kick(reason="not_anchored_recover", aggressive=True)
        self._sync_last_recovery_action = action
        best_header_height = (
            self._sync_best_header.height if self._sync_best_header else anchor_height
        )
        best_header_hash = (
            self._sync_best_header.hash.hex()
            if self._sync_best_header
            else (anchor_hash.hex() if anchor_hash else None)
        )
        log.info(
            "Rejecting header batch: not anchored to local chain",
            extra={
                "remote": peer.remote,
                "anchor_height": anchor_height,
                "anchor_hash": anchor_hash.hex() if anchor_hash else None,
                "anchor_source": (self._sync_last_anchor_check or {}).get("anchor_source"),
                "prev_hash_known": (self._sync_last_anchor_check or {}).get("prev_hash_known"),
                "prev_hash": (self._sync_last_anchor_check or {}).get("prev_hash"),
                "anchor_candidates": (self._sync_last_anchor_check or {}).get(
                    "anchor_candidates"
                ),
                "locator_len": len(locator_info),
                "locator_start": locator_start,
                "locator_end": locator_end,
                "best_header_height": best_header_height,
                "best_header_hash": best_header_hash,
                "first_height": header.height,
                "first_hash": header.hash.hex(),
                "first_prev_hash": header.parent_hash.hex(),
                "reason": reason,
                "action": action,
            },
        )
        self._record_sync_header_event(
            {
                "type": "not_anchored",
                "peer": peer.remote,
                "reason": reason,
                "action": action,
                "anchor_height": anchor_height,
                "anchor_hash": anchor_hash.hex() if anchor_hash else None,
                "best_header_height": best_header_height,
                "best_header_hash": best_header_hash,
                "locator_summary": self._sync_last_locator_summary,
                "anchor_candidates": (self._sync_last_anchor_check or {}).get(
                    "anchor_candidates"
                ),
                "first_height": header.height,
                "first_hash": header.hash.hex(),
                "first_prev_hash": header.parent_hash.hex(),
            }
        )
        return [], "not_anchored"

    async def _pending_get(self, tx_hash: bytes) -> bytes | None:
        # Prefer deps hook (used in tests and alternative mempool implementations).
        if self.deps is not None:
            fn = getattr(self.deps, "get_tx_raw", None)
            if callable(fn):
                with contextlib.suppress(Exception):
                    raw = fn(tx_hash)
                    if asyncio.iscoroutine(raw):
                        raw = await raw
                    if isinstance(raw, (bytes, bytearray)):
                        return bytes(raw)
        try:
            from rpc.methods import tx as tx_methods

            return tx_methods._pending_get("0x" + tx_hash.hex())
        except Exception:
            return None

    def _has_block(self, block_hash: bytes) -> bool:
        return self._get_block_raw(block_hash) is not None

    def _get_block_raw(self, block_hash: bytes) -> bytes | None:
        try:
            bdb = self._block_db()
        except Exception:
            return None
        try:
            kv = getattr(bdb, "kv", None)
            if kv is not None:
                from core.db.block_db import k_blk

                raw = kv.get(k_blk(block_hash))
                if raw is not None:
                    return bytes(raw)
        except Exception:
            pass
        try:
            blk = bdb.get_block_by_hash(block_hash)
            if blk is None:
                return None
            if isinstance(blk, (bytes, bytearray)):
                return bytes(blk)
            return blk.to_cbor() if hasattr(blk, "to_cbor") else None
        except Exception:
            return None

    def _has_header(self, block_hash: bytes) -> bool:
        if block_hash in self._sync_headers:
            return True
        try:
            return self._block_db().get_header_by_hash(block_hash) is not None
        except Exception:
            return False

    def _allow_nonfatal_penalty(self, peer: _PeerState) -> bool:
        now = time.time()
        events = self._sync_nonfatal_penalty_events.setdefault(peer.remote, deque())
        window = self._sync_nonfatal_penalty_window_s
        while events and now - events[0] > window:
            events.popleft()
        if len(events) >= self._sync_nonfatal_penalty_limit:
            return False
        events.append(now)
        return True

    def _decode_block(self, rawb: bytes) -> _SyncBlock:
        from core.types.block import Block

        blk = Block.from_cbor(rawb)
        block_hash = blk.header.hash()
        parent_hash = bytes(blk.header.parentHash)
        return _SyncBlock(block=blk, hash=block_hash, parent_hash=parent_hash)

    def _penalize_peer(
        self,
        peer: Optional[_PeerState],
        reason: str,
        *,
        severity: int = 1,
        quarantine_s: Optional[float] = None,
        points: Optional[int] = None,
        ban_ttl: Optional[float] = None,
        nonfatal: bool = False,
    ) -> None:
        if peer is None:
            return
        penalty_key = self._penalty_key(peer)
        host = self._extract_host(peer.remote) or ""
        if host and self._is_docker_local(host):
            log.info(
                "Skipping sync penalty for docker-local peer",
                extra={"remote": peer.remote, "reason": reason},
            )
            return
        if "timeout" in reason.lower() and self._is_peer_exempt(peer.remote):
            log.info(
                "Skipping timeout penalty for exempt peer",
                extra={"remote": peer.remote, "reason": reason},
            )
            return
        if nonfatal and not self._allow_nonfatal_penalty(peer):
            count = self._sync_peer_penalties.get(peer.remote, 0)
            if "timeout" in reason:
                delay = min(60.0, 2.0 ** min(count, 6))
                self._set_sync_backoff(peer, reason="timeout", delay=delay)
            if quarantine_s:
                self._set_sync_backoff(peer, reason=reason, delay=quarantine_s)
            log.info(
                "Sync peer penalty suppressed (rate limit)",
                extra={"remote": peer.remote, "reason": reason, "penalties": count},
            )
            return
        self._apply_misbehavior(peer, reason, points=points, ban_ttl=ban_ttl)
        if self._is_peer_exempt(peer.remote) or self._is_peer_exempt(penalty_key):
            self._sync_peer_penalties.pop(penalty_key, None)
            self._sync_peer_penalty_events.pop(penalty_key, None)
            return
        now = time.time()
        events = self._sync_peer_penalty_events.setdefault(penalty_key, deque())
        window = self._sync_peer_penalty_window_s
        while events and now - events[0] > window:
            events.popleft()
        for _ in range(max(1, severity)):
            events.append(now)
        count = len(events)
        self._sync_peer_penalties[penalty_key] = count
        if "timeout" in reason:
            delay = min(60.0, 2.0 ** min(count, 6))
            self._set_sync_backoff(peer, reason="timeout", delay=delay)
        if quarantine_s:
            self._set_sync_backoff(peer, reason=reason, delay=quarantine_s)
        log.warning(
            "Sync peer penalty: %s",
            reason,
            extra={"remote": peer.remote, "penalties": count, "reason": reason},
        )
        if count >= self._sync_peer_penalty_threshold:
            self._create_child_task(
                self._drop_peer(peer, reason=f"sync_penalty:{reason}"),
                name=f"p2p.drop_peer@{peer.remote}",
            )

    def _update_latency(self, peer: _PeerState, request_at: float) -> None:
        now = time.time()
        if request_at <= 0:
            return
        delta = max(0.0, now - request_at)
        alpha = 0.2
        if peer.latency_ewma is None:
            peer.latency_ewma = delta
        else:
            peer.latency_ewma = alpha * delta + (1 - alpha) * peer.latency_ewma

    def _apply_misbehavior(
        self,
        peer: _PeerState,
        reason: str,
        *,
        points: Optional[int] = None,
        ban_ttl: Optional[float] = None,
    ) -> None:
        if points is None:
            points = self._reason_points(reason)
        if points <= 0 and ban_ttl is None:
            return
        peer.misbehavior_score = min(
            self._misbehavior_score_cap, peer.misbehavior_score + max(0, points)
        )
        self._increment_peer_counters(peer, reason)
        if ban_ttl is None:
            ban_ttl = self._ban_ttl_for_score(peer.misbehavior_score)
        if ban_ttl:
            self._ban_peer(peer, ban_ttl=ban_ttl, reason=reason)
        self._update_peer_meta(peer)

    def _reason_points(self, reason: str) -> int:
        lowered = reason.lower()
        if "genesis" in lowered:
            return self._score_points["wrong_genesis"]
        if "consensus" in lowered or "network_params" in lowered:
            return self._score_points["wrong_chain"]
        if "chain" in lowered and "mismatch" in lowered:
            return self._score_points["wrong_chain"]
        if lowered.startswith("header_"):
            return self._score_points["invalid_header"]
        if "bad_header" in lowered or "invalid_header" in lowered:
            return self._score_points["invalid_header"]
        if lowered.startswith("block_rejected") or "invalid_block" in lowered:
            return self._score_points["invalid_block"]
        if "timeout" in lowered:
            return self._score_points["timeout"]
        if "missing parent" in lowered or "missing_parent" in lowered:
            return self._score_points["missing_parent"]
        if "stall" in lowered:
            return self._score_points["stall"]
        if "decode" in lowered or "malformed" in lowered or "oversized" in lowered:
            return self._score_points["malformed_message"]
        return 0

    def _increment_peer_counters(self, peer: _PeerState, reason: str) -> None:
        lowered = reason.lower()
        if "timeout" in lowered:
            peer.timeouts += 1
        if "missing parent" in lowered or "missing_parent" in lowered:
            peer.missing_parent += 1
        if "stall" in lowered:
            peer.stall_events += 1
        if lowered.startswith("header_") or "invalid_header" in lowered:
            peer.invalid_headers += 1
        if lowered.startswith("block_rejected") or "invalid_block" in lowered:
            peer.invalid_blocks += 1
        if "decode" in lowered or "malformed" in lowered or "oversized" in lowered:
            peer.invalid_msgs += 1

    def _ban_ttl_for_score(self, score: int) -> Optional[float]:
        if not self._ban_enabled:
            return None
        ttl = None
        for threshold, ttl_s in self._ban_thresholds:
            if score >= threshold:
                ttl = ttl_s
        return ttl

    def _ban_peer(self, peer: _PeerState, *, ban_ttl: float, reason: str) -> None:
        if not self._ban_enabled:
            return
        if self._is_peer_exempt(peer.remote):
            log.info(
                "Skipping ban for exempt peer",
                extra={"remote": peer.remote, "reason": reason},
            )
            return
        if self._is_seed_peer(peer):
            log.warning(
                "Skipping ban for seed peer",
                extra={"remote": peer.remote, "reason": reason},
            )
            return
        until = time.time() + max(0.0, ban_ttl)
        peer.ban_until = until
        for key in self._ban_keys_for_peer(peer):
            self._banlist[key] = {
                "ban_until": until,
                "reason": reason,
                "score": peer.misbehavior_score,
            }
        self._banlist_event.set()
        self._create_child_task(
            self._drop_peer(peer, reason=f"banned:{reason}"),
            name=f"p2p.drop_peer@{peer.remote}",
        )

    async def _import_block_payload(
        self, payload: Any, *, origin_remote: Optional[str]
    ) -> Tuple[bool, Optional[str]]:
        from core.utils.hash import sha3_256

        bh: bytes | None = None
        ok = False
        reason: Optional[str] = None
        blk = None
        before_height, _ = self._local_head()

        if isinstance(payload, (bytes, bytearray)):
            rawb = bytes(payload)
            try:
                blk = self._decode_block(rawb).block
                bh = blk.header.hash()
                ok, reason = await self._deps_call_import(blk)
            except Exception:
                # Fallback: allow deps to import raw bytes directly (dev/test networks).
                bh = sha3_256(rawb)
                ok, reason = await self._deps_call_import(rawb)
        else:
            blk = payload
            try:
                if hasattr(blk, "header") and hasattr(blk.header, "hash"):
                    bh = blk.header.hash()
            except Exception:
                bh = None
            ok, reason = await self._deps_call_import(blk)

        if ok and bh is not None:
            if blk is not None:
                self._reconcile_pending_pool(blk)
            self._remember(self._seen_blocks, bh, self._seen_block_cap)
            if origin_remote:
                origin_peer = self._peer_by_remote(origin_remote)
                if origin_peer and not origin_peer.anchored:
                    self._mark_peer_anchored(origin_peer, reason="block_accepted")
            await self._broadcast_inv(
                [InvItem(typ=InvType.BLOCK, h=bh)],
                exclude_remote=origin_remote,
                is_tx=False,
            )
            await self._broadcast_block_announce(bh, exclude_remote=origin_remote)
            self._sync_last_block_error = None
            self._sync_last_block_error_at = None
            self._sync_last_block_error_peer = None
            self._sync_fatal_error = None
            self._sync_block_stalled_reason = None
            self._stats["blocks_validated_ok"] += 1
            self._stats["blocks_imported"] += 1
            self._stats["blocks_applied"] += 1
            self._drop_from_block_queue(bh)
            self._sync_last_block_at = time.time()
            self._sync_last_progress_at = self._sync_last_block_at
            log.info(
                "Block persisted",
                extra={"hash": bh.hex(), "origin": origin_remote},
            )
            after_height, _ = self._local_head()
            if after_height > before_height:
                self._sync_last_validated_height = after_height
                log.info(
                    "Head advanced",
                    extra={"height": after_height, "origin": origin_remote},
                )
        elif not ok:
            reason_str = reason or "block_rejected"
            if not self._is_orphan_reason(reason_str):
                self._stats["blocks_rejected"] += 1
            self._sync_last_block_error = reason_str
            self._sync_last_block_error_at = time.time()
            if origin_remote:
                self._sync_last_block_error_peer = origin_remote
            if "pow target not met" in reason_str.lower():
                header_hash_hex = None
                theta_micro = None
                target_hex = None
                pow_hash_int = None
                claimed_bits = None
                peer_id = None
                if origin_remote:
                    origin_peer = self._peer_by_remote(origin_remote)
                    peer_id = origin_peer.peer_id if origin_peer else None
                if blk is not None and hasattr(blk, "header"):
                    try:
                        header_hash_hex = bytes(blk.header.hash()).hex()
                    except Exception:
                        header_hash_hex = None
                    try:
                        theta_micro = int(getattr(blk.header, "thetaMicro", 0))
                    except Exception:
                        theta_micro = None
                    try:
                        pow_hash_int = int.from_bytes(blk.header.hash(), "big")
                    except Exception:
                        pow_hash_int = None
                    try:
                        claimed_bits = getattr(blk.header, "bits", None)
                    except Exception:
                        claimed_bits = None
                    if theta_micro is not None:
                        try:
                            from core.chain.block_import import _theta_to_target

                            target_hex = hex(_theta_to_target(theta_micro))
                        except Exception:
                            target_hex = None
                log.debug(
                    "PoW target mismatch",
                    extra={
                        "remote": origin_remote,
                        "peer_id": peer_id,
                        "header_hash": header_hash_hex,
                        "theta_micro": theta_micro,
                        "computed_target": target_hex,
                        "claimed_bits": claimed_bits,
                        "pow_hash_int": pow_hash_int,
                        "pow_rule": "header_hash<=target",
                        "reason": reason_str,
                    },
                )
            if self._is_db_write_error(reason_str):
                self._sync_block_stalled_reason = STALL_BLOCK_INVALID_RESPONSE
                self._sync_last_block_error = f"db not writable: {reason_str}"
                log.error(
                    "Block DB write failed",
                    extra={"origin": origin_remote, "error": reason_str},
                )
        return ok, reason

    def _reconcile_pending_pool(self, block: Any) -> int:
        try:
            from rpc.methods import tx as tx_methods
        except Exception:
            return 0
        try:
            from core.utils.hash import sha3_256
        except Exception:
            import hashlib

            def sha3_256(data: bytes) -> bytes:
                return hashlib.sha3_256(data).digest()

        txs = None
        if isinstance(block, dict):
            txs = block.get("txs") or block.get("transactions")
        if txs is None:
            txs = getattr(block, "txs", None)
        if not txs:
            return 0
        removed = 0
        for tx in txs:
            tx_hash_hex = ""
            try:
                if isinstance(tx, (bytes, bytearray)):
                    tx_hash_hex = "0x" + sha3_256(bytes(tx)).hex()
                elif hasattr(tx, "txid") and callable(getattr(tx, "txid")):
                    tx_hash_hex = "0x" + bytes(tx.txid()).hex()
                else:
                    tx_hash_hex = tx_methods._compute_tx_hash(tx)
            except Exception:
                tx_hash_hex = ""
            if tx_hash_hex:
                if tx_methods._pending_remove(tx_hash_hex):
                    removed += 1
        return removed

    # ---------------------------------------------------------------------
    # Broadcast helpers
    # ---------------------------------------------------------------------

    async def _broadcast_block_announce(
        self, header_hash: bytes, *, exclude_remote: Optional[str]
    ) -> None:
        if proto_blk is None:
            return
        header = None
        try:
            header = self._block_db().get_header_by_hash(header_hash)
        except Exception:
            header = None
        if header is None:
            return
        try:
            parent_hash = bytes(header.parentHash)
        except Exception:
            parent_hash = b"\x00" * 32
        try:
            height = int(getattr(header, "height", 0))
        except Exception:
            height = 0
        try:
            score = int(getattr(header, "thetaMicro", 0))
        except Exception:
            score = 0
        tx_count = 0
        proofs_count = 0
        rawb = self._get_block_raw(header_hash)
        if rawb:
            with contextlib.suppress(Exception):
                blk = self._decode_block(rawb).block
                txs = getattr(blk, "txs", None) or ()
                proofs = getattr(blk, "proofs", None) or ()
                tx_count = len(txs)
                proofs_count = len(proofs)
        try:
            payload = proto_blk.build_announce(
                header_hash=header_hash,
                parent_hash=parent_hash,
                height=height,
                score=score,
                tx_count=tx_count,
                proofs_count=proofs_count,
            )
        except Exception:
            return
        async with self._peer_lock:
            peers = list(self._peers.values())
        for p in peers:
            if exclude_remote and p.remote == exclude_remote:
                continue
            with contextlib.suppress(Exception):
                await self._send_raw(p, MsgID.BLOCK_ANNOUNCE, payload)

    async def _broadcast_inv(
        self,
        items: list[InvItem],
        *,
        exclude_remote: Optional[str],
        is_tx: bool,
    ) -> None:
        if not items:
            return
        if is_tx and not self._tx_relay_allowed():
            return

        async with self._peer_lock:
            peers = list(self._peers.values())

        for p in peers:
            if exclude_remote and p.remote == exclude_remote:
                continue
            await self._send_inv(p, items, is_tx=is_tx)

    def _tx_relay_allowed(self) -> bool:
        if self._bootstrap_mode:
            return False
        return bool(
            self._tx_relay_enabled
            and self._tx_gossip_enabled
            and self._mempool_gossip_enabled
            and self._p2p_tx_enabled
        )

    def _tx_peer_eligibility(self, peer: _PeerState) -> tuple[bool, str]:
        if not self._tx_relay_allowed():
            return False, "relay_disabled"
        if not peer.hello_done.is_set():
            return False, "handshake_incomplete"
        if peer.ban_until and peer.ban_until > time.time():
            return False, "peer_not_eligible"
        if not self._peer_chain_matches(peer):
            return False, "peer_not_eligible"
        return True, "ok"

    def _peer_supports_tx_relay_v2(self, peer: _PeerState) -> bool:
        if peer.negotiated_caps:
            return "tx_relay_v2" in peer.negotiated_caps
        caps = peer.hello.get("capabilities") if peer.hello else None
        return bool(caps and "tx_relay_v2" in caps)

    def _txrelay_peer_ids(self) -> list[str]:
        return [self._peer_tx_key(peer) for peer in self._peers.values()]

    def _txrelay_find_peer(self, peer_key: str) -> Optional[_PeerState]:
        for peer in self._peers.values():
            if self._peer_tx_key(peer) == peer_key:
                return peer
        return None

    def _txrelay_peer_eligible(self, peer_key: str) -> bool:
        peer = self._txrelay_find_peer(peer_key)
        if peer is None:
            log.debug(f"[DIAG] Peer {peer_key} not eligible: peer not found")
            return False
        if not self._tx_relay_v2_enabled:
            log.debug(f"[DIAG] Peer {peer_key} not eligible: tx_relay_v2 disabled globally")
            return False
        if self._tx_relay_v2_enabled and not self._peer_supports_tx_relay_v2(peer):
            log.debug(f"[DIAG] Peer {peer_key} not eligible: peer doesn't support tx_relay_v2")
            return False
        ok, reason = self._tx_peer_eligibility(peer)
        if not ok:
            log.debug(f"[DIAG] Peer {peer_key} not eligible: {reason}")
        return ok

    async def _legacy_tx_relay_announce(
        self, txid: bytes, raw: bytes, origin_peer: Optional[str]
    ) -> None:
        if not self._tx_relay_allowed():
            return
        inv_item = InvItem(typ=InvType.TX, h=txid)
        async with self._peer_lock:
            peers = list(self._peers.values())
        for peer in peers:
            if self._tx_relay_v2_enabled and self._peer_supports_tx_relay_v2(peer):
                continue
            if origin_peer:
                if origin_peer == peer.peer_id:
                    continue
                if origin_peer == peer.remote:
                    continue
                if origin_peer == self._peer_tx_key(peer):
                    continue
            await self._send_inv(peer, [inv_item], is_tx=True)

    def _peer_supports_instant_txblock(self, peer: _PeerState) -> bool:
        if peer.negotiated_caps:
            return "INSTANT_TXBLOCK_V1" in peer.negotiated_caps
        caps = peer.hello.get("capabilities") if peer.hello else None
        return bool(caps and "INSTANT_TXBLOCK_V1" in caps)

    async def _on_mempool_tx_accepted_instant(self, txid: bytes, _raw: bytes) -> None:
        if not instant_enabled():
            return
        txid_hex = "0x" + bytes(txid).hex()
        try:
            height, anchor_hex = self._local_head()
            _ = height
            anchor_hash = anchor_hex if isinstance(anchor_hex, str) and anchor_hex.startswith("0x") else ("0x" + (anchor_hex or "").removeprefix("0x"))
            if anchor_hash in {"0x", ""}:
                return
            svc = get_instant_tx_service_singleton()
            if svc is None:
                return
            existing = svc.get_receipt(txid_hex)
            if existing and existing.get("block"):
                rec = dict(existing["block"])
            else:
                rec = svc.emit_local(txid=txid_hex, anchor_hash=anchor_hash)
        except Exception:
            log.debug("instant tx block local emit failed", exc_info=True)
            return

        block_id = str(rec.get("block_id") or "")
        if not block_id:
            return
        self._remember_ttl(self._txblock_seen, block_id, self._txblock_seen_cap, self._tx_relay_ttl_s)

        async with self._peer_lock:
            peers = list(self._peers.values())
        for peer in peers:
            if not self._peer_supports_instant_txblock(peer):
                continue
            ok, _reason = self._tx_peer_eligibility(peer)
            if not ok:
                continue
            await self._send(peer, MsgID.TXBLOCK_INV, {"ids": [block_id]})

    async def _handle_txblock_inv(self, peer: _PeerState, payload: bytes) -> None:
        if not instant_enabled():
            return
        if not self._peer_supports_instant_txblock(peer):
            return
        data = self._decode_payload_map(payload)
        ids = [str(x) for x in (data.get("ids") or []) if isinstance(x, str)]
        want: list[str] = []
        svc = get_instant_tx_service_singleton()
        for block_id in ids[: self._max_inv_per_msg]:
            if self._seen(self._txblock_seen, block_id):
                continue
            if svc is not None and svc.get_block(block_id) is not None:
                continue
            want.append(block_id)
            self._remember_ttl(self._txblock_seen, block_id, self._txblock_seen_cap, self._tx_relay_ttl_s)
        if want:
            await self._send(peer, MsgID.TXBLOCK_GET, {"ids": want})

    async def _handle_txblock_get(self, peer: _PeerState, payload: bytes) -> None:
        if not instant_enabled():
            return
        if not self._peer_supports_instant_txblock(peer):
            return
        if self._rate_limit(
            self._txblock_inflight_by_peer,
            peer.remote,
            self._max_txblock_per_min_per_peer,
            self._txblock_rate_window_s,
        ):
            self._penalize_peer(peer, "txblock_get_rate_limited", points=self._score_points["malformed_message"])
            return
        data = self._decode_payload_map(payload)
        ids = [str(x) for x in (data.get("ids") or []) if isinstance(x, str)]
        svc = get_instant_tx_service_singleton()
        if svc is None:
            return
        items = []
        for block_id in ids[: self._max_inv_per_msg]:
            rec = svc.get_block(block_id)
            if rec is not None:
                items.append(rec)
        if items:
            await self._send(peer, MsgID.TXBLOCK_DATA, {"items": items})

    async def _handle_txblock_data(self, peer: _PeerState, payload: bytes) -> None:
        if not instant_enabled():
            return
        if not self._peer_supports_instant_txblock(peer):
            return
        data = self._decode_payload_map(payload)
        items = data.get("items") or []
        svc = get_instant_tx_service_singleton()
        if svc is None:
            return
        for it in items[: self._max_inv_per_msg]:
            if not isinstance(it, dict):
                continue
            anchor_hash = str(it.get("anchor_hash") or "")
            txids = [str(t) for t in (it.get("txids") or []) if isinstance(t, str)]
            block_id = str(it.get("block_id") or "")
            ts = int(it.get("timestamp") or 0)
            now = int(time.time())
            if not anchor_hash.startswith("0x") or len(anchor_hash) != 66:
                self._penalize_peer(peer, "invalid_txblock_anchor", points=self._score_points["malformed_message"])
                continue
            if not block_id.startswith("0x") or len(block_id) != 66:
                self._penalize_peer(peer, "invalid_txblock_id", points=self._score_points["malformed_message"])
                continue
            if len(txids) == 0 or len(txids) > 32:
                self._penalize_peer(peer, "invalid_txblock_size", points=self._score_points["malformed_message"])
                continue
            if ts <= 0 or abs(now - ts) > 120:
                self._penalize_peer(peer, "invalid_txblock_timestamp", points=self._score_points["malformed_message"])
                continue
            # anchor must reference known canonical head hash (or current local head hash)
            _h, local_head_hash = self._local_head()
            if anchor_hash != local_head_hash:
                has_anchor = False
                try:
                    if self.deps is not None and hasattr(self.deps, "has_block"):
                        has_anchor = bool(self.deps.has_block(bytes.fromhex(anchor_hash[2:])))
                except Exception:
                    has_anchor = False
                if not has_anchor:
                    continue
            tmp = {
                "version": int(it.get("version") or 1),
                "anchor_hash": anchor_hash,
                "txids": sorted(txids),
                "timestamp": ts,
                "mempool_state_root": it.get("mempool_state_root"),
            }
            expect = "0x" + hashlib.sha3_256(json.dumps(tmp, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            if expect != block_id:
                self._penalize_peer(peer, "invalid_txblock_hash", points=self._score_points["malformed_message"])
                continue
            svc.ingest_remote(dict(it))

    async def _txrelay_send_inv(self, peer_key: str, txids: list[bytes]) -> None:
        peer = self._txrelay_find_peer(peer_key)
        if peer is None:
            return
        payload = TxInv(txids=txids).to_payload()
        await self._send(peer, MsgID.TX_INV, payload)

    async def _txrelay_send_get(self, peer_key: str, txids: list[bytes]) -> None:
        peer = self._txrelay_find_peer(peer_key)
        if peer is None:
            return
        payload = TxGet(txids=txids).to_payload()
        await self._send(peer, MsgID.TX_GET, payload)

    async def _txrelay_send_data(self, peer_key: str, items: list[dict[str, Any]]) -> None:
        peer = self._txrelay_find_peer(peer_key)
        if peer is None:
            return
        payload = TxData(
            items=[
                {"txid": item["txid"], "tx_bytes": item["tx_bytes"]}
                for item in items
            ]
        ).to_payload()
        await self._send(peer, MsgID.TX_DATA, payload)

    async def _txrelay_send_notfound(self, peer_key: str, txids: list[bytes]) -> None:
        peer = self._txrelay_find_peer(peer_key)
        if peer is None:
            return
        payload = TxNotFound(txids=txids).to_payload()
        await self._send(peer, MsgID.TX_NOTFOUND_V2, payload)

    async def _txrelay_send_mempool_req(self, peer_key: str, limit: int) -> None:
        peer = self._txrelay_find_peer(peer_key)
        if peer is None:
            return
        payload = TxMempoolReq(limit=limit).to_payload()
        await self._send(peer, MsgID.TX_MEMPOOL_REQ, payload)

    async def _txrelay_send_mempool_resp(self, peer_key: str, txids: list[bytes]) -> None:
        peer = self._txrelay_find_peer(peer_key)
        if peer is None:
            return
        payload = TxMempoolResp(txids=txids).to_payload()
        await self._send(peer, MsgID.TX_MEMPOOL_RESP, payload)

    async def _txrelay_send_mempool_summary(self, peer_key: str, txids: list[bytes]) -> None:
        peer = self._txrelay_find_peer(peer_key)
        if peer is None:
            return
        payload = TxMempoolSummary(txids=txids, count=len(txids)).to_payload()
        await self._send(peer, MsgID.TX_MEMPOOL_SUMMARY, payload)

    async def _txrelay_has_tx(self, txid: bytes) -> bool:
        if self.deps is None:
            return False
        fn = getattr(self.deps, "has_tx", None)
        if callable(fn):
            if asyncio.iscoroutinefunction(fn):
                return bool(await fn(txid))
            return bool(fn(txid))
        fn = getattr(self.deps, "get_tx_raw", None)
        if callable(fn):
            if asyncio.iscoroutinefunction(fn):
                return (await fn(txid)) is not None
            return fn(txid) is not None
        return False

    async def _txrelay_has_chain_tx(self, txid: bytes) -> bool:
        if self.deps is None:
            return False
        fn = getattr(self.deps, "tx_by_hash", None)
        if callable(fn):
            if asyncio.iscoroutinefunction(fn):
                return (await fn(txid)) is not None
            return fn(txid) is not None
        return False

    async def _txrelay_get_tx_raw(self, txid: bytes) -> Optional[bytes]:
        if self.deps is None:
            return None
        fn = getattr(self.deps, "get_tx_raw", None)
        if callable(fn):
            if asyncio.iscoroutinefunction(fn):
                return await fn(txid)
            return fn(txid)
        return None

    async def _txrelay_admit_tx(
        self, raw: bytes, origin_peer: Optional[str]
    ) -> tuple[bool, Optional[str]]:
        return await self._admit_tx_result(
            raw, local=False, origin_peer=origin_peer
        )

    async def _txrelay_list_mempool_hashes(self, limit: int) -> list[bytes]:
        if self.deps is None:
            return []
        fn = getattr(self.deps, "list_pending_hashes", None)
        if callable(fn):
            if asyncio.iscoroutinefunction(fn):
                return list(await fn(limit=limit))
            return list(fn(limit=limit))
        return []

    def _tx_reject_category(self, reason: Optional[str]) -> str:
        if not reason:
            return "UNKNOWN"
        r = str(reason).lower()
        if "chain_id" in r:
            return "CHAIN_ID"
        if "verify" in r or "sig" in r:
            return "BAD_SIG"
        if "nonce" in r:
            if "too_low" in r or "low" in r:
                return "NONCE_TOO_LOW"
            return "NONCE_GAP"
        if "balance" in r or "insufficient" in r:
            return "INSUFFICIENT_BALANCE"
        if "gas" in r:
            return "BAD_GAS"
        if "fee" in r:
            return "POLICY"
        if "duplicate" in r:
            return "DUPLICATE"
        return "POLICY"

    def _tx_debug_fields(self, raw: bytes) -> dict[str, Any]:
        from core.types.tx import Tx
        try:
            tx = Tx.from_cbor(raw)
        except Exception:
            return {}
        sender = getattr(tx, "sender", None)
        if isinstance(sender, (bytes, bytearray)):
            sender = sender.hex()
        return {
            "sender": sender,
            "nonce": getattr(tx, "nonce", None),
            "gas_limit": getattr(tx, "gas_limit", None),
            "gas_price": getattr(tx, "gas_price", None),
        }

    async def _mempool_size(self) -> Optional[int]:
        if self.deps is None:
            return None
        fn = getattr(self.deps, "mempool_size", None)
        if callable(fn):
            try:
                size = fn()
                if asyncio.iscoroutine(size):
                    size = await size
                return int(size)
            except Exception:
                return None
        fn = getattr(self.deps, "list_pending_hashes", None)
        if callable(fn):
            try:
                pending = fn(limit=self._tx_inv_seed_limit)
                if asyncio.iscoroutine(pending):
                    pending = await pending
                if isinstance(pending, list):
                    return len(pending)
            except Exception:
                return None
        return None

    def _record_tx_reject(
        self, *, tx_hash: str, origin: str, reason: Optional[str]
    ) -> None:
        self._tx_recent_rejects.append(
            {
                "hash": tx_hash,
                "origin": origin,
                "reason": reason,
                "category": self._tx_reject_category(reason),
                "at": time.time(),
            }
        )

    async def _send_inv(
        self, peer: _PeerState, items: list[InvItem], *, is_tx: bool
    ) -> None:
        if not items:
            return
        if is_tx and not self._tx_relay_allowed():
            for it in items:
                if int(it.typ) == int(InvType.TX):
                    log.info(
                        "TX_INV_SKIPPED",
                        extra={
                            "hash": bytes(it.h).hex(),
                            "peer": peer.remote,
                            "reason": "relay_disabled",
                        },
                    )
            return
        if is_tx:
            eligible, reason = self._tx_peer_eligibility(peer)
            if not eligible:
                for it in items:
                    if int(it.typ) == int(InvType.TX):
                        log.info(
                            "TX_INV_SKIPPED",
                            extra={
                                "hash": bytes(it.h).hex(),
                                "peer": peer.remote,
                                "reason": reason,
                            },
                        )
                return
            peer_key = self._peer_tx_key(peer)
            filtered: list[InvItem] = []
            for it in items:
                if int(it.typ) != int(InvType.TX):
                    filtered.append(it)
                    continue
                tx_hash = bytes(it.h)
                if self._inv_sent_recently(peer_key, tx_hash):
                    self._stats["tx_inv_dedup"] += 1
                    log.info(
                        "TX_INV_SKIPPED",
                        extra={
                            "hash": tx_hash.hex(),
                            "peer": peer.remote,
                            "reason": "already_known",
                        },
                    )
                    continue
                filtered.append(it)
            items = filtered
            if not items:
                return
        inv = Inv(items=items)
        with contextlib.suppress(Exception):
            await self._send(peer, MsgID.INV, inv)
            if is_tx:
                self._stats["inv_tx_sent"] += len(items)
                self._stats["tx_inv_sent_total"] += len(items)
                peer.last_tx_inv_sent_at = time.time()
                log.info(
                    "tx.inv_sent",
                    extra={"peer": peer.remote, "count": len(items)},
                )
                for it in items:
                    if int(it.typ) == int(InvType.TX):
                        self._remember_inv_sent(peer_key, bytes(it.h))
                        log.info(
                            "TX_INV_SENT",
                            extra={
                                "hash": bytes(it.h).hex(),
                                "peer": peer.remote,
                            },
                        )
                        log.info(
                            "tx.inv_sent",
                            extra={"peer": peer.remote, "hash": bytes(it.h).hex()},
                        )
                        log.info(
                            "p2p.tx.inv_sent",
                            extra={"peer": peer.remote, "hash": bytes(it.h).hex()},
                        )
            else:
                self._stats["inv_block_sent"] += len(items)

    async def _announce_pending_txs(self, peer: _PeerState) -> None:
        if self.deps is None:
            return
        fn = getattr(self.deps, "list_pending_hashes", None)
        if not callable(fn):
            return
        try:
            hashes = fn(limit=self._tx_inv_seed_limit)
            if asyncio.iscoroutine(hashes):
                hashes = await hashes
        except Exception:
            return
        if not hashes:
            return
        txids: list[bytes] = []
        for h in hashes:
            if isinstance(h, (bytes, bytearray)) and len(h) == 32:
                txids.append(bytes(h))
        if not txids:
            return
        await self._txrelay.announce_txids(txids, exclude_peer=None)

    async def _pending_tx_rebroadcast_loop(self) -> None:
        try:
            while self._running:
                try:
                    await asyncio.sleep(self._tx_inv_reannounce_interval_s)
                except asyncio.CancelledError:
                    return
                await self._relay_heartbeat()
                await self._rebroadcast_pending_txs()
                await self._relay_heartbeat()
        except asyncio.CancelledError:
            return

    async def _tx_rebroadcast_supervisor(self) -> None:
        while self._running:
            try:
                await self._pending_tx_rebroadcast_loop()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.error(
                    "Tx relay rebroadcast loop crashed",
                    exc_info=exc,
                )
                await asyncio.sleep(1.0)

    async def _relay_heartbeat(self) -> None:
        now = time.time()
        if now - self._tx_relay_heartbeat_at < 30.0:
            return
        self._tx_relay_heartbeat_at = now
        async with self._peer_lock:
            peer_count = len(self._peers)
        pending = await self._mempool_size()
        log.info(
            "TX_RELAY_HEARTBEAT",
            extra={
                "queue": pending,
                "peers": peer_count,
            },
        )

    async def _rebroadcast_pending_txs(self) -> None:
        if self.deps is None:
            return
        fn = getattr(self.deps, "list_pending_hashes", None)
        if not callable(fn):
            return
        try:
            hashes = fn(limit=self._tx_inv_seed_limit)
            if asyncio.iscoroutine(hashes):
                hashes = await hashes
        except Exception:
            return
        if not hashes:
            return
        txids: list[bytes] = []
        for h in hashes:
            if isinstance(h, (bytes, bytearray)) and len(h) == 32:
                txids.append(bytes(h))
        if not txids:
            return
        await self._txrelay.announce_txids(txids, exclude_peer=None)

    # ---------------------------------------------------------------------
    # Dedupe helpers
    # ---------------------------------------------------------------------

    def _remember(
        self, table: "OrderedDict[bytes, float]", key: bytes, cap: int
    ) -> None:
        table[key] = time.time()
        table.move_to_end(key, last=True)
        while len(table) > cap:
            table.popitem(last=False)

    def _seen(self, table: "OrderedDict[bytes, float]", key: bytes) -> bool:
        return key in table

    def _remember_ttl(
        self, table: "OrderedDict[bytes, float]", key: bytes, cap: int, ttl_s: float
    ) -> None:
        expire_at = time.time() + ttl_s
        table[key] = expire_at
        table.move_to_end(key, last=True)
        self._prune_ttl(table, cap=cap)

    def _remember_requested(self, key: bytes, peer: str) -> None:
        expire_at = time.time() + self._tx_relay_ttl_s
        self._tx_requested[key] = (expire_at, peer)
        self._tx_requested.move_to_end(key, last=True)
        self._prune_requested()

    def _requested_recently(self, key: bytes) -> bool:
        now = time.time()
        entry = self._tx_requested.get(key)
        if entry is None:
            return False
        expire_at, _peer = entry
        if expire_at <= now:
            self._tx_requested.pop(key, None)
            return False
        return True

    def _peer_tx_key(self, peer: _PeerState) -> str:
        if peer.session_id:
            return peer.session_id
        if peer.peer_id:
            return peer.peer_id
        return peer.remote

    def _remember_sent(self, peer_key: str, key: bytes) -> None:
        table = self._tx_sent_by_peer.setdefault(peer_key, OrderedDict())
        expire_at = time.time() + self._tx_relay_ttl_s
        table[key] = expire_at
        table.move_to_end(key, last=True)
        self._prune_ttl(table, cap=self._seen_tx_cap)

    def _sent_recently(self, peer_key: str, key: bytes) -> bool:
        table = self._tx_sent_by_peer.get(peer_key)
        if table is None:
            return False
        now = time.time()
        expire_at = table.get(key)
        if expire_at is None:
            return False
        if expire_at <= now:
            table.pop(key, None)
            return False
        return True

    def _remember_inv_sent(self, peer_key: str, key: bytes) -> None:
        table = self._tx_inv_sent_by_peer.setdefault(peer_key, OrderedDict())
        expire_at = time.time() + self._tx_relay_ttl_s
        table[key] = expire_at
        table.move_to_end(key, last=True)
        self._prune_ttl(table, cap=self._tx_inv_seen_cap)

    def _inv_sent_recently(self, peer_key: str, key: bytes) -> bool:
        table = self._tx_inv_sent_by_peer.get(peer_key)
        if table is None:
            return False
        now = time.time()
        expire_at = table.get(key)
        if expire_at is None:
            return False
        if expire_at <= now:
            table.pop(key, None)
            return False
        return True

    def _prune_ttl(
        self, table: "OrderedDict[bytes, float]", *, cap: int
    ) -> None:
        now = time.time()
        for k, exp in list(table.items()):
            if exp <= now:
                table.pop(k, None)
            else:
                break
        while len(table) > cap:
            table.popitem(last=False)

    def _prune_requested(self) -> None:
        now = time.time()
        for k, (exp, _peer) in list(self._tx_requested.items()):
            if exp <= now:
                self._tx_requested.pop(k, None)
            else:
                break
        while len(self._tx_requested) > self._tx_requested_cap:
            self._tx_requested.popitem(last=False)

    def _rate_limit(
        self, table: dict[str, Deque[float]], peer_key: str, limit: int, window_s: float
    ) -> bool:
        if limit <= 0:
            return False
        now = time.time()
        bucket = table.setdefault(peer_key, deque())
        while bucket and now - bucket[0] > window_s:
            bucket.popleft()
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        return False

    # ---------------------------------------------------------------------
    # deps invocation helpers
    # ---------------------------------------------------------------------

    async def _deps_call(self, name: str, *args: Any) -> None:
        if self.deps is None:
            return
        fn = getattr(self.deps, name, None)
        if fn is None:
            return
        if asyncio.iscoroutinefunction(fn):
            with contextlib.suppress(Exception):
                await fn(*args)
        else:
            with contextlib.suppress(Exception):
                fn(*args)

    async def _deps_call_ok(self, name: str, *args: Any) -> bool:
        if self.deps is None:
            return False
        fn = getattr(self.deps, name, None)
        if fn is None:
            return False
        try:
            if asyncio.iscoroutinefunction(fn):
                res = await fn(*args)
            else:
                res = fn(*args)
        except Exception:
            return False
        if isinstance(res, tuple) and res:
            return bool(res[0])
        return bool(res)

    async def _admit_tx_result(
        self,
        raw: bytes,
        *,
        local: bool | None = False,
        origin_peer: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        if self.deps is None:
            return False, "deps_missing"
        fn = getattr(self.deps, "admit_tx", None)
        if fn is None:
            return False, "admit_unavailable"
        try:
            if asyncio.iscoroutinefunction(fn):
                try:
                    res = await fn(raw, local, origin_peer)
                except TypeError:
                    res = await fn(raw)
            else:
                try:
                    res = fn(raw, local, origin_peer)
                except TypeError:
                    res = fn(raw)
        except Exception as exc:
            trace_id = uuid.uuid4().hex
            log.error(
                "txrelay admit_tx internal error trace_id=%s",
                trace_id,
                extra={"trace_id": trace_id, "error": str(exc), "error_class": type(exc).__name__},
                exc_info=True,
            )
            return False, f"internal_error:trace_id={trace_id}"
        if isinstance(res, tuple):
            ok = bool(res[0]) if res else False
            reason = res[1] if len(res) > 1 else None
            if isinstance(reason, str) and reason.startswith("admit_error:"):
                trace_id = uuid.uuid4().hex
                log.error("txrelay received legacy admit_error reason trace_id=%s", trace_id, extra={"trace_id": trace_id, "reason": reason})
                return ok, f"internal_error:trace_id={trace_id}"
            return ok, reason
        return bool(res), None

    async def _eager_push_tx(self, raw: bytes, *, max_peers: int) -> None:
        if max_peers <= 0:
            return
        async with self._peer_lock:
            peers = list(self._peers.values())[:max_peers]
        canonical_raw = raw
        try:
            from core.utils.tx import normalize_tx_bytes

            canonical_raw = normalize_tx_bytes(raw)
        except Exception:
            canonical_raw = raw
        try:
            from core.utils.hash import sha3_256

            txh = sha3_256(canonical_raw)
        except Exception:
            txh = hashlib.sha3_256(canonical_raw).digest()
        for peer in peers:
            with contextlib.suppress(Exception):
                if self._sent_recently(self._peer_tx_key(peer), txh):
                    self._stats["tx_sent_dedup"] += 1
                    continue
                await self._send(peer, MsgID.TX, Tx(raw_cbor=canonical_raw))
                self._stats["tx_sent"] += 1
                self._remember_sent(self._peer_tx_key(peer), txh)

    async def _deps_call_import(self, payload: Any) -> Tuple[bool, Optional[str]]:
        if self.deps is None:
            return False, "deps_missing"
        fn = getattr(self.deps, "import_block", None)
        if fn is None:
            return False, "import_unavailable"
        try:
            if asyncio.iscoroutinefunction(fn):
                res = await fn(payload)
            else:
                res = fn(payload)
        except Exception as e:
            reason = str(e)
            if "genesis" in reason.lower():
                self._sync_fatal_error = reason
            return False, reason
        if isinstance(res, tuple):
            ok = bool(res[0]) if res else False
            reason = res[1] if len(res) > 1 else None
            if not ok and reason and "genesis" in str(reason).lower():
                self._sync_fatal_error = str(reason)
            return ok, reason
        return bool(res), None

    def _record_pow_mismatch(
        self, header_hash: Optional[bytes], *, peer: Optional[_PeerState]
    ) -> bool:
        if header_hash is None:
            return False
        now = time.time()
        entry = self._sync_pow_mismatch_votes.get(header_hash)
        if entry is None:
            entry = {"peers": set(), "last_at": 0.0}
            self._sync_pow_mismatch_votes[header_hash] = entry
        peers = entry["peers"]
        if peer is not None:
            peers.add(peer.remote)
        entry["last_at"] = now
        self._sync_pow_mismatch_votes.move_to_end(header_hash, last=True)
        while len(self._sync_pow_mismatch_votes) > 1024:
            self._sync_pow_mismatch_votes.popitem(last=False)
        return len(peers) >= 2
