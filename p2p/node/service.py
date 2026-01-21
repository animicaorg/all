from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import (Any, Awaitable, Callable, Dict, Iterable, List, Optional,
                    Tuple)
from urllib.parse import urlparse

# Local imports are intentionally late/dynamic in a few places to avoid import cycles.
from .. import version as p2p_version
from ..config import P2PConfig  # typed config (see p2p/config.py)
from ..constants import NETWORK_MAGIC
from ..metrics import \
    get_metrics  # light wrapper; no-op metrics if not configured
from ..peer import connection_manager as conman
from ..peer import identify as idsvc
from ..peer import peerstore as pstore
from ..peer.p2p_store import apply_umask_from_env, ensure_writable
from ..peer import ping as pingsvc
from ..peer import ratelimit as prlimit
from ..transport import base as tbase
from ..transport import multiaddr as ma

try:
    from ..gossip import engine as gossip_engine
    from ..gossip import topics as gossip_topics
    from ..protocol import block_announce as proto_blk
    from ..protocol import flow_control as proto_flow
    from ..protocol import hello as proto_hello
    from ..protocol import inventory as proto_inv
    from ..protocol import share_relay as proto_share
    from ..protocol import snapshot as proto_snapshot
    from ..protocol import tx_relay as proto_tx
    from ..wire import encoding as wire_codec
    from ..wire import frames as wire_frames
except Exception:  # pragma: no cover - optional full stack
    gossip_engine = None  # type: ignore
    gossip_topics = None  # type: ignore
    proto_hello = proto_inv = proto_blk = proto_tx = proto_share = proto_flow = proto_snapshot = wire_codec = wire_frames = None  # type: ignore

# Node router/event-bus (these are small glue modules under p2p/node/)
from . import events as node_events
from . import health as node_health
from . import router as node_router

log = logging.getLogger("animica.p2p.node")

OnAccept = Callable[[tbase.Conn], Awaitable[None]]


class ConnectionWrapper:
    """
    Wraps a transport Conn to provide the ConnLike interface expected by the router.
    
    Adds:
    - remote_addr: direct property access to conn.info.remote_addr
    - send_frame(): frames a message and sends it via the connection's stream
    - is_closed(): checks connection closed status
    """
    def __init__(self, conn: tbase.Conn):
        self._conn = conn
        self._stream: Optional[tbase.Stream] = None
        self._send_seq = 0
        self._send_lock = asyncio.Lock()
    
    @property
    def remote_addr(self) -> str:
        return self._conn.info.remote_addr if self._conn.info and self._conn.info.remote_addr else "unknown"
    
    def is_closed(self) -> bool:
        return self._conn.closed
    
    async def ensure_stream(self) -> tbase.Stream:
        """Public method to lazily open the stream for this connection."""
        if self._stream is None:
            self._stream = await self._conn.open_stream()
        return self._stream
    
    async def send_frame(self, msg_id: int, payload: bytes, *, acks: bool = False) -> None:
        """
        Pack and send a frame via the connection's stream.
        
        Args:
            msg_id: Message ID for the frame
            payload: Message payload bytes
            acks: Whether to request acknowledgment (currently unused, reserved for future use)
        """
        async with self._send_lock:
            stream = await self.ensure_stream()
            
            # Pack the frame using the wire protocol
            frame_bytes = wire_frames.pack_frame(
                msg_id=msg_id,
                seq=self._send_seq,
                payload=payload,
                # AEAD is already handled at the transport stream level
                aead=None,
                compress_threshold=1024,
            )
            self._send_seq += 1
            
            # Send via the stream
            await stream.send(frame_bytes)
    
    async def close(self) -> None:
        """Close the underlying connection."""
        await self._conn.close()
    
    # Expose the underlying conn for use by ConnectionManager
    @property
    def conn(self) -> tbase.Conn:
        return self._conn


@dataclass
class NodeDeps:
    """
    Injected glue to core/consensus/proofs so protocol handlers can look up/persist things
    without hard-coding imports. See p2p/deps.py for a ready-made provider.
    """

    head_reader: Any
    block_io: Any
    tx_io: Any
    proofs_view: Any
    consensus_view: Any


@dataclass
class _Listener:
    addr: str
    listener: tbase.Transport
    task: asyncio.Task


@dataclass
class NodeService:
    """
    Orchestrates the full P2P node:
      • Binds listeners for the configured transports (TCP/QUIC/WS)
      • Performs PQ handshake (Kyber768 + HKDF) and upgrades to AEAD
      • Wires protocol handlers (HELLO, INV/GETDATA, block announce, tx/share relay)
      • Runs the gossip mesh + discovery + ping/identify services
      • Exposes a tiny event bus & health snapshot
    """

    cfg: P2PConfig
    deps: NodeDeps
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_event_loop)

    # runtime members
    started: bool = field(default=False, init=False)
    stopping: bool = field(default=False, init=False)
    _listeners: List[_Listener] = field(default_factory=list, init=False)
    _tasks: List[asyncio.Task] = field(default_factory=list, init=False)

    # services
    peerstore: pstore.PeerStore = field(init=False)
    connmgr: conman.ConnectionManager = field(init=False)
    ratelimiter: prlimit.PeerRateLimiter = field(init=False)
    events: node_events.EventBus = field(init=False)
    router: node_router.Router = field(init=False)
    gossip: gossip_engine.GossipEngine = field(init=False)
    ping: pingsvc.PingService = field(init=False)
    identify: idsvc.IdentifyService = field(init=False)
    flowctl: proto_flow.FlowController = field(init=False)
    tx_relay_handler: Any = field(init=False)  # TxRelayHandler

    # crypto/ids
    node_keys: Any = field(init=False)
    peer_id: bytes = field(init=False)

    # metrics
    metrics: Any = field(default_factory=get_metrics, init=False)

    def __post_init__(self) -> None:
        # Load or generate long-term node identity (Dilithium3/SPHINCS+) + peer-id
        from ..crypto import keys as node_keys_mod
        from ..crypto import peer_id as peer_id_mod

        keys_path = self.cfg.keys_path or str(Path(self.cfg.data_dir) / "identity.json")
        writable_keys = ensure_writable(Path(keys_path))
        passphrase = os.environ.get("ANIMICA_P2P_KEY_PASSPHRASE", "")
        self.node_keys = node_keys_mod.load_or_create(
            str(writable_keys.path), passphrase, alg=self.cfg.identity_alg
        )
        self.peer_id = peer_id_mod.peer_id_from_identity(self.node_keys)

        # Core services
        self.peerstore = pstore.PeerStore(self.cfg.data_dir)
        self.ratelimiter = prlimit.PeerRateLimiter(
            per_peer=self.cfg.limit_per_peer,
            per_topic=self.cfg.limit_per_topic,
            global_limits=self.cfg.limit_global,
        )
        self.connmgr = conman.ConnectionManager(
            cfg=self.cfg,
            peerstore=self.peerstore,
            ratelimiter=self.ratelimiter,
            loop=self.loop,
        )
        self.events = node_events.EventBus(self.loop)
        self.router = node_router.Router(loop=self.loop, events=self.events)
        self.gossip = gossip_engine.GossipEngine(
            cfg=self.cfg.gossip,
            router=self.router,
            ratelimiter=self.ratelimiter,
            peerstore=self.peerstore,
            loop=self.loop,
        )
        self.ping = pingsvc.PingService(self.connmgr, window_size=16)
        
        # Get network manifest for P2P identity
        network_id, genesis_hash = self._load_network_manifest()
        
        # Fall back to alg_policy_root if manifest not available
        if not network_id:
            network_id = self.cfg.alg_policy_root
        
        self.identify = idsvc.IdentifyService(
            connmgr=self.connmgr,
            peer_id=self.peer_id,
            version=p2p_version.__version__,
            head_reader=self.deps.head_reader,
            network_id=network_id,
            genesis_hash=genesis_hash,
            alg_policy_root=self.cfg.alg_policy_root,
        )
        self.flowctl = proto_flow.FlowController(self.cfg.flow_control)

        # Mount protocol handlers into the router
        self._mount_protocols()

    def _load_network_manifest(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Load network manifest for P2P identity.
        
        Returns:
            Tuple of (network_id, genesis_hash) or (None, None) if manifest not available.
        """
        network_id = None
        genesis_hash = None
        try:
            from core.network_manifest import get_manifest_for_env
            manifest = get_manifest_for_env()
            if manifest:
                network_id = manifest.p2p_network_id
                genesis_hash = manifest.pinned_genesis_hash_hex
                log.info(
                    f"[p2p] Using network manifest: {manifest.network_identity_string}"
                )
        except ImportError:
            log.debug("[p2p] core.network_manifest not available, skipping manifest")
        except Exception as e:
            log.warning(f"[p2p] Could not load network manifest: {e}", exc_info=True)
        
        return network_id, genesis_hash

    # ——————————————————————————————————————————————————————————
    # Lifecycle
    # ——————————————————————————————————————————————————————————
    async def start(self) -> None:
        if self.started:
            return
        self.started = True
        log.info(
            "P2P node starting",
            extra={"peer_id": self.peer_id.hex(), "version": p2p_version.__version__},
        )

        # Bind listeners (TCP/WS/QUIC) per cfg.listen_multiaddrs
        for addr in self.cfg.listen_multiaddrs:
            listener = await self._bind_listener(addr)
            task = self.loop.create_task(
                self._accept_loop(listener), name=f"accept@{addr}"
            )
            self._listeners.append(_Listener(addr=addr, listener=listener, task=task))

        # Start TxRelayHandler (subscribe to gossip topic)
        await self.tx_relay_handler.start()

        # Start background services
        self._tasks.extend(
            [
                self.loop.create_task(self.connmgr.run(), name="connmgr"),
                self.loop.create_task(self.gossip.run(), name="gossip"),
                self.loop.create_task(self.ping.run(), name="ping"),
                self.loop.create_task(self.identify.run(), name="identify"),
                self.loop.create_task(self._seed_and_discover(), name="discovery"),
                self.loop.create_task(self.flowctl.run(), name="flowctl"),
            ]
        )

        # Hook OS signals for graceful shutdown (best-effort)
        self._install_signal_handlers()
        log.info(
            "P2P node started", extra={"listeners": [l.addr for l in self._listeners]}
        )

    async def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        log.info("P2P node stopping")

        # Stop TxRelayHandler
        await self.tx_relay_handler.stop()

        # Stop background tasks
        for t in self._tasks:
            t.cancel()
        await self._gather("background", *self._tasks)

        # Close listeners
        for L in self._listeners:
            with contextlib.suppress(Exception):
                await L.listener.close()
            if not L.task.done():
                L.task.cancel()
        await self._gather("listeners", *(L.task for L in self._listeners))

        # Stop subordinate services that own resources
        await self.gossip.close()
        await self.connmgr.close()

        self.started = False
        log.info("P2P node stopped")

    # ——————————————————————————————————————————————————————————
    # Transports & handshake
    # ——————————————————————————————————————————————————————————
    async def _bind_listener(self, addr: str) -> tbase.Transport:
        """
        Resolve a multiaddr-like string and bind the appropriate transport listener.
        Supported schemes: tcp://, ws://, wss://, quic:// (if enabled).
        """
        parsed = ma.parse(addr)
        scheme = parsed.scheme
        host, port = parsed.host, parsed.port

        # Reconstruct the full address in the format expected by each transport
        if scheme == "tcp":
            full_addr = f"tcp://{host}:{port}"
        elif scheme in ("ws", "wss"):
            full_addr = f"{scheme}://{host}:{port}/p2p"
        elif scheme == "quic":
            full_addr = f"quic://{host}:{port}"
        else:
            raise ValueError(f"unsupported listen scheme: {scheme}")

        # Create ListenConfig with the full address
        # Use default max_frame_bytes from transport.base
        from ..transport.base import MAX_FRAME_DEFAULT
        listen_cfg = tbase.ListenConfig(
            addr=full_addr,
            max_frame_bytes=MAX_FRAME_DEFAULT,
            backlog=128,
        )

        if scheme == "tcp":
            from ..transport import tcp as tmod

            transport = tmod.TcpTransport(
                handshake_prologue=self.cfg.handshake_hkdf_salt,
                chain_id=self.cfg.chain_id,
                network_magic=NETWORK_MAGIC,
            )
            await transport.listen(listen_cfg)
            return transport
        elif scheme in ("ws", "wss"):
            from ..transport import ws as tmod

            transport = tmod.WsTransport(
                handshake_prologue=self.cfg.handshake_hkdf_salt,
                chain_id=self.cfg.chain_id,
                network_magic=NETWORK_MAGIC,
            )
            await transport.listen(listen_cfg)
            return transport
        elif scheme == "quic":
            from ..transport import quic as tmod

            transport = tmod.QuicTransport(
                handshake_prologue=self.cfg.handshake_hkdf_salt,
                chain_id=self.cfg.chain_id,
                network_magic=NETWORK_MAGIC,
            )
            await transport.listen(listen_cfg)
            return transport
        else:
            raise ValueError(f"unsupported listen scheme: {scheme}")

    async def _accept_loop(self, listener: tbase.Transport) -> None:
        """
        Accept raw connections from the transport and register them with ConnectionManager.
        The transport has already performed the cryptographic handshake during accept().
        """
        try:
            while not self.stopping:
                conn = await listener.accept()
                self.metrics.accepted.inc()
                # Register the inbound connection with the connection manager
                peer = await self.connmgr.register_inbound(conn)
                if peer is None:
                    # Registration failed (limits, bans, or identify failure)
                    with contextlib.suppress(Exception):
                        await conn.close()
                    continue
                # Wrap the connection to provide ConnLike interface for the router
                wrapped_conn = ConnectionWrapper(conn)
                # Once registered, route frames through the router
                remote = conn.info.remote_addr if conn.info else "unknown"
                self.loop.create_task(
                    self._read_frames(wrapped_conn, peer.peer_id), name=f"read@{remote}"
                )
        except asyncio.CancelledError:
            log.debug("Accept loop cancelled")
            raise
        except Exception as e:
            log.error("Accept loop error", exc_info=e)

    async def _read_frames(self, wrapped_conn: ConnectionWrapper, peer_id: str) -> None:
        """
        Read frames from a secure connection and feed them to the router.
        The connection provides streams; we open one and read frames from it.
        """
        try:
            # Open the stream for this connection using public interface
            stream = await wrapped_conn.ensure_stream()
            
            while not wrapped_conn.is_closed():
                # Read frame data from the stream
                # The transport handles length-prefixing, so we just receive the frame
                frame_bytes = await stream.recv()
                if not frame_bytes:
                    # EOF - connection closed gracefully
                    break
                
                # Unpack the frame
                frame = wire_frames.unpack_frame(frame_bytes)
                
                # Optional fast-path flow control
                if not self.flowctl.permit(wrapped_conn, frame):
                    continue
                    
                await self.router.dispatch(wrapped_conn, frame)
                self.metrics.frames_rx.inc()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug(
                "conn read error", extra={"peer": wrapped_conn.remote_addr}, exc_info=e
            )
        finally:
            await self.connmgr._on_disconnect(peer_id, reason="disconnect")
            with contextlib.suppress(Exception):
                await wrapped_conn.close()

    # ——————————————————————————————————————————————————————————
    # Protocols & gossip wiring
    # ——————————————————————————————————————————————————————————
    def _mount_protocols(self) -> None:
        """
        Register all protocol handlers in the router and gossip engine.
        """
        # Router-wide codec (CBOR/msgspec) + checksum is centralized in wire_codec
        codec = wire_codec.Codec()

        # HELLO & identify (version/chain/alg-policy root)
        self.router.add_handler(
            proto_hello.HelloHandler(
                cfg=self.cfg,
                codec=codec,
                identify=self.identify,
                peerstore=self.peerstore,
            )
        )

        # Inventory + data requests (headers/blocks/txs/shares)
        self.router.add_handler(
            proto_inv.InventoryHandler(
                cfg=self.cfg, codec=codec, deps=self.deps, connmgr=self.connmgr
            )
        )
        self.router.add_handler(
            proto_blk.BlockAnnounceHandler(
                cfg=self.cfg, codec=codec, deps=self.deps, gossip=self.gossip
            )
        )
        self.tx_relay_handler = proto_tx.TxRelayHandler(
            cfg=self.cfg,
            codec=codec,
            deps=self.deps,
            gossip=self.gossip,
            ratelimiter=self.ratelimiter,
        )
        self.router.add_handler(self.tx_relay_handler)
        self.router.add_handler(
            proto_share.ShareRelayHandler(
                cfg=self.cfg,
                codec=codec,
                deps=self.deps,
                gossip=self.gossip,
                ratelimiter=self.ratelimiter,
            )
        )

        # Flow control (credits/window updates)
        self.router.add_handler(self.flowctl.handler(codec))
        
        # Snapshot discovery handler
        self.router.add_handler(proto_snapshot.SnapshotHandler())

        # FastBootstrap v2 handler (snapshots + epoch packs + PCP)
        from animica.sync.storage import EpochPackStore, SnapshotStore
        from ..protocol import fastbootstrap as proto_fastbootstrap

        snapshot_store = SnapshotStore(Path(self.cfg.data_dir) / "snapshots_v2")
        epoch_store = EpochPackStore(Path(self.cfg.data_dir) / "epoch_packs")
        self.router.add_handler(
            proto_fastbootstrap.FastBootstrapHandler(
                snapshot_store=snapshot_store,
                epoch_store=epoch_store,
                data_dir=Path(self.cfg.data_dir),
            )
        )

        # Gossip topics
        self.gossip.register_topic(gossip_topics.BLOCKS)
        self.gossip.register_topic(gossip_topics.HEADERS)
        self.gossip.register_topic(gossip_topics.TXS)
        self.gossip.register_topic(gossip_topics.SHARES)
        # DA topics can be added by da/adapter later

    # ——————————————————————————————————————————————————————————
    # Discovery / seeds
    # ——————————————————————————————————————————————————————————
    async def _seed_and_discover(self) -> None:
        """
        Dial configured seeds, then keep the discovery loop running (DNS seeds, Kademlia, mDNS).
        Uses network-specific seeds from config with automatic fallback to embedded bootstrap seeds.
        """
        from ..discovery import kademlia as kad
        from ..discovery import mdns as md
        from ..discovery import seeds as seedmod

        # Use seeds from config (already network-specific with embedded fallback)
        # Seeds are provided as multiaddr strings, ready to dial
        seed_addrs = list(self.cfg.seeds) if self.cfg.seeds else []

        # If no seeds configured, try to discover based on chain_id
        if not seed_addrs:
            try:
                # Try to get chain_id from deps
                chain_id = getattr(self.deps, "chain_id", None)
                if chain_id and chain_id in seedmod.NETWORK_DNS_SEEDS:
                    log.info("[bootstrap] discovering seeds for chain_id=%d", chain_id)
                    bundle = await seedmod.discover_for_network(
                        chain_id, resolve=True, include_fallbacks=True
                    )
                    # Convert SeedEndpoints to multiaddr format
                    for ep in bundle.endpoints:
                        # Determine IP type (ip4 vs ip6) or DNS type
                        try:
                            ip_obj = ipaddress.ip_address(ep.host)
                            ip_type = "ip6" if ip_obj.version == 6 else "ip4"
                        except ValueError:
                            # It's a hostname - use dns4 as default
                            # (dns6 exists but is rarely used; modern DNS resolvers
                            # return both A and AAAA records via dns4)
                            ip_type = "dns4"

                        # Build multiaddr based on scheme
                        if ep.scheme == "quic":
                            # /ip4/host/udp/port/quic-v1 or /dns4/host/udp/port/quic-v1
                            seed_addrs.append(
                                f"/{ip_type}/{ep.host}/udp/{ep.port}/quic-v1"
                            )
                        elif ep.scheme == "tcp":
                            # /ip4/host/tcp/port or /dns4/host/tcp/port
                            seed_addrs.append(f"/{ip_type}/{ep.host}/tcp/{ep.port}")
                        elif ep.scheme in ("ws", "wss"):
                            # /ip4/host/tcp/port/ws or /dns4/host/tcp/port/ws
                            proto = "ws" if ep.scheme == "ws" else "wss"
                            seed_addrs.append(
                                f"/{ip_type}/{ep.host}/tcp/{ep.port}/{proto}"
                            )
                        else:
                            # Fallback: try URL-style (will be parsed by dial)
                            port = f":{ep.port}" if ep.port is not None else ""
                            path = ep.path or ""
                            seed_addrs.append(f"{ep.scheme}://{ep.host}{port}{path}")
            except Exception as e:
                log.warning("Dynamic seed discovery failed", exc_info=e)

        # Dial all seeds (DNS names will be resolved by transport layer)
        for addr in seed_addrs:
            with contextlib.suppress(Exception):
                log.info("[bootstrap] dialing seed %s", addr)
                await self._dial(addr)

        # Run ongoing discovery backends
        tasks = []
        backoff: Dict[str, float] = {}

        async def _periodic_dials() -> None:
            try:
                while not self.stopping:
                    await asyncio.sleep(10.0)
                    try:
                        candidates = [
                            addr
                            for _, addr, _ in self.peerstore.list_addresses(limit=64)
                        ]
                    except Exception:
                        candidates = []
                    now = time.time()
                    for addr in list(dict.fromkeys(seed_addrs + candidates)):
                        if backoff.get(addr, 0.0) > now:
                            continue
                        backoff[addr] = now + 30.0
                        self.loop.create_task(self._dial(addr), name=f"dial@{addr}")
            except asyncio.CancelledError:
                return

        tasks.append(asyncio.create_task(_periodic_dials(), name="seed-loop"))
        if self.cfg.discovery.enable_kademlia:
            tasks.append(
                asyncio.create_task(
                    kad.run(self.cfg, self.peerstore, self.connmgr), name="kad"
                )
            )
        if self.cfg.discovery.enable_mdns:
            tasks.append(
                asyncio.create_task(
                    md.run(self.cfg, self.peerstore, self.connmgr), name="mdns"
                )
            )

        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            await self._gather("discovery-backends", *tasks)

    async def _dial(self, addr: str) -> None:
        """
        Dial and upgrade a single address. Called by discovery and manual CLI.
        Uses ConnectionManager to handle full dial + registration flow.
        """
        peer = await self.connmgr.connect(addr, tag="discovery")
        if peer is None:
            log.debug(f"Failed to connect to {addr}")
            return
        
        # Wrap the connection to provide ConnLike interface for the router
        wrapped_conn = ConnectionWrapper(peer.conn)
        
        # Start reading frames from the connected peer
        self.loop.create_task(
            self._read_frames(wrapped_conn, peer.peer_id),
            name=f"read@{wrapped_conn.remote_addr}"
        )

    # ——————————————————————————————————————————————————————————
    # Utilities
    # ——————————————————————————————————————————————————————————
    async def _gather(self, label: str, *tasks: asyncio.Task) -> None:
        if not tasks:
            return
        res = await asyncio.gather(*tasks, return_exceptions=True)
        for r in res:
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                log.debug("task error", extra={"where": label}, exc_info=r)

    def _install_signal_handlers(self) -> None:
        # Safe on POSIX; ignored on platforms that don't support it
        try:
            self.loop.add_signal_handler(
                signal.SIGTERM, lambda: asyncio.create_task(self.stop())
            )
            self.loop.add_signal_handler(
                signal.SIGINT, lambda: asyncio.create_task(self.stop())
            )
        except NotImplementedError:
            pass

    # Public helpers for CLI/ops
    async def publish(self, topic: str, payload: bytes) -> None:
        await self.gossip.publish(topic, payload)

    async def import_peers(self, addresses: List[str]) -> Dict[str, Any]:
        """
        Import and dial a list of peer addresses.
        
        This method:
        1. Adds addresses to the runtime seed list
        2. Persists them to the peerstore
        3. Triggers immediate dial attempts
        
        Args:
            addresses: List of peer addresses (multiaddr format)
        
        Returns:
            Dict with import results (imported, skipped, invalid counts)
        """
        imported = 0
        skipped = 0
        invalid = 0
        dial_attempted = 0
        dial_success = 0
        errors: List[str] = []
        
        for addr in addresses:
            # Validate address format
            try:
                parsed = ma.parse(addr)
                if not parsed.host or not parsed.port:
                    invalid += 1
                    errors.append(f"invalid address: {addr}")
                    continue
            except Exception as e:
                invalid += 1
                errors.append(f"invalid address {addr}: {e}")
                continue
            
            # Add to runtime seed list if not already present
            if addr not in self.cfg.seeds:
                # Note: cfg.seeds is a tuple, so we can't modify it directly
                # Instead, we'll track it separately and update the peerstore
                log.info(f"[import_peers] Adding seed to runtime: {addr}")
                imported += 1
                
                # Add to peerstore
                try:
                    # Generate a deterministic peer ID from the address
                    peer_id_hash = hashlib.sha256(addr.encode()).digest()
                    peer_id = peer_id_hash.hex()[:32]
                    
                    self.peerstore.add(
                        peer_id=peer_id,
                        addrs=[addr],
                        score=10.0,  # Higher score for manually added seeds
                        direction="outbound"
                    )
                    self.peerstore.record_seen(peer_id, addr)
                except Exception as e:
                    log.warning(f"[import_peers] Failed to add to peerstore: {e}")
                
                # Trigger immediate dial attempt
                dial_attempted += 1
                try:
                    self.loop.create_task(self._dial(addr), name=f"import-dial@{addr}")
                    dial_success += 1
                except Exception as e:
                    errors.append(f"dial failed for {addr}: {e}")
            else:
                skipped += 1
                log.debug(f"[import_peers] Skipping already-known seed: {addr}")
        
        return {
            "ok": imported > 0 or skipped > 0,
            "imported": imported,
            "skipped": skipped,
            "invalid": invalid,
            "dial_attempted": dial_attempted,
            "dial_success": dial_success,
            "errors": errors if errors else None,
        }

    def health(self) -> Dict[str, Any]:
        return node_health.snapshot(
            peer_id=self.peer_id.hex(),
            version=p2p_version.__version__,
            listeners=[l.addr for l in self._listeners],
            peers=self.connmgr.snapshot(),
            gossip=self.gossip.snapshot(),
        )


# -------------------------------------------------------------------------------------
# Thin compatibility service (devnet-friendly)
# -------------------------------------------------------------------------------------


class P2PServiceLegacy:
    """
    Lightweight wrapper that exposes a stable API for the CLI and tests.

    The full NodeService above is more featureful but is still being wired. To
    keep the listener CLI functional across environments, we provide a
    deterministic TCP-only service that performs the authenticated handshake and
    tracks connected peers in-memory.
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
        from ..transport.base import ListenConfig
        from ..transport.multiaddr import parse_multiaddr
        from ..transport.tcp import TcpTransport  # lazy import

        apply_umask_from_env()
        self.listen_addrs = listen_addrs or ["/ip4/0.0.0.0/tcp/42069"]
        self.seeds = seeds or []
        self.chain_id = chain_id
        self.enable_quic = enable_quic
        self.enable_ws = enable_ws
        self.nat = nat
        self.deps = deps

        self.loop = asyncio.get_event_loop()
        prologue = f"animica/tcp/{chain_id}".encode()
        self._transport = TcpTransport(
            handshake_prologue=prologue,
            chain_id=chain_id,
            network_magic=NETWORK_MAGIC,
        )
        self._listen_cfg = ListenConfig(addr="tcp://0.0.0.0:0")
        self._accept_task: asyncio.Task | None = None
        self._dial_tasks: list[asyncio.Task] = []
        self._consensus_task: asyncio.Task | None = None
        self._seed_reconnect_task: asyncio.Task | None = None
        self._sync_monitor_task: asyncio.Task | None = None
        self._peers: Dict[str, Dict[str, Any]] = {}
        self._running = False
        self._parse_multiaddr = parse_multiaddr
        self._listen_config_cls = ListenConfig
        self._log = logging.getLogger("animica.p2p.service")

        # Initialize persistent peer store
        if peerstore_path is None:
            # Default to network-specific directory
            network_name = {0: "mainnet", 2: "testnet", 1337: "devnet"}.get(
                chain_id, "custom"
            )
            peerstore_path = os.path.expanduser(f"~/.animica/p2p/{network_name}")
        writable_peerstore = ensure_writable(Path(peerstore_path))
        self.peerstore = pstore.open_peerstore(writable_peerstore.path)
        self._log.info(
            "Initialized persistent peer store at %s", writable_peerstore.path
        )

        # Lazy identify helper (filled on-demand). We intentionally keep the
        # devnet-friendly service light, but still want peers to exchange
        # basic metadata (height, versions, caps) so operators can confirm the
        # network is healthy.
        self._identify = idsvc.perform_identify

        # Metrics object for test compatibility
        class _Metrics:
            def __init__(self, service):
                self._service = service

            @property
            def peer_count(self):
                # Count both in-memory and persistent peers
                return len(self._service._peers)

        self.metrics = _Metrics(self)

    def _load_network_manifest(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Load network manifest for P2P identity.
        
        Returns:
            Tuple of (network_id, genesis_hash) or (None, None) if manifest not available.
        """
        network_id = None
        genesis_hash = None
        try:
            from core.network_manifest import get_manifest_for_env
            manifest = get_manifest_for_env()
            if manifest:
                network_id = manifest.p2p_network_id
                genesis_hash = manifest.pinned_genesis_hash_hex
                log.info(
                    f"[p2p] Using network manifest: {manifest.network_identity_string}"
                )
        except ImportError:
            log.debug("[p2p] core.network_manifest not available, skipping manifest")
        except Exception as e:
            log.warning(f"[p2p] Could not load network manifest: {e}", exc_info=True)
        
        return network_id, genesis_hash

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        # Load previously known peers from persistent store
        try:
            known_peers = self.peerstore.list_known(limit=100)
            self._log.info(f"Loaded {len(known_peers)} peers from persistent store")
        except Exception as e:
            self._log.warning(f"Failed to load peers from store: {e}", exc_info=True)
            known_peers = []

        # Bind listeners
        for ma in self.listen_addrs:
            parsed = self._parse_multiaddr(ma)
            if parsed.transport != "tcp":
                continue
            host = parsed.host or "0.0.0.0"
            port = parsed.port or 0
            cfg = self._listen_config_cls(addr=f"tcp://{host}:{port}")
            await self._transport.listen(cfg)
        self._accept_task = self.loop.create_task(
            self._accept_loop(), name="tcp-accept"
        )

        # Dial seeds (best-effort, fire-and-forget)
        seed_count = 0
        for seed in self.seeds:
            try:
                parsed = self._parse_multiaddr(seed)
            except Exception as e:
                self._log.warning(f"Failed to parse seed address {seed}: {e}")
                continue
            if parsed.transport != "tcp":
                self._log.debug(
                    f"Skipping non-TCP seed: {seed} (transport={parsed.transport})"
                )
                continue
            addr = f"tcp://{parsed.host}:{parsed.port}"
            self._log.info(f"Dialing seed: {addr}")
            self._dial_tasks.append(
                self.loop.create_task(self._dial(addr), name=f"dial@{addr}")
            )
            seed_count += 1

        if seed_count == 0 and len(self.seeds) > 0:
            self._log.warning(
                f"No TCP seeds to dial (total seeds: {len(self.seeds)}). Ensure at least one TCP seed is configured."
            )
        elif seed_count == 0:
            self._log.warning(
                "No seeds configured. Node will not connect to network unless peers connect to it."
            )

        # Also try to reconnect to previously known peers (best effort)
        for peer in known_peers[:10]:  # Limit to first 10 to avoid overwhelming
            if hasattr(peer, "address") and peer.address:
                try:
                    # Parse address and dial
                    addr_str = peer.address
                    if addr_str.startswith("/"):
                        parsed = self._parse_multiaddr(addr_str)
                        if parsed.transport == "tcp":
                            addr_str = f"tcp://{parsed.host}:{parsed.port}"
                    self._dial_tasks.append(
                        self.loop.create_task(
                            self._dial(addr_str), name=f"redial@{addr_str}"
                        )
                    )
                except Exception:
                    pass  # Skip invalid addresses

        # Continuous consensus/identify probing so peers agree on head hash/height
        self._consensus_task = self.loop.create_task(
            self._consensus_watch_loop(), name="consensus-watch"
        )
        
        # Continuous seed reconnection loop (like legacy P2P)
        self._seed_reconnect_task = self.loop.create_task(
            self._seed_reconnect_loop(), name="seed-reconnect"
        )
        
        # Continuous sync monitoring to ensure we reach network best height
        self._sync_monitor_task = self.loop.create_task(
            self._sync_monitor_loop(), name="sync-monitor"
        )

        self._log.info(
            "Started full P2P service",
            extra={
                "listen": self.listen_addrs,
                "seeds": self.seeds,
                "known_peers": len(known_peers),
            },
        )

    async def stop(self) -> None:
        self._running = False
        if self._accept_task:
            self._accept_task.cancel()
            with contextlib.suppress(Exception):
                await self._accept_task
        for t in self._dial_tasks:
            t.cancel()
        if self._dial_tasks:
            await asyncio.gather(*self._dial_tasks, return_exceptions=True)
        if self._consensus_task:
            self._consensus_task.cancel()
            with contextlib.suppress(Exception):
                await self._consensus_task
        if hasattr(self, "_seed_reconnect_task") and self._seed_reconnect_task:
            self._seed_reconnect_task.cancel()
            with contextlib.suppress(Exception):
                await self._seed_reconnect_task
        if hasattr(self, "_sync_monitor_task") and self._sync_monitor_task:
            self._sync_monitor_task.cancel()
            with contextlib.suppress(Exception):
                await self._sync_monitor_task
        # Close live connections and record disconnections
        for peer in list(self._peers.values()):
            conn = peer.get("conn")
            peer_id = peer.get("peer_id")
            if conn:
                with contextlib.suppress(Exception):
                    await conn.close()
            # Record disconnection in peer store
            if peer_id:
                try:
                    self.peerstore.record_disconnection(peer_id)
                except Exception:
                    pass
        with contextlib.suppress(Exception):
            await self._transport.close()

    async def _accept_loop(self) -> None:
        try:
            while self._running:
                try:
                    conn = await self._transport.accept()
                except asyncio.CancelledError:
                    return
                except Exception:
                    if self._running:
                        self._log.warning("accept loop terminating", exc_info=True)
                    return
                self._track_peer(conn, direction="inbound")
        except asyncio.CancelledError:
            # Swallow expected cancellation during shutdown
            return

    async def _dial(self, addr: str) -> None:
        """Dial a peer with exponential backoff on failure."""
        attempt = 0
        max_attempts = 5
        backoff = 2.0
        
        while self._running and attempt < max_attempts:
            try:
                conn = await self._transport.dial(addr, timeout=5.0)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                attempt += 1
                if attempt >= max_attempts:
                    self._log.warning(
                        "Failed to dial %s after %s attempts (last error: %s: %s)",
                        addr,
                        attempt,
                        e.__class__.__name__,
                        e,
                    )
                    return
                
                delay = min(backoff ** attempt, 60.0)  # Cap at 60 seconds
                self._log.debug(
                    "Failed to dial %s (attempt %s/%s), retrying in %.1fs: %s: %s",
                    addr,
                    attempt,
                    max_attempts,
                    delay,
                    e.__class__.__name__,
                    e,
                )
                await asyncio.sleep(delay)
                continue
            
            self._track_peer(conn, direction="outbound", dial_addr=addr)
            self._log.info("Successfully connected to %s", addr)
            return

    def _peer_id_from_conn(self, conn: Any, remote: str) -> str:
        peer_id = getattr(conn.info, "peer_id", None) or getattr(conn, "peer_id", None)
        if isinstance(peer_id, (bytes, bytearray)):
            return peer_id.hex()
        if isinstance(peer_id, str) and peer_id:
            return peer_id
        host = self._extract_host(remote)
        return f"peer_{hashlib.sha256(str(host).encode()).hexdigest()[:32]}"

    def _extract_host(self, remote: str) -> str:
        if "://" in remote:
            parsed = urlparse(remote)
            return parsed.hostname or remote
        if remote.startswith("[") and "]" in remote:
            return remote.split("]", 1)[0].lstrip("[")
        if ":" in remote:
            return remote.rsplit(":", 1)[0]
        return remote

    def _normalize_peer_addr(self, addr: Optional[str]) -> Optional[str]:
        if not addr:
            return None
        addr = addr.strip()
        if not addr:
            return None
        if addr.startswith("/"):
            try:
                return ma.normalize_multiaddr(addr)
            except Exception:
                return None
        host = None
        port: Optional[int] = None
        scheme = None
        if "://" in addr:
            parsed = urlparse(addr)
            scheme = parsed.scheme
            host = parsed.hostname
            port = parsed.port
        elif ":" in addr:
            host, port_s = addr.rsplit(":", 1)
            try:
                port = int(port_s)
            except ValueError:
                port = None
        if not host or not port or not (1 <= port <= 65535):
            return None
        try:
            ip = ipaddress.ip_address(host)
            host_proto = "ip4" if ip.version == 4 else "ip6"
        except ValueError:
            host_proto = "dns"
        transport = "tcp" if scheme in (None, "", "tcp") else scheme
        if transport not in {"tcp"}:
            return None
        return f"/{host_proto}/{host}/{transport}/{port}"

    def _track_peer(
        self,
        conn: Any,
        direction: str = "outbound",
        dial_addr: Optional[str] = None,
    ) -> None:
        remote = (
            getattr(conn.info, "remote_addr", None)
            or getattr(conn, "remote_addr", None)
            or "unknown"
        )
        peer_id = self._peer_id_from_conn(conn, remote)
        dial_addr = self._normalize_peer_addr(dial_addr)

        self._peers[remote] = {
            "remote": remote,
            "connected": True,
            "last_seen": time.time(),
            "conn": conn,
            "peer_id": peer_id,
            "direction": direction,
            "dial_addr": dial_addr,
            "reported_addr": None,
        }
        self._log.info(
            "peer connected",
            extra={"remote": remote, "peer_id": peer_id, "direction": direction},
        )

        if dial_addr:
            try:
                self.peerstore.add(
                    peer_id=peer_id,
                    addrs=[dial_addr],
                    score=0.0,
                    direction=direction,
                )
                self.peerstore.record_connection(peer_id)
                self._log.debug(
                    "Persisted peer %s to store with direction=%s",
                    peer_id,
                    direction,
                )
            except Exception as e:
                self._log.warning(
                    "Failed to persist peer to store: %s", e, exc_info=True
                )

        # Best-effort IDENTIFY so peers exchange heights/caps/versions.
        async def _do_identify() -> None:
            info: Dict[str, Any] | None = None
            try:
                local_height, local_hash = self._local_head()
                
                # Get network ID from manifest (simple inline version)
                network_id_str = str(self.chain_id)
                genesis_hash_str = None
                try:
                    from core.network_manifest import get_manifest
                    manifest = get_manifest(chain_id=self.chain_id)
                    if manifest:
                        network_id_str = manifest.p2p_network_id
                        genesis_hash_str = manifest.pinned_genesis_hash_hex
                except (ImportError, Exception):
                    pass  # Fall back to chain_id
                
                info = await self._identify(
                    conn,
                    timeout=5.0,
                    local_height=local_height,
                    network_id=network_id_str,
                    agent=f"animica-p2p/{p2p_version.__version__}",
                    head_hash=genesis_hash_str or local_hash,
                )
            except Exception:
                self._log.debug(
                    "identify failed", exc_info=True, extra={"remote": remote}
                )
            if info:
                resolved_peer_id = info.get("peer_id") or peer_id
                reported_addr = self._normalize_peer_addr(info.get("addr"))
                if not reported_addr and direction == "outbound":
                    reported_addr = dial_addr
                self._peers[remote].update(
                    peer_id=resolved_peer_id,
                    reported_addr=reported_addr,
                )
                self._peers[remote].update(
                    info=info,
                    height=info.get("height"),
                    head_hash=info.get("head_hash"),
                )
                self._log.info(
                    "peer identified",
                    extra={
                        "remote": remote,
                        "network": info.get("network_id"),
                        "height": info.get("height"),
                        "head_hash": info.get("head_hash"),
                    },
                )
                # Update peer store with identified info
                try:
                    if reported_addr:
                        self.peerstore.add(
                            peer_id=resolved_peer_id,
                            addrs=[reported_addr],
                            score=0.0,
                            direction=direction,
                        )
                        self.peerstore.record_connection(resolved_peer_id)
                        self.peerstore.record_seen(resolved_peer_id, reported_addr)
                    else:
                        self.peerstore.record_seen(resolved_peer_id)
                except Exception:
                    pass

        self.loop.create_task(_do_identify(), name=f"identify@{remote}")

    async def _seed_reconnect_loop(self) -> None:
        """
        Continuously monitor and reconnect to seed nodes.
        
        This ensures the node maintains connections to bootstrap seeds,
        similar to how the legacy P2P service operates.
        """
        try:
            while self._running:
                await asyncio.sleep(30.0)  # Check every 30 seconds
                
                # Get currently connected peers
                connected_addrs = set()
                for peer in self._peers.values():
                    dial_addr = peer.get("dial_addr")
                    if dial_addr:
                        connected_addrs.add(dial_addr)
                
                # Reconnect to disconnected seeds
                for seed in self.seeds:
                    try:
                        parsed = self._parse_multiaddr(seed)
                        if parsed.transport != "tcp":
                            continue
                        
                        addr = f"tcp://{parsed.host}:{parsed.port}"
                        seed_multiaddr = f"/ip4/{parsed.host}/tcp/{parsed.port}"
                        
                        # Check if already connected
                        if seed_multiaddr in connected_addrs or addr in connected_addrs:
                            continue
                        
                        # Try to reconnect
                        self._log.info("Reconnecting to seed: %s", addr)
                        self.loop.create_task(self._dial(addr), name=f"redial-seed@{addr}")
                    except Exception as e:
                        self._log.debug(
                            "Failed to schedule seed reconnect: %s", e, exc_info=True
                        )
        except asyncio.CancelledError:
            return
    
    async def import_peers(self, addresses: list[str]) -> dict[str, t.Any]:
        """
        Import and dial a list of peer addresses.
        
        This method:
        1. Adds addresses to the runtime seed list
        2. Persists them to the peerstore (if available)
        3. Triggers immediate dial attempts
        
        Args:
            addresses: List of peer addresses (multiaddr format)
        
        Returns:
            Dict with import results (imported, skipped, invalid counts)
        """
        imported = 0
        skipped = 0
        invalid = 0
        dial_attempted = 0
        dial_success = 0
        errors: list[str] = []
        
        for addr in addresses:
            # Validate address format
            try:
                parsed = self._parse_multiaddr(addr)
                if not parsed.host or not parsed.port or parsed.transport != "tcp":
                    invalid += 1
                    errors.append(f"invalid or unsupported address: {addr}")
                    continue
            except Exception as e:
                invalid += 1
                errors.append(f"invalid address {addr}: {e}")
                continue
            
            # Add to runtime seed list if not already present
            if addr not in self.seeds:
                self.seeds.append(addr)
                self._log.info("Added seed to runtime: %s", addr)
                imported += 1
                
                # Add to peerstore if available
                if hasattr(self, '_peerstore') and self._peerstore is not None:
                    try:
                        # Generate a deterministic peer ID from the address
                        peer_id_hash = hashlib.sha256(addr.encode()).digest()
                        peer_id = peer_id_hash.hex()[:32]
                        
                        self._peerstore.add(
                            peer_id=peer_id,
                            addrs=[addr],
                            score=10.0,  # Higher score for manually added seeds
                            direction="outbound"
                        )
                        self._peerstore.record_seen(peer_id, addr)
                    except Exception as e:
                        self._log.warning("Failed to add to peerstore: %s", e)
                
                # Trigger immediate dial attempt
                dial_attempted += 1
                try:
                    tcp_addr = f"tcp://{parsed.host}:{parsed.port}"
                    self.loop.create_task(self._dial(tcp_addr), name=f"import-dial@{tcp_addr}")
                    dial_success += 1
                except Exception as e:
                    errors.append(f"dial failed for {addr}: {e}")
            else:
                skipped += 1
                self._log.debug("Skipping already-known seed: %s", addr)
        
        return {
            "ok": imported > 0 or skipped > 0,
            "imported": imported,
            "skipped": skipped,
            "invalid": invalid,
            "dial_attempted": dial_attempted,
            "dial_success": dial_success,
            "errors": errors if errors else None,
        }
    
    async def _sync_monitor_loop(self) -> None:
        """
        Continuously monitor sync progress and ensure we reach the network best height.
        
        Logs when we're behind and triggers re-sync if needed.
        """
        try:
            while self._running:
                await asyncio.sleep(60.0)  # Check every minute
                
                try:
                    local_height = self._local_height()
                    network_best = self._network_best_height()
                    
                    if network_best is None:
                        self._log.debug("No network best height available yet")
                        continue
                    
                    gap = network_best - local_height
                    
                    if gap > 10:
                        self._log.info(
                            "Sync progress check: local=%s, network_best=%s, gap=%s blocks behind",
                            local_height,
                            network_best,
                            gap,
                        )
                        # Trigger a sync if we have significant gap
                        # The actual syncing is handled by other components,
                        # this just provides visibility
                    elif gap > 0:
                        self._log.debug(
                            "Sync progress: local=%s, network_best=%s, gap=%s blocks",
                            local_height,
                            network_best,
                            gap,
                        )
                    else:
                        # We're at or ahead of network best
                        self._log.debug(
                            "Sync status: fully synced (local=%s, network_best=%s)",
                            local_height,
                            network_best,
                        )
                except Exception as e:
                    self._log.debug(
                        "Sync monitor check failed: %s", e, exc_info=True
                    )
        except asyncio.CancelledError:
            return

    async def _consensus_watch_loop(self) -> None:
        """
        Periodically re-identify peers to keep head height/hash in sync.
        Logs divergences so operators know when consensus is drifting.
        """
        try:
            while self._running:
                await asyncio.sleep(10.0)
                local_height, local_hash = self._local_head()
                for remote, peer in list(self._peers.items()):
                    conn = peer.get("conn")
                    peer_id = peer.get("peer_id")
                    if conn is None or peer_id is None:
                        continue
                    try:
                        # Get network ID from manifest (simple inline version)
                        network_id_str = str(self.chain_id)
                        genesis_hash_str = None
                        try:
                            from core.network_manifest import get_manifest
                            manifest = get_manifest(chain_id=self.chain_id)
                            if manifest:
                                network_id_str = manifest.p2p_network_id
                                genesis_hash_str = manifest.pinned_genesis_hash_hex
                        except (ImportError, Exception):
                            pass  # Fall back to chain_id
                        
                        info = await self._identify(
                            conn,
                            timeout=5.0,
                            local_height=local_height,
                            network_id=network_id_str,
                            agent=f"animica-p2p/{p2p_version.__version__}",
                            head_hash=genesis_hash_str or local_hash,
                        )
                        # Update local cache
                        peer.update(
                            info=info,
                            height=info.get("height"),
                            head_hash=info.get("head_hash"),
                        )
                        remote_height = int(info.get("height") or 0)
                        remote_hash = info.get("head_hash")

                        # Persist latest height for prioritization/dialing
                        try:
                            self.peerstore.update_head_height(peer_id, remote_height)
                            reported_addr = peer.get("reported_addr") or peer.get(
                                "dial_addr"
                            )
                            self.peerstore.record_seen(peer_id, reported_addr)
                        except Exception:
                            pass

                        # Surface consensus drift
                        if (
                            local_hash
                            and remote_hash
                            and remote_height == local_height
                            and str(remote_hash) != str(local_hash)
                        ):
                            self._log.warning(
                                "Consensus mismatch detected",
                                extra={
                                    "peer": peer_id,
                                    "remote": remote,
                                    "local_height": local_height,
                                    "local_head": local_hash,
                                    "remote_head": remote_hash,
                                },
                            )
                    except Exception:
                        self._log.debug(
                            "consensus probe failed",
                            exc_info=True,
                            extra={"peer": peer_id, "remote": remote},
                        )
        except asyncio.CancelledError:
            return

    def _local_height(self) -> int:
        """Read the local canonical height if available (best-effort)."""

        if self.deps and hasattr(self.deps, "block_db"):
            try:
                getter = getattr(self.deps.block_db, "get_canonical_height", None)
                if callable(getter):
                    return int(getter())
                head = getattr(self.deps.block_db, "get_head", None)
                if callable(head):
                    h, _hdr = head()
                    return int(h)
            except Exception:
                self._log.debug("local height probe failed", exc_info=True)
        return 0

    def _local_head(self) -> tuple[int, Optional[str]]:
        """
        Return (height, head_hash_hex) for the local node when available.
        Falls back to height-only if the hash cannot be read.
        """
        height = self._local_height()
        head_hash: Optional[str] = None

        if self.deps and hasattr(self.deps, "block_db"):
            block_db = self.deps.block_db
            try:
                # Prefer tuple (height, hash_bytes)
                head_tuple = None
                if hasattr(block_db, "get_canonical_head"):
                    head_tuple = block_db.get_canonical_head()
                elif hasattr(block_db, "get_head"):
                    head_tuple = block_db.get_head()

                if (
                    head_tuple
                    and isinstance(head_tuple, (list, tuple))
                    and len(head_tuple) >= 2
                ):
                    height = int(head_tuple[0])
                    hh = head_tuple[1]
                    if isinstance(hh, (bytes, bytearray)):
                        head_hash = hh.hex()
                    elif isinstance(hh, str):
                        head_hash = hh
            except Exception:
                self._log.debug("local head probe failed", exc_info=True)

        return height, head_hash
    
    def _network_best_height(self) -> Optional[int]:
        """
        Compute the highest height we know about in the network.
        
        This considers:
        1. Direct peer heights (from identify)
        2. Peer's network views (network_best_height) - enabling multi-hop propagation
        
        Returns the maximum height seen across all peers.
        """
        heights: list[int] = []
        
        for peer in self._peers.values():
            # Get peer's head height from identify
            try:
                peer_height = peer.get("height")
                if peer_height is not None:
                    peer_height = int(peer_height)
                    if peer_height > 0:
                        heights.append(peer_height)
            except (ValueError, TypeError):
                pass
            
            # Get peer's view of network best height (peers-of-peers)
            try:
                info = peer.get("info", {})
                if isinstance(info, dict):
                    network_best = info.get("network_best_height")
                    if network_best is not None:
                        network_best = int(network_best)
                        if network_best > 0:
                            heights.append(network_best)
            except (ValueError, TypeError):
                pass
        
        return max(heights) if heights else None

    # Exposed for tests/ops
    @property
    def peers(self) -> Dict[str, Dict[str, Any]]:
        return {
            k: {kk: vv for kk, vv in v.items() if kk != "conn"}
            for k, v in self._peers.items()
        }

    async def dial(self, addr: str) -> None:
        """
        Public dial method for tests and CLI.
        Connects to a peer at the given multiaddr string.
        """
        # Parse multiaddr format (/ip4/host/tcp/port) to tcp://host:port
        if addr.startswith("/"):
            try:
                parsed = self._parse_multiaddr(addr)
                if parsed.transport == "tcp":
                    addr = f"tcp://{parsed.host}:{parsed.port}"
            except Exception:
                pass  # Fall through and try as-is
        await self._dial(addr)


# -------------------------------------------------------------------------------------
# P2PService: Compatibility wrapper around NodeService
# -------------------------------------------------------------------------------------


class P2PService:
    """
    Production P2P service with a simplified interface for RPC/CLI compatibility.
    
    This wraps the modern NodeService with a simple __init__ signature that accepts
    listen_addrs, seeds, chain_id, etc. and provides start()/stop() methods.
    
    Internally uses NodeService for all the real P2P functionality.
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
        """
        Initialize P2P service with minimal configuration.
        
        Args:
            listen_addrs: List of multiaddr strings to listen on (e.g., ["/ip4/0.0.0.0/tcp/30333"])
            seeds: List of seed peer multiaddrs to connect to
            chain_id: Chain ID for network identification
            enable_quic: Whether to enable QUIC transport (currently unused, TCP only)
            enable_ws: Whether to enable WebSocket transport (currently unused, TCP only)
            nat: Whether to enable NAT traversal (currently unused)
            deps: Dependency injection object with block_io, tx_io, etc.
            peerstore_path: Path to persistent peer store directory
        """
        from ..config import P2PConfig
        
        apply_umask_from_env()
        self._log = logging.getLogger("animica.p2p.service")
        
        # Store init params
        self.chain_id = chain_id
        self.deps = deps
        self.listen_addrs = listen_addrs or ["/ip4/0.0.0.0/tcp/30333"]
        self.seeds = seeds or []
        
        # Resolve peerstore path
        if peerstore_path is None:
            env_peerstore = os.environ.get("ANIMICA_PEER_STORE_PATH") or os.environ.get(
                "ANIMICA_P2P_DATA_DIR"
            )
            if env_peerstore:
                peerstore_path = os.path.expanduser(env_peerstore)
            else:
                base_dir = Path(os.environ.get("ANIMICA_DATA_DIR") or "~/.animica").expanduser()
                peerstore_path = str(base_dir / f"chain-{chain_id}" / "p2p")
        
        peerstore_path = str(Path(peerstore_path).expanduser())
        writable_peerstore = ensure_writable(Path(peerstore_path))
        peerstore_path = str(writable_peerstore.path)
        
        # Create P2PConfig for NodeService
        # Note: P2PConfig doesn't have chain_id field - it uses network identification
        # through network_manifest (loaded in NodeService.__post_init__). We use chain_id
        # here only for peerstore path resolution and store it for compatibility.
        cfg = P2PConfig(
            listen_multiaddrs=tuple(self.listen_addrs),
            seeds=tuple(self.seeds),
            data_dir=peerstore_path,
            enable_tcp=True,
            enable_quic=enable_quic,
            enable_ws=enable_ws,
        )
        
        # Create NodeDeps if not provided
        if deps is None:
            # Create minimal deps for standalone operation
            self._log.warning("No deps provided; P2P will run without block/tx integration")
            deps = self._create_minimal_deps()
        
        # Create NodeService instance
        self._node_service = NodeService(cfg=cfg, deps=deps)
        
        # Expose commonly accessed attributes
        self.peer_id = self._node_service.peer_id
        self.peerstore = self._node_service.peerstore
        self.connmgr = self._node_service.connmgr
        
        # For compatibility with legacy code that checks these
        self.peer_registry = None  # Legacy attribute, not used in NodeService
        
        self._log.info(
            "P2PService initialized",
            extra={
                "chain_id": chain_id,
                "peer_id": self.peer_id.hex()[:16],
                "listen_addrs": len(self.listen_addrs),
                "seeds": len(self.seeds),
                "peerstore": peerstore_path,
            }
        )
    
    def _create_minimal_deps(self) -> NodeDeps:
        """Create minimal NodeDeps for standalone operation."""
        # Minimal stubs that return empty data
        class MinimalReader:
            def get_head(self):
                return {"height": 0, "hash": "0x" + "00" * 32}
        
        class MinimalIO:
            def get_by_hash(self, h):
                return None
        
        return NodeDeps(
            head_reader=MinimalReader(),
            block_io=MinimalIO(),
            tx_io=MinimalIO(),
            proofs_view=MinimalIO(),
            consensus_view=MinimalIO(),
        )
    
    async def start(self) -> None:
        """Start the P2P service."""
        self._log.info("Starting P2P service")
        await self._node_service.start()
        self._log.info("P2P service started")
    
    async def stop(self) -> None:
        """Stop the P2P service."""
        self._log.info("Stopping P2P service")
        await self._node_service.stop()
        self._log.info("P2P service stopped")
    
    def health(self) -> Dict[str, Any]:
        """Get health snapshot."""
        return self._node_service.health()
    
    @property
    def peers(self) -> Dict[str, Dict[str, Any]]:
        """Get connected peers dict from underlying NodeService."""
        return self._node_service.peers
    
    async def publish(self, topic: str, payload: bytes) -> None:
        """Publish a message to a gossip topic."""
        await self._node_service.publish(topic, payload)
