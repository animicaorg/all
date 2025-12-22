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
from p2p.transport.base import ListenConfig
from p2p.constants import DEFAULT_TCP_PORT
from p2p.transport.multiaddr import normalize_multiaddr, parse_multiaddr
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
    "/dns4/rpc.animica.org/tcp/30333",
    "/ip4/144.126.133.21/tcp/30333",
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
    connected_at: float = field(default_factory=time.time)
    feeler: bool = False
    known_addrs: Set[str] = field(default_factory=set)


@dataclass(slots=True)
class _AddrRecord:
    address: str
    last_seen: float
    last_success: Optional[float] = None
    failures: int = 0
    score: float = 0.0

    def touch_seen(self, now: float) -> None:
        self.last_seen = now

    def mark_success(self, now: float) -> None:
        self.last_success = now
        self.failures = 0
        self.score = min(self.score + 1.0, 100.0)

    def mark_failure(self) -> None:
        self.failures += 1
        self.score = max(self.score - 0.5, -10.0)


class _AddrMan:
    def __init__(self) -> None:
        self._records: dict[str, _AddrRecord] = {}

    def add(self, address: str, *, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        rec = self._records.get(address)
        if rec:
            rec.touch_seen(now)
            return
        self._records[address] = _AddrRecord(address=address, last_seen=now)

    def mark_success(self, address: str) -> None:
        rec = self._records.get(address)
        now = time.time()
        if rec is None:
            rec = _AddrRecord(address=address, last_seen=now)
            self._records[address] = rec
        rec.mark_success(now)

    def mark_failure(self, address: str) -> None:
        rec = self._records.get(address)
        if rec is None:
            rec = _AddrRecord(address=address, last_seen=time.time())
            self._records[address] = rec
        rec.mark_failure()

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
    best_header_height: int
    best_header_hash: Optional[str]
    best_block_height: int
    best_block_hash: Optional[str]
    in_flight: int
    last_progress_at: float
    last_header_at: float
    last_block_at: float
    pending_header_batches: int
    peer_penalties: Dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "best_header_height": self.best_header_height,
            "best_header_hash": self.best_header_hash,
            "best_block_height": self.best_block_height,
            "best_block_hash": self.best_block_hash,
            "in_flight": self.in_flight,
            "last_progress_at": self.last_progress_at,
            "last_header_at": self.last_header_at,
            "last_block_at": self.last_block_at,
            "pending_header_batches": self.pending_header_batches,
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
        self.seeds = merged_seeds
        self.chain_id = int(chain_id)
        self.deps = deps
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
        peerstore_dir = peerstore_path if not peerstore_path.suffix else peerstore_path.parent
        self._peerstore_dir = peerstore_dir
        self._peers_json_path = peerstore_dir / "peers.json"

        # Identity + stable peer id (co-locate with peerstore by default)
        identity_path = os.environ.get("ANIMICA_P2P_IDENTITY_PATH")
        if not identity_path:
            identity_path = peerstore_dir / "identity.json"
        identity_path = Path(identity_path).expanduser()
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

        # Persistent peerstore
        self._ensure_peerstore_dir(peerstore_dir)
        self.peerstore = pstore.PeerStore(peerstore_path)

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

        self._peer_lock = asyncio.Lock()
        self._peers: dict[str, _PeerState] = {}  # remote -> state
        self._peers_by_session: dict[str, _PeerState] = {}
        self._peer_registry = PeerRegistry(
            max_inbound_per_ip=int(os.environ.get("ANIMICA_P2P_MAX_INBOUND_PER_IP", "10") or 10),
            handshake_timeout_s=float(os.environ.get("ANIMICA_P2P_HANDSHAKE_TIMEOUT", "6.0") or 6.0),
        )

        # Seen LRU (dedupe + rebroadcast suppression)
        self._seen_tx: "OrderedDict[bytes, float]" = OrderedDict()
        self._seen_blocks: "OrderedDict[bytes, float]" = OrderedDict()
        self._seen_tx_cap = 50_000
        self._seen_block_cap = 10_000

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
            "sync_rounds": 0,
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

        self._sync_lock = asyncio.Lock()
        self._sync_wakeup = asyncio.Event()
        self._sync_phase = "idle"
        self._sync_best_header: Optional[_SyncHeader] = None
        self._sync_headers: Dict[bytes, _SyncHeader] = {}
        self._sync_header_queue: Deque[Tuple[str, List[HeaderCompact]]] = deque()
        self._sync_inflight_blocks: Dict[bytes, float] = {}
        self._sync_inflight_peers: Dict[bytes, str] = {}
        self._sync_block_buffer: Dict[bytes, _SyncBlock] = {}
        self._sync_peer_penalties: Dict[str, int] = {}
        self._sync_last_progress_at = time.time()
        self._sync_last_header_at = 0.0
        self._sync_last_block_at = 0.0
        self._sync_max_inflight = int(
            os.environ.get("ANIMICA_P2P_SYNC_INFLIGHT", "32") or 32
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
        self._bootstrap_attempts: deque[dict[str, Any]] = deque(maxlen=512)
        self._last_bootstrap_attempt: Optional[dict[str, Any]] = None
        self._last_bootstrap_success: Optional[dict[str, Any]] = None
        self._last_bootstrap_error: Optional[dict[str, Any]] = None
        self._last_peer_connect_at: Optional[float] = None
        self._last_peer_disconnect_at: Optional[float] = None

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

        task.add_done_callback(_discard)
        return task

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self._maybe_detect_external_ip()

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
            path.chmod(0o755)
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
                addrs.append(self._normalize_seed(entry))
        if addrs:
            return list(dict.fromkeys(addrs))
        if self._external_ip:
            port = self._local_listen_port()
            addrs.append(self._normalize_seed(f"{self._external_ip}:{port}"))
            return list(dict.fromkeys(addrs))
        for addr in self.listen_addrs:
            try:
                parsed = parse_multiaddr(addr)
                host = parsed.host or ""
                if host in {"0.0.0.0", "::"}:
                    continue
                if parsed.transport == "tcp" and parsed.port:
                    addrs.append(self._normalize_seed(f"{host}:{parsed.port}"))
            except Exception:
                continue
        return list(dict.fromkeys(addrs))

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

    def _sanitize_peer_addr(self, address: str, *, fallback_port: int) -> Optional[str]:
        if not address:
            return None
        normalized = self._normalize_seed(address)
        host = None
        port: Optional[int] = None
        if normalized.startswith("/"):
            try:
                parsed = parse_multiaddr(normalized)
            except Exception:
                return None
            if parsed.transport != "tcp":
                return None
            host = parsed.host or ""
            if parsed.port:
                try:
                    port = int(parsed.port)
                except (TypeError, ValueError):
                    port = None
        else:
            raw = normalized
            if "://" in raw:
                parsed = urlparse(raw)
                host = parsed.hostname
                port = parsed.port
            else:
                h, _, p = raw.rpartition(":")
                host = h or raw
                try:
                    port = int(p) if p else None
                except (TypeError, ValueError):
                    port = None
        if not host:
            return None
        if not self._is_routable_host(host):
            return None
        if not port or port <= 0 or port > 65535:
            port = fallback_port
        if self._is_ephemeral_port(port) and fallback_port and port != fallback_port:
            port = fallback_port
        return self._normalize_seed(f"{host}:{port}")

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
                    self._addrman.add(normalized)
        except Exception:
            return

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
        return self._addr_key(addr) in peer.known_addrs

    def _mark_peer_known(self, peer: _PeerState, addr: str) -> None:
        if not addr:
            return
        peer.known_addrs.add(self._addr_key(addr))
        while len(peer.known_addrs) > self._addr_peer_known_cap:
            peer.known_addrs.pop()

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

    def _collect_peer_addrs(
        self, *, limit: int, exclude: Optional[set[str]] = None
    ) -> list[str]:
        exclude = exclude or set()
        addrs: list[str] = []
        for addr in self._addrman.sample(limit=limit, exclude=set()):
            if self._addr_key(addr) in exclude:
                continue
            addrs.append(addr)
        try:
            for peer_id, address, _ in self.peerstore.list_addresses(limit=limit):
                if not address:
                    continue
                normalized = self._normalize_seed(address)
                if self._addr_key(normalized) in exclude:
                    continue
                addrs.append(normalized)
        except Exception:
            pass
        return list(dict.fromkeys(addrs))[:limit]

    def _ingest_peer_addrs(self, addrs: list[str], *, source: str) -> int:
        if not addrs:
            return 0
        stored = 0
        fallback_port = self._local_listen_port()
        for addr in addrs:
            normalized = self._sanitize_peer_addr(addr, fallback_port=fallback_port)
            if not normalized:
                continue
            self._remember_addr(normalized)
            self._addrman.add(normalized)
            try:
                peer_id = self._peer_id_from_addr(normalized)
                self.peerstore.add(peer_id=peer_id, addrs=[normalized], direction="outbound")
                self.peerstore.record_seen(peer_id, normalized)
                stored += 1
            except Exception:
                continue
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
            discovered.append(self._normalize_seed(f"{host}:{port}"))
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
                entry = peers.setdefault(
                    peer_id,
                    {
                        "peer_id": peer_id,
                        "addrs": [],
                        "score": 0.0,
                        "last_seen": last_seen,
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
        self, addr: str, *, success: bool, error: Optional[str] = None
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
        else:
            self._last_bootstrap_error = entry

    def _dial_delay(self, addr_key: str) -> float:
        attempts = self._dial_attempts.get(addr_key, 0)
        base = 2.0 * (2 ** min(attempts, 5))
        jitter = random.uniform(0.6, 1.4)
        return min(60.0, base * jitter)

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
            self._addrman.mark_failure(normalized)
        if is_seed:
            self._record_bootstrap_attempt(addr, success=False, error=error)
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
        height, head_hash = self._local_head()
        head_hex = head_hash
        best_header_hash = (
            "0x" + self._sync_best_header.hash.hex()
            if self._sync_best_header is not None
            else None
        )
        return SyncStatusSnapshot(
            phase=self._sync_phase,
            best_header_height=(
                self._sync_best_header.height if self._sync_best_header else 0
            ),
            best_header_hash=best_header_hash,
            best_block_height=int(height or 0),
            best_block_hash=head_hex,
            in_flight=len(self._sync_inflight_blocks),
            last_progress_at=self._sync_last_progress_at,
            last_header_at=self._sync_last_header_at,
            last_block_at=self._sync_last_block_at,
            pending_header_batches=len(self._sync_header_queue),
            peer_penalties=dict(self._sync_peer_penalties),
        )

    def _normalize_seed(self, address: str) -> str:
        if address.startswith("/"):
            try:
                return normalize_multiaddr(address)
            except Exception:
                return address

        # strip scheme if present
        if "://" in address:
            address = address.split("://", 1)[1]

        host, _, port = address.rpartition(":")
        if not host:
            host = address
        if not port:
            return address

        try:
            ip_obj = ipaddress.ip_address(host)
            ip_tag = "ip6" if ip_obj.version == 6 else "ip4"
        except Exception:
            ip_tag = "dns4"

        try:
            return normalize_multiaddr(f"/{ip_tag}/{host}/tcp/{port}")
        except Exception:
            return f"/{ip_tag}/{host}/tcp/{port}"

    def _addr_key(self, address: str) -> str:
        """
        Normalize an address so we can deduplicate against active connections.

        Peers are stored using the transport's remote_addr (e.g. "1.2.3.4:30333"),
        while dial targets might include schemes or multiaddr prefixes. Converting
        everything to a simple "host:port" string lets us skip redialing peers we
        are already connected to and proceed to additional candidates.
        """

        if address.startswith("/"):
            try:
                parsed = parse_multiaddr(address)
                if parsed.host and parsed.port:
                    return f"{parsed.host}:{parsed.port}"
            except Exception:
                pass

        # Strip common schemes like tcp://, quic://, ws://, wss://
        if "://" in address:
            address = address.split("://", 1)[1]

        # At this point the address should resemble host:port; fall back to the raw
        # string if we cannot parse cleanly.
        parts = address.rsplit(":", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[0]}:{parts[1]}"
        return address

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
                self._addrman.add(addr)
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
            return {"added": 0, "dialing": 0}

        fallback_port = self._local_listen_port()
        normalized = [
            addr
            for addr in (self._sanitize_peer_addr(a, fallback_port=fallback_port) for a in addresses)
            if addr
        ]
        added = self._seed_peerstore(normalized)

        dial_targets: list[str] = []
        for addr in normalized:
            if addr.startswith("/"):
                with contextlib.suppress(Exception):
                    parsed = parse_multiaddr(addr)
                    if parsed.transport == "tcp":
                        dial_targets.append(f"tcp://{parsed.host}:{parsed.port}")
                        continue
            dial_targets.append(addr)

        for addr in list(dict.fromkeys(dial_targets)):
            self._create_child_task(self._dial(addr), name=f"p2p.import_dial@{addr}")

        self._sync_wakeup.set()
        return {"added": added, "dialing": len(dial_targets)}

    async def force_sync(self) -> dict[str, Any]:
        self._sync_wakeup.set()
        return await self._sync_once(force=True)

    async def dial(self, addr: str) -> None:
        if addr.startswith("/"):
            parsed = parse_multiaddr(addr)
            if parsed.transport == "tcp":
                addr = f"tcp://{parsed.host}:{parsed.port}"
        await self._dial(addr)

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
                for c in candidates:
                    if c.startswith("/"):
                        with contextlib.suppress(Exception):
                            parsed = parse_multiaddr(c)
                            if parsed.transport == "tcp":
                                addrs.append(f"tcp://{parsed.host}:{parsed.port}")
                    else:
                        addrs.append(c)

                addrs = list(dict.fromkeys(addrs))
                now = time.time()
                for addr in addrs:
                    # Skip peers we're already connected to so we can reach new ones.
                    addr_key = self._addr_key(addr)
                    if addr_key in active_keys:
                        continue
                    if addr_key in self._dial_inflight:
                        continue
                    if self._dial_backoff.get(addr_key, 0.0) > now:
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
        raw = addr
        host: Optional[str] = None
        port: Optional[int] = None
        if raw.startswith("/"):
            try:
                parsed = parse_multiaddr(raw)
                host = parsed.host
                port = int(parsed.port) if parsed.port else None
            except Exception:
                return True
        else:
            if "://" in raw:
                parsed = urlparse(raw)
                host = parsed.hostname
                port = parsed.port
            else:
                host, _, port_str = raw.rpartition(":")
                host = host or raw
                try:
                    port = int(port_str) if port_str else None
                except ValueError:
                    port = None
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
        except Exception as exc:
            log.warning("Failed to resolve seed host %s: %s", host, exc)
            return False

    async def _dial(
        self, addr: str, *, is_seed: bool = False, feeler: bool = False
    ) -> None:
        addr_key = self._addr_key(addr)
        self._dial_attempt_total += 1
        if is_seed:
            resolved = await self._resolve_seed_host(addr)
            if not resolved:
                self._mark_dial_failure(addr, is_seed=True, error="dns_lookup_failed")
                self._dial_inflight.discard(addr_key)
                return
        try:
            conn = await self._transport.dial(addr, timeout=5.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            err = f"{exc.__class__.__name__}: {exc}"
            self._mark_dial_failure(addr, is_seed=is_seed, error=err)
            return
        finally:
            self._dial_inflight.discard(addr_key)
        self._mark_dial_success(addr, is_seed=is_seed)
        await self._register_conn(conn, direction="outbound", feeler=feeler)

    async def _register_conn(
        self, conn: Any, *, direction: str, feeler: bool = False
    ) -> None:
        remote = getattr(conn.info, "remote_addr", None) or "unknown"
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
                frame = unpack_frame(data, aead=None)
                await self._handle(peer, frame.msg_id, frame.payload)
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
        obj = decode_payload(payload)
        if not isinstance(obj, dict):
            raise ValueError("payload must be a map")
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

    async def _handle_hello(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        allowed = set(Hello.__dataclass_fields__)
        hello = Hello(**{k: v for k, v in data.items() if k in allowed})

        if int(hello.chain_id) != int(self.chain_id):
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="chain_id_mismatch"),
            )
            raise ValueError("chain mismatch")

        if hello.genesis_hash and bytes(hello.genesis_hash) != self._genesis_hash():
            await self._send(
                peer,
                MsgID.HELLO_ACK,
                HelloAck(accepted=False, reason="genesis_mismatch"),
            )
            raise ValueError("genesis mismatch")

        peer.peer_id = bytes(hello.peer_id).hex()
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
        peer.hello = normalized
        peer.hello_done.set()

        listen_port = int(getattr(hello, "listen_port", 0) or 0)
        reported_addr = self._reported_peer_addr(peer.remote, listen_port)
        reported_addrs: list[str] = []
        if reported_addr:
            reported_addrs.append(reported_addr)
        fallback_port = self._local_listen_port()
        for addr in list(getattr(hello, "listen_addrs", []) or []):
            sanitized = self._sanitize_peer_addr(addr, fallback_port=fallback_port)
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
        addresses = [
            addr
            for addr in self._collect_peer_addrs(limit=max_peers, exclude=exclude)
            if not self._peer_knows_addr(peer, addr)
        ]
        entries: list[tuple[bytes, str]] = []
        for addr in addresses:
            try:
                pid = hashlib.sha3_256(addr.encode()).digest()
            except Exception:
                pid = b"\x00" * 32
            entries.append((pid, addr))
            self._mark_peer_known(peer, addr)
        await self._send(peer, MsgID.PEERS, Peers(entries=entries))

    async def _handle_peers(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        entries = data.get("entries") or []
        addrs: list[str] = []
        for entry in entries:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                addr = entry[1]
            elif isinstance(entry, dict):
                addr = entry.get("addr") or entry.get("address")
            else:
                continue
            if isinstance(addr, bytes):
                try:
                    addr = addr.decode()
                except Exception:
                    continue
            if isinstance(addr, str) and addr:
                addrs.append(addr)

        if addrs:
            for addr in addrs:
                self._mark_peer_known(peer, addr)
            self._ingest_peer_addrs(addrs, source=f"peer:{peer.remote}")
            self._sync_wakeup.set()

    async def _handle_address_announce(self, peer: _PeerState, payload: bytes) -> None:
        data = self._decode_map(payload)
        addresses = data.get("addresses") or []
        addrs: list[str] = []
        for addr in addresses:
            if isinstance(addr, bytes):
                try:
                    addr = addr.decode()
                except Exception:
                    continue
            if isinstance(addr, str) and addr:
                addrs.append(addr)
        if addrs:
            for addr in addrs:
                self._mark_peer_known(peer, addr)
            self._ingest_peer_addrs(addrs, source=f"announce:{peer.remote}")

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

        # If we have a pending request waiting on this response, fulfill it.
        fut = peer.pending_headers
        if fut is not None and not fut.done():
            fut.set_result(msg)
            peer.pending_headers = None
        else:
            # Treat as announcements; queue for sync loop to validate & download.
            if msg.headers:
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
        for rawb in msg.blocks:
            self._stats["blocks_recv"] += 1
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
                self._sync_wakeup.set()
                await self._drain_block_buffer()
            else:
                self._sync_inflight_blocks.pop(sync_block.hash, None)
                self._sync_inflight_peers.pop(sync_block.hash, None)
                if self._is_orphan_reason(reason):
                    sync_block.origin_peer = peer.remote
                    self._sync_block_buffer[sync_block.hash] = sync_block
                else:
                    self._penalize_peer(peer, "block_rejected")

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

    def _is_orphan_reason(self, reason: Optional[str]) -> bool:
        if not reason:
            return False
        lowered = str(reason).lower()
        return "missing parent" in lowered or "orphan" in lowered

    async def _drain_block_buffer(self) -> None:
        if not self._sync_block_buffer:
            return
        progressed = True
        while progressed:
            progressed = False
            for h, blk in list(self._sync_block_buffer.items()):
                if not self._has_header(blk.parent_hash):
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
                        self._penalize_peer(
                            self._peers.get(blk.origin_peer), "block_rejected"
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
            if peer_remote:
                self._penalize_peer(self._peers.get(peer_remote), "block_timeout")

    async def _fetch_headers(self, peer: _PeerState) -> Optional[List[HeaderCompact]]:
        locator = self._build_locator()
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        peer.pending_headers = fut
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
            self._penalize_peer(peer, "headers_timeout")
            return None

        return list(headers_msg.headers)

    def _process_headers(
        self, peer: _PeerState, headers: List[HeaderCompact]
    ) -> List[bytes]:
        if not headers:
            return []

        contiguous: List[_SyncHeader] = []
        prev: Optional[_SyncHeader] = None

        for idx, hc in enumerate(headers):
            header = self._header_from_compact(hc)
            if header.theta_micro < 0 or header.theta_micro > 10**12:
                self._penalize_peer(peer, "header_theta_out_of_range")
                break
            if idx == 0:
                parent_info = self._header_meta(header.parent_hash)
                if parent_info is None:
                    break
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

            self._sync_headers[header.hash] = header
            contiguous.append(header)
            prev = header

        if not contiguous:
            return []

        self._sync_last_header_at = time.time()
        self._sync_last_progress_at = self._sync_last_header_at
        for h in contiguous:
            self._sync_update_best_header(h)
        return [h.hash for h in contiguous]

    async def _queue_block_requests(
        self, peer: _PeerState, hashes: List[bytes]
    ) -> int:
        if not hashes:
            return 0

        requested: List[bytes] = []
        for h in hashes:
            if (
                self._has_block(h)
                or h in self._sync_inflight_blocks
                or h in self._sync_block_buffer
            ):
                continue
            if len(self._sync_inflight_blocks) >= self._sync_max_inflight:
                break
            self._sync_inflight_blocks[h] = time.time()
            self._sync_inflight_peers[h] = peer.remote
            requested.append(h)

        if not requested:
            return 0

        # Chunk requests to keep payloads small.
        for i in range(0, len(requested), 16):
            chunk = requested[i : i + 16]
            with contextlib.suppress(Exception):
                await self._send(
                    peer,
                    MsgID.GET_BLOCKS,
                    GetBlocks(by_hash=chunk, max_blocks=len(chunk)),
                )
            await asyncio.sleep(0)
        return len(requested)

    async def _sync_once(self, *, force: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "started": False,
            "peer": None,
            "remoteHeight": None,
            "localHeight": None,
        }

        async with self._sync_lock:
            peer = self._select_sync_peer()
            if peer is None or not peer.hello_done.is_set():
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

            if remote_height <= local_height and not force and not self._sync_header_queue:
                self._sync_phase = "steady"
                return result

            self._stats["sync_rounds"] += 1
            self._sync_phase = "headers"

            if force and self._sync_inflight_blocks:
                self._sync_inflight_blocks.clear()
                self._sync_inflight_peers.clear()

            headers: Optional[List[HeaderCompact]] = None
            if self._sync_header_queue:
                queued_peer, headers = self._sync_header_queue.popleft()
                if queued_peer != peer.remote:
                    peer = self._peers.get(queued_peer, peer)

            if headers is None:
                headers = await self._fetch_headers(peer)
            if not headers:
                result["error"] = "no-headers"
                return result

            order = self._process_headers(peer, headers)
            if not order:
                result["error"] = "invalid-headers"
                return result

            self._sync_phase = "blocks"
            self._expire_inflight_blocks()
            requested = await self._queue_block_requests(peer, order)

            new_height, _ = self._local_head()
            if new_height >= remote_height:
                self._sync_phase = "steady"

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
                last_block = self._sync_last_block_at or self._sync_last_progress_at
                stalled = (now - last_block) > self._sync_stall_timeout
                await self._sync_once(force=stalled)
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

    def _select_sync_peer(self) -> Optional[_PeerState]:
        best: Optional[_PeerState] = None
        best_height = -1
        for p in self._peers.values():
            if not p.peer_id or not isinstance(p.hello, dict):
                continue
            if self._sync_peer_penalties.get(p.remote, 0) >= self._sync_peer_penalty_threshold:
                continue
            try:
                h = int(p.hello.get("head_height") or 0)
            except Exception:
                h = 0
            if h > best_height:
                best = p
                best_height = h
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
        try:
            bdb = self._block_db()
            head = None
            if hasattr(bdb, "get_canonical_head"):
                head = bdb.get_canonical_head()
            if head is None:
                head = bdb.get_head()
            if head:
                return int(head[0]), "0x" + bytes(head[1]).hex()
        except Exception:
            pass
        genesis = self._block_db().get_genesis_hash()
        if genesis:
            return 0, "0x" + bytes(genesis).hex()
        return 0, None

    def _genesis_hash(self) -> bytes:
        bdb = self._block_db()
        g = bdb.get_genesis_hash()
        if g:
            return bytes(g)
        h0 = bdb.get_canonical_hash(0)
        if h0:
            return bytes(h0)
        return b"\x00" * 32

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

    def _build_locator(self, max_entries: int = 32) -> list[bytes]:
        bdb = self._block_db()
        head = bdb.get_head()
        if not head:
            genesis = bdb.get_canonical_hash(0) or bdb.get_genesis_hash()
            if genesis:
                return [bytes(genesis)]
            return [self._genesis_hash()]
        height = int(head[0])
        out: list[bytes] = []
        step = 1
        while height >= 0 and len(out) < max_entries:
            hh = bdb.get_canonical_hash(height)
            if hh:
                out.append(bytes(hh))
            if height == 0:
                break
            height = max(0, height - step)
            if len(out) > 10:
                step *= 2
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

    def _penalize_peer(self, peer: Optional[_PeerState], reason: str) -> None:
        if peer is None:
            return
        count = self._sync_peer_penalties.get(peer.remote, 0) + 1
        self._sync_peer_penalties[peer.remote] = count
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

    async def _import_block_payload(
        self, payload: Any, *, origin_remote: Optional[str]
    ) -> Tuple[bool, Optional[str]]:
        from core.utils.hash import sha3_256

        bh: bytes | None = None
        ok = False
        reason: Optional[str] = None
        blk = None

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
            self._sync_last_block_at = time.time()
            self._sync_last_progress_at = self._sync_last_block_at
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
            return False, str(e)
        if isinstance(res, tuple):
            ok = bool(res[0]) if res else False
            reason = res[1] if len(res) > 1 else None
            return ok, reason
        return bool(res), None
