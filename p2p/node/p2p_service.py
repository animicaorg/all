from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import logging
import os
import random
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Dict, Optional, Set

from p2p import version as p2p_version
from p2p.crypto import keys as keys_mod
from p2p.crypto import peer_id as peer_id_mod
from p2p.peer import peerstore as pstore
from p2p.transport.base import ListenConfig
from p2p.transport.multiaddr import parse_multiaddr
from p2p.transport.tcp import TcpTransport
from p2p.wire.encoding import decode_payload, encode_payload
from p2p.wire.frames import Framer, unpack_frame
from p2p.wire.message_ids import MsgID
from p2p.wire.messages import (Blocks, GetBlocks, GetData, GetHeaders, HeaderCompact,
                               Headers, Hello, HelloAck, Inv, InvItem, InvType, Tx)
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
            "bootstrap_last_attempt": self.bootstrap_last_attempt,
            "bootstrap_last_success": self.bootstrap_last_success,
            "bootstrap_last_error": self.bootstrap_last_error,
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

        # Identity + stable peer id (co-locate with peerstore by default)
        identity_path = os.environ.get("ANIMICA_P2P_IDENTITY_PATH")
        if not identity_path:
            identity_path = peerstore_dir / "identity.json"
        identity_path = Path(identity_path).expanduser()
        identity_path.parent.mkdir(parents=True, exist_ok=True)

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

        self._sync_lock = asyncio.Lock()
        self._sync_wakeup = asyncio.Event()
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

        # Persist configured seeds so a restarted node reuses them immediately
        if self.seeds:
            self._seed_peerstore(self.seeds)
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
                    self.peerstore.record_disconnection(p.peer_id)

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
        delay = self._dial_delay(addr_key)
        next_retry = time.time() + delay
        self._dial_backoff[addr_key] = next_retry
        if is_seed:
            self._record_bootstrap_attempt(addr, success=False, error=error)
            log.warning(
                "Seed %s failed: %s; next retry in %.1fs", addr, error, delay
            )

    def _mark_dial_success(self, addr: str, *, is_seed: bool) -> None:
        addr_key = self._addr_key(addr)
        self._dial_attempts.pop(addr_key, None)
        self._dial_backoff.pop(addr_key, None)
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
        addrman_size = None
        try:
            addrman_size = self.peerstore.count_known()
        except Exception:
            addrman_size = None

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
            bootstrap_last_attempt=self._last_bootstrap_attempt,
            bootstrap_last_success=self._last_bootstrap_success,
            bootstrap_last_error=self._last_bootstrap_error,
        )

    def _normalize_seed(self, address: str) -> str:
        if address.startswith("/"):
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

    def _peer_id_from_addr(self, address: str) -> str:
        if "/p2p/" in address:
            return address.split("/p2p/", 1)[1].split("/")[0]
        if "/ipfs/" in address:
            return address.split("/ipfs/", 1)[1].split("/")[0]
        return hashlib.sha256(address.encode()).hexdigest()[:32]

    def _seed_peerstore(self, addresses: list[str]) -> int:
        added = 0
        for raw in addresses:
            addr = self._normalize_seed(raw)
            peer_id = self._peer_id_from_addr(addr)
            try:
                self.peerstore.add(peer_id=peer_id, addrs=[addr], direction="outbound")
                self.peerstore.record_seen(peer_id, addr)
                added += 1
            except Exception:
                continue
        return added

    def peer_count(self) -> int:
        return self._peer_registry.peer_count() + self.bootstrap_peer_bonus()

    async def import_peers(self, addresses: list[str]) -> dict[str, Any]:
        if not addresses:
            return {"added": 0, "dialing": 0}

        normalized = [self._normalize_seed(a) for a in addresses]
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
                    continue

                candidates: list[str] = []
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

    async def _dial(self, addr: str, *, is_seed: bool = False) -> None:
        addr_key = self._addr_key(addr)
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
        await self._register_conn(conn, direction="outbound")

    async def _register_conn(self, conn: Any, *, direction: str) -> None:
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

        try:
            while self._running:
                data = await peer.stream.recv()
                if data == b"":
                    break
                self._peer_registry.mark_seen(peer.session_id)
                frame = unpack_frame(data, aead=None)
                await self._handle(peer, frame.msg_id, frame.payload)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("peer loop error", extra={"remote": peer.remote}, exc_info=True)
        finally:
            await self._drop_peer(peer, reason="loop_exit")

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
                self.peerstore.record_disconnection(peer.peer_id)

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
        hello = Hello(
            version="2",
            agent=f"animica-p2p/{p2p_version.__version__}",
            chain_id=self.chain_id,
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
        hello = Hello(**data)

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
        peer.hello = data
        peer.hello_done.set()

        self._peer_registry.update_meta(
            peer.session_id,
            peer_id=peer.peer_id,
            last_seen=time.time(),
            height=int(hello.head_height),
            remote=peer.remote,
            direction=peer.direction,
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
            self.peerstore.add(
                peer.peer_id, addrs=[peer.remote], score=0.0, direction=peer.direction
            )
            self.peerstore.record_connection(peer.peer_id)
            self.peerstore.update_head_height(peer.peer_id, int(hello.head_height))

        await self._send(peer, MsgID.HELLO_ACK, HelloAck(accepted=True, reason=None))

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

        # Opportunistic: treat as announcements for sync (request missing bodies).
        want: list[bytes] = []
        for hc in msg.headers:
            hh = bytes(hc.hash)
            if not self._has_block(hh):
                want.append(hh)
        await self._request_blocks(peer, want)

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
            await self._import_block_raw(bytes(rawb), origin_remote=peer.remote)

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

    async def _sync_once(self, *, force: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "started": False,
            "peer": None,
            "remoteHeight": None,
            "localHeight": None,
        }

        async with self._sync_lock:
            peer = self._best_peer()
            if peer is None or not peer.hello_done.is_set():
                return result

            local_height, _ = self._local_head()
            remote_height = int((peer.hello or {}).get("head_height") or 0)
            result.update({
                "peer": peer.remote,
                "remoteHeight": remote_height,
                "localHeight": local_height,
            })

            if remote_height <= local_height and not force:
                return result

            self._stats["sync_rounds"] += 1

            locator = self._build_locator()
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            peer.pending_headers = fut
            await self._send(
                peer,
                MsgID.GET_HEADERS,
                GetHeaders(locator=locator, max_headers=128),
            )

            try:
                headers_msg: Headers = await asyncio.wait_for(fut, timeout=6.0)
            except Exception:
                peer.pending_headers = None
                result["error"] = "timeout"
                return result

            if not headers_msg.headers:
                result["error"] = "no-headers"
                return result

            hashes = [bytes(h.hash) for h in headers_msg.headers]
            await self._request_blocks(peer, hashes)
            result["started"] = True
            return result

    async def _sync_loop(self) -> None:
        try:
            while self._running:
                try:
                    await asyncio.wait_for(self._sync_wakeup.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                self._sync_wakeup.clear()
                await self._sync_once()
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
        best: Optional[_PeerState] = None
        best_height = -1
        for p in self._peers.values():
            if not p.peer_id or not isinstance(p.hello, dict):
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
            head = self._block_db().get_head()
            if head:
                return int(head[0]), "0x" + bytes(head[1]).hex()
        except Exception:
            pass
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
            return []
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

    async def _import_block_raw(self, rawb: bytes, *, origin_remote: str) -> None:
        from core.utils.hash import sha3_256

        bh: bytes | None = None
        ok = False
        try:
            from core.types.block import Block

            blk = Block.from_cbor(rawb)
            bh = blk.header.hash()
            ok = await self._deps_call_ok("import_block", blk)
        except Exception:
            # Fallback: allow deps to import raw bytes directly (dev/test networks).
            bh = sha3_256(rawb)
            ok = await self._deps_call_ok("import_block", rawb)

        if ok and bh is not None:
            self._remember(self._seen_blocks, bh, self._seen_block_cap)
            await self._broadcast_inv(
                [InvItem(typ=InvType.BLOCK, h=bh)],
                exclude_remote=origin_remote,
                is_tx=False,
            )

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
