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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Deque, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse
from urllib.request import urlopen
from p2p import version as p2p_version
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
from p2p.transport.base import ListenConfig
from p2p.constants import DEFAULT_TCP_PORT
from p2p.transport.multiaddr import parse_multiaddr
from p2p.transport.tcp import TcpTransport
from p2p.wire.encoding import decode_payload, encode_payload
from p2p.wire.frames import Framer, unpack_frame
from p2p.wire.message_ids import MsgID
from p2p.wire.messages import (
    AddressAnnounce,
    Blocks,
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
from p2p.node.peer_registry import PeerRegistry

log = logging.getLogger("animica.p2p.service")

DEFAULT_BOOTSTRAP_SEEDS = [
    "/dns4/mainnet.animica.org/tcp/30333",
]


@dataclass(slots=True)
class _PeerState:
    session_id: str
    remote: str
    direction: str  # "inbound" | "outbound"
    conn: Any
    stream: Any
    framer: Framer
    write_lock: asyncio.Lock
    peer_id: Optional[str] = None  # hex string
    hello: Optional[dict] = None
    hello_done: asyncio.Event = field(default_factory=asyncio.Event)
    pending_headers: Optional[asyncio.Future] = None
    pending_header_request_id: Optional[str] = None
    ready_for_sync: bool = False
    connected_at: float = field(default_factory=time.time)
    feeler: bool = False
    known_addrs: "OrderedDict[str, float]" = field(default_factory=OrderedDict)
    misbehavior_score: int = 0
    invalid_headers: int = 0
    invalid_blocks: int = 0
    invalid_msgs: int = 0
    timeouts: int = 0
    notfound: int = 0
    missing_parent: int = 0
    stall_events: int = 0
    empty_header_responses: int = 0
    last_msg_at: float = field(default_factory=time.time)
    last_progress_at: float = field(default_factory=time.time)
    ban_until: Optional[float] = None
    latency_ewma: Optional[float] = None
    netgroup: Optional[str] = None
    last_header_request_at: Optional[float] = None
    last_block_request_at: Optional[float] = None


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
    learned_addrs_1m: int
    announced_addrs_1m: int
    persisted_peer_count: Optional[int]
    dial_last_error: Optional[dict[str, Any]] = None
    bootstrap_last_attempt: Optional[dict[str, Any]] = None
    bootstrap_last_success: Optional[dict[str, Any]] = None
    bootstrap_last_error: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "p2p_running": self.p2p_running,
            "listen_addrs": list(self.listen_addrs),
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
            "learned_addrs_1m": self.learned_addrs_1m,
            "announced_addrs_1m": self.announced_addrs_1m,
            "persisted_peer_count": self.persisted_peer_count,
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


@dataclass(slots=True)
class SyncStatusSnapshot:
    phase: str
    head_height: int
    head_hash: Optional[str]
    best_header_height: int
    best_header_hash: Optional[str]
    best_block_height: int
    best_block_hash: Optional[str]
    in_flight: int
    in_flight_headers: int
    in_flight_blocks: int
    queued_blocks_count: int
    last_progress_at: float
    last_header_progress_at: float
    last_block_progress_at: float
    last_header_at: float
    last_block_at: float
    last_header_request_at: float
    last_header_response_at: float
    last_header_response_count: int
    last_block_request_at: float
    last_block_response_at: float
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
    pending_header_batches: int
    synchronized: bool
    peer_penalties: Dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "head_height": self.head_height,
            "head_hash": self.head_hash,
            "best_header_height": self.best_header_height,
            "best_header_hash": self.best_header_hash,
            "best_block_height": self.best_block_height,
            "best_block_hash": self.best_block_hash,
            "in_flight": self.in_flight,
            "in_flight_headers": self.in_flight_headers,
            "in_flight_blocks": self.in_flight_blocks,
            "queued_blocks_count": self.queued_blocks_count,
            "last_progress_at": self.last_progress_at,
            "last_header_progress_at": self.last_header_progress_at,
            "last_block_progress_at": self.last_block_progress_at,
            "last_header_at": self.last_header_at,
            "last_block_at": self.last_block_at,
            "last_header_request_at": self.last_header_request_at,
            "last_header_response_at": self.last_header_response_at,
            "last_header_response_count": self.last_header_response_count,
            "last_block_request_at": self.last_block_request_at,
            "last_block_response_at": self.last_block_response_at,
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
            "pending_header_batches": self.pending_header_batches,
            "synchronized": self.synchronized,
            "peer_penalties": dict(self.peer_penalties),
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
        self._seed_keys = {self._addr_key(s) for s in self.seeds}

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
        writable_peerstore = ensure_writable(peerstore_path)
        peerstore_path = writable_peerstore.path
        peerstore_dir = (
            peerstore_path if not peerstore_path.suffix else peerstore_path.parent
        )
        self._peerstore_dir = peerstore_dir
        self._peers_json_path = peerstore_dir / "peers.json"
        self._peerstore_fallback_path = writable_peerstore.fallback_path

        # Identity + stable peer id (co-locate with peerstore by default)
        identity_path = os.environ.get("ANIMICA_P2P_IDENTITY_PATH")
        if not identity_path:
            identity_path = peerstore_dir / "identity.json"
        identity_path = Path(identity_path).expanduser()
        writable_identity = ensure_writable(identity_path)
        identity_path = writable_identity.path
        if writable_identity.used_fallback:
            log.warning(
                "P2P identity path not writable; using fallback",
                extra={
                    "requested": str(writable_identity.fallback_path),
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

        # Persistent peerstore
        self._ensure_peerstore_dir(peerstore_dir)
        fallback_json = (
            self._peerstore_fallback_path / "peers.json"
            if self._peerstore_fallback_path
            else None
        )
        self.peerstore = pstore.open_peerstore(peerstore_path, json_fallback=fallback_json)

        # Transport (TCP only for now)
        prologue = f"animica/tcp/{self.chain_id}".encode()
        self._transport = TcpTransport(
            handshake_prologue=prologue, chain_id=self.chain_id
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
        self._seed_hosts = self._seed_hostnames(self.seeds)

        self._peer_lock = asyncio.Lock()
        self._peers: dict[str, _PeerState] = {}  # remote -> state
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
        )

        # Seen LRU (dedupe + rebroadcast suppression)
        self._seen_tx: "OrderedDict[bytes, float]" = OrderedDict()
        self._seen_blocks: "OrderedDict[bytes, float]" = OrderedDict()
        self._seen_tx_cap = 50_000
        self._seen_block_cap = 10_000
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
        self._sync_header_events: Deque[dict[str, Any]] = deque(
            maxlen=int(os.environ.get("ANIMICA_P2P_SYNC_DEBUG_EVENTS", "50") or 50)
        )
        self._sync_header_sources: Dict[bytes, str] = {}
        self._sync_no_headers_threshold = int(
            os.environ.get("ANIMICA_P2P_NO_HEADERS_THRESHOLD", "3") or 3
        )
        self._sync_no_headers_backoff = float(
            os.environ.get("ANIMICA_P2P_NO_HEADERS_BACKOFF", "15.0") or 15.0
        )

        # Tiny metrics snapshot used by RPC/CLI
        self._stats: dict[str, int] = {
            "peers": 0,
            "inv_tx_sent": 0,
            "inv_tx_recv": 0,
            "tx_recv": 0,
            "tx_sent": 0,
            "inv_block_sent": 0,
            "inv_block_recv": 0,
            "blocks_sent": 0,
            "blocks_recv": 0,
            "blocks_requested": 0,
            "blocks_received": 0,
            "blocks_validated_ok": 0,
            "blocks_imported": 0,
            "blocks_rejected": 0,
            "sync_rounds": 0,
            "p2p_peers_rejected_genesis_mismatch": 0,
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
        self._external_ip = os.environ.get("ANIMICA_P2P_EXTERNAL_IP")
        self._external_ip_endpoint = (
            os.environ.get("ANIMICA_P2P_EXTERNAL_IP_ENDPOINT")
            or os.environ.get("ANIMICA_PUBLIC_IP_ENDPOINT")
        )
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
        self._ban_enabled = False
        self._banlist_path = peerstore_dir / "bans.json"
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
        self._missing_parent_threshold = int(
            os.environ.get("ANIMICA_P2P_MISSING_PARENT_THRESHOLD", "3") or 3
        )

        self._sync_lock = asyncio.Lock()
        self._sync_wakeup = asyncio.Event()
        self._sync_phase = "IDLE"
        self._sync_best_header: Optional[_SyncHeader] = None
        self._sync_headers: Dict[bytes, _SyncHeader] = {}
        self._sync_header_queue: Deque[Tuple[str, List[HeaderCompact]]] = deque()
        self._sync_inflight_header_requests: Dict[tuple[str, str], float] = {}
        self._sync_inflight_blocks: Dict[bytes, float] = {}
        self._sync_inflight_peers: Dict[bytes, str] = {}
        self._sync_block_buffer: "OrderedDict[bytes, _SyncBlock]" = OrderedDict()
        self._sync_block_queue: Deque[bytes] = deque()
        self._sync_block_queue_set: set[bytes] = set()
        self._sync_block_queue_heights: Dict[bytes, int] = {}
        self._sync_last_block_error: Optional[str] = None
        self._sync_last_block_error_at: Optional[float] = None
        self._sync_fatal_error: Optional[str] = None
        self._sync_block_stalled_reason: Optional[str] = None
        self._sync_peer_penalties: Dict[str, int] = {}
        self._sync_peer_penalty_whitelist = {"144.126.133.21:30333"}
        self._sync_last_progress_at = time.time()
        self._sync_last_header_at = 0.0
        self._sync_last_block_at = 0.0
        self._sync_last_header_request_at = 0.0
        self._sync_last_header_response_at = 0.0
        self._sync_last_header_response_count = 0
        self._sync_last_block_request_at = 0.0
        self._sync_last_block_response_at = 0.0
        self._sync_last_header_request_peer: Optional[str] = None
        self._sync_last_header_response_peer: Optional[str] = None
        self._sync_last_header_error: Optional[str] = None
        self._sync_last_header_error_at: Optional[float] = None
        self._sync_last_header_error_peer: Optional[str] = None
        self._sync_active_header_peer: Optional[str] = None
        self._sync_active_block_peer: Optional[str] = None
        self._sync_inflight_headers = 0
        self._sync_max_inflight = int(
            os.environ.get("ANIMICA_P2P_SYNC_INFLIGHT", "32") or 32
        )
        self._sync_max_inflight_per_peer = int(
            os.environ.get("ANIMICA_P2P_SYNC_INFLIGHT_PER_PEER", "16") or 16
        )
        self._sync_headers_batch = int(
            os.environ.get("ANIMICA_P2P_SYNC_HEADERS_BATCH", "128") or 128
        )
        self._sync_request_timeout = float(
            os.environ.get("ANIMICA_P2P_SYNC_TIMEOUT", "8.0") or 8.0
        )
        self._sync_peer_penalty_threshold = int(
            os.environ.get("ANIMICA_P2P_SYNC_PENALTY_THRESHOLD", "3") or 3
        )
        self._sync_stall_timeout = float(
            os.environ.get("ANIMICA_P2P_SYNC_STALL_TIMEOUT", "20.0") or 20.0
        )
        self._sync_tip_tolerance = int(
            os.environ.get("ANIMICA_P2P_SYNC_TIP_TOLERANCE", "2") or 2
        )
        self._bootstrap_attempts: deque[dict[str, Any]] = deque(maxlen=512)
        self._last_bootstrap_attempt: Optional[dict[str, Any]] = None
        self._last_bootstrap_success: Optional[dict[str, Any]] = None
        self._last_bootstrap_error: Optional[dict[str, Any]] = None
        self._last_peer_connect_at: Optional[float] = None
        self._last_peer_disconnect_at: Optional[float] = None
        self._invalid_seed_addrs: set[str] = set()

        class _Metrics:
            def __init__(self, svc: "P2PService") -> None:
                self._svc = svc

            @property
            def peer_count(self) -> int:
                return int(self._svc._stats.get("peers", 0))

        self.metrics = _Metrics(self)

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

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
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
            self._seed_keys.update({self._addr_key(a) for a in discovered})
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

        self._tasks = [
            asyncio.create_task(self._accept_loop(), name="p2p.accept"),
            asyncio.create_task(self._dial_loop(), name="p2p.dial"),
            asyncio.create_task(self._head_watch_loop(), name="p2p.head_watch"),
            asyncio.create_task(self._sync_loop(), name="p2p.sync"),
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
        ]
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
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

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
            records[key] = {
                "addr": rec.address,
                "last_seen": rec.last_seen,
                "last_success": rec.last_success,
                "last_failure": rec.last_failure,
                "failure_reason": rec.failure_reason,
                "score": rec.score,
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
                    "last_seen": last_seen,
                    "score": 0.0,
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

    def _dial_delay(self, addr_key: str) -> float:
        attempts = self._dial_attempts.get(addr_key, 0)
        base = 2.0 * (2 ** min(attempts, 5))
        jitter = random.uniform(0.6, 1.4)
        return min(60.0, base * jitter)

    def _is_invalid_seed_error(self, error: str) -> bool:
        lowered = error.lower()
        return "invalid handshake magic" in lowered or "handshakeerror" in lowered

    def _mark_dial_failure(self, addr: str, *, is_seed: bool, error: str) -> None:
        addr_key = self._addr_key(addr)
        attempts = self._dial_attempts.get(addr_key, 0) + 1
        self._dial_attempts[addr_key] = attempts
        self._dial_last_error = {
            "addr": addr,
            "error": error,
            "attempts": attempts,
            "at": time.time(),
        }
        delay = self._dial_delay(addr_key)
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
                self._seed_keys.discard(addr_key)
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

    def _mark_dial_success(self, addr: str, *, is_seed: bool) -> None:
        addr_key = self._addr_key(addr)
        self._dial_attempts.pop(addr_key, None)
        self._dial_backoff.pop(addr_key, None)
        self._dial_success_total += 1
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
        identified = [
            p for p in snapshot if p.get("peer_id") and p.get("peer_id") != "unknown"
        ]
        inbound = sum(1 for p in identified if p.get("direction") == "inbound")
        outbound = sum(1 for p in identified if p.get("direction") == "outbound")
        bootstrap_bonus = self.bootstrap_peer_bonus()
        now = time.time()
        attempts_last_5m = sum(
            1 for entry in self._bootstrap_attempts if now - entry.get("at", 0) <= 300
        )
        addrman_size = self._addrman.size()
        learned_1m = self._addr_events_last_minute(self._addr_learned_events)
        announced_1m = self._addr_events_last_minute(self._addr_announced_events)

        return P2PStatusSnapshot(
            p2p_running=self._running,
            listen_addrs=list(self.listen_addrs),
            peers_total=self._peer_registry.peer_count() + bootstrap_bonus,
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
            learned_addrs_1m=learned_1m,
            announced_addrs_1m=announced_1m,
            persisted_peer_count=self._persisted_peer_count,
            dial_last_error=self._dial_last_error,
            bootstrap_last_attempt=self._last_bootstrap_attempt,
            bootstrap_last_success=self._last_bootstrap_success,
            bootstrap_last_error=self._last_bootstrap_error,
        )

    def sync_status_snapshot(self) -> SyncStatusSnapshot:
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
        network_best_height = self._network_best_height()
        if network_best_height is None:
            synchronized = False
        else:
            remote_target = max(0, int(network_best_height) - self._sync_tip_tolerance)
            synchronized = (
                best_block_height > 0
                and best_header_height >= remote_target
                and best_block_height >= min(best_header_height, network_best_height)
            )
        queued_blocks_count = self._queued_blocks_count(best_block_height)
        synchronized = synchronized and self._sync_status_invariants(
            head_height=best_block_height,
            best_header_height=best_header_height,
            queued_blocks_count=queued_blocks_count,
            in_flight_headers=int(self._sync_inflight_headers),
            in_flight_blocks=len(self._sync_inflight_blocks),
            last_header_error=self._sync_last_header_error,
            last_block_error=self._sync_last_block_error,
        )
        eligible_peers, ineligible_peers = self._eligible_sync_peers()
        phase = self._derive_sync_phase(
            best_header_height=best_header_height,
            best_block_height=best_block_height,
            pending_header_batches=len(self._sync_header_queue),
            eligible_header_peers=len(eligible_peers),
            synchronized=synchronized,
        )
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
        return SyncStatusSnapshot(
            phase=phase,
            head_height=best_block_height,
            head_hash=head_hex,
            best_header_height=best_header_height,
            best_header_hash=best_header_hash,
            best_block_height=best_block_height,
            best_block_hash=best_block_hash,
            in_flight=len(self._sync_inflight_blocks),
            in_flight_headers=int(self._sync_inflight_headers),
            in_flight_blocks=len(self._sync_inflight_blocks),
            queued_blocks_count=queued_blocks_count,
            last_progress_at=self._sync_last_progress_at,
            last_header_progress_at=self._sync_last_header_at,
            last_block_progress_at=self._sync_last_block_at,
            last_header_at=self._sync_last_header_at,
            last_block_at=self._sync_last_block_at,
            last_header_request_at=self._sync_last_header_request_at,
            last_header_response_at=self._sync_last_header_response_at,
            last_header_response_count=self._sync_last_header_response_count,
            last_block_request_at=self._sync_last_block_request_at,
            last_block_response_at=self._sync_last_block_response_at,
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
            pending_header_batches=len(self._sync_header_queue),
            synchronized=synchronized,
            peer_penalties={
                remote: count
                for remote, count in self._sync_peer_penalties.items()
                if remote not in self._sync_peer_penalty_whitelist
            },
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
            seed_hosts = {"mainnet.animica.org"}
            if result.addr.host in seed_hosts:
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
                hosts.add(parsed.addr.host)
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

    def _is_seed_peer(self, peer: _PeerState) -> bool:
        addr_key = self._addr_key(peer.remote)
        if addr_key in self._seed_keys:
            return True
        host = self._extract_host(peer.remote)
        if host and host in self._seed_hosts:
            return True
        return False

    def _is_banned(self, key: str, *, now: Optional[float] = None) -> bool:
        if not self._ban_enabled:
            return False
        now = time.time() if now is None else now
        if not key:
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
        eligible_header_peers: int = 0,
        synchronized: bool = False,
    ) -> str:
        if self._sync_block_stalled_reason:
            return "STALLED"
        if pending_header_batches > 0 or self._sync_inflight_headers:
            return "HEADERS"
        if best_header_height > best_block_height:
            return "BLOCKS"
        if self._sync_inflight_blocks or self._sync_block_buffer:
            return "VERIFYING"
        if synchronized:
            return "SYNCED"
        if eligible_header_peers > 0:
            return "SYNCING"
        return "IDLE"

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
    ) -> bool:
        if self._sync_block_stalled_reason:
            return False
        if queued_blocks_count != 0:
            return False
        if in_flight_headers != 0 or in_flight_blocks != 0:
            return False
        if best_header_height > head_height:
            return False
        if last_block_error:
            return False
        if last_header_error not in (None, "at_tip"):
            return False
        return True

    def _canonical_head_for_status(self) -> tuple[int, Optional[str]]:
        try:
            bdb = self._block_db()
        except Exception:
            return self._local_head()
        head = None
        if hasattr(bdb, "get_canonical_head"):
            head = bdb.get_canonical_head()
        if head is None:
            head = bdb.get_head()
        if head:
            height = int(head[0])
            head_hash = bytes(head[1])
            if self._has_header(head_hash):
                return height, "0x" + head_hash.hex()
            recovered = self._recover_head_from_canonical(height)
            if recovered is not None:
                recovered_height, recovered_hash = recovered
                return recovered_height, "0x" + recovered_hash.hex()
        return self._local_head()

    def _maybe_mark_block_stalled(self, now: float) -> None:
        if self._sync_best_header is None:
            return
        best_block_height, _ = self._local_head()
        if self._sync_best_header.height <= int(best_block_height or 0):
            return
        if self._sync_last_block_at <= 0:
            return
        if self._sync_last_header_at <= self._sync_last_block_at:
            return
        if now - self._sync_last_block_at <= self._sync_stall_timeout:
            return
        if now - self._sync_last_header_at > self._sync_stall_timeout:
            return
        if not self._sync_block_stalled_reason:
            self._sync_block_stalled_reason = "blocks stalled"
            self._sync_last_block_error = "blocks stalled"
            self._sync_last_block_error_at = now
            log.warning(
                "Block sync stalled",
                extra={
                    "last_block_at": self._sync_last_block_at,
                    "last_header_at": self._sync_last_header_at,
                },
            )

    def _handle_sync_stall(self, *, reason: str) -> None:
        now = time.time()
        if self._sync_best_header is None:
            return
        if self._last_rotation_at and now - self._last_rotation_at < 5.0:
            return
        old_peer = None
        if self._sync_active_block_peer:
            old_peer = self._peers.get(self._sync_active_block_peer)
        if old_peer:
            self._penalize_peer(
                old_peer,
                "stall",
                points=self._score_points["stall"],
                severity=2,
                quarantine_s=30.0,
            )
            old_peer.last_progress_at = now
        self._sync_inflight_blocks.clear()
        self._sync_inflight_peers.clear()
        new_peer = self._select_sync_peer(avoid_peer=old_peer)
        if new_peer:
            self._sync_active_block_peer = new_peer.remote
        self._last_rotation_at = now
        local_height, _ = self._local_head()
        best_header_height = self._sync_best_header.height
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
            },
        )

    def _rotate_sync_peer(self) -> None:
        active = self._peers.get(self._sync_active_block_peer) if self._sync_active_block_peer else None
        candidate = self._select_sync_peer(avoid_peer=active)
        if not candidate or (active and candidate.remote == active.remote):
            return
        if active is None:
            self._sync_active_block_peer = candidate.remote
            return
        active_latency = active.latency_ewma if active.latency_ewma is not None else 9999.0
        candidate_latency = (
            candidate.latency_ewma if candidate.latency_ewma is not None else 9999.0
        )
        if (
            candidate.misbehavior_score < active.misbehavior_score
            or candidate_latency < active_latency
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
        fallback_port = self._local_listen_port()
        if not port:
            port = fallback_port
        return self._sanitize_peer_addr(f"{host}:{port}", fallback_port=fallback_port)

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
                "dial_attempted": 0,
                "dial_success": 0,
                "errors": ["no addresses provided"],
            }

        fallback_port = self._local_listen_port()
        normalized: list[str] = []
        errors: list[str] = []
        skipped = 0
        for raw in addresses:
            addr = self._sanitize_peer_addr(raw, fallback_port=fallback_port)
            if not addr:
                skipped += 1
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
            "dial_attempted": dial_attempted,
            "dial_success": dial_success,
            "errors": errors,
        }

    async def force_sync(self) -> dict[str, Any]:
        self._sync_wakeup.set()
        return await self._sync_once(force=True)

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

    async def relay_tx(self, raw_cbor: bytes) -> str:
        from core.utils.hash import sha3_256

        txh = sha3_256(raw_cbor)
        self._remember(self._seen_tx, txh, self._seen_tx_cap)

        # best-effort local admission
        await self._deps_call("admit_tx", raw_cbor)

        await self._broadcast_inv(
            [InvItem(typ=InvType.TX, h=txh)], exclude_remote=None, is_tx=True
        )
        return "0x" + txh.hex()

    async def relay_block(self, block_hash: bytes) -> None:
        self._remember(self._seen_blocks, block_hash, self._seen_block_cap)
        await self._broadcast_inv(
            [InvItem(typ=InvType.BLOCK, h=block_hash)], exclude_remote=None, is_tx=False
        )

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
                    active_keys = {self._addr_key(p.remote) for p in outbound}
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
                    if self._is_banned(addr):
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
                    is_seed = addr_key in self._seed_keys
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
            log.info("Skipping self dial target %s", parsed.addr.canonical)
            return False
        addr = parsed.addr.canonical
        addr_key = self._addr_key(addr)
        self._dial_attempt_total += 1
        if is_seed:
            resolved = await self._resolve_seed_host(addr)
            if not resolved:
                self._mark_dial_failure(addr, is_seed=True, error="dns_lookup_failed")
                self._dial_inflight.discard(addr_key)
                return False
        try:
            conn = await self._transport.dial(addr, timeout=5.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            err = f"{exc.__class__.__name__}: {exc}"
            self._mark_dial_failure(addr, is_seed=is_seed, error=err)
            return False
        finally:
            self._dial_inflight.discard(addr_key)
        self._mark_dial_success(addr, is_seed=is_seed)
        await self._register_conn(conn, direction="outbound", feeler=feeler)
        return True

    async def _register_conn(
        self, conn: Any, *, direction: str, feeler: bool = False
    ) -> None:
        remote = getattr(conn.info, "remote_addr", None) or "unknown"
        if self._is_self_address(
            self._extract_host(remote), self._extract_port(remote) or 0
        ):
            log.info("Rejecting self peer %s", remote)
            with contextlib.suppress(Exception):
                await conn.close()
            return
        if self._is_banned(remote):
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
            connected_at=session.connected_at,
            feeler=feeler,
            netgroup=netgroup,
        )

        async with self._peer_lock:
            self._peers[remote] = peer
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
            log.info("Dropping peer %s due to handshake timeout", peer.remote)
            await self._drop_peer(peer, reason="handshake_timeout")

    async def _peer_loop(self, peer: _PeerState) -> None:
        # Send HELLO immediately (both sides do this; handler is symmetric).
        try:
            await self._send_hello(peer)
        except Exception:
            pass

        disconnect_reason = "loop_exit"
        try:
            while self._running:
                data = await peer.stream.recv()
                if data == b"":
                    disconnect_reason = "remote_closed"
                    break
                self._peer_registry.mark_seen(peer.session_id)
                peer.last_msg_at = time.time()
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
        self._peer_registry.remove(peer.session_id)

        async with self._peer_lock:
            self._peers.pop(peer.remote, None)
            self._peers_by_session.pop(peer.session_id, None)
            self._stats["peers"] = self._peer_registry.peer_count()
            self._last_peer_disconnect_at = time.time()

        if peer.peer_id:
            with contextlib.suppress(Exception):
                self.peerstore.record_disconnection(peer.peer_id, reason=reason)

        uptime = time.time() - peer.connected_at if peer.connected_at else 0.0
        log.info(
            "Peer disconnected",
            extra={
                "peer_id": peer.peer_id or "unknown",
                "remote": peer.remote,
                "reason": reason,
                "direction": peer.direction,
                "uptime_s": round(uptime, 2),
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

    async def _send_hello(self, peer: _PeerState) -> None:
        height, head_hash_hex = self._local_head()
        head_hash = (
            bytes.fromhex(head_hash_hex[2:]) if head_hash_hex else (b"\x00" * 32)
        )
        listen_port = self._local_listen_port()
        listen_addrs = self._advertised_addrs()
        hello = Hello(
            version="2",
            agent=f"animica-p2p/{p2p_version.__version__}",
            chain_id=self.chain_id,
            listen_port=listen_port,
            listen_addrs=listen_addrs,
            genesis_hash=self._genesis_hash(),
            fork_id=self._fork_id(),
            consensus_id=self._consensus_id(),
            protocol_version=self._protocol_version(),
            genesis_identity=self._genesis_identity(),
            network_params_hash=self._network_params_hash(),
            peer_id=self._peer_id_bytes,
            head_height=height,
            head_hash=head_hash,
            alg_policy_root=b"",
            capabilities=["tx", "blocks", "sync"],
            timestamp=int(time.time()),
        )
        await self._send(peer, MsgID.HELLO, hello)

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
        if mid == int(MsgID.GETDATA):
            await self._handle_getdata(peer, payload)
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
        peer_genesis_identity: Optional[bytes] = None,
        peer_network_params_hash: Optional[bytes] = None,
        peer_fork_id: Optional[int] = None,
        peer_consensus_id: Optional[str] = None,
        peer_protocol_version: Optional[str] = None,
    ) -> None:
        local_genesis = self._genesis_hash()
        local_genesis_identity = self._genesis_identity()
        local_params_hash = self._network_params_hash()
        log.warning(
            "Peer handshake mismatch",
            extra={
                "remote": peer.remote,
                "reason": reason,
                "local_chain_id": self.chain_id,
                "local_genesis_hash": local_genesis.hex(),
                "local_genesis_identity": local_genesis_identity.hex(),
                "local_network_params_hash": local_params_hash.hex(),
                "local_fork_id": self._fork_id(),
                "local_consensus_id": self._consensus_id(),
                "local_protocol_version": self._protocol_version(),
                "peer_chain_id": peer_chain_id,
                "peer_genesis_hash": peer_genesis_hash.hex()
                if peer_genesis_hash
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
            },
        )

    async def _handle_hello(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        allowed = set(Hello.__dataclass_fields__)
        hello = Hello(**{k: v for k, v in data.items() if k in allowed})

        if int(hello.chain_id) != int(self.chain_id):
            self._log_handshake_mismatch(
                peer,
                reason="chain_id_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="chain_id_mismatch"),
            )
            raise PeerMisbehavior(
                "chain_id_mismatch", points=0
            )

        if not hello.genesis_hash:
            self._stats["p2p_peers_rejected_genesis_mismatch"] += 1
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

        if bytes(hello.genesis_hash) != self._genesis_hash():
            self._stats["p2p_peers_rejected_genesis_mismatch"] += 1
            self._log_handshake_mismatch(
                peer,
                reason="genesis_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
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

        if not getattr(hello, "fork_id", None):
            self._log_handshake_mismatch(
                peer,
                reason="fork_id_missing",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
                peer_fork_id=getattr(hello, "fork_id", None),
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="fork_id_missing"),
            )
            raise PeerMisbehavior(
                "fork_id_missing",
                points=self._score_points["wrong_chain"],
            )

        if int(getattr(hello, "fork_id", 0) or 0) != int(self._fork_id()):
            self._log_handshake_mismatch(
                peer,
                reason="fork_id_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
                peer_fork_id=int(getattr(hello, "fork_id", 0) or 0),
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

        if not getattr(hello, "consensus_id", None):
            self._log_handshake_mismatch(
                peer,
                reason="consensus_missing",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
                peer_consensus_id=getattr(hello, "consensus_id", None),
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="consensus_missing"),
            )
            raise PeerMisbehavior(
                "consensus_missing",
                points=self._score_points["wrong_chain"],
            )

        if str(getattr(hello, "consensus_id", "")) != str(self._consensus_id()):
            self._log_handshake_mismatch(
                peer,
                reason="consensus_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
                peer_consensus_id=str(getattr(hello, "consensus_id", "")),
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

        if not getattr(hello, "protocol_version", None):
            self._log_handshake_mismatch(
                peer,
                reason="protocol_missing",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
                peer_protocol_version=getattr(hello, "protocol_version", None),
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="protocol_missing"),
            )
            raise PeerMisbehavior(
                "protocol_missing",
                points=self._score_points["wrong_chain"],
            )

        if str(getattr(hello, "protocol_version", "")) != str(self._protocol_version()):
            self._log_handshake_mismatch(
                peer,
                reason="protocol_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
                peer_protocol_version=str(getattr(hello, "protocol_version", "")),
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

        if not hello.genesis_identity:
            self._log_handshake_mismatch(
                peer,
                reason="genesis_identity_missing",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="genesis_identity_missing"),
            )
            raise PeerMisbehavior(
                "genesis_identity_missing",
                points=self._score_points["wrong_chain"],
            )

        if bytes(hello.genesis_identity) != self._genesis_identity():
            self._log_handshake_mismatch(
                peer,
                reason="genesis_identity_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
                peer_genesis_identity=bytes(hello.genesis_identity or b""),
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

        if not hello.network_params_hash:
            self._log_handshake_mismatch(
                peer,
                reason="network_params_missing",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
                peer_genesis_identity=bytes(hello.genesis_identity or b""),
            )
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="network_params_missing"),
            )
            raise PeerMisbehavior(
                "network_params_missing",
                points=self._score_points["wrong_chain"],
            )

        if bytes(hello.network_params_hash) != self._network_params_hash():
            self._log_handshake_mismatch(
                peer,
                reason="network_params_mismatch",
                peer_chain_id=int(hello.chain_id or 0),
                peer_genesis_hash=bytes(hello.genesis_hash or b""),
                peer_genesis_identity=bytes(hello.genesis_identity or b""),
                peer_network_params_hash=bytes(hello.network_params_hash or b""),
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

        if hello.version and str(hello.version) not in {"1", "2"}:
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
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="clock_skew"),
            )
            raise PeerMisbehavior("clock_skew", points=20)

        peer.peer_id = bytes(hello.peer_id).hex()
        if peer.peer_id and peer.peer_id == self._peer_id_bytes.hex():
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="self_peer"),
            )
            raise PeerMisbehavior("self_peer", points=0)
        if peer.peer_id and self._is_banned(peer.peer_id):
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="banned"),
            )
            raise PeerMisbehavior("banned", points=0)
        remote_host = self._extract_host(peer.remote)
        remote_port = self._extract_port(peer.remote) or 0
        if self._is_self_address(remote_host, remote_port):
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="self_peer"),
            )
            raise PeerMisbehavior("self_peer", points=0)
        normalized = dict(data)
        normalized["chain_id"] = int(getattr(hello, "chain_id", 0) or 0)
        normalized["head_height"] = int(
            getattr(hello, "head_height", 0)
            or data.get("head_height")
            or data.get("headHeight")
            or data.get("height")
            or 0
        )
        normalized["head_hash"] = bytes(getattr(hello, "head_hash", b"")) or data.get(
            "head_hash"
        ) or data.get("headHash")
        normalized["genesis_hash"] = bytes(
            getattr(hello, "genesis_hash", b"")
        ) or data.get("genesis_hash") or data.get("genesisHash")
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
        normalized["genesis_identity"] = bytes(
            getattr(hello, "genesis_identity", b"")
        ) or data.get("genesis_identity") or data.get("genesisIdentity")
        normalized["network_params_hash"] = bytes(
            getattr(hello, "network_params_hash", b"")
        ) or data.get("network_params_hash") or data.get("networkParamsHash")
        peer.hello = normalized
        peer.hello_done.set()
        peer.ready_for_sync = True

        listen_port = int(getattr(hello, "listen_port", 0) or 0)
        reported_addr = self._reported_peer_addr(peer.remote, listen_port)
        if reported_addr:
            parsed = self._normalize_peer_addr(
                reported_addr, fallback_port=self._local_listen_port()
            )
            if parsed.addr and self._is_self_address(parsed.addr.host, parsed.addr.port):
                await self._send(
                    peer,
                    MsgID.HELLO_ACK,
                    HelloAck(accepted=False, reason="self_peer"),
                )
                raise PeerMisbehavior("self_peer", points=0)
        reported_addrs: list[str] = []
        if reported_addr:
            reported_addrs.append(reported_addr)
        fallback_port = self._local_listen_port()
        for addr in list(getattr(hello, "listen_addrs", []) or []):
            parsed = self._normalize_peer_addr(
                addr, fallback_port=fallback_port, source=f"hello:{peer.remote}"
            )
            if parsed.addr and self._is_self_address(parsed.addr.host, parsed.addr.port):
                await self._send(
                    peer,
                    MsgID.HELLO_ACK,
                    HelloAck(accepted=False, reason="self_peer"),
                )
                raise PeerMisbehavior("self_peer", points=0)
            sanitized = self._sanitize_peer_addr(
                addr, fallback_port=fallback_port, source=f"hello:{peer.remote}"
            )
            if sanitized:
                reported_addrs.append(sanitized)
        reported_addrs = list(dict.fromkeys(reported_addrs))
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

        # Deduplicate connections for the same peer_id (keep the newest).
        to_drop = self._peer_registry.mark_identified(peer.session_id, peer.peer_id)
        for session_id in to_drop:
            if session_id == peer.session_id:
                await self._drop_peer(peer, reason="duplicate_peer_id")
                return
            other = self._peers_by_session.get(session_id)
            if other:
                await self._drop_peer(other, reason="duplicate_peer_id")
        self._stats["peers"] = self._peer_registry.peer_count()

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

        await self._send(peer, MsgID.HELLO_ACK, HelloAck(accepted=True, reason=None))
        self._sync_wakeup.set()
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

    async def _close_feeler_after_delay(self, peer: _PeerState) -> None:
        try:
            await asyncio.sleep(self._feeler_hold_s)
            await self._drop_peer(peer, reason="feeler_complete")
        except asyncio.CancelledError:
            return

    async def _handle_inv(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        items = data.get("items") or []
        inv_items: list[InvItem] = []
        for it in items:
            if isinstance(it, dict):
                inv_items.append(InvItem(**it))
        inv = Inv(items=inv_items)

        want: list[InvItem] = []
        for it in inv.items:
            if int(it.typ) == int(InvType.TX):
                self._stats["inv_tx_recv"] += 1
                if self._pending_get(bytes(it.h)) is None and not self._seen(
                    self._seen_tx, bytes(it.h)
                ):
                    want.append(InvItem(typ=InvType.TX, h=bytes(it.h)))
            elif int(it.typ) == int(InvType.BLOCK):
                self._stats["inv_block_recv"] += 1
                if not self._has_block(bytes(it.h)):
                    want.append(InvItem(typ=InvType.BLOCK, h=bytes(it.h)))

        if want:
            await self._send(peer, MsgID.GETDATA, GetData(items=want))
            if any(int(it.typ) == int(InvType.BLOCK) for it in want):
                self._sync_wakeup.set()

    async def _handle_getdata(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        items = data.get("items") or []
        req_items: list[InvItem] = []
        for it in items:
            if isinstance(it, dict):
                req_items.append(InvItem(**it))
        req = GetData(items=req_items)

        txs: list[bytes] = []
        blocks: list[bytes] = []
        for it in req.items:
            if int(it.typ) == int(InvType.TX):
                raw = self._pending_get(bytes(it.h))
                if raw:
                    txs.append(raw)
            elif int(it.typ) == int(InvType.BLOCK):
                rawb = self._get_block_raw(bytes(it.h))
                if rawb:
                    blocks.append(rawb)

        for raw in txs:
            await self._send(peer, MsgID.TX, Tx(raw_cbor=raw))
            self._stats["tx_sent"] += 1

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
        if len(raw) > 512 * 1024:
            raise ValueError("oversize tx")

        from core.utils.hash import sha3_256

        txh = sha3_256(raw)
        if self._seen(self._seen_tx, txh):
            return
        self._remember(self._seen_tx, txh, self._seen_tx_cap)
        self._stats["tx_recv"] += 1

        ok = await self._deps_call_ok("admit_tx", raw)
        if ok:
            await self._broadcast_inv(
                [InvItem(typ=InvType.TX, h=txh)], exclude_remote=peer.remote, is_tx=True
            )

    async def _handle_get_headers(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        req = GetHeaders(**data)
        headers = self._headers_after_locator(
            list(req.locator), limit=int(req.max_headers or 64)
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

    async def _handle_blocks(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        msg = Blocks(**data)
        if len(msg.blocks) > self._max_blocks_per_message:
            raise PeerMisbehavior(
                "blocks_oversized", points=self._score_points["malformed_message"]
            )
        if peer.last_block_request_at:
            self._update_latency(peer, peer.last_block_request_at)
        for rawb in msg.blocks:
            self._stats["blocks_recv"] += 1
            self._stats["blocks_received"] += 1
            self._sync_last_block_response_at = time.time()
            self._sync_active_block_peer = peer.remote
            log.info(
                "Block received",
                extra={"remote": peer.remote, "bytes": len(rawb)},
            )
            raw_bytes = bytes(rawb)
            try:
                sync_block = self._decode_block(raw_bytes)
            except Exception as e:
                self._penalize_peer(peer, f"bad_block_decode:{e.__class__.__name__}")
                continue
            ok, reason = await self._import_block_payload(
                sync_block.block, origin_remote=peer.remote
            )
            if ok:
                self._sync_inflight_blocks.pop(sync_block.hash, None)
                self._sync_inflight_peers.pop(sync_block.hash, None)
                self._sync_last_block_at = time.time()
                self._sync_last_progress_at = self._sync_last_block_at
                peer.last_progress_at = self._sync_last_block_at
                self._sync_block_stalled_reason = None
                self._sync_wakeup.set()
                await self._drain_block_buffer()
            else:
                self._sync_inflight_blocks.pop(sync_block.hash, None)
                self._sync_inflight_peers.pop(sync_block.hash, None)
                if self._is_orphan_reason(reason):
                    sync_block.origin_peer = peer.remote
                    self._sync_block_buffer[sync_block.hash] = sync_block
                    self._handle_missing_parent(peer, sync_block)
                    while len(self._sync_block_buffer) > self._max_orphan_blocks:
                        self._sync_block_buffer.popitem(last=False)
                else:
                    reject_reason = reason or "block_rejected"
                    if self._is_db_write_error(reject_reason):
                        self._sync_block_stalled_reason = "db not writable"
                        self._sync_last_block_error = f"db not writable: {reject_reason}"
                        log.error(
                            "Block DB write failed",
                            extra={"remote": peer.remote, "error": reject_reason},
                        )
                    log.warning(
                        "Block rejected",
                        extra={
                            "remote": peer.remote,
                            "reason": reject_reason,
                        },
                    )
                    if "pow target not met" in reject_reason.lower():
                        self._set_sync_backoff(
                            peer,
                            reason="consensus_mismatch_pow",
                            delay=120.0,
                        )
                        self._penalize_peer(
                            peer,
                            "consensus_mismatch_pow",
                            severity=1,
                            quarantine_s=120.0,
                        )
                    self._penalize_peer(
                        peer,
                        f"block_rejected:{reject_reason}",
                        severity=2,
                        quarantine_s=300.0,
                    )

    # ---------------------------------------------------------------------
    # Gossip + sync loops
    # ---------------------------------------------------------------------

    async def _head_watch_loop(self) -> None:
        last: Optional[str] = None
        try:
            while self._running:
                await asyncio.sleep(1.0)
                _h, hh = self._local_head()
                if hh and hh != last:
                    last = hh
                    with contextlib.suppress(Exception):
                        await self.relay_block(bytes.fromhex(hh[2:]))
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
            ts = int(getattr(hdr, "timestamp", 0))
            return height, ts
        except Exception:
            return None

    def _header_from_compact(self, hc: HeaderCompact) -> _SyncHeader:
        return _SyncHeader(
            hash=bytes(hc.hash),
            parent_hash=bytes(hc.parent),
            height=int(hc.height),
            theta_micro=int(hc.theta_micro),
            timestamp=int(hc.timestamp),
        )

    def _sync_header_from_db(self, hdr: Any) -> _SyncHeader:
        return _SyncHeader(
            hash=hdr.hash(),
            parent_hash=bytes(hdr.parentHash),
            height=int(hdr.height),
            theta_micro=int(getattr(hdr, "thetaMicro", 0)),
            timestamp=int(getattr(hdr, "timestamp", 0)),
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
        return self._sync_header_from_db(hdr)

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

    def _enqueue_missing_blocks(self, headers: list[_SyncHeader]) -> int:
        if not headers:
            return 0
        local_height, _ = self._local_head()
        added = 0
        for hdr in sorted(headers, key=lambda h: h.height):
            if hdr.height <= int(local_height or 0):
                continue
            if self._has_block(hdr.hash):
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
            added += 1
        if added:
            self._sync_wakeup.set()
        return added

    def _drop_from_block_queue(self, block_hash: bytes) -> None:
        if block_hash not in self._sync_block_queue_set:
            return
        self._sync_block_queue_set.discard(block_hash)
        self._sync_block_queue_heights.pop(block_hash, None)
        with contextlib.suppress(ValueError):
            self._sync_block_queue.remove(block_hash)

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

    def _is_orphan_reason(self, reason: Optional[str]) -> bool:
        if not reason:
            return False
        lowered = str(reason).lower()
        return "missing parent" in lowered or "orphan" in lowered

    def _handle_missing_parent(self, peer: _PeerState, sync_block: _SyncBlock) -> None:
        peer.missing_parent += 1
        if sync_block.parent_hash and not self._has_block(sync_block.parent_hash):
            if sync_block.parent_hash not in self._sync_block_queue_set:
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
        if self._sync_block_stalled_reason == "missing parent":
            self._sync_block_stalled_reason = None
        if peer.missing_parent >= self._missing_parent_threshold:
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
                            self._peers.get(blk.origin_peer),
                            f"block_rejected:{reject_reason}",
                            severity=2,
                            quarantine_s=300.0,
                        )

    def _expire_inflight_blocks(self) -> None:
        if not self._sync_inflight_blocks:
            return
        now = time.time()
        timeout = max(1.0, self._sync_request_timeout)
        expired = [
            h
            for h, started in list(self._sync_inflight_blocks.items())
            if now - started >= timeout
        ]
        for h in expired:
            self._sync_inflight_blocks.pop(h, None)
            peer_remote = self._sync_inflight_peers.pop(h, None)
            if not self._has_block(h):
                if h not in self._sync_block_queue_set:
                    self._sync_block_queue.appendleft(h)
                    self._sync_block_queue_set.add(h)
                    if h not in self._sync_block_queue_heights:
                        self._sync_block_queue_heights[h] = -1
            if peer_remote:
                self._penalize_peer(self._peers.get(peer_remote), "block_timeout")
        if expired:
            self._sync_wakeup.set()

    async def _fetch_headers(self, peer: _PeerState) -> Optional[List[HeaderCompact]]:
        local_height, local_hash_hex = self._local_head()
        local_hash = None
        if local_hash_hex:
            with contextlib.suppress(ValueError):
                local_hash = bytes.fromhex(
                    local_hash_hex[2:] if local_hash_hex.startswith("0x") else local_hash_hex
                )
        anchor_height = int(local_height or 0)
        anchor_hash = local_hash
        request_start_height = anchor_height + 1 if anchor_hash else anchor_height
        locator = self._build_locator()
        locator_info = self._locator_debug(locator)
        locator_start = locator_info[0] if locator_info else None
        locator_end = locator_info[-1] if locator_info else None
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        peer.pending_headers = fut
        request_id = self._register_header_request(peer)
        self._sync_last_header_request_at = time.time()
        peer.last_header_request_at = self._sync_last_header_request_at
        self._sync_active_header_peer = peer.remote
        self._sync_last_header_request_peer = peer.remote
        log.debug(
            "Sending getheaders",
            extra={
                "remote": peer.remote,
                "peer_id": peer.peer_id,
                "request_id": request_id,
                "anchor_height": anchor_height,
                "anchor_hash": anchor_hash.hex() if anchor_hash else None,
                "request_start_height": request_start_height,
                "locator": locator_info,
                "locator_start": locator_start,
                "locator_end": locator_end,
                "limit": self._sync_headers_batch,
            },
        )
        self._record_sync_header_event(
            {
                "type": "request",
                "peer": peer.remote,
                "peer_id": peer.peer_id,
                "request_id": request_id,
                "anchor_height": anchor_height,
                "anchor_hash": anchor_hash.hex() if anchor_hash else None,
                "request_start_height": request_start_height,
                "locator": locator_info,
                "locator_start": locator_start,
                "locator_end": locator_end,
                "limit": self._sync_headers_batch,
            }
        )
        await self._send(
            peer,
            MsgID.GET_HEADERS,
            GetHeaders(locator=locator, max_headers=self._sync_headers_batch),
        )

        try:
            headers_msg: Headers = await asyncio.wait_for(
                fut, timeout=self._sync_request_timeout
            )
        except Exception:
            peer.pending_headers = None
            self._clear_header_request(peer)
            self._sync_last_header_error = "headers_timeout"
            self._sync_last_header_error_at = time.time()
            self._sync_last_header_error_peer = peer.remote
            self._penalize_peer(peer, "headers_timeout")
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
        return list(headers_msg.headers)

    def _process_headers(
        self, peer: _PeerState, headers: List[HeaderCompact]
    ) -> tuple[List[bytes], Optional[str]]:
        if not headers:
            return [], None
        peer.empty_header_responses = 0
        local_height, local_hash_hex = self._local_head()
        local_hash: Optional[bytes] = None
        if local_hash_hex:
            try:
                local_hash = bytes.fromhex(
                    local_hash_hex[2:]
                    if local_hash_hex.startswith("0x")
                    else local_hash_hex
                )
            except Exception:
                local_hash = None
        anchor_height = int(local_height or 0)
        anchor_hash = local_hash
        if headers and anchor_hash is not None:
            first = self._header_from_compact(headers[0])
            if first.height == anchor_height and first.hash == anchor_hash:
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
                    return [], None

        contiguous: List[_SyncHeader] = []
        prev: Optional[_SyncHeader] = None
        seen_hashes: set[bytes] = set()
        expected_genesis = self._genesis_hash()

        for idx, hc in enumerate(headers):
            header = self._header_from_compact(hc)
            if header.hash in seen_hashes:
                continue
            seen_hashes.add(header.hash)
            if header.theta_micro < 0 or header.theta_micro > 10**12:
                self._penalize_peer(peer, "header_theta_out_of_range")
                break
            if idx == 0:
                if header.height == 0:
                    if header.hash != expected_genesis:
                        self._stats["p2p_peers_rejected_genesis_mismatch"] += 1
                        self._penalize_peer(peer, "genesis_mismatch", severity=2)
                        return [], "genesis_mismatch"
                else:
                    if anchor_hash is not None and header.height <= anchor_height:
                        if not (
                            header.height == anchor_height and header.hash == anchor_hash
                        ):
                            self._sync_last_header_error = "not_anchored"
                            self._sync_last_header_error_at = time.time()
                            self._sync_last_header_error_peer = peer.remote
                            self._penalize_peer(peer, "header_not_anchored", severity=1)
                            strikes = peer.invalid_headers
                            delay = (
                                self._sync_no_headers_backoff
                                if strikes >= 2
                                else min(2.0, self._sync_no_headers_backoff)
                            )
                            self._set_sync_backoff(
                                peer,
                                reason="not_anchored",
                                delay=delay,
                            )
                            log.info(
                                "Rejecting header batch: not anchored to local head",
                                extra={
                                    "remote": peer.remote,
                                    "anchor_height": anchor_height,
                                    "anchor_hash": anchor_hash.hex(),
                                    "first_height": header.height,
                                    "first_hash": header.hash.hex(),
                                    "strikes": strikes,
                                },
                            )
                            return [], "not_anchored"
                    if (
                        anchor_hash is not None
                        and header.height == anchor_height + 1
                        and header.parent_hash != anchor_hash
                    ):
                        self._sync_last_header_error = "not_anchored"
                        self._sync_last_header_error_at = time.time()
                        self._sync_last_header_error_peer = peer.remote
                        self._penalize_peer(peer, "header_not_anchored", severity=1)
                        strikes = peer.invalid_headers
                        delay = (
                            self._sync_no_headers_backoff
                            if strikes >= 2
                            else min(2.0, self._sync_no_headers_backoff)
                        )
                        self._set_sync_backoff(
                            peer,
                            reason="not_anchored",
                            delay=delay,
                        )
                        log.info(
                            "Rejecting header batch: anchor parent mismatch",
                            extra={
                                "remote": peer.remote,
                                "anchor_height": anchor_height,
                                "anchor_hash": anchor_hash.hex(),
                                "first_height": header.height,
                                "expected_parent": anchor_hash.hex(),
                                "got_parent": header.parent_hash.hex(),
                                "strikes": strikes,
                            },
                        )
                        return [], "not_anchored"
                    if header.height == 1 and header.parent_hash != expected_genesis:
                        self._stats["p2p_peers_rejected_genesis_mismatch"] += 1
                        self._penalize_peer(peer, "genesis_mismatch", severity=2)
                        return [], "genesis_mismatch"
                    if not self._has_header(header.parent_hash):
                        self._sync_last_header_error = "not_anchored"
                        self._sync_last_header_error_at = time.time()
                        self._sync_last_header_error_peer = peer.remote
                        log.info(
                            "Rejecting header batch: parent not in local chain",
                            extra={
                                "remote": peer.remote,
                                "parent_hash": header.parent_hash.hex(),
                            },
                        )
                        self._penalize_peer(peer, "header_not_anchored", severity=1)
                        strikes = peer.invalid_headers
                        delay = (
                            self._sync_no_headers_backoff
                            if strikes >= 2
                            else min(2.0, self._sync_no_headers_backoff)
                        )
                        self._set_sync_backoff(
                            peer,
                            reason="not_anchored",
                            delay=delay,
                        )
                        return [], "not_anchored"
                    parent_info = self._header_meta(header.parent_hash)
                    if parent_info is None:
                        self._sync_last_header_error = "not_anchored"
                        self._sync_last_header_error_at = time.time()
                        self._sync_last_header_error_peer = peer.remote
                        log.info(
                            "Rejecting header batch: parent metadata missing",
                            extra={
                                "remote": peer.remote,
                                "parent_hash": header.parent_hash.hex(),
                            },
                        )
                        self._penalize_peer(peer, "header_not_anchored", severity=1)
                        strikes = peer.invalid_headers
                        delay = (
                            self._sync_no_headers_backoff
                            if strikes >= 2
                            else min(2.0, self._sync_no_headers_backoff)
                        )
                        self._set_sync_backoff(
                            peer,
                            reason="not_anchored",
                            delay=delay,
                        )
                        return [], "not_anchored"
                    parent_height, parent_ts = parent_info
                    if header.height != parent_height + 1:
                        self._penalize_peer(peer, "header_height_mismatch")
                        break
                    if parent_ts and header.timestamp < parent_ts:
                        self._penalize_peer(peer, "header_timestamp_regress")
                        break
            else:
                if prev is None:
                    break
                if header.parent_hash != prev.hash:
                    self._penalize_peer(peer, "header_parent_mismatch")
                    break
                if header.height != prev.height + 1:
                    self._penalize_peer(peer, "header_height_gap")
                    break
                if header.timestamp < prev.timestamp:
                    self._penalize_peer(peer, "header_timestamp_regress")
                    break

            if header.hash not in self._sync_headers and not self._has_header(header.hash):
                self._sync_headers[header.hash] = header
                self._sync_header_sources[header.hash] = peer.remote
                contiguous.append(header)
            prev = header

        if not contiguous:
            return [], "invalid_headers"

        anchor_height = int((peer.hello or {}).get("head_height") or 0)
        anchor_hash = bytes((peer.hello or {}).get("head_hash") or b"")
        if anchor_height and anchor_hash:
            found_anchor = False
            for h in contiguous:
                if h.height == anchor_height:
                    found_anchor = True
                    if h.hash != anchor_hash:
                        self._penalize_peer(peer, "header_anchor_mismatch", severity=2)
                        return [], "invalid_headers"
                    break
            if (
                not found_anchor
                and contiguous[0].height <= anchor_height <= contiguous[-1].height
            ):
                self._penalize_peer(peer, "header_anchor_missing", severity=2)
                return [], "invalid_headers"

        self._sync_last_header_at = time.time()
        self._sync_last_header_response_at = self._sync_last_header_at
        self._sync_last_progress_at = self._sync_last_header_at
        peer.last_progress_at = self._sync_last_header_at
        for h in contiguous:
            self._sync_update_best_header(h)
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
        queued = self._enqueue_missing_blocks(contiguous)
        if queued:
            log.info(
                "Blocks queued from headers",
                extra={"remote": peer.remote, "count": queued},
            )
        return [h.hash for h in contiguous], None

    async def _queue_block_requests(
        self, peer: _PeerState, hashes: List[bytes]
    ) -> int:
        if not hashes:
            return 0

        requested: List[bytes] = []
        inflight_for_peer = sum(
            1 for remote in self._sync_inflight_peers.values() if remote == peer.remote
        )
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
            self._sync_inflight_blocks[h] = time.time()
            self._sync_inflight_peers[h] = peer.remote
            requested.append(h)
            inflight_for_peer += 1

        if not requested:
            return 0

        # Chunk requests to keep payloads small.
        for i in range(0, len(requested), 16):
            chunk = requested[i : i + 16]
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
        log.info(
            "Blocks requested",
            extra={"remote": peer.remote, "count": len(requested)},
        )
        return len(requested)

    async def _schedule_block_requests(
        self, peer: Optional[_PeerState] = None
    ) -> int:
        if not self._sync_block_queue:
            return 0
        if peer is None:
            peer = self._select_sync_peer()
        if peer is None or not peer.hello_done.is_set():
            return 0
        local_height, _ = self._local_head()
        expected_height = int(local_height or 0) + 1
        best_header_height = (
            self._sync_best_header.height if self._sync_best_header else expected_height
        )
        target_height = min(
            best_header_height, expected_height + max(1, self._sync_max_inflight) - 1
        )
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
            if height_hint is None:
                if h in self._sync_headers:
                    height_hint = self._sync_headers[h].height
                else:
                    meta = self._header_meta(h)
                    if meta is not None:
                        height_hint = meta[0]
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
            return 0
        groups: "OrderedDict[str, list[bytes]]" = OrderedDict()
        for h in to_request:
            preferred_remote = self._sync_header_sources.get(h)
            preferred_peer = (
                self._peers.get(preferred_remote) if preferred_remote else None
            )
            target_peer = (
                preferred_peer
                if preferred_peer and preferred_peer.hello_done.is_set()
                else peer
            )
            groups.setdefault(target_peer.remote, []).append(h)
        requested = 0
        for remote, hashes in groups.items():
            target_peer = self._peers.get(remote) or peer
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
            eligible_peers, _ = self._eligible_sync_peers()
            if not eligible_peers:
                self._sync_phase = "IDLE"
                return result

            self._stats["sync_rounds"] += 1
            self._sync_phase = "HEADERS"

            if force and self._sync_inflight_blocks:
                self._sync_inflight_blocks.clear()
                self._sync_inflight_peers.clear()

            tried_peers: set[str] = set()
            no_headers_responses = 0
            eligible_count = len(eligible_peers)
            requested = 0

            while True:
                peer = self._select_sync_peer(
                    avoid_remotes=tried_peers
                )
                if peer is None or not peer.hello_done.is_set():
                    self._sync_phase = "IDLE"
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
                    if (
                        self._sync_best_header is None
                        or self._sync_best_header.height <= local_height
                    ):
                        self._sync_phase = "SYNCED" if local_height > 0 else "IDLE"
                        return result

                saw_headers = False
                while True:
                    headers: Optional[List[HeaderCompact]] = None
                    if self._sync_header_queue:
                        queued_peer, headers = self._sync_header_queue.popleft()
                        if queued_peer != peer.remote:
                            peer = self._peers.get(queued_peer, peer)
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
                            network_best_height = self._network_best_height()
                            empty_reason = self._empty_headers_reason(
                                peer,
                                local_height,
                                remote_height,
                                network_best_height=network_best_height,
                                eligible_peer_count=eligible_count,
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
                                peer.empty_header_responses += 1
                                if peer.empty_header_responses >= self._sync_no_headers_threshold:
                                    self._penalize_peer(peer, "headers_empty")
                                    self._set_sync_backoff(
                                        peer,
                                        reason="headers_empty",
                                        delay=self._sync_no_headers_backoff,
                                    )
                                tried_peers.add(peer.remote)
                            elif empty_reason == "at_tip":
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
                    order, header_error = self._process_headers(peer, headers)
                    if header_error:
                        if result.get("error") is None:
                            result["error"] = header_error.replace("_", "-")
                        self._sync_last_header_error = header_error
                        self._sync_last_header_error_at = time.time()
                        self._sync_last_header_error_peer = peer.remote
                    if not order and len(headers) > 0:
                        all_known = all(
                            self._has_header(bytes(h.hash))
                            or bytes(h.hash) in self._sync_headers
                            for h in headers
                        )
                        if not all_known:
                            if result.get("error") is None:
                                result["error"] = "invalid-headers"
                            if self._sync_last_header_error is None:
                                self._sync_last_header_error = "invalid_headers"
                                self._sync_last_header_error_at = time.time()
                                self._sync_last_header_error_peer = peer.remote
                            break
                        break

                    if len(headers) >= self._sync_headers_batch:
                        log.info(
                            "Scheduling next header request",
                            extra={
                                "remote": peer.remote,
                                "last_batch": len(headers),
                                "batch_size": self._sync_headers_batch,
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
            if best_header_height > local_height:
                self._sync_phase = "BLOCKS"
                self._expire_inflight_blocks()
                added = self._ensure_block_queue()
                if added:
                    log.info(
                        "Blocks queued",
                        extra={"count": added, "best_header": best_header_height},
                    )
                requested = await self._schedule_block_requests(peer)

            new_height, _ = self._local_head()
            if new_height >= remote_height and best_header_height <= new_height:
                self._sync_phase = "SYNCED" if new_height > 0 else "IDLE"

            result["started"] = True
            result["blocksRequested"] = requested
            return result

    async def _sync_loop(self) -> None:
        try:
            while self._running:
                try:
                    await asyncio.wait_for(self._sync_wakeup.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                self._sync_wakeup.clear()
                now = time.time()
                self._expire_inflight_headers()
                self._maybe_mark_block_stalled(now)
                stalled = self._sync_block_stalled_reason is not None
                if stalled:
                    self._handle_sync_stall(
                        reason=self._sync_block_stalled_reason or "stalled"
                    )
                elif (
                    self._rotation_interval > 0
                    and now - self._last_rotation_at >= self._rotation_interval
                ):
                    self._rotate_sync_peer()
                    self._last_rotation_at = now
                await self._sync_once(force=stalled)
                if self._sync_block_stalled_reason is None:
                    await self._schedule_block_requests()
        except asyncio.CancelledError:
            return

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
            "genesis_identity": genesis_identity.hex() if genesis_identity else None,
            "network_params_hash": params_hash.hex() if params_hash else None,
            "head_height": hello.get("head_height"),
            "head_hash": bytes(hello.get("head_hash") or b"").hex()
            if hello.get("head_hash")
            else None,
            "capabilities": list(hello.get("capabilities") or []),
        }

    def _sync_peer_eligibility(
        self,
        peer: _PeerState,
        *,
        now: Optional[float] = None,
        ignore_backoff_reason: Optional[str] = None,
    ) -> tuple[bool, str]:
        now = time.time() if now is None else now
        if peer.hello is None or not isinstance(peer.hello, dict):
            return False, "hello_missing"
        if not peer.peer_id:
            return False, "peer_id_missing"
        if not peer.hello_done.is_set():
            return False, "handshake_pending"
        if not peer.ready_for_sync:
            return False, "not_ready"
        if self._is_self_address(
            self._extract_host(peer.remote), self._extract_port(peer.remote) or 0
        ):
            return False, "self"
        if peer.peer_id and self._is_banned(peer.peer_id, now=now):
            return False, "banned_peer_id"
        if self._is_banned(peer.remote, now=now):
            return False, "banned"
        if (
            peer.remote not in self._sync_peer_penalty_whitelist
            and self._sync_peer_penalties.get(peer.remote, 0)
            >= self._sync_peer_penalty_threshold
        ):
            return False, "penalized"
        backoff_until = self._sync_peer_backoff.get(peer.remote, 0.0)
        if backoff_until and backoff_until > now:
            reason = self._sync_peer_backoff_reason.get(peer.remote, "backoff")
            if ignore_backoff_reason != reason:
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
        genesis_hash = bytes(peer.hello.get("genesis_hash") or b"")
        if not genesis_hash:
            return False, "genesis_missing"
        if genesis_hash != self._genesis_hash():
            return False, "genesis_mismatch"
        fork_id = int(peer.hello.get("fork_id") or 0)
        if not fork_id:
            return False, "fork_id_missing"
        if fork_id != int(self._fork_id()):
            return False, "fork_id_mismatch"
        consensus_id = str(peer.hello.get("consensus_id") or "")
        if not consensus_id:
            return False, "consensus_missing"
        if consensus_id != str(self._consensus_id()):
            return False, "consensus_mismatch"
        protocol_version = str(peer.hello.get("protocol_version") or "")
        if not protocol_version:
            return False, "protocol_missing"
        if protocol_version != str(self._protocol_version()):
            return False, "protocol_mismatch"
        genesis_identity = bytes(peer.hello.get("genesis_identity") or b"")
        if not genesis_identity:
            return False, "genesis_identity_missing"
        if genesis_identity != self._genesis_identity():
            return False, "genesis_identity_mismatch"
        params_hash = bytes(peer.hello.get("network_params_hash") or b"")
        if not params_hash:
            return False, "network_params_missing"
        if params_hash != self._network_params_hash():
            return False, "network_params_mismatch"
        caps = peer.hello.get("capabilities")
        head_height = int(peer.hello.get("head_height") or 0)
        if isinstance(caps, list) and caps:
            if "sync" not in caps and "blocks" not in caps and "headers" not in caps:
                if head_height <= 0:
                    return False, "no_sync_capability"
        elif head_height <= 0:
            return False, "no_chain_data"
        return True, "eligible"

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
        return eligible, ineligible

    def _set_sync_backoff(
        self, peer: _PeerState, *, reason: str, delay: float
    ) -> None:
        until = time.time() + max(0.0, delay)
        self._sync_peer_backoff[peer.remote] = until
        self._sync_peer_backoff_reason[peer.remote] = reason

    def _select_sync_peer(
        self,
        *,
        avoid_peer: Optional[_PeerState] = None,
        avoid_remotes: Optional[set[str]] = None,
        allow_pow_backoff: bool = False,
    ) -> Optional[_PeerState]:
        best: Optional[_PeerState] = None
        best_score = None
        avoid_netgroup = avoid_peer.netgroup if avoid_peer else None
        eligible, _ = self._eligible_sync_peers(
            ignore_backoff_reason="consensus_mismatch_pow" if allow_pow_backoff else None
        )
        avoid_remotes = avoid_remotes or set()
        for p in eligible:
            if p.remote in avoid_remotes:
                continue
            try:
                h = int(p.hello.get("head_height") or 0)
            except Exception:
                h = 0
            latency = p.latency_ewma if p.latency_ewma is not None else 9999.0
            outbound_bonus = 1 if p.direction == "outbound" else 0
            netgroup_penalty = 1 if avoid_netgroup and p.netgroup == avoid_netgroup else 0
            score = (h, outbound_bonus, -p.misbehavior_score, -latency, -netgroup_penalty)
            if best_score is None or score > best_score:
                best = p
                best_score = score
        return best

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
                head_bytes = bytes.fromhex(head_hash[2:])
                try:
                    if self._has_header(head_bytes):
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
                        return height, head_hash
                recovered = self._recover_head_from_canonical(height)
                if recovered is not None:
                    recovered_height, recovered_hash = recovered
                    return recovered_height, "0x" + recovered_hash.hex()
                return height, "0x" + bytes(head[1]).hex()
        except RuntimeError:
            if head_hash:
                return int(height or 0), head_hash
        except Exception:
            pass
        genesis = self._block_db().get_genesis_hash()
        if genesis and self._has_header(bytes(genesis)):
            return 0, "0x" + bytes(genesis).hex()
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

    def _network_best_height(self) -> Optional[int]:
        heights: list[int] = []
        for peer in self._peers.values():
            if not peer.hello_done.is_set():
                continue
            try:
                heights.append(int((peer.hello or {}).get("head_height") or 0))
            except Exception:
                continue
        if not heights:
            return None
        return max(heights)

    def _register_header_request(self, peer: _PeerState) -> str:
        request_id = uuid.uuid4().hex
        peer.pending_header_request_id = request_id
        self._sync_inflight_header_requests[(peer.remote, request_id)] = time.time()
        self._sync_inflight_headers = len(self._sync_inflight_header_requests)
        return request_id

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
        now = time.time()
        timeout = max(1.0, self._sync_request_timeout)
        expired: list[tuple[str, str]] = [
            key
            for key, started in list(self._sync_inflight_header_requests.items())
            if now - started >= timeout
        ]
        for remote, request_id in expired:
            self._sync_inflight_header_requests.pop((remote, request_id), None)
            peer = self._peers.get(remote)
            if peer and peer.pending_header_request_id == request_id:
                peer.pending_header_request_id = None
                fut = peer.pending_headers
                peer.pending_headers = None
                if fut is not None and not fut.done():
                    fut.set_result(None)
                self._penalize_peer(peer, "headers_timeout")
        if expired:
            self._sync_inflight_headers = len(self._sync_inflight_header_requests)
            log.info(
                "Expired in-flight header requests",
                extra={"count": len(expired)},
            )

    def _ensure_sync_cursor_integrity(self) -> None:
        head_height, head_hash = self._local_head()
        head_bytes: Optional[bytes] = None
        if head_hash:
            try:
                head_bytes = bytes.fromhex(head_hash[2:] if head_hash.startswith("0x") else head_hash)
            except Exception:
                head_bytes = None
        head_missing = head_bytes is not None and not self._has_header(head_bytes)
        best_missing = (
            self._sync_best_header is not None
            and not self._has_header(self._sync_best_header.hash)
        )
        if not head_missing and not best_missing:
            return
        recovered = None
        if head_bytes is None and head_height >= 0:
            recovered = self._recover_head_from_canonical(head_height)
        elif head_bytes is not None and head_missing:
            recovered = self._recover_head_from_canonical(head_height)
        if recovered is not None:
            head_height, head_bytes = recovered
        log.warning(
            "sync: reset cursor due to missing head_hash in db",
            extra={
                "head_height": head_height,
                "head_hash": head_hash,
                "best_header_height": self._sync_best_header.height if self._sync_best_header else None,
                "best_header_hash": self._sync_best_header.hash.hex() if self._sync_best_header else None,
            },
        )
        self._sync_inflight_blocks.clear()
        self._sync_inflight_peers.clear()
        self._sync_inflight_header_requests.clear()
        self._sync_inflight_headers = 0
        self._sync_active_header_peer = None
        self._sync_active_block_peer = None
        self._sync_header_queue.clear()
        self._sync_headers.clear()
        self._sync_header_sources.clear()
        self._sync_block_queue.clear()
        self._sync_block_queue_set.clear()
        self._sync_block_queue_heights.clear()
        for peer in self._peers.values():
            peer.pending_headers = None
            peer.pending_header_request_id = None
        self._sync_best_header = None
        if head_bytes is not None:
            hdr = self._sync_header_by_hash(head_bytes)
            if hdr is not None:
                self._sync_best_header = hdr

    def _header_hash_for_status(self, header: Any) -> Optional[str]:
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

        bdb = self._block_db()
        head = bdb.get_head()
        if not head:
            return []
        head_height = int(head[0])
        locset = {bytes(h) for h in locator if isinstance(h, (bytes, bytearray))}

        start = 0
        for h in range(head_height, -1, -1):
            hh = bdb.get_canonical_hash(h)
            if hh and bytes(hh) in locset:
                start = h
                break

        out: list[Any] = []
        lim = max(1, min(int(limit), 512))
        for n in range(start + 1, min(head_height + 1, start + 1 + lim)):
            hdr = bdb.get_header_by_height(n)
            if hdr is None:
                break
            out.append(
                HeaderCompact(
                    hash=hdr.hash(),
                    height=int(hdr.height),
                    parent=bytes(hdr.parentHash),
                    theta_micro=int(getattr(hdr, "thetaMicro", 0)),
                    timestamp=int(getattr(hdr, "timestamp", 0)),
                )
            )
        return out

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
        if (
            remote_height <= local_height
            and (network_best_height is None or network_best_height <= local_height)
        ):
            return "at_tip"
        if remote_height <= local_height:
            return "peer_behind"
        return "headers_empty"

    def _record_sync_header_event(self, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload["at"] = time.time()
        self._sync_header_events.append(payload)

    def _build_locator(self, max_entries: int = 32) -> list[bytes]:
        bdb = self._block_db()
        head = bdb.get_head()
        if not head:
            genesis = bdb.get_canonical_hash(0) or bdb.get_genesis_hash()
            if genesis:
                return [bytes(genesis)]
            return [self._genesis_hash()]

        head_height = int(head[0])
        head_hash = bytes(head[1])
        start_hash = head_hash
        start_height = head_height

        if self._sync_best_header and self._sync_best_header.height > head_height:
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
        return out

    def _pending_get(self, tx_hash: bytes) -> bytes | None:
        # Prefer deps hook (used in tests and alternative mempool implementations).
        if self.deps is not None:
            fn = getattr(self.deps, "get_tx_raw", None)
            if callable(fn):
                with contextlib.suppress(Exception):
                    raw = fn(tx_hash)
                    if isinstance(raw, (bytes, bytearray)):
                        return bytes(raw)
        try:
            from rpc.methods import tx as tx_methods

            return tx_methods._pending_get("0x" + tx_hash.hex())
        except Exception:
            return None

    def _has_block(self, block_hash: bytes) -> bool:
        try:
            return self._block_db().get_block_by_hash(block_hash) is not None
        except Exception:
            return False

    def _has_header(self, block_hash: bytes) -> bool:
        try:
            return self._block_db().get_header_by_hash(block_hash) is not None
        except Exception:
            return False

    def _get_block_raw(self, block_hash: bytes) -> bytes | None:
        try:
            blk = self._block_db().get_block_by_hash(block_hash)
            if blk is None:
                return None
            if isinstance(blk, (bytes, bytearray)):
                return bytes(blk)
            return blk.to_cbor() if hasattr(blk, "to_cbor") else None
        except Exception:
            return None

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
    ) -> None:
        if peer is None:
            return
        self._apply_misbehavior(peer, reason, points=points, ban_ttl=ban_ttl)
        if peer.remote in self._sync_peer_penalty_whitelist:
            self._sync_peer_penalties.pop(peer.remote, None)
            return
        count = self._sync_peer_penalties.get(peer.remote, 0) + max(1, severity)
        self._sync_peer_penalties[peer.remote] = count
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
            self._remember(self._seen_blocks, bh, self._seen_block_cap)
            await self._broadcast_inv(
                [InvItem(typ=InvType.BLOCK, h=bh)],
                exclude_remote=origin_remote,
                is_tx=False,
            )
            self._sync_last_block_error = None
            self._sync_last_block_error_at = None
            self._sync_fatal_error = None
            self._sync_block_stalled_reason = None
            self._stats["blocks_validated_ok"] += 1
            self._stats["blocks_imported"] += 1
            self._drop_from_block_queue(bh)
            self._sync_last_block_at = time.time()
            self._sync_last_progress_at = self._sync_last_block_at
            log.info(
                "Block persisted",
                extra={"hash": bh.hex(), "origin": origin_remote},
            )
            after_height, _ = self._local_head()
            if after_height > before_height:
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
            if "pow target not met" in reason_str.lower():
                header_hash_hex = None
                theta_micro = None
                target_hex = None
                peer_id = None
                if origin_remote:
                    origin_peer = self._peers.get(origin_remote)
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
                        "reason": reason_str,
                    },
                )
            if self._is_db_write_error(reason_str):
                self._sync_block_stalled_reason = "db not writable"
                self._sync_last_block_error = f"db not writable: {reason_str}"
                log.error(
                    "Block DB write failed",
                    extra={"origin": origin_remote, "error": reason_str},
                )
        return ok, reason

    # ---------------------------------------------------------------------
    # Broadcast helpers
    # ---------------------------------------------------------------------

    async def _broadcast_inv(
        self,
        items: list[InvItem],
        *,
        exclude_remote: Optional[str],
        is_tx: bool,
    ) -> None:
        if not items:
            return
        inv = Inv(items=items)

        async with self._peer_lock:
            peers = list(self._peers.values())

        for p in peers:
            if exclude_remote and p.remote == exclude_remote:
                continue
            with contextlib.suppress(Exception):
                await self._send(p, MsgID.INV, inv)
                if is_tx:
                    self._stats["inv_tx_sent"] += len(items)
                else:
                    self._stats["inv_block_sent"] += len(items)

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
